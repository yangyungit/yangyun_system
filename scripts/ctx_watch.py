#!/usr/bin/env python3
"""盯着正在用的 Cursor 会话，上下文堆高了就立刻推 Discord 提醒新开会话。

用法：
    python3 ctx_watch.py           # launchd 每 5 分钟跑一次
    python3 ctx_watch.py --dry     # 只打印，不推 Discord，也不记已提醒
    python3 ctx_watch.py --status  # 看当前所有活跃会话的上下文和每轮成本

原理：Cursor 把每个会话的真实 context token 数实时写在 state.vscdb 的
composerHeaders 里（contextUsagePercent * 3000，窗口 30 万），
实测会话聊着的时候这个值几分钟就更新一次。所以不用调任何 API、不花钱，
读本地库就能知道现在哪个会话正在重复烧钱。

为什么值得提醒：API 是无状态的，每次回复都要把整个对话重发一遍。
上下文到 20 万时，每一次回复光重发历史就要 20 万输入 token，
而一次提问背后往往是五到十次回复（每个工具调用算一次）。
这时候带一段交接摘要新开会话，每轮成本能立刻掉一到两个数量级。

同一个会话同一档只提醒一次，记在 token_watch.db 的 ctx_alerts 表里，不会刷屏。
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from token_report import DEFAULT_RATE, read_env

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "system" / "token_watch.db"
CURSOR_STATE = (
    Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
)

TZ = timezone(timedelta(hours=8))
CTX_WINDOW = 300_000

# 只管刚刚还在用的会话，早就停了的没必要提醒
ACTIVE_WITHIN = timedelta(minutes=12)

# 新开会话时带过去的交接摘要大小，用来算「新开之后每轮多少钱」
HANDOFF = 15_000

# (阈值 token, 档位名, 话术)
TIERS = [
    (220_000, "满", "窗口快满了，再聊下去会被自动压缩，压缩本身也要钱"),
    (150_000, "高", "这是花钱最凶的区间，建议现在就交接"),
    (100_000, "中", "开始明显变贵了，手上这段做完就换个会话"),
]


def load_rate(con: sqlite3.Connection) -> float:
    row = con.execute("select value from config where key = 'rate'").fetchone()
    return float(row[0]) if row else DEFAULT_RATE


def active_chats() -> list[dict]:
    """读本地库，挑出刚刚还在用的会话。"""
    cutoff = int((datetime.now(TZ) - ACTIVE_WITHIN).timestamp() * 1000)
    con = sqlite3.connect(f"file:{CURSOR_STATE}?mode=ro", uri=True)
    rows = con.execute(
        "select composerId, lastUpdatedAt, value from composerHeaders "
        "where lastUpdatedAt > ? and isSubagent = 0",
        (cutoff,),
    ).fetchall()
    con.close()

    out = []
    for cid, updated, value in rows:
        try:
            v = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            continue
        if v.get("isDraft"):
            continue
        pct = v.get("contextUsagePercent") or 0
        out.append(
            {
                "id": cid,
                "title": v.get("name") or "(未命名)",
                "pct": pct,
                "tokens": round(pct * CTX_WINDOW / 100),
                "updated": datetime.fromtimestamp(updated / 1000, TZ),
            }
        )
    return sorted(out, key=lambda c: -c["tokens"])


def tier_of(tokens: int) -> tuple[str, str] | None:
    for limit, name, advice in TIERS:
        if tokens >= limit:
            return name, advice
    return None


def already_alerted(con: sqlite3.Connection, cid: str, tier: str) -> bool:
    return bool(
        con.execute(
            "select 1 from ctx_alerts where chat_id = ? and tier = ?", (cid, tier)
        ).fetchone()
    )


def compose(chat: dict, tier: str, advice: str, rate: float) -> str:
    per_call = chat["tokens"] / 1e6 * rate
    after = HANDOFF / 1e6 * rate
    return "\n".join(
        [
            f"**上下文烧钱提醒 · {tier}**　{chat['updated'].strftime('%H:%M')}",
            f"「{chat['title'][:50]}」已到 {chat['tokens']/10000:.1f} 万 token"
            f"（窗口 {chat['pct']:.0f}%）",
            f"现在每次回复光重发历史就要 ${per_call:.2f}，"
            f"一次提问背后通常五到十次回复，所以一句话 ${per_call*5:.1f}–${per_call*10:.1f}",
            f"带一段交接摘要新开会话，头几轮每次约 ${after:.2f}",
            f"→ {advice}",
        ]
    )


def push(body: str) -> bool:
    url = read_env("DISCORD_WEBHOOK_COST") or read_env("DISCORD_WEBHOOK_URL")
    if not url:
        print("根 .env 里没有 DISCORD_WEBHOOK_COST，跳过推送")
        return False
    try:
        r = requests.post(url, json={"content": body[:1900]}, timeout=20)
    except requests.RequestException as exc:
        print(f"推送失败 {type(exc).__name__}：{exc}")
        return False
    if r.status_code not in (200, 204):
        print(f"推送失败 HTTP {r.status_code}：{r.text[:200]}")
        return False
    return True


def main() -> None:
    dry = "--dry" in sys.argv
    con = sqlite3.connect(DB)
    con.execute(
        "create table if not exists ctx_alerts ("
        "chat_id text, tier text, alerted_at text, primary key (chat_id, tier))"
    )
    rate = load_rate(con)
    chats = active_chats()

    if "--status" in sys.argv:
        print(f"单价 ${rate:.2f}/百万 token，活跃会话 {len(chats)} 个")
        print(f"{'token':>9} {'窗口':>5} {'每轮':>7}  最后活动  标题")
        for c in chats:
            print(
                f"{c['tokens']:>9,} {c['pct']:>4.0f}% "
                f"${c['tokens']/1e6*rate:>6.2f}  {c['updated'].strftime('%H:%M')}"
                f"    {c['title'][:40]}"
            )
        con.close()
        return

    sent = 0
    for c in chats:
        hit = tier_of(c["tokens"])
        if not hit:
            continue
        tier, advice = hit
        if already_alerted(con, c["id"], tier):
            continue
        body = compose(c, tier, advice, rate)
        print(body + "\n")
        if dry:
            continue
        if push(body):
            con.execute(
                "insert or replace into ctx_alerts values (?,?,?)",
                (c["id"], tier, datetime.now(TZ).isoformat(timespec="seconds")),
            )
            con.commit()
            sent += 1

    con.close()
    if not sent and not dry:
        print(f"活跃会话 {len(chats)} 个，没有需要新提醒的")


if __name__ == "__main__":
    main()
