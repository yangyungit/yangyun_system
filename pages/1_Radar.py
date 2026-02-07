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

# 转换为 DataFrame 以便处理
df = pd.DataFrame(st.session_state['news_stream'])

# --- 2. 顶部仪表盘 (Dashboard) ---
# 🛡️ 核心修复：先检查数据是否为空
if df.empty:
    # 如果是空的，显示默认 0
    total_count = 0
    high_surprise_count = 0
    recent_count = 0
else:
    # 如果有数据，但可能缺列，先补齐
    if 'surprise' not in df.columns: df['surprise'] = 0
    if 'time' not in df.columns: df['time'] = ""
    
    total_count = len(df)
    # 计算高惊奇度 (兼容字符串和数字)
    try:
        # 强制转为数字，无法转的变成 NaN 并在计算时忽略
        high_surprise_count = len(df[pd.to_numeric(df['surprise'], errors='coerce') >= 4])
    except:
        high_surprise_count = 0
        
    recent_count = len(df) 

c1, c2, c3 = st.columns(3)
c1.metric("今日情报", str(total_count))
c2.metric("高惊奇 (>4⭐)", str(high_surprise_count))
c3.metric("待处理", str(recent_count))

st.divider()

# --- 3. 情报列表与交互 ---

col_list, col_detail = st.columns([4, 3])

with col_list:
    st.subheader("📨 情报流")
    
    # 🔘 手动录入按钮
    with st.expander("📝 手动录入情报 (Manual Input)", expanded=False):
        with st.form("manual_radar_input"):
            m_title = st.text_input("标题", placeholder="例如：大摩上调 NVDA 目标价")
            m_summary = st.text_area("摘要/原文", height=100)
            m_tags = st.text_input("标签 (用空格分隔)", placeholder="#NVDA #半导体")
            m_surp = st.slider("惊奇度 (Surprise)", 1, 5, 3)
            
            if st.form_submit_button("📥 入库"):
                new_item = {
                    "id": f"MAN/{int(datetime.now().timestamp())}",
                    "title": m_title,
                    "time": datetime.now().strftime("%m-%d %H:%M"),
                    "tags": m_tags.split(" "),
                    "surprise": m_surp,
                    "source": "Manual",
                    "summary": m_summary,
                    "investigation": None
                }
                # 插入 Session
                st.session_state['news_stream'].insert(0, new_item)
                # ✅ 保存到 Google Sheets (radar_data)
                utils.save_data(st.session_state['news_stream'], "radar_data")
                st.rerun()

    # 📭 空状态展示
    if df.empty:
        st.info("暂无情报。请通过 Home 页面投喂，或上方手动录入。")
    else:
        # 📋 渲染列表
        for i, item in enumerate(st.session_state['news_stream']):
            with st.container(border=True):
                # 第一行：标题 + 惊奇度
                c_title, c_score = st.columns([5, 1])
                # 安全获取
                title_txt = item.get('title', 'No Title')
                surp_score = item.get('surprise', 3)
                
                c_title.markdown(f"**{title_txt}**")
                c_score.caption(f"⭐ {surp_score}")
                
                # 第二行：摘要
                summ_txt = item.get('summary', '')
                st.text(summ_txt[:100] + "..." if summ_txt else "")
                
                # 第三行：标签 + 按钮
                c_tags, c_btn = st.columns([4, 1])
                tags_display = item.get('tags', [])
                if isinstance(tags_display, str): tags_display = [tags_display]
                c_tags.caption(" ".join([f"`{t}`" for t in tags_display]))
                
                # 🕵️ 发起侦查按钮
                if c_btn.button("🕵️ 侦查", key=f"btn_inv_{i}"):
                    st.session_state['current_case_id'] = item.get('id')
                    st.switch_page("pages/2_Detective.py")

with col_detail:
    # 统计图表
    if not df.empty and 'surprise' in df.columns:
        st.subheader("📊 惊奇度分布")
        try:
            plot_df = df.copy()
            plot_df['surprise'] = pd.to_numeric(plot_df['surprise'], errors='coerce').fillna(0)
            fig = px.histogram(plot_df, x="surprise", nbins=5, title="情报惊奇度分布")
            fig.update_layout(height=300)
            st.plotly_chart(fig, use_container_width=True)
        except:
            st.caption("数据不足，无法绘图")
    else:
        st.subheader("📊 统计概览")
        st.caption("等待数据中...")