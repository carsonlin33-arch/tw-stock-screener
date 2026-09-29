"""台股每日篩選器 主程式。

用法：
  python -m screener.main                 # 抓到今天（台北時間）並篩選
  python -m screener.main --date 2026-09-25
  python -m screener.main --no-email      # 不寄信
  python -m screener.main --force         # 就算今天已處理過也重跑
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import fetch, notify, report, rules

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "site"
RESULT_DIR = ROOT / "data" / "results"
STATE_FILE = ROOT / "data" / "state.json"

log = logging.getLogger("screener")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="資料日期 YYYY-MM-DD（預設今天，台北時間）")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-fetch", action="store_true", help="只用現有歷史資料，不抓新資料")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    cfg = yaml.safe_load(Path(a.config).read_text("utf-8"))
    st = cfg.get("settings", {})
    markets = st.get("markets", ["TWSE", "TPEX"])
    keep = int(st.get("history_days", 150))

    target = dt.date.fromisoformat(a.date) if a.date else dt.datetime.now(ZoneInfo("Asia/Taipei")).date()

    # 1. 更新歷史資料
    if a.no_fetch:
        hist = fetch.load_history()
    else:
        hist = fetch.update_history(target, markets, keep, st.get("source", "auto"))
    if hist.empty:
        log.error("沒有任何歷史資料")
        return 1
    hist = hist[hist.date <= target.isoformat()]
    data_date = hist.date.max()

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    if data_date != target.isoformat():
        log.info("%s 沒有新資料（休市或尚未公布），最新資料日為 %s", target, data_date)
        if not a.force:
            return 0
    if state.get("last_done") == data_date and not a.force:
        log.info("%s 已經處理過，略過（用 --force 可重跑）", data_date)
        return 0

    ndays = hist.date.nunique()
    if ndays < 61:
        log.warning("目前只有 %d 個交易日的資料，季線等長天期指標要累積到 61 天才會準確", ndays)

    # 2. 篩選
    exclude = set(map(str, st.get("exclude_codes") or []))
    hist = hist[~hist.code.isin(exclude)]
    panel = rules.Panel(hist)
    strategies = [s for s in cfg.get("strategies", []) if s.get("enabled", True)]
    hits = rules.run_strategies(panel, strategies, cfg.get("base_filter", []))
    strat_info = [
        {
            "name": s["name"],
            "desc": "、".join(rules.describe(c) for c in s.get("conditions", [])),
            "codes": hits[s["name"]],
        }
        for s in strategies
    ]
    for s in strat_info:
        log.info("【%s】%d 檔：%s", s["name"], len(s["codes"]), " ".join(s["codes"][:30]))

    # 3. 報表
    all_codes = sorted({c for v in hits.values() for c in v})
    rcfg = cfg.get("report", {})
    spark_days = int(rcfg.get("spark_days", 60))
    met = rules.metrics(panel, all_codes, spark_days)
    stocks = fetch.load_stock_list(markets)
    rows = report.build_rows(stocks, met, hits)
    title = rcfg.get("title", "台股每日篩選")
    scanned = int(panel.traded.iloc[-1].sum())
    html_for = lambda link: report.render_html(title, data_date, scanned, rows, strat_info, spark_days, link)
    report.write_site(SITE_DIR, data_date, html_for)

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    import pandas as pd

    pd.DataFrame(
        [{"date": data_date, "strategy": s["name"], "code": c} for s in strat_info for c in s["codes"]],
        columns=["date", "strategy", "code"],
    ).to_csv(RESULT_DIR / f"{data_date}.csv", index=False)

    # 4. Email
    ecfg = cfg.get("email", {})
    total = len(all_codes)
    if ecfg.get("enabled", True) and not a.no_email and (total or ecfg.get("send_when_empty")):
        report_url = os.environ.get("REPORT_URL", "")
        if not report_url and os.environ.get("GITHUB_REPOSITORY"):
            owner, repo = os.environ["GITHUB_REPOSITORY"].split("/")
            report_url = f"https://{owner.lower()}.github.io/{repo}/{data_date}.html"
        body = notify.build_email_html(title, data_date, report_url, strat_info, {r["code"]: r for r in rows})
        counts = "、".join(f"{s['name']} {len(s['codes'])}" for s in strat_info)
        page = (SITE_DIR / f"{data_date}.html").read_bytes()
        try:
            notify.send_email(f"【{title}】{data_date}｜{counts}", body, (f"report-{data_date}.html", page))
        except Exception as e:  # 寄信失敗不影響報表
            log.error("寄信失敗：%s", e)

    state["last_done"] = data_date
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    log.info("完成：%s，共 %d 檔被標記", data_date, total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
