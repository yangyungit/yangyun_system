import streamlit as st
from openai import OpenAI
import json
import utils

st.set_page_config(page_title="宏观监控台", page_icon="🌍", layout="wide")

st.title("🌍 全球宏观监控台 (Global Macro Monitor)")

# --- 0. 数据初始化 ---
if 'macro_stream' not in st.session_state:
    st.session_state['macro_stream'] = []

# 初始化仪表盘状态 (默认值)
if 'macro_status' not in st.session_state:
    st.session_state['macro_status'] = {
        "liquidity": "中性",
        "fed": "观望",
        "economy": "软着陆",
        "inflation": "粘性",
        "market": "震荡",   # 原叙事改为大盘状态
        "conclusion": "暂无数据，请运行AI校准..."
    }

# --- 1. 侧边栏：控制核心 ---
with st.sidebar:
    st.header("🎛️ 状态控制 (Status Control)")
    
    # === 核心功能：AI 自动校准 ===
    st.info("👇 让 AI 阅读情报流，自动判断当前水位。")
    
    if st.button("🤖 AI 自动校准 (Auto-Calibrate)", type="primary"):
        if not st.session_state['macro_stream']:
            st.error("情报流为空！请先去 Home 页投喂一些宏观数据。")
        else:
            try:
                # 1. 收集最近的 15 条情报
                recent_logs = st.session_state['macro_stream'][:15]
                context_text = "\n".join([f"- [{item.get('bias')}] {item['summary']} (Tags: {item.get('tags')})" for item in recent_logs])
                
                # 2. 构造 Prompt
                prompt = f"""
                你是宏观对冲基金的首席策略师。
                请根据以下【最近收集的宏观情报流】，推断当前的五维宏观状态。
                
                【情报流】：
                {context_text}
                
                【任务】：
                请分析上述线索，输出一个 JSON 对象（不要Markdown格式），包含以下字段的状态：
                1. "liquidity": [枯竭, 紧缩, 中性, 宽裕, 泛滥]
                2. "fed": [极鹰, 鹰派, 观望, 鸽派, 极鸽]
                3. "economy": [衰退, 放缓, 软着陆, 过热, 滞胀]
                4. "inflation": [通缩, 达标, 粘性, 反弹, 失控]
                5. "market": [崩盘, 脆弱, 震荡, 主升浪, 泡沫] (指大盘状态)
                6. "conclusion": 一句简练的定调 (50字以内)
                """
                
                # 3. 调用 AI
                client = OpenAI(api_key=st.secrets["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")
                with st.spinner("🧠 正在研读情报，推演宏观水位..."):
                    res = client.chat.completions.create(
                        model="deepseek-chat",
                        messages=[{"role": "user", "content": prompt}],
                        response_format={"type": "json_object"}
                    )
                    new_status = json.loads(res.choices[0].message.content)
                    
                    # 4. 更新状态
                    st.session_state['macro_status'] = new_status
                    st.success("校准完成！仪表盘已更新。")
                    
            except Exception as e:
                st.error(f"校准失败: {e}")

    st.divider()
    
    # === 手动微调 (Manual Override) ===
    st.caption("🛠️ 手动微调 (Manual Override)")
    with st.form("manual_update"):
        ms = st.session_state['macro_status']
        # 为了防止 key error，做个容错
        s_liq = st.text_input("💧 流动性", value=ms.get('liquidity', '中性'))
        s_fed = st.text_input("🏛️ 美联储", value=ms.get('fed', '观望'))
        s_eco = st.text_input("📉 经济状况", value=ms.get('economy', '软着陆'))
        s_inf = st.text_input("🔥 通胀情况", value=ms.get('inflation', '粘性'))
        s_mkt = st.text_input("📊 大盘状态", value=ms.get('market', '震荡'))
        s_con = st.text_area("🚩 最终定调", value=ms.get('conclusion', ''))
        
        if st.form_submit_button("💾 强制更新"):
            st.session_state['macro_status'] = {
                "liquidity": s_liq, "fed": s_fed, "economy": s_eco, 
                "inflation": s_inf, "market": s_mkt, "conclusion": s_con
            }
            st.rerun()

# --- 2. 顶部：五维仪表盘 (Auto Dashboard) ---
ms = st.session_state['macro_status']

# CSS 美化
st.markdown("""
<style>
div[data-testid="metric-container"] {
    background-color: #1e1e1e;
    border: 1px solid #333;
    padding: 10px 0px 10px 20px;
    border-radius: 8px;
}
</style>
""", unsafe_allow_html=True)

# 渲染指标
cols = st.columns(5)
metrics = [
    ("💧 流动性", ms.get('liquidity', '-')),
    ("🏛️ 美联储", ms.get('fed', '-')),
    ("📉 经济", ms.get('economy', '-')),
    ("🔥 通胀", ms.get('inflation', '-')),
    ("📊 大盘状态", ms.get('market', '-')) # 这里的 Key 换成了 market
]

for col, (label, value) in zip(cols, metrics):
    col.metric(label, value)

# 渲染核心结论 (带有状态指示色)
status_color = "blue"
if "滞胀" in ms.get('conclusion', '') or "衰退" in ms.get('conclusion', ''):
    status_color = "red"
elif "复苏" in ms.get('conclusion', ''):
    status_color = "green"

st.markdown(f"""
<div style="background-color:rgba(255,255,255,0.05); padding:15px; border-radius:8px; border-left:5px solid {status_color}; margin-top:10px;">
    <h4 style="margin:0; padding:0;">🚩 当前宏观定调</h4>
    <p style="margin:5px 0 0 0; font-size:1.1em;">{ms.get('conclusion', '等待校准...')}</p>
</div>
""", unsafe_allow_html=True)

# --- 3. 宏观情报流列表 (The Stream) ---
st.write("")
st.write("")
st.subheader(f"📡 原始信号流 ({len(st.session_state['macro_stream'])})")
st.caption("下方数据为 AI 校准的依据来源")

# 表头
c1, c2, c3, c4 = st.columns([1, 1, 5, 2])
c1.markdown("**时间**")
c2.markdown("**偏向**")
c3.markdown("**摘要**")
c4.markdown("**标签**")
st.divider()

# 列表内容
if not st.session_state['macro_stream']:
    st.info("📭 暂无数据。请前往 Home 页面投喂情报。")

for item in st.session_state['macro_stream']:
    c1, c2, c3, c4 = st.columns([1, 1, 5, 2])
    
    c1.text(item['time'])
    
    bias = item.get('bias', '中性')
    color = "grey"
    if bias == '利多': color = ":green"
    elif bias == '利空': color = ":red"
    elif bias == '结构性': color = ":orange"
    
    c2.markdown(f"{color}[{bias}]")
    c3.write(item['summary'])
    
    tags = item.get('tags', [])
    c4.caption(" ".join([f"`{t}`" for t in tags]))
    
    st.markdown("<div style='margin-bottom:5px'></div>", unsafe_allow_html=True) # 微调行间距

# 底部功能
st.write("")
if st.button("🗑️ 清空记录"):
    st.session_state['macro_stream'] = []
    st.rerun()