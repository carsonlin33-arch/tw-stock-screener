"""美股隔夜行情：費半、那斯達克、台積電 ADR 等。

台股開盤方向和美國科技股前一晚的表現高度相關，尤其是費城半導體指數與台積電 ADR。
資料來源：Yahoo Finance（yfinance），在 GitHub Actions 上執行。
"""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)

SYMBOLS = [
    ("^SOX", "費城半導體"),
    ("^IXIC", "那斯達克"),
    ("^GSPC", "標普500"),
    ("TSM", "台積電ADR"),
    ("NVDA", "輝達"),
    ("AAPL", "蘋果"),
    ("^VIX", "VIX恐慌指數"),
]


def snapshot(tw2330_close: float | None = None) -> dict:
    """回傳 {"items": [{sym,name,close,chg,date}], "adr_premium": %}；失敗回傳空 dict。"""
    import yfinance as yf

    syms = [s for s, _ in SYMBOLS] + ["TWD=X"]
    df = yf.download(syms, period="10d", interval="1d", progress=False, auto_adjust=False, threads=True)
    close = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df
    items = []
    for sym, name in SYMBOLS:
        if sym not in close:
            continue
        s = close[sym].dropna()
        if len(s) < 2:
            continue
        items.append({"sym": sym, "name": name, "close": round(float(s.iloc[-1]), 2),
                      "chg": round((float(s.iloc[-1]) / float(s.iloc[-2]) - 1) * 100, 2),
                      "date": s.index[-1].strftime("%m/%d")})
    out = {"items": items}
    try:
        tsm = close["TSM"].dropna().iloc[-1]
        twd = close["TWD=X"].dropna().iloc[-1]
        if tw2330_close:
            # 1 股 ADR = 5 股台積電
            out["adr_premium"] = round((float(tsm) * float(twd) / 5 / tw2330_close - 1) * 100, 1)
    except Exception:  # noqa: BLE001
        pass
    log.info("美股隔夜：%s", "、".join(f"{i['name']} {i['chg']:+.2f}%" for i in items))
    return out
