"""資料抓取：證交所（上市）＋櫃買中心（上櫃）每日全市場行情，Yahoo Finance 當備援。

歷史資料快取在 data/history.csv.gz，每天只需補抓新的交易日。
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
HISTORY_FILE = ROOT / "data" / "history.csv.gz"
STOCK_LIST_FILE = ROOT / "data" / "stock_list.csv"

COLS = ["date", "code", "open", "high", "low", "close", "volume"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}
REQUEST_GAP = 3.5  # 秒；證交所請求太密會被暫時封鎖

FIELD_ALIASES = {
    "code": ["證券代號", "代號"],
    "name": ["證券名稱", "名稱"],
    "open": ["開盤價", "開盤"],
    "high": ["最高價", "最高"],
    "low": ["最低價", "最低"],
    "close": ["收盤價", "收盤"],
    "volume": ["成交股數"],
}


class SourceUnavailable(Exception):
    """資料來源連不上（不是休市，而是網路/封鎖問題）。"""


# ---------------------------------------------------------------- 工具函式
def _num(x):
    if x is None:
        return None
    s = str(x).replace(",", "").strip()
    if s in ("", "--", "---", "----", "X", "除權息", "除息", "除權"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _map_fields(fields: list[str]) -> dict[str, int]:
    clean = [str(f).replace(" ", "").replace("　", "").strip() for f in fields]
    out: dict[str, int] = {}
    for key, aliases in FIELD_ALIASES.items():
        for i, f in enumerate(clean):
            if f in aliases:
                out[key] = i
                break
        else:
            for i, f in enumerate(clean):
                if any(f.startswith(a) for a in aliases):
                    out[key] = i
                    break
    return out


def _rows_to_df(fields, rows, date: dt.date) -> pd.DataFrame:
    idx = _map_fields(fields)
    need = {"code", "open", "high", "low", "close", "volume"}
    if not need.issubset(idx):
        raise ValueError(f"欄位對不上：{fields}")
    recs = []
    for r in rows:
        code = str(r[idx["code"]]).strip()
        recs.append(
            {
                "date": date.isoformat(),
                "code": code,
                "name": str(r[idx["name"]]).strip() if "name" in idx else "",
                "open": _num(r[idx["open"]]),
                "high": _num(r[idx["high"]]),
                "low": _num(r[idx["low"]]),
                "close": _num(r[idx["close"]]),
                "volume": _num(r[idx["volume"]]),
            }
        )
    return pd.DataFrame(recs, columns=COLS + ["name"])


def _get_json(session: requests.Session, url: str, params: dict) -> dict:
    last = None
    for attempt in range(3):
        try:
            r = session.get(url, params=params, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    # 被封鎖時常回傳 HTML
                    last = f"非 JSON 回應（可能被暫時封鎖）：{r.text[:120]!r}"
            else:
                last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = repr(e)
        time.sleep(REQUEST_GAP * (attempt + 2))
    raise SourceUnavailable(f"{url} 失敗：{last}")


# ---------------------------------------------------------------- 官方來源
def fetch_twse_day(session: requests.Session, date: dt.date) -> pd.DataFrame | None:
    """證交所：某日全部上市股票收盤行情。休市回傳 None。"""
    j = _get_json(
        session,
        "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
        {"date": date.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"},
    )
    if str(j.get("stat", "")).upper() != "OK":
        return None
    tables = j.get("tables")
    if tables is None:  # 舊版格式 fields9/data9
        tables = [
            {"fields": j[k], "data": j.get("data" + k[6:], [])}
            for k in j
            if k.startswith("fields")
        ]
    for t in tables:
        fields = t.get("fields") or []
        f = "".join(fields)
        if "證券代號" in f and "收盤價" in f and t.get("data"):
            return _rows_to_df(fields, t["data"], date)
    return None


def fetch_tpex_day(session: requests.Session, date: dt.date) -> pd.DataFrame | None:
    """櫃買中心：某日全部上櫃股票收盤行情。休市回傳 None。"""
    try:
        j = _get_json(
            session,
            "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes",
            {"date": date.strftime("%Y/%m/%d"), "id": "", "response": "json"},
        )
        for t in j.get("tables") or []:
            if t.get("data") and t.get("fields"):
                return _rows_to_df(t["fields"], t["data"], date)
        if j.get("tables") is not None:
            return None
    except (SourceUnavailable, ValueError) as e:
        log.warning("櫃買新版 API 失敗，改試舊版：%s", e)

    # 舊版 API（民國日期，固定欄位順序）
    roc = f"{date.year - 1911}/{date.month:02d}/{date.day:02d}"
    j = _get_json(
        session,
        "https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php",
        {"l": "zh-tw", "d": roc, "o": "json"},
    )
    rows = j.get("aaData") or []
    if not rows:
        return None
    fields = ["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數"]
    return _rows_to_df(fields, [r[:9] for r in rows], date)


def fetch_official_range(dates: list[dt.date], markets: list[str]) -> pd.DataFrame:
    session = requests.Session()
    frames = []
    for i, d in enumerate(dates):
        got = []
        if "TWSE" in markets:
            df = fetch_twse_day(session, d)
            time.sleep(REQUEST_GAP)
            if df is not None:
                got.append(df.assign(market="TWSE"))
        if "TPEX" in markets:
            df = fetch_tpex_day(session, d)
            time.sleep(REQUEST_GAP)
            if df is not None:
                got.append(df.assign(market="TPEX"))
        status = f"{sum(len(g) for g in got)} 筆" if got else "休市/無資料"
        log.info("[%d/%d] %s %s", i + 1, len(dates), d, status)
        frames.extend(got)
    if not frames:
        return pd.DataFrame(columns=COLS)
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------- Yahoo 備援
def fetch_yahoo(stocks: pd.DataFrame, start: dt.date) -> pd.DataFrame:
    import yfinance as yf

    suffix = {"TWSE": ".TW", "TPEX": ".TWO"}
    tickers = {f"{c}{suffix[m]}": c for c, m in zip(stocks.code, stocks.market)}
    names = list(tickers)
    frames = []
    for i in range(0, len(names), 200):
        chunk = names[i : i + 200]
        log.info("Yahoo 下載 %d-%d / %d", i + 1, i + len(chunk), len(names))
        raw = yf.download(
            chunk,
            start=start.isoformat(),
            auto_adjust=False,
            group_by="ticker",
            threads=True,
            progress=False,
        )
        for t in chunk:
            if t not in raw.columns.get_level_values(0):
                continue
            sub = raw[t].dropna(subset=["Close"])
            if sub.empty:
                continue
            frames.append(
                pd.DataFrame(
                    {
                        "date": sub.index.strftime("%Y-%m-%d"),
                        "code": tickers[t],
                        "open": sub["Open"].values,
                        "high": sub["High"].values,
                        "low": sub["Low"].values,
                        "close": sub["Close"].values,
                        "volume": sub["Volume"].values,
                    }
                )
            )
        time.sleep(2)
    if not frames:
        raise SourceUnavailable("Yahoo Finance 也抓不到資料")
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------- 對外介面
def _build_stock_list() -> None:
    """第一次執行時，用 twstock 套件內建的代號表建立股票清單。"""
    import importlib.util

    base = Path(importlib.util.find_spec("twstock").origin).parent / "codes"
    out = []
    for f, mk_name, mk in [("twse", "上市", "TWSE"), ("tpex", "上櫃", "TPEX")]:
        d = pd.read_csv(base / f"{f}_equities.csv", dtype=str)
        d = d[d.type.isin(["股票", "創新板"]) & d.code.str.fullmatch(r"[1-9]\d{3}")]
        out.append(
            pd.DataFrame({"code": d.code, "name": d.name, "market": mk, "industry": d.group})
        )
    full = pd.concat(out).drop_duplicates("code").sort_values("code")
    STOCK_LIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    full.to_csv(STOCK_LIST_FILE, index=False)
    log.info("已建立股票清單：%d 檔", len(full))


def load_stock_list(markets: list[str]) -> pd.DataFrame:
    if not STOCK_LIST_FILE.exists():
        _build_stock_list()
    s = pd.read_csv(STOCK_LIST_FILE, dtype=str)
    return s[s.market.isin(markets)].reset_index(drop=True)


def _add_new_listings(new: pd.DataFrame, stocks: pd.DataFrame, markets: list[str]) -> None:
    """官方資料出現清單裡沒有的新上市櫃股票時，自動加入股票清單。"""
    if "name" not in new or "market" not in new:
        return
    fresh = new[~new.code.isin(set(stocks.code))].drop_duplicates("code", keep="last")
    if fresh.empty:
        return
    full = pd.read_csv(STOCK_LIST_FILE, dtype=str)
    add = pd.DataFrame(
        {"code": fresh.code, "name": fresh.name, "market": fresh.market, "industry": ""}
    )
    full = pd.concat([full, add]).drop_duplicates("code").sort_values("code")
    full.to_csv(STOCK_LIST_FILE, index=False)
    log.info("新增 %d 檔新股到清單：%s", len(add), ", ".join(add.code))


def load_history() -> pd.DataFrame:
    if HISTORY_FILE.exists():
        return pd.read_csv(HISTORY_FILE, dtype={"code": str})
    return pd.DataFrame(columns=COLS)


def save_history(df: pd.DataFrame, keep_days: int) -> None:
    dates = sorted(df.date.unique())[-keep_days:]
    df = df[df.date.isin(dates)].sort_values(["date", "code"]).copy()
    df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float).round(2)
    df["volume"] = df["volume"].astype(float).round(0).astype("Int64")
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(HISTORY_FILE, index=False, compression="gzip")


def update_history(
    target: dt.date, markets: list[str], keep_days: int, source: str = "auto"
) -> pd.DataFrame:
    """補齊歷史資料到 target 日，回傳完整歷史（長表）。"""
    hist = load_history()
    stocks = load_stock_list(markets)

    if hist.empty:
        # 首次執行：約 keep_days 個交易日 ≈ keep_days*1.5 個日曆天
        start = target - dt.timedelta(days=int(keep_days * 1.5) + 10)
    else:
        start = dt.date.fromisoformat(hist.date.max()) + dt.timedelta(days=1)

    dates = [
        start + dt.timedelta(days=i)
        for i in range((target - start).days + 1)
        if (start + dt.timedelta(days=i)).weekday() < 5
    ]
    if not dates:
        log.info("歷史資料已是最新（%s）", hist.date.max())
        return hist

    new = None
    if source in ("auto", "official"):
        try:
            log.info("從證交所/櫃買中心抓取 %d 個工作日（%s ~ %s）", len(dates), dates[0], dates[-1])
            new = fetch_official_range(dates, markets)
        except SourceUnavailable as e:
            if source == "official":
                raise
            log.warning("官方來源無法使用，改用 Yahoo Finance：%s", e)
    if new is None:
        new = fetch_yahoo(stocks, start)
        new = new[new.date <= target.isoformat()]

    new = new[new.code.str.fullmatch(r"[1-9]\d{3}")]
    _add_new_listings(new, stocks, markets)
    stocks = load_stock_list(markets)
    new = new[new.code.isin(set(stocks.code))][COLS]
    merged = pd.concat([hist, new], ignore_index=True)
    merged = merged.drop_duplicates(["date", "code"], keep="last")
    save_history(merged, keep_days)
    return load_history()
