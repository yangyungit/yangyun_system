#!/usr/bin/env python3
"""拉 EIA 周度库存，给「供给刚性清单」的紧度读数加一道实物确认。

`tightness_scan.py` 判断紧不紧全看期货近月溢价和载体股价异动——那些都是价格的
影子。价格能被资金推上去，库存推不上去：罐里有多少桶就是多少桶。两张表对照着
看才知道是真缺货还是只有钱在抢。

只做 EIA 是因为它是这批数据源里唯一有正规 JSON API 的。USDA 是 PDF、LME 免费档
是延迟两天的 Excel、ICE 是网页报告，那三个要写脆弱的抓取，留到第二期。

本版不推 Discord，先跑 30 天看报警频率。
"""

import json
import sys
from datetime import date
from pathlib import Path

import requests

API = "http://127.0.0.1:8000"
EIA_BASE = "https://api.eia.gov/v2"

# 分位阈值。库存和期货紧度方向相反：库存是越低越紧，所以看低分位
LOW_Q = 0.15        # 5 年分位低于这个数算库存偏紧
VERY_LOW_Q = 0.05   # 低于这个数算极低
HIST_MIN_W = 260    # 不足 5 年（260 周）不算分位，直接留空
DROP_WEEKS = 4      # 快速去库看 4 周
DROP_MIN = 0.08     # 4 周降幅超过 8% 算快速去库
DROP_Q_MAX = 0.35   # 快速去库还要求分位已经在 35% 以下，避免高位正常回落也报

COOLDOWN_DAYS = 30  # 与 tightness_scan 保持一致
STATE = Path(__file__).with_name("inventory_alert_state.json")

# 入库多少周历史。前端默认画 5 年，多存一倍留给拉长窗口；再往前的只用来算分位，
# 不落库——每行都要有 HIST_MIN_W 周垫底才算得出 q5
STORE_WEEKS = 520

# series_id -> (品类名, 显示名, EIA route)
# 品类名必须与 rigid_list.RIGID 的 key 完全一致，否则前端对不上
SERIES = {
    "WCESTUS1":              ("原油", "美国原油总库存（不含 SPR）", "petroleum/stoc/wstk"),
    "W_EPC0_SAX_YCUOK_MBBL": ("油品罐容", "库欣库存", "petroleum/stoc/wstk"),
    "WGTSTUS1":              ("汽油 RBOB", "美国汽油总库存", "petroleum/stoc/wstk"),
    "WDISTUS1":              ("炼能与柴油裂解价差", "美国馏分油库存", "petroleum/stoc/wstk"),
    "NW2_EPG0_SWO_R48_BCF":  ("美国天然气", "Lower 48 工作气量", "natural-gas/stor/wkly"),
}


def read_env(path: Path, key: str) -> str | None:
    """launchd 不加载 .env，脚本自己读。"""
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip() or None
    return None


def fetch_series(sid: str, route: str, key: str) -> list[dict]:
    """拉一个序列的全部历史，返回正序 [{period, value, unit}]。

    EIA 只给倒序，入算前必须翻正，否则分位和环比全是反的。
    """
    r = requests.get(f"{EIA_BASE}/{route}/data/", timeout=60, params={
        "api_key": key, "frequency": "weekly", "data[0]": "value",
        "facets[series][]": sid,
        "sort[0][column]": "period", "sort[0][direction]": "desc",
        "length": 5000})
    r.raise_for_status()
    out = []
    for d in r.json().get("response", {}).get("data", []):
        try:
            out.append({"period": d["period"], "value": float(d["value"]),
                        "unit": d.get("units")})
        except (TypeError, ValueError, KeyError):
            continue
    out.reverse()
    return out


def quantile_of(values: list[float], cur: float, years: int = 5) -> float | None:
    """库存 5 年分位。返回 0-1，越低说明库存越少越紧。"""
    w = values[-52 * years:]
    if len(w) < HIST_MIN_W:
        return None
    return sum(1 for v in w if v < cur) / len(w)


def build_rows(sid: str, key: str) -> list[dict]:
    """一个序列的最近 STORE_WEEKS 周，每周都按截至当周的历史算分位。"""
    category, name, route = SERIES[sid]
    hist = fetch_series(sid, route, key)
    if not hist:
        print(f"  {name}：EIA 返回空，跳过", file=sys.stderr)
        return []
    vals = [h["value"] for h in hist]
    rows = []
    for i in range(max(0, len(hist) - STORE_WEEKS), len(hist)):
        cur = vals[i]
        rows.append({
            "period": hist[i]["period"],
            "series_id": sid,
            "category": category,
            "name": name,
            "value": cur,
            "unit": hist[i]["unit"],
            "q5": quantile_of(vals[:i + 1], cur),
            "chg_4w": cur / vals[i - DROP_WEEKS] - 1 if i >= DROP_WEEKS else None,
            "yoy": cur / vals[i - 52] - 1 if i >= 52 else None,
        })
    return rows


def build_alerts(rows: list[dict]) -> list[dict]:
    """报警只建在每个序列的最新一期上。key 格式与 tightness_scan 一致，用于跨天去重。"""
    out = []

    def add(r: dict, kind: str, text: str):
        out.append({"key": f"{r['category']}|{kind}", "text": text})

    for r in rows:
        q5, unit = r["q5"], r["unit"] or ""
        head = f"{r['name']} {r['value']:,.0f} {unit}".rstrip()
        if q5 is not None:
            if q5 < VERY_LOW_Q:
                add(r, "库存极低", f"{head}，5 年分位 {q5:.0%}（近五年最低区间）")
            elif q5 < LOW_Q:
                add(r, "库存低位", f"{head}，5 年分位 {q5:.0%}")
        chg = r["chg_4w"]
        if chg is not None and q5 is not None and chg <= -DROP_MIN and q5 < DROP_Q_MAX:
            add(r, "快速去库",
                f"{r['name']} 四周降 {abs(chg):.1%}，5 年分位 {q5:.0%}")
    return out


def unseen(alerts: list[dict]) -> list[dict]:
    """过滤掉 COOLDOWN_DAYS 内已报过的同类报警。"""
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
    state = {k: v for k, v in state.items()
             if (today - date.fromisoformat(v)).days < COOLDOWN_DAYS * 4}
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return fresh


def push_backend(rows: list[dict], alerts: list[dict]) -> str:
    token = read_env(Path.home() / "yangyun/Code_Projects/valuation-radar/.env",
                     "RESONANCE_INTERNAL_TOKEN")
    if not token:
        return "valuation-radar/.env 里没有 RESONANCE_INTERNAL_TOKEN，跳过上报"
    payload = {"rows": rows,
               "alerts": [{"category": a["key"].split("|")[0],
                           "kind": a["key"].split("|")[1],
                           "text": a["text"]} for a in alerts]}
    try:
        r = requests.post(f"{API}/api/v1/inventory/ingest", json=payload,
                          headers={"X-Internal-Token": token}, timeout=30)
        if r.status_code == 200:
            d = r.json()
            return f"已上报后端：{d.get('rows')} 个序列、{d.get('alerts')} 条报警"
        return f"上报后端失败 HTTP {r.status_code}：{r.text[:200]}"
    except Exception as exc:
        return f"上报后端失败 {type(exc).__name__}：{exc}"


def qstr(v) -> str:
    return "—" if v is None else f"{v:.0%}"


def pstr(v) -> str:
    return "—" if v is None else f"{v:+.1%}"


def main() -> int:
    key = read_env(Path.home() / "yangyun/Code_Projects/valuation-radar/.env",
                   "EIA_API_KEY")
    if not key:
        print("valuation-radar/.env 里没有 EIA_API_KEY", file=sys.stderr)
        return 1

    rows, latest = [], []
    print(f"拉 EIA {len(SERIES)} 个周度库存序列…")
    for sid in SERIES:
        rs = build_rows(sid, key)
        rows += rs
        if rs:
            latest.append(rs[-1])

    if not latest:
        print("五个序列一个都没拉到", file=sys.stderr)
        return 1

    for r in latest:
        print(f"  {r['name']:22s} {r['period']}  {r['value']:>10,.0f} {r['unit']}  "
              f"5年分位 {qstr(r['q5'])}  四周 {pstr(r['chg_4w'])}  同比 {pstr(r['yoy'])}")

    alerts = build_alerts(latest)
    print(f"报警 {len(alerts)} 条：")
    for a in alerts:
        print("  -", a["text"])
    fresh = unseen(alerts)
    print(f"去重后新增 {len(fresh)} 条")
    print(push_backend(rows, fresh))
    return 0


if __name__ == "__main__":
    sys.exit(main())
