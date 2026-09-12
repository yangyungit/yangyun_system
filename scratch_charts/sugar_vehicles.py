import yfinance as yf
import pandas as pd
import numpy as np

pd.set_option("display.width", 240)

CANDS = {
    # 期货/ETF 载体
    "SB=F": ("糖期货 #11", "USD"),
    "CANE": ("糖 ETF (Teucrium)", "USD"),
    "SGG": ("iPath 糖 ETN(查是否已清盘)", "USD"),
    "DBA": ("农产品宽基(对照组)", "USD"),
    "PDBC": ("商品宽基(对照组)", "USD"),
    # 巴西：甘蔗/乙醇/物流
    "SMTO3.SA": ("圣马蒂诺 纯甘蔗糖厂", "BRL"),
    "CSAN3.SA": ("Cosan 控股", "BRL"),
    "CSAN": ("Cosan ADR", "USD"),
    "RAIZ4.SA": ("Raizen 全球最大甘蔗加工", "BRL"),
    "RAIL3.SA": ("Rumo 铁路(糖出口物流)", "BRL"),
    "AGRO": ("Adecoagro", "USD"),
    "JALL3.SA": ("Jalles Machado 有机糖", "BRL"),
    # 印度
    "BALRAMCHIN.NS": ("Balrampur Chini", "INR"),
    "TRIVENI.NS": ("Triveni Engineering", "INR"),
    "EIDPARRY.NS": ("EID Parry", "INR"),
    "DALMIASUG.NS": ("Dalmia Bharat Sugar", "INR"),
    "SHREERENUKA.NS": ("Shree Renuka Sugars", "INR"),
    "BAJAJHIND.NS": ("Bajaj Hindusthan", "INR"),
    "AVADHSUGAR.NS": ("Avadh Sugar", "INR"),
    # 泰国
    "KSL.BK": ("Khon Kaen Sugar", "THB"),
    "BRR.BK": ("Buriram Sugar", "THB"),
    # 其他
    "SZU.DE": ("Suedzucker 欧洲最大糖企", "EUR"),
    "F34.SI": ("Wilmar 糖贸易/精炼", "SGD"),
    "RSI.TO": ("Rogers Sugar 加拿大", "CAD"),
    "TATE.L": ("Tate & Lyle", "GBp"),
    # 下游/替代品
    "INGR": ("Ingredion 玉米糖浆", "USD"),
    "ADM": ("ADM", "USD"),
    "BG": ("Bunge", "USD"),
    "ANDE": ("Andersons", "USD"),
    "GPRE": ("Green Plains 乙醇", "USD"),
    "ALTO": ("Alto Ingredients 乙醇", "USD"),
    "REX": ("REX American 乙醇", "USD"),
    "HSY": ("好时(糖是成本)", "USD"),
}

FX_PAIR = {
    "BRL": "BRL=X", "INR": "INR=X", "THB": "THB=X", "EUR": "EURUSD=X",
    "SGD": "SGD=X", "CAD": "CAD=X", "GBp": "GBPUSD=X", "USD": None,
}
fx = {}
for c, p in FX_PAIR.items():
    if p is None:
        fx[c] = 1.0
        continue
    try:
        v = yf.Ticker(p).history(period="5d")["Close"].dropna().iloc[-1]
        fx[c] = float(v) if p.endswith("USD=X") else 1.0 / float(v)
    except Exception:
        fx[c] = np.nan
fx["GBp"] = fx["GBp"] / 100.0

rows = []
for t, (name, cur) in CANDS.items():
    try:
        h = yf.Ticker(t).history(period="2y", auto_adjust=False)
        if h.empty or len(h) < 30:
            rows.append({"代码": t, "说明": name, "状态": "拉不到/已清盘"})
            continue
        px = h["Close"].dropna()
        adv = float((h["Close"] * h["Volume"]).tail(60).mean()) * fx[cur]
        last_dt = px.index[-1].date()
        def chg(n):
            if len(px) > n:
                return f"{(px.iloc[-1]/px.iloc[-1-n]-1)*100:+.0f}%"
            return "—"
        rows.append({
            "代码": t, "说明": name, "最后交易日": str(last_dt),
            "价格": round(float(px.iloc[-1]), 2), "币种": cur,
            "日均成交额(美元)": f"{adv/1e6:.1f}M" if adv == adv else "—",
            "近1月": chg(21), "近3月": chg(63), "近1年": chg(252),
        })
    except Exception as e:
        rows.append({"代码": t, "说明": name, "状态": f"err {type(e).__name__}"})

df = pd.DataFrame(rows)
print(df.to_string(index=False))
