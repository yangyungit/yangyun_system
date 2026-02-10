import streamlit as st
import utils
import pandas as pd
import google.generativeai as genai

st.set_page_config(page_title="情报侦查室", page_icon="🕵️", layout="wide")

st.title("🕵️ 深度情报侦查室 (Detective Room)")

# --- 1. 读取案卷 ---
if 'case_files' not in st.session_state or not st.session_state['case_files']:
    st.info("📭 案卷库是空的。请先去 [宏观] 或 [雷达] 页面点击 ➕ 号添加情报。")
    st.stop()

files = st.session_state['case_files']

# --- 2. 侧边栏：案卷管理 ---
with st.sidebar:
    st.header(f"📂 待侦查案卷 ({len(files)})")
    if st.button("🗑️ 清空案卷", use_container_width=True):
        st.session_state['case_files'] = []
        st.rerun()
    st.divider()
    for idx, case in enumerate(files):
        st.markdown(f"**{idx+1}. {case.get('title', '无标题')}**")
        cat = case.get('category', 'UNKNOWN')
        if cat == 'MACRO': st.caption("🌍 宏观情报")
        else: st.caption("📡 雷达情报")
        if st.button("❌ 移除", key=f"del_case_{idx}"):
            files.pop(idx)
            st.session_state['case_files'] = files
            st.rerun()
        st.markdown("---")

# --- 3. 案卷内容预览 (四维视图) ---
with st.expander("查看所有案卷的【四维结构化知识】 (事实/观点/逻辑/假设)", expanded=False):
    for i, f in enumerate(files):
        st.markdown(f"### 📄 案卷 {i+1}: {f.get('title')}")
        # 这里展示的是 01/02 里的深度分析内容
        # 如果是新版 utils 生成的，这里已经包含了四个标题
        content = f.get('deep_analysis_md', '⚠️ 缺少结构化数据')
        st.markdown(content)
        st.divider()

# --- 4. AI 侦探工作台 ---
st.subheader("🧠 AI 联合侦查 (Joint Investigation)")

# 构造 Prompt 上下文
context_text = ""
for i, f in enumerate(files):
    content_payload = f.get('deep_analysis_md')
    if not content_payload:
        # 兼容旧数据
        content_payload = f"摘要: {f.get('summary')} (缺失结构化分析)"

    context_text += f"""
    === 🕵️ 案卷 {i+1} ===
    【标题】: {f.get('title')}
    【分类】: {f.get('category')}
    【结构化知识块】:
    {content_payload}
    ====================
    """

# 预设高阶侦查指令 (针对四维切分)
q_options = [
    "🔍 事实 vs 观点：请帮我把【事实】剥离出来，重新评估作者的【观点】是否过于激进？",
    "🔗 逻辑链压力测试：检查【逻辑】推导过程，哪里存在断裂或强行归因？",
    "⚠️ 假设崩塌推演：攻击文中的【假设】，如果这些前提不成立（例如通胀反弹了），结论会发生什么逆转？",
    "⚔️ 跨案卷矛盾：案卷之间是否存在【事实】冲突或【逻辑】互斥？",
    "💰 制定作战计划：基于以上事实和逻辑，构建一个高胜率交易策略。"
]

col_q1, col_q2 = st.columns([1, 1])
user_q = col_q1.selectbox("选择侦查方向:", q_options)
manual_q = col_q2.text_input("或 💬 向侦探提问:", placeholder="例如：如果假设中的良品率不及预期，股价下跌空间有多少？")

final_q = manual_q if manual_q else user_q

if st.button("🚀 开始并案侦查", type="primary"):
    with st.status("🕵️ 侦探正在进行四维审视...", expanded=True) as status:
        try:
            # 1. 配置 AI
            api_key = utils.get_config("GOOGLE_API_KEY")
            if not api_key:
                st.error("❌ 缺少 API Key")
                st.stop()
            
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-2.0-flash') 
            
            # 2. 构造 Prompt
            full_prompt = f"""
            你是一位极度理性的情报侦探。你面前的案卷已经经过了【事实、观点、逻辑、假设】的四维切分。
            
            请基于以下【案卷内容】，回答【我的问题】。
            
            【案卷内容】：
            {context_text}
            
            【我的问题】：
            {final_q}
            
            【回答要求】：
            1. **事实核查**：在回答时，请明确引用案卷中的【事实】部分作为证据。
            2. **观点隔离**：不要被原文的【观点】带偏，要用批判性眼光审视它们。
            3. **攻击假设**：重点关注【假设】部分，这是最容易出错的地方，请进行证伪。
            4. 输出格式清晰，使用 Markdown。
            """
            
            status.write("🧠 正在进行逻辑对抗与假设验证...")
            response = model.generate_content(full_prompt)
            
            status.update(label="✅ 侦查报告已生成", state="complete", expanded=False)
            
            st.markdown("### 📝 侦查报告")
            st.markdown(response.text)
            
        except Exception as e:
            st.error(f"侦查失败: {e}")