exec(open("gen.py").read())

# ─────────────────────────────── PREDICTIONS
page("Predictions","pred", """
<div class="head">
  <div><h1>Predictions</h1>
  <p>Write the view down before you find out. Append-only — a log you can revise is a memory,
     and one missing its losers produces confident, wrong calibration.</p></div>
  <button class="btn pri">Log a view</button>
</div>

<div class="grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:18px">
  <div class="card stat"><b>14</b><span>logged</span></div>
  <div class="card stat"><b>6</b><span>graded</span></div>
  <div class="card stat"><b style="color:var(--warn)">3</b><span>due now</span></div>
  <div class="card stat"><b>0.241</b><span>Brier · lower is better</span></div>
</div>

<div class="grid" style="grid-template-columns:minmax(0,1fr) 340px;align-items:start">
  <div class="card" style="padding:0;overflow:hidden">
    <div style="padding:13px 18px;border-bottom:1px solid var(--rule);display:flex;gap:7px">
      <sc-for list="{{tabs}}" as="t" hint-placeholder-count="3">
        <button class="btn" onClick="{{ t.pick }}" style="{{t.style}}">{{t.label}}</button>
      </sc-for>
    </div>
    <table>
      <thead><tr><th>Instrument</th><th>View</th><th style="text-align:right">Conf</th>
        <th>Horizon</th><th style="text-align:right">Outcome</th></tr></thead>
      <tbody>
        <sc-for list="{{rows}}" as="r" hint-placeholder-count="6">
          <tr>
            <td><code style="font-size:12.5px;font-weight:600">{{r.inst}}</code>
              <div style="font-size:11.5px;color:var(--muted);max-width:34ch">{{r.thesis}}</div></td>
            <td><span class="chip {{r.dirCls}}">{{r.dir}}</span></td>
            <td class="num" style="text-align:right">{{r.conf}}</td>
            <td style="color:var(--muted);font-size:12.5px">{{r.horizon}}<br>
              <span style="font-size:11px">{{r.gradeOn}}</span></td>
            <td style="text-align:right"><span class="chip {{r.outCls}}">{{r.out}}</span></td>
          </tr>
        </sc-for>
      </tbody>
    </table>
  </div>

  <div class="grid">
    <div class="card">
      <p class="eyebrow">Calibration</p>
      <div style="display:flex;flex-direction:column;gap:11px;margin-bottom:14px">
        <sc-for list="{{bands}}" as="b" hint-placeholder-count="4">
          <div>
            <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px">
              <span style="color:var(--ink-2)">said {{b.said}}</span>
              <span class="num" style="color:{{b.color}}">was {{b.was}} <span style="color:var(--muted)">n={{b.n}}</span></span>
            </div>
            <div style="height:7px;background:var(--panel-2);border-radius:2px;position:relative">
              <div style="{{b.barStyle}}"></div>
              <div style="{{b.markStyle}}"></div>
            </div>
          </div>
        </sc-for>
      </div>
      <div class="box warn"><span class="lbl">Not yet meaningful</span>
      <p>24 more graded calls before this measures skill rather than luck. It cannot be
         back-filled — only waited for.</p></div>
    </div>

    <div class="box key"><span class="lbl">Graded against the benchmark</span>
    <p>Being up 6% in a month the index rose 8% is being wrong. A no-view call is recorded
       as resolved and excluded from the score entirely.</p></div>
  </div>
</div>
""", logic="""
const POS='#3B6A38', NEG='#9E3626', WARN='#8A5B12', MUT='#606D71';
const ALL = [
  {inst:'MYX:1155', thesis:'NIM stabilises above 2.25%', dir:'+1', conf:'0.62', horizon:'63d', gradeOn:'due 18 Sep', state:'pending'},
  {inst:'XNAS:NVDA', thesis:'Multiple compresses on supply normalising', dir:'−1', conf:'0.55', horizon:'63d', gradeOn:'due 02 Sep', state:'due'},
  {inst:'XSES:D05', thesis:'Deposit franchise holds through the cut', dir:'+1', conf:'0.71', horizon:'21d', gradeOn:'due 29 Aug', state:'due'},
  {inst:'MYX:5347', thesis:'Tariff review lands neutral', dir:'0', conf:'0.50', horizon:'21d', gradeOn:'graded 12 Aug', state:'noview'},
  {inst:'MYX:1155', thesis:'CASA erosion continues a fourth quarter', dir:'+1', conf:'0.68', horizon:'63d', gradeOn:'graded 04 Aug', state:'hit'},
  {inst:'XNAS:MSFT', thesis:'Azure reacceleration is already priced', dir:'−1', conf:'0.74', horizon:'63d', gradeOn:'graded 28 Jul', state:'miss'}
];
const OUT = {
  pending:{t:'pending', c:'n'}, due:{t:'grade it', c:'wait'},
  hit:{t:'correct', c:'ok'}, miss:{t:'wrong', c:'no'}, noview:{t:'unscored', c:'n'}
};
class Component extends DCLogic {
  constructor(p){ super(p); this.state = { tab:'all' }; }
  renderVals(){
    const t = this.state.tab;
    const rows = ALL.filter(r => t==='all'
      || (t==='due' && r.state==='due')
      || (t==='graded' && (r.state==='hit'||r.state==='miss'||r.state==='noview')));
    const bands = [
      {said:'50–59%', was:'50%', n:2, s:55, w:50},
      {said:'60–69%', was:'67%', n:3, s:65, w:67},
      {said:'70–79%', was:'40%', n:5, s:74, w:40},
      {said:'80–89%', was:'—', n:0, s:84, w:0}
    ];
    return {
      tabs: [['all','All 14'],['due','Due now'],['graded','Graded']].map(([k,label]) => ({
        label, pick: () => this.setState({tab:k}),
        style:`font-size:12px;padding:4px 11px;${t===k
          ? 'background:#0F5C63;border-color:#0F5C63;color:#fff;font-weight:600'
          : 'font-weight:500'}` })),
      rows: rows.map(r => ({ ...r,
        dirCls: r.dir==='+1'?'ok':(r.dir==='−1'?'no':'n'),
        out: OUT[r.state].t, outCls: OUT[r.state].c })),
      bands: bands.map(b => {
        const over = b.n>=3 && (b.s - b.w) > 10;
        return { ...b, n:String(b.n), color: b.n===0 ? MUT : (over?NEG:POS),
          barStyle:`position:absolute;left:0;top:0;bottom:0;width:${b.w}%;
            background:${b.n===0?'#C2C9C6':(over?NEG:POS)};border-radius:2px`,
          markStyle:`position:absolute;left:${b.s}%;top:-3px;bottom:-3px;width:2px;
            background:#15191B;opacity:.55` };
      })
    };
  }
}
""")

# ─────────────────────────────── PRICES
page("Prices","prices", """
<div class="head">
  <div><h1>Prices</h1>
  <p>Daily bars, bounded by an as-at date so no bar after it is ever returned. A source that
     cannot answer says so — it never returns an empty series.</p></div>
  <span class="chip ok">Stooq · free, no key</span>
</div>

<div style="display:flex;gap:8px;margin-bottom:18px">
  <sc-for list="{{picks}}" as="p" hint-placeholder-count="4">
    <button class="btn" onClick="{{ p.pick }}" style="{{p.style}}">{{p.label}}</button>
  </sc-for>
</div>

<sc-if value="{{d.ok}}" hint-placeholder-val="{{true}}">
  <div class="grid" style="grid-template-columns:minmax(0,1fr) 300px;align-items:start">
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:18px">
        <div><code style="font-size:15px;font-weight:700">{{d.inst}}</code>
          <span style="font-size:12.5px;color:var(--muted);margin-left:10px">{{d.range}}</span></div>
        <div class="num" style="font-size:22px;font-weight:600;color:{{d.retColor}}">{{d.ret}}</div>
      </div>
      <svg viewBox="0 0 600 170" style="width:100%;height:170px;display:block">
        <polyline points="{{d.spark}}" fill="none" stroke="{{d.retColor}}" stroke-width="1.8"
          stroke-linejoin="round" stroke-linecap="round"/>
        <line x1="0" y1="150" x2="600" y2="150" stroke="#D6DBD9" stroke-width="1"/>
      </svg>
      <table style="margin-top:14px">
        <thead><tr><th>Day</th><th style="text-align:right">Open</th><th style="text-align:right">High</th>
          <th style="text-align:right">Low</th><th style="text-align:right">Close</th>
          <th style="text-align:right">Volume</th></tr></thead>
        <tbody>
          <sc-for list="{{d.bars}}" as="b" hint-placeholder-count="5">
            <tr><td class="num" style="color:var(--muted)">{{b.day}}</td>
              <td class="num" style="text-align:right">{{b.o}}</td>
              <td class="num" style="text-align:right">{{b.h}}</td>
              <td class="num" style="text-align:right">{{b.l}}</td>
              <td class="num" style="text-align:right;font-weight:600">{{b.c}}</td>
              <td class="num" style="text-align:right;color:var(--muted)">{{b.v}}</td></tr>
          </sc-for>
        </tbody>
      </table>
    </div>
    <div class="grid">
      <div class="card">
        <p class="eyebrow">Derived</p>
        <table><tbody>
          <tr><td style="color:var(--muted)">20d ADV</td><td class="num" style="text-align:right">{{d.adv}}</td></tr>
          <tr><td style="color:var(--muted)">20d ATR</td><td class="num" style="text-align:right">{{d.atr}}</td></tr>
          <tr><td style="color:var(--muted)">Bars held</td><td class="num" style="text-align:right">{{d.n}}</td></tr>
          <tr><td style="color:var(--muted)">Board lot</td><td class="num" style="text-align:right">{{d.lot}}</td></tr>
        </tbody></table>
      </div>
      <div class="box key"><span class="lbl">Validated at the seam</span>
      <p>Non-finite prices, inverted bars and non-positive values are dropped before they can
         become a Bar. A NaN return once reached a verdict as &ldquo;nan% unexplained&rdquo;.</p></div>
    </div>
  </div>
</sc-if>
<sc-if value="{{d.nodata}}" hint-placeholder-val="{{false}}">
  <div class="card" style="border-left:3px solid var(--neg);background:var(--neg-soft)">
    <code style="font-size:14px;font-weight:700;color:var(--neg)">NO DATA</code>
    <p style="margin:8px 0 0;font-size:14px;color:var(--ink-2)">{{d.msg}}</p>
    <p style="margin:10px 0 0;font-size:12.5px;color:var(--muted)">
      An unmapped market raises rather than guessing a suffix — a guess returns another
      company&rsquo;s prices, which is silent, plausible and wrong.</p>
  </div>
</sc-if>
""", logic="""
const POS='#3B6A38', NEG='#9E3626';
function series(seed, n, base, drift){
  let x = seed, out = [], p = base;
  for (let i=0;i<n;i++){ x = (x*1103515245+12345)%2147483648;
    p = p*(1 + drift + ((x/2147483648)-0.5)*0.028); out.push(p); }
  return out;
}
function mk(inst, base, drift, lot, cur){
  const px = series(inst.length*7+3, 30, base, drift);
  const lo = Math.min(...px), hi = Math.max(...px);
  const spark = px.map((v,i) => `${(i/(px.length-1)*600).toFixed(1)},${(150 - (v-lo)/((hi-lo)||1)*135).toFixed(1)}`).join(' ');
  const ret = (px[px.length-1]/px[0]-1);
  const days = ['2026-08-24','2026-08-25','2026-08-26','2026-08-27','2026-08-28'];
  const bars = px.slice(-5).map((c,i) => ({ day:days[i],
    o:(c*0.996).toFixed(2), h:(c*1.008).toFixed(2), l:(c*0.991).toFixed(2), c:c.toFixed(2),
    v:(820000+i*41000).toLocaleString() }));
  return { ok:true, nodata:false, inst, range:'30 bars to 28 Aug 2026',
    ret:(ret>0?'+':'')+(ret*100).toFixed(2)+'%', retColor: ret>0?POS:NEG,
    spark, bars, n:'2,847', lot:String(lot),
    adv:cur+' '+(px[px.length-1]*880000/1000).toFixed(0)+'k',
    atr:(px[px.length-1]*0.017).toFixed(3) };
}
const D = {
  'MYX:1155': mk('MYX:1155', 6.20, 0.0009, 100, 'RM'),
  'XNAS:NVDA': mk('XNAS:NVDA', 178.40, 0.0021, 1, 'USD'),
  'XHKG:0700': mk('XHKG:0700', 402.60, -0.0006, 100, 'HKD'),
  'XFRA:BMW': { ok:false, nodata:true, inst:'XFRA:BMW',
    msg:'No Stooq suffix registered for market \\'XFRA\\'. Add it to StooqFeed.SUFFIX and verify with a live fetch.' }
};
class Component extends DCLogic {
  constructor(p){ super(p); this.state = { k:'MYX:1155' }; }
  renderVals(){
    const k = this.state.k;
    return { d: D[k],
      picks: Object.keys(D).map(key => ({ label:key, pick: () => this.setState({k:key}),
        style:`font-size:12px;padding:4px 12px;font-family:"JetBrains Mono",monospace;${k===key
          ? 'background:#0F5C63;border-color:#0F5C63;color:#fff;font-weight:600' : ''}` })) };
  }
}
""")
