"""从 channel_ranking 的 CSV 里挑出财经/商业/金融垂类，按播放量出榜。

半佛的标题起得抽象（「弹力痛快，靓仔喜爱」讲的是行业），关键词过滤召回太低，
所以垂类判断是人工标注的，存在 finance_tags.txt 里，一行一个标题。
数据更新后只需要给新增的标题补标注。

小Lin 全频道都是财经，不用标注。半佛的 YouTube 号是死号（同内容 B站 174 万、
YouTube 4400），整个忽略，只看 B站。

用法：
    python3 finance_top.py ../data/channel_ranking/2026-09-04_all.csv --top 100
"""

import argparse
import csv
import os
import re

TAGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "finance_tags.txt")


def norm(title):
    """去掉【半佛】这类前缀和 YouTube 标题里的频道后缀，用来跨平台匹配同一内容。"""
    t = re.sub(r"[【\[].{0,6}[】\]]", "", title)
    t = t.split("|")[0]
    return re.sub(r"[\s\W_]+", "", t).lower()


def load_tags():
    with open(TAGS_FILE, encoding="utf-8") as f:
        return {norm(line) for line in f if line.strip() and not line.startswith("#")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--out")
    args = ap.parse_args()

    tags = load_tags()
    with open(args.csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    picked = {}
    for r in rows:
        is_banfo = "半佛" in r["channel"] or "Banfox" in r["channel"]
        is_lin = "Lin" in r["channel"]
        if is_banfo and r["platform"] != "bilibili":
            continue  # 半佛的 YouTube 是死号
        if is_banfo and norm(r["title"]) not in tags:
            continue
        if not (is_banfo or is_lin):
            continue

        key = norm(r["title"])
        r = dict(r, views=int(r["views"]))
        prev = picked.get(key)
        if prev is None:
            r["also"] = ""
            picked[key] = r
        elif r["views"] > prev["views"]:
            # 同一内容两平台都发了，留播放量高的，另一边记进 also 列
            r["also"] = f"{prev['platform']} {prev['views']:,}"
            picked[key] = r
        else:
            prev["also"] = f"{r['platform']} {r['views']:,}"

    out_rows = sorted(picked.values(), key=lambda r: -r["views"])[:args.top]

    if args.out:
        fields = ["platform", "channel", "title", "views", "published",
                  "date_precision", "duration_sec", "also", "url"]
        with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(out_rows)
        print(f"{len(out_rows)} 行 → {args.out}")

    print(f"\n| # | 频道 | 播放量 | 发布 | 时长 | 标题 | 另一平台 |")
    print("|---|---|---|---|---|---|---|")
    for i, r in enumerate(out_rows, 1):
        d = r["published"][:4] + " 年" if r["date_precision"] == "year" else r["published"]
        m = f"{int(r['duration_sec']) // 60}分" if r["duration_sec"] else "-"
        ch = "半佛" if "半佛" in r["channel"] or "Banfox" in r["channel"] else "小Lin"
        t = re.sub(r"^【半佛】", "", r["title"]).replace("|", "/")[:40]
        print(f"| {i} | {ch} | {r['views']:,} | {d} | {m} | {t} | {r['also']} |")


if __name__ == "__main__":
    main()
