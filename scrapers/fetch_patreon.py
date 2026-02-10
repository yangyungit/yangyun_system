import os
import datetime
import json
from openai import OpenAI
from github import Github
import utils 

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
REPO_NAME = "yangyungit/obsidian_notes" 

client = OpenAI(
    api_key=os.environ.get("GOOGLE_API_KEY"), 
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)

# 👈 必须保留这个函数，防止 monitor.py 报错导致服务器重启
def get_cookie_path():
    return "cookies.txt"

def run_scraper_task(url, title=None):
    # 既然自动抓取 403，这个函数直接引导用户手动投喂
    return "❌ 自动抓取被 Patreon 拦截 (403)。请直接将文章正文粘贴发给我，我会立即处理入库！"

def run_text_task(text):
    """手动投喂模式的核心"""
    try:
        # 1. AI 分析
        analysis = utils.auto_dispatch(client, text)
        
        # 2. 准备数据
        category = analysis.get('category', 'MACRO')
        title = text[:15].replace("\n", " ") + "..."
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
        # 3. 入库 Google Sheets (调用 utils 里的新函数)
        current_data = utils.load_data("radar_data")
        new_row = {
            "date": date_str,
            "category": category,
            "bias": analysis.get('bias', '中性'),
            "summary": analysis.get('summary', '无摘要'),
            "logic_chain_display": analysis.get('logic_chain_display', '无'),
            "tags": analysis.get('tags', []),
            "url": "手动投喂",
            "deep_analysis_md": analysis.get('deep_analysis_md', ''),
            "raw_text": text[:500]
        }
        current_data.append(new_row)
        utils.save_data(current_data, "radar_data")
        
        # 4. 入库 GitHub (Obsidian)
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(REPO_NAME)
        folder = "01_Macro_Research" if category == "MACRO" else "02_Radar_Ticker"
        file_name = f"{folder}/{date_str}_{datetime.datetime.now().strftime('%H%M%S')}.md"
        repo.create_file(path=file_name, message="Add manual note", content=text, branch="main")
        
        return f"✅ **{category}** 研报已同步至中台和 Obsidian！"
    except Exception as e:
        return f"❌ 处理失败: {e}"