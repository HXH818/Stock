# 指数期权仓位：标普500 用 SPY 期权、纳指100 用 QQQ 期权，写入 options.json，由 bbi.py 每次运行时调用
#   看跌/看涨比 = 看跌合计 ÷ 看涨合计（成交量、未平仓量各算一个），范围：35 天内到期的合约
#   支撑 = 现价下方 5% 以内 看跌未平仓量最大的行权价；阻力 = 现价上方 5% 以内 看涨未平仓量最大的行权价
#   最大痛点 = 最近一个到期日，使期权买方到期总收益最小的结算价
#   异常合约 = 今日成交量 ≥ 2 × 未平仓量，且权利金（成交量 × 最新价 × 100）达到门槛，按权利金排序
# 注意：未平仓量每天只更新一次（反映前一交易日收盘），成交量是当天盘中累计；数据延迟约 15 分钟
import json
import pandas as pd
import yfinance as yf

TZ = "America/New_York"
GROUPS = [("SPY", "标普500", [("SPY", "SPY")]),
          ("QQQ", "纳指100", [("QQQ", "QQQ")])]
BAND = 0.05           # 支撑 / 阻力只在现价 ±5% 以内找
MAX_DAYS, MAX_EXP = 35, 8
MIN_PREMIUM = {"SPY": 1e6, "QQQ": 1e6}
N_UNUSUAL = 5

def chains(sym):
    """返回 (现价, [(到期日, calls, puts), ...])，只取 35 天内、最多 8 个到期日"""
    tk = yf.Ticker(sym)
    spot = float(tk.history(period="5d")["Close"].dropna().iloc[-1])
    today = pd.Timestamp.now(tz=TZ).normalize().tz_localize(None)
    out = []
    for e in tk.options:
        if (pd.Timestamp(e) - today).days > MAX_DAYS or len(out) >= MAX_EXP: break
        ch = tk.option_chain(e)
        out.append((e, ch.calls.copy(), ch.puts.copy()))
    return spot, out

def clean(df):
    for c in ("volume", "openInterest", "lastPrice", "strike"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce").fillna(0)
    return df

def max_pain(calls, puts):
    ks = sorted(set(calls["strike"]) | set(puts["strike"]))
    if not ks: return None
    def pay(s):
        return float((calls["openInterest"] * (s - calls["strike"]).clip(lower=0)).sum()
                     + (puts["openInterest"] * (puts["strike"] - s).clip(lower=0)).sum())
    return float(min(ks, key=pay))

def analyze(label, spot, exps, today):
    allc = pd.concat([clean(c) for _, c, _ in exps]) if exps else pd.DataFrame()
    allp = pd.concat([clean(p) for _, _, p in exps]) if exps else pd.DataFrame()
    if allc.empty or allp.empty: return None
    cv, pv, co, po = allc["volume"].sum(), allp["volume"].sum(), allc["openInterest"].sum(), allp["openInterest"].sum()
    oi_c = allc.groupby("strike")["openInterest"].sum(); oi_p = allp.groupby("strike")["openInterest"].sum()
    above = oi_c[(oi_c.index > spot) & (oi_c.index <= spot * (1 + BAND))]
    below = oi_p[(oi_p.index < spot) & (oi_p.index >= spot * (1 - BAND))]
    e0, c0, p0 = exps[0]
    zero = [x for x in exps if x[0] == today]
    unusual = []
    for e, c, p in exps:
        for kind, df in (("call", c), ("put", p)):
            df = clean(df)
            prem = df["volume"] * df["lastPrice"] * 100
            hit = df[(df["volume"] >= 2 * df["openInterest"]) & (prem >= MIN_PREMIUM[label])]
            for _, r in hit.iterrows():
                unusual.append({"u": label, "exp": e, "type": kind, "strike": float(r["strike"]),
                                "vol": int(r["volume"]), "oi": int(r["openInterest"]),
                                "prem": round(float(r["volume"] * r["lastPrice"] * 100)),
                                "dist": round((float(r["strike"]) / spot - 1) * 100, 2)})
    return {"u": label, "spot": round(spot, 2),
            "pcr_vol": round(pv / cv, 2) if cv else None, "pcr_oi": round(po / co, 2) if co else None,
            "res": float(above.idxmax()) if len(above) else None, "res_oi": int(above.max()) if len(above) else None,
            "sup": float(below.idxmax()) if len(below) else None, "sup_oi": int(below.max()) if len(below) else None,
            "mp": max_pain(clean(c0), clean(p0)), "mp_exp": e0,
            "zero": {"call": int(sum(clean(c)["volume"].sum() for _, c, _ in zero)),
                     "put": int(sum(clean(p)["volume"].sum() for _, _, p in zero))} if zero else None,
            "unusual": unusual}

def main():
    now = pd.Timestamp.now(tz=TZ)
    today = now.strftime("%Y-%m-%d")
    out = {"updated": now.strftime("%Y-%m-%d %H:%M") + " 美东", "groups": []}
    for key, name, syms in GROUPS:
        g = {"key": key, "name": name, "main": None, "unusual": []}
        for sym, label in syms:
            try:
                spot, exps = chains(sym)
                r = analyze(label, spot, exps, today)
            except Exception as e:
                print("期权失败:", sym, e); continue
            if not r: continue
            if g["main"] is None: g["main"] = {k: v for k, v in r.items() if k != "unusual"}
            g["unusual"] += r["unusual"]
        g["unusual"] = sorted(g["unusual"], key=lambda x: -x["prem"])[:N_UNUSUAL]
        out["groups"].append(g)
    with open("options.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("期权完成:", [(g["key"], g["main"] and g["main"]["u"], len(g["unusual"])) for g in out["groups"]])

if __name__ == "__main__":
    main()
