import streamlit as st
import pandas as pd
import utils
import json
import time # 新增：用于频率控制
from scrapers.internal_generator import MACRO_OPEN_QUESTIONS

st.set_page_config(page_title="宏观监控", page_icon="🌍", layout="wide")
utils.inject_custom_css()
st.title("🌍 宏观监控 (Macro)")

# --- [架构师升级版] 侧边栏：Gemini 3 Pro + 谷歌搜索 (Deep Research Lite) ---
import time
import google.generativeai as genai
from scrapers.internal_generator import MACRO_OPEN_QUESTIONS

with st.sidebar:
    st.markdown("### 🛠️ 情报工具箱")
    if st.button("🚀 AI生成宏观 (联网版)", type="primary", use_container_width=True):
        
        # 初始化
        api_key = utils.get_config("GOOGLE_API_KEY")
        if api_key: genai.configure(api_key=api_key)
        
        # 🔥 关键修改：配置工具 (Tools)
        # 这一步等于给了 AI 访问 Google 搜索的权限
        tools = [
            {"google_search_retrieval": {
                "dynamic_retrieval_config": {
                    "mode": "dynamic",  # 只有需要搜的时候才搜
                    "dynamic_threshold": 0.6,
                }
            }}
        ]

        # 👑 加载模型：Gemini 3 Pro + Tools
        try:
            model = genai.GenerativeModel('gemini-3-pro-preview', tools=tools)
        except:
            # 如果 3-Pro 不支持 Tools，退回到 2.0-Pro
            st.warning("3-Pro 暂不支持搜索工具，降级为 2.0-Pro...")
            model = genai.GenerativeModel('gemini-2.0-pro-exp-02-05', tools=tools)

        with st.status("正在进行联网深度推演...", expanded=True) as status:
            for i, q in enumerate(MACRO_OPEN_QUESTIONS):
                st.write(f"正在研判: {q[:15]}...")
                
                try:
                    # 直球生成 (现在它会自动去 Google 搜最新的数据了！)
                    response = model.generate_content(q)
                    
                    # 检查有没有用到搜索（调试用）
                    search_source = ""
                    if response.candidates[0].grounding_metadata.search_entry_point:
                        search_source = "\n\n*(已调用 Google Search 实时数据)*"

                    raw_answer = response.text + search_source
                    
                    # 物理拼接
                    final_content = f"""
# ❓ 提问 (Question)
{q}

---
# 🌐 联网回答 (Web-Search Answer)
{raw_answer}
"""
                    
                    # 发送给中台
                    new_items = utils.auto_dispatch(None, final_content, source="AI_Macro_Search")
                    
                    # 写入数据库
                    if new_items:
                        current_data = utils.load_data("macro_stream")
                        updated_data = new_items + current_data
                        utils.save_data(updated_data, "macro_stream")
                        
                except Exception as e:
                    st.error(f"Error: {e}")
                
                time.sleep(2) # 联网搜索比较慢，频率保护要加长
            
            status.update(label="✅ 深度调研完成", state="complete", expanded=False)
        
        st.toast("已生成含实时数据的研报", icon="🌍")
        time.sleep(1)
        st.rerun()

        
try:
    raw_data = utils.load_data(sheet_name="macro_stream")
    st.session_state['macro_data'] = raw_data
except:
    st.session_state['macro_data'] = []

df = pd.DataFrame(st.session_state['macro_data'])
if 'case_files' not in st.session_state: st.session_state['case_files'] = []

# --- 水位仪 ---
with st.expander("🦅 宏观水位仪", expanded=True):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("流动性", "Neutral")
    c2.metric("美联储", "Hawkish")
    c3.metric("10Y 美债", "4.15%")
    c4.metric("通胀预期", "2.3%")

st.divider()

# --- 情报流 ---
if len(st.session_state['case_files']) > 0:
    st.toast(f"🕵️ 案卷库现有 {len(st.session_state['case_files'])} 份情报", icon="📂")

st.subheader(f"📋 宏观情报流 ({len(df)})")

h1, h2, h3, h4, h5 = st.columns([1.5, 1, 6, 2, 1])
h1.markdown("**时间**")
h2.markdown("**偏向**")
h3.markdown("**结论 & 逻辑**")
h4.markdown("**标签 & 来源**")
h5.markdown("**并案**")
st.markdown("---")

for i, item in enumerate(st.session_state['macro_data']):
    with st.container():
        c1, c2, c3, c4, c5 = st.columns([1.5, 1, 6, 2, 1])
        
        # 1. 时间
        c1.caption(item.get('date', 'N/A'))
        pub_date = item.get('publication_date', 'Unknown')
        if pub_date and pub_date != 'Unknown':
            c1.caption(f"原文: {pub_date}")
        
        # 2. 偏向
        bias = item.get('bias', 'Neutral')
        if bias == 'Bullish': c2.markdown(":green[**Bullish**]")
        elif bias == 'Bearish': c2.markdown(":red[**Bearish**]")
        else: c2.markdown(":gray[Neutral]")
        
        # 3. 核心展示
        title = item.get('title', '').strip()
        summary = item.get('summary', '').strip()
        logic = item.get('logic_chain_display', '').strip()
        
        # 标题兜底策略
        display_title = title
        if not display_title or display_title in ["无结论", "无标题"]:
            if summary: display_title = summary
            elif logic: display_title = logic
            else: display_title = "暂无结论"
        
        # 链接
        # 👇 加上 .replace(' ', '%20') 修复空格导致链接断裂的问题
        raw_link = item.get('raw_doc_link', '#').replace(' ', '%20')
        card_link = item.get('card_link', '#').replace(' ', '%20') 
        
        # A. 结论
        if raw_link == "#error_no_token":
             c3.markdown(f"#### {display_title} (⚠️GitHub配置错误)")
        else:
             c3.markdown(f"#### [{display_title}]({raw_link})")
            
        # B. 逻辑链 (修复：清洗括号)
        if logic and logic != "无":
            # 👇 关键修复：把 AI 生成的中括号删掉，防止 Markdown 链接失效
            clean_logic = logic.replace('[', '').replace(']', '')
            
            if card_link == "#error_no_token":
                c3.info(f"⛓️ **逻辑**: {clean_logic}")
            else:
                c3.info(f"⛓️ **逻辑**: [{clean_logic}]({card_link})")
        
        # 4. 标签 & 来源
        tags = item.get('tags', [])
        if isinstance(tags, str):
            try: tags = json.loads(tags.replace("'", '"'))
            except: tags = [str(tags)]
        if tags and isinstance(tags, list):
            c4.markdown(" ".join([f"`{t}`" for t in tags[:3]]))
        
        url = item.get('url', '').strip()
        if url:
            if url.startswith("http"): c4.markdown(f"[🔗 原文]({url})")
            elif url == "Telegram Bot": c4.caption("🤖 Telegram")
            elif url == "Manual": c4.caption("📝 手动录入")
            else: c4.caption(f"来源: {url}")
        else:
            c4.caption("来源: 未知")

        # 5. 操作
        current_sum = item.get('summary', '') 
        is_in_cart = any(case.get('summary') == current_sum for case in st.session_state['case_files'])
        if is_in_cart:
            c5.button("✅", key=f"mac_done_{i}", disabled=True)
        else:
            if c5.button("➕", key=f"mac_add_{i}"):
                st.session_state['case_files'].append(item)
                st.rerun()
            
        st.markdown("<div style='margin-bottom: 12px; border-bottom: 1px solid #333;'></div>", unsafe_allow_html=True)