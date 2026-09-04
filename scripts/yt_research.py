"""用 YouTube Data API v3 扒中文商业财经频道，看这个赛道谁在头部、哪类选题跑得动。

和同目录 channel_ranking.py 的分工：那个走 yt-dlp 不要 key，但发布时间只精确到
「3 年前 / 2 周前」、拿不到点赞评论；这个走官方 API，时间精确到秒、有点赞评论，
代价是要 key 和配额。找频道 ID 仍然用 channel_ranking.py find（0 配额）。

配额：playlistItems / videos / channels 都是 1 unit 一次（各带 50 条），
一个 500 期的频道全量拉完约 20 units。search 是 100 units，默认不用。

用法：
    # 0. key 放工作区根 .env 的 YOUTUBE_API_KEY=
    # 1. 生成配置模板，把频道和关键词填进去
    python3 yt_research.py init

    # 2. 看频道体量（订阅数 / 总播放 / 视频数），顺便验证配置里的频道能解析
    python3 yt_research.py channels

    # 3. 拉全部视频存 csv + parquet
    python3 yt_research.py fetch --since 2023-01-01

    # 4. 按关键词分组比播放量
    python3 yt_research.py stats

    # 5. 红海 / 空缺矩阵
    python3 yt_research.py gaps
"""

import argparse
import json
import os
import re
import sys
import time
import datetime as dt

import pandas as pd
import requests

API = "https://www.googleapis.com/youtube/v3"
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "yt_research_config.json")
DATA_DIR = os.path.join(HERE, "..", "data", "yt_research")
ROOT_ENV = os.path.join(HERE, "..", "..", ".env")


def load_key(cli_key=None):
    if cli_key:
        return cli_key
    if os.environ.get("YOUTUBE_API_KEY"):
        return os.environ["YOUTUBE_API_KEY"]
    try:
        with open(ROOT_ENV, encoding="utf-8") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k.strip() == "YOUTUBE_API_KEY":
                    return v.strip().strip("'\"")
    except FileNotFoundError:
        pass
    raise SystemExit(
        "没找到 API key。放工作区根 .env 里 YOUTUBE_API_KEY=xxx，或用 --key 传。")


class YT:
    """带配额计数的 API 客户端。每个方法自己报成本，跑完打总账。"""

    COST = {"channels": 1, "playlistItems": 1, "videos": 1, "search": 100}

    def __init__(self, key):
        self.key = key
        self.used = 0
        self.calls = 0
        self.sess = requests.Session()

    def get(self, endpoint, **params):
        params["key"] = self.key
        for attempt in range(4):
            r = self.sess.get(f"{API}/{endpoint}", params=params, timeout=30)
            if r.status_code == 200:
                self.used += self.COST[endpoint]
                self.calls += 1
                return r.json()
            body = r.json().get("error", {}) if r.headers.get(
                "content-type", "").startswith("application/json") else {}
            reason = (body.get("errors") or [{}])[0].get("reason", "")
            if reason in ("quotaExceeded", "dailyLimitExceeded"):
                raise SystemExit(
                    f"配额用光了（本次已花 {self.used} units）。太平洋时间 0 点重置。")
            if r.status_code in (403, 500, 503) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(
                f"{endpoint} 失败 {r.status_code} {reason}: {body.get('message', r.text[:200])}")

    def paged(self, endpoint, **params):
        token = None
        while True:
            if token:
                params["pageToken"] = token
            data = self.get(endpoint, **params)
            yield data
            token = data.get("nextPageToken")
            if not token:
                return


# ---------- 频道解析 ----------

def parse_target(s):
    """把用户写的各种形式归一成 ('id'|'handle'|'name', 值)。
    允许在后面写 `# 频道名` 当注释，一列 UC 开头的乱码没注释根本没法维护。"""
    s = s.split("#")[0].strip()
    m = re.search(r"youtube\.com/channel/(UC[\w-]{22})", s)
    if m:
        return "id", m.group(1)
    m = re.search(r"youtube\.com/@([\w.-]+)", s)
    if m:
        return "handle", m.group(1)
    if re.fullmatch(r"UC[\w-]{22}", s):
        return "id", s
    if s.startswith("@"):
        return "handle", s[1:]
    return "name", s


def resolve(yt, targets, allow_search=False):
    """频道标识 → 元信息。ID 能 50 个一批（1 unit），handle 只能一个一个查（各 1 unit），
    中文名要走 search（100 units），默认拒绝。"""
    ids, handles, names = [], [], []
    for t in targets:
        kind, val = parse_target(t)
        if not val:          # 整行都是 # 注释
            continue
        {"id": ids, "handle": handles, "name": names}[kind].append(val)

    if names and not allow_search:
        raise SystemExit(
            "配置里这些只有名字，转 ID 要走 search（100 units 一个）：\n  "
            + "\n  ".join(names)
            + "\n\n省配额的做法：先跑 `python3 channel_ranking.py find \"名字\"`（yt-dlp，0 配额）"
              "拿到 UC 开头的 ID 填回配置。\n真要用 API 查就加 --allow-search。")

    rows = []
    part = "snippet,statistics,contentDetails"

    for i in range(0, len(ids), 50):
        for c in yt.get("channels", part=part, id=",".join(ids[i:i + 50]),
                        maxResults=50).get("items", []):
            rows.append(_channel_row(c))

    for h in handles:
        items = yt.get("channels", part=part, forHandle=h).get("items", [])
        if not items:
            print(f"  查无此 handle: @{h}", file=sys.stderr)
            continue
        rows.append(_channel_row(items[0]))

    for n in names:
        res = yt.get("search", part="snippet", q=n, type="channel", maxResults=1)
        items = res.get("items", [])
        if not items:
            print(f"  搜不到频道: {n}", file=sys.stderr)
            continue
        cid = items[0]["snippet"]["channelId"]
        rows.append(_channel_row(yt.get("channels", part=part, id=cid)["items"][0]))

    return rows


def _channel_row(c):
    st = c.get("statistics", {})
    return {
        "channel_id": c["id"],
        "channel": c["snippet"]["title"],
        "handle": c["snippet"].get("customUrl", ""),
        "country": c["snippet"].get("country", ""),
        "created": c["snippet"]["publishedAt"][:10],
        "subscribers": int(st.get("subscriberCount", 0)),
        "hidden_subs": st.get("hiddenSubscriberCount", False),
        "total_views": int(st.get("viewCount", 0)),
        "video_count": int(st.get("videoCount", 0)),
        "uploads_playlist": c["contentDetails"]["relatedPlaylists"]["uploads"],
    }


# ---------- 拉视频 ----------

DUR = re.compile(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def dur_sec(s):
    m = DUR.fullmatch(s or "")
    if not m:
        return None
    d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + se


def fetch_channel(yt, ch, since=None):
    """先用 playlistItems 翻上传列表拿 videoId（1 unit / 50 条），
    再用 videos 批量取统计（1 unit / 50 条）。--since 早于该日期就停止翻页，
    因为上传列表是按发布时间倒序的。"""
    vid_ids, stopped = [], False
    for page in yt.paged("playlistItems", part="contentDetails",
                         playlistId=ch["uploads_playlist"], maxResults=50):
        for it in page.get("items", []):
            pub = it["contentDetails"].get("videoPublishedAt")
            if pub is None:          # 私有 / 已删除的占位条目
                continue
            if since and pub[:10] < since:
                stopped = True
                continue
            vid_ids.append(it["contentDetails"]["videoId"])
        if stopped:
            break
        print(f"  {ch['channel']} 已列 {len(vid_ids)} 条", file=sys.stderr)

    rows = []
    for i in range(0, len(vid_ids), 50):
        batch = yt.get("videos", part="snippet,statistics,contentDetails",
                       id=",".join(vid_ids[i:i + 50]), maxResults=50)
        for v in batch.get("items", []):
            st, sn = v.get("statistics", {}), v["snippet"]
            sec = dur_sec(v["contentDetails"]["duration"])
            rows.append({
                "channel_id": ch["channel_id"],
                "channel": ch["channel"],
                "video_id": v["id"],
                "title": sn["title"],
                "published": sn["publishedAt"],
                "views": int(st.get("viewCount", 0)),
                # 作者可以隐藏点赞、关闭评论，这时字段直接不返回，记 None 不记 0
                "likes": int(st["likeCount"]) if "likeCount" in st else None,
                "comments": int(st["commentCount"]) if "commentCount" in st else None,
                "duration_sec": sec,
                "is_short": bool(sec is not None and sec <= 60),
                "tags": "|".join(sn.get("tags") or []),
                "url": f"https://www.youtube.com/watch?v={v['id']}",
            })
    print(f"  {ch['channel']} 取到 {len(rows)} 条统计", file=sys.stderr)
    return rows


# ---------- 关键词分组 ----------

def load_config(path=CONFIG):
    if not os.path.exists(path):
        raise SystemExit(f"没有配置文件 {path}，先跑 `python3 yt_research.py init`")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def tag_groups(df, groups):
    """一条视频可以同时命中多组（讲英伟达财报 = 单公司 + 蹭新闻），
    所以返回的是「每命中一组一行」的长表，另加一个 group='未分类' 兜底。"""
    out = []
    low = df["title"].str.lower()
    for name, words in groups.items():
        hit = pd.Series(False, index=df.index)
        for w in words:
            hit |= low.str.contains(re.escape(w.lower()), regex=True, na=False)
        sub = df[hit].copy()
        sub["group"] = name
        sub["group_hits"] = [
            "|".join(w for w in words if w.lower() in t) for t in low[hit]]
        out.append(sub)
    tagged = pd.concat(out) if out else df.iloc[0:0].assign(group=None)
    rest = df[~df.index.isin(tagged.index)].copy()
    rest["group"] = "未分类"
    rest["group_hits"] = ""
    return pd.concat([tagged, rest], ignore_index=True)


def add_view_index(df):
    """同一个频道里，老视频攒的播放多、大频道基数高，直接比中位数会把
    「哪类选题跑得动」和「谁家粉丝多」混在一起。所以除以「同频道同一年」的
    播放中位数，得到 1.0 = 这个频道那一年的平均水平。当年样本 < 5 条就退回全频道中位数。"""
    df = df.copy()
    df["year"] = df["published"].str[:4]
    base_y = df.groupby(["channel_id", "year"])["views"].transform("median")
    n_y = df.groupby(["channel_id", "year"])["views"].transform("size")
    base_c = df.groupby("channel_id")["views"].transform("median")
    base = base_y.where(n_y >= 5, base_c)
    df["view_index"] = (df["views"] / base.replace(0, pd.NA)).astype(float)
    return df


# ---------- 子命令 ----------

TEMPLATE = {
    "_说明": "channels 填 UC 开头的 ID 或 @handle（都是 1 unit）；只填中文名要 search，100 units",
    "channels": [
        "@小Lin说",
        "UC000000000000000000000a"
    ],
    "groups": {
        "讲单家公司": ["苹果", "英伟达", "特斯拉", "台积电", "微软", "亚马逊", "谷歌",
                       "Meta", "奈飞", "Netflix", "AMD", "博通", "礼来", "波音",
                       "星巴克", "可口可乐", "伯克希尔", "巴菲特", "马斯克", "OpenAI"],
        "讲宏观": ["美联储", "降息", "加息", "通胀", "衰退", "非农", "国债", "美元",
                   "汇率", "债务", "GDP", "失业率", "关税", "日元", "黄金"],
        "讲行业": ["行业", "赛道", "产业", "板块", "半导体", "新能源", "医药", "军工",
                   "航空", "电商", "游戏", "餐饮", "奢侈品", "银行业", "保险"],
        "蹭新闻": ["财报", "暴跌", "暴涨", "崩盘", "突发", "刚刚", "最新", "宣布",
                   "上市", "IPO", "裁员", "收购", "退市", "诉讼", "发布会", "爆雷"],
        "讲方法": ["如何", "怎么", "教你", "指南", "入门", "估值", "定投", "仓位",
                   "组合", "ETF", "复利", "止损"],
        "讲人物故事": ["创始人", "他是", "为什么他", "传奇", "首富", "身价", "跌落",
                       "崛起", "翻车", "破产"]
    }
}


def cmd_init(args):
    if os.path.exists(CONFIG) and not args.force:
        raise SystemExit(f"{CONFIG} 已存在，要覆盖加 --force")
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(TEMPLATE, f, ensure_ascii=False, indent=2)
    print(f"写好模板 → {CONFIG}\n把 channels 换成你自己的频道，groups 按需增删关键词。")


def cmd_channels(args):
    cfg = load_config(args.config)
    yt = YT(load_key(args.key))
    rows = resolve(yt, cfg["channels"], args.allow_search)
    rows.sort(key=lambda r: -r["subscribers"])

    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "channels.csv")
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")

    print("\n| # | 频道 | 订阅 | 总播放 | 视频数 | 单片均播 | 开号 | 地区 |")
    print("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        avg = r["total_views"] // r["video_count"] if r["video_count"] else 0
        subs = "隐藏" if r["hidden_subs"] else f"{r['subscribers']:,}"
        print(f"| {i} | {r['channel']} | {subs} | {r['total_views']:,} | "
              f"{r['video_count']:,} | {avg:,} | {r['created'][:7]} | {r['country']} |")
    print(f"\n{len(rows)} 个频道 → {path}", file=sys.stderr)
    print(f"本次 {yt.calls} 次请求，花 {yt.used} units", file=sys.stderr)


def cmd_peek(args):
    """每个频道 1 unit 看几条最近标题。决定要不要把某个号纳进样本时用，
    比盯着订阅数猜内容靠谱。"""
    cfg = load_config(args.config)
    yt = YT(load_key(args.key))
    targets = args.channel or cfg["channels"]
    for ch in resolve(yt, targets, args.allow_search):
        page = yt.get("playlistItems", part="snippet",
                      playlistId=ch["uploads_playlist"], maxResults=args.n)
        print(f"\n### {ch['channel']}  订阅 {ch['subscribers']:,} / "
              f"视频 {ch['video_count']:,}  {ch['channel_id']}")
        for it in page.get("items", []):
            sn = it["snippet"]
            print(f"  {sn['publishedAt'][:10]}  {sn['title'][:70]}")
    print(f"\n本次 {yt.calls} 次请求，花 {yt.used} units", file=sys.stderr)


def cmd_fetch(args):
    cfg = load_config(args.config)
    yt = YT(load_key(args.key))
    os.makedirs(DATA_DIR, exist_ok=True)

    chans = resolve(yt, cfg["channels"], args.allow_search)
    est = sum(-(-c["video_count"] // 50) * 2 for c in chans)
    print(f"{len(chans)} 个频道，共 {sum(c['video_count'] for c in chans):,} 个视频，"
          f"预计花 ~{est} units（--since 会更少）", file=sys.stderr)

    cache_dir = os.path.join(DATA_DIR, ".cache")
    os.makedirs(cache_dir, exist_ok=True)
    rows = []
    for c in chans:
        cache = os.path.join(cache_dir, f"{c['channel_id']}_{args.since or 'all'}.json")
        if os.path.exists(cache) and not args.refresh:
            with open(cache, encoding="utf-8") as f:
                got = json.load(f)
            print(f"[{c['channel']}] 用缓存 {len(got)} 条", file=sys.stderr)
        else:
            print(f"[{c['channel']}]", file=sys.stderr)
            got = fetch_channel(yt, c, args.since)
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(got, f, ensure_ascii=False)
        rows += got

    df = pd.DataFrame(rows).sort_values("views", ascending=False)
    stem = os.path.join(DATA_DIR, "videos")
    df.to_csv(stem + ".csv", index=False, encoding="utf-8-sig")
    df.to_parquet(stem + ".parquet", index=False)
    print(f"\n{len(df)} 行 → {stem}.csv / .parquet", file=sys.stderr)
    print(f"本次 {yt.calls} 次请求，花 {yt.used} units", file=sys.stderr)

    print(f"\n播放量 Top {args.top}：")
    print("| # | 频道 | 播放量 | 点赞 | 评论 | 发布 | 时长 | 标题 |")
    print("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(df.head(args.top).itertuples(), 1):
        mins = f"{r.duration_sec // 60}分" if r.duration_sec else "-"
        print(f"| {i} | {r.channel} | {r.views:,} | {_n(r.likes)} | {_n(r.comments)} | "
              f"{r.published[:10]} | {mins} | {r.title.replace('|', '/')[:48]} |")


def _n(x):
    return "-" if pd.isna(x) else f"{int(x):,}"


def _load_videos(args):
    path = os.path.join(DATA_DIR, "videos.parquet")
    if not os.path.exists(path):
        raise SystemExit(f"没有 {path}，先跑 fetch")
    df = pd.read_parquet(path)
    if not args.include_shorts:
        df = df[~df["is_short"]]
    if args.since:
        df = df[df["published"].str[:10] >= args.since]
    # 刚发的视频播放量还没长起来，留在样本里会把最近热门的选题一律算成扑街
    if args.min_age_days:
        cutoff = (dt.date.today() - dt.timedelta(days=args.min_age_days)).isoformat()
        n_before = len(df)
        df = df[df["published"].str[:10] <= cutoff]
        if n_before - len(df):
            print(f"（剔除 {n_before - len(df)} 条发布不足 {args.min_age_days} 天的新片）",
                  file=sys.stderr)
    if df.empty:
        raise SystemExit("过滤完一条不剩，放宽 --since 或加 --include-shorts")
    return add_view_index(df)


def cmd_stats(args):
    cfg = load_config(args.config)
    df = _load_videos(args)
    t = tag_groups(df, cfg["groups"])

    g = t.groupby("group")
    out = pd.DataFrame({
        "视频数": g.size(),
        "频道数": g["channel_id"].nunique(),
        "播放中位数": g["views"].median().round().astype(int),
        "播放P75": g["views"].quantile(.75).round().astype(int),
        "最高": g["views"].max(),
        "相对指数中位": g["view_index"].median().round(2),
        "互动率": (g["likes"].median() / g["views"].median()).round(4),
    }).sort_values("相对指数中位", ascending=False)

    print(f"\n样本：{len(df)} 条视频 / {df['channel_id'].nunique()} 个频道"
          f"（{df['published'].min()[:10]} ~ {df['published'].max()[:10]}"
          f"{'，已剔除 60 秒内的 Shorts' if not args.include_shorts else ''}）")
    print("\n相对指数 = 该视频播放 ÷ 同频道同年播放中位数。1.0 就是这个频道的平常水平，"
          "\n1.5 表示这类选题比该频道自己的平均线高 50%。跨频道比就看它，不要看绝对中位数。\n")
    print("| 组 | 视频数 | 频道数 | 播放中位数 | P75 | 最高 | 相对指数中位 | 点赞/播放 |")
    print("|---|---|---|---|---|---|---|---|")
    for name, r in out.iterrows():
        print(f"| {name} | {r['视频数']} | {r['频道数']} | {r['播放中位数']:,} | "
              f"{r['播放P75']:,} | {r['最高']:,} | {r['相对指数中位']} | {r['互动率']:.2%} |")

    if args.by_channel:
        print("\n分频道看（相对指数中位，能看出是不是只有某一家吃得下某类题）：")
        pt = t.pivot_table(index="group", columns="channel",
                           values="view_index", aggfunc="median").round(2)
        print(pt.to_markdown())

    path = os.path.join(DATA_DIR, "stats_by_group.csv")
    out.to_csv(path, encoding="utf-8-sig")
    t.to_csv(os.path.join(DATA_DIR, "videos_tagged.csv"),
             index=False, encoding="utf-8-sig")
    print(f"\n→ {path}", file=sys.stderr)


def cmd_gaps(args):
    cfg = load_config(args.config)
    df = _load_videos(args)
    low = df["title"].str.lower()

    rows = []
    for group, words in cfg["groups"].items():
        for w in words:
            hit = low.str.contains(re.escape(w.lower()), na=False)
            sub = df[hit]
            rows.append({
                "组": group,
                "关键词": w,
                "期数": len(sub),
                "做过的频道": sub["channel_id"].nunique(),
                "播放中位数": int(sub["views"].median()) if len(sub) else 0,
                "相对指数中位": round(sub["view_index"].median(), 2) if len(sub) else None,
                "最近一期": sub["published"].max()[:10] if len(sub) else "",
            })
    kw = pd.DataFrame(rows)

    hot = kw[kw["期数"] >= args.min_n]
    cut_n = hot["期数"].median() if len(hot) else 0
    cut_v = hot["相对指数中位"].median() if len(hot) else 0

    print(f"\n供给 = 这批频道做过多少期；回报 = 相对指数中位（1.0 = 频道平常水平）。"
          f"\n分界线取样本内中位数：期数 {cut_n:.0f} 期、指数 {cut_v:.2f}\n")

    for label, cond in [
        ("验证过的富矿（做的人多，还是能爆）",
         (hot["期数"] >= cut_n) & (hot["相对指数中位"] >= cut_v)),
        ("空缺（几乎没人做，做了的都跑赢）",
         (hot["期数"] < cut_n) & (hot["相对指数中位"] >= cut_v)),
        ("红海（一堆人在做，播放却压不过平常水平）",
         (hot["期数"] >= cut_n) & (hot["相对指数中位"] < cut_v)),
        ("冷门（没人做，做了也没人看）",
         (hot["期数"] < cut_n) & (hot["相对指数中位"] < cut_v)),
    ]:
        block = hot[cond].sort_values("相对指数中位", ascending=False)
        print(f"\n### {label}  ({len(block)})")
        if block.empty:
            print("（空）")
            continue
        print("| 关键词 | 组 | 期数 | 频道数 | 播放中位数 | 相对指数 | 最近一期 |")
        print("|---|---|---|---|---|---|---|")
        for r in block.head(args.top).itertuples():
            print(f"| {r.关键词} | {r.组} | {r.期数} | {r.做过的频道} | "
                  f"{r.播放中位数:,} | {r.相对指数中位} | {r.最近一期} |")

    cold = kw[kw["期数"] < args.min_n].sort_values("期数")
    if len(cold):
        print(f"\n### 样本不足（< {args.min_n} 期，判断不了好坏，只能说没人碰）")
        print("、".join(f"{r.关键词}({r.期数})" for r in cold.itertuples()))
        print("\nYouTube Data API 不提供搜索量，所以「没人碰」是否等于「没需求」，")
        print("这份数据答不了。要看需求侧得去 Google Trends 手工查这几个词。")

    path = os.path.join(DATA_DIR, "keyword_matrix.csv")
    kw.sort_values("期数", ascending=False).to_csv(
        path, index=False, encoding="utf-8-sig")
    print(f"\n→ {path}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=CONFIG)
    ap.add_argument("--key", help="不传就读环境变量 / 工作区根 .env 的 YOUTUBE_API_KEY")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="生成配置模板")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("channels", help="频道体量榜")
    p.add_argument("--allow-search", action="store_true", help="允许用 search 解析中文名，100 units 一个")
    p.set_defaults(func=cmd_channels)

    p = sub.add_parser("peek", help="看几条最近标题，验明这个号是不是同一赛道")
    p.add_argument("channel", nargs="*", help="不传就用配置里全部频道")
    p.add_argument("-n", type=int, default=5)
    p.add_argument("--allow-search", action="store_true")
    p.set_defaults(func=cmd_peek)

    p = sub.add_parser("fetch", help="拉视频存 csv + parquet")
    p.add_argument("--since", help="只要这天之后发的，如 2023-01-01")
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--refresh", action="store_true", help="忽略缓存重新拉")
    p.add_argument("--allow-search", action="store_true")
    p.set_defaults(func=cmd_fetch)

    for name, fn, helptext in [("stats", cmd_stats, "按关键词分组比播放量"),
                               ("gaps", cmd_gaps, "红海 / 空缺矩阵")]:
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--since")
        p.add_argument("--include-shorts", action="store_true",
                       help="默认剔除 60 秒内的 Shorts，它们会把中位数带偏")
        p.add_argument("--min-age-days", type=int, default=30,
                       help="剔除发布不足这么多天的新片，它们播放量还没长完（默认 30）")
        if name == "stats":
            p.add_argument("--by-channel", action="store_true")
        else:
            p.add_argument("--min-n", type=int, default=3, help="少于这个期数的关键词单独列，不参与四象限")
            p.add_argument("--top", type=int, default=15)
        p.set_defaults(func=fn)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
