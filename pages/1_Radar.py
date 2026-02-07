import streamlit as st
import pandas as pd
import plotly.express as px
import utils
from datetime import datetime

st.set_page_config(page_title="情报雷达", page_icon="📡", layout="wide")

st.title("📡 全球情报雷达 (Radar)")

# --- 1. 加载数据 ---
if 'news_stream' not in st.session_state:
    st.session_state['news_stream'] = utils.load_data(sheet_name="radar_data")

# 转换为 DataFrame 方便统计
df = pd.DataFrame(st.session_state['news_stream'])

# --- 2. 顶部仪表盘 (保留关键指标) ---
if df.empty:
    total_count = 0
    high_surprise_count = 0
    recent_count = 0
else:
    # 补齐缺少的列
    if 'surprise' not in df.columns: df['surprise'] = 0
    if 'time' not in df.columns: df['time'] = ""
    
    total_count = len(df)
    try:
        # 计算高惊奇度 (>4)
        high_surprise_count = len(df[pd.to_numeric(df['surprise'], errors='coerce') >= 4])
    except:
        high_surprise_count = 0
    recent_count = len(df)

# 仪表盘区域
with st.container():
    c1, c2, c3, c4 = st.columns([1, 1, 1, 3])
    c1.metric("情报总量", str(total_count))
    c2.metric("高惊奇 (>4)", str(high_surprise_count), delta_color="inverse")
    c3.metric("待处理", str(recent_count))
    
    # 放一个手动录入的折叠入口
    with c4.expander("📝 手动录入 (Manual Input)", expanded=False):
        with st.form("manual_radar_input", clear_on_submit=True):
            f_col1, f_col2 = st.columns([3, 1])
            m_title = f_col1.text_input("标题", placeholder="例如：NVDA 财报超预期...")
            m_surp = f_col2.number_input("惊奇度", 1, 5, 3)
            m_summary = st.text_area("摘要", height=60, placeholder="详细内容...")
            m_tags = st.text_input("标签", placeholder="#NVDA #半导体 (空格分隔)")
            
            if st.form_submit_button("📥 快速入库"):
                new_item = {
                    "id": f"MAN/{int(datetime.now().timestamp())}",
                    "title": m_title,
                    "time": datetime.now().strftime("%m-%d %H:%M"),
                    "tags": m_tags.split(" ") if m_tags else [],
                    "surprise": m_surp,
                    "source": "Manual",
                    "summary": m_summary,
                    "investigation": None
                }
                st.session_state['news_stream'].insert(0, new_item)
                utils.save_data(st.session_state['news_stream'], "radar_data")
                st.rerun()

st.divider()

# --- 3. 硬核情报列表 (Hardcore List) ---

# 表头设计
h1, h2, h3, h4, h5 = st.columns([1.5, 1, 5, 2, 1])
h1.markdown("**时间**")
h2.markdown("**惊奇度**")
h3.markdown("**情报内容 (Title & Summary)**")
h4.markdown("**标签 (Tags)**")
h5.markdown("**操作**")
st.markdown("---")

# 列表渲染
if not st.session_state['news_stream']:
    st.info("📭 暂无数据，请从 Home 投喂。")

for i, item in enumerate(st.session_state['news_stream']):
    # 定义列宽比例：时间 | 惊奇 | 内容 | 标签 | 按钮
    c1, c2, c3, c4, c5 = st.columns([1.5, 1, 5, 2, 1])
    
    # 1. 时间
    time_str = item.get('time', 'N/A')
    c1.text(time_str)
    
    # 2. 惊奇度 (高亮处理)
    try:
        score = float(item.get('surprise', 0))
    except:
        score = 0
    
    # 颜色编码：分越高越红
    score_color = "gray"
    if score >= 4: score_color = "red"
    elif score >= 3: score_color = "orange"
    
    c2.markdown(f":{score_color}[**{score}**]")
    
    # 3. 内容 (标题加粗，摘要换行变灰)
    title = item.get('title', 'No Title')
    summary = item.get('summary', '')
    # 如果 summary 太长，截断一下
    if len(summary) > 80: summary = summary[:80] + "..."
    
    c3.markdown(f"**{title}**")
    if summary and summary != title:
        c3.caption(summary)
    
    # 4. 标签
    tags = item.get('tags', [])
    if isinstance(tags, str): tags = [tags] # 容错
    if tags:
        # 用代码块风格展示标签，看着更硬核
        tag_html = " ".join([f"`{t}`" for t in tags])
        c4.markdown(tag_html)
    else:
        c4.caption("-")
        
    # 5. 侦查按钮
    # 使用唯一的 key 防止冲突
    if c5.button("🕵️", key=f"inv_{i}", help="进入侦查室"):
        st.session_state['current_case_id'] = item.get('id')
        st.switch_page("pages/2_Detective.py")
        
    # 行间分割线 (可选，为了紧凑可以不要，或者用空行)
    # st.markdown("---") 
    st.markdown("<div style='margin-bottom: 10px;'></div>", unsafe_allow_html=True)