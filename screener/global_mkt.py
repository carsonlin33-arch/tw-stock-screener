"""美股隔夜行情：費半、那斯達克、台積電 ADR 等。

台股開盤方向和美國科技股前一晚的表現高度相關，尤其是費城半導體指數與台積電 ADR。
資料來源：Yahoo Finance（yfinance），在 GitHub Actions 上執行。
"""
from __future__ import annotations

import logging


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


def _closes(sym: str):
    """單一代號近 10 日收盤（逐檔抓，避免多檔一起下載時日期對不齊、整欄缺值）。"""
    import yfinance as yf

    for _ in range(2):
        try:
            h = yf.Ticker(sym).history(period="10d", interval="1d", auto_adjust=False)
            s = h["Close"].dropna()
            if len(s) >= 2:
                return s
        except Exception as e:  # noqa: BLE001
            log.warning("%s 下載失敗：%s", sym, e)
    return None


# 抓不到時的替代代號（費半指數抓不到就用費半 ETF）
FALLBACK = {"^SOX": ("SOXX", "費半ETF")}


def snapshot(tw2330_close: float | None = None) -> dict:
    """回傳 {"items": [{sym,name,close,chg,date}], "adr_premium": %}；失敗回傳空 dict。"""
    items, closes = [], {}
    for sym, name in SYMBOLS + [("TWD=X", "美元台幣")]:
        s = _closes(sym)
        if s is None and sym in FALLBACK:
            sym, name = FALLBACK[sym]
            s = _closes(sym)
        if s is None:
            log.warning("美股隔夜缺 %s", name)
            continue
        closes[sym] = s
        if sym == "TWD=X":
            continue
        items.append({"sym": sym, "name": name, "close": round(float(s.iloc[-1]), 2),
                      "chg": round((float(s.iloc[-1]) / float(s.iloc[-2]) - 1) * 100, 2),
                      "date": s.index[-1].strftime("%m/%d")})
    out = {"items": items}
    try:
        if tw2330_close and "TSM" in closes and "TWD=X" in closes:
            # 1 股 ADR = 5 股台積電
            out["adr_premium"] = round((float(closes["TSM"].iloc[-1]) * float(closes["TWD=X"].iloc[-1]) / 5
                                        / tw2330_close - 1) * 100, 1)
    except Exception:  # noqa: BLE001
        pass
    log.info("美股隔夜：%s", "、".join(f"{i['name']} {i['chg']:+.2f}%（{i['date']}）" for i in items))
    return out
