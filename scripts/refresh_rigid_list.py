#!/usr/bin/env python3
"""刷新「供给刚性清单」里各载体的流动性与衰减数据。

载体清单来自后端 `valuation-radar/rigid_list.py`（品类 → 载体的真相源），这里
只负责拉 yfinance 核对存活状态和流动性，把结果写回 AUTO 标记之间。载体清盘率
很高（iPath 系列 2023-07-21 一天清掉五个 ETN），所以存活状态必须机器核对。

非美股载体的价格是本币。韩股不换算会算出「1 亿韩元」然后去跟 500 万**美元**
的门槛比，结论全错，所以价格序列先换成美元再算收益率、回撤和成交额。
"""

import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path.home() / "yangyun/Code_Projects/valuation-radar"))
from rigid_list import all_tickers, carrier_kind, market_of  # noqa: E402

warnings.filterwarnings("ignore")

NOTE = Path.home() / "yangyun/Code_Projects/obsidian_notes/99_Human_Zone/供给刚性清单.md"
START, END = "<!-- AUTO:START -->", "<!-- AUTO:END -->"

# 日均成交额低于这个数（美元）只能算观察标的：钱装不进去
LIQUID_FLOOR = 5e6
STALE_DAYS = 20

# 伦敦和约堡报的是「分」不是「元」，yfinance 的 currency 字段给 GBp / ZAc。
# 漏掉这一步 GLEN.L 的成交额会虚高 100 倍
_CENTS = {"GBP": "GBP", "GBX": "GBP", "ZAC": "ZAR"}
# ICE 软商品期货（CT=F / KC=F / SB=F）的 currency 是 USX（美分每磅）。
# 这是行业惯用报价单位，笔记一直按美分显示，不去换算也不去拉汇率
_PASSTHROUGH = {"USD", "USX", ""}

# 期货的 Volume 是合约张数、Close 是单位报价，直接相乘不是成交额，差几万倍：
# RB=F 汽油这么算出来日均 15 万美元，真实是 61 亿，会被误判成「装不进钱」然后
# 去买流动性差得多的股票载体。一张合约的名义价值 = 报价 × 合约规格，
# 美分报价的再 × 0.01。规格是交易所固定的，新增期货载体漏填这里 main 会拦下
_FUTURES_MULT = {
    "CL=F": 1000,            # NYMEX 原油，1000 桶，美元/桶
    "BZ=F": 1000,            # ICE 布伦特，1000 桶，美元/桶
    "RB=F": 42000,           # NYMEX 汽油，42000 加仑，美元/加仑
    "CC=F": 10,              # ICE 可可，10 公吨，美元/吨
    "ZM=F": 100,             # CBOT 豆粕，100 短吨，美元/短吨
    "SB=F": 112000 * 0.01,   # ICE 11 号糖，112000 磅，美分/磅
    "CT=F": 50000 * 0.01,    # ICE 棉花，50000 磅，美分/磅
    "KC=F": 37500 * 0.01,    # ICE 咖啡，37500 磅，美分/磅
    "ZL=F": 60000 * 0.01,    # CBOT 豆油，60000 磅，美分/磅
}
_FX_CACHE: dict[str, pd.Series | None] = {}
_FX_LOCK = threading.Lock()


def _fx(cur: str) -> tuple[pd.Series | None, float]:
    """本币 -> 美元的日线汇率序列，外带「分转元」的缩放系数。

    返回 None 表示本来就是美元。收益率和回撤必须用换算后的序列算，
    不能用本币收益率乘一个点汇率——那等于假设汇率一年没动过。
    """
    raw = (cur or "USD").strip()
    scale = 0.01 if raw in ("GBp", "GBX", "ZAc") else 1.0
    code = _CENTS.get(raw.upper(), raw.upper())
    if code in _PASSTHROUGH:
        return None, scale
    with _FX_LOCK:
        if code not in _FX_CACHE:
            try:
                h = yf.Ticker(f"{code}USD=X").history(period="max", auto_adjust=False)
                h.index = h.index.tz_localize(None)
                s = h["Close"].dropna()
                _FX_CACHE[code] = s if len(s) > 250 else None
            except Exception:
                _FX_CACHE[code] = None
        return _FX_CACHE[code], scale


def _to_usd(s: pd.Series, rate: pd.Series | None, scale: float) -> pd.Series:
    if rate is None:
        return s * scale
    return (s * rate.reindex(s.index).ffill() * scale).dropna()


def _currency(tk) -> str:
    try:
        return tk.fast_info.get("currency") or "USD"
    except Exception:
        return "USD"


def fetch(t: str) -> dict:
    # Yahoo 偶发对 period=max 返回空（ACGL 上市日被识别成 1927 年就会这样），
    # 回退到较短窗口再试一次，避免把活着的标的误标成清盘
    for attempt, period in enumerate(["max", "10y", "5y"]):
        try:
            tk = yf.Ticker(t)
            h = tk.history(period=period, auto_adjust=False)
            if h.empty or len(h) < 20:
                if attempt < 2:
                    time.sleep(1)
                    continue
                return {"ticker": t, "alive": False, "note": "三次都没拉到数据，需手工核对"}
            h.index = h.index.tz_localize(None)
            cur = _currency(tk)
            rate, scale = _fx(cur)
            c = _to_usd(h["Adj Close"].dropna(), rate, scale)
            if len(c) < 20:
                return {"ticker": t, "alive": False,
                        "note": f"{cur} 汇率拉不到，成交额无法换成美元，需手工核对"}
            last = c.index[-1]
            stale = (pd.Timestamp(date.today()) - last).days
            dv = float(_to_usd(h["Volume"] * h["Close"], rate, scale).tail(60).mean())
            dv *= _FUTURES_MULT.get(t, 1)

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
                "currency": cur,
                "market": market_of(t),
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
           f"价格、日均成交额、收益率和回撤全部已换算成美元（按逐日汇率，不是点汇率）。"
           f"日均成交额 = 近 60 个交易日均值，期货合约按「张数 × 报价 × 合约规格」"
           f"算名义成交额；低于 500 万美元的只算观察标的。", ""]
    out.append("| 载体 | 市场 | 类型 | 价格 | 日均成交额 | 可执行 | 近一年 | 3 年年化 | 5 年年化 | 最大回撤 |")
    out.append("|---|---|---|---:|---:|---|---:|---:|---:|---:|")
    for r in alive:
        dv = r["dollar_vol"]
        dv_s = f"{dv / 1e8:.2f} 亿" if dv >= 1e8 else f"{dv / 1e4:.0f} 万"
        out.append(
            f"| `{r['ticker']}` | {r['market']} | {carrier_kind(r['ticker'])} | "
            f"{r['price']:.2f} | {dv_s} | "
            f"{'可' if dv >= LIQUID_FLOOR else '观察'} | {pct(r['r1y'])} | "
            f"{pct(r['cagr3'])} | {pct(r['cagr5'])} | {pct(r['mdd'])} |"
        )

    if dead:
        out += ["", "**已清盘或抓不到数据**（别照着下单）", ""]
        out.append("| 载体 | 状态 |")
        out.append("|---|---|")
        for r in dead:
            out.append(f"| `{r['ticker']}` | {r['note']} |")

    rolling_neg = [r for r in alive if carrier_kind(r["ticker"]) == "期货滚动"
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

    tickers = all_tickers()
    if not tickers:
        print("rigid_list.RIGID 里一个载体都没有", file=sys.stderr)
        return 1
    no_mult = [t for t in tickers if t.endswith("=F") and t not in _FUTURES_MULT]
    if no_mult:
        print(f"这些期货合约还没填合约乘数，成交额会算错几万倍：{no_mult}", file=sys.stderr)
        return 1
    print(f"rigid_list 里 {len(tickers)} 个载体，开始拉数据…")

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
