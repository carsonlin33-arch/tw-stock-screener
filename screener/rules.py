"""篩選條件引擎。

所有條件都在「寬表」（列 = 日期、欄 = 股票代號）上向量化計算，
回傳同形狀的布林表；最後取最新一天判斷是否符合。

每個條件都可以加兩個通用參數：
  days_ago: N   用 N 天前的狀態判斷（例如 days_ago: 1 = 前一天）
  within:   N   最近 N 天內「任一天」成立即可（例如 3 天內曾漲停）
  not:   true   反向（不符合才算）
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class Panel:
    """把長表轉成各欄位的寬表，並快取常用指標。"""

    def __init__(self, hist: pd.DataFrame):
        hist = hist.dropna(subset=["close"])
        self.dates = sorted(hist.date.unique())
        piv = lambda c: hist.pivot(index="date", columns="code", values=c).sort_index()
        self.close = piv("close")
        self.open = piv("open").reindex_like(self.close)
        self.high = piv("high").reindex_like(self.close)
        self.low = piv("low").reindex_like(self.close)
        self.volume = piv("volume").reindex_like(self.close).fillna(0)
        # 沒成交的日子：收盤延用前一日（避免均線斷掉），但成交量為 0
        self.traded = self.close.notna()
        self.close = self.close.ffill()
        self._cache: dict = {}

    def ma(self, n: int) -> pd.DataFrame:
        k = ("ma", n)
        if k not in self._cache:
            self._cache[k] = self.close.rolling(n, min_periods=n).mean()
        return self._cache[k]

    def vol_avg(self, n: int) -> pd.DataFrame:
        """前 n 日平均量（不含當日）。"""
        k = ("va", n)
        if k not in self._cache:
            self._cache[k] = self.volume.shift(1).rolling(n, min_periods=n).mean()
        return self._cache[k]

    @property
    def prev_close(self) -> pd.DataFrame:
        return self.close.shift(1)

    @property
    def change_pct(self) -> pd.DataFrame:
        return (self.close / self.prev_close - 1) * 100

    def limit_price(self, up: bool = True) -> pd.DataFrame:
        raw = self.prev_close * (1.1 if up else 0.9)
        tick = pd.DataFrame(
            np.select(
                [raw < 10, raw < 50, raw < 100, raw < 500, raw < 1000],
                [0.01, 0.05, 0.1, 0.5, 1.0],
                default=5.0,
            ),
            index=raw.index,
            columns=raw.columns,
        )
        f = np.floor if up else np.ceil
        return f(raw / tick + (1e-6 if up else -1e-6)) * tick


def _ratio(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    return a / b.where(b > 0)


# ------------------------------------------------------------------ 條件定義
# 每個函式：(panel, 參數 dict) -> 布林寬表
def c_volume_vs_prev(p: Panel, a):
    """當日成交量 ≥ 前一日的 min 倍。"""
    return _ratio(p.volume, p.volume.shift(1)) >= a.get("min", 2)


def c_volume_vs_avg(p: Panel, a):
    """當日成交量 ≥ 前 days 日均量的 min 倍。"""
    return _ratio(p.volume, p.vol_avg(a.get("days", 5))) >= a.get("min", 2)


def c_volume(p: Panel, a):
    """成交量（張）介於 min~max。1 張 = 1000 股。"""
    lots = p.volume / 1000
    return (lots >= a.get("min", 0)) & (lots <= a.get("max", np.inf))


def c_price(p: Panel, a):
    return (p.close >= a.get("min", 0)) & (p.close <= a.get("max", np.inf))


def c_change_pct(p: Panel, a):
    """當日漲跌幅（%）介於 min~max。"""
    c = p.change_pct
    return (c >= a.get("min", -100)) & (c <= a.get("max", 100))


def _normal_day(p: Panel):
    """今天和前一天都有成交，且漲跌幅在 ±10.5% 內（排除停牌後恢復交易、減資等參考價重設）。"""
    return p.traded & p.traded.shift(1, fill_value=False) & (p.change_pct.abs() <= 10.5)


def c_limit_up(p: Panel, a):
    return _normal_day(p) & (p.close >= p.limit_price(True) - 1e-6)


def c_limit_down(p: Panel, a):
    return _normal_day(p) & (p.close <= p.limit_price(False) + 1e-6)


def c_above_ma(p: Panel, a):
    return p.close > p.ma(a.get("period", 60))


def c_below_ma(p: Panel, a):
    return p.close < p.ma(a.get("period", 60))


def c_cross_above_ma(p: Panel, a):
    """今天收盤站上均線，且前一天收盤還在均線下（真正的「突破」）。"""
    ma = p.ma(a.get("period", 60))
    return (p.close > ma) & (p.close.shift(1) <= ma.shift(1))


def c_cross_below_ma(p: Panel, a):
    ma = p.ma(a.get("period", 60))
    return (p.close < ma) & (p.close.shift(1) >= ma.shift(1))


def c_ma_rising(p: Panel, a):
    """均線上揚：今天的 MA 比 days 天前高。"""
    ma = p.ma(a.get("period", 60))
    return ma > ma.shift(a.get("days", 1))


def c_ma_bullish(p: Panel, a):
    """均線多頭排列：periods 由短到長，短均 > 長均。"""
    ps = a.get("periods", [5, 20, 60])
    out = pd.DataFrame(True, index=p.close.index, columns=p.close.columns)
    for s, l in zip(ps, ps[1:]):
        out &= p.ma(s) > p.ma(l)
    return out


def c_ma_bias(p: Panel, a):
    """乖離率（收盤相對均線，%）介於 min~max。"""
    b = (p.close / p.ma(a.get("period", 20)) - 1) * 100
    return (b >= a.get("min", -100)) & (b <= a.get("max", 100))


def c_new_high(p: Panel, a):
    """收盤創 days 日新高（突破前 days 日最高價）。"""
    n = a.get("days", 20)
    return p.close > p.high.shift(1).rolling(n, min_periods=n).max()


def c_new_low(p: Panel, a):
    n = a.get("days", 20)
    return p.close < p.low.shift(1).rolling(n, min_periods=n).min()


def c_up_days(p: Panel, a):
    """連續上漲 days 天（收盤一天比一天高）。"""
    up = (p.close > p.prev_close).astype(int)
    n = a.get("days", 3)
    return up.rolling(n, min_periods=n).sum() >= n


def c_red_candle(p: Panel, a):
    """紅K：收盤 > 開盤，實體至少 min_body%（相對前收）。"""
    body = (p.close - p.open) / p.prev_close * 100
    return body >= a.get("min_body", 0.0001)


def c_near_high(p: Panel, a):
    """收盤距離前 days 日最高價在 pct% 以內（尚未突破也算）。"""
    n = a.get("days", 60)
    hi = p.high.rolling(n, min_periods=n).max()
    return p.close >= hi * (1 - a.get("pct", 5) / 100)


def c_range_pct(p: Panel, a):
    """近 days 日振幅（最高-最低）/收盤，介於 min~max %。"""
    n = a.get("days", 20)
    r = (p.high.rolling(n, min_periods=n).max() - p.low.rolling(n, min_periods=n).min()) / p.close * 100
    return (r >= a.get("min", 0)) & (r <= a.get("max", 1000))


CONDITIONS = {
    "volume_vs_prev": (c_volume_vs_prev, "量比前日≥{min}倍"),
    "volume_vs_avg": (c_volume_vs_avg, "量比{days}日均量≥{min}倍"),
    "volume": (c_volume, "成交量{rng}張"),
    "price": (c_price, "股價{rng}元"),
    "change_pct": (c_change_pct, "漲跌幅{rng}%"),
    "limit_up": (c_limit_up, "漲停"),
    "limit_down": (c_limit_down, "跌停"),
    "above_ma": (c_above_ma, "站上MA{period}"),
    "below_ma": (c_below_ma, "跌破MA{period}"),
    "cross_above_ma": (c_cross_above_ma, "突破MA{period}"),
    "cross_below_ma": (c_cross_below_ma, "跌破MA{period}（當日）"),
    "ma_rising": (c_ma_rising, "MA{period}上揚"),
    "ma_bullish": (c_ma_bullish, "均線多頭排列{periods}"),
    "ma_bias": (c_ma_bias, "MA{period}乖離{rng}%"),
    "new_high": (c_new_high, "創{days}日新高"),
    "new_low": (c_new_low, "創{days}日新低"),
    "up_days": (c_up_days, "連漲{days}天"),
    "red_candle": (c_red_candle, "紅K"),
    "near_high": (c_near_high, "距{days}日高點{pct}%內"),
    "range_pct": (c_range_pct, "{days}日振幅{rng}%"),
}


def describe(cond: dict) -> str:
    fn, tpl = CONDITIONS[cond["type"]]
    args = {"min": "", "max": "", "days": "", "period": "", "periods": "", "pct": ""}
    args.update(cond)
    lo, hi = cond.get("min"), cond.get("max")
    args["rng"] = (
        f"{lo}~{hi}" if lo is not None and hi is not None
        else f"≥{lo}" if lo is not None else f"≤{hi}" if hi is not None else ""
    )
    text = tpl.format(**args)
    if cond.get("days_ago"):
        text = f"{cond['days_ago']}天前{text}"
    if cond.get("within"):
        text = f"近{cond['within']}天內曾{text}"
    if cond.get("not"):
        text = f"非（{text}）"
    return text


def evaluate(p: Panel, cond: dict) -> pd.Series:
    """回傳最新一天每檔股票是否符合此條件。"""
    t = cond.get("type")
    if t not in CONDITIONS:
        raise ValueError(f"不認得的條件類型：{t}（可用：{', '.join(CONDITIONS)}）")
    m = CONDITIONS[t][0](p, cond).fillna(False).astype(bool)
    if cond.get("days_ago"):
        m = m.shift(int(cond["days_ago"]), fill_value=False)
    if cond.get("within"):
        m = m.astype(int).rolling(int(cond["within"]), min_periods=1).max().astype(bool)
    res = m.iloc[-1]
    return ~res if cond.get("not") else res


def run_strategies(p: Panel, strategies: list[dict], base_filter: list[dict]) -> dict[str, list[str]]:
    """回傳 {策略名稱: [符合的股票代號]}。"""
    base = pd.Series(True, index=p.close.columns)
    base &= p.traded.iloc[-1]  # 當天有成交
    for c in base_filter:
        base &= evaluate(p, c)
    out = {}
    for s in strategies:
        if not s.get("enabled", True):
            continue
        m = base.copy()
        for c in s.get("conditions", []):
            m &= evaluate(p, c)
        out[s["name"]] = sorted(m[m].index)
    return out


def metrics(p: Panel, codes: list[str], spark_days: int = 60) -> pd.DataFrame:
    """報表要顯示的數值。"""
    last = lambda df: df.iloc[-1].reindex(codes)
    d = pd.DataFrame(index=codes)
    d["close"] = last(p.close)
    d["change_pct"] = last(p.change_pct)
    d["volume_lots"] = last(p.volume) / 1000
    d["vol_x_prev"] = last(_ratio(p.volume, p.volume.shift(1)))
    d["vol_x_avg5"] = last(_ratio(p.volume, p.vol_avg(5)))
    d["ma20"] = last(p.ma(20))
    d["ma60"] = last(p.ma(60))
    d["bias60"] = (d.close / d.ma60 - 1) * 100
    tail = p.close.iloc[-spark_days:]
    ma_tail = p.ma(60).iloc[-spark_days:]
    d["spark"] = [tail[c].round(2).tolist() for c in codes]
    d["spark_ma"] = [ma_tail[c].round(2).tolist() for c in codes]
    return d


# ============================================================
#  高檔鈍化後回檔：KD / MACD 條件（新增）
# ============================================================
def _kd(p: Panel):
    if "kd" not in p._cache:
        lo = p.low.rolling(9, min_periods=1).min()
        hi = p.high.rolling(9, min_periods=1).max()
        rsv = ((p.close - lo) / (hi - lo) * 100).where(hi > lo).fillna(50)
        seed = pd.DataFrame(50.0, index=[rsv.index[0]], columns=rsv.columns)
        k = pd.concat([seed, rsv]).ewm(alpha=1 / 3, adjust=False).mean().iloc[1:]
        k.index = rsv.index
        d = pd.concat([seed, k]).ewm(alpha=1 / 3, adjust=False).mean().iloc[1:]
        d.index = rsv.index
        p._cache["kd"] = (k, d)
    return p._cache["kd"]


def _osc(p: Panel):
    if "osc" not in p._cache:
        dif = p.close.ewm(span=12, adjust=False).mean() - p.close.ewm(span=26, adjust=False).mean()
        p._cache["osc"] = dif - dif.ewm(span=9, adjust=False).mean()
    return p._cache["osc"]


def c_kd_dunhua(p: Panel, a):
    """近 days 日內，K 值曾連續 run 天以上 > level（高檔鈍化）。"""
    k, _ = _kd(p)
    above = (k > a.get("level", 80)).astype(int)
    cs = above.cumsum()
    run = cs - cs.where(above == 0).ffill().fillna(0)
    return run.rolling(a.get("days", 60), min_periods=1).max() >= a.get("run", 3)


def c_kd_range(p: Panel, a):
    """K 值介於 min~max。"""
    k, _ = _kd(p)
    return (k >= a.get("min", 0)) & (k <= a.get("max", 100))


def c_kd_golden(p: Panel, a):
    """K 值在 D 值之上（黃金交叉狀態）。"""
    k, d = _kd(p)
    return k > d


def c_macd_improving(p: Panel, a):
    """MACD 柱狀體翻紅，或比 days 天前明顯縮短（綠柱變短）。"""
    o = _osc(p)
    return (o > 0) | (o > o.shift(a.get("days", 3)))


def c_retrace_ok(p: Panel, a):
    """回檔沒跌破漲幅的一半：收盤 ≥ 低點 + (近 days 日高點 − 近 base_days 日低點) × keep%。"""
    peak = p.high.rolling(a.get("days", 60), min_periods=1).max()
    base = p.low.rolling(a.get("base_days", 120), min_periods=1).min()
    return p.close >= base + (peak - base) * a.get("keep", 50) / 100


def c_vol_shrink(p: Panel, a):
    """近 short 日均量 < 近 long 日均量（量縮整理）。"""
    s = p.volume.rolling(a.get("short", 5), min_periods=1).mean()
    l = p.volume.rolling(a.get("long", 20), min_periods=1).mean()
    return s < l


def c_avg_volume(p: Panel, a):
    """近 days 日平均成交量（張）≥ min。"""
    return p.volume.rolling(a.get("days", 20), min_periods=1).mean() / 1000 >= a.get("min", 1000)


CONDITIONS.update({
    "kd_dunhua": (c_kd_dunhua, "近{days}日曾高檔鈍化"),
    "kd_range": (c_kd_range, "K值{rng}"),
    "kd_golden": (c_kd_golden, "KD黃金交叉"),
    "macd_improving": (c_macd_improving, "MACD柱轉強"),
    "retrace_ok": (c_retrace_ok, "回檔未破漲幅一半"),
    "vol_shrink": (c_vol_shrink, "量縮整理"),
    "avg_volume": (c_avg_volume, "{days}日均量{rng}張"),
})

# 兩個新策略（不用改 config.yaml，會自動加在策略清單最前面）
DUNHUA_STRATEGIES = [
    {"name": "鈍化回檔－進場訊號", "conditions": [
        {"type": "kd_dunhua", "days": 60, "run": 3},     # 近 60 日 K 值曾連 3 天 > 80
        {"type": "kd_range", "min": 20, "max": 45},      # 現在 K 值回到低檔
        {"type": "above_ma", "period": 60},              # 守住季線
        {"type": "ma_rising", "period": 60, "days": 20}, # 季線往上
        {"type": "retrace_ok", "keep": 50},              # 回檔沒跌破漲幅一半
        {"type": "avg_volume", "days": 20, "min": 1000}, # 20 日均量 1000 張以上
        {"type": "kd_golden"},                           # KD 黃金交叉
        {"type": "macd_improving", "days": 3},           # MACD 柱翻紅或綠柱縮短
        {"type": "above_ma", "period": 5},               # 站回 5 日線
    ]},
    {"name": "鈍化回檔－觀察中", "conditions": [
        {"type": "kd_dunhua", "days": 60, "run": 3},
        {"type": "kd_range", "min": 20, "max": 45},
        {"type": "above_ma", "period": 60},
        {"type": "ma_rising", "period": 60, "days": 20},
        {"type": "retrace_ok", "keep": 50},
        {"type": "avg_volume", "days": 20, "min": 1000},
        {"type": "kd_golden", "not": True},              # 還沒黃金交叉：先觀察
    ]},
]

_run_strategies_orig = run_strategies


def run_strategies(p, strategies, base_filter):  # noqa: F811
    names = {s.get("name") for s in strategies}
    for i, s in enumerate(DUNHUA_STRATEGIES):
        if s["name"] not in names:
            strategies.insert(i, dict(s, enabled=True))
    return _run_strategies_orig(p, strategies, base_filter)


# 報表點股票名稱時，K 線下方加畫 KD 與 MACD
try:
    from . import report as _rep

    _IND_JS = r"""
  try{(function(){
    let ex=document.getElementById('kc2');
    if(!ex){ex=document.createElement('div');ex.id='kc2';box.after(ex);}
    ex.innerHTML='<div style="font-size:12px;color:var(--muted);margin-top:8px">KD：黃 K、藍 D・虛線 80 / 20・黃底 = 高檔鈍化（K 連 3 天 &gt; 80）</div><canvas id="kdc" style="display:block;width:100%;height:120px"></canvas><div style="font-size:12px;color:var(--muted);margin-top:6px">MACD 柱狀體：紅 = 多方、綠 = 空方</div><canvas id="mdc" style="display:block;width:100%;height:90px"></canvas>';
    const T=[],H=[],Lw=[],C=[];
    OHLC.dates.forEach((d,i)=>{const x=k[i];if(!x)return;T.push(d);H.push(x[1]);Lw.push(x[2]);C.push(x[3]);});
    let kk=50,dd=50;const K=[],D=[];
    for(let i=0;i<C.length;i++){const a=Math.max(0,i-8),hh=Math.max(...H.slice(a,i+1)),ll=Math.min(...Lw.slice(a,i+1));
      const rsv=hh>ll?(C[i]-ll)/(hh-ll)*100:50;kk=kk*2/3+rsv/3;dd=dd*2/3+kk/3;K.push(kk);D.push(dd);}
    const ema=(arr,n)=>{const r=[];const al=2/(n+1);arr.forEach((v,i)=>r.push(i?r[i-1]+al*(v-r[i-1]):v));return r;};
    const e12=ema(C,12),e26=ema(C,26),dif=C.map((_,i)=>e12[i]-e26[i]),sg=ema(dif,9),osc=dif.map((v,i)=>v-sg[i]);
    const muted=cv('--muted')||'#888',line=cv('--line')||'#ddd';
    function prep(cn){const r=window.devicePixelRatio||1,w=cn.clientWidth,h=cn.clientHeight;cn.width=w*r;cn.height=h*r;const g=cn.getContext('2d');g.setTransform(r,0,0,r,0,0);g.clearRect(0,0,w,h);return [g,w,h];}
    function paint(){
      const xs=T.map(t=>chart.timeScale().timeToCoordinate(t));
      const bw=Math.max(1,(chart.timeScale().width()/Math.max(1,(chart.timeScale().getVisibleLogicalRange()||{to:T.length,from:0}).to-(chart.timeScale().getVisibleLogicalRange()||{from:0}).from))*0.7);
      const pw=chart.timeScale().width();
      let [g,w,h]=prep(document.getElementById('kdc'));const Y=v=>4+(100-v)/100*(h-8);
      for(let i=0;i<K.length;){if(K[i]>80){let j=i;while(j<K.length&&K[j]>80)j++;if(j-i>=3&&xs[i]!=null&&xs[j-1]!=null){g.fillStyle='rgba(237,161,0,.22)';g.fillRect(xs[i]-bw/2,0,xs[j-1]-xs[i]+bw,h);}i=j;}else i++;}
      g.strokeStyle=muted;g.setLineDash([3,3]);g.lineWidth=1;[80,20].forEach(v=>{g.beginPath();g.moveTo(0,Y(v));g.lineTo(pw,Y(v));g.stroke();g.fillStyle=muted;g.font='11px sans-serif';g.fillText(String(v),pw+6,Y(v)+4);});g.setLineDash([]);
      [[K,'#e0a100'],[D,'#2a78d6']].forEach(([arr,col])=>{g.strokeStyle=col;g.lineWidth=1.8;g.beginPath();let st=false;arr.forEach((v,i)=>{const x=xs[i];if(x==null)return;st?g.lineTo(x,Y(v)):g.moveTo(x,Y(v));st=true;});g.stroke();});
      const n=K.length-1;g.fillStyle=muted;g.fillText('K '+K[n].toFixed(1)+'  D '+D[n].toFixed(1),4,12);
      [g,w,h]=prep(document.getElementById('mdc'));let mx=0;osc.forEach((v,i)=>{if(xs[i]!=null)mx=Math.max(mx,Math.abs(v));});mx=mx||1;const mid=h/2;
      g.strokeStyle=line;g.beginPath();g.moveTo(0,mid);g.lineTo(pw,mid);g.stroke();
      osc.forEach((v,i)=>{const x=xs[i];if(x==null)return;g.fillStyle=v>=0?up:dn;const y=mid-v/mx*(mid-4);g.fillRect(x-bw/2,Math.min(y,mid),bw,Math.max(1,Math.abs(y-mid)));});
    }
    if(window._indSub)try{window._indSubChart.timeScale().unsubscribeVisibleLogicalRangeChange(window._indSub);}catch(e){}
    window._indSub=()=>requestAnimationFrame(paint);window._indSubChart=chart;
    chart.timeScale().subscribeVisibleLogicalRangeChange(window._indSub);
    setTimeout(paint,50);
  })();}catch(e){console.warn('KD/MACD draw failed',e&&e.message);}
"""
    _ANCHOR = "  chart.timeScale().fitContent();\n}"
    if "kc2" not in _rep.TEMPLATE and _ANCHOR in _rep.TEMPLATE:
        _rep.TEMPLATE = _rep.TEMPLATE.replace(_ANCHOR, "  chart.timeScale().fitContent();\n" + _IND_JS + "}", 1)
except Exception:  # noqa: BLE001
    pass
  

# ============================================================
#  第二種型態：鈍化後深回檔 → 守住季線打底 → MACD 翻紅站回月線（台塑化型）
# ============================================================
def c_held_ma(p: Panel, a):
    """近 days 日收盤都守在 MA{period} 之上（容許 tol% 誤差）。"""
    ma = p.ma(a.get("period", 60))
    ok = (p.close >= ma * (1 - a.get("tol", 2) / 100)).astype(float)
    return ok.rolling(a.get("days", 30), min_periods=a.get("days", 30)).min() == 1


def c_base_range(p: Panel, a):
    """前 days 日（不含今天）的整理區間振幅 ≤ max%：代表在打底橫盤。"""
    n = a.get("days", 15)
    rng = (p.high.rolling(n).max() - p.low.rolling(n).min()) / p.close * 100
    return rng.shift(1) <= a.get("max", 15)


def c_macd_turn_red(p: Panel, a):
    """MACD 柱狀體在 recent 天內由綠翻紅，且今天仍是紅柱。"""
    o = _osc(p)
    turned = ((o > 0) & (o.shift(1) <= 0)).astype(float).rolling(a.get("recent", 3), min_periods=1).max() == 1
    return turned & (o > 0)


CONDITIONS.update({
    "held_ma": (c_held_ma, "近{days}日守住MA{period}"),
    "base_range": (c_base_range, "{days}日打底振幅≤{max}%"),
    "macd_turn_red": (c_macd_turn_red, "MACD柱翻紅"),
})

DUNHUA_STRATEGIES.append(
    {"name": "鈍化回檔－打底突破", "conditions": [
        {"type": "kd_dunhua", "days": 60, "run": 3},      # 近 60 日 K 值曾連 3 天 > 80
        {"type": "held_ma", "period": 60, "days": 30},    # 回檔期間守住季線
        {"type": "base_range", "days": 15, "max": 15},    # 前 15 日橫盤打底，振幅 ≤ 15%
        {"type": "macd_turn_red", "recent": 3},           # MACD 柱 3 天內翻紅
        {"type": "above_ma", "period": 20},               # 站上月線
        {"type": "avg_volume", "days": 20, "min": 1000},  # 20 日均量 1000 張以上
    ]}
)

# 第一種型態加上「回檔不超過近 60 日高點 15%」（回測後的優化；已手動加過就略過）
for _s in DUNHUA_STRATEGIES[:2]:
    if not any(_c.get("type") == "near_high" for _c in _s["conditions"]):
        _s["conditions"].append({"type": "near_high", "days": 60, "pct": 15})
