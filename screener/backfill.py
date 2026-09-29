"""下載多年歷史資料供回測用（不影響每日篩選）。

優先用 Yahoo Finance（含除權息還原），失敗再改用證交所/櫃買官方資料（未還原、較慢）。
用法：python -m screener.backfill --years 5 --out backtest.csv.gz
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging

import pandas as pd

from . import fetch

log = logging.getLogger("backfill")


def yahoo_adjusted(stocks: pd.DataFrame, start: dt.date) -> pd.DataFrame:
    import yfinance as yf

    suffix = {"TWSE": ".TW", "TPEX": ".TWO"}
    tickers = {f"{c}{suffix[m]}": c for c, m in zip(stocks.code, stocks.market)}
    names = list(tickers)
    frames = []
    for i in range(0, len(names), 100):
        chunk = names[i : i + 100]
        log.info("Yahoo %d-%d / %d", i + 1, i + len(chunk), len(names))
        raw = yf.download(chunk, start=start.isoformat(), auto_adjust=True, group_by="ticker",
                          threads=True, progress=False)
        for t in chunk:
            if t not in raw.columns.get_level_values(0):
                continue
            sub = raw[t].dropna(subset=["Close"])
            sub = sub[sub["Volume"] > 0]
            if sub.empty:
                continue
            frames.append(pd.DataFrame({
                "date": sub.index.strftime("%Y-%m-%d"), "code": tickers[t],
                "open": sub["Open"].values, "high": sub["High"].values, "low": sub["Low"].values,
                "close": sub["Close"].values, "volume": sub["Volume"].values,
            }))
    if not frames:
        raise fetch.SourceUnavailable("Yahoo 沒有資料")
    return pd.concat(frames, ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=5)
    ap.add_argument("--out", default="backtest.csv.gz")
    ap.add_argument("--source", default="yahoo", choices=["yahoo", "official"])
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    stocks = fetch.load_stock_list(["TWSE", "TPEX"])
    end = dt.date.today()
    start = end - dt.timedelta(days=int(a.years * 365.25))
    df = None
    if a.source == "yahoo":
        try:
            df = yahoo_adjusted(stocks, start)
            df["adjusted"] = 1
        except Exception as e:  # noqa: BLE001
            log.warning("Yahoo 失敗，改用官方資料：%s", e)
    if df is None:
        dates = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
        dates = [d for d in dates if d.weekday() < 5]
        df = fetch.fetch_official_range(dates, ["TWSE", "TPEX"])
        df = df[df.code.isin(set(stocks.code))][fetch.COLS]
        df["adjusted"] = 0
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype(float).round(3)
    df["volume"] = df["volume"].astype(float).round(0).astype("Int64")
    df.sort_values(["date", "code"]).to_csv(a.out, index=False, compression="gzip")
    log.info("完成：%d 筆，%s ~ %s，%d 檔", len(df), df.date.min(), df.date.max(), df.code.nunique())


if __name__ == "__main__":
    main()
