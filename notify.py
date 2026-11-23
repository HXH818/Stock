# Telegram 提醒：在 bbi.py / screen.py 之后运行
#   盘中（美东 9:30–16:30，工作日）：持仓触发 止损 / 止盈 / 信号平仓 → 立即推送；同一只、同一提醒当天只发一次
#   收盘后（美东 17:00 以后，工作日）：每天一条总结（大盘、今日信号、持仓）
# 需要的 Secrets：TELEGRAM_TOKEN、TELEGRAM_CHAT_ID；持仓来自 HOLDINGS（网页自动加密上传）
# 注意：仓库是公开的，Actions 日志也公开，所以这里绝不打印持仓内容
import hashlib, json, os
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
    return out

def money(v): return ("-$" if v < 0 else "$") + f"{abs(v):,.0f}"
def pct(v): return f"{'+' if v > 0 else ''}{v:.2f}%"
def tag(side): return "🟢多" if side == "L" else "🔴空"

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
            lines.append(f"{tag(p['side'])} {p['t']}  今日 {pct(p['day'])}  累计 {pct(p['pnl'])}（{money(p['amt'])}）→ {a}")
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
