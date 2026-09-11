#!/usr/bin/env python3
"""把历史事件回测灌进后端，供「供给紧度」页按品类查「这个品类遇到过什么」。

事件元数据（含卡住哪一环）来自 backfill_events.py 的 EVENTS，逐载体表现来自
backfill_events_raw.csv。回测本身不重跑——CSV 是 2026-09-11 那次的产物，
重跑一次要拉 37 个代码的全历史。

事件名旁边的 stage_hit 是这份数据里唯一真正值钱的判断：红海那次全买 10 个候选
中位只有 +3.4%，但判断对「卡的是航线不是货」去买运费载体 BDRY 是 +88.4%。
"""

import csv
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path.home() / "yangyun/Code_Projects/valuation-radar"))

from backfill_events import EVENTS  # noqa: E402
from rigid_list import RIGID  # noqa: E402

API = "http://127.0.0.1:8000"
CSV_PATH = Path(__file__).with_name("backfill_events_raw.csv")

# CSV 表头是中文，这里写死映射
COLS = {
    "事件": "event_id", "代码": "ticker", "类型": "kind", "状态": "status",
    "T0日期": "t0_date", "前20日": "r_pre20", "T0当日": "r_t0",
    "T+5": "r_t5", "T+20": "r_t20", "T+60": "r_t60", "T+120": "r_t120",
    "最大回撤": "mdd", "日均成交额": "dollar_vol",
    "超额T+5": "ex_t5", "超额T+20": "ex_t20", "超额T+60": "ex_t60",
    "超额T+120": "ex_t120", "超额前20日": "ex_pre20",
}
TEXT_COLS = {"event_id", "ticker", "kind", "status", "t0_date"}


def read_env(path: Path, key: str) -> str | None:
    """launchd 不加载 .env，脚本自己读。"""
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip() or None
    return None


def load_events() -> list[dict]:
    out = []
    for e in EVENTS:
        bad = [c for c in e["categories"] if c not in RIGID]
        if bad:
            raise SystemExit(f"事件 {e['id']} 的品类对不上 rigid_list.RIGID：{bad}")
        out.append({
            "event_id": e["id"], "name": e["name"], "event_date": e["date"],
            "note": e["note"], "categories": e["categories"],
            "stage_hit": e["stage_hit"], "stage_note": e["stage_note"],
        })
    return out


def load_perf() -> list[dict]:
    rows = []
    with CSV_PATH.open(encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            # SPY 基准行不入库：超额那几列已经把基准扣掉了，再存一遍只会污染排序
            if raw["类型"] == "基准":
                continue
            r = {}
            for zh, en in COLS.items():
                v = (raw.get(zh) or "").strip()
                if en in TEXT_COLS:
                    r[en] = v or None
                else:
                    try:
                        r[en] = float(v)
                    except ValueError:
                        r[en] = None
            rows.append(r)
    return rows


def main() -> int:
    if not CSV_PATH.exists():
        print(f"找不到 {CSV_PATH}", file=sys.stderr)
        return 1
    token = read_env(Path.home() / "yangyun/Code_Projects/valuation-radar/.env",
                     "RESONANCE_INTERNAL_TOKEN")
    if not token:
        print("valuation-radar/.env 里没有 RESONANCE_INTERNAL_TOKEN", file=sys.stderr)
        return 1

    events, perf = load_events(), load_perf()
    print(f"{len(events)} 个事件、{len(perf)} 行载体表现，开始上报…")
    r = requests.post(f"{API}/api/v1/tightness/events/ingest",
                      json={"events": events, "perf": perf},
                      headers={"X-Internal-Token": token}, timeout=60)
    if r.status_code != 200:
        print(f"上报失败 HTTP {r.status_code}：{r.text[:300]}", file=sys.stderr)
        return 1
    print("已入库：", r.json())
    return 0


if __name__ == "__main__":
    sys.exit(main())
