"""盤中連續監控：09:00~13:25 每幾分鐘掃描全市場一次。

1. 早期預警：股票一出現「預估爆量＋上漲＋突破 60 日新高」就記下來（網頁即時顯示，Email 每 30 分鐘彙整一次）
2. 持股即時：盤中提醒過、還沒出場的股票，顯示即時報酬與「今天會不會量縮出場」的預估
3. 13:12 正式進場提醒：呼叫 intraday.py（與回測相同條件），同一天只做一次
結果寫成 live.json 推到儲存庫的 live 分支，網頁 live.html 每分鐘去抓。

用法：python -m screener.live [--test]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import os
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml

from . import fetch, intraday, notify, positions, sentiment

log = logging.getLogger("live")
TZ = ZoneInfo("Asia/Taipei")
ROOT = fetch.ROOT
STATE_F = ROOT / "data" / "live_state.json"
OUT_F = ROOT / "data" / "live.json"
PUB_DIR = ROOT / ".live_pub"

# 台股一天成交量的累積比例（09:00 起算的分鐘數 → 已完成全天量的比例，含開盤集合競價，約略值）
PROFILE = [(0, .05), (15, .17), (30, .26), (60, .39), (120, .57), (180, .72), (240, .87), (265, .95), (270, 1.0)]


def vol_fraction(now: dt.datetime) -> float:
    m = (now.hour - 9) * 60 + now.minute + now.second / 60
    xs, ys = zip(*PROFILE)
    return float(np.interp(max(0, min(270, m)), xs, ys))


def _hm(s: str, day: dt.datetime) -> dt.datetime:
    h, m = map(int, s.split(":"))
    return day.replace(hour=h, minute=m, second=0, microsecond=0)


def _num(v, nd=2):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, nd)


def _clean(o):
    """轉成可寫 JSON 的型別（NaN → null）。"""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (int, np.integer)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return _num(o, 4)
    return o if o is None or isinstance(o, str) else str(o)


def build_ref(hist: pd.DataFrame, nh: int) -> pd.DataFrame:
    h = hist.sort_values("date")
    g = h.groupby("code")
    return pd.DataFrame({
        "avg5": g.volume.apply(lambda s: s.iloc[-5:].mean() if len(s) >= 5 else np.nan),
        "hi": g.high.apply(lambda s: s.iloc[-nh:].max() if len(s) >= nh else np.nan),
    })


def watchlist() -> set[str]:
    """最近一天收盤報表中的「準備突破觀察」名單。"""
    d = ROOT / "data" / "results"
    files = sorted(d.glob("*.csv")) if d.exists() else []
    if not files:
        return set()
    r = pd.read_csv(files[-1], dtype=str)
    return set(r[r.strategy == "準備突破觀察"].code)


# ------------------------------------------------------------------ 發佈
def publish(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    OUT_F.parent.mkdir(parents=True, exist_ok=True)
    OUT_F.write_text(text, "utf-8")
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return
    run = lambda *a: subprocess.run(["git", *a], cwd=PUB_DIR, check=True, capture_output=True, text=True)
    try:
        first = not (PUB_DIR / ".git").exists()
        if first:
            PUB_DIR.mkdir(exist_ok=True)
            run("init", "-q", "-b", "live")
            run("config", "user.name", "github-actions[bot]")
            run("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
            run("remote", "add", "origin", f"https://x-access-token:{token}@github.com/{repo}.git")
        (PUB_DIR / "live.json").write_text(text, "utf-8")
        run("add", "live.json")
        run("commit", "-q", "-m", f"盤中 {payload.get('updated', '')}", *([] if first else ["--amend"]))
        run("push", "-q", "-f", "origin", "HEAD:live")
    except subprocess.CalledProcessError as e:
        log.warning("推送 live 分支失敗：%s", (e.stderr or "")[-300:])


def restore_state(today: str) -> dict:
    """同一天重新啟動時，接續之前的預警紀錄（避免重複通知）。"""
    blank = {"date": today, "alerts": {}, "hold_alerted": [], "last_notify": 0, "pending": [], "official": None}
    cands = []
    if STATE_F.exists():
        cands.append(STATE_F.read_text("utf-8"))
    if os.environ.get("GITHUB_TOKEN"):
        try:
            subprocess.run(["git", "fetch", "-q", "origin", "live"], cwd=ROOT, check=True, capture_output=True, timeout=60)
            cands.append(subprocess.run(["git", "show", "FETCH_HEAD:live.json"], cwd=ROOT, check=True,
                                        capture_output=True, text=True).stdout)
        except Exception:  # noqa: BLE001
            pass
    for c in cands:
        try:
            s = json.loads(c)
            s = s.get("state", s)
            if s.get("date") == today:
                log.info("接續今天的監控紀錄：已預警 %d 檔", len(s.get("alerts", {})))
                return {**blank, **s}
        except Exception:  # noqa: BLE001
            continue
    return blank


# ------------------------------------------------------------------ 單次掃描
def scan(q: pd.DataFrame, ref: pd.DataFrame, stocks: pd.DataFrame, lc: dict, now: dt.datetime,
         watch: set[str]) -> pd.DataFrame:
    q = q.drop_duplicates("code").merge(ref, left_on="code", right_index=True, how="left").merge(
        stocks[["code", "industry", "market"]].drop_duplicates("code"), on="code", how="left")
    q["industry"] = q.industry.fillna("")
    q["chg"] = (q.price / q.yclose - 1) * 100
    q["vol_x"] = q.vol_lots * 1000 / q.avg5.where(q.avg5 > 0)
    q["proj_x"] = q.vol_x / vol_fraction(now)
    q["watch"] = q.code.isin(watch)
    cond = (
        (q.proj_x >= lc.get("volume_multiple", 3))
        & (q.vol_x >= lc.get("min_actual_multiple", 1))
        & (q.vol_lots >= lc.get("min_volume_lots", 300))
        & (q.chg >= lc.get("min_change_pct", 3)) & (q.chg <= lc.get("max_change_pct", 10.5))
        & (q.price > q.open)
        & (q.price > q.hi)
        & (q.price >= lc.get("min_price", 10))
    )
    q["hit"] = cond.fillna(False)
    return q


def holdings_view(q: pd.DataFrame, now: dt.datetime, shrink: float, drop_pct: float) -> list[dict]:
    pos = positions.load()
    if pos.empty:
        return []
    today = now.date().isoformat()
    pos = pos[(pos.status == "open") & (pos.signal_date != today)]
    qi = q.set_index("code")
    frac = vol_fraction(now)
    out = []
    for r in pos.itertuples():
        if r.code not in qi.index:
            continue
        x = qi.loc[r.code]
        entry = r.entry_price if pd.notna(r.entry_price) else r.alert_price
        price = x.price
        ret = (price / float(entry) - 1) * 100 if price and entry and pd.notna(entry) else None
        proj_ratio = (x.vol_lots * 1000 / frac / float(r.surge_volume)
                      if pd.notna(r.surge_volume) and float(r.surge_volume) > 0 else None)
        status = "—"
        if proj_ratio is not None:
            status = "可能量縮出場" if proj_ratio < shrink else "量能維持"
        out.append({"code": r.code, "name": r.name, "signal_date": r.signal_date, "entry": _num(entry),
                    "price": _num(price), "ret": _num(ret), "chg": _num(x.chg),
                    "vol_lots": _num(x.vol_lots, 0), "proj_ratio": _num(proj_ratio),
                    "status": status, "drop_alert": ret is not None and ret <= -drop_pct})
    return sorted(out, key=lambda d: d["ret"] if d["ret"] is not None else 0)


def official_result(today: str) -> dict | None:
    sf = ROOT / "data" / "intraday_state.json"
    if not sf.exists():
        return None
    s = json.loads(sf.read_text())
    if s.get("last_run") != today:
        return None
    pos = positions.load()
    pos = pos[pos.signal_date == today]
    return {"time": s.get("time"), "count": s.get("hits", len(pos)),
            "stocks": [{"code": r.code, "name": r.name, "price": _num(r.alert_price)} for r in pos.itertuples()]}


def stock_row(r, first: dict | None = None) -> dict:
    r = r._asdict() if hasattr(r, "_asdict") else r.to_dict()  # Series 的 .name 是索引，不能用屬性取
    d = {"code": r["code"], "name": r["name"], "industry": r["industry"], "market": r["market"],
         "price": _num(r["price"]), "chg": _num(r["chg"]), "vol_lots": _num(r["vol_lots"], 0),
         "vol_x": _num(r["vol_x"], 1), "proj_x": _num(r["proj_x"], 1), "watch": bool(r["watch"]), "hit": bool(r["hit"])}
    if first:
        d.update(first_time=first["time"], first_price=_num(first["price"]),
                 since=_num((r["price"] / first["price"] - 1) * 100) if r["price"] and first["price"] else None)
    return d


# ------------------------------------------------------------------ 通知
def alert_md(rows: list[dict], holds: list[dict], senti: dict | None, page: str) -> str:
    lines = []
    if senti:
        lines += [sentiment.md_line(senti, intraday=True), ""]
    if holds:
        lines += ["## 🔻 持股跌幅警示", "", "| 股票 | 進場價 | 現價 | 報酬 |", "|---|--:|--:|--:|"]
        lines += [f"| {h['code']} {h['name']} | {h['entry']} | {h['price']} | {h['ret']:+.2f}% |" for h in holds]
        lines += ["", "<sub>回測的出場規則只有「量縮」，這只是提醒你注意，不代表一定要賣。</sub>", ""]
    if rows:
        lines += ["## ⚡ 盤中早期預警（尚未收盤確認）", "",
                  "| 預警時間 | 股票 | 產業 | 現價 | 漲幅 | 目前量(張) | 預估全日量比 | 備註 |",
                  "|---|---|---|--:|--:|--:|--:|---|"]
        for r in rows:
            mk = "TW" if r.get("market") == "TWSE" else "TWO"
            note = "⭐昨日觀察名單" if r.get("watch") else ""
            lines.append(f"| {r['first_time']} | [{r['code']} {r['name']}](https://tw.stock.yahoo.com/quote/{r['code']}.{mk}) | "
                         f"{r.get('industry') or ''} | {r['price']} | {r['chg']:+.2f}% | {r['vol_lots']:,.0f} | "
                         f"{r['proj_x']}× | {note} |")
        lines += ["", "<sub>早期預警 = 盤中「預估」全日量達 5 日均量 3 倍、漲 3% 以上、突破 60 日新高。"
                  "回測的進場點是收盤前，太早進場常遇到衝高回落；13:12 會再發正式進場提醒。</sub>"]
    if page:
        lines += ["", f"👉 [盤中即時網頁]({page})"]
    lines.append("\n<sub>僅供參考，不構成投資建議。</sub>")
    return "\n".join(lines)


# ------------------------------------------------------------------ 主程式
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="測試：只掃一次、不等開盤、不通知")
    ap.add_argument("--once", action="store_true", help="只掃一次")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text("utf-8"))
    lc, ic = cfg.get("live", {}), cfg.get("intraday", {})
    if not lc.get("enabled", True) and not a.test:
        log.info("盤中連續監控已關閉")
        return 0

    now = dt.datetime.now(TZ)
    today = now.date().isoformat()
    if now.weekday() >= 5 and not a.test:
        log.info("週末不監控")
        return 0
    end = _hm(lc.get("end", "13:25"), now)
    if now >= end and not a.test:
        log.info("已超過 %s，今天的盤中監控結束", lc.get("end", "13:25"))
        return 0
    open_t = _hm("09:01", now)
    if now < open_t and not a.test:
        log.info("等待開盤（%.0f 秒）", (open_t - now).total_seconds())
        time.sleep((open_t - now).total_seconds())

    markets = cfg.get("settings", {}).get("markets", ["TWSE", "TPEX"])
    stocks = fetch.load_stock_list(markets)
    hist = fetch.load_history()
    hist = hist[hist.date < today]
    ref = build_ref(hist, int(lc.get("new_high_days", 60)))
    watch = watchlist()
    log.info("觀察名單 %d 檔", len(watch))
    st = restore_state(today)
    interval = float(lc.get("interval_min", 3)) * 60
    alert_start = _hm(lc.get("alert_start", "09:30"), now)
    official_t = _hm(lc.get("official_time", "13:12"), now)
    shrink = float(ic.get("exit_shrink_ratio", 0.5))
    drop_pct = float(lc.get("hold_drop_alert_pct", 7))
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    page = f"https://{repo.split('/')[0].lower()}.github.io/{repo.split('/')[1]}/live.html" if "/" in repo else ""
    scans = 0

    while True:
        t0 = time.time()
        now = dt.datetime.now(TZ)
        q, day = intraday.fetch_quotes(stocks)
        if q.empty:
            log.warning("這次抓不到報價")
        elif day and day != today.replace("-", "") and not a.test:
            log.info("今天（%s）沒有交易（MIS 日期 %s），可能休市", today, day)
            return 0
        else:
            scans += 1
            q = scan(q, ref, stocks, lc, now, watch)
            hm = now.strftime("%H:%M")
            if now >= alert_start or a.test:
                for r in q[q.hit].itertuples():
                    if r.code not in st["alerts"]:
                        st["alerts"][r.code] = {"time": hm, "price": float(r.price)}
                        st["pending"].append(r.code)
            qi = q.set_index("code", drop=False)
            alerts = [stock_row(qi.loc[c], f) for c, f in st["alerts"].items() if c in qi.index]
            alerts.sort(key=lambda d: d["first_time"], reverse=True)
            cands = [stock_row(r) for r in q[q.hit].sort_values("proj_x", ascending=False).head(60).itertuples()]
            holds = holdings_view(q, now, shrink, drop_pct)

            # 13:12 正式進場提醒（與回測相同條件）
            if now >= official_t and not st.get("official") and not a.test:
                try:
                    intraday.main([])
                except Exception as e:  # noqa: BLE001
                    log.error("正式提醒失敗：%s", e)
                st["official"] = official_result(today) or {"time": hm, "count": 0, "stocks": []}

            try:
                senti = sentiment.from_quotes(q, hist)
            except Exception as e:  # noqa: BLE001
                log.warning("情緒失敗：%s", e)
                senti = None

            # 通知：持股大跌立即；早期預警每 notify_interval_min 分鐘彙整一次
            new_drop = [h for h in holds if h["drop_alert"] and h["code"] not in st["hold_alerted"]]
            due = time.time() - st["last_notify"] >= float(lc.get("notify_interval_min", 30)) * 60
            pend = [d for d in alerts if d["code"] in st["pending"]]
            if lc.get("notify", True) and not a.test and (new_drop or (pend and due)):
                title = f"【盤中預警】{today} {hm}｜" + "、".join(
                    ([f"持股警示 {len(new_drop)}"] if new_drop else []) + ([f"早期預警 {len(pend)}"] if pend else []))
                try:
                    notify.send(title, alert_md(sorted(pend, key=lambda d: d["first_time"]), new_drop, senti, page))
                    st["last_notify"] = time.time()
                    st["pending"] = []
                    st["hold_alerted"] += [h["code"] for h in new_drop]
                except Exception as e:  # noqa: BLE001
                    log.error("通知失敗：%s", e)

            payload = {
                "updated": now.strftime("%Y-%m-%d %H:%M:%S"), "date": today, "scans": scans,
                "interval_min": interval / 60, "end": lc.get("end", "13:25"),
                "vol_fraction": round(vol_fraction(now), 3), "alert_start": lc.get("alert_start", "09:30"),
                "market": {k: v for k, v in (senti or {}).items()},
                "alerts": alerts, "candidates": cands, "holdings": holds, "official": st.get("official"),
                "quotes": len(q), "state": st,
            }
            publish(_clean(payload))
            STATE_F.write_text(json.dumps(st, ensure_ascii=False), "utf-8")
            log.info("%s 掃描 %d 檔｜符合 %d｜今日預警 %d｜持股 %d（%.0f 秒）",
                     hm, len(q), len(cands), len(alerts), len(holds), time.time() - t0)

        if a.test or a.once:
            return 0
        nxt = t0 + interval
        if not st.get("official") and t0 < official_t.timestamp() < nxt:
            nxt = official_t.timestamp()  # 準時在 13:12 做正式提醒
        if dt.datetime.fromtimestamp(nxt, TZ) >= end:
            break
        time.sleep(max(5, nxt - time.time()))

    # 收盤前最後一次：確保正式提醒有做
    if not st.get("official"):
        try:
            intraday.main([])
        except Exception as e:  # noqa: BLE001
            log.error("正式提醒失敗：%s", e)
    log.info("盤中監控結束，共掃描 %d 次", scans)
    return 0


if __name__ == "__main__":
    sys.exit(main())
