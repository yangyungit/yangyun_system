#!/usr/bin/env python3
"""扫 Cursor 本地会话记录，估算每个聊天框烧掉多少 token，写进 system/token_watch.db。

用法：
    python3 cursor_chat_scan.py          # 默认扫最近 2 天
    python3 cursor_chat_scan.py 30       # 回溯 30 天
    python3 cursor_chat_scan.py 2 --top 15   # 顺便打印最贵的 15 个会话

两个数据源：
- Cursor 的 state.vscdb，表 composerHeaders：会话标题、改了哪个仓、加删行数，
  以及 contextUsagePercent —— 乘 3000 就是该会话最后的真实 context token 数
  （窗口 30 万；195 个样本乘 3000 全是整数，比例已验证）。
- agent-transcripts/<id>/<id>.jsonl：每条消息正文，用户提问带分钟级时间戳。

估算逻辑：每次模型回复都是一次独立 API 调用，都要重发整个对话，
所以一个会话的计费输入 ≈ 各次调用时的 context 之和，跟回复条数近似成平方关系。
聊天记录里没存工具返回结果（读文件、跑命令的输出），那部分是 context 的大头，
所以先按「可见字数 + 每次工具调用补一个常数」拼出 context 增长的形状，
再用 composerHeaders 的真实终值把整条曲线校准到正确刻度。
形状仍是估的，只够用来排「哪个会话最贵」，不能当账单——
真实美元由 token_report.py 用 Anthropic 的 cost API 按占比摊过来。

会话中途被压缩过时 contextUsagePercent 会掉下来，校准终值偏小、估算偏低，
这类会话打 compacted 标记，报告里单独说明。
"""
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "system" / "token_watch.db"

CURSOR_STATE = (
    Path.home()
    / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
)
PROJECTS = Path.home() / ".cursor/projects"

CTX_WINDOW = 300_000  # contextUsagePercent 的分母，pct * 3000 = token 数
TZ = timezone(timedelta(hours=8))

# 每轮必付的地板：system prompt + 工具定义 + rules + skills + subagent 定义。
# 一句话没说就已经占掉这么多，新开会话也照付，省不掉。
# 2026-09-15 从 Cursor 的 Context Usage 面板实测 41.8K（工具定义 22.7K 是大头，
# rules 9.7K，skills 3.4K，system prompt 4K，subagent 定义 2K）；
# 同日删掉两个重复的 rule 文件后降约 2.6K，取中间值 4 万。
# 加这个之前估算曲线是从 0 开始爬的，等于假设第一轮免费，早期轮次严重低估，
# 反事实拆会话也少算了「每段都要重付一次地板」，省下的比例算得偏高。
CTX_FLOOR = 40_000

# 上下文超过这个数之后的每一轮，都在为前面一大段历史重复付费
LATE_CTX = 150_000

# 反事实测算：假设上下文一到 SPLIT_AT 就新开会话、带 HANDOFF 大小的交接摘要过去，
# 同样的活儿要花多少。用来回答「早点新开会话到底能省多少」。
# SPLIT_AT 跟 ctx_handoff_hook.py 的第一档对齐（都是含地板的真实 context 口径），
# 所以这个反事实测的就是「一直按 hook 的策略办能省多少」，跑几天可以拿实际账单验。
SPLIT_AT = 150_000
HANDOFF = 15_000

# 工具返回结果没进聊天记录，按这个常数占位。只影响 context 增长的形状，
# 整体刻度由真实终值校准掉，所以不用调准。
TOOL_RESULT_CHARS = 3000

TS_RE = re.compile(r"<timestamp>([^<]+)</timestamp>")
QUERY_RE = re.compile(r"<user_query>(.*?)</user_query>", re.S)
CJK_RE = re.compile(r"[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef]")


def est_tokens(text: str) -> int:
    """中日韩字符按 1 token 算，其余按 4 字符 1 token。"""
    cjk = len(CJK_RE.findall(text))
    return cjk + (len(text) - cjk) // 4


def block_text(b: dict) -> str:
    if b.get("type") == "text":
        return b.get("text") or ""
    return json.dumps(b.get("input") or {}, ensure_ascii=False)


def parse_ts(raw: str) -> datetime | None:
    """解析 'Monday, Sep 14, 2026, 12:25 AM (UTC+8)'。"""
    m = re.match(r"^\w+, (\w+ \d+, \d{4}, \d+:\d+ [AP]M)", raw.strip())
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%b %d, %Y, %I:%M %p").replace(tzinfo=TZ)
    except ValueError:
        return None


def read_headers(since_ms: int) -> dict:
    """从 composerHeaders 取会话元数据。"""
    con = sqlite3.connect(f"file:{CURSOR_STATE}?mode=ro", uri=True)
    rows = con.execute(
        "select composerId, createdAt, lastUpdatedAt, value from composerHeaders "
        "where lastUpdatedAt > ? and isSubagent = 0",
        (since_ms,),
    ).fetchall()
    con.close()

    out = {}
    for cid, created, updated, value in rows:
        try:
            v = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            continue
        if v.get("isDraft"):
            continue
        repos = [
            Path(r["repoPath"]).name
            for r in v.get("trackedGitRepos") or []
            if r.get("repoPath")
        ]
        out[cid] = {
            "title": v.get("name") or "(未命名)",
            "subtitle": v.get("subtitle") or "",
            "repo": ",".join(sorted(set(repos))),
            "created_at": created,
            "updated_at": updated,
            "ctx_final": round((v.get("contextUsagePercent") or 0) * CTX_WINDOW / 100),
            "files_changed": v.get("filesChangedCount") or 0,
            "lines_added": v.get("totalLinesAdded") or 0,
            "lines_removed": v.get("totalLinesRemoved") or 0,
        }
    return out


def find_transcript(chat_id: str) -> Path | None:
    for proj in PROJECTS.iterdir():
        p = proj / "agent-transcripts" / chat_id / f"{chat_id}.jsonl"
        if p.is_file():
            return p
    return None


def load_messages(path: Path) -> list[dict]:
    """逐条读消息，抽出角色、可见字数、工具调用次数、时间戳。"""
    msgs = []
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = d.get("role")
        if role not in ("user", "assistant"):
            continue

        content = d.get("message", {}).get("content")
        text_chars = tool_calls = tokens = 0
        ts = query = None
        if isinstance(content, list):
            for b in content:
                kind = b.get("type")
                if kind not in ("text", "tool_use"):
                    continue
                t = block_text(b)
                text_chars += len(t)
                tokens += est_tokens(t)
                if kind == "tool_use":
                    tool_calls += 1
                elif role == "user":
                    if ts is None:
                        m = TS_RE.search(t)
                        if m:
                            ts = parse_ts(m.group(1))
                    if query is None:
                        q = QUERY_RE.search(t)
                        if q and q.group(1).strip():
                            query = q.group(1).strip()
        elif isinstance(content, str):
            text_chars = len(content)
            tokens = est_tokens(content)

        msgs.append(
            {
                "role": role,
                "chars": text_chars,
                "tokens": tokens,
                "tools": tool_calls,
                "ts": ts,
                "query": query,
            }
        )
    return msgs


def interp_times(msgs: list[dict], start: datetime, end: datetime) -> None:
    """用户提问带时间戳，模型回复没有；拿已知时间点做锚，线性插值补齐。"""
    anchors = [(i, m["ts"]) for i, m in enumerate(msgs) if m["ts"]]
    anchors = [(-1, start)] + anchors + [(len(msgs), end)]
    anchors.sort(key=lambda x: x[0])

    for (i0, t0), (i1, t1) in zip(anchors, anchors[1:]):
        span = i1 - i0
        if span <= 0:
            continue
        for i in range(max(i0, 0), min(i1, len(msgs))):
            if msgs[i]["ts"] is None:
                frac = (i - i0) / span
                msgs[i]["ts"] = t0 + (t1 - t0) * frac


def score_chat(msgs: list[dict], ctx_final: int) -> dict:
    """算计费输入、输出、以及按小时的分布。"""
    raw = [m["chars"] + m["tools"] * TOOL_RESULT_CHARS for m in msgs]
    cum, total = [], 0
    for r in raw:
        total += r
        cum.append(total)

    # 终值里扣掉地板才是对话本身，剩下的按字符比例摊回去，终点仍对齐真实值。
    convo = max(ctx_final - CTX_FLOOR, 0)
    scale = (convo / total) if (total and convo) else 0.0

    est_input = est_late = late_calls = est_split = 0
    hours: dict[str, list[int]] = {}
    api_calls = 0
    base, seg_start = CTX_FLOOR, 0  # 反事实里当前这一段的起点
    segments = 1
    for i, m in enumerate(msgs):
        ctx_here = CTX_FLOOR + (round(cum[i] * scale) if scale else cum[i] // 4)
        ctx_split = base + round((cum[i] - seg_start) * scale) if scale else ctx_here
        if ctx_split > SPLIT_AT:
            # 新开一段：地板重付一次，再加上带过去的交接摘要
            base, seg_start = CTX_FLOOR + HANDOFF, cum[i - 1] if i else 0
            ctx_split = base + round((cum[i] - seg_start) * scale)
            segments += 1
        if m["role"] != "assistant":
            continue
        api_calls += 1
        est_input += ctx_here
        est_split += ctx_split
        if ctx_here > LATE_CTX:
            est_late += ctx_here
            late_calls += 1
        if m["ts"]:
            key = m["ts"].strftime("%Y-%m-%d %H")
            slot = hours.setdefault(key, [0, 0])
            slot[0] += ctx_here
            slot[1] += 1

    est_output = sum(m["tokens"] for m in msgs if m["role"] == "assistant")
    return {
        "api_calls": api_calls,
        "user_turns": sum(1 for m in msgs if m["role"] == "user"),
        "tool_calls": sum(m["tools"] for m in msgs),
        "est_input": est_input,
        "est_output": est_output,
        "est_late": est_late,
        "late_calls": late_calls,
        "est_split": est_split,
        "segments": segments,
        "hours": hours,
        "scale_ok": bool(scale),
    }


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        create table if not exists chats (
            chat_id text primary key,
            title text, subtitle text, repo text,
            started_at text, ended_at text,
            api_calls integer, user_turns integer, tool_calls integer,
            ctx_final_tokens integer,
            est_input_tokens integer, est_output_tokens integer,
            est_late_tokens integer, late_calls integer,
            est_split_tokens integer, split_segments integer,
            files_changed integer, lines_added integer, lines_removed integer,
            first_query text,
            compacted integer,
            scanned_at text
        );
        create table if not exists chat_hours (
            chat_id text, hour text, est_input_tokens integer, api_calls integer,
            primary key (chat_id, hour)
        );
        create index if not exists idx_chats_ended on chats (ended_at);
        create index if not exists idx_hours_hour on chat_hours (hour);
        """
    )


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    days = int(args[0]) if args else 2
    top = 0
    if "--top" in sys.argv:
        idx = sys.argv.index("--top")
        top = int(sys.argv[idx + 1]) if len(sys.argv) > idx + 1 else 10

    since = datetime.now(TZ) - timedelta(days=days)
    headers = read_headers(int(since.timestamp() * 1000))

    con = sqlite3.connect(DB)
    init_db(con)

    scanned = skipped = 0
    rows = []
    for cid, h in headers.items():
        path = find_transcript(cid)
        if not path:
            skipped += 1
            continue
        msgs = load_messages(path)
        if not msgs:
            skipped += 1
            continue

        start = datetime.fromtimestamp(h["created_at"] / 1000, TZ)
        end = datetime.fromtimestamp(h["updated_at"] / 1000, TZ)
        interp_times(msgs, start, end)
        s = score_chat(msgs, h["ctx_final"])

        # 回复多但 context 反而不高 = 中途被压缩过，终值校准会偏低
        compacted = int(s["api_calls"] > 40 and h["ctx_final"] < 120_000)

        first_q = next(
            (m["query"] for m in msgs if m.get("query")), h["title"]
        )

        con.execute(
            "insert or replace into chats values "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                cid, h["title"], h["subtitle"], h["repo"],
                start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes"),
                s["api_calls"], s["user_turns"], s["tool_calls"],
                h["ctx_final"], s["est_input"], s["est_output"],
                s["est_late"], s["late_calls"],
                s["est_split"], s["segments"],
                h["files_changed"], h["lines_added"], h["lines_removed"],
                first_q[:800], compacted,
                datetime.now(TZ).isoformat(timespec="seconds"),
            ),
        )
        con.execute("delete from chat_hours where chat_id = ?", (cid,))
        con.executemany(
            "insert into chat_hours values (?,?,?,?)",
            [(cid, hr, tok, calls) for hr, (tok, calls) in s["hours"].items()],
        )
        scanned += 1
        rows.append((s["est_input"], h["title"], s["api_calls"], h["ctx_final"], end))

    con.commit()
    con.close()

    print(f"扫了 {scanned} 个会话，跳过 {skipped} 个（没有对应聊天记录文件）")
    total = sum(r[0] for r in rows)
    print(f"最近 {days} 天估算计费输入合计 {total/1e6:.1f}M token")
    if top:
        print(f"\n最贵 {top} 个：")
        print(f"{'估算输入':>10}  {'调用':>5}  {'终值ctx':>8}  结束时间        标题")
        for inp, title, calls, ctx, end in sorted(rows, reverse=True)[:top]:
            print(
                f"{inp/1e6:>9.2f}M  {calls:>5}  {ctx/1000:>7.0f}k  "
                f"{end.strftime('%m-%d %H:%M')}  {title[:40]}"
            )


if __name__ == "__main__":
    main()
