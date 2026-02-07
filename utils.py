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
        # 从 secrets 读取配置
        credentials = st.secrets["gcp_service_account"]
        # 使用 gspread 进行认证
        gc = gspread.service_account_from_dict(credentials)
        return gc
    except Exception as e:
        st.error(f"密钥配置错误: {e}")
        return None

def load_data(sheet_name="radar_data"):
    """从 Google Sheets 加载数据"""
    try:
        gc = get_gsheet_client()
        if not gc: return []
        
        # 打开表格
        sh = gc.open("yangyun_system_db")
        worksheet = sh.worksheet(sheet_name)
        
        # 获取所有记录
        records = worksheet.get_all_records()
        
        if not records: return []
            
        # 数据清洗：修复 tags 格式
        for r in records:
            if 'tags' in r and isinstance(r['tags'], str):
                try:
                    # 尝试把字符串 "['#a', '#b']" 变回列表
                    r['tags'] = json.loads(r['tags'].replace("'", '"'))
                except:
                    r['tags'] = []
        return records
        
    except Exception as e:
        # 如果是第一次运行表格可能是空的，不算大错，返回空列表即可
        print(f"加载 {sheet_name} 提示: {e}")
        return []

def save_data(data, sheet_name="radar_data"):
    """保存数据到 Google Sheets (全量覆盖)"""
    try:
        gc = get_gsheet_client()
        sh = gc.open("yangyun_system_db")
        worksheet = sh.worksheet(sheet_name)
        
        worksheet.clear() # 先清空
        
        if not data: return
            
        # 预处理：把 list/dict 转为字符串存入单元格
        df = pd.DataFrame(data)
        for col in df.columns:
            df[col] = df[col].apply(lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, (list, dict)) else x)
            
        # 写入
        worksheet.update([df.columns.values.tolist()] + df.values.tolist())
        
    except Exception as e:
        st.error(f"保存失败: {e}")

# --- 搜索与分发 (保留原有逻辑) ---

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

def auto_dispatch(client, raw_text):
    # 这里保留你的 AI 分发逻辑
    prompt = f"分析以下文本归类为 MACRO 或 RADAR，输出 JSON: {raw_text}"
    try:
        res = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return json.loads(res.choices[0].message.content)
    except Exception as e:
        return {"error": str(e)}