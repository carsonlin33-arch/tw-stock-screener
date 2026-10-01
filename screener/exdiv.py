"""除權息：算含息報酬用（虛擬帳戶、0050 對照組）。

資料來源（2026/10 用 GitHub Actions 實測過格式）：
- 已除權息：證交所 exRight/TWT49U、櫃買 bulletin/exDailyQ（可以一次查一段日期）
- 即將除權息：證交所 openapi TWT48U_ALL、櫃買 openapi tpex_exright_prepost

檔案：
- data/extras/exdiv.csv：date, code, market, kind（息／權／權息）, prev_close（除權息前收盤）, ref_price（除權息參考價）,
  value（權值＋息值，元）, factor（prev_close ÷ ref_price）
  含息報酬：除權息日之後的價格 × factor 才能和之前比；連乘所有 factor 就是還原權息。
- data/extras/exdiv_upcoming.csv：date, code, market, kind, cash_div（元／股）, stock_ratio（無償配股比例）
- data/extras/bench.csv 加 tr 欄：0050 含息指數（收盤 × 之前所有除息 factor 連乘），和收盤價同一天起算
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import time

import numpy as np
import pandas as pd

from .enrich import DIR, _col, _json, _num, _openapi_rows, _session, _tables

log = logging.getLogger("exdiv")

EXDIV = DIR / "exdiv.csv"
UPCOMING = DIR / "exdiv_upcoming.csv"
BENCH = DIR / "bench.csv"
COLS = ["date", "code", "market", "kind", "prev_close", "ref_price", "value", "factor"]
UP_COLS = ["date", "code", "market", "kind", "cash_div", "stock_ratio"]
KEEP_DAYS = 800  # 保留約兩年
CODE_RE = re.compile(r"\d{4,6}[A-Z]?")


def roc_date(s) -> str | None:
    """'114年10月02日'、'114/10/02'、'1141002' → '2025-10-02'。"""
    s = str(s).strip()
    p = re.findall(r"\d+", s)
    if len(p) == 1 and len(p[0]) == 7:
        p = [p[0][:3], p[0][3:5], p[0][5:]]
    if len(p) != 3:
        return None
    try:
        return dt.date(int(p[0]) + 1911, int(p[1]), int(p[2])).isoformat()
    except ValueError:
        return None


def _kind(s) -> str:
    s = str(s).replace("除", "")
    return "權息" if "權" in s and "息" in s else "權" if "權" in s else "息" if "息" in s else s


def _rows(fields, data, market) -> list[dict]:
    i_d, i_c = _col(fields, "日期"), _col(fields, "代號")
    i_p, i_r = _col(fields, "除權息前收盤"), _col(fields, "除權息參考價")
    i_v, i_k = _col(fields, "權值", "息值"), _col(fields, "權/息")
    if None in (i_d, i_c, i_p, i_r):
        log.warning("%s 除權息欄位對不上：%s", market, fields)
        return []
    out = []
    for r in data:
        code = str(r[i_c]).strip()
        d = roc_date(r[i_d])
        p, ref = _num(r[i_p]), _num(r[i_r])
        if not d or not CODE_RE.fullmatch(code) or not p or not ref:
            continue
        out.append({"date": d, "code": code, "market": market, "kind": _kind(r[i_k]) if i_k is not None else "",
                    "prev_close": p, "ref_price": ref, "value": _num(r[i_v]) if i_v is not None else None,
                    "factor": round(p / ref, 8)})
    return out


def fetch_done(s, start: dt.date, end: dt.date) -> pd.DataFrame:
    rows = []
    j = _json(s, "https://www.twse.com.tw/rwd/zh/exRight/TWT49U",
              {"startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d"), "response": "json"})
    for t in _tables(j):
        rows += _rows(t["fields"], t["data"], "TWSE")
    time.sleep(2)
    j = _json(s, "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ",
              {"startDate": start.strftime("%Y/%m/%d"), "endDate": end.strftime("%Y/%m/%d"), "response": "json"})
    for t in _tables(j):
        rows += _rows(t["fields"], t["data"], "TPEX")
    return pd.DataFrame(rows, columns=COLS)


def fetch_upcoming(s) -> pd.DataFrame:
    out = []
    for r in _openapi_rows(s, "https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL"):
        out.append({"date": roc_date(r.get("Date")), "code": str(r.get("Code", "")).strip(), "market": "TWSE",
                    "kind": _kind(r.get("Exdividend")), "cash_div": _num(r.get("CashDividend")),
                    "stock_ratio": _num(r.get("StockDividendRatio"))})
    for r in _openapi_rows(s, "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"):
        out.append({"date": roc_date(r.get("ExRrightsExDividendDate")), "code": str(r.get("SecuritiesCompanyCode", "")).strip(),
                    "market": "TPEX", "kind": _kind(r.get("ExRrightsExDividend")), "cash_div": _num(r.get("CashDividend")),
                    "stock_ratio": _num(r.get("StockDividendRatio"))})
    df = pd.DataFrame(out, columns=UP_COLS)
    return df[df.date.notna() & df.code.str.fullmatch(CODE_RE.pattern)].sort_values(["date", "code"])


def bench_tr() -> int:
    """bench.csv 加 tr（含息指數）：收盤 × 之前（含當天）所有除權息 factor 連乘。"""
    if not BENCH.exists() or not EXDIV.exists():
        return 0
    b = pd.read_csv(BENCH, dtype={"code": str})
    ex = pd.read_csv(EXDIV, dtype={"code": str})
    parts = []
    for code, g in b.sort_values("date").groupby("code"):
        f = ex[ex.code == code].set_index("date").factor
        step = g.date.map(f).fillna(1.0).astype(float)
        parts.append(g.assign(tr=(g.close * np.cumprod(step.values)).round(4)))
    pd.concat(parts).to_csv(BENCH, index=False)
    return len(b)


def update(today: dt.date, s=None) -> dict:
    """第一次補兩年，之後每天重抓最近 30 天（避免漏掉晚公布的）＋即將除權息清單，順便更新 0050 含息指數。"""
    s = s or _session()
    old = pd.read_csv(EXDIV, dtype={"code": str}) if EXDIV.exists() else pd.DataFrame(columns=COLS)
    start = today - dt.timedelta(days=30 if len(old) else KEEP_DAYS)
    new = []
    # 證交所一次查太長會被擋，分段（每段約半年）
    a = start
    while a <= today:
        b = min(today, a + dt.timedelta(days=180))
        try:
            new.append(fetch_done(s, a, b))
        except Exception as e:  # noqa: BLE001
            log.warning("除權息 %s~%s 失敗：%s", a, b, e)
        a = b + dt.timedelta(days=1)
        time.sleep(2)
    df = pd.concat([old, *new], ignore_index=True).drop_duplicates(["date", "code"], keep="last")
    df = df[df.date >= (today - dt.timedelta(days=KEEP_DAYS)).isoformat()].sort_values(["date", "code"])
    DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(EXDIV, index=False)
    got = {"exdiv": len(df), "exdiv_new": int(sum(len(n) for n in new))}
    try:
        up = fetch_upcoming(s)
        if len(up):
            up.to_csv(UPCOMING, index=False)
        got["exdiv_upcoming"] = len(up)
    except Exception as e:  # noqa: BLE001
        log.warning("即將除權息清單失敗：%s", e)
    try:
        got["bench_tr"] = bench_tr()
    except Exception as e:  # noqa: BLE001
        log.warning("0050 含息指數失敗：%s", e)
    log.info("除權息：%s", got)
    return got
