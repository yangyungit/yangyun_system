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
A_START, A_END = "<!-- ALERT:START -->", "<!-- ALERT:END -->"

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

MONTH_CODE = "FGHJKMNQUVXZ"

# 期限结构：品类 -> (合约代号, 交易所后缀, 该品种有活跃合约的交割月代码)
# 不同品种交割月不同，玉米只有 3/5/7/9/12 月，硬推月份会落到空合约上
TERM = {
    "原油": ("CL", "NYM", MONTH_CODE),
    "布伦特": ("BZ", "NYM", MONTH_CODE),
    "馏分油（柴油）": ("HO", "NYM", MONTH_CODE),
    "汽油 RBOB": ("RB", "NYM", MONTH_CODE),
    "美国天然气": ("NG", "NYM", MONTH_CODE),
    "铜": ("HG", "CMX", "HKNUZ"),
    "白银": ("SI", "CMX", "HKNUZ"),
    "黄金": ("GC", "CMX", "GJMQVZ"),
    "铂": ("PL", "NYM", "FJNV"),
    "钯": ("PA", "NYM", "HMUZ"),
    "铝": ("ALI", "CMX", "HMUZ"),
    "玉米": ("ZC", "CBT", "HKNUZ"),
    "小麦": ("ZW", "CBT", "HKNUZ"),
    "大豆": ("ZS", "CBT", "FHKNQUX"),
    "咖啡": ("KC", "NYB", "HKNUZ"),
    "可可": ("CC", "NYB", "HKNUZ"),
    "糖": ("SB", "NYB", "HKNV"),
    "棉花": ("CT", "NYB", "HKNVZ"),
}

# 远月取 8-12 个月之后。太近会撞上近月合约本身，太远流动性差
FAR_MIN, FAR_MAX = 8, 12


def far_contract(root: str, suffix: str, months: str) -> str | None:
    """找 8-12 个月后第一个该品种有活跃合约的交割月。"""
    today = date.today()
    for out in range(FAR_MIN, FAR_MAX + 1):
        m = today.month - 1 + out
        code = MONTH_CODE[m % 12]
        if code in months:
            return f"{root}{code}{(today.year + m // 12) % 100:02d}.{suffix}"
    return None


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
    row["term"] = term_one(name)
    return row


def term_one(name: str) -> dict | None:
    """算一个品类的近月对远月溢价。正值 = backwardation = 现货紧张。"""
    if name not in TERM:
        return None
    root, suffix, months = TERM[name]
    code = far_contract(root, suffix, months)
    if code is None:
        return None
    near = close_series(FUTURES[name], "1y")
    far = close_series(code, "6mo")
    if near is None or far is None:
        return None
    n, f = float(near.iloc[-1]), float(far.iloc[-1])
    # 价格完全相同说明 Yahoo 把连续合约映射到了同一张合约，不是真的平价
    if f <= 0 or abs(n - f) < 1e-9:
        return None
    out = {"code": code, "near": n, "far": f, "prem": n / f - 1, "prem_prev": None}
    # 一个月前的溢价：能看出在往 backwardation 走还是往 contango 走
    both = pd.concat({"n": near, "f": far}, axis=1).dropna()
    if len(both) > 23:
        p = both.iloc[-23]
        if p.f > 0:
            out["prem_prev"] = p.n / p.f - 1
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


def verdict_of(q5: float | None, prem: float | None) -> str:
    """判读。价格分位测「贵不贵」，期限结构测「缺不缺」，两者经常分离。"""
    if prem is None:
        if q5 is None:
            return "数据不足"
        return "价格高位（无期限结构）" if q5 >= TIGHT_Q else "中性（无期限结构）"
    if prem > 0.05:
        return "现货紧张" + ("，且价格在高位" if q5 is not None and q5 >= TIGHT_Q else "")
    if prem > 0:
        return "轻微倒挂"
    if q5 is not None and q5 >= TIGHT_Q:
        return "贵，但不缺"
    if q5 is not None and q5 <= 0.3:
        return "松"
    return "中性"


def build_alerts(rows: list[dict]) -> list[str]:
    """报警建在紧度变化上，不是建在价格涨跌上。价格异动是结果，紧度异动才是提前量。"""
    out = []
    for r in rows:
        if not r.get("ok"):
            continue
        name, q5, prev = r["name"], r["q5"], r.get("q5_prev")
        t = r.get("term")
        if t and t["prem_prev"] is not None:
            p, pp = t["prem"], t["prem_prev"]
            if p > 0 and pp <= 0:
                out.append(f"**{name} 期限结构翻转成倒挂**（一个月前 {pp:+.1%} → 现在 {p:+.1%}）"
                           "，市场开始为「立刻拿到货」付溢价，这是现货转紧最直接的信号")
            elif p > 0.05 and p - pp > 0.05:
                out.append(f"**{name} 倒挂加深**（{pp:+.1%} → {p:+.1%}），现货比一个月前更抢手")
            elif pp > 0.05 and p < 0:
                out.append(f"{name} 倒挂消失（{pp:+.1%} → {p:+.1%}），现货紧张在缓解")
        if q5 is not None and prev is not None:
            if q5 >= TIGHT_Q > prev:
                out.append(f"{name} 价格进入 5 年 {TIGHT_Q:.0%} 分位以上（{qstr(prev)} → {qstr(q5)}）")
            elif q5 - prev > 0.2:
                out.append(f"{name} 价格分位一个月跳升 {(q5 - prev) * 100:.0f} 个百分点"
                           f"（{qstr(prev)} → {qstr(q5)}）")
    return out


def render(rows: list[dict], crack: dict | None) -> str:
    ok = [r for r in rows if r.get("ok")]
    # 按真实紧度排序：有倒挂的排前面，其次看价格分位
    ok.sort(key=lambda r: (-(r["term"]["prem"] if r.get("term") else -9), -(r["q5"] or 0)))

    out = [START, "", f"> 自动生成于 {date.today()}，由 `system/scripts/tightness_scan.py` 写入。",
           "> 判读以期限结构为主、价格分位为辅：分位高只说明贵，倒挂才说明缺。", ""]
    out.append("| 品类 | 最新 | 近月溢价 | 一月前溢价 | 5 年分位 | 一月前分位 | 近一月 | 近一年 | 判读 |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for r in ok:
        t = r.get("term")
        prem = t["prem"] if t else None
        out.append(
            f"| {r['name']} | {r['price']:.2f} | {pct(prem, 1)} | "
            f"{pct(t['prem_prev'], 1) if t else '—'} | {qstr(r['q5'])} | {qstr(r.get('q5_prev'))} | "
            f"{pct(r['r1m'])} | {pct(r['r1y'])} | {verdict_of(r['q5'], prem)} |"
        )

    out += ["", "天然气有强季节性（冬季合约天然贵过夏季），它的近月溢价要跟往年同月比才有意义，"
            "不能直接当宽松读。原油和金属没有这个问题。"]

    if crack:
        out += ["", "**炼能紧张（3-2-1 裂解价差）**", "",
                f"当前 {crack['value']:.1f} 美元/桶，10 年中位 {crack['median10']:.1f}，"
                f"5 年分位 {qstr(crack['q5'])}，10 年分位 {qstr(crack['q10'])}。",
                "", "注意裂解价差和柴油价格是两件事：炼油厂赚的是价差，原油涨得比油品快的时候，"
                "柴油越贵炼厂反而越不赚钱。要区分「炼能端缺口」（炼厂着火、出口禁令，价差扩大）"
                "和「原油端缺口」（地缘冲突，价差被压缩）。"]

    tight = [r["name"] for r in ok if r.get("term") and r["term"]["prem"] > 0]
    if tight:
        out += ["", f"**当前处在倒挂（现货紧张）的品类**：{'、'.join(tight)}。"
                "事件打在这些品类上才容易出非线性行情。"]
    else:
        out += ["", "**当前没有任何品类处在倒挂状态**，全部 contango。"]

    bad = [r["ticker"] for r in rows if not r.get("ok")]
    if bad:
        out += ["", f"拉取失败：{'、'.join(f'`{b}`' for b in bad)}"]

    out += ["", END]
    return "\n".join(out)


def render_alerts(alerts: list[str]) -> str:
    out = [A_START, ""]
    if alerts:
        out.append(f"> {date.today()} 扫出 {len(alerts)} 条紧度变化：")
        out.append("")
        out += [f"- {a}" for a in alerts]
    else:
        out.append(f"> {date.today()}：没有紧度异动。")
    out += ["", A_END]
    return "\n".join(out)


def replace_block(text: str, start: str, end: str, body: str) -> str:
    return text.split(start)[0] + body + text.split(end)[1]


def main() -> int:
    if not NOTE.exists():
        print(f"找不到笔记：{NOTE}", file=sys.stderr)
        return 1
    text = NOTE.read_text(encoding="utf-8")
    for mark in (START, END, A_START, A_END):
        if mark not in text:
            print(f"笔记里缺少标记 {mark}，不敢写", file=sys.stderr)
            return 1

    print(f"扫 {len(FUTURES)} 个品类…")
    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(scan_one, FUTURES.items()))
    crack = crack_321()
    alerts = build_alerts(rows)

    text = replace_block(text, START, END, render(rows, crack))
    text = replace_block(text, A_START, A_END, render_alerts(alerts))
    NOTE.write_text(text, encoding="utf-8")

    ok = [r for r in rows if r.get("ok")]
    back = [r for r in ok if r.get("term") and r["term"]["prem"] > 0]
    print(f"写入完成：{len(ok)}/{len(rows)} 个品类有数据，处在倒挂（现货紧张）的 {len(back)} 个")
    for r in sorted(back, key=lambda x: -x["term"]["prem"]):
        print(f"  倒挂 {r['name']}  近月溢价 {r['term']['prem']:+.1%}  5年分位 {qstr(r['q5'])}")
    print(f"报警 {len(alerts)} 条：")
    for a in alerts:
        print("  -", a.replace("**", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
