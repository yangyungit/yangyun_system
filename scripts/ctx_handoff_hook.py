#!/usr/bin/env python3
"""Cursor stop hook：一轮回复结束时查当前会话的上下文，过档就让这个会话自己写交接摘要。

装在 Code_Projects/.cursor/hooks.json 的 stop 事件上。Cursor 每次回复完调一次，
stdin 给一段 JSON（conversation_id / status / loop_count / transcript_path ...），
stdout 返回 {"followup_message": "..."} 时 Cursor 会把它当成一条新的用户消息自动提交。

为什么要挂在这儿而不是继续靠 ctx_watch 推 Discord：
Discord 只能告诉你「该换会话了」，换不了。真正的痛点是换窗口时不知道怎么复述上下文，
而唯一知道上下文的就是正在聊的这个会话本身。stop hook 是唯一能反过来驱动它干活的入口。

摘要内容和落盘位置由 handoff skill 规定，本脚本只负责判断时机和发口令。
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "system" / "token_watch.db"
CURSOR_STATE = (
    Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
)

TZ = timezone(timedelta(hours=8))
CTX_WINDOW = 300_000

# 自动写摘要的档位。10 万这档是算出来的不是拍的：近 30 天数据重跑反事实，
# 10 万拆省 29%（约 $400/月），15 万只省 15.5%（$214/月），代价是一天多换六次窗口。
# 22 万那档是兜底，给 10 万没听劝的时候再催一次。
# 档位名跟着阈值走，改了阈值旧的已提醒记录就不会挡住新档。
TIERS = [220_000, 100_000]


def tier_name(limit: int) -> str:
    return f"auto-{limit // 1000}k"


def ctx_tokens(cid: str) -> tuple[int, str] | None:
    """读 Cursor 自己的库，拿这个会话当前的真实 context token 数和标题。"""
    if not CURSOR_STATE.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{CURSOR_STATE}?mode=ro", uri=True)
        row = con.execute(
            "select value from composerHeaders where composerId = ?", (cid,)
        ).fetchone()
        con.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    try:
        v = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return None
    pct = v.get("contextUsagePercent") or 0
    return round(pct * CTX_WINDOW / 100), (v.get("name") or "当前会话")


def tier_of(tokens: int) -> str | None:
    for limit in TIERS:
        if tokens >= limit:
            return tier_name(limit)
    return None


def claim(cid: str, tier: str) -> bool:
    """同一会话同一档只催一次。抢到返回 True。"""
    con = sqlite3.connect(DB)
    con.execute(
        "create table if not exists ctx_alerts ("
        "chat_id text, tier text, alerted_at text, primary key (chat_id, tier))"
    )
    cur = con.execute(
        "insert or ignore into ctx_alerts values (?,?,?)",
        (cid, tier, datetime.now(TZ).isoformat(timespec="seconds")),
    )
    con.commit()
    con.close()
    return cur.rowcount > 0


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    # 中断和报错时别插话；已经被 followup 驱动过一轮就收手，免得摘要自己又触发摘要
    if payload.get("status") != "completed" or payload.get("loop_count", 0) > 0:
        return

    cid = payload.get("conversation_id")
    if not cid:
        tp = payload.get("transcript_path")
        cid = Path(tp).stem if tp else None
    if not cid:
        return

    hit = ctx_tokens(cid)
    if not hit:
        return
    tokens, title = hit

    tier = tier_of(tokens)
    if not tier or not claim(cid, tier):
        return

    msg = (
        f"【上下文 {tokens/10000:.1f} 万 token，窗口 {tokens/CTX_WINDOW*100:.0f}%】"
        "现在按 handoff skill 生成一份交接摘要：读 "
        "Code_Projects/.cursor/skills/handoff/SKILL.md 并照它执行，"
        "写盘 + 复制到剪贴板 + 在回复里贴出文件路径。"
        "手上如果还有没做完的活，先把进度写进摘要的「进行中」一节，不用停下来等我。"
    )
    json.dump({"followup_message": msg}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
