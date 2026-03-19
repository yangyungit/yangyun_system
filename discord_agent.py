import os
import re
import asyncio
import discord
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

import utils

# ── 配置 ─────────────────────────────────────────────────────────────────────
DISCORD_TOKEN    = os.environ.get("DISCORD_TOKEN", "")
ALLOWED_CHANNEL  = 1483360908385452102
STREAMLIT_URL    = "https://system-white-glitter-4681.fly.dev"
URL_PATTERN      = re.compile(r'https?://\S+')

REPLY_TEXT = (
    "✅ 收到！情报已拆解并入库。"
    f"👉 [点击前往中台大屏查看]({STREAMLIT_URL})"
)

PAYWALL_MARKER = "__PAYWALL__"

# 已知付费/会员墙域名，直接跳过抓取
PAYWALL_DOMAINS = {
    "patreon.com",
    "substack.com",
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "economist.com",
    "nytimes.com",
    "theathletic.com",
}

# ── URL 正文提取 ──────────────────────────────────────────────────────────────
def fetch_url_text(url: str) -> str:
    """用 requests + BeautifulSoup 从 URL 抓取纯文本正文。"""

    # 先检查已知付费墙域名，无需尝试抓取
    try:
        domain = urlparse(url).netloc.lower().lstrip("www.")
        if domain in PAYWALL_DOMAINS or any(d in domain for d in PAYWALL_DOMAINS):
            print(f"[discord_agent] 已知付费域名，跳过抓取: {url} ({domain})")
            return f"{PAYWALL_MARKER}:{domain} (已知付费域名)"
    except Exception:
        pass

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    # 付费/登录墙常见关键词
    PAYWALL_HINTS = ["sign in", "log in", "登录", "注册", "subscribe",
                     "please log", "members only", "sign up to", "create account",
                     "become a patron", "unlock", "premium"]

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # 移除脚本、样式、导航等噪音标签
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        # 优先取 <article> / <main>，否则取全文
        container = soup.find("article") or soup.find("main") or soup.body
        if container:
            text = container.get_text(separator="\n", strip=True)
        else:
            text = soup.get_text(separator="\n", strip=True)

        # 折叠连续空行
        lines = [ln for ln in text.splitlines() if ln.strip()]
        clean_text = "\n".join(lines)[:8000]

        # 检测登录墙：正文太短或充斥登录提示
        title_tag = soup.find("title")
        page_title = title_tag.get_text(strip=True) if title_tag else ""
        is_paywall = len(clean_text) < 300 or any(
            hint in clean_text.lower() for hint in PAYWALL_HINTS
        )
        if is_paywall:
            print(f"[discord_agent] 检测到登录墙/付费墙: {url}")
            return f"{PAYWALL_MARKER}:{page_title}"

        return clean_text
    except requests.HTTPError as e:
        # 4xx/5xx 状态码，视为无法访问，当付费墙处理
        print(f"[discord_agent] URL HTTP 错误 {url}: {e}")
        return f"{PAYWALL_MARKER}:HTTP {e.response.status_code if e.response else 'error'}"
    except Exception as e:
        print(f"[discord_agent] URL 抓取失败 {url}: {e}")
        return f"{PAYWALL_MARKER}:抓取失败"


# ── Discord Bot ───────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True          # 必须开启才能读取消息正文

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"[discord_agent] 已登录：{client.user}  (只监听频道 {ALLOWED_CHANNEL})")


@client.event
async def on_message(message: discord.Message):
    # 忽略机器人自身 & 非目标频道
    if message.author.bot:
        return
    if message.channel.id != ALLOWED_CHANNEL:
        return

    raw = message.content.strip()

    # 优先读取 .txt 附件内容（如从 Patreon/付费页面手动复制后粘贴为文件）
    attachment_text = ""
    for att in message.attachments:
        if att.filename.lower().endswith(".txt") and att.size < 100_000:
            try:
                att_bytes = await att.read()
                attachment_text = att_bytes.decode("utf-8", errors="ignore").strip()
                print(f"[discord_agent] 读取附件 {att.filename}，{len(attachment_text)} 字")
                break
            except Exception as e:
                print(f"[discord_agent] 附件读取失败: {e}")

    # 若有附件文本，直接以附件内容为主（忽略消息正文中的 URL）
    if attachment_text:
        text_to_analyze = attachment_text
        print(f"[discord_agent] 使用附件正文（{len(text_to_analyze)} 字）送分析")
    elif not raw:
        return
    else:
        # 判断消息中的 URL
        urls = URL_PATTERN.findall(raw)
        if urls and raw == urls[0]:          # 消息本身就是一条 URL
            fetched = await asyncio.get_event_loop().run_in_executor(
                None, fetch_url_text, urls[0]
            )
            if fetched.startswith(PAYWALL_MARKER):
                detail = fetched.split(":", 1)[1] if ":" in fetched else ""
                hint = (
                    f"⚠️ 该链接无法自动抓取正文（付费墙/登录墙）。\n"
                    f"📋 详情：**{detail}**\n\n"
                    f"👇 请把文章正文直接粘贴到频道，或以 `.txt` 附件上传，我来帮你解析入库！"
                )
                await message.channel.send(hint)
                return
            text_to_analyze = fetched
        elif urls:                           # 混合内容：URL + 文字
            fetched_parts = []
            has_paywall = False
            for u in urls:
                fetched = await asyncio.get_event_loop().run_in_executor(
                    None, fetch_url_text, u
                )
                if fetched.startswith(PAYWALL_MARKER):
                    has_paywall = True
                    detail = fetched.split(":", 1)[1] if ":" in fetched else ""
                    fetched_parts.append(f"【付费墙，无法抓取】{detail}")
                else:
                    fetched_parts.append(fetched)
            text_to_analyze = raw + "\n\n--- 抓取正文 ---\n\n" + "\n\n---\n\n".join(fetched_parts)
            if has_paywall:
                await message.channel.send(
                    "⚠️ 其中部分链接有登录墙，已跳过抓取，仅分析可获取内容。\n"
                    "如需完整分析，请将文章正文直接粘贴到频道或以 .txt 附件上传。"
                )
        else:
            text_to_analyze = raw            # 纯文本，直接送分析

    print(f"[discord_agent] 收到消息（{len(text_to_analyze)} 字），开始 auto_dispatch ...")

    # 调用核心逻辑（在线程池内运行，避免阻塞事件循环）
    new_items = await asyncio.get_event_loop().run_in_executor(
        None, utils.auto_dispatch, None, text_to_analyze, "Discord"
    )

    # 按分类写入对应的 Google Sheets
    if new_items:
        def save_items():
            macro_items = [it for it in new_items if it.get("category") == "MACRO"]
            radar_items = [it for it in new_items if it.get("category") != "MACRO"]

            if macro_items:
                current = utils.load_data("macro_stream")
                utils.save_data(macro_items + current, "macro_stream")

            if radar_items:
                current = utils.load_data("radar_stream")
                utils.save_data(radar_items + current, "radar_stream")

        await asyncio.get_event_loop().run_in_executor(None, save_items)
        print(f"[discord_agent] 已写入 {len(new_items)} 条情报到 Google Sheets。")
    else:
        print("[discord_agent] auto_dispatch 未返回有效数据，跳过入库。")

    await message.channel.send(REPLY_TEXT)
    print("[discord_agent] 已回复用户，入库完成。")


# ── 入口 ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("环境变量 DISCORD_TOKEN 未设置！")
    client.run(DISCORD_TOKEN)
