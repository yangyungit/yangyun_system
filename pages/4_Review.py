import streamlit as st
import pandas as pd
import utils

st.set_page_config(page_title="复盘中心", page_icon="📜", layout="wide")

st.title("📜 投资复盘中心 (Case History)")

# 加载数据
if 'news_stream' not in st.session_state:
    st.session_state['news_stream'] = utils.load_data()

data = st.session_state['news_stream']

# 筛选出有实质内容的案子（有侦查报告或已归档的）
valid_cases = [x for x in data if x.get('investigation') or x.get('status') == 'Archived']

if not valid_cases:
    st.info("暂无历史案件。请去 Radar 录入并去 Detective 侦查。")
    st.stop()

# --- 统计看板 ---
c1, c2, c3 = st.columns(3)
c1.metric("累计研究", f"{len(valid_cases)} 个")
c2.metric("已归档决策", f"{len([x for x in valid_cases if x.get('status') == 'Archived'])} 个")
c3.metric("知识库", "Obsidian 连通")

st.divider()

# --- 历史列表 ---
for case in valid_cases:
    # 动态计算图标
    icon = "✅" if case.get('status') == 'Archived' else "🕵️"
    status_text = "已结案" if case.get('status') == 'Archived' else "侦查中"
    
    with st.expander(f"{icon} [{case['time']}] {case['title']} ({status_text})"):
        
        # 1. 基础信息
        st.caption(f"ID: {case['id']} | Tags: {', '.join(case.get('tags', []))}")
        st.markdown(f"**原文:** {case['summary']}")
        
        # 2. 侦查报告预览
        if case.get('investigation'):
            st.markdown("---")
            st.markdown("#### 🕵️ 侦查报告精华")
            # 只显示前 200 字预览
            preview = case['investigation'][:200] + "..."
            st.text(preview)
            
            if st.checkbox("展开完整报告", key=f"inv_{case['id']}"):
                st.markdown(case['investigation'])
        
        # 3. 最终裁决 (如果是已归档)
        if case.get('status') == 'Archived':
            st.markdown("---")
            st.success(f"**👨‍⚖️ 最终裁决:**\n\n{case.get('verdict', '无')}")
        
        # 4. 操作
        if st.button("📂 在法庭打开", key=f"btn_{case['id']}"):
            st.session_state['current_case_id'] = case['id']
            st.switch_page("pages/3_Court.py")