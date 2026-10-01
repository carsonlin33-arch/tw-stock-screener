"""今天是第幾季、這一季的第幾個交易日。"""
from __future__ import annotations

import datetime as dt


def quarter_info(today: str, trade_dates) -> dict:
    """today: YYYY-MM-DD；trade_dates: 歷史交易日（字串），不含今天也可以。"""
    d = dt.date.fromisoformat(today)
    q = (d.month - 1) // 3 + 1
    start = dt.date(d.year, 3 * (q - 1) + 1, 1)
    days = {x for x in map(str, trade_dates) if start.isoformat() <= x <= today}
    days.add(today)
    return {"year": d.year, "q": q, "tday": len(days), "cday": (d - start).days + 1,
            "text": f"{d.year} 年第 {q} 季・第 {len(days)} 個交易日"}
