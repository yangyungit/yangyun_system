import yfinance as yf
import pandas as pd
import numpy as np

pd.set_option("display.width", 200)

# ICE 原糖合约月份码：H(3) K(5) N(7) V(10)
codes = []
for yr in [2026, 2027, 2028]:
    for mc, mn in [("H", 3), ("K", 5), ("N", 7), ("V", 10)]:
        codes.append((f"SB{mc}{str(yr)[-2:]}.NYB", yr, mn))

print("=== 糖期货远期曲线 ===")
curve = []
for sym, yr, mn in codes:
    try:
        h = yf.Ticker(sym).history(period="10d")["Close"].dropna()
        if len(h):
            curve.append((sym, f"{yr}-{mn:02d}", round(float(h.iloc[-1]), 2)))
    except Exception:
        pass
cv = pd.DataFrame(curve, columns=["合约", "到期", "价格"])
if len(cv) > 1:
    front = cv["价格"].iloc[0]
    cv["相对近月"] = ((cv["价格"] / front - 1) * 100).round(1).astype(str) + "%"
print(cv.to_string(index=False))

# --- 周期长度 ---
px = yf.Ticker("SB=F").history(period="max", auto_adjust=False)["Close"]
px.index = px.index.tz_localize(None)
m = px.resample("ME").last()
peaks = [("2003-02", 8.90), ("2006-01", 18.02), ("2008-02", 14.27), ("2011-01", 33.97),
         ("2016-09", 22.53), ("2021-08", 19.84), ("2023-10", 27.09)]
troughs = [("2002-04", 5.68), ("2003-12", 5.67), ("2007-06", 9.07), ("2010-05", 14.19),
           ("2015-08", 10.69), ("2018-09", 10.42), ("2020-04", 10.39), ("2022-07", 17.54)]

def gaps(pts, label):
    d = [pd.Timestamp(p[0] + "-01") for p in pts]
    g = [round((d[i + 1] - d[i]).days / 30.44) for i in range(len(d) - 1)]
    print(f"\n{label}间隔(月): {g}  中位={int(np.median(g))}  均值={np.mean(g):.0f}")

gaps(peaks, "峰-峰")
gaps(troughs, "谷-谷")

last_peak = pd.Timestamp("2023-10-01")
now = m.index[-1]
print(f"\n距上个高点 2023-10 已 {round((now-last_peak).days/30.44)} 个月")
print(f"本轮低点 2026-04 (13.43 日内低), 当前 {m.iloc[-1]:.2f}, 距低点 {(m.iloc[-1]/13.43-1)*100:+.0f}%")

# 5年分位
w = px.tail(252 * 5)
print(f"当前 5 年分位: {(w < px.iloc[-1]).mean()*100:.0f}%")
print(f"2026-07-31 时的 5 年分位: {(px.loc[:'2026-07-31'].tail(252*5) < px.loc[:'2026-07-31'].iloc[-1]).mean()*100:.0f}%")

# CANE vs SB=F 跟踪
cane = yf.Ticker("CANE").history(period="6y", auto_adjust=True)["Close"]
cane.index = cane.index.tz_localize(None)
both = pd.concat([px.rename("SB"), cane.rename("CANE")], axis=1).dropna()
print("\n=== CANE 对 SB=F 的跟踪损耗 ===")
for yrs in [1, 3, 5]:
    sub = both.tail(252 * yrs)
    a = (sub["SB"].iloc[-1] / sub["SB"].iloc[0] - 1) * 100
    b = (sub["CANE"].iloc[-1] / sub["CANE"].iloc[0] - 1) * 100
    print(f"近{yrs}年: SB=F {a:+.1f}%  CANE {b:+.1f}%  差 {b-a:+.1f}pp")
