#!/usr/bin/env python3
"""报警历史胜率——离线跑一次算完存表，不在 /latest 里实时算。

tightness_daily 只回填了半年，算不出全历史胜率，所以从 yfinance 重拉每个品类的
全部历史，按 tightness_scan.build_alerts 同一套规则逐日重放报警（同样的 30 天冷
却），再算每条报警后 T+60/120/250 的表现。

期限结构类报警（翻转倒挂 / 倒挂加深 / 倒挂消失）不算胜率——已到期合约 yfinance
拉不到（`CCK24.NYB` 等实测 0 天），回测不了，不要在这里补。

命中 = 绝对收益 > 0，商品没有统一基准，不算超额。对照组是同品类任意一天买入
持有同样天数的收益中位数，没有它 43% 这种数字看不出好坏。
"""

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from sortedcontainers import SortedList

sys.path.insert(0, str(Path(__file__).parent))
from tightness_scan import (  # noqa: E402
    API, COOLDOWN_DAYS, FUTURES, PROXY_HIST_MIN, PROXY_JUMP_CAP, PROXY_JUMP_FLOOR,
    PROXY_JUMP_Q, PROXY_VOL_MULT, TIGHT_Q, close_series, ohlcv, read_env,
)

sys.path.insert(0, str(Path.home() / "yangyun/Code_Projects/valuation-radar"))
from rigid_list import proxy_categories  # noqa: E402

HITRATE_HORIZONS = (60, 120, 250)
HITRATE_MIN_N = 5
HITRATE_KINDS = ("进入紧张区", "分位跳升", "代理载体异动")


def _rolling_rank(vals, window: int, min_len: int) -> list:
    """逐日的「过去 window 天里，当前值比多少比例的历史值大」。

    等价于逐天调用 backfill_tightness.quantile_at，用 SortedList 维护定长窗口把
    暴力 O(n²) 降到 O(n log n)——16 个品类里有几个是几十年的日线，暴力算太慢。
    """
    sl = SortedList()
    out = [None] * len(vals)
    for i, v in enumerate(vals):
        sl.add(float(v))
        if len(sl) > window:
            sl.remove(float(vals[i - window]))
        if len(sl) >= min_len:
            out[i] = sl.bisect_left(float(v)) / len(sl)
    return out


def _expanding_quantile(vals, q: float, min_len: int, lo: float, hi: float, default: float) -> list:
    """截止每天的全部历史算分位数，口径同 tightness_scan.jump_thr（「用截至当天全部
    历史算」，不是滚动窗口），一样用 SortedList 把重复排序省掉。
    """
    sl = SortedList()
    out = [default] * len(vals)
    for i, v in enumerate(vals):
        if v == v:  # 排除 NaN（前 22 天 shift 出来的）
            sl.add(float(v))
        n = len(sl)
        if n >= min_len:
            idx = q * (n - 1)
            lo_i, hi_i = int(idx), min(int(idx) + 1, n - 1)
            val = sl[lo_i] + (sl[hi_i] - sl[lo_i]) * (idx - lo_i)
            out[i] = min(max(val, lo), hi)
    return out


def _vol_mult_at(v: pd.Series, pos: int):
    """口径同 tightness_scan.vol_mult，只是取到 pos 为止的窗口。"""
    w = v.iloc[max(0, pos + 1 - 274): pos + 1].dropna()
    if len(w) < 274 or (w <= 0).mean() > 0.3:
        return None
    base = float(w.iloc[:-22].median())
    return float(w.iloc[-22:].median()) / base if base > 0 else None


def alerts_for_futures(ticker: str):
    """price-quantile 类报警：进入紧张区 + 分位跳升。跟 build_alerts 同一套判据。"""
    c = close_series(ticker, "max")
    if c is None or len(c) < 252:
        return [], None
    q5 = _rolling_rank(c.values, 252 * 5, 252)
    alerts = []
    last_seen = {}
    for i in range(22, len(c)):
        cur, prev = q5[i], q5[i - 22]
        if cur is None or prev is None:
            continue
        if cur >= TIGHT_Q > prev:
            kind = "进入紧张区"
        elif cur - prev > 0.2:
            kind = "分位跳升"
        else:
            continue
        day = c.index[i].date()
        seen = last_seen.get(kind)
        if seen and (day - seen).days < COOLDOWN_DAYS:
            continue
        last_seen[kind] = day
        alerts.append({"kind": kind, "pos": i})
    return alerts, c


def alerts_for_proxy(ticker: str):
    """代理载体异动：涨幅过自己的异动线，且放量。跟 build_alerts 同一套判据。"""
    h = ohlcv(ticker, "max")
    if h is None or len(h) < PROXY_HIST_MIN:
        return [], None
    c, v = h["Close"], h["Volume"]
    r22 = c / c.shift(22) - 1
    thr = _expanding_quantile(r22.values, PROXY_JUMP_Q, PROXY_HIST_MIN,
                              PROXY_JUMP_FLOOR, PROXY_JUMP_CAP, PROXY_JUMP_CAP)
    alerts = []
    last_seen = None
    for i in range(22, len(c)):
        r1m = r22.iloc[i]
        if r1m != r1m or r1m <= thr[i]:
            continue
        vm = _vol_mult_at(v, i)
        if vm is not None and vm < PROXY_VOL_MULT:
            continue
        day = c.index[i].date()
        if last_seen and (day - last_seen).days < COOLDOWN_DAYS:
            continue
        last_seen = day
        alerts.append({"kind": "代理载体异动", "pos": i})
    return alerts, c


def _forward_return(c: pd.Series, pos: int, h: int) -> float:
    """T+h 收益，落在数据末尾外的用最后一天（不是 None，报警发生过，总要有个数）。"""
    end = min(pos + h, len(c) - 1)
    return float(c.iloc[end]) / float(c.iloc[pos]) - 1


def _control_median(c: pd.Series, h: int):
    """对照组：同品类任意一天买入持有 h 天，只用能凑出完整窗口的天数。"""
    vals = c.values
    n = len(vals)
    if n <= h:
        return None
    rs = [vals[i + h] / vals[i] - 1 for i in range(n - h)]
    return float(pd.Series(rs).median())


def summarize(category: str, kind: str, alerts: list, c) -> dict:
    row = {"category": category, "kind": kind, "n": len(alerts),
           "win_t120": None, "med_t60": None, "med_t120": None,
           "med_t250": None, "base_t120": None}
    if len(alerts) < HITRATE_MIN_N or c is None:
        return row
    rets = {h: [_forward_return(c, a["pos"], h) for a in alerts] for h in HITRATE_HORIZONS}
    s120 = pd.Series(rets[120])
    row["win_t120"] = float((s120 > 0).mean())
    row["med_t60"] = float(pd.Series(rets[60]).median())
    row["med_t120"] = float(s120.median())
    row["med_t250"] = float(pd.Series(rets[250]).median())
    row["base_t120"] = _control_median(c, 120)
    return row


def push(rows: list) -> str:
    """把胜率表推给量化系统后端存档。"""
    token = read_env(Path.home() / "yangyun/Code_Projects/valuation-radar/.env",
                      "RESONANCE_INTERNAL_TOKEN")
    if not token:
        return "valuation-radar/.env 里没有 RESONANCE_INTERNAL_TOKEN，跳过上报"
    try:
        r = requests.post(f"{API}/api/v1/tightness/hitrate/ingest", json={"rows": rows},
                           headers={"X-Internal-Token": token}, timeout=60)
        if r.status_code == 200:
            d = r.json()
            return f"已上报后端：{d.get('rows', len(rows))} 行"
        return f"上报后端失败 HTTP {r.status_code}：{r.text[:200]}"
    except Exception as exc:
        return f"上报后端失败 {type(exc).__name__}：{exc}"


def main() -> None:
    rows = []

    for name, ticker in FUTURES.items():
        print(f"{name}（{ticker}）...")
        try:
            alerts, c = alerts_for_futures(ticker)
        except Exception as exc:
            print(f"  跳过：{type(exc).__name__}: {exc}")
            continue
        for kind in ("进入紧张区", "分位跳升"):
            row = summarize(name, kind, [a for a in alerts if a["kind"] == kind], c)
            rows.append(row)
            print(f"  {kind}: n={row['n']} win_t120={row['win_t120']} "
                  f"med_t120={row['med_t120']} base_t120={row['base_t120']}")

    for name, ticker in proxy_categories().items():
        print(f"{name}（{ticker}，代理）...")
        try:
            alerts, c = alerts_for_proxy(ticker)
        except Exception as exc:
            print(f"  跳过：{type(exc).__name__}: {exc}")
            continue
        row = summarize(name, "代理载体异动", alerts, c)
        rows.append(row)
        print(f"  代理载体异动: n={row['n']} win_t120={row['win_t120']} "
              f"med_t120={row['med_t120']} base_t120={row['base_t120']}")

    print(f"共 {len(rows)} 行（(category, kind) 对），{sum(r['n'] for r in rows)} 条报警")
    print(push(rows))


if __name__ == "__main__":
    main()
