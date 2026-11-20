# 由 GitHub Actions 定时运行：计算日线和 4H 的 BBI 多空，写入 data.json
# BBI = (MA3 + MA6 + MA12 + MA24) / 4；价格 > BBI 为偏多，< BBI 为偏空
import json, datetime
import pandas as pd
import yfinance as yf

# (代码, 显示名, 4H 切分起点)  —— 与 TradingView 一致
#   指数：常规时段 9:30 起，切成 9:30–13:30、13:30–16:00
#   期货：18:00 开盘起，每 4 小时一根（最后一根 14:00–17:00）
SYMBOLS = [
    ("ES=F", "标普 500（ES 期货）", (18, 0)),
    ("NQ=F", "纳指 100（NQ 期货）", (18, 0)),
]
TZ = "America/New_York"

def bbi(c):
    return (c.rolling(3).mean() + c.rolling(6).mean()
            + c.rolling(12).mean() + c.rolling(24).mean()) / 4

def to_4h(c1, anchor):
    """把 1 小时收盘价合成 4 小时：从 anchor 起每 4 小时一组，取组内最后一个收盘价"""
    h, m = anchor
    shifted = c1.index - pd.Timedelta(hours=h, minutes=m)
    key = shifted.normalize() + ((shifted - shifted.normalize()) // pd.Timedelta(hours=4)) * pd.Timedelta(hours=4)
    return c1.groupby(key).last()

def summarize(c):
    b = bbi(c)
    c, b = c[b.notna()], b.dropna()
    above = (c > b).tolist()
    bars = 1
    for v in reversed(above[:-1]):
        if v != above[-1]: break
        bars += 1
    return {"bull": above[-1], "close": round(float(c.iloc[-1]), 2),
            "bbi": round(float(b.iloc[-1]), 2),
            "pct": round(float(c.iloc[-1] / b.iloc[-1] - 1) * 100, 2), "bars": bars}

def fetch(tk, period, interval):
    c = yf.Ticker(tk).history(period=period, interval=interval)["Close"].dropna()
    if c.index.tz is not None:
        c.index = c.index.tz_convert(TZ)
    return c

if __name__ == "__main__":
    now = pd.Timestamp.now(tz=TZ)
    out = {"updated": now.strftime("%Y-%m-%d %H:%M") + " 美东", "items": []}
    for tk, name, anchor in SYMBOLS:
        try:
            out["items"].append({
                "name": name, "symbol": tk,
                "d1": summarize(fetch(tk, "1y", "1d")),
                "h4": summarize(to_4h(fetch(tk, "60d", "1h"), anchor)),
            })
        except Exception as e:
            print("失败:", tk, e)
    try:  # VIX：只取最新值和前一日收盘
        v = fetch("^VIX", "10d", "1d")
        out["vix"] = {"value": round(float(v.iloc[-1]), 2),
                      "chg": round(float(v.iloc[-1] - v.iloc[-2]), 2),
                      "pct": round(float(v.iloc[-1] / v.iloc[-2] - 1) * 100, 2)}
    except Exception as e:
        print("失败: ^VIX", e)
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(json.dumps(out, ensure_ascii=False, indent=1))
