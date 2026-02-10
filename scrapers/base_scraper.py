import os
import datetime
from github import Github
import utils 

# 配置
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
REPO_NAME = "yangyungit/obsidian_notes" 

def dispatch_to_system(title, content, author="System", source_url=""):
    """
    统一分发接口：这是所有抓取逻辑入库的唯一大门。
    """
    print(f"🚀 接口层收到来自 [{author}] 的情报: {title}")
    
    try:
        # 1. 调用 AI 进行分析
        # 直接使用 utils 里的 AI 客户端和调度逻辑
        analysis = utils.auto_dispatch(utils.client, content)
        
        # 2. 准备结构化数据 (用于 Google Sheets)
        category = analysis.get('category', 'MACRO')
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
        db_row = {
            "date": date_str,
            "category": category,
            "bias": analysis.get('bias', '中性'),
            "summary": analysis.get('summary', '无摘要'),
            "logic_chain_display": analysis.get('logic_chain_display', '无'),
            "tags": analysis.get('tags', []),
            "url": source_url,
            "deep_analysis_md": analysis.get('deep_analysis_md', ''),
            "raw_text": content[:1000] # 只存前1000字防止表格爆炸
        }
        
        # 3. 写入 Google Sheets
        current_data = utils.load_data("radar_data")
        current_data.append(db_row)
        utils.save_data(current_data, "radar_data")
        
        # 4. 写入 GitHub (Obsidian)
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(REPO_NAME)
        folder = "01_Macro_Research" if category == "MACRO" else "02_Radar_Ticker"
        safe_title = title.replace(' ', '_').replace('/', '-').replace('|', '')[:20]
        file_name = f"{folder}/{date_str}_{safe_title}_{datetime.datetime.now().strftime('%H%M%S')}.md"
        
        # 组装完整的 Markdown 内容
        obsidian_md = f"# {title}\n\n> 来源: {author}\n> 链接: {source_url}\n\n{content}"
        
        repo.create_file(
            path=file_name,
            message=f"New report from {author}",
            content=obsidian_md,
            branch="main"
        )
        
        return f"✅ 已通过接口分发成功！分类: {category}"
        
    except Exception as e:
        print(f"❌ 接口分发失败: {e}")
        return f"❌ 处理失败: {e}"