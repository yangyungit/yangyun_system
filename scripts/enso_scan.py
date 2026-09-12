#!/usr/bin/env python3
"""拉 NOAA CPC 的 ONI / RONI（厄尔尼诺指数），推给量化系统后端存档。

判定「厄尔尼诺发作」用 RONI（2026-02 起官方监控口径，扣掉海温长期升温趋势），
但 ONI 一起存一起显示——主理人笔记里的历史排名是按 ONI 算的，只存 RONI 会对不上。
"""

import urllib.request
from pathlib import Path

import requests

API = "http://127.0.0.1:8000"
ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"    # 4 列：SEAS YR TOTAL ANOM
RONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/RONI.ascii.txt"  # 3 列：SEAS YR ANOM
SEAS = ["DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ", "JJA", "JAS", "ASO", "SON", "OND", "NDJ"]


def _parse(url: str, ncol: int) -> dict[tuple[str, int], float]:
    raw = urllib.request.urlopen(url, timeout=30).read().decode()
    out = {}
    for ln in raw.strip().splitlines()[1:]:
        p = ln.split()
        if len(p) == ncol:
            out[(p[0], int(p[1]))] = float(p[-1])   # 两个源的异常值都在最后一列
    return out


def read_env(path: Path, key: str) -> str | None:
    """launchd 不加载 .env，脚本自己读。"""
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip() or None
    return None


def push_backend(rows: list[dict]) -> str:
    """把 ONI/RONI 全序列推给后端存档。后端不在线只打日志，不能让脚本失败。"""
    token = read_env(Path.home() / "yangyun/Code_Projects/valuation-radar/.env",
                      "RESONANCE_INTERNAL_TOKEN")
    if not token:
        return "valuation-radar/.env 里没有 RESONANCE_INTERNAL_TOKEN，跳过上报"
    try:
        r = requests.post(f"{API}/api/v1/tightness/enso/ingest", json={"rows": rows},
                           headers={"X-Internal-Token": token}, timeout=30)
        if r.status_code == 200:
            d = r.json()
            return f"已上报后端：{d.get('rows', len(rows))} 期"
        return f"上报后端失败 HTTP {r.status_code}：{r.text[:200]}"
    except Exception as exc:
        return f"上报后端失败 {type(exc).__name__}：{exc}"


def main() -> None:
    oni = _parse(ONI_URL, 4)
    roni = _parse(RONI_URL, 3)
    keys = sorted(set(oni) | set(roni), key=lambda k: (k[1], SEAS.index(k[0])))
    rows = [{"seas": s, "yr": y, "oni": oni.get((s, y)), "roni": roni.get((s, y))}
            for s, y in keys]

    latest = rows[-1]
    print(f"最新一期：{latest['seas']} {latest['yr']}  ONI={latest['oni']:+.2f}  RONI={latest['roni']:+.2f}")
    print(f"共 {len(rows)} 期，{sum(1 for r in rows if r['roni'] is not None)} 期有 RONI")
    print(push_backend(rows))


if __name__ == "__main__":
    main()
