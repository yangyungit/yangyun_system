import io
import re
import json
import zipfile
import urllib.request
from pathlib import Path

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

# 每个作物年度的 ENSO 状态：用 OND~NDJ（厄尔尼诺/拉尼娜的成熟期）
peak = oni[oni.index.month.isin([11, 12])].groupby(lambda d: d.year).mean()
peak.name = "ONI_峰期"

# --- FRED ---
key = None
for ln in open("/Users/zhanghao/yangyun/Code_Projects/valuation-radar/.env"):
    m = re.match(r"\s*FRED_API_KEY\s*=\s*(\S+)", ln)
    if m:
        key = m.group(1).strip('"').strip("'")


def fred(sid):
    u = (f"https://api.stlouisfed.org/fred/series/observations?series_id={sid}"
         f"&api_key={key}&file_type=json&observation_start=1980-01-01")
    js = json.loads(urllib.request.urlopen(u, timeout=30).read())
    return pd.Series({pd.Timestamp(o["date"]): float(o["value"])
                      for o in js["observations"] if o["value"] != "."}).sort_index()


sug = fred("PSUGAISAUSDM")        # 糖 ISA
allc = fred("PALLFNFINDEXM")      # IMF 全部商品价格指数

# --- 拉尼娜事件：糖 vs 商品指数 超额 ---
cold = oni <= -0.5
g = (cold != cold.shift()).cumsum()
res = []
for _, seg in oni.groupby(g):
    if not (seg <= -0.5).all() or len(seg) < 5:
        continue
    t0 = seg.index[0]
    if t0 < max(sug.index.min(), allc.index.min()):
        continue
    r = {"起点": t0.date(), "谷值ONI": round(seg.min(), 2)}
    for n in [6, 12]:
        t1 = t0 + pd.DateOffset(months=n)
        if t1 > min(sug.index.max(), allc.index.max()):
            continue
        s = sug.asof(t1) / sug.asof(t0) - 1
        a = allc.asof(t1) / allc.asof(t0) - 1
        r[f"糖+{n}月"] = f"{s*100:+.0f}%"
        r[f"商品指数+{n}月"] = f"{a*100:+.0f}%"
        r[f"超额+{n}月"] = f"{(s-a)*100:+.0f}pp"
    res.append(r)
print("=== 拉尼娜起点后：糖 vs IMF 全商品指数 ===")
print(pd.DataFrame(res).to_string(index=False))

# 厄尔尼诺同样口径
warm = oni >= 0.5
gw = (warm != warm.shift()).cumsum()
res2 = []
for _, seg in oni.groupby(gw):
    if not (seg >= 0.5).all() or len(seg) < 5:
        continue
    t0 = seg.index[0]
    if t0 < max(sug.index.min(), allc.index.min()):
        continue
    r = {"起点": t0.date(), "峰值ONI": round(seg.max(), 2)}
    for n in [6, 12]:
        t1 = t0 + pd.DateOffset(months=n)
        if t1 > min(sug.index.max(), allc.index.max()):
            continue
        s = sug.asof(t1) / sug.asof(t0) - 1
        a = allc.asof(t1) / allc.asof(t0) - 1
        r[f"糖+{n}月"] = f"{s*100:+.0f}%"
        r[f"超额+{n}月"] = f"{(s-a)*100:+.0f}pp"
    res2.append(r)
print("\n=== 厄尔尼诺起点后：糖 超额 ===")
print(pd.DataFrame(res2).to_string(index=False))

# --- 产量：谁被什么天气打 ---
PSD = Path(__file__).with_name("cache") / "psd_sugar.csv"
if not PSD.exists():
    PSD.parent.mkdir(exist_ok=True)
    z = urllib.request.urlopen(
        "https://apps.fas.usda.gov/psdonline/downloads/psd_sugar_csv.zip", timeout=60).read()
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        PSD.write_bytes(zf.read("psd_sugar.csv"))
d = pd.read_csv(PSD)
prod = d[d.Attribute_Description == "Production"].pivot_table(
    index="Market_Year", columns="Country_Name", values="Value", aggfunc="sum") / 1000.0
world = prod.sum(axis=1)
tab = pd.DataFrame({
    "全球": world, "巴西": prod.get("Brazil"), "印度": prod.get("India"),
    "泰国": prod.get("Thailand"),
})
gr = tab.pct_change() * 100
gr["ONI峰期"] = peak.reindex(gr.index)
gr = gr.dropna(subset=["ONI峰期"]).loc[1990:]
gr["天气"] = np.where(gr["ONI峰期"] >= 0.5, "厄尔尼诺",
                    np.where(gr["ONI峰期"] <= -0.5, "拉尼娜", "中性"))
print("\n=== 作物年度产量同比(%) 按 ENSO 分组 ===")
print(gr.groupby("天气")[["全球", "巴西", "印度", "泰国"]].agg(["mean", "median", "count"]).round(1).to_string())
print("\n=== 逐年明细 (1990 起) ===")
print(gr.round(1).to_string())

# 库存消费比 vs 价格
end = d[d.Attribute_Description == "Ending Stocks"].groupby("Market_Year").Value.sum() / 1000
use = d[d.Attribute_Description == "Human Dom. Consumption"].groupby("Market_Year").Value.sum() / 1000
stu = (end / use * 100).loc[1990:]
yr_px = sug.groupby(sug.index.year).mean()
j = pd.DataFrame({"库存消费比%": stu, "年均糖价": yr_px}).dropna()
print(f"\n=== 库存消费比 vs 年均糖价 相关系数: {j.corr().iloc[0,1]:.2f} ===")
print(j.round(1).tail(22).to_string())
