# 优质公司 + BBI 选股：每个交易日收盘后（美东 17:00 以后）运行一次，写入 screen.json
#   基本面（每 7 天刷新一次，缓存在 fundamentals.json）：
#     营收增长 > 5%，营业利润率 > 15%，自由现金流/营收 > 10%，净负债/EBITDA < 2；排除金融板块
#   技术面：日线 BBI = (MA3 + MA6 + MA12 + MA24) / 4，收盘价 > BBI 为站上
import io, json, os, time
import pandas as pd
import requests
import yfinance as yf

TZ = "America/New_York"
RULES = {"rev_growth": 0.05, "op_margin": 0.15, "fcf_margin": 0.10, "nd_ebitda": 2.0}
EXCLUDE_SECTORS = {"Financials"}
FUND_DAYS = 7

def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)

def sp500():
    """从维基百科读取标普 500 成分股：代码、名称、板块"""
    html = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
    t = pd.read_html(io.StringIO(html))[0]
    return [{"ticker": str(r["Symbol"]).replace(".", "-"), "name": r["Security"], "sector": r["GICS Sector"]}
            for _, r in t.iterrows()]

def fundamentals(t):
    i = yf.Ticker(t).info
    rev, fcf, ebitda = i.get("totalRevenue"), i.get("freeCashflow"), i.get("ebitda")
    debt, cash = i.get("totalDebt") or 0, i.get("totalCash") or 0
    f = {"rev_growth": i.get("revenueGrowth"), "op_margin": i.get("operatingMargins"),
         "fcf_margin": fcf / rev if fcf is not None and rev else None,
         "nd_ebitda": (debt - cash) / ebitda if ebitda and ebitda > 0 else None,
         "net_cash": debt - cash < 0}
    return f

def passes(f):
    if any(f.get(k) is None for k in ("rev_growth", "op_margin", "fcf_margin")):
        return False
    debt_ok = f["net_cash"] or (f["nd_ebitda"] is not None and f["nd_ebitda"] < RULES["nd_ebitda"])
    return (f["rev_growth"] > RULES["rev_growth"] and f["op_margin"] > RULES["op_margin"]
            and f["fcf_margin"] > RULES["fcf_margin"] and debt_ok)

def bbi_table(close):
    """close: 行=日期，列=代码。返回每只股票的最新状态"""
    bbi = (close.rolling(3).mean() + close.rolling(6).mean()
           + close.rolling(12).mean() + close.rolling(24).mean()) / 4
    out = {}
    for t in close.columns:
        c, b = close[t], bbi[t]
        ok = c.notna() & b.notna()
        c, b = c[ok], b[ok]
        if len(c) < 2:
            continue
        above = (c > b).tolist()
        days = 1
        for v in reversed(above[:-1]):
            if v != above[-1]: break
            days += 1
        out[t] = {"close": round(float(c.iloc[-1]), 2), "pct": round(float(c.iloc[-1] / b.iloc[-1] - 1) * 100, 2),
                  "bull": above[-1], "days": days, "new": above[-1] and days == 1}
    return out

def ha_signal(k, look=3):
    """日线买卖信号（最近 look 个交易日内最近的一次）：
       买入 = 平均K线绿 且 收盘 > BBI，两者首次同日成立；卖出 = 平均K线红 且 收盘 < BBI，两者首次同日成立。
       买卖必须交替出现：卖出之后，只有先出现买入，才会再出现卖出（反之亦然），
       避免中间某一天条件短暂中断（比如平均K线闪一根绿）又重复发出同一个信号。
       返回 (信号, 几天前)，例如 ("buy", 0) 表示今天出现买入信号；没有信号返回 ("", None)"""
    o, h, l, c = (k[x].to_numpy() for x in ("Open", "High", "Low", "Close"))
    if len(c) < 26: return "", None
    hc = (o + h + l + c) / 4                      # 平均K线 收 = (开+高+低+收)/4
    ho = hc.copy(); ho[0] = (o[0] + c[0]) / 2
    for i in range(1, len(c)):                    # 平均K线 开 = (前一根开 + 前一根收)/2
        ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    green = hc > ho
    cs = k["Close"]
    b = ((cs.rolling(3).mean() + cs.rolling(6).mean() + cs.rolling(12).mean() + cs.rolling(24).mean()) / 4).to_numpy()
    up, dn = green & (c > b), ~green & (c < b)
    last, last_i = "", None                       # 上一个信号及其位置
    for i in range(24, len(c)):
        if up[i] and not up[i - 1] and last != "buy":   last, last_i = "buy", i
        elif dn[i] and not dn[i - 1] and last != "sell": last, last_i = "sell", i
    if last and len(c) - 1 - last_i < look:
        return last, len(c) - 1 - last_i
    return "", None

def main():
    now = pd.Timestamp.now(tz=TZ)
    today = now.strftime("%Y-%m-%d")
    prev = load("screen.json") or {}
    force = os.environ.get("FORCE") == "true"
    if not force and (now.weekday() >= 5 or now.hour < 17 or prev.get("run_date") == today):
        print("选股：现在不需要运行（收盘后每天一次）")
        return

    members = sp500()
    tickers = [m["ticker"] for m in members]

    # 基本面：缓存 7 天
    fund = load("fundamentals.json") or {}
    age = (now - pd.Timestamp(fund["date"]).tz_localize(TZ)).days if fund.get("date") else 999
    if age >= FUND_DAYS or force and os.environ.get("REFRESH_FUND") == "true":
        data = {}
        for m in members:
            if m["sector"] in EXCLUDE_SECTORS:
                continue
            try:
                data[m["ticker"]] = fundamentals(m["ticker"])
            except Exception as e:
                print("基本面失败:", m["ticker"], e)
            time.sleep(0.3)
        fund = {"date": today, "data": data}
        save("fundamentals.json", fund)

    # 日线：批量下载全部成分股
    px = yf.download(tickers, period="1y", interval="1d", auto_adjust=False, progress=False, threads=True)
    close = px["Close"] if isinstance(px.columns, pd.MultiIndex) else px[["Close"]].rename(columns={"Close": tickers[0]})
    tech = bbi_table(close)

    rows = []
    for m in members:
        t = m["ticker"]
        f = fund["data"].get(t)
        if not f or t not in tech or not passes(f):
            continue
        hi_s, lo_s = px["High"][t].dropna().tail(252), px["Low"][t].dropna().tail(252)   # 最近 52 周
        c = tech[t]["close"]
        w52 = {"hi52": round(float(hi_s.max()), 2), "lo52": round(float(lo_s.min()), 2),
               "from_hi": round((c / float(hi_s.max()) - 1) * 100, 2),
               "nh": bool(hi_s.iloc[-1] >= hi_s.iloc[:-1].max()),    # 今天创 52 周新高
               "nl": bool(lo_s.iloc[-1] <= lo_s.iloc[:-1].min())}    # 今天创 52 周新低
        k = pd.DataFrame({"Open": px["Open"][t], "High": px["High"][t], "Low": px["Low"][t], "Close": close[t]}).dropna()
        w52["sig"], w52["sig_age"] = ha_signal(k)
        rows.append({**m, **tech[t], **w52, **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in f.items()}})

    # 为清单里的每只股票保存 K 线数据（网页画 BBI + MACD 图用）
    os.makedirs("charts", exist_ok=True)
    keep = {r["ticker"] for r in rows}
    for t in {r["ticker"] for r in prev.get("rows", [])} - keep:   # 只删掉已经不在清单里的股票（板块 ETF 的图不动）
        try: os.remove(os.path.join("charts", f"{t}.json"))
        except FileNotFoundError: pass
    for r in rows:
        t = r["ticker"]
        k = pd.DataFrame({"o": px["Open"][t], "h": px["High"][t], "l": px["Low"][t], "c": close[t]}).dropna().tail(220)
        save(f"charts/{t}.json", {"t": t, "name": r["name"], "d": [x.strftime("%Y-%m-%d") for x in k.index],
                                  **{c: [round(float(v), 2) for v in k[c]] for c in "ohlc"}})

    n_above = sum(1 for v in tech.values() if v["bull"])
    out = {"run_date": today, "asof": close.index[-1].strftime("%Y-%m-%d"),
           "updated": now.strftime("%Y-%m-%d %H:%M") + " 美东", "fund_date": fund["date"],
           "breadth": {"above": n_above, "total": len(tech), "pct": round(n_above / max(len(tech), 1) * 100, 1)},
           "rules": RULES, "rows": rows}
    save("screen.json", out)
    print(f"选股完成：宽度 {out['breadth']}，优质公司 {len(rows)} 家，其中站上 BBI {sum(r['bull'] for r in rows)} 家")

if __name__ == "__main__":
    main()
