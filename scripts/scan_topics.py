#!/usr/bin/env python3
"""扫 99_Human_Zone 新归档的笔记，挑出可能能写成主站深读的，追加到待判断清单。

用法：
    python3 scan_topics.py [天数]     # 默认 2 天
    python3 scan_topics.py 30         # 回溯一个月，第一次跑用这个

只做粗筛和摘要，不判断值不值得写——判断要开一个会话，
读 obsidian_notes/99_Human_Zone/深读选题池.md 里的规则。

扫到新候选会推一条 Discord（webhook 在根 .env 的 DISCORD_WEBHOOK_TOPICS），
不然它们只在 memory/inbox/topics-pending.md 里无声堆着。

粗筛条件：顶层 md、够长、不是思维模型库、没被选题池或深读列表收录过。
"""
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
ZONE = ROOT / "obsidian_notes" / "99_Human_Zone"
POOL = ZONE / "深读选题池.md"
PUBLISHED = ZONE / "深读列表.md"
PENDING = ROOT / "memory" / "inbox" / "topics-pending.md"

MIN_BYTES = 3000  # 低于这个基本是 stub 或一句话备忘，撑不起一篇
SUMMARY_LEN = 500
PUSH_SUMMARY_LEN = 220  # Discord 一条 2000 字上限，摘要按这个截；全文仍写进 pending

BACKLOG_ALERT = 15  # 待判断攒过这个数，就在选题池顶上挂一行，不然只在 inbox 里无声堆着
MARK_BEGIN = "<!-- backlog -->"
MARK_END = "<!-- /backlog -->"

# 编号开头的是芒格思维模型库，批量导入的，不是聊出来的
NUMBERED = re.compile(r"^\d{3} ")

# 内部运营文档，不对外发；靠文件名兜底，漏了由判断会话剔
INTERNAL = re.compile(
    r"环节$|清单$|列表$|模板$|选题池$|^EP\d|^养云|^躺盈|prompt|todo|FAQ|架构$|页面|后台|前台",
    re.I,
)

# 量化系统和记账工具的内部笔记，讲参数不讲生意。
# 只挡最明显的：好素材里这些词基本为零，误伤风险低。
SYSTEM_DOC = re.compile(
    r"参数|阈值|回测|字段|函数|脚本|表结构|返回值|接口|def |SELECT |\.py|因子值|列名",
    re.I,
)

# 有具体年份说明背后有公司和历史，是能撑起深读的料。只用来排序，不做门槛。
YEAR = re.compile(r"(?:19|20)\d{2}")

SECRET_RE = re.compile(
    r"密码|口令|密钥|账号密码"
    r"|pass(?:word|wd)|secret|token|credential"
    r"|api[_-]?key|access[_-]?key|private[_-]?key",
    re.I,
)


def already_seen():
    """选题池收录过、深读列表发过、待判断清单里已经排着的，都不再报。"""
    seen = set()
    for f in (POOL, PUBLISHED, PENDING):
        if f.exists():
            seen |= set(re.findall(r"\[\[(.*?)\]\]", f.read_text(errors="ignore")))
    return seen


def summarize(text):
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("![")]
    return " ".join(lines)[:SUMMARY_LEN]


def collect(days):
    cutoff = time.time() - days * 86400
    seen = already_seen()
    out = []

    for f in sorted(ZONE.glob("*.md"), key=lambda p: p.stat().st_mtime):
        name = f.stem
        if f in (POOL, PUBLISHED):
            continue
        if f.stat().st_mtime < cutoff or name in seen:
            continue
        if NUMBERED.match(name) or INTERNAL.search(name):
            continue
        if f.stat().st_size < MIN_BYTES:
            continue
        text = f.read_text(errors="ignore")
        head = text[:4000]
        if SECRET_RE.search(head) or len(SYSTEM_DOC.findall(head)) >= 2:
            continue
        out.append(
            (
                len(YEAR.findall(head)),
                f.stat().st_mtime,
                name,
                f.stat().st_size,
                summarize(text),
            )
        )

    out.sort(reverse=True)
    return out


def backlog():
    """待判断清单里攒了多少条、最早那批是哪天扫的。"""
    if not PENDING.exists():
        return 0, ""
    text = PENDING.read_text(errors="ignore")
    dates = re.findall(r"^## 扫描 (\d{4}-\d{2}-\d{2})", text, re.M)
    return len(re.findall(r"^### ", text, re.M)), dates[0] if dates else ""


def mark_pool():
    """堆过阈值就在选题池顶上挂提醒；判完清空 pending 后下次扫描自动摘掉。"""
    if not POOL.exists():
        return
    text = re.sub(
        f"{re.escape(MARK_BEGIN)}.*?{re.escape(MARK_END)}\n*",
        "",
        POOL.read_text(),
        flags=re.S,
    )
    count, since = backlog()
    if count >= BACKLOG_ALERT:
        text = text.replace(
            "## 选题池\n",
            f"## 选题池\n\n{MARK_BEGIN}\n"
            f"> 待判断攒了 {count} 条（最早 {since}），在 `memory/inbox/topics-pending.md`。"
            f"开个会话按下面四项过一遍。\n{MARK_END}\n",
            1,
        )
    POOL.write_text(text)


def read_env(key):
    """launchd 不加载 .env，脚本自己读根 .env。"""
    f = ROOT / ".env"
    if not f.exists():
        return None
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip() or None
    return None


def chunk_lines(lines, limit=1900):
    out, cur = [], ""
    for ln in lines:
        if len(ln) > limit:
            ln = ln[: limit - 1] + "…"
        if cur and len(cur) + 1 + len(ln) > limit:
            out.append(cur)
            cur = ln
        else:
            cur = f"{cur}\n{ln}" if cur else ln
    if cur:
        out.append(cur)
    return out


def notify(found, days):
    """推 Discord。推送失败不影响写 pending——那份是判断会话的输入，优先级更高。"""
    url = read_env("DISCORD_WEBHOOK_TOPICS")
    if not url:
        return "根 .env 里没有 DISCORD_WEBHOOK_TOPICS，跳过推送"

    count, since = backlog()
    lines = [f"**深读选题 · 新候选 {len(found)} 条**　近 {days} 天", ""]
    for yrs, mt, name, size, summary in found:
        day = time.strftime("%m-%d", time.localtime(mt))
        lines += [
            f"**{name}**　{day} · {size // 1000}KB · 年份 {yrs} 处",
            summary[:PUSH_SUMMARY_LEN] + ("…" if len(summary) > PUSH_SUMMARY_LEN else ""),
            "",
        ]
    lines.append(f"待判断共 {count} 条（最早 {since}），判断规则见选题池。")

    sent = 0
    for body in chunk_lines(lines):
        try:
            r = requests.post(url, json={"content": body}, timeout=20)
            if r.status_code not in (200, 204):
                return f"推送失败 HTTP {r.status_code}：{r.text[:200]}（已发 {sent} 条）"
            sent += 1
        except Exception as exc:
            return f"推送失败 {type(exc).__name__}：{exc}（已发 {sent} 条）"
    return f"已推送 {len(found)} 条候选到 Discord（分 {sent} 条消息）"


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    found = collect(days)
    if not found:
        print("无新候选")
        mark_pool()
        return

    PENDING.parent.mkdir(parents=True, exist_ok=True)
    new_file = not PENDING.exists()
    with PENDING.open("a") as fh:
        if new_file:
            fh.write(
                "# 待判断选题\n\n"
                "判断规则见 `obsidian_notes/99_Human_Zone/深读选题池.md`。"
                "判断完把这个文件清空。\n"
            )
        fh.write(f"\n## 扫描 {time.strftime('%Y-%m-%d')}（近 {days} 天）\n")
        for yrs, mt, name, size, summary in found:
            day = time.strftime("%m-%d", time.localtime(mt))
            fh.write(
                f"\n### [[{name}]]\n{day} · {size // 1000}KB · 提到 {yrs} 处年份\n\n{summary}\n"
            )

    mark_pool()

    print(f"{PENDING}：新增 {len(found)} 条候选")
    for yrs, _, name, _, _ in found:
        print(f"  {yrs:>3} 年份  {name}")
    print(notify(found, days))


if __name__ == "__main__":
    main()
