import json
import streamlit as st
import time
from openai import OpenAI
import pandas as pd
from datetime import datetime

# --- 0. 全局配置与工具函数 ---
st.set_page_config(page_title="养云资产·投研中台", layout="wide")

# CSS 美化
st.markdown("""
<style>
    .big-font { font-size:20px !important; }
    .risk-alert { background-color: #330000; padding: 10px; border-radius: 5px; color: #ffcccc; border: 1px solid #ff0000; }
    .stButton>button { width: 100%; border-radius: 5px; }
    .success-box { background-color: #003300; padding: 10px; border-radius: 5px; color: #ccffcc; border: 1px solid #00ff00; }
</style>
""", unsafe_allow_html=True)

# 初始化 Session State (让数据在页面切换时不丢失)
if 'news_stream' not in st.session_state:
    st.session_state['news_stream'] = [
        # 预设几条假数据
        {"id": "NVDA_01", "title": "大摩翻多 NVDA 至 $1600", "time": "10:30", "tags": ["#Semi", "#Macro"], "surprise": 4, "status": "Wait", "source": "Bloomberg", "summary": "渠道调研显示台积电新封装解决过热问题", "investigation": None},
        {"id": "CN_STIMULUS", "title": "央行意外降准 50bp", "time": "09:15", "tags": ["#Macro", "#China"], "surprise": 5, "status": "Wait", "source": "Caixin", "summary": "超出预期的全面降准", "investigation": None},
    ]

if 'current_case_id' not in st.session_state:
    st.session_state['current_case_id'] = None

# --- 1. 侧边栏：导航 & 手动录入 ---
with st.sidebar:
    st.title("🏯 养云资产")
    page = st.radio("工作流导航", ["📡 1. 情报雷达 (Radar)", "🕵️ 2. 侦探工作室 (Detective)", "⚖️ 3. 认知法庭 (Court)"])
    
    st.divider()
    
    # === 新增：手动录入功能 ===
    with st.expander("📝 手动录入情报 (Manual Input)", expanded=False):
        with st.form("manual_input_form"):
            new_source = st.selectbox("来源", ["我的思考", "Twitter", "饭局/路边社", "研报"])
            new_title = st.text_input("标题/核心观点", placeholder="例如：我觉得铜价要涨，库存太低了")
            new_tags = st.multiselect("标签", ["#Macro", "#Semi", "#Crypto", "#Energy", "#Idea"])
            new_surprise = st.slider("惊奇指数", 1, 5, 3)
            new_summary = st.text_area("详细内容/原文")
            
            submitted = st.form_submit_button("📥 录入中台")
            if submitted and new_title:
                # 构造新数据
                new_item = {
                    "id": f"MANUAL_{int(time.time())}",
                    "title": new_title,
                    "time": datetime.now().strftime("%H:%M"),
                    "tags": new_tags,
                    "surprise": new_surprise,
                    "status": "Wait",
                    "source": new_source,
                    "summary": new_summary,
                    "investigation": None # 还没侦查
                }
                # 插入到列表最前面
                st.session_state['news_stream'].insert(0, new_item)
                st.toast("✅ 情报已录入！请在雷达查看。", icon="🎉")


# ==========================================
# 页面 1: 情报雷达 (Radar)
# ==========================================
if page == "📡 1. 情报雷达 (Radar)":
    st.title("📡 全球情报雷达 (Global Intelligence Radar)")
    
    # 顶部统计
    count = len(st.session_state['news_stream'])
    c1, c2, c3 = st.columns(3)
    c1.metric("情报流总数", f"{count} 条")
    c2.metric("待侦查案件", f"{len([x for x in st.session_state['news_stream'] if x['status']=='Wait'])} 条")
    
    st.divider()
    
    # 渲染列表
    for index, row in enumerate(st.session_state['news_stream']):
        with st.container():
            cols = st.columns([1, 1, 4, 1, 1.5])
            cols[0].text(row['time'])
            cols[1].caption(row['source'])
            cols[2].markdown(f"**{row['title']}**")
            cols[3].markdown("⭐" * row['surprise'])
            
            # 状态按钮
            if row['status'] == "Wait":
                if cols[4].button("🔍 启动侦查", key=f"btn_{row['id']}"):
                    st.session_state['current_case_id'] = row['id']
                    st.toast(f"案件 {row['id']} 已移交侦探！", icon="🕵️")
                    # 提示用户手动切换（Streamlit自动跳转比较复杂，先用提示）
                    st.info("请点击左侧导航栏进入 **'2. 侦探工作室'**")
            else:
                cols[4].success("已结案")
            
            st.markdown("---")


# ==========================================
# 页面 2: 侦探工作室 (Detective)
# ==========================================
elif page == "🕵️ 2. 侦探工作室 (Detective)":
    st.title("🕵️ 侦探工作室 (Investigation Room)")
    
    case_id = st.session_state['current_case_id']
    
    if not case_id:
        st.warning("⚠️ 当前没有选中任何案件。请先去 '情报雷达' 选择一条线索。")
        st.stop()
    
    # 找到当前案件的数据
    current_case = next((item for item in st.session_state['news_stream'] if item["id"] == case_id), None)
    
    # 左侧显示原始线索
    with st.sidebar:
        st.subheader("📁 原始档案")
        st.info(f"**标题:** {current_case['title']}")
        st.write(f"**来源:** {current_case['source']}")
        st.write(f"**原文:** {current_case['summary']}")
    
    # 主界面：侦查过程
    st.header(f"案件侦查: {current_case['title']}")
    
    if current_case['investigation']:
        st.success("✅ 此案件已完成侦查报告。")
        st.json(current_case['investigation'])
        st.info("👉 请点击左侧导航栏进入 **'3. 认知法庭'** 进行审判")
    else:
        st.markdown("""
        > **侦探任务：**
        > 1. 核实信息源头真实性。
        > 2. 寻找旁证（交叉验证）。
        > 3. 压力测试（攻击隐含假设）。
        """)
        
        # 模拟 AI 侦查的按钮
        if st.button("🚀 呼叫 AI 侦探开始调查 (Call Detective)"):
            with st.status("🕵️ 侦探正在行动...", expanded=True) as status:
                st.write("正在连接外部网络...")
                time.sleep(1)
                st.write("🔍 正在搜索 'Google Search' 验证关键词...")
                time.sleep(1)
                st.write("📉 正在比对宏观数据 (Fred Data)...")
                time.sleep(1)
                
                # --- 这里未来接入真正的 Search API ---
                # 现在我们模拟生成一份报告
                simulated_report = {
                    "verification": "HIGH_CONFIDENCE (多方信源确认)",
                    "risks": [
                        "⚠️ 宏观错配：降息预期与非农数据冲突",
                        "⚠️ 估值风险：当前股价已计入完美预期"
                    ],
                    "evidence": ["路透社报道确认", "Fred数据：10年期美债反弹"]
                }
                
                status.update(label="✅ 侦查完成！", state="complete", expanded=False)
            
            # 保存结果到 Session
            current_case['investigation'] = simulated_report
            current_case['status'] = "Investigated"
            st.rerun()


# ==========================================
# 页面 3: 认知法庭 (Court)
# ==========================================
elif page == "⚖️ 3. 认知法庭 (Court)":
    st.title("⚖️ 认知法庭 (The Courtroom)")
    
    case_id = st.session_state['current_case_id']
    if not case_id:
        st.warning("请先在雷达中选择案件，并完成侦查。")
        st.stop()

    current_case = next((item for item in st.session_state['news_stream'] if item["id"] == case_id), None)
    
    # 检查是否有侦查报告
    if not current_case.get('investigation'):
        st.error("⛔️ 该案件尚未经过侦探调查！法官拒绝开庭。请返回侦探工作室。")
        st.stop()

    # --- 构造法庭数据 ---
    # 这里把 Session 里的数据组装成法庭需要的 JSON
    court_data = {
        "meta_data": {"id": case_id, "surprise": current_case['surprise']},
        "structured_content": {"core_view": current_case['title'], "logic_chain": f"{current_case['summary']} -> 股价上涨"},
        "investigation_report": {"final_verdict": {"flag": "MACRO_RISK", "detective_summary": str(current_case['investigation']['risks'])}},
        "raw_data_snapshot": {"excerpt": current_case['summary']}
    }

    # --- 法庭 UI (简化版) ---
    col1, col2 = st.columns([2, 1])
    with col1:
        st.subheader("🎯 核心逻辑")
        st.info(court_data['structured_content']['logic_chain'])
    with col2:
        st.metric("风险警示", "宏观错配", delta="-High Risk", delta_color="inverse")
    
    st.divider()
    
    # --- 侦探呈堂证供 ---
    with st.expander("📂 查看侦探调查报告", expanded=True):
        st.write(current_case['investigation'])

    st.divider()
    
    # --- 董事会辩论 (DeepSeek) ---
    st.subheader("🧠 董事会辩论")
    selected_personas = st.multiselect("召唤董事会:", ["查理·芒格", "索罗斯", "疯狂散户"], default=["查理·芒格", "疯狂散户"])
    
    if st.button("🔴 开始辩论"):
        # --- 填你的 Key ---
        DEEPSEEK_API_KEY = "sk-f061ba878a8741da8f5ac206b75d4041" # <--- 🔴 填Key
        
        try:
            client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
            system_prompt = f"你是一场投资辩论的导演。基于以下侦探报告：{current_case['investigation']}，模拟{selected_personas}之间的激烈辩论。"
            
            with st.chat_message("assistant", avatar="🤖"):
                message_placeholder = st.empty()
                full_response = ""
                stream = client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": "开始辩论"}],
                    stream=True
                )
                for chunk in stream:
                    if chunk.choices[0].delta.content:
                        full_response += chunk.choices[0].delta.content
                        message_placeholder.markdown(full_response + "▌")
                message_placeholder.markdown(full_response)
        except Exception as e:
            st.error(f"Error: {e}")