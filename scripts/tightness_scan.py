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

import json
import re
import sqlite3
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

sys.path.insert(0, str(Path.home() / "yangyun/Code_Projects/valuation-radar"))
from rigid_list import RIGID, news_terms_of, proxy_categories  # noqa: E402

warnings.filterwarnings("ignore")

NOTE = Path.home() / "yangyun/Code_Projects/obsidian_notes/99_Human_Zone/供给刚性清单.md"

# 量化系统后端，存每日快照供前端画时间序列
API = "http://127.0.0.1:8000"
START, END = "<!-- TIGHTNESS:START -->", "<!-- TIGHTNESS:END -->"
A_START, A_END = "<!-- ALERT:START -->", "<!-- ALERT:END -->"

# 已推送报警的记录，用于跨天去重
STATE = Path(__file__).with_name("tightness_alert_state.json")
COOLDOWN_DAYS = 30

# 分位超过这个数就标紧
TIGHT_Q = 0.85

# 代理读数的异动线。BWET 那波 2025-08 起月线 +14%/+17%/+22%/+28%，20% 会在第三个月
# 触发，比主理人 2026-09 才注意到早约 11 个月——这就是这个数的依据
PROXY_JUMP = 0.20

PROXY_WORDING = ("代理读数——这是载体自己的涨幅和分位，不是现货紧度。"
                 "运费的真紧度要去 Baltic Exchange 查 TD3C。")

# 报警只说「紧度翻转了」，不说为什么翻。新闻库补上这一句
NEWS_DB = Path.home() / "yangyun/Code_Projects/valuation-radar/data/narrative.db"
NEWS_LOOKBACK_DAYS = 7
NEWS_PER_ALERT = 3
NEWS_HEADLINE_MAX = 120
NO_NEWS = "没找到对得上的新闻"

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

# 近月和远月共同有报价的最后一天，允许比近月自己的最后一天旧几天
MAX_TERM_LAG = 5

# 近月最多往后找几个交割月；`XX=F` 和显式近月合约允许差多少
NEAR_MAX, NEAR_TOL = 3, 0.015


def far_contract(root: str, suffix: str, months: str, on: date | None = None) -> str | None:
    """找 8-12 个月后第一个该品种有活跃合约的交割月。on 用于回填历史某天。"""
    on = on or date.today()
    for out in range(FAR_MIN, FAR_MAX + 1):
        m = on.month - 1 + out
        code = MONTH_CODE[m % 12]
        if code in months:
            return f"{root}{code}{(on.year + m // 12) % 100:02d}.{suffix}"
    return None


@lru_cache(maxsize=None)
def near_contract(root: str, suffix: str, months: str) -> tuple[str, pd.Series] | None:
    """显式拼出当前近月合约，不用 Yahoo 的 `XX=F` 连续报价。

    连续报价会临时滚到下一张合约：2026-09-11 糖滚到 SBH27（报 19.23，当天真实
    近月 18.15，19.23 在前后两周的近月里根本没成交过）、同日咖啡滚到 KCZ26
    （报 289.00，真实 313.65）。糖是正向市场滚过去变贵、咖啡深度倒挂滚过去变便宜，
    于是一个被算成假倒挂、一个把 +14% 压成 +4.6%。这里按交割月往后找第一张还在
    交易的，合约到期自然往后走。
    """
    today = date.today()
    for out in range(NEAR_MAX + 1):
        m = today.month - 1 + out
        code = MONTH_CODE[m % 12]
        if code not in months:
            continue
        t = f"{root}{code}{(today.year + m // 12) % 100:02d}.{suffix}"
        c = close_series(t, "1y")
        if c is not None and (pd.Timestamp(today) - c.index[-1]).days <= MAX_TERM_LAG:
            return t, c
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
    # `XX=F` 滚月时价格、分位、涨跌幅整行都是错的，不只是期限结构。对不上就整行
    # 不出，不硬填——2026-09-11 糖那条假报警就是硬填出来的
    if name in TERM:
        nc = near_contract(*TERM[name])
        if nc is not None and abs(cur / float(nc[1].iloc[-1]) - 1) > NEAR_TOL:
            print(f"  {name}：{ticker} 报 {cur:.2f}，显式近月 {nc[0]} 是 "
                  f"{float(nc[1].iloc[-1]):.2f}，连续合约滚月了，本日跳过")
            return {"name": name, "ticker": ticker, "ok": False}
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
    row["source"] = "futures"
    return row


def scan_proxy(item: tuple[str, str]) -> dict:
    """没有活跃美股期货的品类，用主载体自身的价格算一份代理读数。

    期限结构一律留空——载体股价算不出现货对远月的溢价，硬填就是拿别的东西充数。
    """
    name, ticker = item
    c = close_series(ticker)
    if c is None:
        return {"name": name, "ticker": ticker, "ok": False, "source": "proxy"}
    cur = float(c.iloc[-1])
    return {
        "name": name, "ticker": ticker, "ok": True, "price": cur, "source": "proxy",
        "q5": quantile_of(c, cur, 5),
        "q10": quantile_of(c, cur, 10),
        "r1m": cur / c.iloc[-22] - 1 if len(c) > 22 else None,
        "r1y": cur / c.iloc[-253] - 1 if len(c) > 253 else None,
        "stale": (pd.Timestamp(date.today()) - c.index[-1]).days,
        "term": None,
    }


def term_one(name: str) -> dict | None:
    """算一个品类的近月对远月溢价。正值 = backwardation = 现货紧张。"""
    if name not in TERM:
        return None
    root, suffix, months = TERM[name]
    code = far_contract(root, suffix, months)
    if code is None:
        return None
    nc = near_contract(root, suffix, months)
    far = close_series(code, "6mo")
    if nc is None or far is None:
        return None
    near = nc[1]
    # 两条腿必须取同一天的收盘。Yahoo 对远月合约经常停更几天，各自取 iloc[-1]
    # 会拿两个不同日期的价格相比，算出根本不存在的倒挂（2026-09-11 糖被误报
    # 「翻转倒挂 +3.0%」、同日咖啡被低估成 +4.6%，实际一直在 -2% 和 +14% 附近）
    both = pd.concat({"n": near, "f": far}, axis=1).dropna()
    if both.empty or (near.index[-1] - both.index[-1]).days > MAX_TERM_LAG:
        return None
    n, f = float(both.n.iloc[-1]), float(both.f.iloc[-1])
    # 价格完全相同说明 Yahoo 把连续合约映射到了同一张合约，不是真的平价
    if f <= 0 or abs(n - f) < 1e-9:
        return None
    out = {"code": code, "near": n, "far": f, "prem": n / f - 1, "prem_prev": None}
    # 一个月前的溢价：能看出在往 backwardation 走还是往 contango 走。这一段仍然用
    # `XX=F`，因为一个月前的近月是另一张合约，拿今天这张回溯会比错对象；滚月只污染
    # 最新报价，历史那几天 Yahoo 事后会改回来
    hist = close_series(FUTURES[name], "1y")
    if hist is not None:
        prev = pd.concat({"n": hist, "f": far}, axis=1).dropna()
        if len(prev) > 23 and prev.f.iloc[-23] > 0:
            out["prem_prev"] = prev.n.iloc[-23] / prev.f.iloc[-23] - 1
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


def snapshot_row(r: dict) -> dict:
    """一行扫描结果 → 后端 tightness_daily 的一行。"""
    t = r.get("term")
    return {
        "category": r["name"], "ticker": r["ticker"], "price": r["price"],
        "prem": t["prem"] if t else None,
        "prem_prev": t["prem_prev"] if t else None,
        "far_code": t["code"] if t else None,
        "q5": r["q5"], "q5_prev": r.get("q5_prev"), "q10": r["q10"],
        "r1m": r["r1m"], "r1y": r["r1y"],
        "verdict": PROXY_WORDING if r.get("source") == "proxy"
                   else verdict_of(r["q5"], t["prem"] if t else None),
        "source": r.get("source", "futures"),
    }


def build_alerts(rows: list[dict]) -> list[dict]:
    """报警建在紧度变化上，不是建在价格涨跌上。价格异动是结果，紧度异动才是提前量。

    每条带一个 key（品类 + 报警类型，不含具体数字），用于跨天去重：翻转类信号的
    判断基准是一个月前，翻转后一个月内每天都会命中，不去重会天天推同一条。
    """
    out = []

    def add(name: str, kind: str, text: str):
        out.append({"key": f"{name}|{kind}", "text": text})

    for r in rows:
        if not r.get("ok"):
            continue
        # 代理行只报涨幅异动：载体自己的 5 年分位跟现货紧度没关系，BWET 涨了 47 倍之后
        # 分位恒等于 100%，拿它当紧度会天天误报
        if r.get("source") == "proxy":
            if r.get("r1m") is not None and r["r1m"] > PROXY_JUMP:
                ind = RIGID.get(r["name"], {}).get("indicator", "行业现货价")
                add(r["name"], "代理载体异动",
                    f"{r['name']} 主载体 {r['ticker']} 近一月 {r['r1m']:+.0%}，"
                    f"去查 {ind} 确认是不是真紧")
            continue
        name, q5, prev = r["name"], r["q5"], r.get("q5_prev")
        t = r.get("term")
        if t and t["prem_prev"] is not None:
            p, pp = t["prem"], t["prem_prev"]
            if p > 0 and pp <= 0:
                add(name, "翻转倒挂",
                    f"**{name} 期限结构翻转成倒挂**（一个月前 {pp:+.1%} → 现在 {p:+.1%}）"
                    "，市场开始为「立刻拿到货」付溢价，这是现货转紧最直接的信号")
            elif p > 0.05 and p - pp > 0.05:
                add(name, "倒挂加深",
                    f"**{name} 倒挂加深**（{pp:+.1%} → {p:+.1%}），现货比一个月前更抢手")
            elif pp > 0.05 and p < 0:
                add(name, "倒挂消失",
                    f"{name} 倒挂消失（{pp:+.1%} → {p:+.1%}），现货紧张在缓解")
        if q5 is not None and prev is not None:
            if q5 >= TIGHT_Q > prev:
                add(name, "进入紧张区",
                    f"{name} 价格进入 5 年 {TIGHT_Q:.0%} 分位以上（{qstr(prev)} → {qstr(q5)}）")
            elif q5 - prev > 0.2:
                add(name, "分位跳升",
                    f"{name} 价格分位一个月跳升 {(q5 - prev) * 100:.0f} 个百分点"
                    f"（{qstr(prev)} → {qstr(q5)}）")
    return out


def render(rows: list[dict], crack: dict | None) -> str:
    ok = [r for r in rows if r.get("ok") and r.get("source") != "proxy"]
    proxies = [r for r in rows if r.get("ok") and r.get("source") == "proxy"]
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

    if proxies:
        proxies.sort(key=lambda r: -(r["r1m"] or -9))
        out += ["", "**没有期货读数的品类（代理读数）**", "", f"{PROXY_WORDING}", "",
                "| 品类 | 主载体 | 最新 | 5 年分位 | 近一月 | 近一年 |",
                "|---|---|---:|---:|---:|---:|"]
        for r in proxies:
            out.append(f"| {r['name']} | `{r['ticker']}` | {r['price']:.2f} | "
                       f"{qstr(r['q5'])} | {pct(r['r1m'])} | {pct(r['r1y'])} |")

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


def render_alerts(alerts: list[dict]) -> str:
    out = [A_START, ""]
    if alerts:
        out.append(f"> {date.today()} 扫出 {len(alerts)} 条紧度变化：")
        out.append("")
        out += [f"- {a['text']}" for a in alerts]
    else:
        out.append(f"> {date.today()}：没有紧度异动。")
    out += ["", A_END]
    return "\n".join(out)


def read_env(path: Path, key: str) -> str | None:
    """launchd 不加载 .env，脚本自己读。"""
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip() or None
    return None


def load_webhook() -> str | None:
    return read_env(Path.home() / "yangyun/Code_Projects/.env", "DISCORD_WEBHOOK_TIGHTNESS")


def push_backend(rows: list[dict], alerts: list[dict]) -> str:
    """把当天快照推给量化系统后端存档，供前端画时间序列。

    后端不在线只打日志，不能让写 md 失败——md 那份是主理人的存档，优先级更高。
    """
    token = read_env(Path.home() / "yangyun/Code_Projects/valuation-radar/.env",
                     "RESONANCE_INTERNAL_TOKEN")
    if not token:
        return "valuation-radar/.env 里没有 RESONANCE_INTERNAL_TOKEN，跳过上报"
    payload = {
        "snap_date": date.today().isoformat(),
        "rows": [snapshot_row(r) for r in rows if r.get("ok")],
        "alerts": [{"category": a["key"].split("|")[0], "kind": a["key"].split("|")[1],
                    "text": a["text"].replace("**", "")} for a in alerts],
    }
    try:
        r = requests.post(f"{API}/api/v1/tightness/ingest", json=payload,
                          headers={"X-Internal-Token": token}, timeout=30)
        if r.status_code == 200:
            d = r.json()
            return f"已上报后端：{d.get('rows')} 个品类、{d.get('alerts')} 条报警"
        return f"上报后端失败 HTTP {r.status_code}：{r.text[:200]}"
    except Exception as exc:
        return f"上报后端失败 {type(exc).__name__}：{exc}"


def unseen(alerts: list[dict]) -> list[dict]:
    """过滤掉 COOLDOWN_DAYS 内已推送过的同类报警。"""
    today = date.today()
    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    fresh = []
    for a in alerts:
        last = state.get(a["key"])
        if last:
            try:
                if (today - date.fromisoformat(last)).days < COOLDOWN_DAYS:
                    continue
            except ValueError:
                pass
        fresh.append(a)
        state[a["key"]] = today.isoformat()
    # 清掉很久以前的记录，别让文件无限长
    state = {k: v for k, v in state.items()
             if (today - date.fromisoformat(v)).days < COOLDOWN_DAYS * 4}
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return fresh


def match_words(low: str, kw: str) -> bool:
    """检索词里每个词都要在标题里整词出现，顺序不管。

    整词是关键：子串匹配下 tin 命中 prin(tin)g、gold 命中 goldman、unica 命中
    comm(unica)tions、wave 命中 heat(wave)，整页噪音就是这么来的。
    """
    return all(re.search(rf"\b{re.escape(w)}\b", low) for w in kw.split())


def why_news(name: str, on: date | None = None) -> list[tuple[str, str]]:
    """报警日往前 NEWS_LOOKBACK_DAYS 天该品类的新闻，最多 NEWS_PER_ALERT 条。

    daily_news 是英文的 google RSS 加 gdelt，所以用 rigid_list 的 keywords_en 命中、
    exclude_en 淘汰。这两道网都要：单词级的商品名歧义极大，搜 sugar 会拉回化妆品
    公司融资、糖厂铜失窃、含糖饮料禁令。

    查不到、库不在、SQL 炸了都返回空表——上层照常推报警，只是不带「为什么」。
    """
    kw, ex = news_terms_of(name)
    if not kw or not NEWS_DB.exists():
        return []
    end = on or date.today()
    start = end - timedelta(days=NEWS_LOOKBACK_DAYS)
    # SQL 只做粗筛：每个检索词挑最长的那个词去 LIKE。一个检索词要求所有词都出现，
    # 所以「含最长的词」必然是「全都含」的超集，粗筛不会漏掉真命中。
    # 7 天窗口有 6 万多条，不粗筛就得把它们全捞到 Python 里
    probes = sorted({max(k.split(), key=len) for k in kw})
    where = " OR ".join(["lower(headline) LIKE ?"] * len(probes))
    try:
        with sqlite3.connect(f"file:{NEWS_DB}?mode=ro", uri=True, timeout=15) as db:
            hits = db.execute(
                "SELECT fetch_date, headline FROM daily_news "
                f"WHERE fetch_date BETWEEN ? AND ? AND ({where}) "
                "ORDER BY fetch_date DESC",
                [start.isoformat(), end.isoformat(), *(f"%{p}%" for p in probes)],
            ).fetchall()
    except Exception:
        return []

    out, seen = [], set()
    for d, headline in hits:
        # 标题里的弯引号要拉平成直的，不然排除词 "cushing's" 对不上 Cushing’s
        # 综合征、检索词 "cote d'ivoire" 对不上 Côte d’Ivoire
        low = headline.lower().replace("\u2019", "'").replace("\u2018", "'")
        if any(x in low for x in ex):
            continue
        if not any(match_words(low, k) for k in kw):
            continue
        # 同一条新闻常同时进 google_rss 和 gdelt，标题略有差别，按前缀去重
        if low[:50] in seen:
            continue
        seen.add(low[:50])
        h = headline.strip()
        if len(h) > NEWS_HEADLINE_MAX:
            h = h[:NEWS_HEADLINE_MAX - 1] + "…"
        out.append((str(d)[:10], h))
        if len(out) >= NEWS_PER_ALERT:
            break
    return out


def chunk_lines(lines: list[str], limit: int = 1900) -> list[str]:
    """按行装箱。带上新闻之后正文轻易超过 Discord 的 2000 字上限，
    原来那种一刀切到 1900 会把后面的报警整条吃掉，改成分几条发。"""
    out, cur = [], ""
    for ln in lines:
        if len(ln) > limit:
            ln = ln[:limit - 1] + "…"
        if cur and len(cur) + 1 + len(ln) > limit:
            out.append(cur)
            cur = ln
        else:
            cur = f"{cur}\n{ln}" if cur else ln
    if cur:
        out.append(cur)
    return out


def notify(alerts: list[dict], rows: list[dict]) -> str:
    """推 Discord。推送失败不影响写笔记这个主要功能。"""
    if not alerts:
        return "无新报警，不推送"
    url = load_webhook()
    if not url:
        return "根 .env 里没有 DISCORD_WEBHOOK_TIGHTNESS，跳过推送"

    back = sorted([r for r in rows if r.get("ok") and r.get("term") and r["term"]["prem"] > 0],
                  key=lambda r: -r["term"]["prem"])
    lines = [f"**供给刚性清单 · 紧度报警**　{date.today()}", ""]
    for a in alerts:
        lines.append(f"- {a['text']}")
        news = why_news(a["key"].split("|")[0])
        lines += [f"　　· {d}　{h}" for d, h in news] if news else [f"　　· {NO_NEWS}"]
    if back:
        lines += ["", "当前处在倒挂（现货紧张）的品类："]
        lines.append("　" + "、".join(f"{r['name']} {r['term']['prem']:+.1%}" for r in back))

    sent = 0
    for body in chunk_lines(lines):
        try:
            r = requests.post(url, json={"content": body}, timeout=20)
            if r.status_code not in (200, 204):
                return f"推送失败 HTTP {r.status_code}：{r.text[:200]}（已发 {sent} 条）"
            sent += 1
        except Exception as exc:
            return f"推送失败 {type(exc).__name__}：{exc}（已发 {sent} 条）"
    return f"已推送 {len(alerts)} 条报警到 Discord（分 {sent} 条消息）"


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

    proxies = proxy_categories()
    print(f"扫 {len(FUTURES)} 个有期货的品类 + {len(proxies)} 个代理读数品类…")
    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(scan_one, FUTURES.items()))
        rows += list(ex.map(scan_proxy, proxies.items()))
    crack = crack_321()
    alerts = build_alerts(rows)

    text = replace_block(text, START, END, render(rows, crack))
    text = replace_block(text, A_START, A_END, render_alerts(alerts))
    NOTE.write_text(text, encoding="utf-8")

    ok = [r for r in rows if r.get("ok")]
    back = [r for r in ok if r.get("term") and r["term"]["prem"] > 0]
    npx = sum(1 for r in ok if r.get("source") == "proxy")
    print(f"写入完成：{len(ok)}/{len(rows)} 个品类有数据（其中代理读数 {npx} 个），"
          f"处在倒挂（现货紧张）的 {len(back)} 个")
    for r in sorted(back, key=lambda x: -x["term"]["prem"]):
        print(f"  倒挂 {r['name']}  近月溢价 {r['term']['prem']:+.1%}  5年分位 {qstr(r['q5'])}")
    print(f"报警 {len(alerts)} 条：")
    for a in alerts:
        print("  -", a["text"].replace("**", ""))
    fresh = unseen(alerts)
    print(notify(fresh, rows))
    print(push_backend(rows, fresh))
    return 0


if __name__ == "__main__":
    sys.exit(main())
