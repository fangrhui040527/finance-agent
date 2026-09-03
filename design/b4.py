from gen import page  # run via `python -m design.build` or from design/

# ─────────────────────────────── THESIS + RED TEAM
page(
    "Thesis",
    "thesis",
    """
<div class="head">
  <div><h1>Thesis</h1>
  <p>A stance you cannot falsify is not a thesis. Two machine-checkable breakers, or no
     stance is taken — and the red team runs on every one.</p></div>
  <code style="font-size:14px;font-weight:700;color:var(--ink)">MYX:1155</code>
</div>

<div class="grid" style="grid-template-columns:minmax(0,1fr) 380px;align-items:start">
  <div class="grid">
    <div class="card" style="border-left:3px solid {{t.color}}">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:9px;flex-wrap:wrap">
        <span class="chip {{t.cls}}">{{t.stance}}</span>
        <span style="font-size:12.5px;color:var(--muted)">horizon 12 months</span>
        <span style="font-size:12.5px;color:var(--muted)">·</span>
        <span style="font-size:12.5px;color:var(--muted)">confidence <b class="num" style="color:var(--ink-2)">{{t.conf}}</b></span>
      </div>
      <p style="margin:0;font-size:15px;color:var(--ink);line-height:1.55">{{t.sentence}}</p>
      <sc-if value="{{t.blocked}}" hint-placeholder-val="{{false}}">
        <div class="box stop" style="margin-top:14px"><span class="lbl">Why no stance</span>
        <p>{{t.blockReason}}</p></div>
      </sc-if>
    </div>

    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:13px">
        <p class="eyebrow" style="margin:0">What would falsify it</p>
        <span class="chip {{t.brCls}}">{{t.brCount}} of 2 minimum</span>
      </div>
      <div style="display:flex;flex-direction:column;gap:9px">
        <sc-for list="{{breakers}}" as="b" hint-placeholder-count="3">
          <div onClick="{{ b.toggle }}" style="{{b.rowStyle}}">
            <div style="{{b.dotStyle}}"></div>
            <div style="flex-grow:1;min-width:0">
              <div style="font-size:13.5px;font-weight:600;color:{{b.textColor}}">{{b.statement}}</div>
              <code style="font-size:11.5px;color:var(--muted)">{{b.query}} → {{b.store}}</code>
            </div>
            <span class="chip {{b.dateCls}}" style="flex:none">{{b.date}}</span>
          </div>
        </sc-for>
      </div>
      <p style="font-size:11.5px;color:var(--muted);margin:13px 0 0">
        Click a breaker to drop it. Each needs a query that can actually run — prose does not
        qualify, and a breaker with no review date will never fire.</p>
    </div>

    <div class="card">
      <p class="eyebrow">Supporting evidence</p>
      <table><tbody>
        <tr><td><code style="color:var(--accent);font-size:12px">a1_fundamentals</code></td>
            <td>CASA fell to 24% from 31% over four quarters</td></tr>
        <tr><td><code style="color:var(--accent);font-size:12px">a2_valuation</code></td>
            <td>P/B at the 12th percentile of its own 40-quarter history</td></tr>
        <tr><td><code style="color:var(--accent);font-size:12px">a5_catalyst_events</code></td>
            <td>Results due in three weeks</td></tr>
        <tr><td><code style="color:var(--accent);font-size:12px">a6_macro_regime</code></td>
            <td>OPR on hold, curve flat</td></tr>
      </tbody></table>
    </div>
  </div>

  <div class="card" style="padding:0;overflow:hidden">
    <div style="padding:15px 18px;border-bottom:1px solid var(--rule);background:var(--panel-2)">
      <p class="eyebrow" style="margin:0 0 3px">Red team</p>
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <code style="font-size:13px;font-weight:700;color:{{rt.color}}">{{rt.verdict}}</code>
        <span style="font-size:12px;color:var(--muted)">{{rt.n}} challenges</span>
      </div>
    </div>
    <div style="padding:6px 0">
      <sc-for list="{{challenges}}" as="c" hint-placeholder-count="6">
        <div style="padding:11px 18px;border-bottom:1px solid var(--rule);display:flex;gap:11px;align-items:flex-start">
          <span class="chip {{c.cls}}" style="flex:none;margin-top:1px">{{c.sev}}</span>
          <div style="min-width:0">
            <div style="font-size:13px;color:var(--ink-2);line-height:1.5">{{c.text}}</div>
            <code style="font-size:11px;color:var(--muted)">{{c.kind}}</code>
          </div>
        </div>
      </sc-for>
    </div>
    <div style="padding:13px 18px;background:var(--panel-2)">
      <p style="font-size:11.5px;color:var(--muted);margin:0">
        Six standing challenges are fixed in code so they cannot be tuned to pass. A red team
        that goes silent on a live thesis is itself a finding.</p>
    </div>
  </div>
</div>
""",
    logic="""
const ACC='#0F5C63', NEG='#9E3626', WARN='#8A5B12', MUT='#606D71', INK='#15191B';
const ALL = [
  { id:'nim', statement:'NIM falls below 2.00%', query:'nim < 0.020', store:'kb_filings',
    date:'review 15 Nov', dateCls:'n' },
  { id:'casa', statement:'CASA ratio falls below 22%', query:'casa_ratio < 0.22', store:'kb_filings',
    date:'review 15 Nov', dateCls:'n' },
  { id:'npl', statement:'Gross impaired loans exceed 2.5%', query:'gil_ratio > 0.025', store:'kb_filings',
    date:'no date', dateCls:'wait' }
];
class Component extends DCLogic {
  constructor(p){ super(p); this.state = { off: {} }; }
  renderVals(){
    const on = ALL.filter(b => !this.state.off[b.id]);
    const n = on.length, ok = n >= 2;
    const gaps = [];
    const conf = Math.max(0.05, Math.min(0.9, 0.75 - 0.12*gaps.length - 0.02*2 - (ok?0:0.55)));

    const t = ok
      ? { stance:'accumulate', cls:'ok', color:ACC, conf:conf.toFixed(2), blocked:false, blockReason:'',
          sentence:'Accumulate MYX:1155 on four evidence findings — a deposit-mix problem that is priced in at the 12th percentile of its own book value history, with results as the near catalyst.' }
      : { stance:'no view', cls:'no', color:NEG, conf:'0.20', blocked:true,
          blockReason:'Fewer than two falsifiable breakers. A stance with nothing that could prove it wrong is an opinion, and the code will not record one as a thesis.',
          sentence:'No view on MYX:1155: the evidence does not support a stance you could be shown to be wrong about.' };

    const ch = [];
    if (!ok) ch.push({sev:'fatal', cls:'no', kind:'breakers',
      text:'Fewer than two breakers. Nothing here could falsify the position.'});
    ch.push({sev:'fatal', cls:'no', kind:'valuation',
      text:'Accumulating with no valuation range means agreeing to pay any price.'});
    if (on.some(b => b.dateCls==='wait')) ch.push({sev:'minor', cls:'wait', kind:'breaker_timing',
      text:'A breaker with no review date will never fire — it reads as protection and provides none.'});
    ch.push({sev:'material', cls:'wait', kind:'consensus',
      text:'This is the consensus view on the name and is already in the price.'});
    ch.push({sev:'material', cls:'wait', kind:'accounting',
      text:'Earnings quality has not been checked against operating cash flow.'});
    ch.push({sev:'material', cls:'wait', kind:'regime',
      text:'The evidence comes from one rate regime and is assumed to hold in another.'});
    ch.push({sev:'material', cls:'wait', kind:'liquidity',
      text:'The position cannot be exited at the size assumed without moving the price.'});

    const fatal = ch.filter(c=>c.sev==='fatal').length;
    const mat = ch.filter(c=>c.sev==='material').length;
    const rt = fatal ? {verdict:'thesis_rejected', color:NEG, n:ch.length}
             : mat>=3 ? {verdict:'thesis_weakened', color:WARN, n:ch.length}
             : {verdict:'thesis_survives', color:ACC, n:ch.length};

    return {
      t: { ...t, brCount:String(n), brCls: ok?'ok':'no' },
      rt, challenges: ch,
      breakers: ALL.map(b => {
        const live = !this.state.off[b.id];
        return { ...b, toggle: () => this.setState({ off: {...this.state.off, [b.id]: live } }),
          textColor: live ? INK : MUT,
          dateCls: live ? b.dateCls : 'n',
          rowStyle: `display:flex;gap:12px;align-items:center;padding:11px 13px;cursor:pointer;
            border:1px solid ${live?'#D6DBD9':'#EFF2F1'};border-radius:3px;
            background:${live?'#FFFFFF':'#EFF2F1'};opacity:${live?1:0.55}`,
          dotStyle: `width:9px;height:9px;border-radius:50%;flex:none;
            background:${live?ACC:'#C2C9C6'}` };
      })
    };
  }
}
""",
)

# ─────────────────────────────── PORTFOLIO
page(
    "Portfolio",
    "port",
    """
<div class="head">
  <div><h1>Portfolio</h1>
  <p>Concentration, heat and every breach. Effective bets is the number to watch — a book can
     look diversified by HHI and collapse into three real positions.</p></div>
  <span class="chip n">base MYR</span>
</div>

<div class="grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:18px">
  <div class="card stat"><b style="color:{{m.hhiColor}}">{{m.hhi}}</b><span>HHI · limit 0.18</span></div>
  <div class="card stat"><b style="color:{{m.bimColor}}">{{m.bets}}</b><span>effective bets · floor 5.0</span></div>
  <div class="card stat"><b style="color:{{m.heatColor}}">{{m.heat}}</b><span>portfolio heat · cap 6.0%</span></div>
  <div class="card stat"><b>1.00</b><span>drawdown scalar</span></div>
</div>

<div class="grid" style="grid-template-columns:minmax(0,1fr) 360px;align-items:start">
  <div class="card" style="padding:0;overflow:hidden">
    <div style="padding:15px 20px;border-bottom:1px solid var(--rule);display:flex;
                justify-content:space-between;align-items:center">
      <p class="eyebrow" style="margin:0">Holdings · click to exclude</p>
      <span style="font-size:12px;color:var(--muted)">{{m.n}} positions</span>
    </div>
    <table>
      <thead><tr><th>Instrument</th><th>Sector</th><th style="text-align:right">Weight</th>
        <th style="text-align:right">Risk to stop</th><th></th></tr></thead>
      <tbody>
        <sc-for list="{{rows}}" as="r" hint-placeholder-count="5">
          <tr onClick="{{ r.toggle }}" style="{{r.style}}">
            <td><code style="font-size:12.5px;font-weight:600;color:{{r.ink}}">{{r.id}}</code></td>
            <td style="color:{{r.mut}}">{{r.sector}} · {{r.country}}</td>
            <td class="num" style="text-align:right;color:{{r.wColor}};font-weight:{{r.wWeight}}">{{r.w}}</td>
            <td class="num" style="text-align:right;color:{{r.mut}}">{{r.risk}}</td>
            <td style="text-align:right"><span class="chip {{r.cls}}">{{r.flag}}</span></td>
          </tr>
        </sc-for>
      </tbody>
    </table>
  </div>

  <div class="grid">
    <div class="card">
      <p class="eyebrow">Breaches</p>
      <sc-if value="{{m.hasBreach}}" hint-placeholder-val="{{true}}">
        <div style="display:flex;flex-direction:column;gap:10px">
          <sc-for list="{{breaches}}" as="b" hint-placeholder-count="4">
            <div style="display:flex;gap:10px;align-items:flex-start">
              <span class="chip no" style="flex:none;margin-top:1px">{{b.over}}</span>
              <div><div style="font-size:13px;font-weight:600">{{b.kind}}</div>
              <div style="font-size:12px;color:var(--muted)">{{b.detail}}</div></div>
            </div>
          </sc-for>
        </div>
      </sc-if>
      <sc-if value="{{m.clean}}" hint-placeholder-val="{{false}}">
        <div class="box"><p>No limit is breached at these weights.</p></div>
      </sc-if>
    </div>

    <div class="box warn"><span class="lbl">The dangerous case</span>
    <p>HHI can look healthy while effective bets sits below five — that is correlation hiding
       inside apparent diversification. Watch the second number, not the first.</p></div>

    <div class="card" style="padding:15px 18px">
      <p class="eyebrow" style="margin-bottom:9px">Correlation clusters</p>
      <div style="display:flex;flex-direction:column;gap:7px;font-size:12.5px">
        <div style="display:flex;justify-content:space-between">
          <span>Banks <code style="font-size:11px;color:var(--muted)">ρ 0.71</code></span>
          <span class="num">{{m.bankW}}</span></div>
        <div style="display:flex;justify-content:space-between">
          <span>US tech <code style="font-size:11px;color:var(--muted)">ρ 0.64</code></span>
          <span class="num">{{m.techW}}</span></div>
      </div>
    </div>
  </div>
</div>
""",
    logic="""
const NEG='#9E3626', POS='#3B6A38', MUT='#606D71', INK='#15191B', WARN='#8A5B12';
const BOOK = [
  {id:'MYX:1155', sector:'bank', country:'MY', w:0.22, risk:0.010},
  {id:'XNAS:NVDA', sector:'tech', country:'US', w:0.18, risk:0.020},
  {id:'XSES:D05', sector:'bank', country:'SG', w:0.15, risk:0.010},
  {id:'MYX:5347', sector:'utility', country:'MY', w:0.11, risk:0.008},
  {id:'XNAS:MSFT', sector:'tech', country:'US', w:0.09, risk:0.007}
];
class Component extends DCLogic {
  constructor(p){ super(p); this.state = { off:{} }; }
  renderVals(){
    const live = BOOK.filter(p => !this.state.off[p.id]);
    const tot = live.reduce((s,p)=>s+p.w, 0) || 1;
    const ws = live.map(p => p.w/tot);
    const hhi = ws.reduce((s,w)=>s+w*w, 0);
    const bets = Math.max(1, Math.min(live.length, 1/hhi*0.62));
    const heat = live.reduce((s,p)=>s+p.risk, 0);
    const bankW = live.filter(p=>p.sector==='bank').reduce((s,p)=>s+p.w/tot,0);
    const techW = live.filter(p=>p.sector==='tech').reduce((s,p)=>s+p.w/tot,0);

    const breaches = [];
    live.forEach(p => { if (p.w/tot > 0.08) breaches.push({
      kind:'single_name', over:(p.w/tot*100).toFixed(0)+'%',
      detail:p.id+' against a limit of 8%' }); });
    if (bankW > 0.25) breaches.push({kind:'sector', over:(bankW*100).toFixed(0)+'%',
      detail:'banks against a limit of 25%'});
    if (hhi > 0.18) breaches.push({kind:'hhi', over:hhi.toFixed(2),
      detail:'concentration index against a limit of 0.18'});
    if (bets < 5) breaches.push({kind:'effective_bets', over:bets.toFixed(1),
      detail:'below the floor of 5.0 independent positions'});

    return {
      m: { hhi:hhi.toFixed(3), hhiColor: hhi>0.18?NEG:POS,
           bets:bets.toFixed(2), bimColor: bets<5?NEG:POS,
           heat:(heat*100).toFixed(1)+'%', heatColor: heat>0.06?NEG:POS,
           n:String(live.length), hasBreach:breaches.length>0, clean:breaches.length===0,
           bankW:(bankW*100).toFixed(0)+'%', techW:(techW*100).toFixed(0)+'%' },
      breaches,
      rows: BOOK.map(p => {
        const on = !this.state.off[p.id];
        const w = on ? p.w/tot : 0;
        return { id:p.id, sector:p.sector, country:p.country,
          w: on ? (w*100).toFixed(1)+'%' : '—',
          risk: on ? (p.risk*100).toFixed(1)+'%' : '—',
          flag: on ? (w>0.08 ? 'over' : 'ok') : 'out',
          cls: on ? (w>0.08 ? 'no' : 'ok') : 'n',
          ink: on ? INK : MUT, mut: MUT,
          wColor: on && w>0.08 ? NEG : (on ? INK : MUT),
          wWeight: on && w>0.08 ? '700' : '400',
          style:`cursor:pointer;opacity:${on?1:0.45}`,
          toggle: () => this.setState({ off: {...this.state.off, [p.id]: on} }) };
      })
    };
  }
}
""",
)
