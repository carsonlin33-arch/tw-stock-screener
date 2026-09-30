"""追蹤盤中提醒過的股票，收盤後檢查「量縮」出場條件。

檔案：data/positions.csv（每一列是一筆盤中提醒的訊號）
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "data" / "positions.csv"
COLS = ["code", "name", "industry", "signal_date", "signal_time", "alert_price", "entry_price",
        "surge_volume", "status", "exit_signal_date", "exit_reason", "exit_close", "days_held", "est_return_pct"]


def load() -> pd.DataFrame:
    text = ["code", "name", "industry", "signal_date", "signal_time", "status", "exit_signal_date", "exit_reason"]
    if FILE.exists():
        df = pd.read_csv(FILE, dtype={c: str for c in text})
    else:
        df = pd.DataFrame(columns=COLS)
    return df.astype({c: object for c in text})


def save(df: pd.DataFrame) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    df[COLS].to_csv(FILE, index=False)


def add_signals(rows: list[dict]) -> None:
    df = load()
    new = pd.DataFrame(rows)
    new["status"] = "open"
    df = pd.concat([df, new], ignore_index=True)
    df = df.drop_duplicates(["code", "signal_date"], keep="first")
    save(df)


def update(hist: pd.DataFrame, data_date: str, shrink: float = 0.5, max_hold: int = 20,
           stop_pct: float | None = None) -> tuple[list[dict], list[dict]]:
    """用收盤後的完整資料更新持有中的訊號。回傳 (今天出現出場訊號的, 仍持有的)。"""
    df = load()
    if df.empty:
        return [], []
    dates = sorted(hist.date.unique())
    day = hist[hist.date == data_date].set_index("code")
    exits, holding = [], []
    for i, r in df.iterrows():
        if r.status != "open" or r.code not in day.index:
            if r.status == "open":
                holding.append(r.to_dict())
            continue
        vol, close = float(day.at[r.code, "volume"]), float(day.at[r.code, "close"])
        if r.signal_date == data_date:
            # 爆量當天：用收盤資料確認進場價與爆量日成交量
            df.at[i, "entry_price"] = close
            df.at[i, "surge_volume"] = vol
            df.at[i, "days_held"] = 0
            holding.append(df.loc[i].to_dict())
            continue
        if r.signal_date not in dates:
            continue
        held = sum(1 for d in dates if r.signal_date < d <= data_date)
        entry = float(r.entry_price) if pd.notna(r.entry_price) else float(r.alert_price)
        surge = float(r.surge_volume) if pd.notna(r.surge_volume) else None
        df.at[i, "days_held"] = held
        df.at[i, "est_return_pct"] = round((close / entry - 1) * 100, 2)
        reason = None
        if stop_pct and close <= entry * (1 - stop_pct / 100):
            reason = f"收盤跌破進場價 {stop_pct:g}%（停損）"
        elif surge and vol < shrink * surge:
            reason = f"量縮至爆量日 {vol / surge:.0%}"
        elif held >= max_hold:
            reason = f"已持有 {held} 天（上限）"
        if reason:
            df.at[i, "status"] = "closed"
            df.at[i, "exit_signal_date"] = data_date
            df.at[i, "exit_reason"] = reason
            df.at[i, "exit_close"] = close
            exits.append(df.loc[i].to_dict())
        else:
            d = df.loc[i].to_dict()
            d["vol_ratio"] = vol / surge if surge else None
            holding.append(d)
    save(df)
    log.info("出場訊號 %d 檔，持有中 %d 檔", len(exits), len(holding))
    return exits, holding


def build_md(exits: list[dict], holding: list[dict]) -> str:
    if not exits and not holding:
        return ""
    lines = []
    if exits:
        lines += ["## 🔴 出場提醒（明天開盤賣出）", "",
                  "| 股票 | 進場日 | 進場價(收盤) | 今日收盤 | 估計報酬 | 原因 |", "|---|---|--:|--:|--:|---|"]
        for r in exits:
            lines.append(f"| {r['code']} {r['name']} | {r['signal_date']} | {r['entry_price']:.2f} | "
                         f"{r['exit_close']:.2f} | {r['est_return_pct']:+.2f}% | {r['exit_reason']} |")
        lines.append("")
    if holding:
        lines += ["## 🟡 持有中（尚未量縮）", "",
                  "| 股票 | 進場日 | 進場價 | 已持有 | 估計報酬 | 今日量 / 爆量日 |", "|---|---|--:|--:|--:|--:|"]
        for r in holding:
            vr = r.get("vol_ratio")
            ep = r.get("entry_price") if pd.notna(r.get("entry_price")) else r.get("alert_price")
            lines.append(f"| {r['code']} {r['name']} | {r['signal_date']} | "
                         f"{ep:.2f} | {int(r['days_held']) if pd.notna(r.get('days_held')) else 0} 天 | "
                         f"{(str(r['est_return_pct']) + '%') if pd.notna(r.get('est_return_pct')) else '—'} | "
                         f"{(f'{vr:.0%}') if vr else '—'} |")
        lines.append("")
    lines.append("<sub>出場規則：收盤成交量低於爆量日的一半，或收盤跌破進場價 10% → 隔天開盤賣出；最多持有 20 天。清單包含所有盤中提醒過的股票（不代表你實際買了）。</sub>")
    return "\n".join(lines) + "\n\n"
