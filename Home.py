import streamlit as st
import utils
if not utils.check_password():
    st.stop()  # 密码不对，直接停止运行下面的代码

from openai import OpenAI
from datetime import datetime

st.set_page_config(page_title="情报投喂口", page_icon="⚡️", layout="wide")

# --- 1. 全局数据初始化 ---
# 必须带 sheet_name 参数
if 'news_stream' not in st.session_state:
    st.session_state['news_stream'] = utils.load_data(sheet_name="radar_data")

if 'macro_stream' not in st.session_state:
    st.session_state['macro_stream'] = utils.load_data(sheet_name="macro_stream")

# --- 2. 界面设计 ---
st.title("⚡️ 全球情报投喂口 (Global Intel Port)")
st.caption("🚀 工作流：在 Gemini/ChatGPT 思考 -> 将精华结论粘贴至此 -> 系统自动分发归档")

# --- 3. 核心投喂区 ---
with st.container(border=True):
    st.markdown("### 📥 粘贴情报/观点")
    
    # 巨大的输入框
    with st.form("injection_form", clear_on_submit=True):
        raw_text = st.text_area(
            "在此粘贴任何内容 (宏观分析、个股研报、突发新闻...)", 
            height=300, 
            placeholder="例如：\n1. 刚才 Gemini 说现在的通胀结构很像 70 年代...\n2. 或者是粘贴一段 NVDA 的财报摘要..."
        )
        
        col_submit, col_source = st.columns([1, 4])
        with col_submit:
            submitted = st.form_submit_button("🚀 立即分发", type="primary")
        
        if submitted and raw_text:
            try:
                # 获取 Key
                api_key = st.secrets["DEEPSEEK_API_KEY"]
                client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
                
                with st.spinner("🧠 正在识别情报属性 (Macro vs Radar)..."):
                    # 调用分发器
                    data = utils.auto_dispatch(client, raw_text)
                    
                    if "error" in data:
                        st.error("分发失败，请重试")
                    else:
                        data['time'] = datetime.now().strftime("%m-%d %H:%M")
                        
                        # === 分支 A: 宏观情报 ===
                        if data['category'] == 'MACRO':
                            st.session_state['macro_stream'].insert(0, data)
                            
                            # 反馈卡片
                            st.success("✅ 已归档至【宏观作战室】")
                            st.markdown(f"""
                            **摘要:** {data['summary']}  
                            **标签:** `{data['tags']}`  
                            **偏向:** {data['bias']}
                            """)
                        
                        # === 分支 B: 微观/雷达情报 ===
                        elif data['category'] == 'RADAR':
                            radar_item = {
                                "id": f"EXT/{int(datetime.now().timestamp())}",
                                "title": data['summary'],
                                "time": data['time'],
                                "tags": data['tags'],
                                "surprise": 3, # 默认为中等惊奇
                                "source": "External Intel", # 标记来源
                                "summary": raw_text, # 保留你粘贴的全文
                                "investigation": None
                            }
                            st.session_state['news_stream'].insert(0, radar_item)
                            utils.save_data(st.session_state['news_stream'], "radar_data")
                            
                            # 反馈卡片
                            st.success("✅ 已归档至【情报雷达】")
                            st.markdown(f"""
                            **标的:** {data['summary']}  
                            **标签:** `{data['tags']}`
                            """)
                            
            except Exception as e:
                st.error(f"处理错误: {e}")

# --- 4. 最近入库记录 (Recent Logs) ---
st.divider()
st.subheader("🗄️ 最近入库记录")

c1, c2 = st.columns(2)

with c1:
    st.markdown("#### 🌍 宏观库 (Latest 3)")
    if st.session_state['macro_stream']:
        for item in st.session_state['macro_stream'][:3]:
            # ✅ 安全写法：用 .get() 防止报错
            time_str = item.get('time', 'Unknown Time')
            summary_str = item.get('summary', 'No Summary')
            st.code(f"[{time_str}] {summary_str}", language="text")
    else:
        st.caption("暂无数据")

with c2:
    st.markdown("#### 📡 雷达库 (Latest 3)")
    if st.session_state['news_stream']:
        for item in st.session_state['news_stream'][:3]:
            # ✅ 安全写法
            time_str = item.get('time', 'Unknown Time')
            title_str = item.get('title', 'No Title')
            st.code(f"[{time_str}] {title_str}", language="text")
    else:
        st.caption("暂无数据")