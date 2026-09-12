import yfinance as yf
import pandas as pd
import numpy as np

pd.set_option("display.width", 200)

px = yf.Ticker("SB=F").history(period="max", auto_adjust=False)["Close"]
px.index = px.index.tz_localize(None)
m = px.resample("ME").last()

print("=== 糖 SB=F 月线 ===")
print("区间", m.index.min().date(), "->", m.index.max().date(), "共", len(m), "个月")

# 主要波峰波谷（12 个月滚动极值确认）
win = 12
peaks, troughs = [], []
for i in range(win, len(m) - win):
    w = m.iloc[i - win : i + win + 1]
    if m.iloc[i] == w.max():
        peaks.append((m.index[i].date(), round(m.iloc[i], 2)))
    if m.iloc[i] == w.min():
        troughs.append((m.index[i].date(), round(m.iloc[i], 2)))

print("\n--- 波峰(前后各12个月最高) ---")
for p in peaks:
    print(p)
print("\n--- 波谷 ---")
for t in troughs:
    print(t)

print("\n=== 近 36 个月 月末收盘与月涨幅 ===")
tail = m.tail(36)
out = pd.DataFrame({"close": tail.round(2), "mom%": (tail.pct_change() * 100).round(1)})
print(out.to_string())

print("\n=== 关键区间涨幅 ===")
last = m.iloc[-1]
for lbl, n in [("近1月", 1), ("近3月", 3), ("近6月", 6), ("近12月", 12), ("近24月", 24)]:
    print(f"{lbl}: {(last/m.iloc[-1-n]-1)*100:+.1f}%  (从 {m.iloc[-1-n]:.2f} 到 {last:.2f})")

print("\n=== 日线近 120 个交易日 每 10 日 ===")
print(px.tail(120).iloc[::10].round(2).to_string())

# 年度季节性：每个月平均涨幅
ret = m.pct_change()
seas = ret.groupby(ret.index.month).agg(["mean", "median", "count"])
print("\n=== 月度季节性(2000-2026, 月涨幅%) ===")
print((seas[["mean", "median"]] * 100).round(2).join(seas["count"]).to_string())
