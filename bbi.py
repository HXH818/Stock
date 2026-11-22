# 由 GitHub Actions 定时运行：计算 ES/NQ 的日线和 4H BBI、11 个板块 ETF 的日线 BBI、VIX，写入 data.json
# 同时保存 K 线数据到 charts/，供网页画 K 线 + BBI + MACD 图
# BBI = (MA3 + MA6 + MA12 + MA24) / 4；价格 > BBI 为偏多，< BBI 为偏空
import json, os
import pandas as pd
import yfinance as yf

TZ = "America/New_York"

# (代码, 图表文件名, 显示名, 4H 切分起点) —— 与 TradingView 一致：期货 18:00 开盘起，每 4 小时一根
SYMBOLS = [
    ("ES=F", "ES", "标普 500（ES 期货）", (18, 0)),
    ("NQ=F", "NQ", "纳指 100（NQ 期货）", (18, 0)),
]
# 11 个板块 ETF（只看日线）
SECTORS = [("XLK", "科技"), ("XLC", "通信"), ("XLY", "可选消费"), ("XLP", "必需消费"), ("XLV", "医疗"),
           ("XLF", "金融"), ("XLI", "工业"), ("XLE", "能源"), ("XLB", "原材料"), ("XLU", "公用事业"), ("XLRE", "房地产")]
# 热点赛道（主题 ETF，只看日线）
THEMES = [("SOXX", "半导体"), ("DRAM", "存储"), ("LYTE", "光通信"), ("NCLD", "数据中心"), ("NLR", "核能"),
          ("ICLN", "清洁能源"), ("BUG", "网络安全"), ("WQTM", "量子"), ("JEDI", "无人机"), ("REMX", "稀土"), ("UFO", "航天"),
          ("GLD", "黄金"), ("IBIT", "比特币")]
N_CHART = 220   # 每张图保存的 K 线根数

def bbi(c):
    return (c.rolling(3).mean() + c.rolling(6).mean()
            + c.rolling(12).mean() + c.rolling(24).mean()) / 4

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

def ohlc(tk, period, interval):
    k = yf.Ticker(tk).history(period=period, interval=interval)[["Open", "High", "Low", "Close"]].dropna()
    if k.index.tz is not None:
        k.index = k.index.tz_convert(TZ)
    return k

def to_4h(k1, anchor):
    """把 1 小时 K 线合成 4 小时：从 anchor 起每 4 小时一组（开=首、高=最高、低=最低、收=末）"""
    h, m = anchor
    shifted = k1.index - pd.Timedelta(hours=h, minutes=m)
    key = shifted.normalize() + ((shifted - shifted.normalize()) // pd.Timedelta(hours=4)) * pd.Timedelta(hours=4)
    g = k1.groupby(key)
    k4 = g.agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
    k4.index = g.apply(lambda x: x.index[0])          # 每根 4H 线的开始时间
    return k4

def pack(k, intraday=False):
    """K 线打包成网页用的格式；日内图的时间用“美东时间当作 UTC”的秒数，网页上直接显示美东时间"""
    k = k.tail(N_CHART)
    d = ([int(x.tz_localize(None).timestamp()) for x in k.index] if intraday
         else [x.strftime("%Y-%m-%d") for x in k.index])
    return {"d": d, **{c.lower()[0]: [round(float(v), 2) for v in k[c]] for c in ["Open", "High", "Low", "Close"]}}

def save_chart(key, obj):
    with open(f"charts/{key}.json", "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)

if __name__ == "__main__":
    os.makedirs("charts", exist_ok=True)
    now = pd.Timestamp.now(tz=TZ)
    out = {"updated": now.strftime("%Y-%m-%d %H:%M") + " 美东", "items": []}

    # ES / NQ：日线 + 4H
    for tk, key, name, anchor in SYMBOLS:
        try:
            d1 = ohlc(tk, "1y", "1d")
            h4 = to_4h(ohlc(tk, "60d", "1h"), anchor)
            out["items"].append({"name": name, "symbol": tk, "key": key,
                                 "d1": summarize(d1["Close"]), "h4": summarize(h4["Close"])})
            save_chart(key, {"t": key, "name": name, "frames": {"日线": pack(d1), "4H": pack(h4, intraday=True)}})
        except Exception as e:
            print("失败:", tk, e)

    # 行业板块 + 热点赛道：日线 BBI + 52 周位置
    out["sectors"], out["themes"] = [], []
    for group, lst, suffix in (("sectors", SECTORS, "板块 ETF"), ("themes", THEMES, " ETF")):
      for tk, name in lst:
        try:
            h = ohlc(tk, "1y", "1d")
            if len(h) < 30:                                   # 数据太少（新上市或代码无效）就跳过
                print("数据不足:", tk, len(h)); continue
            r = summarize(h["Close"])
            y = h.tail(252)                                   # 最近 52 周
            hi, lo, c = float(y["High"].max()), float(y["Low"].min()), float(y["Close"].iloc[-1])
            w52 = {"hi52": round(hi, 2), "lo52": round(lo, 2),
                   "pos": round((c - lo) / (hi - lo), 4) if hi > lo else 0.5,
                   "from_hi": round((c / hi - 1) * 100, 2), "from_lo": round((c / lo - 1) * 100, 2),
                   "nh": bool(y["High"].iloc[-1] >= y["High"].iloc[:-1].max()),   # 今天创 52 周新高
                   "nl": bool(y["Low"].iloc[-1] <= y["Low"].iloc[:-1].min())}     # 今天创 52 周新低
            w52["young"] = len(h) < 240                       # 上市不足一年：52 周位置按上市以来计算
            sig, age = ha_signal(h)
            out[group].append({"ticker": tk, "name": name, **r, **w52, "new": r["bull"] and r["bars"] == 1, "sig": sig, "sig_age": age})
            save_chart(tk, {"t": tk, "name": name + suffix, **pack(h)})
        except Exception as e:
            print("失败:", tk, e)

    # VIX：最新值和涨跌；图表只画 K 线 + MACD（不画 BBI）
    try:
        v = ohlc("^VIX", "1y", "1d")
        c = v["Close"]
        out["vix"] = {"value": round(float(c.iloc[-1]), 2),
                      "chg": round(float(c.iloc[-1] - c.iloc[-2]), 2),
                      "pct": round(float(c.iloc[-1] / c.iloc[-2] - 1) * 100, 2)}
        save_chart("VIX", {"t": "VIX", "name": "VIX 恐慌指数", "nobbi": True, **pack(v)})
    except Exception as e:
        print("失败: ^VIX", e)

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(json.dumps({k: out[k] for k in ("updated", "items")}, ensure_ascii=False, indent=1))
