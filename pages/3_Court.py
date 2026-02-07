import streamlit as st
from openai import OpenAI

st.set_page_config(page_title="认知法庭", page_icon="⚖️", layout="wide")

DEEPSEEK_API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxx" # <--- 🔴 记得填 Key !!!
BASE_URL = "https://api.deepseek.com"

if 'current_case_id' not in st.session_state or not st.session_state['current_case_id']:
    st.warning("⚠️ 请先移交案件。")
    st.stop()

case_id = st.session_state['current_case_id']
current_case = next((x for x in st.session_state['news_stream'] if x['id'] == case_id), None)

st.title(f"⚖️ 认知法庭: {current_case['title']}")

# 检查证据
if not current_case.get('investigation'):
    st.error("⛔️ 侦探报告缺失！法官拒绝开庭。请返回 Detective 页面补充调查。")
    st.stop()

# 展示证据
with st.expander("📂 呈堂证供 (侦探报告)", expanded=False):
    st.markdown(current_case['investigation'])

st.divider()

# 董事会
st.subheader("🧠 董事会辩论")

selected_personas = st.multiselect(
    "召唤董事会成员:",
    ["查理·芒格", "乔治·索罗斯", "外星人", "疯狂散户", "段永平"],
    default=["查理·芒格", "乔治·索罗斯"]
)

if st.button("🔴 开始辩论 (Start Debate)"):
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=BASE_URL)
    
    board_prompt = f"""
    你是一个顶级基金投委会导演。
    案件：{current_case['title']}
    详情：{current_case['summary']}
    证据：{current_case['investigation']}
    
    请模拟 {", ".join(selected_personas)} 之间的对话。
    
    要求：
    1. **去油腻**：禁止任何动作描写（如“点燃雪茄”），禁止情绪化废话。
    2. **硬核**：芒格关注反向思考和护城河；索罗斯关注假象和时机；外星人关注物理第一性；散户关注价格冲动。
    3. **结论**：最后由“主持人”总结胜率和赔率。
    """
    
    with st.chat_message("assistant"):
        placeholder = st.empty()
        full_text = ""
        stream = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": board_prompt}],
            stream=True
        )
        for chunk in stream:
            if chunk.choices[0].delta.content:
                full_text += chunk.choices[0].delta.content
                placeholder.markdown(full_text + "▌")
        placeholder.markdown(full_text)

# 最终裁决
st.divider()
st.subheader("👨‍⚖️ 最终裁决")
decision = st.text_area("法官笔记", placeholder="在此输入最终决策逻辑，将存入 Obsidian...")
if st.button("归档决策"):
    st.success("已保存至知识库！(模拟)")