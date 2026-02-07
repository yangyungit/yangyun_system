import streamlit as st
from openai import OpenAI
import utils 

st.set_page_config(page_title="侦探工作室", page_icon="🕵️", layout="wide")

try:
    DEEPSEEK_API_KEY = st.secrets["DEEPSEEK_API_KEY"]
except FileNotFoundError:
    st.error("密钥未配置！")
    st.stop()

BASE_URL = "https://api.deepseek.com"

if 'current_case_id' not in st.session_state or not st.session_state['current_case_id']:
    st.warning("⚠️ 请先在 'Radar' 页面选择一个案件。")
    st.stop()

case_id = st.session_state['current_case_id']
current_case = next((x for x in st.session_state['news_stream'] if x['id'] == case_id), None)

st.title(f"🕵️ 案件侦查: {current_case['title']}")

# --- 1. 宏观天眼 (Macro Eye) - 适配 V10.0 ---
# 读取最新的 macro_status (五维状态)
if 'macro_status' not in st.session_state:
    st.warning("⚠️ 宏观舰桥未初始化！侦探将盲目办案。请先去 'Macro' 页面进行 AI 校准。")
    macro_context_str = "【宏观数据缺失】默认假设：中性环境。"
else:
    ms = st.session_state['macro_status']
    # 组装给 AI 看的 Prompt
    macro_context_str = f"""
    🌍 **当前宏观五维状态 (Macro Dashboard)**
    ---------------------------
    1. 💧 流动性: {ms.get('liquidity')}
    2. 🏛️ 美联储: {ms.get('fed')}
    3. 📉 经济: {ms.get('economy')}
    4. 🔥 通胀: {ms.get('inflation')}
    5. 📊 大盘状态: {ms.get('market')}
    ---------------------------
    🚩 **宏观定调:** {ms.get('conclusion')}
    
    💡 **侦查原则：** 个股逻辑必须服从于【大盘状态】与【流动性】。逆势交易需有极高门槛。
    """
    
    # 界面展示
    color = "green" if "复苏" in ms.get('conclusion', '') else "red"
    with st.expander(f"🌍 当下宏观环境: {ms.get('conclusion')} (点击展开)", expanded=False):
        st.markdown(f":{color}[{macro_context_str}]")

# --- 辅助函数 ---
def smart_search(client, query, context=""):
    keyword_prompt = f"基于任务'{query}'，生成2个英文搜索关键词。"
    try:
        kw_res = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": keyword_prompt}],
            max_tokens=30
        )
        keywords = kw_res.choices[0].message.content.strip()
    except: keywords = query 
    return utils.search_web(keywords, max_results=3), keywords

# --- 侧边栏：技术面与基本面工具 ---
with st.sidebar:
    st.subheader("📁 原始档案")
    st.info(f"ID: {case_id}")
    st.write(f"**原文:**\n{current_case['summary']}")
    st.divider()
    
    # === 标的扫描仪 ===
    with st.expander("🔬 标的深度扫描 (Tech+Fund)", expanded=True):
        ticker = st.text_input("代码 (NVDA, BTC-USD):", placeholder="NVDA")
        
        if st.button("📥 扫描并入库", type="primary"):
            if not ticker:
                st.warning("请输入代码")
            else:
                with st.spinner(f"正在分析 {ticker} 的量价结构..."):
                    # 1. 获取增强版数据
                    analysis_report = utils.get_stock_analysis(ticker.upper())
                    
                    # 2. AI 短评
                    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=BASE_URL)
                    mini_prompt = f"""
                    宏观环境：{macro_context_str}
                    标的数据：{analysis_report}
                    
                    请用一句话犀利点评：
                    1. 技术面是多头还是空头？
                    2. 在当前宏观下，这个估值是否合理？
                    """
                    try:
                        res = client.chat.completions.create(
                            model="deepseek-chat", messages=[{"role": "user", "content": mini_prompt}]
                        )
                        comment = res.choices[0].message.content
                    except: comment = "已扫描"

                    # 3. 存证
                    evidence_block = (
                        "\n---\n"
                        f"#### 📊 技术与估值扫描: {ticker.upper()}\n"
                        "```yaml\n"
                        f"{analysis_report}\n"
                        "```\n"
                        f"**🕵️ AI 综合点评:** {comment}\n"
                    )
                    
                    if not current_case.get('investigation'):
                        current_case['investigation'] = "### 📂 侦查档案初始化\n"
                    current_case['investigation'] += evidence_block
                    utils.save_data(st.session_state['news_stream'],"radar_data")
                    st.toast(f"{ticker} 技术面数据已入库！", icon="📈")
                    st.rerun()

    st.divider()
    if st.button("🗑️ 重置侦查"):
        current_case['investigation'] = None
        utils.save_data(st.session_state['news_stream'],"radar_data")
        st.rerun()

# --- 主界面 ---

if not current_case.get('investigation'):
    st.markdown("### 🚀 全维侦查启动")
    st.info("本次侦查将整合：1.宏观背景 2.逻辑验证 3.技术形态")
    
    use_internet = st.checkbox("启用联网搜索", value=True)
    
    if st.button("开始初次侦查"):
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=BASE_URL)
        status_box = st.status("🕵️ 正在构建全维证据链...", expanded=True)
        
        # A. 联网
        search_context = ""
        if use_internet:
            status_box.write("🌐 全网情报检索...")
            res, kws = smart_search(client, current_case['title'], current_case['summary'])
            search_context = f"【联网情报】:\n{res}"
        
        # B. 核心 Prompt (注入了新的五维宏观)
        detective_prompt = f"""
        你是一个拥有全局视野的对冲基金研究员。
        
        【当前宏观天气】：
        {macro_context_str}
        
        【案件线索】：{current_case['title']}
        【详情】：{current_case['summary']}
        {search_context}
        
        请严格按照以下结构建立档案：
        
        ### 1. 宏观顺势检测 (Macro Check)
        * **关键矛盾:** 当前宏观定调为“{ms.get('conclusion', '未知')}”，该交易逻辑是否顺应此趋势？
        * **流动性匹配:** 当前流动性“{ms.get('liquidity')}”，是否支持该资产的估值扩张？
        
        ### 2. 逻辑与事实核查 (Logic Check)
        * **核心驱动:** ...
        * **证伪节点:** ...
        
        ### 3. 风险与反身性 (Risk & Reflexivity)
        * **拥挤度分析:** 这是一个共识交易吗？
        * **魔鬼代言人:** 假设我们错了，最可能是因为什么被忽视了？
        
        ### 4. 待查硬数据
        * 需要去验证的 K 线形态或财务指标。
        """
        
        status_box.write("🧠 深度思考中...")
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": detective_prompt}]
        )
        
        current_case['investigation'] = response.choices[0].message.content
        utils.save_data(st.session_state['news_stream'],"radar_data")
        status_box.update(label="✅ 完成", state="complete")
        st.rerun()

else:
    # 展示现有报告
    with st.container(border=True):
        st.markdown(current_case['investigation'])
    
    st.divider()
    
    # === 质检环节 ===
    st.subheader("🛡️ 补充侦查与质检 (QA Gate)")
    
    tab1, tab2 = st.tabs(["🔎 自由追查", "✅ 5D 投研质检清单"])
    
    with tab1:
        col_input, col_btn = st.columns([4, 1])
        with col_input:
            follow_up_query = st.text_input("输入追查指令:", placeholder="查一下 AMD 的作为竞品情况...")
        with col_btn:
            st.write("") 
            st.write("")
            if st.button("🚀 追查", type="primary"):
                client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=BASE_URL)
                with st.status("🕵️ 追查中..."):
                    search_res, _ = smart_search(client, follow_up_query, context=current_case['investigation'])
                    follow_up_prompt = f"【新指令】{follow_up_query}\n【情报】{search_res}\n请补充侦查笔记。"
                    res = client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": follow_up_prompt}])
                    current_case['investigation'] += f"\n\n#### 🕵️ 补充侦查: {follow_up_query}\n{res.choices[0].message.content}"
                    utils.save_data(st.session_state['news_stream'],"radar_data")
                    st.rerun()
    
    with tab2:
        st.info("🛑 在移交法庭前，请务必进行【事前尸检】。")
        
        checks = {
            "check_macro": "1. 宏观顺势窗口检测 (流动性/大盘是否配合？)",
            "check_truth": "2. 事件真实性确认 (已排除谣言/循环论证？)",
            "check_fund": "3. 标的基本面检测 (估值/供需逻辑自洽？)",
            "check_tech": "4. 技术面形态检测 (均线/量能/背离确认？)",
            "check_devil": "5. 魔鬼代言人检测 (已考虑最坏情况？)"
        }
        
        all_checked = True
        cols = st.columns(2)
        for i, (key, label) in enumerate(checks.items()):
            state_key = f"{case_id}_{key}"
            with cols[i % 2]:
                if not st.checkbox(label, key=state_key):
                    all_checked = False
        
        st.divider()
        if all_checked:
            st.success("✅ 质检通过！证据链完整，准予开庭。")
            st.markdown("👉 **下一步：** 点击左侧 `Court` 进入董事会辩论。")
        else:
            st.warning("⚠️ 警告：存在未通过的质检项。")