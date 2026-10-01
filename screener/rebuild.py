"""只改網頁版面時，用現有資料重新產生 site/（不抓資料、不篩選、不通知）。

用法：python -m screener.rebuild
合併到 main 的修改如果動到 screener/report.py 或 live_page.html，GitHub Actions（pages.yml）會自動執行並發佈。
"""
from __future__ import annotations

import logging
import sys

from . import report
from .main import ROOT, SITE_DIR


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if not (SITE_DIR / "index.html").exists() and not (ROOT / "data" / "report_data.json").exists():
        logging.error("還沒有任何報表可以重畫")
        return 1
    date = report.rebuild(SITE_DIR, ROOT / "data" / "report_data.json")
    logging.info("已用目前的版面重新產生 %s 的報表與盤中頁", date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
