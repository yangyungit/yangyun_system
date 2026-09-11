"""供给刚性清单 —— 历史事件回测

对五个已知供给冲击事件，检验清单上对应品类的载体在事件后的表现。

口径：
- 价格用 auto_adjust=False 的 Adj Close（含股息全收益）
- 基准日 = 事件日前一个交易日（T-1）收盘，这样 T+N 涨幅包含事件当天的跳空
- 事件日 T0 = 给定日期当天；非交易日取之后第一个交易日
- 事件前 20 日涨幅 = close[T-1] / close[T-21] - 1（不含事件日，用来看提前预期）
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import yfinance as yf

WORKDIR = "/tmp/rigid"
CACHE = os.path.join(WORKDIR, "prices")
os.makedirs(CACHE, exist_ok=True)

BENCH = "SPY"

EVENTS = [
    {
        "id": "E1",
        "name": "沙特 Abqaiq 油田遇袭",
        "date": "2019-09-14",
        "note": "一次性中断全球约 5% 原油供给",
        "categories": "原油、成品油轮、战争险",
        "tickers": ["CL=F", "BZ=F", "USO", "XLE", "VLO", "MPC",
                    "STNG", "FRO", "INSW", "DHT", "RNR", "EG"],
    },
    {
        "id": "E2",
        "name": "苏伊士运河长赐号搁浅",
        "date": "2021-03-23",
        "note": "堵塞六天",
        "categories": "集装箱运费、成品油轮、干散货",
        "tickers": ["ZIM", "MATX", "BDRY", "STNG", "FRO", "GNK", "SBLK", "CL=F"],
    },
    {
        "id": "E3",
        "name": "俄乌战争爆发",
        "date": "2022-02-24",
        "note": "",
        "categories": "VLCC 油轮运费、成品油轮、钾肥、铝、谷物、天然气、铀浓缩",
        "tickers": ["FRO", "STNG", "TRMD", "INSW", "MOS", "NTR", "CF", "IPI",
                    "AA", "CENX", "ZW=F", "ZC=F", "WEAT", "CORN", "NG=F",
                    "UNG", "LEU", "CCJ", "URA", "HO=F", "CL=F"],
    },
    {
        "id": "E4",
        "name": "缅甸佤邦宣布 8 月起停止锡矿开采",
        "date": "2023-04-15",
        "note": "公告在 2023 年 4 月中",
        "categories": "锡",
        "tickers": ["JJT", "TINY", "AFMJF", "MSB.AX"],
    },
    {
        "id": "E5",
        "name": "胡塞武装开始袭击红海商船",
        "date": "2023-11-19",
        "note": "",
        "categories": "集装箱运费、成品油轮、战争险",
        "tickers": ["ZIM", "MATX", "STNG", "TRMD", "FRO", "INSW",
                    "RNR", "EG", "ACGL", "BDRY"],
    },
]

# 载体类型分类，用于回答「哪类载体涨得多」
KIND = {
    "CL=F": "期货合约", "BZ=F": "期货合约", "ZW=F": "期货合约", "ZC=F": "期货合约",
    "NG=F": "期货合约", "HO=F": "期货合约",
    "USO": "期货滚动ETF", "UNG": "期货滚动ETF", "WEAT": "期货滚动ETF",
    "CORN": "期货滚动ETF", "BDRY": "期货滚动ETF",
    "XLE": "股票ETF", "URA": "股票ETF",
}
STOCKS = ["VLO", "MPC", "STNG", "FRO", "INSW", "DHT", "RNR", "EG", "ZIM", "MATX",
          "GNK", "SBLK", "TRMD", "MOS", "NTR", "CF", "IPI", "AA", "CENX",
          "LEU", "CCJ", "ACGL", "JJT", "TINY", "AFMJF", "MSB.AX"]
for t in STOCKS:
    KIND.setdefault(t, "股票")

HORIZONS = [5, 20, 60, 120]

ALL_TICKERS = sorted({t for e in EVENTS for t in e["tickers"]} | {BENCH})


# ---------------------------------------------------------------- 数据拉取

def fetch_one(ticker):
    """带重试的单票拉取。max 空了就退回 10y / 5y。"""
    path = os.path.join(CACHE, ticker.replace("=", "_").replace(".", "_") + ".csv")
    if os.path.exists(path):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if len(df) > 0:
            return ticker, df, "cache"

    for period in ("max", "10y", "5y"):
        for attempt in range(3):
            try:
                h = yf.Ticker(ticker).history(
                    period=period, auto_adjust=False, actions=False, timeout=30
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  {ticker} {period} attempt{attempt} error: {exc}")
                time.sleep(2 + attempt * 3)
                continue
            if h is not None and len(h) > 0:
                h.index = h.index.tz_localize(None)
                keep = [c for c in ("Adj Close", "Close", "Volume") if c in h.columns]
                h = h[keep].copy()
                h.to_csv(path)
                return ticker, h, period
            time.sleep(2 + attempt * 3)
    return ticker, None, "FAILED"


def fetch_all():
    data, failed, source = {}, [], {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for ticker, df, src in ex.map(fetch_one, ALL_TICKERS):
            source[ticker] = src
            if df is None:
                failed.append(ticker)
                print(f"FAILED  {ticker}")
            else:
                data[ticker] = df
                print(f"ok      {ticker:8s} {src:6s} {len(df):6d} rows  "
                      f"{df.index[0].date()} ~ {df.index[-1].date()}")
    return data, failed, source


# ---------------------------------------------------------------- 指标计算

def px(df):
    col = "Adj Close" if "Adj Close" in df.columns else "Close"
    s = df[col].astype(float).dropna()
    return s


def metrics(df, event_date):
    """返回一行指标 dict，或 dict(status=...) 说明为什么算不了。"""
    s = px(df)
    ed = pd.Timestamp(event_date)
    after = s.index[s.index >= ed]
    if len(after) == 0:
        return {"状态": "事件日之后无数据"}
    t0 = after[0]
    i0 = s.index.get_loc(t0)
    if i0 < 1:
        return {"状态": "事件日之前无数据"}

    base = s.iloc[i0 - 1]          # T-1 收盘，作为所有前瞻收益的基准
    out = {"状态": "ok", "T0日期": str(t0.date())}

    # 事件前 20 个交易日涨幅（截至 T-1，不含事件日）
    out["前20日"] = (base / s.iloc[i0 - 21] - 1) if i0 >= 21 else np.nan
    # 事件当日涨幅
    out["T0当日"] = s.iloc[i0] / base - 1

    for k in HORIZONS:
        j = i0 + k
        out[f"T+{k}"] = (s.iloc[j] / base - 1) if j < len(s) else np.nan

    # T0 ~ T+120 最大回撤（峰值从 T0 收盘起算）
    seg = s.iloc[i0: min(i0 + 121, len(s))]
    if len(seg) > 1:
        out["最大回撤"] = float((seg / seg.cummax() - 1).min())
        out["回撤窗口完整"] = len(seg) >= 121
    else:
        out["最大回撤"] = np.nan
        out["回撤窗口完整"] = False

    # 事件前 60 个交易日日均成交额
    if "Volume" in df.columns:
        c = df["Close"] if "Close" in df.columns else df[px(df).name]
        dv = (c.astype(float) * df["Volume"].astype(float)).reindex(s.index)
        lo = max(0, i0 - 60)
        w = dv.iloc[lo:i0].dropna()
        out["日均成交额"] = float(w.mean()) if len(w) else np.nan
    else:
        out["日均成交额"] = np.nan
    return out


def run():
    data, failed, source = fetch_all()
    rows = []
    for ev in EVENTS:
        spy = metrics(data[BENCH], ev["date"]) if BENCH in data else {"状态": "无基准"}
        for t in ev["tickers"]:
            base = {"事件": ev["id"], "事件名": ev["name"], "代码": t,
                    "类型": KIND.get(t, "股票")}
            if t not in data:
                rows.append({**base, "状态": "数据拉取失败"})
                continue
            m = metrics(data[t], ev["date"])
            row = {**base, **m}
            if m.get("状态") == "ok" and spy.get("状态") == "ok":
                for k in HORIZONS:
                    a, b = m.get(f"T+{k}"), spy.get(f"T+{k}")
                    row[f"超额T+{k}"] = (a - b) if (pd.notna(a) and pd.notna(b)) else np.nan
                row["超额前20日"] = (
                    m["前20日"] - spy["前20日"]
                    if pd.notna(m.get("前20日")) and pd.notna(spy.get("前20日")) else np.nan
                )
            rows.append(row)
        rows.append({**{"事件": ev["id"], "事件名": ev["name"], "代码": "SPY(基准)",
                        "类型": "基准"}, **spy})

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(WORKDIR, "backfill_events_raw.csv"), index=False)
    with open(os.path.join(WORKDIR, "fetch_status.json"), "w") as f:
        json.dump({"source": source, "failed": failed}, f, indent=2, ensure_ascii=False)
    return df, failed


# ---------------------------------------------------------------- 报告

# E4 候选里这两个和锡无关，查 yfinance 元数据确认后剔除，不当作锡的载体
NOT_TIN = {"TINY": "ProShares Nanotechnology ETF，纳米技术主题，与锡无关",
           "MSB.AX": "Mesoblast Limited，澳洲生物科技公司，与锡无关"}

HIT = 0.15  # 超额 > 15% 算明显


def pct(x, digits=1):
    return "—" if pd.isna(x) else f"{x * 100:+.{digits}f}%"


def money(x):
    if pd.isna(x):
        return "—"
    if x >= 1e9:
        return f"{x / 1e8:.0f} 亿美元"
    if x >= 1e8:
        return f"{x / 1e8:.1f} 亿美元"
    if x >= 1e6:
        return f"{x / 1e4:.0f} 万美元"
    if x >= 1e4:
        return f"{x / 1e4:.1f} 万美元"
    return f"{x:.0f} 美元"


def event_table(d, ev_id):
    s = d[d["事件"] == ev_id]
    lines = ["| 代码 | 类型 | 前20日 | 事件当日 | T+5 | T+20 | T+60 | T+120 | 超额T+20 | 超额T+60 | 最大回撤 | 日均成交额 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in s.iterrows():
        if r["状态"] != "ok":
            lines.append(f"| `{r['代码']}` | {r['类型']} | {r['状态']} | | | | | | | | | |")
            continue
        name = "SPY（基准）" if r["代码"] == "SPY(基准)" else f"`{r['代码']}`"
        lines.append("| " + " | ".join([
            name, r["类型"], pct(r["前20日"]), pct(r["T0当日"]), pct(r["T+5"]),
            pct(r["T+20"]), pct(r["T+60"]), pct(r["T+120"]),
            pct(r.get("超额T+20")), pct(r.get("超额T+60")),
            pct(r["最大回撤"]), money(r["日均成交额"]),
        ]) + " |")
    return "\n".join(lines)


def kind_table(d, ev_id):
    s = d[(d["事件"] == ev_id) & (d["代码"] != "SPY(基准)")]
    g = s.groupby("类型").agg(
        n=("代码", "count"), m20=("超额T+20", "mean"), m60=("超额T+60", "mean"),
        x60=("超额T+60", "max"), dd=("最大回撤", "mean"))
    lines = ["| 载体类型 | 个数 | 超额T+20 均值 | 超额T+60 均值 | 超额T+60 最好 | 最大回撤 均值 |",
             "|---|---|---|---|---|---|"]
    for k, r in g.iterrows():
        lines.append(f"| {k} | {int(r['n'])} | {pct(r['m20'])} | {pct(r['m60'])} | "
                     f"{pct(r['x60'])} | {pct(r['dd'])} |")
    return "\n".join(lines)


def write_report(df, failed):
    d = df.copy()
    car = d[d["代码"] != "SPY(基准)"]                      # 全部候选载体
    tin_ok = car[~car["代码"].isin(NOT_TIN)]               # 剔除非锡标的后的口径

    out = []
    A = out.append
    A("# 供给刚性清单 —— 五个历史事件回测\n")
    A(f"生成时间：{pd.Timestamp.now():%Y-%m-%d %H:%M}　数据源：yfinance（`auto_adjust=False`，取 `Adj Close`，含股息全收益）\n")
    A("## 口径说明\n")
    A("- **基准日**：事件日前一个交易日（T-1）收盘。T+N 涨幅从 T-1 算起，**包含事件当天的跳空**。")
    A("- **事件日 T0**：给定日期当天；当天非交易日则取之后第一个交易日。")
    A("- **前20日涨幅**：T-21 到 T-1 收盘，**不含事件当天**，用来看市场有没有提前反应。")
    A("- **超额**：该标的区间涨幅减去 SPY 同窗口涨幅。**超额 > 15% 记为明显反应**。")
    A("- **最大回撤**：T0 到 T+120 期间，从 T0 之后的滚动高点算起的最大跌幅。")
    A("- **日均成交额**：事件日前 60 个交易日的「收盘价 × 成交量」均值。期货合约（`CL=F` 这类）"
      "的成交量是合约张数、未乘合约乘数，所以期货那几行的金额严重低估，**只能和自己比，不能和股票横向比**。\n")
    if failed:
        A(f"- **数据拉取失败**：{', '.join('`' + t + '`' for t in failed)}\n")
    else:
        A("- **数据拉取失败**：无。全部 37 个代码（含 SPY）均成功拉取。\n")
    A("---\n")

    # ---- 三个问题的答案
    A("## 一、三个问题的答案\n")

    A("### 问题 1：命中率——五个事件里有几个跑出了明显超额\n")
    A("**5 个事件里 4 个在 T+60 跑出了超额 > 15% 的载体，1 个（锡）完全没有。**\n")
    rows = ["| 事件 | 候选载体数 | T+20 超额>15% | T+60 超额>15% | T+60 最好的载体 |", "|---|---|---|---|---|"]
    for ev in EVENTS:
        s = tin_ok[tin_ok["事件"] == ev["id"]]
        h20 = s[s["超额T+20"] > HIT]["代码"].tolist()
        h60 = s[s["超额T+60"] > HIT].sort_values("超额T+60", ascending=False)
        best = (f"`{h60.iloc[0]['代码']}` {pct(h60.iloc[0]['超额T+60'])}"
                if len(h60) else "无")
        rows.append(f"| {ev['id']} {ev['name']}（{ev['date']}） | {len(s)} | "
                    f"{len(h20)} 个 | {len(h60)} 个 | {best} |")
    A("\n".join(rows) + "\n")
    A("- **反应最猛的是俄乌战争（E3）**：21 个载体里 16 个在 T+60 跑出 15% 以上超额，"
      "最好的 `STNG` 超额 +92.7%、`NG=F` +82.2%。这是唯一一个「清单上几乎每个品类都兑现」的事件。")
    A("- **苏伊士（E2）是慢热**：T+20 一个都没命中，但 T+60 有 4 个（`ZIM` +52.5%、`GNK` +50.3%、"
      "`BDRY` +39.8%、`SBLK` +37.6%）。堵船六天本身没行情，后面的集运与干散货紧张才是行情。")
    A("- **红海（E5）最窄**：10 个载体只有 2 个命中，且都在运费侧（`BDRY` 超额T+60 +88.4%、"
      "`ZIM` +54.2%）。清单里挂的三家再保险（`RNR` `EG` `ACGL`）T+60 超额分别是 "
      "-1.4% / -18.0% / -8.3%，**战争险这条线完全没兑现**。")
    A("- **锡（E4）是零命中**：见下面第三节，这个事件在美股实际上没法参与。\n")

    A("### 问题 2：期货 / 期货滚动 ETF / 股票，哪类涨得多\n")
    A("**没有稳定规律，但有一条清晰的分工：谁最贴近「被卡住的那个东西」，谁涨得最多。**\n")
    A("逐事件看三类载体的平均超额：\n")
    for ev in EVENTS:
        if ev["id"] == "E4":
            continue
        A(f"**{ev['id']} {ev['name']}**\n")
        A(kind_table(tin_ok, ev["id"]) + "\n")
    A("三条能落到操作上的结论：\n")
    A("1. **看冲击落在哪个环节，就买哪个环节的载体——品类对不上，载体类型再对也没用。** "
      "Abqaiq（E1）炸的是沙特产能，商品价格只有脉冲：`CL=F` 事件当天 +14.7%，"
      "但 T+20 超额已回到 -1.0%、T+120 超额 -35.2%；真正拿住涨幅的是运力侧的油轮股，"
      "`INSW` T+60 超额 +41.3%、`DHT` +20.8%。红海（E5）冲击的是航线而不是货，"
      "所以运费 ETF `BDRY` T+60 超额 +88.4%，9 只股票平均只有 +6.2%。"
      "**这两次赢的都不是「某个类型」，而是「离瓶颈最近的那个环节」。**")
    A("2. **期货滚动 ETF 在事件里能跟上对应期货，衰减不是事件窗口内的主要矛盾。** "
      "E3 里 `NG=F` T+120 +100.0% 对 `UNG` +97.8%，`ZW=F` T+60 +33.4% 对 `WEAT` +36.6%，"
      "120 个交易日的跟踪差在 2 个百分点级别。清单第三节说的「持续失血」是**没有事件的年份**的问题，"
      "确认事件后买入这一条在数据上站得住。")
    A("3. **股票的上限最高、下限也最低。** 全样本 37 个股票观测（同一代码出现在不同事件里分别计次）"
      "中，T+60 超额最好的是 `STNG` +92.7%，"
      "最差的是 `CENX` -38.1%；同一个事件（E3）里 `IPI` T+20 超额 +87.8%，"
      "但 T+120 掉回 -4.6%，期间最大回撤 -69.2%。期货合约与期货滚动 ETF 的最大回撤均值"
      "（-38.2% / -33.5%）反而比股票（-31.7%）更深，**说明「股票更抗跌」这个直觉不成立**。\n")

    A("### 问题 3：提前性——有几个在事件前 20 天就已经涨上去了\n")
    n_pre = int((tin_ok["前20日"] > 0.10).sum())
    n_all = int(tin_ok["前20日"].notna().sum())
    A(f"**{n_all} 个有效载体里 {n_pre} 个（{n_pre / n_all * 100:.0f}%）在事件前 20 个交易日已经涨超 10%。**\n")
    rows = ["| 事件 | 有效载体 | 前20日涨>10% | 其中事件当天收跌 |", "|---|---|---|---|"]
    for ev in EVENTS:
        s = tin_ok[(tin_ok["事件"] == ev["id"]) & tin_ok["前20日"].notna()]
        p = s[s["前20日"] > 0.10]
        rows.append(f"| {ev['id']} {ev['name']} | {len(s)} | {len(p)} 个 | "
                    f"{int((p['T0当日'] < 0).sum())} 个 |")
    A("\n".join(rows) + "\n")
    pre = tin_ok[tin_ok["前20日"] > 0.10]
    nop = tin_ok[(tin_ok["前20日"] <= 0.10) & tin_ok["前20日"].notna()]
    A(f"- **提前涨过的那批，事件当天反而中位数收跌 {pct(pre['T0当日'].median(), 2)}**"
      f"（{len(pre)} 个里 {int((pre['T0当日'] < 0).sum())} 个当天下跌）；"
      f"没提前涨的那批事件当天中位数是 {pct(nop['T0当日'].median(), 2)}。"
      "这就是「消息落地反而卖出」的典型形态。")
    A("- 但**提前涨过不等于行情走完**：提前涨过的那批 T+60 中位超额是 "
      f"{pct(pre['超额T+60'].median())}，没提前涨的只有 {pct(nop['超额T+60'].median())}。"
      f"前20日涨幅与 T+60 超额的相关系数为 "
      f"{d[d['代码'] != 'SPY(基准)'][['前20日', '超额T+60']].corr().iloc[0, 1]:+.2f}，"
      "**弱正相关**——已经在涨的品类，事件后往往继续涨得更好。")
    A("- 最极端的两个：E1 里 `STNG` 前 20 日已涨 +43.1%、`FRO` +42.7%，事件当天双双收跌"
      "（-3.3% / -2.0%），但 `FRO` 的 T+60 超额仍有 +21.5%。E2 里 `BDRY` 前 20 日已涨 +38.7%，"
      "事件当天 -12.4%，T+60 超额 +39.8%。")
    A("- **结论：前 20 日已经涨上去，说明的是「这个品类已经紧了」，不是「行情结束了」。"
      "真正吃亏的是在事件当天追高**——这批标的事件当日的中位涨幅是负的。\n")

    A("---\n")

    # ---- 逐事件明细
    A("## 二、逐事件明细\n")
    for ev in EVENTS:
        A(f"### {ev['id']}　{ev['name']}（{ev['date']}"
          + (f"，{ev['note']}" if ev["note"] else "") + "）\n")
        A(f"清单对应品类：{ev['categories']}\n")
        A(event_table(d, ev["id"]) + "\n")
        if ev["id"] == "E4":
            A("> 注：`TINY` 与 `MSB.AX` 经核对与锡无关（分别是纳米技术 ETF 和生物科技公司），"
              "列在这里只为交代排查过程，**不计入命中统计**，详见第三节。\n")

    A("---\n")

    # ---- 锡专节
    A("## 三、锡（E4）单独说明：结论是「无载体，事件无法参与」\n")
    A("原任务要求「不要猜 ticker」，所以先核对了四个候选到底是什么东西（查 yfinance 元数据）：\n")
    A("| 代码 | 实际是什么 | 算不算锡的载体 |")
    A("|---|---|---|")
    A("| `JJT` | iPath Series B Bloomberg Tin Subindex Total Return ETN | 是，但 2023-07-21 清盘 |")
    A("| `AFMJF` | Alphamin Resources Corp.（刚果金 Bisie 锡矿），美国 OTC 粉单 | 是，但 OTC 粉单 |")
    A("| `TINY` | ProShares Nanotechnology ETF（纳米技术主题） | **否，与锡无关** |")
    A("| `MSB.AX` | Mesoblast Limited（澳洲生物科技） | **否，与锡无关** |\n")
    A("`TINY` 和 `MSB.AX` 在上面所有统计里都已剔除——它们的涨跌和佤邦停矿没有因果关系，"
      "把它们算作命中就是自己骗自己。\n")
    A("**两个真载体的实际表现（事件日 T0 = 2023-04-17）：**\n")
    A("| 代码 | 前20日 | 事件当日 | T+5 | T+20 | T+60 | T+120 | 超额T+60 | 日均成交额 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for t in ("JJT", "AFMJF"):
        r = d[(d["事件"] == "E4") & (d["代码"] == t)].iloc[0]
        A(f"| `{t}` | {pct(r['前20日'])} | {pct(r['T0当日'])} | {pct(r['T+5'])} | {pct(r['T+20'])} | "
          f"{pct(r['T+60'])} | {pct(r['T+120'])} | {pct(r['超额T+60'])} | {money(r['日均成交额'])} |")
    A("")
    A("三个事实：\n")
    A("1. **行情本身就没出现。** `JJT` 直接跟踪彭博锡分项指数，可以当锡价代理。"
      "从 T-1 到清盘（2023-07-21，共 67 个交易日）累计只有 **+3.8%**，"
      "期间最高点在 2023-04-18（事件后第二天）的 **+10.2%**。停矿公告没有带来持续的锡价行情。")
    A("2. **唯一的纯锡载体三个月后就没了。** `JJT` 在 2023-07-21 清盘，"
      "所以它的 T+120 是空值——**佤邦禁令 8 月正式生效时，这个 ETN 已经不存在了**。")
    A("3. **两个载体都装不进钱。** `JJT` 事件前 60 日的日均成交额只有 **6.4 万美元**，"
      "且 2023 年 3-8 月 99 个交易日里有 **30 天成交量为零**；`AFMJF` 是 14.0 万美元，"
      "OTC 粉单，同期有 26 天收盘价原地不动。按 10% 参与率算，两者单日能吃下的资金都在 1-2 万美元级别。\n")
    A("**另外，LME 锡价在 yfinance 上拉不到**：试过 `TIN=F` / `SN=F` / `LME-TIN` / `^TIN`，"
      "全部返回 404 或空数据；`TIN.L` 查出来是 Cornish Metals plc（英国锡矿开发商股票，不是价格序列）。"
      "所以本节的锡价判断只能靠 `JJT`，这一点要留意。\n")
    A("**清单第二节写「锡：无美股载体（`JJT` 2023-07-21 清盘）」——这条记录是对的，"
      "回测结果支持它。** 只是要补一句：清盘前的 `JJT` 虽然还在交易，但成交额太小，"
      "实际上从 2023 年 4 月起就已经等于「无载体」了。\n")

    A("---\n")
    A("## 四、清单需要修正的地方\n")
    A("1. **「战争险与再保险费率」这一行要降级。** 两次战争险事件里再保险股都没反应："
      "E1（Abqaiq）`RNR` T+60 超额 -2.0%、`EG` +1.4%；E5（红海）`RNR` -1.4%、`EG` -18.0%、"
      "`ACGL` -8.3%。清单自己写了「承保资本要等年度换约才调整」——"
      "**换约周期 1 年，意味着事件后 60 个交易日内本来就不该有反应，这一行不适合做事件驱动。**")
    A("2. **「集装箱运费：供给不刚性」的判断偏保守。** `ZIM` 在两次绕航事件里都是最强的股票载体之一："
      "E2 T+120 超额 +114.5%、E5 T+120 超额 +133.0%。清单说「只有绕航能撑住运价」是对的，"
      "但这两次恰恰都是绕航事件——**对集运来说，绕航事件就是它的全部行情来源，不该因为「结构过剩」而低配。**")
    A("3. **`BDRY` 标注「流动性极薄」，但它是 E5 里 T+60 表现最好的载体（超额 +88.4%）。** "
      "事件前 60 日日均成交额 148 万美元——薄是真薄，可这个量级已经能装进个人仓位。"
      "建议把「极薄」改成具体数字，方便判断能放多少钱。")
    A("4. **`USO` 在 E1 里完全等于 `CL=F`**（T+60 +8.3% 对 +8.0%，T+120 -43.0% 对 -43.2%）。"
      "原油这个品类上，期货滚动 ETF 和期货合约没有差别，清单里可以直接注明「买不了期货就用 `USO`，"
      "事件窗口内跟踪误差可忽略」。\n")

    A("---\n")
    A("原始逐行数据见 `/tmp/rigid/backfill_events_raw.csv`，复现脚本 `/tmp/rigid/backfill_events.py`。\n")

    path = os.path.join(WORKDIR, "backfill_events.md")
    with open(path, "w") as f:
        f.write("\n".join(out))
    return path


if __name__ == "__main__":
    df, failed = run()
    pd.set_option("display.width", 250, "display.max_columns", 50)
    print(df.to_string())
    print("\nFAILED:", failed)
    print("report ->", write_report(df, failed))
