# 每个交易日收盘后由 GitHub Actions 运行：计算 BBI 多空，写入 data.json
import json, datetime
import yfinance as yf

SYMBOLS = [("^GSPC", "标普 500"), ("^NDX", "纳斯达克 100")]
N_SHOW = 120  # 网页上画最近多少个交易日

def bbi_state(close):
    bbi = (close.rolling(3).mean() + close.rolling(6).mean()
           + close.rolling(12).mean() + close.rolling(24).mean()) / 4
    c, b = close[bbi.notna()], bbi.dropna()
    above = (c > b).tolist()
    days = 1                       # 当前多/空已持续几天
    for v in reversed(above[:-1]):
        if v != above[-1]: break
        days += 1
    return c, b, above[-1], days

out = {"updated": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "items": []}
for tk, name in SYMBOLS:
    close = yf.Ticker(tk).history(period="1y", interval="1d")["Close"].dropna()
    c, b, bull, days = bbi_state(close)
    c, b = c[-N_SHOW:], b[-N_SHOW:]
    out["items"].append({
        "name": name, "symbol": tk,
        "date": c.index[-1].strftime("%Y-%m-%d"),
        "close": round(float(c.iloc[-1]), 2),
        "bbi": round(float(b.iloc[-1]), 2),
        "pct": round(float(c.iloc[-1] / b.iloc[-1] - 1) * 100, 2),
        "state": "多" if bull else "空",
        "days": days,
        "dates": [d.strftime("%m-%d") for d in c.index],
        "closes": [round(float(x), 2) for x in c],
        "bbis": [round(float(x), 2) for x in b],
    })

with open("data.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
print(json.dumps({i["name"]: [i["state"], i["days"], i["pct"]] for i in out["items"]}, ensure_ascii=False))
