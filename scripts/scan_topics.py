#!/usr/bin/env python3
"""扫 99_Human_Zone 新归档的笔记，挑出可能能写成主站深读的，追加到待判断清单。

用法：
    python3 scan_topics.py [天数]     # 默认 2 天
    python3 scan_topics.py 30         # 回溯一个月，第一次跑用这个

只做粗筛和摘要，不判断值不值得写——判断要开一个会话，
读 obsidian_notes/99_Human_Zone/养云协作/深读栏目.md 里「选题池」一节的规则。

粗筛条件：顶层 md、够长、不是思维模型库、没被选题池或深读列表收录过。
"""
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ZONE = ROOT / "obsidian_notes" / "99_Human_Zone"
POOL = ZONE / "养云协作" / "深读栏目.md"
PUBLISHED = ZONE / "深读列表.md"
PENDING = ROOT / "memory" / "inbox" / "topics-pending.md"

MIN_BYTES = 3000  # 低于这个基本是 stub 或一句话备忘，撑不起一篇
SUMMARY_LEN = 500

# 编号开头的是芒格思维模型库，批量导入的，不是聊出来的
NUMBERED = re.compile(r"^\d{3} ")

# 内部运营文档，不对外发；靠文件名兜底，漏了由判断会话剔
INTERNAL = re.compile(
    r"环节$|清单$|列表$|模板$|^EP\d|^养云|^躺盈|prompt|todo|FAQ|架构$|页面|后台|前台",
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


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    found = collect(days)
    if not found:
        print("无新候选")
        return

    PENDING.parent.mkdir(parents=True, exist_ok=True)
    new_file = not PENDING.exists()
    with PENDING.open("a") as fh:
        if new_file:
            fh.write(
                "# 待判断选题\n\n"
                "判断规则见 `obsidian_notes/99_Human_Zone/养云协作/深读栏目.md` 里「选题池」一节。"
                "判断完把这个文件清空。\n"
            )
        fh.write(f"\n## 扫描 {time.strftime('%Y-%m-%d')}（近 {days} 天）\n")
        for yrs, mt, name, size, summary in found:
            day = time.strftime("%m-%d", time.localtime(mt))
            fh.write(
                f"\n### [[{name}]]\n{day} · {size // 1000}KB · 提到 {yrs} 处年份\n\n{summary}\n"
            )

    print(f"{PENDING}：新增 {len(found)} 条候选")
    for yrs, _, name, _, _ in found:
        print(f"  {yrs:>3} 年份  {name}")


if __name__ == "__main__":
    main()
