import streamlit as st
import utils
import pandas as pd
from datetime import datetime
from github import Github

st.set_page_config(page_title="Moltbot 中台", page_icon="🧠", layout="wide")

st.title("🧠 Moltbot 情报中台 (Command Center)")

# --- 投喂区 ---
with st.container():
    st.subheader("⚡️ 快速投喂 (Quick Feed)")
    col1, col2 = st.columns([1, 2])
    with col1:
        input_url = st.text_input("🔗 来源链接 (可选)", placeholder="https://...")
    with col2:
        raw_text = st.text_area("📝 情报文本", height=200, placeholder="粘贴文本，AI 将自动识别意图并拆分入库...")

    if st.button("🚀 启动 AI 分析", type="primary"):
        if not raw_text and not input_url:
            st.warning("⚠️ 内容不能为空")
            st.stop()
            
        status_box = st.status("🧠 AI 正在介入...", expanded=True)
        
        try:
            content = raw_text if raw_text else f"分析链接: {input_url}"
            
            # 1. 调用 AI (返回列表)
            items = utils.auto_dispatch(None, content)
            if isinstance(items, dict): items = [items] # 兼容性保护
            
            status_box.write(f"🔍 AI 识别出 {len(items)} 个情报点...")
            
            results_log = []
            
            # 2. 循环入库
            for item in items:
                category = item.get('category', 'MACRO')
                title = item.get('title', '无标题')
                
                # 补全信息
                item['date'] = datetime.now().strftime("%Y-%m-%d")
                item['url'] = input_url if input_url else "Web Console"
                
                # 路由
                target_sheet = "macro_stream" if category == "MACRO" else "radar_data"
                
                # 写入
                status_box.write(f"🔀 正在将【{title}】写入 {target_sheet}...")
                current_data = utils.load_data(target_sheet)
                current_data.insert(0, item)
                utils.save_data(current_data, target_sheet)
                
                results_log.append(item)
            
            status_box.update(label="✅ 全部入库成功！", state="complete", expanded=False)
            
            # 3. 展示结果
            for res in results_log:
                st.success(f"已归档至【{res.get('category')}】: {res.get('title')}")
                with st.expander("查看详情"):
                    st.json(res)
                
        except Exception as e:
            status_box.update(label="❌ 处理失败", state="error")
            st.error(f"错误详情: {e}")

st.divider()

# --- 数据库状态 ---
try:
    c1, c2 = st.columns(2)
    c1.metric("🌍 宏观库", f"{len(utils.load_data('macro_stream'))} 条")
    c2.metric("📡 雷达库", f"{len(utils.load_data('radar_data'))} 条")
except:
    st.caption("数据库连接中...")
