import streamlit as st
import utils
import google.generativeai as genai
import time

st.set_page_config(page_title="情报工厂", page_icon="🏭", layout="wide")
utils.inject_custom_css()

st.title("🏭 情报工厂 (Intelligence Factory)")
st.caption("基于‘宏观范式’逻辑及‘事件套利’Prompt 生成合成情报。")

# 你的深度宏观问题集
MACRO_PARADIGM_QUESTIONS = [
    "当前宏观范式正在从什么向什么转变？我们处于什么经济周期？",
    "当前市场比较超越预期的是什么？主要矛盾和张力最大的部分在哪里？",
    "当下供需关系严重错配的地方是什么？美股市场的主题主线及行业逻辑是什么？",
    "当下最现象级的事件/产品/公司是什么？资金轮动路径及流动性走向如何？"
]

# --- 核心逻辑 ---
def run_factory(questions, source_tag="Prompt_AI"):
    model = genai.GenerativeModel('gemini-2.0-flash')
    progress_bar = st.progress(0)
    
    for i, q in enumerate(questions):
        st.write(f"🔍 正在推演: {q}")
        
        # 1. 深度模拟 (注入顶级对冲基金研究员的人设)
        prompt = f"你是一个宏观对冲基金的首席策略师。请针对以下问题进行深度思考并给出研报：{q}。要求：逻辑深邃，避开平庸观点，寻找市场共识之外的偏差。"
        
        try:
            response = model.generate_content(prompt)
            # 2. 直接调用 utils 存入 00_Inbox_AI 并分发
            # 在 raw_text 前面贴上标签，方便以后过滤
            injected_content = f"【来源: {source_tag}】\n\n{response.text}"
            utils.auto_dispatch(None, injected_content)
            st.success(f"✅ 第 {i+1} 组情报已归档至 00_Inbox_AI 及对应研究库")
        except Exception as e:
            st.error(f"生成失败: {e}")
            
        progress_bar.progress((i + 1) / len(questions))
        time.sleep(2)

# --- UI 布局 ---
col1, col2 = st.columns(2)

with col1:
    st.subheader("🌍 宏观范式推演")
    st.info("基于你设计的 11 个深度宏观维度进行系统性生成。")
    if st.button("开始全量宏观生产"):
        run_factory(MACRO_PARADIGM_QUESTIONS, source_tag="Macro_Paradigm")

with col2:
    st.subheader("🎯 事件套利模拟")
    st.warning("将使用你之前的‘事件套利 Prompt’模拟市场突发事件。")
    event_context = st.text_input("输入一个模拟事件", placeholder="例如：英伟达财报超预期但指引下调")
    if st.button("启动套利逻辑生成"):
        # 这里可以贴入你之前的事件套利具体 Prompt 逻辑
        custom_q = [f"基于事件套利逻辑分析：{event_context}"]
        run_factory(custom_q, source_tag="Event_Arbitrage")