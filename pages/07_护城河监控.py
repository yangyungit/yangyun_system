"""
第 7 个页面：护城河衰减监控。
完全独立于现有 6 个页面（不读 utils.py，不连 Google Sheets）。
"""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils_fundamentals import (
    EARNINGS_CALL_KEYWORDS,
    MOAT_POOL,
    add_manual_note,
    compute_all_signals,
    get_db,
    list_manual_notes,
    read_quarterly,
    record_signals,
    should_sell,
    upsert_call_keyword,
)
from scrapers.fetch_financials import fetch_spy_yoy_series, sync_all_pool, sync_ticker

st.set_page_config(page_title="护城河监控", layout="wide")
st.title("🏰 护城河监控 — Moat Decay Tracker")
st.caption("跟踪 10 只龙头股的 5 类衰减信号 — 卖出比买入难 10 倍")

# 确保 DB 存在（首次访问建表 + 注入池子）
_conn = get_db()
_conn.close()

# === 侧边栏：数据维护 ===
with st.sidebar:
    st.header("数据维护")
    if st.button("🔄 刷新全池子", help="跑 FMP/EDGAR/yfinance 三源，约 30s"):
        with st.spinner("拉取中..."):
            results = sync_all_pool()
        for t, ok, msg in results:
            st.write(f"{'✅' if ok else '❌'} {msg}")
    selected_one = st.selectbox("单只刷新", [""] + MOAT_POOL)
    if selected_one and st.button("🔄 刷新这只"):
        ok, msg = sync_ticker(selected_one)
        st.write(f"{'✅' if ok else '❌'} {msg}")

# === 顶部：池子总览 ===
st.subheader("池子总览")

spy_yoy = fetch_spy_yoy_series()
overview_rows = []
sell_alerts = []

for t in MOAT_POOL:
    qs = read_quarterly(t, n=4)
    triggered = compute_all_signals(t, spy_yoy)
    record_signals(t, triggered)
    sell, reason = should_sell(triggered)
    if sell:
        sell_alerts.append((t, reason))

    latest_gm = "—"
    gm_trend = "—"
    if qs:
        if qs[0].get("gross_margin") is not None:
            latest_gm = f"{qs[0]['gross_margin'] * 100:.1f}%"
        valid_gms = [q for q in qs if q.get("gross_margin") is not None]
        if valid_gms:
            gm_trend = " → ".join(
                f"{q['gross_margin'] * 100:.1f}" for q in reversed(valid_gms)
            )

    overview_rows.append({
        "股票": t,
        "最新毛利率": latest_gm,
        "毛利率趋势（旧→新）": gm_trend,
        "触发信号数": len(triggered),
        "卖出提示": "🚨" if sell else "",
    })

if sell_alerts:
    alert_str = ", ".join(f"{t}（{r}）" for t, r in sell_alerts)
    st.error(f"⚠️ 卖出提示：{alert_str}")

st.dataframe(pd.DataFrame(overview_rows), use_container_width=True, hide_index=True)

# === 单股详情 ===
st.divider()
st.subheader("单股深度")
ticker = st.selectbox("选择股票", MOAT_POOL)

col_data, col_signals = st.columns([3, 2])

with col_data:
    st.write(f"### {ticker} — 最近 8 季度财务")
    qs = read_quarterly(ticker, n=8)
    if qs:
        df = pd.DataFrame(qs)
        display_cols = [
            c for c in [
                "fiscal_quarter", "period_end", "revenue", "gross_margin",
                "rd_pct", "revenue_yoy", "customer_top5_pct", "data_source",
            ]
            if c in df.columns
        ]
        df = df[display_cols].copy()
        for col in ["gross_margin", "rd_pct", "revenue_yoy"]:
            if col in df.columns:
                df[col] = (df[col] * 100).round(2)
        st.dataframe(df, use_container_width=True, hide_index=True)

        # 毛利率折线图
        chart_df = df[["fiscal_quarter", "gross_margin"]].dropna().iloc[::-1]
        if not chart_df.empty:
            st.line_chart(chart_df.set_index("fiscal_quarter"), y="gross_margin")
    else:
        st.info(f"{ticker} 暂无数据，请先在侧边栏点「刷新这只」")

with col_signals:
    st.write(f"### {ticker} — 5 类信号")
    triggered = compute_all_signals(ticker, spy_yoy)
    signal_names = {
        "gross_margin": "1. 毛利率下行",
        "revenue_lag": "2. 营收跑输市场",
        "call_keyword": "3. 电话会议关键词",
        "product_delay": "4. 产品延期",
        "customer_concentration": "5. 客户集中度异常",
    }
    for stype, name in signal_names.items():
        sig = next((s for s in triggered if s["signal_type"] == stype), None)
        if sig:
            st.error(f"🔴 {name}（severity {sig['severity']}）\n\n{sig['evidence']}")
        else:
            st.success(f"🟢 {name} 未触发")

# === 手工录入 ===
st.divider()
st.subheader("手工录入")
tab_kw, tab_delay, tab_note = st.tabs(["电话会议关键词", "产品延期", "一般备注"])

with tab_kw:
    fq_input = st.text_input("财季（如 2026Q1）", key="kw_fq")
    kw_input = st.selectbox("关键词", EARNINGS_CALL_KEYWORDS, key="kw_keyword")
    cnt_input = st.number_input("出现次数", min_value=0, value=0, key="kw_cnt")
    snip_input = st.text_area("上下文片段", key="kw_snip")
    if st.button("保存关键词记录"):
        if fq_input:
            upsert_call_keyword(ticker, fq_input, kw_input, int(cnt_input), snip_input)
            st.success("已保存")
        else:
            st.warning("请先填写财季")

with tab_delay:
    delay_content = st.text_area("延期描述（保存即触发 severity 3 卖出提示）", key="delay_content")
    if st.button("标记产品延期"):
        if delay_content:
            add_manual_note(ticker, "product_delay", delay_content)
            st.success("已标记 — 该股下次计算信号时触发 severity 3")
        else:
            st.warning("请填写延期描述")

with tab_note:
    note_type_input = st.selectbox(
        "类型", ["general", "mgmt_change", "competitive_event"], key="note_type"
    )
    note_content = st.text_area("内容", key="note_content")
    if st.button("保存备注"):
        if note_content:
            add_manual_note(ticker, note_type_input, note_content)
            st.success("已保存")
        else:
            st.warning("请填写内容")

# === 历史备注 ===
notes = list_manual_notes(ticker, days=180)
if notes:
    st.write(f"### {ticker} — 最近 6 个月备注")
    notes_df = pd.DataFrame(notes)
    show_cols = [c for c in ["note_date", "note_type", "content"] if c in notes_df.columns]
    st.dataframe(notes_df[show_cols], use_container_width=True, hide_index=True)
