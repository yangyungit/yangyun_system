import streamlit as st
import gspread
import pandas as pd
import json
import os
import base64
import toml
import google.generativeai as genai
from datetime import datetime
import urllib.parse
from github import Github

# --- 1. 基础配置 ---
def ensure_secrets_file():
    secret_path = ".streamlit/secrets.toml"
    encoded = os.environ.get("STREAMLIT_SECRETS_B64")
    if encoded and not os.path.exists(secret_path):
        try:
            decoded = base64.b64decode(encoded.strip()).decode()
            os.makedirs(".streamlit", exist_ok=True)
            with open(secret_path, "w") as f: f.write(decoded)
        except: pass

ensure_secrets_file()

def get_config(key_name):
    # 优先从环境变量取 (Fly.io secrets)，其次从本地文件取
    val = os.environ.get(key_name)
    if val: return val
    if os.path.exists(".streamlit/secrets.toml"):
        try:
            with open(".streamlit/secrets.toml", "r") as f:
                return toml.load(f).get(key_name)
        except: pass
    return None

def inject_custom_css():
    st.markdown("""
    <style>
        div[data-testid="stMarkdownContainer"] a {
            color: inherit !important;
            text-decoration: none !important;
            border-bottom: 1px dashed #666;
            transition: all 0.2s;
        }
        div[data-testid="stMarkdownContainer"] a:hover {
            color: #ffffff !important;
            text-decoration: underline !important;
            border-bottom: none;
        }
    </style>
    """, unsafe_allow_html=True)

# --- 2. Google Sheets ---
def get_gsheet_client():
    secret_file = ".streamlit/secrets.toml"
    try:
        scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        with open(secret_file, "r") as f:
            data = toml.load(f)
        creds = data.get("gcp_service_account") or data
        return gspread.service_account_from_dict(creds, scopes=scopes)
    except: return None

def load_data(sheet_name):
    try:
        gc = get_gsheet_client()
        sh = gc.open("yangyun_system_db")
        return sh.worksheet(sheet_name).get_all_records()
    except: return []

def save_data(data, sheet_name):
    try:
        gc = get_gsheet_client()
        sh = gc.open("yangyun_system_db")
        worksheet = sh.worksheet(sheet_name)
        worksheet.clear()
        if not data: return
        df = pd.DataFrame(data).fillna("")
        for col in df.columns:
            df[col] = df[col].apply(lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, (list, dict)) else x)
        worksheet.update([df.columns.values.tolist()] + df.values.tolist())
        return True
    except Exception as e:
        raise Exception(f"GS写入失败: {e}")

# --- 3. GitHub 归档 (增强版) ---
def push_to_github(filename, content, folder):
    token = get_config("GITHUB_TOKEN")
    if not token: 
        print(f"⚠️ 缺少 GITHUB_TOKEN，无法推送 {filename}")
        return None 
        
    try:
        g = Github(token)
        repo = g.get_user().get_repo("obsidian_notes") # 你的仓库名
        path = f"{folder}/{filename}"
        
        # PyGithub 会自动处理父文件夹不存在的情况
        try:
            contents = repo.get_contents(path)
            repo.update_file(path, f"Update {filename}", content, contents.sha)
            print(f"✅ 更新文件成功: {path}")
        except:
            repo.create_file(path, f"Create {filename}", content)
            print(f"✅ 创建文件成功: {path}")
            
        return f"https://github.com/yangyungit/obsidian_notes/blob/main/{path}"
    except Exception as e:
        print(f"❌ GitHub 归档失败 [{path}]: {e}")
        return None

# --- 4. AI 分析与分发 (核心修改：20字结论) ---
def auto_dispatch(client, raw_text):
    api_key = get_config("GOOGLE_API_KEY")
    if not api_key: return []

    genai.configure(api_key=api_key)
    
    # 👇 Prompt 修改：Title 必须是结论，Logic Chain 必须清晰
    prompt = f"""
    你是一个顶级宏观对冲基金的情报官。请分析以下文本。
    
    【文本】：{raw_text[:6000]}
    
    【任务】：
    1. 识别主旨，拆分独立的宏观/雷达情报。
    2. 提取原文时间。
    3. **四维拆解**：事实、观点、逻辑、假设。
    
    【输出 JSON 列表】：
    [
      {{
        "category": "MACRO" 或 "RADAR",
        "title": "极简结论 (必须在20字以内，例如: 美联储鹰派言论将压制科技股估值)",
        "summary": "完整的摘要内容 (保留供侦查室使用，但不在列表展示)",
        "bias": "Bullish/Bearish/Neutral",
        "tags": ["标签"],
        "logic_chain_display": "逻辑链 (A -> B -> C，简练有力)",
        "publication_date": "原文时间", 
        "url": "原文链接",
        "deep_analysis_md": "请按 Markdown 格式输出：\\n\\n### 1. 事实 (Facts)\\n...\\n\\n### 2. 观点 (Opinions)\\n...\\n\\n### 3. 逻辑 (Logic)\\n...\\n\\n### 4. 假设 (Assumptions)\\n..."
      }}
    ]
    """
    
    analysis_results = []
    for model_name in ['gemini-2.5-flash', 'gemini-2.0-flash']:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            text = response.text.strip()
            if text.startswith("```json"): text = text[7:]
            if text.endswith("```"): text = text[:-3]
            analysis_results = json.loads(text)
            if not isinstance(analysis_results, list): analysis_results = [analysis_results]
            break
        except: continue
    
    if not analysis_results: return []

    final_items = []
    
    # 1. 归档原文 (00_Inbox_AI)
    # 再次强调：自动创建文件夹
    # 增加 .replace(' ', '_')
    safe_title = analysis_results[0].get('title', 'Untitled').replace('/', '_').replace(' ', '_')[:20]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_filename = f"{safe_title}_{timestamp}.md"
    
    raw_content = f"""# 原文归档
    - **抓取时间**: {datetime.now().strftime("%Y-%m-%d %H:%M")}
    - **原文时间**: {analysis_results[0].get('publication_date')}
    - **来源**: {analysis_results[0].get('url')}
    ---
    {raw_text}
    """
    
    # 推送到 00 (PyGithub 会自动创建 00_Inbox_AI 文件夹)
    raw_link = push_to_github(raw_filename, raw_content, "00_Inbox_AI")
    
    # 2. 归档知识块 (01/02)
    for item in analysis_results:
        item['date'] = datetime.now().strftime("%Y-%m-%d")
        
        # 链接容错
        item['raw_doc_link'] = raw_link if raw_link else "#error_no_token"
        
        # 卡片内容
        card_content = f"""# {item['title']}
        - **分类**: {item['category']}
        - **偏向**: {item['bias']}
        - **原文**: [点击跳转]({item['raw_doc_link']})
        
        ## 深度结构化分析
        {item['deep_analysis_md']}
        """
        
        folder = "01_Macro_Research" if item.get('category') == "MACRO" else "02_Radar_Ticker"
        card_filename = f"{item['title'].replace('/', '_')}.md"
        
        card_link = push_to_github(card_filename, card_content, folder)
        item['card_link'] = card_link if card_link else "#error_no_token"
        
        final_items.append(item)
        
    return final_items