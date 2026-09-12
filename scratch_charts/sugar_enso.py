import os
import re
import urllib.request
import json
import pandas as pd
import numpy as np

pd.set_option("display.width", 220)

# --- ONI ---
raw = urllib.request.urlopen(
    "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt", timeout=30
).read().decode()
rows = []
for ln in raw.strip().splitlines()[1:]:
    p = ln.split()
    if len(p) == 4:
        rows.append((p[0], int(p[1]), float(p[3])))
oni = pd.DataFrame(rows, columns=["seas", "yr", "anom"])
SEAS = ["DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ", "JJA", "JAS", "ASO", "SON", "OND", "NDJ"]
oni["mon"] = oni["seas"].map(lambda s: SEAS.index(s) + 1)
oni["date"] = pd.to_datetime(dict(year=oni.yr, month=oni.mon, day=1))
oni = oni.set_index("date")["anom"].sort_index()

print("=== ONI 最近 18 期 ===")
print(oni.tail(18).to_string())

# 事件定义：ONI >= 0.5 连续 5 期以上
warm = oni >= 0.5
grp = (warm != warm.shift()).cumsum()
events = []
for _, seg in oni.groupby(grp):
    if (seg >= 0.5).all() and len(seg) >= 5:
        events.append(seg)

print("\n=== 历次厄尔尼诺事件（ONI>=0.5 连续>=5期）===")
for s in events:
    print(f"{s.index[0].date()} ~ {s.index[-1].date()}  时长{len(s)}期  峰值{s.max():+.2f} ({s.idxmax().date()})")

print("\n=== 各年 JJA（6-8月）ONI 排名 ===")
jja = oni[oni.index.month == 7]  # JJA 的中心月是7月
print(jja.sort_values(ascending=False).head(12).to_string())
print(f"\n2026 JJA 在 {len(jja)} 个年份里排第 {(jja > jja.loc['2026-07-01']).sum() + 1} 位")

# --- 糖价：FRED 全球糖价(ISA) 月度, 1990 起 ---
key = None
for ln in open("/Users/zhanghao/yangyun/Code_Projects/valuation-radar/.env"):
    mm = re.match(r"\s*FRED_API_KEY\s*=\s*(\S+)", ln)
    if mm:
        key = mm.group(1).strip('"').strip("'")
url = (
    "https://api.stlouisfed.org/fred/series/observations?series_id=PSUGAISAUSDM"
    f"&api_key={key}&file_type=json&observation_start=1980-01-01"
)
js = json.loads(urllib.request.urlopen(url, timeout=30).read())
sug = pd.Series(
    {pd.Timestamp(o["date"]): float(o["value"]) for o in js["observations"] if o["value"] != "."}
).sort_index()
print(f"\n=== FRED 糖价(ISA, 美分/磅) {sug.index.min().date()} ~ {sug.index.max().date()} 共{len(sug)}月 ===")
print(sug.tail(6).round(2).to_string())

# 事件研究：以 ONI 首次 >=0.5 的月份为 T0
print("\n=== 厄尔尼诺起点后糖价表现（T0 = ONI 首次>=0.5 的月）===")
res = []
for s in events:
    t0 = s.index[0]
    if t0 < sug.index.min():
        continue
    peak = s.max()
    row = {"起点": t0.date(), "峰值ONI": round(peak, 2)}
    try:
        base = sug.asof(t0)
        row["T0糖价"] = round(base, 2)
        for n in [3, 6, 9, 12, 18]:
            tgt = t0 + pd.DateOffset(months=n)
            if tgt <= sug.index.max():
                row[f"+{n}月"] = f"{(sug.asof(tgt)/base-1)*100:+.0f}%"
        # 事件期间最大涨幅（起点到峰值后6个月内）
        endw = s.index[-1] + pd.DateOffset(months=6)
        wnd = sug.loc[t0:min(endw, sug.index.max())]
        row["期内最高"] = f"{(wnd.max()/base-1)*100:+.0f}%"
    except Exception as e:
        row["err"] = str(e)
    res.append(row)
print(pd.DataFrame(res).to_string(index=False))

# 对照：拉尼娜起点
cold = oni <= -0.5
grpc = (cold != cold.shift()).cumsum()
cres = []
for _, seg in oni.groupby(grpc):
    if (seg <= -0.5).all() and len(seg) >= 5:
        t0 = seg.index[0]
        if t0 < sug.index.min():
            continue
        base = sug.asof(t0)
        r = {"起点": t0.date(), "谷值ONI": round(seg.min(), 2), "T0糖价": round(base, 2)}
        for n in [6, 12]:
            tgt = t0 + pd.DateOffset(months=n)
            if tgt <= sug.index.max():
                r[f"+{n}月"] = f"{(sug.asof(tgt)/base-1)*100:+.0f}%"
        cres.append(r)
print("\n=== 对照组：拉尼娜起点后糖价 ===")
print(pd.DataFrame(cres).to_string(index=False))
