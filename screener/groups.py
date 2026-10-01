"""族群強弱排行、相對強度（RS）分數、K 線資料。

參考專業選股網站的做法：
- 族群強弱：Finviz 的 Groups、IBD 的產業群組排名——資金往哪個族群流，比單一個股更早看得出來。
- RS 分數：IBD 的 Relative Strength Rating，把每檔股票近 3 個月與 6 個月的漲幅，跟全市場比較排成 1~99 分。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


def _ret(p, n: int) -> pd.Series:
    c = p.close
    if len(c) <= n:
        return pd.Series(dtype=float)
    return c.iloc[-1] / c.iloc[-1 - n] - 1


def rs_rating(p) -> pd.Series:
    """1~99：近 63 個交易日漲幅權重 2、近 126 個交易日權重 1，和全市場比較的百分位。"""
    r63, r126 = _ret(p, 63), _ret(p, 126)
    if r63.empty:
        return pd.Series(dtype=float)
    score = 2 * r63 + (r126 if not r126.empty else r63)
    liquid = p.volume.iloc[-20:].mean() >= 100_000  # 近 20 日平均量 ≥ 100 張才列入比較
    score = score[liquid & score.notna()]
    return (score.rank(pct=True) * 98 + 1).round(0)


def industry_strength(p, stocks: pd.DataFrame, min_n: int = 5) -> list[dict]:
    """每個產業：檔數、今日／5 日／20 日等權平均漲幅、站上月線比例、今天創 60 日新高與爆量家數。"""
    ind = stocks.drop_duplicates("code").set_index("code").industry
    c, v = p.close, p.volume
    traded = p.traded.iloc[-1] & (v.iloc[-20:].mean() >= 100_000)
    codes = [x for x in c.columns if traded.get(x, False) and isinstance(ind.get(x), str) and ind.get(x)]
    if not codes or len(c) < 61:
        return []
    clip = lambda s: s.clip(-0.5, 0.5)
    d = pd.DataFrame(index=codes)
    d["ind"] = ind.reindex(codes)
    d["r1"] = (c.iloc[-1] / c.iloc[-2] - 1).reindex(codes).clip(-0.11, 0.11)
    d["r5"] = clip(_ret(p, 5).reindex(codes))
    d["r20"] = clip(_ret(p, 20).reindex(codes))
    d["above"] = (c.iloc[-1] > p.ma(20).iloc[-1]).reindex(codes)
    d["newhi"] = (c.iloc[-1] >= c.iloc[-61:-1].max()).reindex(codes)
    d["surge"] = (v.iloc[-1] >= 3 * p.vol_avg(5).iloc[-1]).reindex(codes)
    g = d.groupby("ind")
    out = pd.DataFrame({"n": g.size(), "r1": g.r1.mean() * 100, "r5": g.r5.mean() * 100, "r20": g.r20.mean() * 100,
                        "above": g.above.mean() * 100, "newhi": g.newhi.sum(), "surge": g.surge.sum()})
    out = out[out.n >= min_n]
    if out.empty:
        return []
    # 綜合分數：5 日與 20 日漲幅的排名平均（同時看短期與中期的資金流向）
    out["score"] = (out.r5.rank(pct=True) + out.r20.rank(pct=True)) / 2 * 100
    out = out.sort_values("score", ascending=False)
    out["rank"] = np.arange(1, len(out) + 1)
    return [{"ind": k, "n": int(r.n), "r1": round(float(r.r1), 2), "r5": round(float(r.r5), 1), "r20": round(float(r.r20), 1),
             "above": round(float(r.above)), "newhi": int(r.newhi), "surge": int(r.surge),
             "score": round(float(r.score)), "rank": int(r["rank"])} for k, r in out.iterrows()]


def write_ohlc(path: Path, p, codes: list[str], days: int = 120) -> None:
    """報表上股票的近 days 日 K 線（開高低收量），網頁點名稱時才載入。"""
    idx = p.close.index[-days:]
    out = {"dates": list(map(str, idx)), "k": {}}
    tr = p.traded.loc[idx]
    for code in codes:
        if code not in p.close.columns:
            continue
        ok = tr[code].values
        rows = []
        for i, dte in enumerate(idx):
            if not ok[i]:
                rows.append(None)
                continue
            o, h, l, cl = (p.open.at[dte, code], p.high.at[dte, code], p.low.at[dte, code], p.close.at[dte, code])
            if any(pd.isna(x) for x in (o, h, l, cl)):
                rows.append(None)
                continue
            rows.append([round(float(o), 2), round(float(h), 2), round(float(l), 2), round(float(cl), 2),
                         int(p.volume.at[dte, code] // 1000)])
        out["k"][code] = rows
    path.write_text(json.dumps(out, separators=(",", ":")), "utf-8")
