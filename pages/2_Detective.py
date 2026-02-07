import streamlit as st
from openai import OpenAI

st.set_page_config(page_title="侦探工作室", page_icon="🕵️", layout="wide")

# --- 配置区 (已安全升级) ---
try:
    # 尝试从保险柜 (.streamlit/secrets.toml) 拿钥匙
    DEEPSEEK_API_KEY = st.secrets["DEEPSEEK_API_KEY"]
except FileNotFoundError:
    st.error("密钥未配置！请在 .streamlit/secrets.toml 中配置 DEEPSEEK_API_KEY")
    st.stop()

BASE_URL = "https://api.deepseek.com"

# 检查数据
if 'current_case_id' not in st.session_state or not st.session_state['current_case_id']:
    st.warning("⚠️ 请先在 'Radar' 页面选择一个案件。")
    st.stop()

case_id = st.session_state['current_case_id']
# 从列表中查找案件对象
current_case = next((x for x in st.session_state['news_stream'] if x['id'] == case_id), None)

st.title(f"🕵️ 案件侦查: {current_case['title']}")

# 左侧显示原始档案
with st.sidebar:
    st.subheader("📁 原始档案")
    st.info(f"ID: {case_id}")
    st.write(f"**原文/逻辑:**\n{current_case['summary']}")

# 侦查主逻辑
if current_case['investigation']:
    st.success("✅ 此案件已完成侦查报告。")
    with st.container(border=True):
        st.markdown(current_case['investigation'])
    st.info("👉 请点击左侧 'Court' 进入法庭审判")
else:
    st.markdown("### 🚀 AI 侦探待命")
    st.write("点击下方按钮，AI 将调用金融知识库，对该观点进行逻辑压力测试和背景调查。")
    
    if st.button("开始调查 (Start Investigation)"):
        if "sk-" not in DEEPSEEK_API_KEY:
            st.error("请先在代码中填入 DeepSeek API Key！")
            st.stop()
            
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=BASE_URL)
        
        detective_prompt = f"""
        你是一个严谨的金融侦探。请针对以下情报进行核查：
        情报：{current_case['title']}
        详情：{current_case['summary']}
        
        任务：
        1. **逻辑自洽性检验**：这个观点的推导链条是否完整？有无逻辑跃迁？
        2. **反身性思考**：如果是共识，现在的价格是否已经计价（Priced in）？
        3. **风险情景**：列出 3 个可能导致该判断失效的“黑天鹅”或“灰犀牛”因素。
        4. **关键验证指标**：我应该去查什么数据来验证它？
        
        输出格式：Markdown 简报。
        """
        
        with st.spinner("🕵️ 侦探正在分析..."):
            try:
                response = client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[{"role": "user", "content": detective_prompt}]
                )
                report = response.choices[0].message.content
                # 保存结果
                current_case['investigation'] = report
                st.rerun()
            except Exception as e:
                st.error(f"侦探出错: {e}")