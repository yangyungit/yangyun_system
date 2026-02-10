import os
import telebot
from scrapers.fetch_patreon import run_scraper_task, run_text_task

# 初始化 Bot
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

print("🤖 Telegram 机器人已启动...")

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "👋 我是 Moltbot。\n\n1. 发送 **链接** -> 我尝试去爬。\n2. 发送 **正文** -> 我直接分析并入库（推荐！）。")

# 处理所有文本消息
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    text = message.text.strip()
    user_id = message.from_user.id
    print(f"📩 收到消息: {text[:20]}...")

    # 1. 如果是链接
    if text.startswith("http"):
        bot.reply_to(message, "🕵️ 发现链接，正在尝试特工潜入... (如果失败请直接把正文发给我)")
        try:
            # 这是一个耗时操作，可能会让 Telegram 超时，但在 Fly.io 上通常没事
            reply = run_scraper_task(text)
            bot.reply_to(message, reply)
        except Exception as e:
            bot.reply_to(message, f"❌ 系统错误: {e}")

    # 2. 如果是长文本 (手动投喂)
    elif len(text) > 50:
        bot.reply_to(message, "🧠 收到长文本，跳过爬虫，直接进行 AI 分析与入库...")
        try:
            reply = run_text_task(text)
            bot.reply_to(message, reply)
        except Exception as e:
            bot.reply_to(message, f"❌ 处理失败: {e}")
            
    # 3. 其他短语
    else:
        bot.reply_to(message, "🤔 内容太短了，请发送链接或完整的文章正文。")

# 启动轮询
if __name__ == "__main__":
    bot.infinity_polling()