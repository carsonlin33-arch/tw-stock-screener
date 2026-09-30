"""產生網頁報表（單一 HTML 檔，免外部資源）。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

TEMPLATE = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ __DATE__</title>
<style>
:root{
  --bg:#f7f7f5;--surface:#ffffff;--ink:#1d1d1b;--ink2:#5c5c57;--muted:#8a8a84;
  --line:#e4e4df;--chip:#efefea;--accent:#2f5bd3;--up:#d0312d;--down:#18864b;
  --spark:#1d1d1b;--ma:#2f5bd3;
}
@media (prefers-color-scheme: dark){
  :root{--bg:#141413;--surface:#1e1e1c;--ink:#ecebe6;--ink2:#b5b4ad;--muted:#85847e;
  --line:#33332f;--chip:#2a2a27;--accent:#7d9cf0;--up:#f0625d;--down:#3fbf7a;
  --spark:#ecebe6;--ma:#7d9cf0;}
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.5 -apple-system,"PingFang TC","Microsoft JhengHei","Noto Sans TC",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 2px}
.sub{color:var(--ink2);margin:0 0 20px}
.sub a{color:var(--accent)}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;margin-bottom:18px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px;
  cursor:pointer;text-align:left;color:inherit;font:inherit}
.tile.on{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
.tile .n{font-size:26px;font-weight:650;font-variant-numeric:tabular-nums}
.tile .t{font-weight:600}
.tile .d{color:var(--muted);font-size:12px}
.bar{display:flex;gap:10px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
.bar input{flex:1;min-width:180px;padding:8px 10px;border-radius:8px;border:1px solid var(--line);
  background:var(--surface);color:var(--ink);font:inherit}
.bar .cnt{color:var(--ink2)}
.tbl{background:var(--surface);border:1px solid var(--line);border-radius:10px;overflow-x:auto}
table{border-collapse:collapse;width:100%;min-width:920px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap;text-align:right}
th{position:sticky;top:0;background:var(--surface);color:var(--ink2);font-weight:600;
  font-size:12px;cursor:pointer;user-select:none}
th.l,td.l{text-align:left}
th .arr{color:var(--accent)}
tr:last-child td{border-bottom:0}
td{font-variant-numeric:tabular-nums}
.code a{color:var(--accent);font-weight:600;text-decoration:none}
.name{color:var(--ink)}
.meta{color:var(--muted);font-size:12px}
.up{color:var(--up)}.down{color:var(--down)}
.tag{display:inline-block;background:var(--chip);border-radius:999px;padding:1px 8px;
  font-size:12px;margin:1px 2px;color:var(--ink2)}
svg.sp{display:block}
.empty{padding:40px;text-align:center;color:var(--muted)}
.foot{color:var(--muted);font-size:12px;margin-top:16px}
.legend{display:flex;gap:14px;color:var(--ink2);font-size:12px;margin:0 0 8px}
.legend i{display:inline-block;width:14px;height:2px;vertical-align:middle;margin-right:4px}
.senti{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:18px}
.senti .hd{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.senti .lv{font-size:18px;font-weight:650}
.senti .adv{color:var(--ink2);margin:4px 0 10px}
.senti .kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px}
.senti .k{background:var(--chip);border-radius:8px;padding:6px 10px}
.senti .k b{display:block;font-size:17px;font-variant-numeric:tabular-nums}
.senti .k span{font-size:12px;color:var(--muted)}
.flag{display:inline-block;border-radius:999px;padding:1px 8px;font-size:12px;margin:1px 2px;background:var(--up);color:#fff}
.news{max-width:260px;white-space:normal;font-size:12px;line-height:1.35}
.news a{color:var(--ink2);text-decoration:none}.news a:hover{text-decoration:underline}
.news .more{color:var(--muted)}
</style>
</head>
<body>
<div class="wrap">
  <h1>__TITLE__</h1>
  <p class="sub">資料日期 __DATE__ ・ 共掃描 __SCANNED__ 檔 ・ __ARCHIVE__</p>
  <div class="senti" id="senti" hidden></div>
  <div class="tiles" id="tiles"></div>
  <div class="bar">
    <input id="q" placeholder="搜尋代號、名稱或產業…" autocomplete="off">
    <span class="cnt" id="cnt"></span>
  </div>
  <div class="legend"><span><i style="background:var(--spark)"></i>收盤價</span>
    <span><i style="background:var(--ma)"></i>60日均線（季線）</span></div>
  <div class="tbl"><table>
    <thead><tr id="hd"></tr></thead><tbody id="bd"></tbody>
  </table><div class="empty" id="empty" hidden>沒有符合條件的股票</div></div>
  <p class="foot">投信/外資 = 當日買賣超張數。月營收年增 = 最新公布月份與去年同月比較。本益比 10 倍以下的爆量突破，歷史表現較弱。「準備突破觀察」只是觀察名單，本身不是買進訊號。量比 = 當日成交量 ÷ 比較基準。乖離 = 收盤相對 60 日均線。價格未還原除權息。本報表僅供參考，不構成投資建議。</p>
</div>
<script>
const DATA=__DATA__;
const STRATS=__STRATS__;
const SENTI=__SENTI__;
const cols=[
 {k:'code',t:'代號',l:1},{k:'name',t:'名稱 / 產業',l:1},{k:'spark',t:'近__SPARK__日走勢',l:1,ns:1},
 {k:'close',t:'收盤',f:v=>v.toFixed(2)},
 {k:'change_pct',t:'漲跌%',f:v=>(v>0?'+':'')+v.toFixed(2),c:v=>v>0?'up':v<0?'down':''},
 {k:'volume_lots',t:'成交量(張)',f:v=>Math.round(v).toLocaleString()},
 {k:'vol_x_prev',t:'量比前日',f:v=>v.toFixed(1)+'×'},
 {k:'vol_x_avg5',t:'量比5日均',f:v=>v.toFixed(1)+'×'},
 {k:'bias60',t:'季線乖離%',f:v=>(v>0?'+':'')+v.toFixed(1),c:v=>v>0?'up':v<0?'down':''},
 {k:'pe',t:'本益比',f:v=>v<=0?'虧損':v.toFixed(1)},
 {k:'trust',t:'投信(張)',f:v=>(v>0?'+':'')+Math.round(v).toLocaleString(),c:v=>v>0?'up':v<0?'down':''},
 {k:'foreign',t:'外資(張)',f:v=>(v>0?'+':'')+Math.round(v).toLocaleString(),c:v=>v>0?'up':v<0?'down':''},
 {k:'rev_yoy',t:'月營收年增%',f:v=>(v>0?'+':'')+v.toFixed(0),c:v=>v>0?'up':v<0?'down':''},
 {k:'news',t:'消息 / 重大訊息',l:1,ns:1},
 {k:'tags',t:'符合策略',l:1,ns:1},
];
let sel=null,sortK='change_pct',sortD=-1;
const $=id=>document.getElementById(id);
function tiles(){
  $('tiles').innerHTML=STRATS.map((s,i)=>`<button class="tile${sel===i?' on':''}" data-i="${i}">
   <div class="n">${s.codes.length}</div><div class="t">${s.name}</div><div class="d">${s.desc}</div></button>`).join('');
  document.querySelectorAll('.tile').forEach(b=>b.onclick=()=>{const i=+b.dataset.i;sel=sel===i?null:i;tiles();render();});
}
function spark(a,m){
  const W=120,H=30,v=a.concat(m).filter(x=>x!=null);if(!v.length)return'';
  let lo=Math.min(...v),hi=Math.max(...v);if(hi===lo)hi=lo+1;
  const pts=s=>s.map((y,i)=>y==null?null:[(i/(a.length-1||1))*(W-4)+2,H-2-(y-lo)/(hi-lo)*(H-4)]);
  const path=s=>{let d='',pen=0;pts(s).forEach(p=>{if(!p){pen=0;return}d+=(pen?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1);pen=1});return d};
  const last=pts(a).at(-1);
  return `<svg class="sp" width="${W}" height="${H}" role="img" aria-label="近期收盤走勢">
   <title>最低 ${lo.toFixed(2)} ・ 最高 ${hi.toFixed(2)}</title>
   <path d="${path(m)}" fill="none" stroke="var(--ma)" stroke-width="1.5" stroke-linejoin="round" opacity=".9"/>
   <path d="${path(a)}" fill="none" stroke="var(--spark)" stroke-width="1.5" stroke-linejoin="round"/>
   ${last?`<circle cx="${last[0]}" cy="${last[1]}" r="2.5" fill="var(--spark)"/>`:''}</svg>`;
}
function render(){
  $('hd').innerHTML=cols.map(c=>`<th class="${c.l?'l':''}" data-k="${c.ns?'':c.k}">${c.t}${sortK===c.k?`<span class="arr">${sortD>0?' ▲':' ▼'}</span>`:''}</th>`).join('');
  document.querySelectorAll('th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;if(!k)return;
    if(sortK===k)sortD*=-1;else{sortK=k;sortD=(k==='code'||k==='name')?1:-1}render();});
  const q=$('q').value.trim().toLowerCase();
  let rows=DATA.filter(r=>(sel===null||r.tags.includes(STRATS[sel].name))&&
    (!q||(r.code+r.name+r.industry).toLowerCase().includes(q)));
  rows.sort((a,b)=>{const x=a[sortK],y=b[sortK];if(x==null)return 1;if(y==null)return -1;
    return (x>y?1:x<y?-1:0)*sortD;});
  $('cnt').textContent=`顯示 ${rows.length} 檔`;
  $('empty').hidden=rows.length>0;
  $('bd').innerHTML=rows.map(r=>`<tr>
   <td class="l code"><a href="${r.url}" target="_blank" rel="noopener">${r.code}</a></td>
   <td class="l"><div class="name">${r.name}</div><div class="meta">${r.market==='TWSE'?'上市':'上櫃'}${r.industry?' ・ '+r.industry:''}</div></td>
   <td class="l">${spark(r.spark,r.spark_ma)}</td>
   ${cols.slice(3,13).map(c=>{const v=r[c.k];return `<td class="${v!=null&&c.c?c.c(v):''}">${v==null?'—':c.f(v)}</td>`}).join('')}
   <td class="l news">${newsCell(r)}</td>
   <td class="l">${r.flag?`<span class="flag">${r.flag}</span>`:''}${r.tags.map(t=>`<span class="tag">${t}</span>`).join('')}</td></tr>`).join('');
}
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function newsCell(r){
  const a=(r.ann||[]).map(t=>`<div>📢 ${esc(t)}</div>`);
  const n=(r.news||[]).slice(0,2).map(x=>`<div><a href="${esc(x.link)}" target="_blank" rel="noopener">${esc(x.title)}</a> <span class="more">${esc(x.when)}</span></div>`);
  const all=a.concat(n);return all.length?all.join(''):'<span class="more">—</span>';
}
function senti(){
  if(!SENTI||!SENTI.level)return;const s=SENTI;const el=$('senti');el.hidden=false;
  const k=(v,l)=>`<div class="k"><b>${v}</b><span>${l}</span></div>`;
  el.innerHTML=`<div class="hd"><span class="lv">${s.icon} 市場情緒：${s.level}</span>
   <span class="more">站上月線 ${s.above_ma20.toFixed(0)}%${s.above_ma20_5d_ago!=null?`（5 天前 ${s.above_ma20_5d_ago.toFixed(0)}%）`:''}</span></div>
   <div class="adv">${s.advice}${s.weak_market?' ⚠️ 大盤近 20 日跌超過 3%，回測顯示此時爆量突破平均虧損。':''}</div>
   <div class="kpis">${k(s.up_pct.toFixed(0)+'%','上漲家數')}${k(s.above_ma60.toFixed(0)+'%','站上季線')}
   ${k(s.new_high+' / '+s.new_low,'創60日新高 / 新低')}${k(s.limit_up+' / '+s.limit_down,'漲停 / 跌停')}
   ${k(s.surge_pct.toFixed(1)+'%','爆量家數')}${k((s.mkt20>0?'+':'')+s.mkt20.toFixed(1)+'%','大盤近20日')}</div>`;
}
$('q').oninput=render;senti();tiles();render();
</script>
</body>
</html>
"""


def _clean(v):
    if hasattr(v, "item") and not isinstance(v, (list, dict, str)):
        v = v.item()
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return None
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    return v


def build_rows(stocks: pd.DataFrame, met: pd.DataFrame, hits: dict[str, list[str]],
               extras: pd.DataFrame | None = None, ann: dict | None = None, news: dict | None = None) -> list[dict]:
    tags: dict[str, list[str]] = {}
    for name, codes in hits.items():
        for c in codes:
            tags.setdefault(c, []).append(name)
    info = stocks.set_index("code")
    rows = []
    for code, m in met.iterrows():
        mk = info.at[code, "market"] if code in info.index else "TWSE"
        ind = info.at[code, "industry"] if code in info.index else ""
        rows.append(
            {
                "code": code,
                "name": info.at[code, "name"] if code in info.index else "",
                "market": mk,
                "industry": ind if isinstance(ind, str) else "",
                "url": f"https://tw.stock.yahoo.com/quote/{code}.{'TW' if mk == 'TWSE' else 'TWO'}",
                "tags": tags.get(code, []),
                **{k: _clean(v) for k, v in m.items()},
            }
        )
        r = rows[-1]
        ex = extras.loc[code] if extras is not None and code in extras.index else None
        for k in ["pe", "trust", "foreign", "rev_yoy"]:
            r[k] = _clean(float(ex[k])) if ex is not None and k in ex and pd.notna(ex[k]) else None
        if r["pe"] is None and ex is not None and "pe" in ex:
            r["pe"] = -1 if "pb" in ex and pd.notna(ex.get("pb")) else None  # 有資料但沒本益比 = 虧損
        r["flag"] = ex["flag"] if ex is not None and "flag" in ex and isinstance(ex["flag"], str) else ""
        r["ann"] = (ann or {}).get(code, [])
        r["news"] = (news or {}).get(code, [])
    return rows


def render_html(title, date, scanned, rows, strat_info, spark_days, archive_link="", senti=None):
    js = lambda o: json.dumps(o, ensure_ascii=False).replace("</", "<\\/")
    return (
        TEMPLATE.replace("__TITLE__", title)
        .replace("__DATE__", date)
        .replace("__SCANNED__", f"{scanned:,}")
        .replace("__ARCHIVE__", archive_link)
        .replace("__SPARK__", str(spark_days))
        .replace("__DATA__", js(rows))
        .replace("__STRATS__", js(strat_info))
        .replace("__SENTI__", js(_clean(senti or {})))
    )


def write_site(out_dir: Path, date: str, html_for) -> None:
    """寫出 site/：YYYY-MM-DD.html（當日）、index.html（最新）、archive.html（歷史清單）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{date}.html").write_text(html_for("<a href='archive.html'>歷史報表</a>"), "utf-8")
    (out_dir / "index.html").write_text(html_for("<a href='archive.html'>歷史報表</a>"), "utf-8")
    days = sorted((p.stem for p in out_dir.glob("20??-??-??.html")), reverse=True)
    items = "".join(f"<li><a href='{d}.html'>{d}</a></li>" for d in days)
    (out_dir / "archive.html").write_text(
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>歷史報表</title><style>body{font:15px/1.8 sans-serif;max-width:600px;margin:24px auto;padding:0 16px;"
        "background:#f7f7f5;color:#1d1d1b}@media(prefers-color-scheme:dark){body{background:#141413;color:#ecebe6}"
        "a{color:#7d9cf0}}</style><h2>歷史報表</h2><p><a href='index.html'>← 最新</a></p>"
        f"<ul>{items}</ul>",
        "utf-8",
    )
