import streamlit as st
from openai import OpenAI
import utils 

st.set_page_config(page_title="认知法庭", page_icon="⚖️", layout="wide")

try:
    DEEPSEEK_API_KEY = st.secrets["DEEPSEEK_API_KEY"]
except FileNotFoundError:
    st.error("密钥未配置！")
    st.stop()

BASE_URL = "https://api.deepseek.com"

# --- 案件加载 ---
if 'current_case_id' not in st.session_state or not st.session_state['current_case_id']:
    st.warning("⚠️ 请先移交案件。")
    st.stop()

case_id = st.session_state['current_case_id']
current_case = next((x for x in st.session_state['news_stream'] if x['id'] == case_id), None)

# --- 侧边栏：法官席 (Judge's Bench) ---
with st.sidebar:
    st.header("👨‍⚖️ 法官席 (Verdict)")
    st.info(f"审理: {current_case['title']}")
    
    st.caption("在此记录辩论要点，并下达最终指令。")
    
    # 裁决输入框
    default_verdict = current_case.get('verdict', "")
    decision = st.text_area("✍️ 法官笔记", value=default_verdict, height=400, 
                           placeholder="例如：\n1. 芒格对估值的担忧值得注意...\n2. 但索罗斯的趋势逻辑目前占优...\n3. 结论：轻仓试错。")
    
    st.divider()
    
# 归档按钮
    if st.button("🏁 宣判并归档 (Archive)", type="primary"):
        if not decision:
            st.error("请填写裁决内容！")
        else:
            # --- 核心修改：完整保存辩论记录 ---
            debate_transcript = "\n\n### 💬 董事会辩论全记录\n"
            if 'debate_history' in current_case:
                for m in current_case['debate_history']:
                    # 格式化角色名
                    role_name = "主席 (User)" if m['role'] == "user" else "AI 董事"
                    # 尝试从内容里提取角色名 (如果 AI 输出格式是 **Name:**)
                    content = m['content']
                    
                    debate_transcript += f"\n> **{role_name}**: \n{content}\n"
            
            # 拼接到 Obsidian 内容里
            full_content = decision + debate_transcript
            
            # 保存
            success, msg = utils.save_to_obsidian(current_case, full_content)
            
            if success:
                current_case['status'] = 'Archived'
                current_case['verdict'] = decision
                utils.save_data(st.session_state['news_stream'])
                st.balloons()
                st.success(f"已归档！包含 {len(current_case.get('debate_history', []))} 条辩论记录。")
                st.rerun()

# --- 主界面 ---

st.title("🧠 董事会辩论 (Boardroom)")

# 0. 顶部宽屏展示证据
with st.expander("📂 查阅侦查卷宗 (Investigation Report)", expanded=False):
    st.markdown(current_case.get('investigation', '暂无报告'))

st.divider()

# 如果已结案
if current_case.get('status') == 'Archived':
    st.success("✅ 本案已结案。")
    st.markdown(f"### 最终裁决\n{current_case.get('verdict')}")
    if st.button("🔄 重新审理"):
        current_case['status'] = 'Active'
        utils.save_data(st.session_state['news_stream'])
        st.rerun()
    st.stop()

# 1. 董事选择区 (仅在未开始时显示)
if 'debate_history' not in current_case or not current_case['debate_history']:
    st.info("🔔 请配置董事会成员，并点击下方按钮开庭。")
    
    col1, col2 = st.columns([3, 1])
    with col1:
        selected_personas = st.multiselect(
            "出席董事:",
            ["查理·芒格", "乔治·索罗斯", "巴菲特", "段永平", "马斯克", "塔勒布"],
            default=["查理·芒格", "乔治·索罗斯"]
        )
    with col2:
        st.write("")
        st.write("")
        # --- 敲锤开庭 (Auto-Start) ---
        if st.button("🔨 敲锤开庭 (Start)", type="primary"):
            # A. 锁定名单 (修复 NameError)
            current_case['board_members'] = selected_personas
            current_case['debate_history'] = []
            
            # B. 写入系统开场白
            current_case['debate_history'].append({
                "role": "assistant",
                "content": f"**[系统]** 会议已召开。出席人：{', '.join(selected_personas)}。\n正在等待董事们发表开场陈词..."
            })
            
            # C. AI 强制生成第一轮辩论 (The Opening Shot)
            client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=BASE_URL)
            
            opening_prompt = f"""
            你正在模拟一场投资董事会。
            案件：{current_case['title']}
            详情：{current_case['summary']}
            证据：{current_case.get('investigation', '无')}
            
            角色：{", ".join(selected_personas)}
            
            任务：
            请**立刻开始**第一轮辩论。不要等待主席发言。
            必须模拟角色之间的直接对话，甚至争论。
            
            示例：
            **{selected_personas[0]}:** ...
            **{selected_personas[1] if len(selected_personas)>1 else 'AI'}:** ... (反驳或补充)
            """
            
            with st.spinner("董事们正在整理领带，准备激烈交锋..."):
                response = client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[{"role": "user", "content": opening_prompt}]
                )
                
                # 存入 AI 的开场辩论
                current_case['debate_history'].append({
                    "role": "assistant",
                    "content": response.choices[0].message.content
                })
                utils.save_data(st.session_state['news_stream'])
                st.rerun()

# 2. 聊天记录展示区
if 'debate_history' in current_case:
    for msg in current_case['debate_history']:
        # 渲染每条消息
        # 即使是 AI 生成的开场白（包含多个角色的对话），也放在一个 bubble 里显示
        avatar = "👨‍✈️" if msg['role'] == "user" else "🧠"
        with st.chat_message(msg['role'], avatar=avatar):
            st.markdown(msg['content'])

# 3. 底部输入框 (主席插话)
# 只要开会了，就显示输入框
if 'debate_history' in current_case and current_case['debate_history']:
    if prompt := st.chat_input("主席，请下达指示或插话..."):
        # A. 记录主席发言
        current_case['debate_history'].append({"role": "user", "content": prompt})
        with st.chat_message("user", avatar="👨‍✈️"):
            st.markdown(prompt)
        
        # B. AI 董事回应
        board_members = current_case.get('board_members', ["董事会"])
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=BASE_URL)
        
        # 上下文构建
        recent_history = current_case['debate_history'][-8:] 
        system_prompt = f"""
        你是董事会模拟器。角色：{", ".join(board_members)}。
        当前案件：{current_case['title']}
        侦查结论简述：{current_case.get('investigation', '')[:500]}...
        
        用户是主席。他刚才插话说："{prompt}"。
        请根据他的话，让相关的董事进行回应。保持角色性格。
        """
        
        with st.chat_message("assistant", avatar="🧠"):
            placeholder = st.empty()
            full_res = ""
            stream = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "system", "content": system_prompt}] + 
                         [{"role": m["role"], "content": m["content"]} for m in recent_history],
                stream=True
            )
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    full_res += chunk.choices[0].delta.content
                    placeholder.markdown(full_res + "▌")
            placeholder.markdown(full_res)
        
        current_case['debate_history'].append({"role": "assistant", "content": full_res})
        utils.save_data(st.session_state['news_stream'])