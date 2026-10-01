"""報表附加統計：早期預警準確度、大型股觀察表。"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import tech
from .enrich import DIR, INST_HIST

log = logging.getLogger("stats")
ROOT = Path(__file__).resolve().parent.parent
ALERT_DIR = ROOT / "data" / "alerts"
ALERT_STATS = DIR / "alert_stats.json"
LARGECAP = DIR / "largecap.csv"

TIME_BUCKETS = [("09:30–10:30", "10:30"), ("10:30–12:00", "12:00"), ("12:00 後", "99:99")]
PEER_BUCKETS = [("0 家", 0, 0), ("1~2 家", 1, 2), ("3~5 家", 3, 5), ("6 家以上", 6, 999)]


def _time_bucket(t: str) -> str:
    for name, end in TIME_BUCKETS:
        if str(t) < end:
            return name
    return TIME_BUCKETS[-1][0]


def _peer_bucket(n) -> str:
    n = 0 if pd.isna(n) else int(n)
    return next(name for name, lo, hi in PEER_BUCKETS if lo <= n <= hi)


def alert_stats(p) -> dict:
    """用 data/alerts/*.csv 的早期預警紀錄，算：
    - close_hit：預警當天收盤仍符合「爆量突破 60 日新高」（量 ≥ 前 5 日均量 3 倍、漲 ≥ 3%、紅K、收盤 > 前 60 日最高價）
    - ret3 / ret5：從預警價到第 3、5 個交易日收盤的報酬（%）；還沒滿天數的不列入
    依預警時段、族群同步家數分組。"""
    files = sorted(ALERT_DIR.glob("*.csv")) if ALERT_DIR.exists() else []
    if not files:
        return {}
    a = pd.concat([pd.read_csv(f, dtype={"code": str}) for f in files], ignore_index=True)
    a = a.dropna(subset=["first_price"])
    dates = list(p.close.index)
    pos = {d: i for i, d in enumerate(dates)}
    surge = tech.surge_frame(p)
    recs = []
    for r in a.itertuples():
        i = pos.get(r.date)
        if i is None or r.code not in p.close.columns:
            continue
        rec = {"time": _time_bucket(r.first_time), "peers": _peer_bucket(r.peers),
               "close_hit": bool(surge.at[r.date, r.code])}
        for k in (3, 5):
            rec[f"ret{k}"] = (p.close.iat[i + k, p.close.columns.get_loc(r.code)] / r.first_price - 1) * 100 \
                if i + k < len(dates) else np.nan
        recs.append(rec)
    if not recs:
        return {}
    df = pd.DataFrame(recs)

    def summ(g: pd.DataFrame) -> dict:
        return {"n": int(len(g)), "close_hit": round(float(g.close_hit.mean() * 100), 1),
                "ret3": None if g.ret3.notna().sum() == 0 else round(float(g.ret3.mean()), 2), "ret3_n": int(g.ret3.notna().sum()),
                "ret5": None if g.ret5.notna().sum() == 0 else round(float(g.ret5.mean()), 2), "ret5_n": int(g.ret5.notna().sum())}

    out = {"from": str(a.date.min()), "to": str(a.date.max()), "days": int(a.date.nunique()), "all": summ(df),
           "by_time": [{"bucket": b, **summ(df[df.time == b])} for b, _ in TIME_BUCKETS if (df.time == b).any()],
           "by_peers": [{"bucket": b, **summ(df[df.peers == b])} for b, *_ in PEER_BUCKETS if (df.peers == b).any()]}
    return out


def save_alert_stats(p) -> dict:
    s = alert_stats(p)
    if s:
        ALERT_STATS.write_text(json.dumps(s, ensure_ascii=False, indent=1), "utf-8")
    return s


def _inst_sum(col: str, n: int) -> pd.Series:
    if not INST_HIST.exists():
        return pd.Series(dtype=float)
    h = pd.read_csv(INST_HIST, dtype={"code": str})
    keep = sorted(h.date.unique())[-n:]
    return h[h.date.isin(keep)].groupby("code")[col].sum()


def largecap(p, stocks: pd.DataFrame, extras: pd.DataFrame, rs: pd.Series, tpl: pd.Series, n: int = 50) -> list[dict]:
    """成交值前 n 大（近 20 日平均成交值）：本益比、營收、外資 20 日買賣超、離 52 週高點、趨勢樣板、RS。"""
    val = (p.close * p.volume).iloc[-20:].mean()
    val = val[p.traded.iloc[-1]].sort_values(ascending=False).head(n)
    info = stocks.drop_duplicates("code").set_index("code")
    ex = extras if extras is not None else pd.DataFrame()
    f20 = _inst_sum("foreign", 20)
    hi = tech.high52_dist(p)
    chg = p.change_pct.iloc[-1]
    out = []
    for rank, (code, v) in enumerate(val.items(), 1):
        g = lambda k: round(float(ex.at[code, k]), 2) if code in ex.index and k in ex.columns and pd.notna(ex.at[code, k]) else None
        t = tpl.get(code)
        out.append({"rank": rank, "code": code, "name": info.name.get(code, ""), "industry": info.industry.get(code, "") or "",
                    "close": round(float(p.close.iat[-1, p.close.columns.get_loc(code)]), 2),
                    "chg": round(float(chg.get(code)), 2) if pd.notna(chg.get(code)) else None,
                    "turnover": round(float(v) / 1e8, 1), "pe": g("pe"), "rev_yoy": g("rev_yoy"), "rev_yoy_3m": g("rev_yoy_3m"),
                    "foreign_20d": round(float(f20[code])) if code in f20.index else None,
                    "hi52_dist": round(float(hi.get(code)), 1) if pd.notna(hi.get(code)) else None,
                    "tpl": None if t is None or pd.isna(t) else bool(t),
                    "rs": float(rs.get(code)) if pd.notna(rs.get(code)) else None})
    pd.DataFrame(out).to_csv(LARGECAP, index=False)
    return out
