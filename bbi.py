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

# 11 个板块 ETF（只看日线）
SECTORS = [("XLK", "科技"), ("XLC", "通信"), ("XLY", "可选消费"), ("XLP", "必需消费"), ("XLV", "医疗"),
           ("XLF", "金融"), ("XLI", "工业"), ("XLE", "能源"), ("XLB", "原材料"), ("XLU", "公用事业"), ("XLRE", "房地产")]

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
    # 板块：日线 BBI，并保存 K 线数据供网页画图
    import os
    os.makedirs("charts", exist_ok=True)
    out["sectors"] = []
    for tk, name in SECTORS:
        try:
            h = yf.Ticker(tk).history(period="1y", interval="1d")[["Open", "High", "Low", "Close"]].dropna()
            r = summarize(h["Close"])
            y = h.tail(252)                                   # 最近 52 周
            hi, lo, c = float(y["High"].max()), float(y["Low"].min()), float(y["Close"].iloc[-1])
            w52 = {"hi52": round(hi, 2), "lo52": round(lo, 2),
                   "pos": round((c - lo) / (hi - lo), 4) if hi > lo else 0.5,
                   "from_hi": round((c / hi - 1) * 100, 2), "from_lo": round((c / lo - 1) * 100, 2),
                   "nh": bool(y["High"].iloc[-1] >= y["High"].iloc[:-1].max()),   # 今天创 52 周新高
                   "nl": bool(y["Low"].iloc[-1] <= y["Low"].iloc[:-1].min())}     # 今天创 52 周新低
            out["sectors"].append({"ticker": tk, "name": name, **r, **w52, "new": r["bull"] and r["bars"] == 1})
            k = h.tail(220)
            with open(f"charts/{tk}.json", "w", encoding="utf-8") as f:
                json.dump({"t": tk, "name": name + "板块 ETF", "d": [x.strftime("%Y-%m-%d") for x in k.index],
                           "o": [round(float(v), 2) for v in k["Open"]], "h": [round(float(v), 2) for v in k["High"]],
                           "l": [round(float(v), 2) for v in k["Low"]], "c": [round(float(v), 2) for v in k["Close"]]}, f)
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
