import streamlit as st
import utils  # <--- 核心：引入我们刚才建的工具箱

st.set_page_config(
    page_title="养云资产·投研中台",
    page_icon="🏯",
    layout="wide"
)

# --- 核心：全局数据初始化 ---
# 无论你从哪个页面进入，这段代码都会确保数据不会丢失
if 'news_stream' not in st.session_state:
    # 1. 尝试从本地 JSON 文件加载数据
    local_data = utils.load_data()
    
    if local_data:
        # 如果本地有存档，直接读取存档
        st.session_state['news_stream'] = local_data
        st.toast("已加载本地历史数据", icon="📂")
    else:
        # 2. 如果是第一次运行（本地没文件），初始化默认数据
        st.session_state['news_stream'] = [
            {
                "id": "NVDA_02", 
                "title": "大摩翻多 NVDA 至 $1600，良率瓶颈突破", 
                "time": "10:30", 
                "tags": ["#技术突破", "#宏观"], 
                "surprise": 4, 
                "source": "Bloomberg", 
                "summary": "台积电 CoWoS 良率由 40% 升至 80%，Blackwell 发货延迟风险解除。",
                "investigation": None
            },
            {
                "id": "GOLD_01", 
                "title": "金铜比突破历史高位，衰退信号亮起", 
                "time": "09:45", 
                "tags": ["#大宗商品", "#背离", "#泡沫预警"], 
                "surprise": 5, 
                "source": "ZeroHedge", 
                "summary": "铜价因需求衰退下跌，金价因避险上涨，两者背离程度达到 2008 年水平。",
                "investigation": None
            }
        ]
        # 马上保存一次，生成 json 文件
        utils.save_data(st.session_state['news_stream'])

if 'current_case_id' not in st.session_state:
    st.session_state['current_case_id'] = None

# --- 首页 UI ---
st.title("🏯 养云资产·智能投研系统")
st.markdown("""
### 👋 欢迎回来，指挥官。

系统运行状态：**🟢 Online** (已连接本地数据库)

请从左侧侧边栏选择工作流：
1. **📡 Radar**: 全球情报监控与去噪
2. **🕵️ Detective**: AI 深度侦查与验证
3. **⚖️ Court**: 认知法庭与决策归档

---
*Powered by DeepSeek-V3 & Streamlit*
""")