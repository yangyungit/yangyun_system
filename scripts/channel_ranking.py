"""拉取 YouTube / B站频道全部视频，按播放量排序输出 CSV。

用法：
    # 按名字查频道 id
    python3 channel_ranking.py find "半佛仙人"

    # 拉取并排序（可混多个平台多个频道）
    python3 channel_ranking.py fetch \
        yt:UCMUnInmOkrWN4gof9KlhNmQ \
        bili:37663924 \
        --out ../data/channel_ranking/2026-09-04.csv

YouTube 走 yt-dlp，不需要 API key，但代价是精度：频道页只写「3 年前 / 2 周前」，
所以老视频的 published 只有年份可信（date_precision=year），近期的能到周；
播放量也是页面上的取整值（1800 万）。B站走公开 wbi 接口，日期和播放量都精确。
"""

import argparse
import collections
import csv
import datetime as dt
import hashlib
import http.cookiejar
import json
import os
import random
import subprocess
import sys
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# B站 wbi 签名用的固定重排表
MIXIN_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
             33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
             26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
             20, 34, 44, 52]


class Bili:
    """B站公开接口客户端。用游客 buvid 指纹绕过 -352 风控，不需要登录 cookie。"""

    def __init__(self):
        self.refresh()

    def refresh(self):
        """重新领一套游客身份。被风控盯上后换一套通常能继续。"""
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self._get("https://www.bilibili.com/", raw=True)
        spi = self._get("https://api.bilibili.com/x/frontend/finger/spi")["data"]
        for name, key in (("buvid3", "b_3"), ("buvid4", "b_4")):
            self._set_cookie(name, spi[key])
        wbi = self._get("https://api.bilibili.com/x/web-interface/nav")["data"]["wbi_img"]
        img = wbi["img_url"].rsplit("/", 1)[1].split(".")[0]
        sub = wbi["sub_url"].rsplit("/", 1)[1].split(".")[0]
        self.mixin = "".join((img + sub)[i] for i in MIXIN_TAB)[:32]

    def _set_cookie(self, name, value):
        self.jar.set_cookie(http.cookiejar.Cookie(
            0, name, value, None, False, ".bilibili.com", True, True,
            "/", False, False, None, False, None, None, {}))

    def _get(self, url, referer="https://www.bilibili.com/", raw=False):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Referer": referer,
            "Origin": "https://space.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        body = self.opener.open(req, timeout=20).read()
        return body if raw else json.loads(body)

    def _signed(self, path, params, referer="https://www.bilibili.com/", retries=5):
        for attempt in range(retries + 1):
            # wts 必须每次重签：等待几十秒后旧时间戳本身就会被判 412
            p = dict(params, wts=int(time.time()))
            query = urllib.parse.urlencode(sorted(p.items()))
            p["w_rid"] = hashlib.md5((query + self.mixin).encode()).hexdigest()
            url = f"https://api.bilibili.com{path}?" + urllib.parse.urlencode(sorted(p.items()))
            try:
                return self._get(url, referer)
            except urllib.error.HTTPError as e:
                if e.code != 412 or attempt == retries:
                    raise
                wait = 15 * (attempt + 1) + random.uniform(0, 5)
                print(f"  被限流，等 {wait:.0f}s 换身份重试", file=sys.stderr)
                time.sleep(wait)
                self.refresh()

    def search_user(self, keyword):
        r = self._signed(
            "/x/web-interface/wbi/search/type",
            {"search_type": "bili_user", "keyword": keyword, "page": 1},
            referer="https://search.bilibili.com/upuser?keyword=" + urllib.parse.quote(keyword))
        if r["code"] != 0:
            raise RuntimeError(f"B站搜索失败 code={r['code']} {r.get('message')}")
        return [(u["mid"], u["uname"], u["fans"], u["videos"])
                for u in (r.get("data", {}).get("result") or [])]

    def videos(self, mid, page_size=50, pause=3.0):
        referer = f"https://space.bilibili.com/{mid}/video"
        rows, page, total = [], 1, None
        while True:
            r = self._signed("/x/space/wbi/arc/search", {
                "mid": mid, "pn": page, "ps": page_size,
                "order": "pubdate", "platform": "web", "web_location": 1550101,
                "dm_img_list": "[]",
                "dm_img_str": "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ",
                "dm_img_inter": '{"ds":[],"wh":[0,0,0],"of":[0,0,0]}',
            }, referer=referer)
            if r["code"] != 0:
                raise RuntimeError(f"B站取视频失败 mid={mid} code={r['code']} {r.get('message')}")
            batch = r["data"]["list"]["vlist"]
            if total is None:
                total = r["data"]["page"]["count"]
                name = batch[0]["author"] if batch else str(mid)
                print(f"  B站 {name} 共 {total} 个视频", file=sys.stderr)
            if not batch:
                break
            for v in batch:
                rows.append({
                    "platform": "bilibili",
                    "channel": "",  # 联合投稿时 author 是合作方，收完再统一按众数回填
                    "uploader": v["author"],
                    "title": v["title"],
                    "views": v["play"],
                    "published": dt.date.fromtimestamp(v["created"]).isoformat(),
                    "date_precision": "day",
                    "duration_sec": _parse_len(v["length"]),
                    "url": f"https://www.bilibili.com/video/{v['bvid']}",
                })
            print(f"  ...{len(rows)}/{total}", file=sys.stderr)
            if len(rows) >= total:
                break
            page += 1
            time.sleep(pause + random.uniform(0, 2))
        owner = collections.Counter(r["uploader"] for r in rows).most_common(1)
        for r in rows:
            r["channel"] = owner[0][0] if owner else str(mid)
        return rows


def _parse_len(text):
    """B站的 length 是 '12:34' 或 '1:02:03'。"""
    try:
        parts = [int(x) for x in text.split(":")]
    except (ValueError, AttributeError):
        return None
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return sec


def _yt_precision(date):
    if date is None:
        return ""
    today = dt.date.today()
    if (date.month, date.day) == (today.month, today.day):
        return "year"
    return "week"


def youtube_videos(channel_id):
    url = f"https://www.youtube.com/channel/{channel_id}/videos"
    out = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--ignore-errors", "-J",
         "--extractor-args", "youtubetab:approximate_date", url],
        capture_output=True, text=True)
    if not out.stdout.strip() or out.stdout.strip() == "null":
        raise RuntimeError(f"yt-dlp 拉不到 {channel_id}：{out.stderr.strip()[:300]}")
    data = json.loads(out.stdout)
    channel = data.get("channel") or data.get("title") or channel_id
    rows = []
    for e in data.get("entries") or []:
        if not e.get("id"):
            continue
        ts = e.get("timestamp")
        date = dt.date.fromtimestamp(ts) if ts else None
        rows.append({
            "platform": "youtube",
            "channel": channel,
            "uploader": channel,
            "title": e.get("title") or "",
            "views": e.get("view_count") or 0,
            "published": date.isoformat() if date else "",
            # 频道页只写「3 年前 / 2 周前」，yt-dlp 拿今天往回推：
            # 落在今天月日的说明只有年份可信，其余能到周
            "date_precision": _yt_precision(date),
            "duration_sec": e.get("duration"),
            "url": e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
        })
    print(f"  YouTube {channel} 共 {len(rows)} 个视频", file=sys.stderr)
    return rows


def cmd_find(args):
    bili = Bili()
    for kw in args.keyword:
        print(f"=== {kw} ===")
        print("  [B站]")
        for mid, name, fans, n in bili.search_user(kw):
            print(f"    bili:{mid}  {name}  粉丝 {fans:,}  视频 {n}")
        print("  [YouTube]")
        out = subprocess.run(["yt-dlp", "--flat-playlist", "-J", f"ytsearch5:{kw}"],
                             capture_output=True, text=True)
        seen = {}
        for e in (json.loads(out.stdout or "{}").get("entries") or []):
            if e.get("channel_id") and e["channel_id"] not in seen:
                seen[e["channel_id"]] = e.get("channel")
        for cid, name in seen.items():
            print(f"    yt:{cid}  {name}")


def cmd_fetch(args):
    out_dir = os.path.dirname(os.path.abspath(args.out))
    # B站一个大频道要翻十几页、被限流后重跑很贵，按频道缓存，中途挂了不用从头来
    cache_dir = os.path.join(out_dir, ".cache")
    os.makedirs(cache_dir, exist_ok=True)

    rows, bili = [], None
    for target in args.target:
        kind, _, ident = target.partition(":")
        cache = os.path.join(cache_dir, target.replace(":", "_") + ".json")
        if os.path.exists(cache) and not args.refresh:
            with open(cache, encoding="utf-8") as f:
                got = json.load(f)
            print(f"[{target}] 用缓存 {len(got)} 条", file=sys.stderr)
        else:
            print(f"[{target}]", file=sys.stderr)
            if kind == "yt":
                got = youtube_videos(ident)
            elif kind == "bili":
                bili = bili or Bili()
                got = bili.videos(int(ident))
            else:
                raise SystemExit(f"不认识的前缀 {kind}，只支持 yt: 和 bili:")
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(got, f, ensure_ascii=False)
        rows += got

    rows.sort(key=lambda r: r["views"], reverse=True)
    fields = ["platform", "channel", "uploader", "title", "views",
              "published", "date_precision", "duration_sec", "url"]
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"\n{len(rows)} 行 → {args.out}", file=sys.stderr)

    print(f"\n播放量 Top {args.top}：")
    print("| # | 平台 | 频道 | 播放量 | 发布 | 标题 |")
    print("|---|---|---|---|---|---|")
    for i, r in enumerate(rows[:args.top], 1):
        title = r["title"].replace("|", "\\|")[:52]
        date = r["published"][:4] + " 年" if r["date_precision"] == "year" else r["published"]
        print(f"| {i} | {r['platform']} | {r['channel']} | {r['views']:,} | {date} | {title} |")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("find", help="按名字查频道 id")
    f.add_argument("keyword", nargs="+")
    f.set_defaults(func=cmd_find)

    g = sub.add_parser("fetch", help="拉全部视频并按播放量排序")
    g.add_argument("target", nargs="+", help="yt:<channel_id> 或 bili:<mid>")
    g.add_argument("--out", default="channel_ranking.csv")
    g.add_argument("--top", type=int, default=30)
    g.add_argument("--refresh", action="store_true", help="忽略缓存重新拉")
    g.set_defaults(func=cmd_fetch)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
