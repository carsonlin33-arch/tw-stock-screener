"""盤中進場提醒：約 13:12 掃描全市場，找出「爆量＋上漲＋突破 60 日新高」的股票。

回測（2021~2026）：爆量當天收盤前買進、成交量縮到爆量日一半以下隔天賣出，
加上「創 60 日新高」條件後，平均每筆跑贏大盤約 0.8%，6 個年度都是正的。
資料來源：證交所 MIS 即時報價（mis.twse.com.tw）。
用法：python -m screener.intraday [--wait-until 13:12] [--test]
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
import time
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yaml

from . import fetch, notify, positions

log = logging.getLogger("intraday")
TZ = ZoneInfo("Asia/Taipei")
MIS = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"


def _f(x):
    try:
        v = float(str(x).split("_")[0])
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def fetch_quotes(stocks: pd.DataFrame, batch: int = 50) -> tuple[pd.DataFrame, str | None]:
    s = requests.Session()
    s.headers.update(fetch.HEADERS | {"Referer": "https://mis.twse.com.tw/stock/index.jsp"})
    try:
        s.get("https://mis.twse.com.tw/stock/index.jsp", timeout=20)
    except requests.RequestException as e:
        log.warning("MIS 首頁連線失敗：%s", e)
    keys = [f"{'tse' if m == 'TWSE' else 'otc'}_{c}.tw" for c, m in zip(stocks.code, stocks.market)]
    rows, day = [], None
    for i in range(0, len(keys), batch):
        chunk = "|".join(keys[i : i + batch])
        for attempt in range(3):
            try:
                r = s.get(MIS, params={"ex_ch": chunk, "json": "1", "delay": "0", "_": int(time.time() * 1000)}, timeout=20)
                j = r.json()
                break
            except Exception as e:  # noqa: BLE001
                log.warning("MIS 失敗（%d）：%s", attempt + 1, e)
                time.sleep(3)
        else:
            continue
        for q in j.get("msgArray", []):
            price = _f(q.get("z")) or _f(q.get("pz")) or _f(q.get("b"))
            rows.append({"code": q.get("c"), "name": q.get("n"), "price": price, "open": _f(q.get("o")),
                         "high": _f(q.get("h")), "yclose": _f(q.get("y")), "vol_lots": _f(q.get("v")) or 0,
                         "time": q.get("t"), "date": q.get("d")})
            day = day or q.get("d")
        time.sleep(0.8)
    log.info("取得 %d 檔即時報價", len(rows))
    return pd.DataFrame(rows), day


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait-until", help="台北時間 HH:MM，等到這個時間才掃描")
    ap.add_argument("--test", action="store_true", help="測試模式：不檢查日期、不記錄持倉")
    ap.add_argument("--no-notify", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    root = fetch.ROOT
    cfg = yaml.safe_load((root / "config.yaml").read_text("utf-8"))
    ic = cfg.get("intraday", {})
    if not ic.get("enabled", True) and not a.test:
        log.info("盤中提醒已關閉")
        return 0

    now = dt.datetime.now(TZ)
    if now.weekday() >= 5 and not a.test:
        log.info("週末不掃描")
        return 0
    if a.wait_until and not a.test:
        hh, mm = map(int, a.wait_until.split(":"))
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        wait = (target - now).total_seconds()
        if wait > 0:
            log.info("等待 %.0f 秒到 %s", wait, a.wait_until)
            time.sleep(wait)

    markets = cfg.get("settings", {}).get("markets", ["TWSE", "TPEX"])
    stocks = fetch.load_stock_list(markets)
    hist = fetch.load_history()
    today = dt.datetime.now(TZ).date().isoformat()
    hist = hist[hist.date < today]
    g = hist.sort_values("date").groupby("code")
    nh = int(ic.get("new_high_days", 60))
    ref = pd.DataFrame({
        "avg5": g.volume.apply(lambda s: s.iloc[-5:].mean() if len(s) >= 5 else None),
        "hi": g.high.apply(lambda s: s.iloc[-nh:].max() if len(s) >= nh else None),
        "last_close": g.close.last(),
    })

    q, day = fetch_quotes(stocks)
    if q.empty:
        log.error("抓不到即時報價")
        return 1
    if day and day != today.replace("-", "") and not a.test:
        log.info("今天（%s）沒有交易資料（MIS 日期 %s），可能休市", today, day)
        return 0

    q = q.merge(ref, left_on="code", right_index=True, how="left").merge(
        stocks[["code", "industry"]], on="code", how="left")
    q["industry"] = q.industry.fillna("")
    q["chg"] = (q.price / q.yclose - 1) * 100
    q["vol_x"] = q.vol_lots * 1000 / q.avg5
    cond = (
        (q.vol_x >= ic.get("volume_multiple", 3))
        & (q.vol_lots >= ic.get("min_volume_lots", 500))
        & (q.chg >= ic.get("min_change_pct", 3)) & (q.chg <= ic.get("max_change_pct", 10.5))
        & (q.price > q.open)
        & (q.price > q.hi)
        & (q.price >= ic.get("min_price", 10))
    )
    ex_ind = set(ic.get("exclude_industries") or [])
    if ex_ind:
        cond &= ~q.industry.isin(ex_ind)
    hits = q[cond].sort_values("chg", ascending=False)
    log.info("符合 %d 檔：%s", len(hits), " ".join(hits.code))

    # 大盤環境：過去 20 天等權報酬（回測顯示大盤跌超過 3% 時此策略表現差）
    closes = hist.pivot(index="date", columns="code", values="close").sort_index()
    ew = closes.pct_change().clip(-0.11, 0.11).mean(axis=1).iloc[-20:]
    mkt20 = ((1 + ew).prod() - 1) * 100
    warn = mkt20 < ic.get("weak_market_pct", -3)

    scan_time = dt.datetime.now(TZ).strftime("%H:%M")
    if not a.test and len(hits):
        positions.add_signals([{
            "code": r.code, "name": r.name, "industry": r.industry, "signal_date": today, "signal_time": scan_time,
            "alert_price": r.price, "surge_volume": r.vol_lots * 1000,
        } for r in hits.itertuples()])

    if a.no_notify or (hits.empty and not ic.get("notify_when_empty", False)):
        return 0
    title = f"【盤中進場提醒】{today} {scan_time}｜{len(hits)} 檔"
    lines = [f"## 盤中進場提醒 {today} {scan_time}", ""]
    if warn:
        lines += [f"> ⚠️ 大盤過去 20 天下跌 {mkt20:.1f}%。回測顯示大盤弱勢時此策略平均是虧損的，今天建議降低部位或觀望。", ""]
    if hits.empty:
        lines.append("今天沒有符合條件的股票。")
    else:
        lines += ["| 股票 | 產業 | 現價 | 漲幅 | 目前量(張) | 量 / 5日均量 | 前60日最高 |",
                  "|---|---|--:|--:|--:|--:|--:|"]
        for r in hits.itertuples():
            mk = "TW" if stocks.set_index("code").at[r.code, "market"] == "TWSE" else "TWO"
            lines.append(f"| [{r.code} {r.name}](https://tw.stock.yahoo.com/quote/{r.code}.{mk}) | {r.industry or ''} | "
                         f"{r.price:.2f} | {r.chg:+.2f}% | {r.vol_lots:,.0f} | {r.vol_x:.1f}× | {r.hi:.2f} |")
    lines += ["", "<sub>條件：目前成交量 ≥ 前 5 日均量 3 倍、漲 3% 以上、現價 > 開盤價、突破前 60 日最高價。"
              "回測做法：收盤前買進，之後收盤量低於爆量日一半時，隔天開盤賣出（收盤後會另外通知出場）。"
              "盤中量尚未含收盤集合競價，實際爆量倍數通常更高。僅供參考，不構成投資建議。</sub>"]
    body = "\n".join(lines)
    try:
        notify.send(title, body)
    except Exception as e:  # noqa: BLE001
        log.error("通知失敗：%s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
