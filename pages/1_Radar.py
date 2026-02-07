import streamlit as st
import pandas as pd
from datetime import datetime
import time

st.set_page_config(page_title="情报雷达", page_icon="📡", layout="wide")

# 确保数据已初始化 (防止用户直接刷新此页面报错)
if 'news_stream' not in st.session_state:
    st.switch_page("Home.py") # 踢回首页去初始化

st.title("📡 全球情报雷达 (Radar)")

# --- 侧边栏：手动录入 ---
with st.sidebar:
    with st.expander("📝 手动录入情报", expanded=False):
        with st.form("manual_input"):
            new_title = st.text_input("核心情报/观点")
            # 更新为你想要的标签体系
            new_tags = st.multiselect("标签体系", 
                ["#大宗商品", "#技术突破", "#宏观", "#美联储", "#情绪", "#泡沫预警", "#共振", "#背离", "#拐点", "#Crypto"])
            new_surprise = st.slider("惊奇度", 1, 5, 3)
            new_source = st.text_input("信源", value="我的思考")
            new_summary = st.text_area("详细逻辑/原文")
            
            if st.form_submit_button("📥 录入中台"):
                new_item = {
                    "id": f"MAN/{int(time.time())}",
                    "title": new_title,
                    "time": datetime.now().strftime("%H:%M"),
                    "tags": new_tags,
                    "surprise": new_surprise,
                    "source": new_source,
                    "summary": new_summary,
                    "investigation": None
                }
                st.session_state['news_stream'].insert(0, new_item)
                st.rerun()

# --- 核心大屏 ---
df = pd.DataFrame(st.session_state['news_stream'])

# 顶部统计
c1, c2, c3 = st.columns(3)
c1.metric("今日情报", str(len(df)))
c2.metric("高惊奇 (>4⭐)", str(len(df[df['surprise'] >= 4])))
c3.metric("市场情绪", "贪婪 76", "泡沫预警", delta_color="off")

st.divider()

# 表格配置 (去掉了 Status)
st.dataframe(
    df[['id', 'time', 'title', 'tags', 'surprise', 'source']],
    column_config={
        "id": st.column_config.TextColumn("ID", width="small"),
        "time": st.column_config.TextColumn("时间", width="small"),
        "title": st.column_config.TextColumn("核心情报", width="large"),
        "tags": st.column_config.ListColumn("标签体系", width="medium"),
        "surprise": st.column_config.NumberColumn("惊奇度", format="%d ⭐", width="small"),
        "source": st.column_config.TextColumn("信源", width="small"),
    },
    use_container_width=True,
    hide_index=True
)

st.caption("👇 选中下方 ID 启动侦查")

# 交互操作区
all_ids = df['id'].tolist()
# 增加一个空选项，防止误触
selected_case = st.selectbox("🎯 选择案件 ID:", [""] + all_ids)

if selected_case:
    # 找到对应的标题用于提示
    case_title = df[df['id'] == selected_case]['title'].values[0]
    st.info(f"已选中: {case_title}")
    
    if st.button("🔍 移交侦探工作室 (Dispatch)"):
        st.session_state['current_case_id'] = selected_case
        st.toast(f"案件 {selected_case} 已移交！请点击左侧 'Detective' 页面。", icon="🕵️")