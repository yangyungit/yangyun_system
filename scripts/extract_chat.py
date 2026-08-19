#!/usr/bin/env python3
"""从 Cursor 聊天记录里抽出主理人自己说的话，过滤掉含密钥的条目，供每周复盘用。

用法：
    python3 extract_chat.py [天数]        # 默认 7 天
    python3 extract_chat.py 120           # 全量回溯

产出 memory/inbox/raw-<日期>.txt。这一步只做提取和过滤，不做提炼——
提炼要开一个会话读 raw 文件，提示词见 memory/inbox/PROMPT.md。
"""
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

TRANSCRIPTS = Path.home() / (
    ".cursor/projects/Users-zhanghao-yangyun-Code-Projects/agent-transcripts"
)
INBOX = Path(__file__).resolve().parents[2] / "memory" / "inbox"

QUERY_RE = re.compile(r"<user_query>(.*?)</user_query>", re.S)
TS_RE = re.compile(r"<timestamp>(.*?)</timestamp>")

# 命中即整条丢弃。宁可误伤，也不能把密钥抄进 memory 仓。
SECRET_RE = re.compile(
    r"密码|口令|密钥"
    r"|pass(?:word|wd)|secret|token|credential"
    r"|api[_-]?key|access[_-]?key|private[_-]?key"
    r"|sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{16,}|AKIA[A-Z0-9]{12,}"
    r"|[A-Za-z0-9+/]{32,}={0,2}",  # 长随机串
    re.I,
)

# Cursor 自动插入的占位消息，不是主理人说的话
NOISE = ("Briefly inform the user about the task result",)

MAX_LEN = 1200  # 粘贴的长素材截断，避免 raw 文件被口播稿撑爆


def collect(days):
    cutoff = time.time() - days * 86400
    sessions, dropped = [], 0

    for f in TRANSCRIPTS.rglob("*.jsonl"):
        if f.stat().st_mtime < cutoff:
            continue
        queries, first_ts = [], None
        for line in f.read_text(errors="ignore").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("role") != "user":
                continue
            for block in rec.get("message", {}).get("content", []):
                if block.get("type") != "text":
                    continue
                text = block.get("text", "")
                if first_ts is None:
                    m = TS_RE.search(text)
                    if m:
                        first_ts = m.group(1)
                for q in QUERY_RE.findall(text):
                    q = q.strip()
                    if len(q) < 8 or any(n in q for n in NOISE):
                        continue
                    if SECRET_RE.search(q):
                        dropped += 1
                        continue
                    if len(q) > MAX_LEN:
                        q = q[:MAX_LEN] + "\n[……已截断]"
                    queries.append(q)
        if queries:
            sessions.append((f.stat().st_mtime, f.stem, first_ts, queries))

    sessions.sort()
    return sessions, dropped


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    sessions, dropped = collect(days)
    total = sum(len(s[3]) for s in sessions)

    INBOX.mkdir(parents=True, exist_ok=True)
    out = INBOX / f"raw-{date.today()}.txt"

    with out.open("w") as fh:
        fh.write(
            f"# 近 {days} 天：{len(sessions)} 个会话，{total} 条提问，"
            f"因命中密钥特征丢弃 {dropped} 条\n"
        )
        for _, sid, ts, queries in sessions:
            fh.write(f"\n{'=' * 70}\n## session {sid[:8]}  {ts or ''}\n")
            for q in queries:
                fh.write(f"\n{q}\n")

    print(f"{out}：{len(sessions)} 个会话 / {total} 条提问 / 丢弃 {dropped} 条")
    if dropped:
        print("丢弃的是命中密码、token、长随机串特征的整条提问，不会进入 raw 文件。")


if __name__ == "__main__":
    main()
