"""下載歷史本益比（每月第一個交易日），供回測用。

來源：證交所 BWIBBU_d（上市）、櫃買中心本益比查詢（上櫃）。
用法：python -m screener.pe_backfill --years 5 --out pe.csv.gz
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import time

import pandas as pd
import requests

from .fetch import HEADERS, REQUEST_GAP, SourceUnavailable, _get_json, _num

log = logging.getLogger("pe")
ALIAS = {"code": ["證券代號", "股票代號", "代號"], "pe": ["本益比"], "pb": ["股價淨值比"], "yield": ["殖利率(%)", "殖利率"]}


def _parse(fields, rows, date, market):
    clean = [str(f).replace(" ", "").strip() for f in fields]
    idx = {}
    for k, al in ALIAS.items():
        for i, f in enumerate(clean):
            if f in al or any(f.startswith(a) for a in al):
                idx[k] = i
                break
    if "code" not in idx or "pe" not in idx:
        raise ValueError(f"欄位對不上：{fields}")
    out = []
    for r in rows:
        out.append({"date": date.isoformat(), "code": str(r[idx["code"]]).strip(), "market": market,
                    "pe": _num(r[idx["pe"]]), "pb": _num(r[idx["pb"]]) if "pb" in idx else None,
                    "yield": _num(r[idx["yield"]]) if "yield" in idx else None})
    return out


def twse(s, d):
    j = _get_json(s, "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d",
                  {"date": d.strftime("%Y%m%d"), "selectType": "ALL", "response": "json"})
    if str(j.get("stat", "")).upper() != "OK":
        return []
    if j.get("tables"):
        t = j["tables"][0]
        return _parse(t["fields"], t["data"], d, "TWSE")
    return _parse(j.get("fields", []), j.get("data", []), d, "TWSE")


def tpex(s, d):
    try:
        j = _get_json(s, "https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate",
                      {"date": d.strftime("%Y/%m/%d"), "response": "json"})
        for t in j.get("tables") or []:
            if t.get("data"):
                return _parse(t["fields"], t["data"], d, "TPEX")
        if j.get("tables") is not None:
            return []
    except (SourceUnavailable, ValueError) as e:
        log.warning("櫃買新版失敗，改舊版：%s", e)
    roc = f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"
    j = _get_json(s, "https://www.tpex.org.tw/web/stock/aftertrading/peratio_analysis/pera_result.php",
                  {"l": "zh-tw", "o": "json", "d": roc})
    rows = j.get("aaData") or []
    if not rows:
        return []
    # 舊版欄位：股票代號, 名稱, 本益比, 每股股利, 股利年度, 殖利率(%), 股價淨值比
    return _parse(["股票代號", "名稱", "本益比", "每股股利", "股利年度", "殖利率(%)", "股價淨值比"], rows, d, "TPEX")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=5)
    ap.add_argument("--out", default="pe.csv.gz")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    end = dt.date.today()
    start = end - dt.timedelta(days=int(a.years * 365.25))
    s = requests.Session()
    s.headers.update(HEADERS)
    recs = []
    m = dt.date(start.year, start.month, 1)
    while m <= end:
        got = False
        for k in range(8):  # 每月第一個有資料的交易日
            d = m + dt.timedelta(days=k)
            if d.weekday() >= 5 or d > end:
                continue
            try:
                a1 = twse(s, d)
                time.sleep(REQUEST_GAP)
                a2 = tpex(s, d)
                time.sleep(REQUEST_GAP)
            except SourceUnavailable as e:
                log.warning("%s 失敗：%s", d, e)
                continue
            if a1 or a2:
                recs += a1 + a2
                log.info("%s 上市 %d 筆、上櫃 %d 筆", d, len(a1), len(a2))
                got = True
                break
        if not got:
            log.warning("%s 月份沒有資料", m)
        m = dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    df = pd.DataFrame(recs)
    df.to_csv(a.out, index=False, compression="gzip")
    log.info("完成：%d 筆", len(df))


if __name__ == "__main__":
    main()
