"""
护城河监控的独立工具层 —— 不依赖 utils.py，不连 Google Sheets。
所有数据读写走 SQLite (data/fundamentals.db)。
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime, date, timedelta

DB_PATH = Path(__file__).parent / "data" / "fundamentals.db"

# === 常量（与 plan §1.3 同步，禁改）===
MOAT_POOL = [
    "AAPL", "JNJ", "COST", "LLY", "TJX",
    "WMT", "LMT", "GOOGL", "MNST", "TSLA",
]

GROSS_MARGIN_DECLINE_QUARTERS = 4
GROSS_MARGIN_DECLINE_THRESHOLD_PP = 2.0
REVENUE_LAG_QUARTERS = 2
REVENUE_LAG_RATIO = 0.5
EARNINGS_CALL_KEYWORDS = [
    "competitive pressure",
    "pricing pressure",
    "market share loss",
    "promotion",
    "discount",
]
EARNINGS_CALL_SURGE_RATIO = 1.5
CUSTOMER_CONCENTRATION_DELTA_PP = 5.0

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS companies (
    ticker TEXT PRIMARY KEY,
    name TEXT,
    sector TEXT,
    industry TEXT,
    pool_role TEXT DEFAULT 'core',
    added_at DATE DEFAULT CURRENT_DATE,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS quarterly_fundamentals (
    ticker TEXT NOT NULL,
    fiscal_quarter TEXT NOT NULL,
    period_end DATE,
    revenue REAL,
    cost_of_revenue REAL,
    gross_profit REAL,
    gross_margin REAL,
    rd_expense REAL,
    rd_pct REAL,
    operating_income REAL,
    net_income REAL,
    revenue_yoy REAL,
    revenue_qoq REAL,
    customer_top5_pct REAL,
    data_source TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, fiscal_quarter)
);

CREATE TABLE IF NOT EXISTS decay_signals (
    ticker TEXT NOT NULL,
    detected_at DATE NOT NULL,
    signal_type TEXT NOT NULL,
    severity INTEGER,
    evidence TEXT,
    manual_note TEXT,
    PRIMARY KEY (ticker, detected_at, signal_type)
);

CREATE TABLE IF NOT EXISTS earnings_call_keywords (
    ticker TEXT NOT NULL,
    fiscal_quarter TEXT NOT NULL,
    keyword TEXT NOT NULL,
    occurrence_count INTEGER DEFAULT 0,
    context_snippet TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, fiscal_quarter, keyword)
);

CREATE TABLE IF NOT EXISTS manual_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    note_date DATE DEFAULT CURRENT_DATE,
    note_type TEXT,
    content TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# === DB 基础设施 ===

def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    _seed_pool(conn)
    return conn


def _init_schema(conn: sqlite3.Connection):
    conn.executescript(_SCHEMA_SQL)
    conn.commit()


def _seed_pool(conn: sqlite3.Connection):
    for ticker in MOAT_POOL:
        conn.execute(
            "INSERT OR IGNORE INTO companies(ticker, pool_role) VALUES(?, 'core')",
            (ticker,),
        )
    conn.commit()


# === 季度数据读写 ===

def upsert_quarterly(ticker: str, fq: str, data: dict, source: str = "fmp"):
    """插入或更新季度财务数据，自动计算 gross_margin / rd_pct。
    revenue_yoy / revenue_qoq 由调用方传入或此处查库计算。"""
    conn = get_db()
    revenue = data.get("revenue")
    gross_profit = data.get("gross_profit")
    cost_of_revenue = data.get("cost_of_revenue")
    rd_expense = data.get("rd_expense")

    gross_margin = (gross_profit / revenue) if revenue and gross_profit else None
    rd_pct = (rd_expense / revenue) if revenue and rd_expense else None

    # 计算 revenue_yoy：找 4 季度前同 ticker 的 revenue
    revenue_yoy = data.get("revenue_yoy")
    revenue_qoq = data.get("revenue_qoq")

    if revenue and revenue_yoy is None:
        rows = conn.execute(
            "SELECT revenue, fiscal_quarter FROM quarterly_fundamentals "
            "WHERE ticker=? ORDER BY period_end DESC LIMIT 8",
            (ticker,),
        ).fetchall()
        # 找 4 季度前的记录（同 ticker，按 period_end 排）
        if len(rows) >= 4:
            prev_4q_revenue = rows[3]["revenue"]
            if prev_4q_revenue:
                revenue_yoy = (revenue - prev_4q_revenue) / abs(prev_4q_revenue)
        if len(rows) >= 1:
            prev_1q_revenue = rows[0]["revenue"]
            if prev_1q_revenue:
                revenue_qoq = (revenue - prev_1q_revenue) / abs(prev_1q_revenue)

    conn.execute(
        """
        INSERT INTO quarterly_fundamentals
            (ticker, fiscal_quarter, period_end, revenue, cost_of_revenue, gross_profit,
             gross_margin, rd_expense, rd_pct, operating_income, net_income,
             revenue_yoy, revenue_qoq, customer_top5_pct, data_source, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(ticker, fiscal_quarter) DO UPDATE SET
            period_end=excluded.period_end,
            revenue=excluded.revenue,
            cost_of_revenue=excluded.cost_of_revenue,
            gross_profit=excluded.gross_profit,
            gross_margin=excluded.gross_margin,
            rd_expense=excluded.rd_expense,
            rd_pct=excluded.rd_pct,
            operating_income=excluded.operating_income,
            net_income=excluded.net_income,
            revenue_yoy=excluded.revenue_yoy,
            revenue_qoq=excluded.revenue_qoq,
            customer_top5_pct=COALESCE(excluded.customer_top5_pct, quarterly_fundamentals.customer_top5_pct),
            data_source=excluded.data_source,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            ticker, fq, data.get("period_end"),
            revenue, cost_of_revenue, gross_profit, gross_margin,
            rd_expense, rd_pct,
            data.get("operating_income"), data.get("net_income"),
            revenue_yoy, revenue_qoq,
            data.get("customer_top5_pct"), source,
        ),
    )
    conn.commit()
    conn.close()


def read_quarterly(ticker: str, n: int = 8) -> list[dict]:
    """读最近 N 个季度，按 period_end DESC。"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM quarterly_fundamentals WHERE ticker=? ORDER BY period_end DESC LIMIT ?",
        (ticker, n),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# === 信号计算 ===

def check_gross_margin_signal(ticker: str) -> dict | None:
    """信号 1：连续 4 季度毛利率单调下降 + 累计降幅 ≥ 2pp"""
    quarters = read_quarterly(ticker, n=GROSS_MARGIN_DECLINE_QUARTERS)
    if len(quarters) < GROSS_MARGIN_DECLINE_QUARTERS:
        return None
    if any(q["gross_margin"] is None for q in quarters):
        return None
    # quarters[0] 是最新，reversed 得时间正序
    gms = [q["gross_margin"] for q in reversed(quarters)]
    monotone_down = all(gms[i] > gms[i + 1] for i in range(len(gms) - 1))
    total_decline_pp = (gms[0] - gms[-1]) * 100
    if monotone_down and total_decline_pp >= GROSS_MARGIN_DECLINE_THRESHOLD_PP:
        return {
            "signal_type": "gross_margin",
            "severity": 1,
            "evidence": (
                f"连续 {GROSS_MARGIN_DECLINE_QUARTERS} 季度毛利率: "
                f"{[round(g * 100, 2) for g in gms]}，"
                f"累计降幅 {round(total_decline_pp, 2)}pp"
            ),
        }
    return None


def check_revenue_lag_signal(ticker: str, spy_yoy_series: dict) -> dict | None:
    """信号 2：连续 2 季度 revenue_yoy < SPY_yoy × 0.5"""
    quarters = read_quarterly(ticker, n=REVENUE_LAG_QUARTERS)
    if len(quarters) < REVENUE_LAG_QUARTERS:
        return None
    lag_count = 0
    evidence_parts = []
    for q in quarters[:REVENUE_LAG_QUARTERS]:
        rev_yoy = q.get("revenue_yoy")
        if rev_yoy is None:
            continue
        spy_yoy = spy_yoy_series.get(q["fiscal_quarter"])
        if spy_yoy is None:
            continue
        threshold = spy_yoy * REVENUE_LAG_RATIO
        if rev_yoy < threshold:
            lag_count += 1
            evidence_parts.append(
                f"{q['fiscal_quarter']}: 公司 {round(rev_yoy*100,1)}% < SPY×0.5 {round(threshold*100,1)}%"
            )
    if lag_count >= REVENUE_LAG_QUARTERS:
        return {
            "signal_type": "revenue_lag",
            "severity": 1,
            "evidence": "; ".join(evidence_parts),
        }
    return None


def check_call_keyword_signal(ticker: str) -> dict | None:
    """信号 3：earnings_call_keywords 表中同比出现次数 ≥ +50% 的关键词"""
    conn = get_db()
    rows = conn.execute(
        "SELECT fiscal_quarter, keyword, occurrence_count FROM earnings_call_keywords "
        "WHERE ticker=? ORDER BY fiscal_quarter DESC",
        (ticker,),
    ).fetchall()
    conn.close()

    # 按关键词分组，找最近 2 个有记录的季度
    from collections import defaultdict
    by_kw: dict[str, list] = defaultdict(list)
    for r in rows:
        by_kw[r["keyword"]].append(r)

    triggered_kws = []
    for kw, kw_rows in by_kw.items():
        if len(kw_rows) < 2:
            continue
        latest = kw_rows[0]["occurrence_count"]
        prev = kw_rows[1]["occurrence_count"]
        if prev > 0 and latest / prev >= EARNINGS_CALL_SURGE_RATIO:
            triggered_kws.append(f'"{kw}"({prev}→{latest})')

    if not triggered_kws:
        return None
    severity = 2 if len(triggered_kws) >= 2 else 1
    return {
        "signal_type": "call_keyword",
        "severity": severity,
        "evidence": f"关键词同比 +50%: {', '.join(triggered_kws)}",
    }


def check_product_delay_signal(ticker: str) -> dict | None:
    """信号 4：manual_notes 中最近 90 天有 product_delay 记录"""
    conn = get_db()
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    row = conn.execute(
        "SELECT content, note_date FROM manual_notes "
        "WHERE ticker=? AND note_type='product_delay' AND note_date >= ? "
        "ORDER BY note_date DESC LIMIT 1",
        (ticker, cutoff),
    ).fetchone()
    conn.close()
    if row:
        return {
            "signal_type": "product_delay",
            "severity": 3,
            "evidence": f"手工标记 ({row['note_date']}): {row['content'][:100]}",
        }
    return None


def check_customer_concentration_signal(ticker: str) -> dict | None:
    """信号 5：customer_top5_pct 同比变化 |Δ| ≥ 5pp"""
    quarters = read_quarterly(ticker, n=8)
    # 找最近两条有 customer_top5_pct 的记录（至少间隔 3 个季度，代理同比）
    valid = [q for q in quarters if q.get("customer_top5_pct") is not None]
    if len(valid) < 2:
        return None
    latest_pct = valid[0]["customer_top5_pct"]
    prev_pct = valid[1]["customer_top5_pct"]
    delta_pp = (latest_pct - prev_pct) * 100
    if abs(delta_pp) >= CUSTOMER_CONCENTRATION_DELTA_PP:
        severity = 2 if delta_pp < 0 else 1  # 下降=大客户跑路，更严重
        direction = "上升" if delta_pp > 0 else "下降"
        return {
            "signal_type": "customer_concentration",
            "severity": severity,
            "evidence": (
                f"前5大客户占比 {direction} {abs(round(delta_pp,1))}pp "
                f"({valid[1]['fiscal_quarter']} {round(prev_pct*100,1)}% → "
                f"{valid[0]['fiscal_quarter']} {round(latest_pct*100,1)}%)"
            ),
        }
    return None


def compute_all_signals(ticker: str, spy_yoy_series: dict = None) -> list[dict]:
    """跑 5 类信号，返回触发清单。"""
    spy_yoy_series = spy_yoy_series or {}
    triggered = []
    checks = [
        check_gross_margin_signal,
        lambda t: check_revenue_lag_signal(t, spy_yoy_series),
        check_call_keyword_signal,
        check_product_delay_signal,
        check_customer_concentration_signal,
    ]
    for fn in checks:
        sig = fn(ticker)
        if sig:
            sig["ticker"] = ticker
            triggered.append(sig)
    return triggered


def record_signals(ticker: str, signals: list[dict]):
    """触发的信号写入 decay_signals 表（当日覆盖）。"""
    if not signals:
        return
    conn = get_db()
    today = date.today().isoformat()
    for sig in signals:
        conn.execute(
            """
            INSERT INTO decay_signals(ticker, detected_at, signal_type, severity, evidence)
            VALUES(?,?,?,?,?)
            ON CONFLICT(ticker, detected_at, signal_type) DO UPDATE SET
                severity=excluded.severity, evidence=excluded.evidence
            """,
            (ticker, today, sig["signal_type"], sig["severity"], sig.get("evidence", "")),
        )
    conn.commit()
    conn.close()


# === 卖出触发判断 ===

def should_sell(triggered: list[dict]) -> tuple[bool, str]:
    """单一 severity 3 触发；或 ≥ 2 个 severity ≥ 1 触发。"""
    if any(s["severity"] >= 3 for s in triggered):
        return True, "单一严重信号（产品延期）"
    if len(triggered) >= 2:
        return True, f"{len(triggered)} 类信号同时触发"
    return False, ""


# === 手工备注 ===

def add_manual_note(ticker: str, note_type: str, content: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO manual_notes(ticker, note_type, content) VALUES(?,?,?)",
        (ticker, note_type, content),
    )
    conn.commit()
    conn.close()


def list_manual_notes(ticker: str, days: int = 90) -> list[dict]:
    conn = get_db()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM manual_notes WHERE ticker=? AND note_date >= ? ORDER BY created_at DESC",
        (ticker, cutoff),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# === 电话会议关键词手工录入 ===

def upsert_call_keyword(ticker: str, fq: str, keyword: str, count: int, snippet: str = ""):
    conn = get_db()
    conn.execute(
        """
        INSERT INTO earnings_call_keywords(ticker, fiscal_quarter, keyword, occurrence_count, context_snippet)
        VALUES(?,?,?,?,?)
        ON CONFLICT(ticker, fiscal_quarter, keyword) DO UPDATE SET
            occurrence_count=excluded.occurrence_count,
            context_snippet=excluded.context_snippet,
            updated_at=CURRENT_TIMESTAMP
        """,
        (ticker, fq, keyword, count, snippet),
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    conn = get_db()
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    print("建表成功，共", len(tables), "张表：", [t["name"] for t in tables])
    companies = conn.execute("SELECT ticker FROM companies ORDER BY ticker").fetchall()
    print("池子股票：", [c["ticker"] for c in companies])
    conn.close()
