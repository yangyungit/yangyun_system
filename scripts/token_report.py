#!/usr/bin/env python3
"""复盘 Claude 花在哪儿了，推 Discord。数据来自 cursor_chat_scan.py 写的 token_watch.db。

用法：
    python3 token_report.py                    # 昨天全天（早上那次）
    python3 token_report.py --today            # 今天到现在（下午那次）
    python3 token_report.py --date 2026-09-14  # 指定某天
    python3 token_report.py --dry              # 只打印，不推 Discord
    python3 token_report.py --calibrate 2026-08-17 2026-09-15 1334.72
        # 用 Console 上这段时间的真实美元反推单价，存进库，以后都按这个算。
        # 区间越长越稳，建议直接用 Cost 页「近 30 天」那个总数。

单价怎么来的，按可靠性从高到低：
1. 根 .env 里有 ANTHROPIC_ADMIN_KEY —— 直接问 cost API 要那天的真实美元，
   再除以估算 token 得单价，不写死价格表，模型涨价也不用改代码。
2. 用 --calibrate 手动校准过一次 —— 读库里存的值。
3. 都没有 —— 用 DEFAULT_RATE 粗估，报告里标「粗估」。

Anthropic 的 cost API 只有日级，所以 --today 拿不到真实美元，只能按已知单价推。

会话分类走 DeepSeek（根 .env 的 DEEPSEEK_API_KEY），一天的会话一次调用批量分完，
结果存库不重复分类。拿不到 key 就跳过分类那一段，其余照发。
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "system" / "token_watch.db"
ENV = ROOT / ".env"

TZ = timezone(timedelta(hours=8))

# 没有 Admin key 也没校准过时的兜底单价（美元／百万输入 token）。
# 由来：2026-09-14 估算 6140 万输入 token，Console 上那天约 $88，约合 $1.43。
# 这个数只对「输入几乎全部命中缓存」的用法成立，别当价格表用。
DEFAULT_RATE = 1.43

CATS = [
    "改代码",
    "调试报错",
    "数据查询回测",
    "研究概念解释",
    "记账对账",
    "笔记文案",
    "出 plan 规划",
]
# 这几类不碰代码、不依赖本地数据，换个便宜模型不影响结果
SWITCHABLE = {"研究概念解释", "笔记文案"}


def read_env(key: str) -> str | None:
    """launchd 不加载 .env，脚本自己读。"""
    if not ENV.exists():
        return None
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip() or None
    return None


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        create table if not exists config (key text primary key, value text);
        create table if not exists chat_class (
            chat_id text primary key, cat text, classed_at text
        );
        """
    )


def day_tokens(con: sqlite3.Connection, day: str, until_hour: int | None) -> dict:
    """某天按小时汇总。until_hour 用于 --today，只算到当前这个小时。"""
    rows = con.execute(
        "select hour, chat_id, est_input_tokens, api_calls from chat_hours "
        "where hour like ?",
        (f"{day}%",),
    ).fetchall()
    if until_hour is not None:
        rows = [r for r in rows if int(r[0][-2:]) <= until_hour]

    by_hour: dict[str, dict] = {}
    by_chat: dict[str, int] = {}
    calls = 0
    for hour, cid, tok, n in rows:
        h = by_hour.setdefault(hour[-2:], {"tokens": 0, "chats": set()})
        h["tokens"] += tok
        h["chats"].add(cid)
        by_chat[cid] = by_chat.get(cid, 0) + tok
        calls += n or 0
    return {
        "by_hour": by_hour,
        "by_chat": by_chat,
        "calls": calls,
        "total": sum(by_chat.values()),
    }


def chat_meta(con: sqlite3.Connection, ids: list[str]) -> dict:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = con.execute(
        f"""select chat_id, title, repo, api_calls, tool_calls, ctx_final_tokens,
                   compacted, first_query, est_late_tokens, late_calls,
                   est_split_tokens, est_input_tokens,
                   lines_added, lines_removed, files_changed
            from chats where chat_id in ({marks})""",
        ids,
    ).fetchall()
    keys = (
        "title", "repo", "api_calls", "tool_calls",
        "ctx_final", "compacted", "first_query", "est_late", "late_calls",
        "est_split", "est_input",
        "lines_added", "lines_removed", "files_changed",
    )
    return {r[0]: dict(zip(keys, r[1:])) for r in rows}


def fetch_real_cost(admin_key: str, day: str) -> float | None:
    """问 cost API 要某天的真实美元。只有日级，返回 None 表示拿不到。"""
    start = f"{day}T00:00:00Z"
    end = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime(
        "%Y-%m-%dT00:00:00Z"
    )
    try:
        r = requests.get(
            "https://api.anthropic.com/v1/organizations/cost_report",
            params={"starting_at": start, "ending_at": end},
            headers={
                "x-api-key": admin_key,
                "anthropic-version": "2023-06-01",
                "User-Agent": "yangyun-token-watch/1.0",
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        print(f"cost API 请求失败 {type(exc).__name__}：{exc}")
        return None
    if r.status_code != 200:
        print(f"cost API 返回 HTTP {r.status_code}：{r.text[:300]}")
        return None

    cents = 0.0
    for bucket in r.json().get("data", []):
        for item in bucket.get("results", []):
            cents += float(item.get("amount") or 0)
        # UTC 日界跟北京时间差 8 小时，这里是整天口径，不逐条对齐
    return cents / 100 if cents else None


def resolve_rate(con: sqlite3.Connection, day: str, est_tokens: int) -> tuple[float, str]:
    """返回（美元／百万 token，这个单价怎么来的）。"""
    admin_key = read_env("ANTHROPIC_ADMIN_KEY")
    if admin_key and est_tokens:
        real = fetch_real_cost(admin_key, day)
        if real:
            rate = real / (est_tokens / 1e6)
            con.execute(
                "insert or replace into config values ('rate', ?)", (f"{rate:.4f}",)
            )
            con.commit()
            return rate, f"按 {day} 真实账单 ${real:.2f} 反推"

    row = con.execute("select value from config where key = 'rate'").fetchone()
    if row:
        return float(row[0]), "按校准过的单价"
    return DEFAULT_RATE, "粗估，未校准"


def classify(con: sqlite3.Connection, metas: dict) -> dict:
    """批量给会话分类，结果存库。已分过的不重复问。"""
    cached = {
        cid: cat
        for cid, cat in con.execute("select chat_id, cat from chat_class").fetchall()
    }
    todo = [cid for cid in metas if cid not in cached]
    if not todo:
        return {cid: cached[cid] for cid in metas if cid in cached}

    key = read_env("DEEPSEEK_API_KEY")
    if not key:
        print("根 .env 里没有 DEEPSEEK_API_KEY，跳过分类")
        return {cid: cached[cid] for cid in metas if cid in cached}

    items = [
        {
            "i": n,
            "title": metas[cid]["title"][:80],
            "q": (metas[cid]["first_query"] or "")[:300],
        }
        for n, cid in enumerate(todo)
    ]
    prompt = (
        "下面是一批 AI 编程助手的会话，每条给了标题和用户的第一个提问。"
        f"把每条归到这几类之一：{'、'.join(CATS)}。\n"
        '只回 JSON：{"results":[{"i":0,"cat":"改代码"}]}\n\n'
        + json.dumps(items, ensure_ascii=False)
    )
    try:
        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
            timeout=120,
        )
        r.raise_for_status()
        out = json.loads(r.json()["choices"][0]["message"]["content"])
    except (requests.RequestException, KeyError, ValueError) as exc:
        print(f"分类失败 {type(exc).__name__}：{exc}，这次跳过分类")
        return {cid: cached[cid] for cid in metas if cid in cached}

    now = datetime.now(TZ).isoformat(timespec="seconds")
    for item in out.get("results", []):
        try:
            cid = todo[int(item["i"])]
        except (KeyError, ValueError, IndexError):
            continue
        cat = item.get("cat") if item.get("cat") in CATS else "改代码"
        cached[cid] = cat
        con.execute("insert or replace into chat_class values (?,?,?)", (cid, cat, now))
    con.commit()
    return {cid: cached[cid] for cid in metas if cid in cached}


def build_report(con: sqlite3.Connection, day: str, until_hour: int | None) -> list[str]:
    cur = day_tokens(con, day, until_hour)
    if not cur["total"]:
        return [f"**Claude 花费复盘 · {day[5:]}**", "", "这天没有会话记录。"]

    rate, rate_note = resolve_rate(con, day, cur["total"])
    dollars = cur["total"] / 1e6 * rate
    metas = chat_meta(con, list(cur["by_chat"]))
    cats = classify(con, metas)

    prev_day = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    prev = day_tokens(con, prev_day, until_hour)["total"]
    delta = f"，比前一天 {(cur['total']/prev - 1)*100:+.0f}%" if prev else ""

    calls = cur["calls"]
    scope = "今天到现在" if until_hour is not None else "全天"
    lines = [
        f"**Claude 花费复盘 · {day[5:]}（{scope}）**",
        f"约 ${dollars:.0f}　{cur['total']/1e6:.0f}M 输入 token"
        f"　{calls} 次调用　{len(cur['by_chat'])} 个会话{delta}",
        f"　（单价 ${rate:.2f}/百万 token，{rate_note}）",
    ]

    lines += ["", "**最贵的会话**"]
    top = sorted(cur["by_chat"].items(), key=lambda kv: -kv[1])[:4]
    for cid, tok in top:
        m = metas.get(cid, {})
        tail = "，中途压缩过、实际更高" if m.get("compacted") else ""
        lines.append(
            f"- ${tok/1e6*rate:.1f}　{m.get('title','?')[:34]}"
            f"　{m.get('api_calls',0)} 次调用，上下文到 {(m.get('ctx_final') or 0)/1000:.0f}k{tail}"
        )

    hours = sorted(cur["by_hour"].items(), key=lambda kv: -kv[1]["tokens"])[:3]
    lines += ["", "**钱集中在**"]
    for hh, h in hours:
        share = h["tokens"] / cur["total"] * 100
        lines.append(
            f"- {hh}:00　${h['tokens']/1e6*rate:.1f}"
            f"　{len(h['chats'])} 个会话并行　占 {share:.0f}%"
        )

    # 同样的活儿，如果上下文一到 10 万就带交接摘要新开会话，要花多少。
    # est_split 是整个会话全程的，跨午夜时得按当天归属到的比例缩放，
    # 否则跟表头那个当天总额不是一个口径。
    inp = split = 0
    for c, tok in cur["by_chat"].items():
        m = metas.get(c)
        whole = (m or {}).get("est_input") or 0
        if not whole:
            continue
        share = tok / whole
        inp += tok
        split += (m.get("est_split") or 0) * share
    if inp and split < inp:
        late_calls = sum(
            metas[c].get("late_calls") or 0 for c in cur["by_chat"] if c in metas
        )
        lines += [
            "",
            f"**长会话的代价**：同样的活儿，上下文一到 10 万就带交接摘要新开会话，"
            f"只要 ${split/1e6*rate:.0f}——**省 ${(inp-split)/1e6*rate:.0f}"
            f"（{(1-split/inp)*100:.0f}%）**。"
            f"今天有 {late_calls} 次回复是在上下文超过 15 万时发出的，"
            f"每一次都把前面积累的全部历史重发了一遍。",
        ]

    if cats:
        agg: dict[str, int] = {}
        for cid, tok in cur["by_chat"].items():
            agg[cats.get(cid, "未分类")] = agg.get(cats.get(cid, "未分类"), 0) + tok
        lines += ["", "**按问题类型**"]
        for cat, tok in sorted(agg.items(), key=lambda kv: -kv[1]):
            lines.append(
                f"- {cat}　${tok/1e6*rate:.1f}（{tok/cur['total']*100:.0f}%）"
            )

        # 分类靠模型判断，会看走眼（问的是研究问题，聊到后面动手改了代码）。
        # 所以「能不能挪走」不听分类的，只认改动记录：动过文件的一律不算。
        movable = [
            (c, t)
            for c, t in sorted(cur["by_chat"].items(), key=lambda kv: -kv[1])
            if cats.get(c) in SWITCHABLE and c in metas and not touched_code(metas[c])
        ]
        if movable:
            total = sum(t for _, t in movable)
            lines += [
                "",
                f"**这些可以挪去 ChatGPT**：共 ${total/1e6*rate:.1f}"
                f"（{total/cur['total']*100:.0f}%），全程没改过一个文件",
                "　" + "、".join(metas[c]["title"][:22] for c, _ in movable[:4]),
            ]

    return lines


def touched_code(m: dict) -> bool:
    return bool(
        (m.get("files_changed") or 0)
        or (m.get("lines_added") or 0)
        or (m.get("lines_removed") or 0)
    )


def push(lines: list[str]) -> str:
    url = read_env("DISCORD_WEBHOOK_COST") or read_env("DISCORD_WEBHOOK_URL")
    if not url:
        return "根 .env 里没有 DISCORD_WEBHOOK_COST 或 DISCORD_WEBHOOK_URL，跳过推送"
    body = "\n".join(lines)[:1900]
    try:
        r = requests.post(url, json={"content": body}, timeout=20)
    except requests.RequestException as exc:
        return f"推送失败 {type(exc).__name__}：{exc}"
    if r.status_code not in (200, 204):
        return f"推送失败 HTTP {r.status_code}：{r.text[:200]}"
    return "已推送 Discord"


def calibrate(con: sqlite3.Connection, start: str, end: str, amount: float) -> None:
    """拿 Console 上一段时间的真实美元反推单价。区间越长越稳，建议用 30 天那个数。"""
    tok, = con.execute(
        "select sum(est_input_tokens) from chat_hours where hour >= ? and hour <= ?",
        (start, f"{end} 24"),
    ).fetchone()
    if not tok:
        print(f"{start} ~ {end} 没有会话记录，先跑 cursor_chat_scan.py 把天数扫够")
        return
    rate = amount / (tok / 1e6)
    con.execute("insert or replace into config values ('rate', ?)", (f"{rate:.4f}",))
    con.commit()
    print(
        f"{start} ~ {end} 估算 {tok/1e6:.0f}M token，真实 ${amount:.2f}"
        f" → 单价 ${rate:.2f}/百万，已存库"
    )


def main() -> None:
    con = sqlite3.connect(DB)
    init_db(con)

    if "--calibrate" in sys.argv:
        i = sys.argv.index("--calibrate")
        calibrate(con, sys.argv[i + 1], sys.argv[i + 2], float(sys.argv[i + 3]))
        con.close()
        return

    now = datetime.now(TZ)
    until_hour = None
    if "--today" in sys.argv:
        day = now.strftime("%Y-%m-%d")
        until_hour = now.hour
    elif "--date" in sys.argv:
        day = sys.argv[sys.argv.index("--date") + 1]
    else:
        day = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    lines = build_report(con, day, until_hour)
    con.close()

    print("\n".join(lines))
    if "--dry" not in sys.argv:
        print("\n" + push(lines))


if __name__ == "__main__":
    main()
