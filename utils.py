import streamlit as st
import gspread
import pandas as pd
import json
from duckduckgo_search import DDGS
import yfinance as yf

# --- 权限与工具函数 ---

def check_password():
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False
    if not st.session_state["password_correct"]:
        st.text_input("请输入指挥官口令:", type="password", key="password_input", on_change=password_entered)
        return False
    return True

def password_entered():
    if st.session_state["password_input"] == st.secrets["PASSWORD"]:
        st.session_state["password_correct"] = True
    else:
        st.error("口令错误")

# --- 核心：Google Sheets 连接器 ---
def get_gsheet_client():
    try:
        credentials = st.secrets["gcp_service_account"]
        gc = gspread.service_account_from_dict(credentials)
        return gc
    except Exception as e:
        print(f"密钥配置错误: {e}")
        return None

def load_data(sheet_name="radar_data"):
    """从 Google Sheets 加载数据"""
    try:
        gc = get_gsheet_client()
        if not gc: return []
        
        sh = gc.open("yangyun_system_db")
        worksheet = sh.worksheet(sheet_name)
        records = worksheet.get_all_records()
        
        if not records: return []
            
        for r in records:
            if 'tags' in r and isinstance(r['tags'], str):
                try:
                    r['tags'] = json.loads(r['tags'].replace("'", '"'))
                except:
                    r['tags'] = []
        return records
    except Exception as e:
        print(f"加载 {sheet_name} 提示: {e}")
        return []

def save_data(data, sheet_name="radar_data"):
    """保存数据到 Google Sheets"""
    try:
        gc = get_gsheet_client()
        sh = gc.open("yangyun_system_db")
        worksheet = sh.worksheet(sheet_name)
        
        worksheet.clear()
        
        if not data: return
            
        df = pd.DataFrame(data)
        for col in df.columns:
            df[col] = df[col].apply(lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, (list, dict)) else x)
            
        worksheet.update([df.columns.values.tolist()] + df.values.tolist())
    except Exception as e:
        st.error(f"云端保存失败: {e}")

# --- 搜索与分析 ---

def search_web(query, max_results=3):
    try:
        results = DDGS().text(query, max_results=max_results)
        return "\n".join([f"- {r['title']}: {r['body']} (Source: {r['href']})" for r in results])
    except Exception as e:
        return f"搜索失败: {e}"

def get_stock_analysis(ticker):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="6mo")
        info = stock.info
        if hist.empty: return "无数据"
        price = hist['Close'].iloc[-1]
        return f"价格: {price:.2f} | 业务: {info.get('longBusinessSummary', '')[:50]}..."
    except: return "分析失败"

# --- 🧠 核心修复：智能分发逻辑 ---
def auto_dispatch(client, raw_text):
    """
    V2.0: 更严格的分类与格式控制
    """
    prompt = f"""
    你是一个专业的金融情报路由员。请分析下面的文本，并严格按照 JSON 格式输出。
    
    【待分析文本】：
    {raw_text}
    
    【分类规则 (Category)】：
    1. MACRO (宏观): 仅限央行政策、CPI/PCE数据、地缘政治、大宗商品（黄金/原油）、汇率。
    2. RADAR (个股/微观): 任何涉及具体上市公司（如 TSLA, NVDA, AAPL）、个股财报、具体产品发布、行业新闻。
       * 注意：如果提到 "Tesla" 或 "Musk"，必须归类为 RADAR，哪怕它影响很大。
    
    【输出格式 (JSON)】：
    必须包含以下字段，不要包含 Markdown 格式：
    {{
        "category": "MACRO" 或 "RADAR",
        "summary": "一句话中文摘要（30字以内）",
        "tags": ["#标签1", "#标签2"],
        "bias": "利多/利空/中性"
    }}
    """
    
    try:
        res = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"} # 强制 JSON 模式
        )
        # 解析返回的 JSON
        data = json.loads(res.choices[0].message.content)
        
        # 🛡️ 安全检查：确保 summary 字段存在
        if 'summary' not in data:
            data['summary'] = raw_text[:20] + "..." # 如果 AI 没给摘要，就截取原文
        
        return data
        
    except Exception as e:
        return {"error": str(e), "category": "ERROR", "summary": "AI 解析失败"}