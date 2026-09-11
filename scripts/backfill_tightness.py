#!/usr/bin/env python3
"""一次性回填 tightness_daily，让前端的时间序列图开箱就有半年历史。

不回填的话，图要等一个月才看得出形状。

每一天都按「那一天」重算，不用今天的窗口：
  - q5 用截止当天往前 5 年的价格算分位
  - 远月合约按当天往后 8-12 个月挑，不是拿今天的合约去比历史近月
    （拿今天的远月比 6 个月前的近月，等于把期限拉成 14-18 个月，曲线形状全变）

远月合约在 yfinance 有好几年历史（CLX26 能拉到 2018），所以这么做拉得动。

报警也一起补，否则前端那块要空一个月。用的是 tightness_scan 同一套规则和同样
五种类型，但当时并没有真的推过 Discord，所以文本前面标「回填」。
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent))
from tightness_scan import (  # noqa: E402
    API, COOLDOWN_DAYS, FUTURES, TERM, build_alerts, close_series, far_contract,
    read_env, verdict_of,
)

# 一个月 = 22 个交易日，与 tightness_scan 的口径一致
MONTH = 22


def far_map(root: str, suffix: str, months: str, days: list[pd.Timestamp]) -> dict:
    """每个交易日对应的远月合约代码。合约按月滚动，去重后只有几个代码要下载。"""
    return {d: far_contract(root, suffix, months, d.date()) for d in days}


def quantile_at(c: pd.Series, pos: int, years: int) -> float | None:
    """截止 c 的第 pos 行（含）往前 years 年的分位。"""
    w = c.iloc[max(0, pos + 1 - 252 * years): pos + 1]
    if len(w) < 252:
        return None
    return float((w < c.iloc[pos]).mean())


def backfill_one(item: tuple[str, str], days_back: int) -> list[dict]:
    name, ticker = item
    near = close_series(ticker)
    if near is None:
        print(f"  {name} 拉不到近月，跳过")
        return []
    start = pd.Timestamp(date.today() - timedelta(days=days_back))
    # 今天归每天跑的 tightness_scan 管，回填不碰，免得覆盖掉真实推过的报警
    dates = [d for d in near.index if start <= d and d.date() < date.today()]
    if not dates:
        return []

    # 远月：按当天算合约代码，同一个代码只下载一次
    prem_by_day: dict[pd.Timestamp, tuple[str, float]] = {}
    if name in TERM:
        root, suffix, months = TERM[name]
        # prem_prev 要往前多取一个月
        wide = [d for d in near.index if d >= start - pd.Timedelta(days=45)]
        fm = far_map(root, suffix, months, wide)
        cache = {}
        for code in sorted({c for c in fm.values() if c}):
            cache[code] = close_series(code)
        for d in wide:
            code = fm.get(d)
            f = cache.get(code) if code else None
            if f is None:
                continue
            fv = f.loc[:d]
            if fv.empty:
                continue
            n, fval = float(near.loc[d]), float(fv.iloc[-1])
            # 价格完全相同说明 Yahoo 把连续合约映射到了同一张合约，不是真的平价
            if fval <= 0 or abs(n - fval) < 1e-9:
                continue
            prem_by_day[d] = (code, n / fval - 1)

    pos_of = {d: i for i, d in enumerate(near.index)}
    out = []
    for d in dates:
        i = pos_of[d]
        prev_d = near.index[i - MONTH] if i >= MONTH else None
        code, prem = prem_by_day.get(d, (None, None))
        _, prem_prev = prem_by_day.get(prev_d, (None, None)) if prev_d is not None else (None, None)
        q5 = quantile_at(near, i, 5)
        out.append({
            "category": name, "ticker": ticker, "price": float(near.iloc[i]),
            "prem": prem, "prem_prev": prem_prev, "far_code": code,
            "q5": q5, "q5_prev": quantile_at(near, i - MONTH, 5) if i >= MONTH else None,
            "q10": quantile_at(near, i, 10),
            "r1m": float(near.iloc[i] / near.iloc[i - MONTH] - 1) if i >= MONTH else None,
            "r1y": float(near.iloc[i] / near.iloc[i - 253] - 1) if i >= 253 else None,
            "verdict": verdict_of(q5, prem),
            "snap_date": d.date().isoformat(),
        })
    print(f"  {name} {len(out)} 天，其中有期限结构的 {sum(1 for r in out if r['prem'] is not None)} 天")
    return out


def replay_alerts(by_day: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """按天重放 tightness_scan 的报警规则，带同样 30 天冷却去重。"""
    from datetime import date as _d
    last: dict[str, _d] = {}
    out: dict[str, list[dict]] = {}
    for snap in sorted(by_day):
        day = _d.fromisoformat(snap)
        scan_rows = [{
            "name": r["category"], "ok": True, "q5": r["q5"], "q5_prev": r["q5_prev"],
            "term": {"prem": r["prem"], "prem_prev": r["prem_prev"]}
            if r["prem"] is not None else None,
        } for r in by_day[snap]]
        fresh = []
        for a in build_alerts(scan_rows):
            prev = last.get(a["key"])
            if prev and (day - prev).days < COOLDOWN_DAYS:
                continue
            last[a["key"]] = day
            cat, kind = a["key"].split("|")
            fresh.append({"category": cat, "kind": kind,
                          "text": "回填 · " + a["text"].replace("**", "")})
        if fresh:
            out[snap] = fresh
    return out


def post_day(token: str, snap: str, rows: list[dict], alerts: list[dict]) -> bool:
    payload = {"snap_date": snap, "alerts": alerts,
               "rows": [{k: v for k, v in r.items() if k != "snap_date"} for r in rows]}
    r = requests.post(f"{API}/api/v1/tightness/ingest", json=payload,
                      headers={"X-Internal-Token": token}, timeout=60)
    if r.status_code != 200:
        print(f"{snap} 写入失败 HTTP {r.status_code}：{r.text[:200]}", file=sys.stderr)
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180, help="回填最近多少个自然日")
    args = ap.parse_args()

    token = read_env(Path.home() / "yangyun/Code_Projects/valuation-radar/.env",
                     "RESONANCE_INTERNAL_TOKEN")
    if not token:
        print("valuation-radar/.env 里没有 RESONANCE_INTERNAL_TOKEN", file=sys.stderr)
        return 1

    print(f"回填最近 {args.days} 天，{len(FUTURES)} 个品类…")
    with ThreadPoolExecutor(max_workers=4) as ex:
        chunks = list(ex.map(lambda it: backfill_one(it, args.days), FUTURES.items()))

    by_day: dict[str, list[dict]] = {}
    for rows in chunks:
        for r in rows:
            by_day.setdefault(r["snap_date"], []).append(r)

    alerts = replay_alerts(by_day)
    ok = 0
    for snap in sorted(by_day):
        if post_day(token, snap, by_day[snap], alerts.get(snap, [])):
            ok += 1
    print(f"写入 {ok}/{len(by_day)} 天，共 {sum(len(v) for v in by_day.values())} 行，"
          f"重放报警 {sum(len(v) for v in alerts.values())} 条")
    return 0 if ok == len(by_day) else 1


if __name__ == "__main__":
    sys.exit(main())
