import telebot
import os
import logging
import utils
from datetime import datetime

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 初始化 Bot
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    # 本地防报错占位符
    BOT_TOKEN = "7000000000:AAHQ..." 

bot = telebot.TeleBot(BOT_TOKEN)

# --- 核心处理逻辑 ---
def process_content(text):
    # 1. AI 分析 (现在返回的是一个列表 List)
    items = utils.auto_dispatch(None, text)
    
    if not items: 
        return False, "❌ AI 分析返回为空"
    
    # 如果 AI 出错返回了错误字典，把它包进列表里兼容处理
    if isinstance(items, dict):
        items = [items]

    results_report = []
    
    # 2. 循环处理每一条情报 (因为可能被拆成了多条)
    for item in items:
        try:
            category = item.get('category', 'MACRO')
            title = item.get('title', '无标题')
            
            # 补全元数据
            item['date'] = datetime.now().strftime("%Y-%m-%d")
            item['url'] = "Telegram Bot"
            
            # 3. 分流写入
            target_sheet = "radar_data" # 默认
            if category == "MACRO":
                target_sheet = "macro_stream"
            elif category == "RADAR":
                target_sheet = "radar_data"
            
            logger.info(f"🚀 写入表: {target_sheet} | 标题: {title}")
            
            # 4. 执行写入
            current_data = utils.load_data(target_sheet)
            current_data.insert(0, item)
            utils.save_data(current_data, target_sheet)
            
            results_report.append(f"✅ **{category}** (_{title}_) -> `{target_sheet}`")
            
        except Exception as e:
            logger.error(f"写入单条失败: {e}")
            results_report.append(f"❌ 写入失败: {e}")

    return True, "\n".join(results_report)

# --- 消息处理器 ---
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    text = message.text
    if len(text) < 5: return # 太短不回

    msg = bot.reply_to(message, "🧠 正在进行深度拆解与分发...")
    
    try:
        success, reply_text = process_content(text)
        bot.edit_message_text(reply_text, chat_id=msg.chat.id, message_id=msg.message_id, parse_mode="Markdown")
    except Exception as e:
        bot.edit_message_text(f"🔥 系统严重错误: {e}", chat_id=msg.chat.id, message_id=msg.message_id)

if __name__ == "__main__":
    logger.info("🛡️ Moltbot 哨兵模式已启动！")
    bot.infinity_polling()