"""
三源串行：FMP 主 → EDGAR 校核（V2）→ yfinance 兜底。
不调用 base_scraper.py —— 那是情报系统接口，与基本面无关。
"""

import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils_fundamentals import upsert_quarterly, MOAT_POOL

FMP_BASE = "https://financialmodelingprep.com/api/v3"


def _get_fmp_key() -> str | None:
    # Streamlit 环境才能读 st.secrets，非 Streamlit 环境走 env
    try:
        import streamlit as st
        return st.secrets.get("FMP_API_KEY") or os.environ.get("FMP_API_KEY")
    except Exception:
        return os.environ.get("FMP_API_KEY")


def fetch_from_fmp(ticker: str, limit: int = 12) -> list[dict] | None:
    """拉 income-statement + key-metrics 季度数据并合并。"""
    key = _get_fmp_key()
    if not key:
        print(f"[FMP] 无 API key，跳过 {ticker}")
        return None
    try:
        is_url = f"{FMP_BASE}/income-statement/{ticker}?period=quarter&limit={limit}&apikey={key}"
        km_url = f"{FMP_BASE}/key-metrics/{ticker}?period=quarter&limit={limit}&apikey={key}"
        is_resp = requests.get(is_url, timeout=15)
        km_resp = requests.get(km_url, timeout=15)

        if is_resp.status_code != 200:
            print(f"[FMP] {ticker} income-statement HTTP {is_resp.status_code}")
            return None
        is_data = is_resp.json()
        if not isinstance(is_data, list) or not is_data:
            print(f"[FMP] {ticker} income-statement 返回空")
            return None

        km_data = km_resp.json() if km_resp.status_code == 200 else []
        # FMP key-metrics period 字段格式："Q1" + calendarYear
        km_by_period: dict[str, dict] = {}
        if isinstance(km_data, list):
            for x in km_data:
                k = f"{x.get('calendarYear', '')}{x.get('period', '')}"
                km_by_period[k] = x

        merged = []
        for row in is_data:
            cal_year = str(row.get("calendarYear", ""))
            period = row.get("period", "")           # 'Q1' / 'Q2' / ...
            fq = f"{cal_year}{period}"               # '2026Q1'
            km = km_by_period.get(f"{cal_year}{period}", {})
            merged.append({
                "fiscal_quarter": fq,
                "period_end": row.get("date"),
                "revenue": row.get("revenue"),
                "cost_of_revenue": row.get("costOfRevenue"),
                "gross_profit": row.get("grossProfit"),
                "rd_expense": row.get("researchAndDevelopmentExpenses"),
                "operating_income": row.get("operatingIncome"),
                "net_income": row.get("netIncome"),
                "customer_top5_pct": None,  # FMP 无此字段
            })
        return merged

    except Exception as e:
        print(f"[FMP] {ticker} 拉取异常: {e}")
        return None


def fetch_from_edgar(ticker: str, limit: int = 12) -> list[dict] | None:
    """SEC EDGAR 兜底（V2 再做，MVP 直接 return None）。"""
    return None


def fetch_from_yfinance(ticker: str) -> list[dict] | None:
    """yfinance 最后兜底，拉 quarterly_financials DataFrame。"""
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        qf = tk.quarterly_financials
        if qf is None or qf.empty:
            print(f"[yfinance] {ticker} quarterly_financials 为空")
            return None
        result = []
        for col in qf.columns:
            try:
                period_end = col.strftime("%Y-%m-%d")
                year = col.year
                quarter = (col.month - 1) // 3 + 1
                row_data = qf[col]

                def safe_float(key):
                    val = row_data.get(key)
                    if val is None:
                        return None
                    try:
                        f = float(val)
                        return f if f != 0 else None
                    except Exception:
                        return None

                result.append({
                    "fiscal_quarter": f"{year}Q{quarter}",
                    "period_end": period_end,
                    "revenue": safe_float("Total Revenue"),
                    "cost_of_revenue": safe_float("Cost Of Revenue"),
                    "gross_profit": safe_float("Gross Profit"),
                    "rd_expense": safe_float("Research Development"),
                    "operating_income": safe_float("Operating Income"),
                    "net_income": safe_float("Net Income"),
                    "customer_top5_pct": None,
                })
            except Exception as row_e:
                print(f"[yfinance] {ticker} 解析单季度异常: {row_e}")
                continue
        return result if result else None

    except Exception as e:
        print(f"[yfinance] {ticker} 拉取异常: {e}")
        return None


def sync_ticker(ticker: str) -> tuple[bool, str]:
    """三源串行：FMP → EDGAR → yfinance，第一个成功就用。"""
    for src_name, fn in [
        ("fmp", fetch_from_fmp),
        ("edgar", fetch_from_edgar),
        ("yfinance", fetch_from_yfinance),
    ]:
        try:
            data = fn(ticker)
        except Exception as e:
            print(f"[{src_name}] {ticker} 调用异常: {e}")
            data = None

        if data:
            for row in data:
                try:
                    upsert_quarterly(ticker, row["fiscal_quarter"], row, source=src_name)
                except Exception as e:
                    print(f"[{src_name}] {ticker} {row.get('fiscal_quarter')} upsert 失败: {e}")
            return True, f"{ticker} 从 {src_name} 拉取 {len(data)} 季度"
    return False, f"{ticker} 三源全失败"


def sync_all_pool() -> list[tuple[str, bool, str]]:
    """跑遍 10 只池子，单只失败不影响其他。"""
    results = []
    for t in MOAT_POOL:
        try:
            ok, msg = sync_ticker(t)
        except Exception as e:
            ok, msg = False, f"{t} 整体异常: {e}"
        results.append((t, ok, msg))
        time.sleep(0.5)  # FMP 限速保护
    return results


def fetch_spy_yoy_series() -> dict:
    """用 SPY 价格 4 季度滚动同比代理市场增速，返回 {fiscal_quarter: yoy_pct}。
    V1 粗代理，V2 换行业 ETF。"""
    try:
        import yfinance as yf
        import pandas as pd
        spy = yf.Ticker("SPY")
        hist = spy.history(period="4y", interval="3mo")
        if hist is None or hist.empty:
            return {}
        close = hist["Close"]
        result = {}
        for i in range(4, len(close)):
            dt = close.index[i]
            prev_dt = close.index[i - 4]
            if close.iloc[i - 4] == 0:
                continue
            yoy = (close.iloc[i] - close.iloc[i - 4]) / abs(close.iloc[i - 4])
            year = dt.year
            quarter = (dt.month - 1) // 3 + 1
            fq = f"{year}Q{quarter}"
            result[fq] = float(yoy)
        return result
    except Exception as e:
        print(f"[SPY yoy] 计算失败: {e}")
        return {}
