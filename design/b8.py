from gen import page  # run via `python -m design.build` or from design/

page(
    "WorldMonitor",
    "prices",
    """
<div class="head">
  <div><h1>World monitor</h1>
  <p>What came in, from where, in what language — and which of it earned a model call.
     A source that breaks says so; it never returns an empty list you could read as a quiet day.</p></div>
  <div style="display:flex;gap:8px;align-items:center">
    <span class="chip ok">● polling</span>
    <span style="font-size:12px;color:var(--muted)">last 15:22:04 · next in 4m</span>
  </div>
</div>

<div class="grid" style="grid-template-columns:repeat(5,1fr);margin-bottom:18px">
  <div class="card stat"><b>1,284</b><span>fetched today</span></div>
  <div class="card stat"><b style="color:var(--muted)">−961</b><span>wire duplicates</span></div>
  <div class="card stat"><b>323</b><span>indexed</span></div>
  <div class="card stat"><b style="color:var(--accent)">11</b><span>escalated</span></div>
  <div class="card stat"><b>96</b><span>polls · 15 min</span></div>
</div>

<div class="grid" style="grid-template-columns:minmax(0,1fr) 360px;align-items:start">
  <div class="grid">
    <div class="card" style="padding:0;overflow:hidden">
      <div style="padding:13px 18px;border-bottom:1px solid var(--rule);display:flex;
                  justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
        <p class="eyebrow" style="margin:0">Incoming · click a story</p>
        <div style="display:flex;gap:6px">
          <sc-for list="{{filters}}" as="f" hint-placeholder-count="3">
            <button class="btn" onClick="{{ f.pick }}" style="{{f.style}}">{{f.label}}</button>
          </sc-for>
        </div>
      </div>
      <div>
        <sc-for list="{{stories}}" as="s" hint-placeholder-count="6">
          <div onClick="{{ s.pick }}" style="{{s.rowStyle}}">
            <div style="{{s.barStyle}}"></div>
            <div style="flex-grow:1;min-width:0">
              <div style="font-size:13.5px;font-weight:600;color:{{s.titleColor}};line-height:1.4">{{s.title}}</div>
              <div style="font-size:11.5px;color:var(--muted);margin-top:2px">
                {{s.domain}} · {{s.country}} · {{s.lang}} · {{s.ago}}</div>
            </div>
            <div style="text-align:right;flex:none">
              <span class="chip {{s.cls}}">{{s.verdict}}</span>
              <div class="num" style="font-size:11px;color:var(--muted);margin-top:3px">rel {{s.rel}}</div>
            </div>
          </div>
        </sc-for>
      </div>
    </div>

    <div class="card">
      <p class="eyebrow">Selected · five dimensions</p>
      <div style="display:flex;gap:10px;align-items:baseline;margin-bottom:14px">
        <span style="font-size:14.5px;font-weight:600;line-height:1.4">{{sel.title}}</span>
      </div>
      <sc-for list="{{sel.dims}}" as="d" hint-placeholder-count="5">
        <div style="display:flex;align-items:center;gap:14px;margin-bottom:9px">
          <span style="width:96px;font-size:12.5px;color:var(--ink-2);text-align:right;flex:none">{{d.name}}</span>
          <div style="flex-grow:1;height:16px;background:var(--panel-2);border-radius:2px;position:relative">
            <div style="{{d.style}}"></div>
            <div style="{{d.gate}}"></div>
          </div>
          <span class="num" style="width:44px;font-size:12px;text-align:right;flex:none">{{d.v}}</span>
        </div>
      </sc-for>
      <div class="box {{sel.boxCls}}" style="margin-top:14px">
        <span class="lbl">{{sel.gateTitle}}</span>
        <p>{{sel.gateWhy}}</p>
      </div>
      <div style="display:flex;gap:16px;margin-top:13px;font-size:11.5px;color:var(--muted);flex-wrap:wrap">
        <span>entities <code style="font-size:11px;color:var(--ink-2)">{{sel.entities}}</code></span>
        <span>dedup hash <code style="font-size:11px">{{sel.hash}}</code></span>
        <span>trust <span class="chip n">{{sel.trust}}</span></span>
      </div>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <p class="eyebrow">Sources · what is actually wired</p>
      <div style="display:flex;flex-direction:column;gap:0">
        <sc-for list="{{sources}}" as="s" hint-placeholder-count="8">
          <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--rule)">
            <span style="{{s.dot}}"></span>
            <div style="flex-grow:1;min-width:0">
              <div style="font-size:12.5px;font-weight:600;color:{{s.color}}">{{s.name}}</div>
              <div style="font-size:11px;color:var(--muted)">{{s.note}}</div>
            </div>
            <span class="chip {{s.cls}}" style="flex:none">{{s.state}}</span>
          </div>
        </sc-for>
      </div>
      <p style="font-size:11.5px;color:var(--muted);margin:12px 0 0">
        Keys for unwired sources are deliberately absent from <code style="font-size:11px">.env</code>.
        A key that sets nothing reads as configured, and that is worse than a missing key.</p>
    </div>

    <div class="card">
      <p class="eyebrow">Coverage this window</p>
      <div style="margin-bottom:12px">
        <div style="font-size:11px;color:var(--muted);margin-bottom:6px">BY LANGUAGE</div>
        <sc-for list="{{langs}}" as="l" hint-placeholder-count="5">
          <div style="display:flex;align-items:center;gap:9px;margin-bottom:5px">
            <span style="width:64px;font-size:11.5px;flex:none">{{l.name}}</span>
            <div style="flex-grow:1;height:6px;background:var(--panel-2);border-radius:2px">
              <div style="{{l.style}}"></div></div>
            <span class="num" style="width:30px;font-size:11px;text-align:right;color:var(--muted)">{{l.n}}</span>
          </div>
        </sc-for>
      </div>
      <div style="font-size:11px;color:var(--muted);margin-bottom:6px">BY COUNTRY</div>
      <div style="display:flex;gap:5px;flex-wrap:wrap">
        <span class="chip n">MY 84</span><span class="chip n">US 61</span><span class="chip n">SG 39</span>
        <span class="chip n">HK 28</span><span class="chip n">CN 24</span><span class="chip n">GB 19</span>
        <span class="chip n">JP 14</span><span class="chip n">+22 more</span>
      </div>
    </div>

    <div class="box warn"><span class="lbl">The record cap decides cadence</span>
    <p>GDELT returns at most 250 records per call. A narrow watchlist never approaches it, so
       catching up twice a day loses nothing — a whole-market query would lose coverage
       silently.</p></div>
  </div>
</div>
""",
    logic="""
const ACC='#0F5C63', NEG='#9E3626', MUT='#606D71', INK='#15191B', WARN='#8A5B12';
const STORIES = [
  {id:1, title:'Maybank Q2 net interest margin slips to 2.04%, below guidance',
   domain:'theedgemalaysia.com', country:'Malaysia', lang:'English', ago:'12m',
   rel:0.92, pol:-0.61, inten:0.67, unc:0.12, fwd:0.33, ent:'MYX:1155', hash:'a71f2c9e4b0d',
   trust:'CURATED_NEWS', held:true},
  {id:2, title:'马来亚银行第二季净利息收益率降至2.04%',
   domain:'sinchew.com.my', country:'Malaysia', lang:'Chinese', ago:'11m',
   rel:0.88, pol:-0.58, inten:0.61, unc:0.14, fwd:0.30, ent:'MYX:1155', hash:'a71f2c9e4b0d',
   trust:'CURATED_NEWS', held:true, dupe:true},
  {id:3, title:'Nvidia supply constraints ease as CoWoS capacity comes online',
   domain:'reuters.com', country:'United States', lang:'English', ago:'34m',
   rel:0.71, pol:0.44, inten:0.52, unc:0.28, fwd:0.71, ent:'XNAS:NVDA', hash:'3d8b1a05fe72',
   trust:'CURATED_NEWS', held:true},
  {id:4, title:'Bank Negara holds OPR at 3.00% for a fifth meeting',
   domain:'bnm.gov.my', country:'Malaysia', lang:'English', ago:'2h',
   rel:0.55, pol:0.02, inten:0.31, unc:0.09, fwd:0.48, ent:'macro:MY_OPR', hash:'9c04e7f13ab6',
   trust:'FILINGS', held:false, macro:true},
  {id:5, title:'Analysts see regional banks under pressure into 2027',
   domain:'seekingalpha.com', country:'United States', lang:'English', ago:'3h',
   rel:0.29, pol:-0.33, inten:0.40, unc:0.55, fwd:0.66, ent:'sector:banks', hash:'5e2a91c7d804',
   trust:'WEB_SEARCH', held:false},
  {id:6, title:'Petronas awards offshore contract to local consortium',
   domain:'nst.com.my', country:'Malaysia', lang:'English', ago:'4h',
   rel:0.61, pol:0.38, inten:0.44, unc:0.19, fwd:0.29, ent:'MYX:5681', hash:'7b3f0d62ae19',
   trust:'CURATED_NEWS', held:false}
];
function esc(s){ return s.rel >= 0.34 && s.held; }
class Component extends DCLogic {
  constructor(p){ super(p); this.state = { sel:1, f:'all' }; }
  renderVals(){
    const f = this.state.f;
    const shown = STORIES.filter(s => f==='all'
      || (f==='esc' && esc(s)) || (f==='dupe' && s.dupe));
    const sel = STORIES.find(s => s.id === this.state.sel) || STORIES[0];
    const on = esc(sel);
    const dim = (name, v, gate) => ({ name, v: v.toFixed(2),
      style:`position:absolute;left:0;top:2px;bottom:2px;width:${Math.abs(v)*100}%;
        background:${name==='polarity' ? (v<0?NEG:'#3B6A38') : ACC};border-radius:2px`,
      gate: gate ? `position:absolute;left:34%;top:-2px;bottom:-2px;width:2px;background:${NEG};opacity:.65` : '' });
    return {
      filters:[['all','All 1,284'],['esc','Escalated 11'],['dupe','Duplicates']].map(([k,label])=>({
        label, pick:()=>this.setState({f:k}),
        style:`font-size:11.5px;padding:4px 10px;${f===k
          ?'background:#0F5C63;border-color:#0F5C63;color:#fff;font-weight:600':''}` })),
      stories: shown.map(s => {
        const e = esc(s), isSel = s.id === this.state.sel;
        return { ...s, rel:s.rel.toFixed(2),
          verdict: s.dupe ? 'duplicate' : (e ? 'escalated' : 'indexed'),
          cls: s.dupe ? 'n' : (e ? 'ok' : 'n'),
          titleColor: s.dupe ? MUT : INK,
          barStyle:`width:3px;align-self:stretch;flex:none;border-radius:2px;
            background:${s.dupe ? '#C2C9C6' : (e ? ACC : '#D6DBD9')}`,
          rowStyle:`display:flex;gap:12px;align-items:center;padding:11px 18px;cursor:pointer;
            border-bottom:1px solid #D6DBD9;background:${isSel?'#EFF2F1':'#FFFFFF'};
            opacity:${s.dupe?0.6:1}`,
          pick: () => this.setState({ sel:s.id }) };
      }),
      sel: { title: sel.title, entities: sel.ent, hash: sel.hash, trust: sel.trust,
        dims: [dim('relevance', sel.rel, true), dim('polarity', sel.pol, false),
               dim('intensity', sel.inten, false), dim('uncertainty', sel.unc, false),
               dim('forwardness', sel.fwd, false)],
        boxCls: on ? 'key' : '',
        gateTitle: on ? 'Escalated — earns a model call' : 'Indexed only — no model call',
        gateWhy: on
          ? 'Relevance is above the 0.34 gate AND the entity is one you hold or watch. Both conditions are required; volume alone never buys a model call.'
          : (sel.rel < 0.34
             ? 'Relevance is below the 0.34 gate (the red line). It is stored and searchable, but nothing reads it for you.'
             : 'Relevance clears the gate, but the entity touches nothing in your holdings or watchlist. The gate exists so the queue stays short enough to read.') },
      sources: [
        {name:'GDELT 2.0 DOC', note:'100+ languages, worldwide · no key', state:'live', cls:'ok'},
        {name:'Stooq', note:'daily bars, 92 exchanges · no key', state:'live', cls:'ok'},
        {name:'WorldMonitor', note:'150+ curated feeds, 54-country index', state:'registered', cls:'n'},
        {name:'Bursa · SGXNET · HKEXnews', note:'exchange announcements', state:'registered', cls:'n'},
        {name:'SEC EDGAR', note:'needs a contact User-Agent', state:'registered', cls:'n'},
        {name:'Marketaux · Finnhub', note:'news with sentiment · key', state:'registered', cls:'n'},
        {name:'Frankfurter', note:'ECB reference FX · no key', state:'registered', cls:'n'},
        {name:'FRED', note:'macro series · key', state:'registered', cls:'n'}
      ].map(s => ({ ...s, color: s.cls==='ok' ? INK : MUT,
        dot:`width:7px;height:7px;border-radius:50%;flex:none;
          background:${s.cls==='ok'?'#3B6A38':'transparent'};border:1.5px solid ${s.cls==='ok'?'#3B6A38':'#C2C9C6'}` })),
      langs: [['English',148],['Chinese',61],['Malay',44],['Japanese',31],['Other',39]]
        .map(([name,n]) => ({ name, n:String(n),
          style:`width:${n/148*100}%;height:100%;background:${ACC};border-radius:2px` }))
    };
  }
}
""",
)
