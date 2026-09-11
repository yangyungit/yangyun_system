#!/usr/bin/env python3
"""刷新「供给刚性清单」第七节的紧度读数。

回答的是「现在这个品类的垫子有多厚」。同一个事件打在紧市场和松市场上差一个
数量级：2019-09 沙特 Abqaiq 遇袭砍掉全球 5% 原油供给，四周后油价跌回原位，
因为当时有闲置产能；2026 年油轮这波能持续爆，因为事发时船队利用率已在 90%。

三个指标都是免费日更的：
  分位       —— 当前价在过去 5 年的百分位，高 = 紧
  期限结构   —— 近月比远月贵多少，正值（backwardation）= 现货紧张
  裂解价差   —— 3-2-1，炼能紧张程度

真正的库存和日租金数据（LME 库存、TD3C 日租金、UxC 铀价）都要付费，拿不到。
"""

import re
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

NOTE = Path.home() / "yangyun/Code_Projects/obsidian_notes/99_Human_Zone/供给刚性清单.md"
START, END = "<!-- TIGHTNESS:START -->", "<!-- TIGHTNESS:END -->"

# 分位超过这个数就标紧
TIGHT_Q = 0.85

# 品类 -> 连续合约。对应第二节的品类名
FUTURES = {
    "原油": "CL=F",
    "布伦特": "BZ=F",
    "汽油 RBOB": "RB=F",
    "馏分油（柴油）": "HO=F",
    "美国天然气": "NG=F",
    "铜": "HG=F",
    "铝": "ALI=F",
    "黄金": "GC=F",
    "白银": "SI=F",
    "铂": "PL=F",
    "钯": "PA=F",
    "小麦": "ZW=F",
    "玉米": "ZC=F",
    "大豆": "ZS=F",
    "咖啡": "KC=F",
    "可可": "CC=F",
    "糖": "SB=F",
    "棉花": "CT=F",
}

# 只对逐月都有活跃合约的品种算期限结构。玉米、铜这类只有特定交割月，
# 自动推算月份会落到空合约上，所以不做
TERM_ROOTS = {"原油": ("CL", "NYM"), "美国天然气": ("NG", "NYM")}
MONTH_CODE = "FGHJKMNQUVXZ"


def contract_code(root: str, suffix: str, months_out: int) -> str:
    m = date.today().month - 1 + months_out
    y = date.today().year + m // 12
    return f"{root}{MONTH_CODE[m % 12]}{y % 100:02d}.{suffix}"


def close_series(ticker: str, period: str = "max") -> pd.Series | None:
    for attempt, p in enumerate([period, "5y", "1y"]):
        try:
            h = yf.Ticker(ticker).history(period=p, auto_adjust=False)
            if h.empty:
                continue
            h.index = h.index.tz_localize(None)
            c = h["Close"].dropna()
            return c if len(c) >= 60 else None
        except Exception:
            if attempt == 2:
                return None
    return None


def quantile_of(series: pd.Series, value: float, years: int) -> float | None:
    w = series.tail(252 * years)
    if len(w) < 252:
        return None
    return float((w < value).mean())


def scan_one(item: tuple[str, str]) -> dict:
    name, ticker = item
    c = close_series(ticker)
    if c is None:
        return {"name": name, "ticker": ticker, "ok": False}
    cur = float(c.iloc[-1])
    row = {
        "name": name, "ticker": ticker, "ok": True, "price": cur,
        "q5": quantile_of(c, cur, 5),
        "q10": quantile_of(c, cur, 10),
        "r1m": cur / c.iloc[-22] - 1 if len(c) > 22 else None,
        "r1y": cur / c.iloc[-253] - 1 if len(c) > 253 else None,
        "stale": (pd.Timestamp(date.today()) - c.index[-1]).days,
    }
    # 一个月前的分位，用来看在变紧还是变松
    if len(c) > 22 + 252 * 5:
        past = c.iloc[:-22]
        row["q5_prev"] = quantile_of(past, float(past.iloc[-1]), 5)
    return row


def term_structure() -> list[dict]:
    out = []
    for name, (root, suffix) in TERM_ROOTS.items():
        spot = close_series(FUTURES[name], "1y")
        if spot is None:
            continue
        legs = []
        for months in (3, 9):
            code = contract_code(root, suffix, months)
            far = close_series(code, "6mo")
            if far is not None:
                legs.append((months, code, float(far.iloc[-1])))
        if legs:
            out.append({"name": name, "spot": float(spot.iloc[-1]), "legs": legs})
    return out


def crack_321() -> dict | None:
    cl, rb, ho = (close_series(t) for t in ("CL=F", "RB=F", "HO=F"))
    if any(s is None for s in (cl, rb, ho)):
        return None
    df = pd.concat({"cl": cl, "rb": rb, "ho": ho}, axis=1).dropna()
    # RB/HO 报价是美元每加仑，CL 是美元每桶，1 桶 = 42 加仑
    crack = (2 * df.rb + df.ho) * 42 / 3 - df.cl
    cur = float(crack.iloc[-1])
    return {
        "value": cur,
        "q5": quantile_of(crack, cur, 5),
        "q10": quantile_of(crack, cur, 10),
        "median10": float(crack.tail(2520).median()),
    }


def pct(v, digits=0) -> str:
    return "—" if v is None or v != v else f"{v * 100:+.{digits}f}%"


def qstr(v) -> str:
    return "—" if v is None or v != v else f"{v * 100:.0f}%"


def render(rows: list[dict], terms: list[dict], crack: dict | None) -> str:
    ok = [r for r in rows if r.get("ok")]
    ok.sort(key=lambda r: (r["q5"] is None, -(r["q5"] or 0)))

    out = [START, "", f"> 自动生成于 {date.today()}，由 `system/scripts/tightness_scan.py` 写入。", ""]
    out.append("| 品类 | 最新 | 5 年分位 | 10 年分位 | 一个月前分位 | 近一月 | 近一年 | 判读 |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---|")
    for r in ok:
        q5 = r["q5"]
        if q5 is None:
            verdict = "历史不足"
        elif q5 >= TIGHT_Q:
            verdict = "紧"
        elif q5 <= 0.3:
            verdict = "松"
        else:
            verdict = "中性"
        prev = r.get("q5_prev")
        if q5 is not None and prev is not None:
            verdict += "，在变紧" if q5 - prev > 0.1 else ("，在变松" if prev - q5 > 0.1 else "")
        out.append(
            f"| {r['name']} | {r['price']:.2f} | {qstr(q5)} | {qstr(r['q10'])} | "
            f"{qstr(prev)} | {pct(r['r1m'])} | {pct(r['r1y'])} | {verdict} |"
        )

    if crack:
        out += ["", "**炼能紧张（3-2-1 裂解价差）**", "",
                f"当前 {crack['value']:.1f} 美元/桶，10 年中位 {crack['median10']:.1f}，"
                f"5 年分位 {qstr(crack['q5'])}，10 年分位 {qstr(crack['q10'])}。"]

    if terms:
        out += ["", "**期限结构**（近月溢价为正 = backwardation = 现货紧张）", "",
                "| 品类 | 近月 | 远月 | 远月价 | 近月溢价 |", "|---|---:|---|---:|---:|"]
        for t in terms:
            for months, code, price in t["legs"]:
                out.append(f"| {t['name']} | {t['spot']:.2f} | {months} 个月后 `{code}` | "
                           f"{price:.2f} | {t['spot'] / price - 1:+.1%} |")
        out += ["", "天然气有强季节性（冬季合约天然贵过夏季），它的近月溢价要跟往年同月比才有意义，"
                "不能直接当宽松读。原油没有这个问题。"]

    tight = [r["name"] for r in ok if r["q5"] is not None and r["q5"] >= TIGHT_Q]
    if tight:
        out += ["", f"**当前处在 5 年 {TIGHT_Q:.0%} 分位以上的品类**：{'、'.join(tight)}。"
                "事件真打在这些品类上才容易出非线性行情。"]

    bad = [r["ticker"] for r in rows if not r.get("ok")]
    if bad:
        out += ["", f"拉取失败：{'、'.join(f'`{b}`' for b in bad)}"]

    out += ["", END]
    return "\n".join(out)


def main() -> int:
    if not NOTE.exists():
        print(f"找不到笔记：{NOTE}", file=sys.stderr)
        return 1
    text = NOTE.read_text(encoding="utf-8")
    if START not in text or END not in text:
        print("笔记里缺少 TIGHTNESS 标记，不敢写", file=sys.stderr)
        return 1

    print(f"扫 {len(FUTURES)} 个品类…")
    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(scan_one, FUTURES.items()))
    terms = term_structure()
    crack = crack_321()

    NOTE.write_text(text.split(START)[0] + render(rows, terms, crack) + text.split(END)[1],
                    encoding="utf-8")

    ok = [r for r in rows if r.get("ok")]
    tight = [r for r in ok if r["q5"] is not None and r["q5"] >= TIGHT_Q]
    print(f"写入完成：{len(ok)}/{len(rows)} 个品类有数据，处在 {TIGHT_Q:.0%} 分位以上的 {len(tight)} 个")
    for r in sorted(tight, key=lambda x: -x["q5"]):
        print(f"  紧  {r['name']}  5年分位 {qstr(r['q5'])}  近一年 {pct(r['r1y'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
