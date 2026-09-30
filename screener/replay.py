"""盲測回放：從過去某天開始一天一天解鎖資料，由 Claude 依自己的判斷買賣，累積經驗。

- 股票代號匿名化（S+數字）、不顯示日期，只看得到「當天收盤時」已知的價量資訊，避免用記憶作弊。
- 規則同虛擬帳戶：收盤買、隔天開盤賣；最多 4 檔、每檔 ≤ 總資產 25%；國泰證券電子下單費率。

用法（state 預設 replay_state.json）：
  python -m screener.replay init  --data backtest.csv.gz [--start 2022-09-01]
  python -m screener.replay show  --data backtest.csv.gz
  python -m screener.replay act   --data backtest.csv.gz --buy S12345,S23456 --sell S34567 --note "理由"
  python -m screener.replay report --data backtest.csv.gz   # 含同期「純規則」與大盤對照
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import random
from pathlib import Path

import numpy as np
import pandas as pd

from . import rules

ROOT = Path(__file__).resolve().parent.parent
FEE = 0.001425 * 0.28
TAX = 0.003


def fee(x: float) -> int:
    return max(1, math.floor(x * FEE))


def load(data: str) -> dict:
    cache = Path(data).with_suffix(".panel.pkl")
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    h = pd.read_csv(data, dtype={"code": str}, usecols=["date", "code", "open", "high", "low", "close", "volume"])
    p = rules.Panel(h)
    sl = pd.read_csv(ROOT / "data" / "stock_list.csv", dtype=str).drop_duplicates("code").set_index("code")
    d = dict(C=p.close, O=p.open, H=p.high, L=p.low, V=p.volume, TR=p.traded,
             ind=sl.industry.reindex(p.close.columns).fillna("其他"))
    cache.write_bytes(pickle.dumps(d))
    return d


def aliases(codes: list[str], salt: int) -> dict[str, str]:
    ids = random.Random(salt).sample(range(10000, 99999), len(codes))
    return {c: f"S{i}" for c, i in zip(sorted(codes), ids)}


def feat(D: dict, t: int) -> dict:
    C, O, H, L, V, TR = D["C"], D["O"], D["H"], D["L"], D["V"], D["TR"]
    c, pc = C.iloc[t], C.iloc[t - 1]
    chg = (c / pc - 1) * 100
    va5 = V.iloc[t - 5:t].mean()
    vx = V.iloc[t] / va5.where(va5 > 0)
    hi60 = H.iloc[t - 60:t].max()
    ma = lambda n: C.iloc[t - n + 1:t + 1].mean()
    ma50, ma150, ma200 = ma(50), ma(150), ma(200)
    ma200p = C.iloc[t - 219:t - 19].mean()
    hi250, lo250 = H.iloc[t - 249:t + 1].max(), L.iloc[t - 249:t + 1].min()
    tt = (c > ma50) & (ma50 > ma150) & (ma150 > ma200) & (ma200 > ma200p) & (c >= 1.3 * lo250) & (c >= 0.75 * hi250)
    tr = TR.iloc[t] & TR.iloc[t - 1]
    E = tr & (V.iloc[t] / 1000 >= 500) & (c >= 10) & (chg.abs() <= 10.5) & (vx >= 3) & (chg >= 3) & (c > O.iloc[t]) & (c > hi60)
    su = (tr & (vx >= 3) & (chg >= 3) & (V.iloc[t] / 1000 >= 300)).astype(int)
    peers = su.groupby(D["ind"]).transform("sum") - su
    return dict(c=c, chg=chg, vx=vx, tt=tt, E=E, peers=peers, r20=(c / C.iloc[t - 20] - 1) * 100,
                dist52=(c / hi250 - 1) * 100)


def mkt_ret(D: dict) -> pd.Series:
    C, V, TR = D["C"], D["V"], D["TR"]
    return (C / C.shift(1) - 1).where(TR & TR.shift(1, fill_value=False) & (V / 1000 >= 100)).clip(-.11, .11).mean(axis=1)


def equity(st: dict, D: dict, t: int) -> float:
    return st["cash"] + sum(p["sh"] * float(D["C"][c].iloc[t]) for c, p in st["pos"].items())


def show(st: dict, D: dict) -> str:
    t = st["t"]; f = feat(D, t); al = aliases(list(D["C"].columns), st["salt"]); ind = D["ind"]
    r = mkt_ret(D)
    m20 = ((1 + r.iloc[t - 19:t + 1]).prod() - 1) * 100
    C, V, TR = D["C"], D["V"], D["TR"]
    live = TR.iloc[t] & (V.iloc[t] / 1000 >= 100)
    above = ((C.iloc[t] > C.iloc[t - 19:t + 1].mean()) & live).sum() / max(1, live.sum()) * 100
    eq = equity(st, D, t)
    out = [f"=== 第 {t - st['t0'] + 1} 天收盤 ===  大盤等權今日 {r.iloc[t] * 100:+.2f}%｜近20日 {m20:+.1f}%｜站上月線 {above:.0f}%",
           f"現金 {st['cash']:,.0f}｜總資產 {eq:,.0f}（{(eq / 1e5 - 1) * 100:+.2f}%）"]
    for c, p in st["pos"].items():
        cc = float(C[c].iloc[t])
        out.append(f"  持有 {al[c]} {ind[c]} {p['sh']}股 成本{p['cost']:.2f} 現{cc:.2f} {(cc / p['cost'] - 1) * 100:+.1f}% "
                   f"持{t - p['t']}天 今量/爆量日 {float(V[c].iloc[t]) / p['sv']:.0%} 今漲跌{float(f['chg'][c]):+.1f}%")
    cand = list(f["E"][f["E"]].index)
    rows = sorted(cand, key=lambda c: -f["vx"][c])[:15]
    out.append(f"今日符合爆量突破 {len(cand)} 檔（列前 {len(rows)}，依量比）：")
    for c in rows:
        lim = "(漲停買不到)" if f["chg"][c] >= 9.5 else ""
        out.append(f"  {al[c]} {ind[c]}｜價{f['c'][c]:.0f} 漲{f['chg'][c]:+.1f}%{lim} 量比{f['vx'][c]:.1f}｜"
                   f"趨勢樣板{'✓' if f['tt'][c] else '✗'}｜族群同步{int(f['peers'][c])}｜前20日{f['r20'][c]:+.0f}%｜距52週高{f['dist52'][c]:+.0f}%")
    if t + 1 >= len(C.index):
        out.append("（資料已到盡頭，回放結束）")
    return "\n".join(out)


def act(st: dict, D: dict, buy: list[str], sell: list[str], note: str) -> str:
    t = st["t"]; al = aliases(list(D["C"].columns), st["salt"]); inv = {v: k for k, v in al.items()}
    C, O, V = D["C"], D["O"], D["V"]
    f = feat(D, t); msgs = []
    eq = equity(st, D, t)
    for a in buy:
        c = inv.get(a)
        if not c or c in st["pos"] or len(st["pos"]) >= 4 or f["chg"][c] >= 9.5:
            msgs.append(f"略過買進 {a}（不存在/已持有/滿倉/漲停）"); continue
        px = float(C[c].iloc[t]); sh = int(min(eq * 0.25, st["cash"]) // px)
        while sh > 0 and sh * px + fee(sh * px) > st["cash"]:
            sh -= 1
        if sh <= 0:
            msgs.append(f"現金不足，買不了 {a}"); continue
        amt = sh * px; fe = fee(amt); st["cash"] -= amt + fe
        st["pos"][c] = dict(sh=sh, cost=(amt + fe) / sh, t=t, sv=float(V[c].iloc[t]))
        st["trades"].append(dict(d=t - st["t0"] + 1, side="buy", id=a, sh=sh, px=round(px, 2)))
    pend = [inv[a] for a in sell if inv.get(a) in st["pos"]]
    if note:
        st["log"] = (st.get("log", []) + [dict(d=t - st["t0"] + 1, note=note)])[-300:]
    t += 1; st["t"] = t
    for c in pend:
        p = st["pos"].pop(c)
        o = float(O[c].iloc[t]); px = o if not math.isnan(o) else float(C[c].iloc[t - 1])
        amt = p["sh"] * px; net = amt - fee(amt) - math.floor(amt * TAX); st["cash"] += net
        ret = (net / (p["sh"] * p["cost"]) - 1) * 100
        st["trades"].append(dict(d=t - st["t0"] + 1, side="sell", id=al[c], sh=p["sh"], px=round(px, 2),
                                 pnl=round(net - p["sh"] * p["cost"]), ret=round(ret, 2), hold=t - p["t"]))
        msgs.append(f"開盤賣出 {al[c]} @{px:.2f} 報酬 {ret:+.2f}%（持 {t - p['t']} 天）")
    st["eq"] = (st.get("eq", []) + [round(equity(st, D, t))])
    return "\n".join(msgs + [show(st, D)])


def baseline(D: dict, t0: int, t1: int, tt_only: bool) -> tuple[float, list[float]]:
    """同期「純規則」：有訊號就依量比買滿 4 檔、量縮一半隔天開盤賣。"""
    C, O, V = D["C"], D["O"], D["V"]; cash = 1e5; pos = {}; res = []
    for t in range(t0, t1):
        f = feat(D, t); eq = cash + sum(p["sh"] * float(C[c].iloc[t]) for c, p in pos.items())
        sells = [c for c, p in pos.items() if t > p["t"] and float(V[c].iloc[t]) < 0.5 * p["sv"]]
        cand = sorted([c for c in f["E"][f["E"]].index if f["chg"][c] < 9.5 and c not in pos and (not tt_only or f["tt"][c])],
                      key=lambda c: -f["vx"][c])
        for c in cand:
            if len(pos) >= 4: break
            px = float(C[c].iloc[t]); sh = int(min(eq * .25, cash) // px)
            while sh > 0 and sh * px + fee(sh * px) > cash: sh -= 1
            if sh <= 0: continue
            cash -= sh * px + fee(sh * px); pos[c] = dict(sh=sh, cost=(sh * px + fee(sh * px)) / sh, t=t, sv=float(V[c].iloc[t]))
        for c in sells:
            p = pos.pop(c); px = float(O[c].iloc[t + 1]); amt = p["sh"] * px
            net = amt - fee(amt) - math.floor(amt * TAX); cash += net; res.append(net / (p["sh"] * p["cost"]) - 1)
    eq = cash + sum(p["sh"] * float(C[c].iloc[t1]) for c, p in pos.items())
    return (eq / 1e5 - 1) * 100, res


def report(st: dict, D: dict, with_baseline: bool = True) -> dict:
    t0, t = st["t0"], st["t"]; C = D["C"]
    r = mkt_ret(D); ew = ((1 + r.iloc[t0 + 1:t + 1]).prod() - 1) * 100
    sells = [x for x in st["trades"] if x["side"] == "sell"]
    eqs = np.array(st.get("eq") or [1e5]); dd = float(((eqs / np.maximum.accumulate(eqs)) - 1).min() * 100)
    out = dict(days=t - t0, ret=round((equity(st, D, t) / 1e5 - 1) * 100, 2), ew=round(ew, 2), trades=len(sells),
               win=round(np.mean([s["ret"] > 0 for s in sells]) * 100, 1) if sells else None,
               avg=round(float(np.mean([s["ret"] for s in sells])), 2) if sells else None, maxdd=round(dd, 2),
               period=f"{str(C.index[t0])[:10]} ~ {str(C.index[t])[:10]}")
    if with_baseline:
        b1, r1 = baseline(D, t0, t, False); b2, r2 = baseline(D, t0, t, True)
        out.update(rule=round(b1, 2), rule_avg=round(np.mean(r1) * 100, 2) if r1 else None,
                   rule_tt=round(b2, 2), rule_tt_avg=round(np.mean(r2) * 100, 2) if r2 else None)
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["init", "show", "act", "report"])
    ap.add_argument("--data", default="backtest.csv.gz")
    ap.add_argument("--state", default="replay_state.json")
    ap.add_argument("--start", default="2022-09-01")
    ap.add_argument("--buy", default=""); ap.add_argument("--sell", default=""); ap.add_argument("--note", default="")
    ap.add_argument("--no-baseline", action="store_true")
    a = ap.parse_args(argv)
    D = load(a.data); sp = Path(a.state)
    if a.cmd == "init":
        dates = [str(x)[:10] for x in D["C"].index]
        t = next(i for i, d in enumerate(dates) if d >= a.start)
        t = max(t, 260)
        st = dict(t0=t, t=t, cash=100000.0, pos={}, trades=[], log=[], eq=[], salt=int.from_bytes(os.urandom(4), "big"))
        sp.write_text(json.dumps(st)); print(show(st, D)); return
    st = json.loads(sp.read_text())
    if a.cmd == "show":
        print(show(st, D))
    elif a.cmd == "act":
        if st["t"] + 1 >= len(D["C"].index):
            print("資料已到盡頭，回放結束"); return
        print(act(st, D, [x for x in a.buy.split(",") if x], [x for x in a.sell.split(",") if x], a.note))
        sp.write_text(json.dumps(st))
    else:
        print(json.dumps(report(st, D, not a.no_baseline), ensure_ascii=False))


if __name__ == "__main__":
    main()
