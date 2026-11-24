# Telegram 提醒：在 bbi.py / screen.py 之后运行
#   盘中（美东 9:30–16:30，工作日）：持仓触发 止损 / 止盈 / 信号平仓 → 立即推送；同一只、同一提醒当天只发一次
#   收盘后（美东 17:00 以后，工作日）：每天一条总结（大盘、今日信号、持仓）
# 需要的 Secrets：TELEGRAM_TOKEN、TELEGRAM_CHAT_ID；持仓来自 HOLDINGS（网页自动加密上传）
# 注意：仓库是公开的，Actions 日志也公开，所以这里绝不打印持仓内容
import glob, hashlib, json, os
import numpy as np
import pandas as pd
import requests

TZ = "America/New_York"
STOP, TAKE = -5.0, 10.0
SITE = "https://hxh818.github.io/Stock/"
STATE = "notify_state.json"

def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def send(text):
    tok, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("没有设置 TELEGRAM_TOKEN / TELEGRAM_CHAT_ID，跳过"); return False
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", timeout=20,
                      json={"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
    print("Telegram 发送:", r.status_code)
    return r.ok

def ema(a, n):
    return pd.Series(a).ewm(span=n, adjust=False).mean().to_numpy()   # 与网页一致：首值为种子

def analyze(h, d, short):
    """与网页持仓页的规则完全一致（日线收盘）"""
    o, hi, lo, c = (np.array(d[k], float) for k in "ohlc")
    n = len(c)
    s = pd.Series(c)
    bbi = ((s.rolling(3).mean() + s.rolling(6).mean() + s.rolling(12).mean() + s.rolling(24).mean()) / 4).to_numpy()
    hc = (o + hi + lo + c) / 4
    ho = np.empty(n); ho[0] = (o[0] + c[0]) / 2
    for i in range(1, n): ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    dif = ema(c, 12) - ema(c, 26); dea = ema(dif, 9)
    last, price = c[-1], float(h["price"])
    pnl = (1 - last / price) * 100 if short else (last / price - 1) * 100
    red, below, dead = hc[-1] <= ho[-1], last < bbi[-1], dif[-1] < dea[-1]
    exit_ = (not red and not below and not dead) if short else (red and below and dead)
    w = "平仓" if short else "清仓"
    act = ("⚠ 止损" + w) if pnl <= STOP else ("✓ 止盈" + w) if pnl >= TAKE else ("⚠ 信号" + w) if exit_ else "持有"
    amt = (price - last if short else last - price) * float(h["shares"])
    return {"last": last, "day": (c[-1] / c[-2] - 1) * 100, "pnl": pnl, "amt": amt, "act": act, "date": d["d"][-1]}

def chart(t):
    d = load(f"charts/{t}.json")
    if d and d.get("frames"): d = d["frames"]["日线"]
    return d

# ===== 期权：与网页规则一致 =====
OT = {"C": ("看涨期权", True, True, 1), "P": ("看跌期权", False, True, 1),
      "BC": ("牛市看涨价差", True, True, 2), "BP": ("熊市看跌价差", False, True, 2),
      "BuP": ("牛市看跌价差", True, False, 2), "BeC": ("熊市看涨价差", False, False, 2)}   # 名称, 看涨?, 借方?, 腿数
RULE = {"single": (1.0, -0.5, 7), "debit": (0.5, -0.5, 7), "credit": (0.5, -2.0, 21)}   # 止盈, 止损, 到期提醒天数

def kkey(k): return ("%f" % float(k)).rstrip("0").rstrip(".")   # 与网页 String(数字) 一致，如 680、682.5

def fetch_chains(opts, today):
    """把持仓用到的 标的 + 到期日 的期权链存到 charts/opt/（现价 ±40% 以内的行权价），不需要的旧文件删掉"""
    import yfinance as yf
    os.makedirs("charts/opt", exist_ok=True)
    need = {(o["t"], o["exp"]) for o in opts}
    for f in glob.glob("charts/opt/*.json"):
        if tuple(os.path.basename(f)[:-5].split("_", 1)) not in need: os.remove(f)
    for t, exp in need:
        if exp < today: continue                                  # 已到期：保留最后一次的报价
        try:
            tk = yf.Ticker(t)
            if exp not in tk.options: print("期权到期日不存在（请检查日期）"); continue
            spot = float(tk.history(period="5d")["Close"].dropna().iloc[-1])
            ch = tk.option_chain(exp)
            def pack(df):
                df = df[(df["strike"] >= spot * 0.6) & (df["strike"] <= spot * 1.4)]
                return {kkey(r.strike): [float(r.bid or 0), float(r.ask or 0), float(r.lastPrice or 0)] for r in df.itertuples()}
            with open(f"charts/opt/{t}_{exp}.json", "w", encoding="utf-8") as f:
                json.dump({"t": t, "exp": exp, "spot": round(spot, 2), "c": pack(ch.calls), "p": pack(ch.puts)}, f)
        except Exception as e:
            print("期权链失败:", type(e).__name__)                  # 不打印代码和日期，避免在公开日志里暴露持仓

def exit_sig(d, bull):
    o, hi, lo, c = (np.array(d[k], float) for k in "ohlc")
    n, s = len(c), pd.Series(c)
    bbi = ((s.rolling(3).mean() + s.rolling(6).mean() + s.rolling(12).mean() + s.rolling(24).mean()) / 4).to_numpy()
    hc = (o + hi + lo + c) / 4
    ho = np.empty(n); ho[0] = (o[0] + c[0]) / 2
    for i in range(1, n): ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    dif = ema(c, 12) - ema(c, 26); dea = ema(dif, 9)
    red, below, dead = hc[-1] <= ho[-1], c[-1] < bbi[-1], dif[-1] < dea[-1]
    return (red and below and dead) if bull else (not red and not below and not dead)

def analyze_opt(o, today):
    q = load(f"charts/opt/{o['t']}_{o['exp']}.json")
    if not q: return None
    name, bull, debit, legs = OT[o["type"]]
    def px(side, k):
        x = q[side].get(kkey(k))
        if not x: return None
        bid, ask, last = x
        return (bid + ask) / 2 if bid > 0 and ask > 0 else last
    lo, hi = float(o["k1"]), float(o["k2"] or 0)
    t = o["type"]
    legs_px = {"C": (("c", lo),), "P": (("p", lo),), "BC": (("c", lo), ("c", hi)), "BP": (("p", hi), ("p", lo)),
               "BuP": (("p", hi), ("p", lo)), "BeC": (("c", lo), ("c", hi))}[t]
    vals = [px(*l) for l in legs_px]
    if any(v is None for v in vals): return None
    val = vals[0] - (vals[1] if len(vals) > 1 else 0)
    price, qty = float(o["price"]), int(o["qty"])
    pl = val - price if debit else price - val
    max_p = None if legs == 1 else ((hi - lo) - price if debit else price)
    take, stop, dte_warn = RULE["single" if legs == 1 else "debit" if debit else "credit"]
    dte = (pd.Timestamp(o["exp"]) - pd.Timestamp(today)).days
    d = chart(o["t"])
    if dte < 0: act = "⚠ 已到期"
    elif pl <= stop * price: act = "⚠ 止损平仓"
    elif pl >= take * (max_p if legs == 2 and debit else price): act = "✓ 止盈平仓"
    elif d and len(d["c"]) >= 30 and exit_sig(d, bull): act = "⚠ 信号平仓"
    elif dte <= dte_warn: act = f"⏰ 剩 {dte} 天"
    else: act = "持有"
    nm = f"{o['t']} {name} {o['exp'][5:]} {kkey(lo)}" + (f"/{kkey(hi)}" if legs == 2 else "")
    return {"side": "O", "t": nm, "last": val, "day": None, "pnl": pl / price * 100, "amt": pl * qty * 100, "act": act, "dte": dte}

def positions():
    raw = os.environ.get("HOLDINGS", "").strip()
    if not raw: return None
    try: hd = json.loads(raw)
    except Exception: return None
    out = []
    for side in ("L", "S"):
        for h in hd.get(side, []):
            d = chart(h["t"])
            if d and len(d["c"]) >= 30:
                out.append({"side": side, "t": h["t"], **analyze(h, d, side == "S")})
    opts = hd.get("O", [])
    if opts:
        today = pd.Timestamp.now(tz=TZ).strftime("%Y-%m-%d")
        fetch_chains(opts, today)
        for o in opts:
            try:
                r = analyze_opt(o, today)
                if r: out.append(r)
            except Exception as e:
                print("期权计算失败:", type(e).__name__)
    return out

def money(v): return ("-$" if v < 0 else "$") + f"{abs(v):,.0f}"
def pct(v): return f"{'+' if v > 0 else ''}{v:.2f}%"
def tag(side): return {"L": "🟢多", "S": "🔴空", "O": "🟣权"}[side]

def summary(pos):
    d, sc = load("data.json", {}), load("screen.json", {})
    lines = [f"<b>📊 美股频道 · 收盘总结</b>  {d.get('updated', '')}", ""]
    for it in d.get("items", []):
        x = it["d1"]
        lines.append(f"{it['name']}：<b>{'偏多' if x['bull'] else '偏空'} {pct(x['pct'])}</b>（{x['bars']} 天）")
    v = d.get("vix")
    if v: lines.append(f"VIX：<b>{v['value']}</b>（{pct(v['pct'])}）")
    pool = [x for x in d.get("sectors", []) + d.get("themes", []) + sc.get("rows", []) if x.get("sig") and x.get("sig_age") == 0]
    for k, nm in (("buy", "买入"), ("sell", "卖出")):
        ts = list(dict.fromkeys(x["ticker"] for x in pool if x["sig"] == k))
        lines.append(f"今日{nm}信号：{('、'.join(ts)) if ts else '无'}")
    lines.append("")
    if pos is None:
        lines.append("持仓：还没有同步（打开网页的持仓页一次即可）")
    elif not pos:
        lines.append("持仓：无")
    else:
        lines.append("<b>持仓</b>")
        for p in sorted(pos, key=lambda p: (p["act"] == "持有", p["side"], -p["pnl"])):
            a = p["act"] if p["act"] == "持有" else f"<b>{p['act']}</b>"
            day = f"今日 {pct(p['day'])}  " if p["day"] is not None else f"剩 {p['dte']} 天  "
            lines.append(f"{tag(p['side'])} {p['t']}  {day}累计 {pct(p['pnl'])}（{money(p['amt'])}）→ {a}")
        todo = sum(p["act"] != "持有" for p in pos)
        lines.append(f"总盈亏 <b>{money(sum(p['amt'] for p in pos))}</b> ｜ 今日需处理 <b>{todo}</b> 只")
    lines += ["", f'<a href="{SITE}">打开网站</a>']
    return "\n".join(lines)

def main():
    now = pd.Timestamp.now(tz=TZ)
    today, hm, wd = now.strftime("%Y-%m-%d"), now.hour * 60 + now.minute, now.weekday()
    st = load(STATE, {})
    if st.get("date") != today: st = {"date": today, "sent": [], "summary": st.get("summary")}
    force = os.environ.get("NOTIFY_NOW") == "true"
    pos = positions()

    # 盘中即时提醒（记录只存哈希，不在公开仓库里留下代码和价格）
    if wd < 5 and 9 * 60 + 30 <= hm <= 16 * 60 + 30 and pos:
        for p in pos:
            if p["act"] == "持有": continue
            key = hashlib.sha256(f"{today}|{p['side']}|{p['t']}|{p['act']}".encode()).hexdigest()[:16]
            if key in st["sent"]: continue
            msg = (f"<b>{p['act']}</b>  {tag(p['side'])} {p['t']}\n现价 {p['last']:.2f}　累计 {pct(p['pnl'])}（{money(p['amt'])}）\n"
                   f"盘中数据，按日线收盘确认\n<a href=\"{SITE}\">打开网站</a>")
            if send(msg): st["sent"].append(key)

    # 收盘总结：工作日 17:00 以后一次；手动运行时勾选“立即发送”可随时发
    if force or (wd < 5 and hm >= 17 * 60 and st.get("summary") != today):
        if send(summary(pos)) and not force: st["summary"] = today

    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f)

if __name__ == "__main__":
    main()
