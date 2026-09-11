#!/usr/bin/env python3
"""刷新「供给刚性清单」里各载体的流动性与衰减数据。

从笔记第二节反引号里抓 ticker，拉 yfinance，把结果写回 AUTO 标记之间。
载体清盘率很高（iPath 系列 2023-07-21 一天清掉五个 ETN），所以存活状态
必须机器核对，不能手填。
"""

import re
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

NOTE = Path.home() / "yangyun/Code_Projects/obsidian_notes/99_Human_Zone/供给刚性清单.md"
START, END = "<!-- AUTO:START -->", "<!-- AUTO:END -->"

# 日均成交额低于这个数（美元）只能算观察标的：钱装不进去
LIQUID_FLOOR = 5e6
STALE_DAYS = 20

# 期货滚动型会持续衰减，实物型不会——这个区分决定能不能提前埋伏
ROLLING = {"BWET", "BDRY", "UNG", "BOIL", "CPER", "WEAT", "CORN", "SOYB", "DBA",
           "DBC", "PDBC", "GSG", "BCI", "FTGC", "COMT", "UGA", "KRBN", "GRN"}
PHYSICAL = {"GLD", "SLV", "PPLT", "PALL", "SPPP", "SRUUF"}


def extract_tickers(text: str) -> list[str]:
    body = text.split(START)[0]
    found = {t for t in re.findall(r"`([^`]+)`", body)
             if re.fullmatch(r"[A-Z]{1,6}(?:=F)?", t)}
    return sorted(found)


def kind(t: str) -> str:
    if t in ROLLING:
        return "期货滚动"
    if t in PHYSICAL:
        return "实物"
    if t.endswith("=F"):
        return "期货合约"
    return "股票/股票ETF"


def fetch(t: str) -> dict:
    # Yahoo 偶发对 period=max 返回空（ACGL 上市日被识别成 1927 年就会这样），
    # 回退到较短窗口再试一次，避免把活着的标的误标成清盘
    for attempt, period in enumerate(["max", "10y", "5y"]):
        try:
            h = yf.Ticker(t).history(period=period, auto_adjust=False)
            if h.empty or len(h) < 20:
                if attempt < 2:
                    time.sleep(1)
                    continue
                return {"ticker": t, "alive": False, "note": "三次都没拉到数据，需手工核对"}
            h.index = h.index.tz_localize(None)
            c = h["Adj Close"].dropna()
            last = c.index[-1]
            stale = (pd.Timestamp(date.today()) - last).days
            dv = float((h["Volume"] * h["Close"]).tail(60).mean())

            def ann(days):
                """days 为交易日数，按每年 252 个交易日折算年化。"""
                if len(c) <= days:
                    return None
                return (c.iloc[-1] / c.iloc[-days - 1]) ** (252 / days) - 1

            return {
                "ticker": t,
                "alive": stale <= STALE_DAYS,
                "note": "" if stale <= STALE_DAYS else f"停更于 {last.date()}",
                "price": float(c.iloc[-1]),
                "dollar_vol": dv,
                "r1y": (c.iloc[-1] / c.iloc[-253] - 1) if len(c) > 253 else None,
                "cagr3": ann(756),
                "cagr5": ann(1260),
                "mdd": float((c / c.cummax() - 1).min()),
                "listed": str(c.index[0].date()),
            }
        except Exception as exc:
            if attempt == 2:
                return {"ticker": t, "alive": False, "note": f"抓取失败 {type(exc).__name__}"}
            time.sleep(2)
    return {"ticker": t, "alive": False, "note": "三次都没拉到数据，需手工核对"}


def pct(v) -> str:
    return "—" if v is None or v != v else f"{v * 100:+.1f}%"


def render(rows: list[dict]) -> str:
    alive = [r for r in rows if r.get("alive")]
    dead = [r for r in rows if not r.get("alive")]
    alive.sort(key=lambda r: r["dollar_vol"], reverse=True)

    out = [START, "", f"> 自动生成于 {date.today()}，由 `system/scripts/refresh_rigid_list.py` 写入。"
           f"日均成交额 = 近 60 个交易日均值；低于 500 万美元的只算观察标的。", ""]
    out.append("| 载体 | 类型 | 价格 | 日均成交额 | 可执行 | 近一年 | 3 年年化 | 5 年年化 | 最大回撤 |")
    out.append("|---|---|---|---:|---|---:|---:|---:|---:|")
    for r in alive:
        dv = r["dollar_vol"]
        dv_s = f"{dv / 1e8:.2f} 亿" if dv >= 1e8 else f"{dv / 1e4:.0f} 万"
        out.append(
            f"| `{r['ticker']}` | {kind(r['ticker'])} | {r['price']:.2f} | {dv_s} | "
            f"{'可' if dv >= LIQUID_FLOOR else '观察'} | {pct(r['r1y'])} | "
            f"{pct(r['cagr3'])} | {pct(r['cagr5'])} | {pct(r['mdd'])} |"
        )

    if dead:
        out += ["", "**已清盘或抓不到数据**（别照着下单）", ""]
        out.append("| 载体 | 状态 |")
        out.append("|---|---|")
        for r in dead:
            out.append(f"| `{r['ticker']}` | {r['note']} |")

    rolling_neg = [r for r in alive if kind(r["ticker"]) == "期货滚动"
                   and r.get("cagr5") is not None and r["cagr5"] == r["cagr5"] and r["cagr5"] < 0]
    if rolling_neg:
        names = "、".join(f"`{r['ticker']}` {pct(r['cagr5'])}" for r in rolling_neg)
        out += ["", f"**五年年化为负的期货滚动载体**：{names}。这些只能事件确认后买，不能拿着等事件。"]

    out += ["", END]
    return "\n".join(out)


def main() -> int:
    if not NOTE.exists():
        print(f"找不到笔记：{NOTE}", file=sys.stderr)
        return 1
    text = NOTE.read_text(encoding="utf-8")
    if START not in text or END not in text:
        print("笔记里缺少 AUTO 标记，不敢写", file=sys.stderr)
        return 1

    tickers = extract_tickers(text)
    if not tickers:
        print("没抓到任何 ticker", file=sys.stderr)
        return 1
    print(f"抓到 {len(tickers)} 个载体，开始拉数据…")

    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(fetch, tickers))

    head = text.split(START)[0]
    tail = text.split(END)[1]
    NOTE.write_text(head + render(rows) + tail, encoding="utf-8")

    alive = sum(1 for r in rows if r.get("alive"))
    ok = sum(1 for r in rows if r.get("alive") and r["dollar_vol"] >= LIQUID_FLOOR)
    print(f"写入完成：存活 {alive}/{len(rows)}，其中流动性够用的 {ok} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
