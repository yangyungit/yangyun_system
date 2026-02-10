import streamlit as st
import pandas as pd
import utils
import json
import time # 新增：用于频率控制
from scrapers.internal_generator import MACRO_OPEN_QUESTIONS

st.set_page_config(page_title="宏观监控", page_icon="🌍", layout="wide")
utils.inject_custom_css()
st.title("🌍 宏观监控 (Macro)")
# --- [架构师新增] 侧边栏：AI 生成功能 ---
with st.sidebar:
    st.markdown("### 🛠️ 情报工具箱")
    if st.button("🚀 AI生成宏观", type="primary", use_container_width=True):
        with st.status("正在调动 AI 进行深度范式推演...", expanded=True) as status:
            for i, q in enumerate(MACRO_OPEN_QUESTIONS):
                st.write(f"正在研判第 {i+1}/{len(MACRO_OPEN_QUESTIONS)} 组维度...")
                
                # 构造引导 Prompt，确保 AI 保持高水准输出 
                structured_prompt = f"【系统指令：执行宏观范式专项研判】\n\n研判维度：{q}"
                
                # 调用 utils 核心分发函数 
                # 这会自动完成：AI分析 -> GitHub存入00(原文)和01(卡片) -> Google Sheets 记录
                utils.auto_dispatch(None, structured_prompt)
                
                time.sleep(1.5) # 频率保护，防止 API 触发速率限制
            
            status.update(label="✅ 宏观研判生成完毕！", state="complete", expanded=False)
        
        st.toast("情报已同步至 Google Sheets 及 GitHub", icon="💾")
        time.sleep(1)
        st.rerun() # 自动刷新页面，展示最新生成的情报
# --- 加载数据 ---
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