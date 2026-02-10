import streamlit as st
import pandas as pd
import utils
from datetime import datetime
import json

st.set_page_config(page_title="情报雷达", page_icon="📡", layout="wide")
utils.inject_custom_css()
st.title("📡 情报雷达 (Radar)")

# --- 加载数据 ---
try:
    st.session_state['news_stream'] = utils.load_data(sheet_name="radar_data")
except:
    st.session_state['news_stream'] = []
    
df = pd.DataFrame(st.session_state['news_stream'])
if 'case_files' not in st.session_state: st.session_state['case_files'] = []

# --- 仪表盘 ---
with st.container():
    c1, c2, c3, c4 = st.columns([1, 1, 1, 3])
    c1.metric("情报总量", str(len(df)))
    
    with c4.expander("📝 手动录入", expanded=False):
        with st.form("manual_radar"):
            f1, f2 = st.columns([3, 1])
            m_input = f1.text_input("极简结论 (标题)", placeholder="例如: NVDA 财报超预期...")
            m_bias = f2.selectbox("偏向", ["Bullish", "Bearish", "Neutral"])
            m_logic = st.text_input("逻辑链", placeholder="业绩 -> 估值 -> 股价")
            
            if st.form_submit_button("📥 入库"):
                if m_input:
                    new = {
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "category": "RADAR",
                        "bias": m_bias,
                        "title": m_input,   
                        "summary": m_input, 
                        "logic_chain_display": m_logic if m_logic else "手动录入",
                        "tags": ["Manual"],
                        "url": "Manual", # 明确标记来源
                        "raw_doc_link": "#",
                        "card_link": "#"
                    }
                    cur = utils.load_data("radar_data")
                    cur.insert(0, new)
                    utils.save_data(cur, "radar_data")
                    st.rerun()

st.divider()

# --- 列表 ---
h1, h2, h3, h4, h5 = st.columns([1.5, 1, 6, 2, 1])
h1.markdown("**时间**")
h2.markdown("**偏向**")
h3.markdown("**结论 & 逻辑**")
h4.markdown("**标签 & 来源**")
h5.markdown("**并案**")
st.markdown("---")

for i, item in enumerate(st.session_state['news_stream']):
    with st.container():
        c1, c2, c3, c4, c5 = st.columns([1.5, 1, 6, 2, 1])
        
        c1.caption(item.get('date', 'N/A'))
        
        bias = item.get('bias', 'Neutral')
        if bias == 'Bullish': c2.markdown(":green[**Bullish**]")
        elif bias == 'Bearish': c2.markdown(":red[**Bearish**]")
        else: c2.markdown(":gray[Neutral]")
        
        # 3. 核心展示 (三级替补策略)
        title = item.get('title', '').strip()
        summary = item.get('summary', '').strip()
        logic = item.get('logic_chain_display', '').strip()
        
        # 智能选择标题
        display_title = title
        # 如果标题无效，降级使用摘要；再不行，使用逻辑链
        if not display_title or display_title in ["无结论", "无标题"]:
            if summary:
                display_title = summary
            elif logic:
                display_title = logic
            else:
                display_title = "暂无结论"
        
        # 限制长度，防止太长 (如果用了摘要作为标题)
        if len(display_title) > 40:
             display_title = display_title[:38] + "..."

        raw_link = item.get('raw_doc_link', '#')
        card_link = item.get('card_link', '#')
        
        if raw_link == "#error_no_token":
             c3.markdown(f"#### {display_title}")
        else:
             c3.markdown(f"#### [{display_title}]({raw_link})")
             
        if logic and logic != "无": 
            # 👇 关键修复：把 AI 生成的中括号删掉，防止 Markdown 链接失效
            clean_logic = logic.replace('[', '').replace(']', '')
            if card_link == "#error_no_token":
                c3.info(f"⛓️ **逻辑**: {logic}")
            else:
                c3.info(f"⛓️ **逻辑**: [{logic}]({card_link})")
        
        # 4. 标签 & 来源 (修正：显示所有类型来源)
        tags = item.get('tags', [])
        if isinstance(tags, str):
            try: tags = json.loads(tags.replace("'", '"'))
            except: tags = [str(tags)]
        if tags and isinstance(tags, list):
            c4.markdown(" ".join([f"`{t}`" for t in tags[:3]]))
            
        url = item.get('url', '').strip()
        if url:
            if url.startswith("http"): 
                c4.markdown(f"[🔗 原文]({url})")
            elif url == "Manual":
                c4.caption("📝 手动")
            else:
                c4.caption(f"来源: {url}")
        else:
            c4.caption("来源: 未知")

        # 5. 按钮
        current_sum = item.get('summary', '')
        is_in_cart = any(case.get('summary') == current_sum for case in st.session_state['case_files'])
        if is_in_cart:
            c5.button("✅", key=f"rad_done_{i}", disabled=True)
        else:
            if c5.button("➕", key=f"rad_add_{i}"):
                st.session_state['case_files'].append(item)
                st.rerun()
            
        st.markdown("<div style='margin-bottom: 12px; border-bottom: 1px solid #333;'></div>", unsafe_allow_html=True)