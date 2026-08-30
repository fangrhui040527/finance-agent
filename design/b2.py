exec(open("gen.py").read())

# ─────────────────────────────── WHY IT MOVED
page("WhyItMoved","why", """
<div class="head">
  <div><h1>Why it moved</h1>
  <p>The decomposition runs before any cause is named. Most single-day moves are market and
     sector; the unexplained share is always on screen.</p></div>
  <span class="chip ok">measured from feed</span>
</div>

<div style="display:flex;gap:8px;margin-bottom:20px">
  <sc-for list="{{scenarios}}" as="s" hint-placeholder-count="4">
    <button class="btn" onClick="{{ s.pick }}" style="font-size:12.5px">{{s.label}}</button>
  </sc-for>
</div>

<div class="grid" style="grid-template-columns:1fr 340px;align-items:start">
  <div class="grid">
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:20px">
        <div><code style="font-size:15px;font-weight:700;color:var(--ink)">{{d.inst}}</code>
        <span style="font-size:12.5px;color:var(--muted);margin-left:10px">{{d.window}}</span></div>
        <div class="num" style="font-size:26px;font-weight:600;color:{{d.moveColor}}">{{d.move}}</div>
      </div>

      <sc-for list="{{d.bars}}" as="b" hint-placeholder-count="5">
        <div style="display:flex;align-items:center;gap:14px;margin-bottom:11px">
          <span style="width:112px;font-size:12.5px;color:var(--ink-2);text-align:right;flex:none">{{b.name}}</span>
          <div style="flex-grow:1;height:22px;background:var(--panel-2);border-radius:2px;position:relative">
            <div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--rule-2)"></div>
            <div style="{{b.style}}"></div>
          </div>
          <span class="num" style="width:70px;font-size:12.5px;text-align:right;flex:none;color:{{b.color}}">{{b.val}}</span>
        </div>
      </sc-for>

      <div style="border-top:1px solid var(--rule);margin-top:16px;padding-top:15px;
                  display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:13px;color:var(--ink-2);font-weight:600">Unexplained</span>
        <span class="num" style="font-size:18px;font-weight:600;color:{{d.unexColor}}">{{d.unex}}</span>
      </div>
    </div>

    <div class="card" style="border-left:3px solid {{d.verdictColor}}">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
        <code style="font-size:13px;font-weight:700;color:{{d.verdictColor}}">{{d.verdict}}</code>
      </div>
      <p style="margin:0;font-size:14px;color:var(--ink-2);line-height:1.6">{{d.sentence}}</p>
      <sc-for list="{{d.caveats}}" as="c" hint-placeholder-count="2">
        <p style="margin:9px 0 0;font-size:12.5px;color:var(--muted)">
          <span style="color:var(--warn);font-weight:700">caveat</span> · {{c.t}}</p>
      </sc-for>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <p class="eyebrow">Candidate causes</p>
      <sc-if value="{{d.hasCauses}}" hint-placeholder-val="{{true}}">
        <div style="display:flex;flex-direction:column;gap:12px">
          <sc-for list="{{d.causes}}" as="c" hint-placeholder-count="2">
            <div style="padding-bottom:12px;border-bottom:1px solid var(--rule)">
              <div style="display:flex;justify-content:space-between;gap:10px;margin-bottom:4px">
                <span style="font-size:13px;font-weight:600;line-height:1.4">{{c.headline}}</span>
                <span class="num chip {{c.cls}}" style="flex:none">{{c.score}}</span>
              </div>
              <div style="font-size:11.5px;color:var(--muted)">{{c.src}} · {{c.note}}</div>
            </div>
          </sc-for>
        </div>
      </sc-if>
      <sc-if value="{{d.noCauses}}" hint-placeholder-val="{{false}}">
        <div class="box"><p><b style="color:var(--ink-2)">No catalyst cleared the bar.</b><br>
        A significant move with no identified cause is rendered as exactly that — never given
        a story to make it feel explained.</p></div>
      </sc-if>
    </div>

    <div class="card">
      <p class="eyebrow">Factor model</p>
      <table><tbody>
        <tr><td style="color:var(--muted)">β market</td><td class="num" style="text-align:right">{{d.bm}}</td></tr>
        <tr><td style="color:var(--muted)">β sector</td><td class="num" style="text-align:right">{{d.bs}}</td></tr>
        <tr><td style="color:var(--muted)">r²</td><td class="num" style="text-align:right">{{d.r2}}</td></tr>
        <tr><td style="color:var(--muted)">observations</td><td class="num" style="text-align:right">{{d.n}}</td></tr>
      </tbody></table>
      <p style="font-size:11.5px;color:var(--muted);margin:12px 0 0">
        Huber regression over a purged window. Below 120 observations the engine refuses
        rather than fitting betas to noise.</p>
    </div>
  </div>
</div>
""", logic="""
const NEG='#9E3626', POS='#3B6A38', ACC='#0F5C63', MUT='#606D71', WARN='#8A5B12';
function bar(name, pct, color){
  const w = Math.min(Math.abs(pct)*3.6, 49);
  const side = pct < 0 ? `right:50%` : `left:50%`;
  return { name, val: (pct>0?'+':'') + pct.toFixed(2) + '%', color,
    style: `position:absolute;top:3px;bottom:3px;${side};width:${w}%;background:${color};border-radius:1px` };
}
const S = {
  market: { label:'Market-wide fall', inst:'MYX:1155', window:'24 – 25 Aug 2026', move:'−9.00%',
    moveColor:NEG, unex:'8%', unexColor:MUT, verdict:'market_driven', verdictColor:ACC,
    bars:[bar('market',-8.80,NEG),bar('sector',-1.00,NEG),bar('style',-0.12,MUT),
          bar('currency',0.00,MUT),bar('idiosyncratic',0.92,POS)],
    sentence:'This was the market. 88% of the move is explained by the index and the sector — no company-specific cause was sought, and none should be read into it.',
    caveats:[], hasCauses:false, noCauses:true, causes:[],
    bm:'1.104', bs:'0.487', r2:'0.71', n:'250' },
  idio: { label:'Idiosyncratic jump', inst:'XNAS:NVDA', window:'26 – 27 Aug 2026', move:'+7.20%',
    moveColor:POS, unex:'92%', unexColor:NEG, verdict:'no_identified_catalyst', verdictColor:WARN,
    bars:[bar('market',0.44,POS),bar('sector',0.10,POS),bar('style',0.05,MUT),
          bar('currency',0.00,MUT),bar('idiosyncratic',6.61,POS)],
    sentence:'92% of this move is unexplained by the factor model, and no catalyst scored above the threshold. Moves of this shape historically reverse more often than they continue.',
    caveats:[{t:'unexplained share above 70% — treat any narrative offered for this with suspicion'},
             {t:'entry on an unexplained move is staged, not full size'}],
    hasCauses:false, noCauses:true, causes:[],
    bm:'1.312', bs:'0.402', r2:'0.58', n:'250' },
  catal: { label:'Real catalyst', inst:'MYX:1155', window:'12 – 13 Aug 2026', move:'−4.60%',
    moveColor:NEG, unex:'61%', unexColor:WARN, verdict:'catalyst_matched', verdictColor:ACC,
    bars:[bar('market',-1.10,NEG),bar('sector',-0.62,NEG),bar('style',-0.08,MUT),
          bar('currency',-0.02,MUT),bar('idiosyncratic',-2.78,NEG)],
    sentence:'A results miss on net interest margin matches the residual in direction, size and timing. The base rate for this event type on a T1 bank is a 3–6% single-day drawdown.',
    caveats:[{t:'base-rate cell has 34 observations — thin, but above the 30 floor'}],
    hasCauses:true, noCauses:false,
    causes:[{headline:'Q2 NIM falls to 2.04%, below guidance', score:'0.78', cls:'ok',
             src:'Bursa announcement', note:'timing matches to the session'},
            {headline:'Sector downgrade from a regional broker', score:'0.31', cls:'n',
             src:'wire', note:'below threshold — reported, not used'}],
    bm:'1.104', bs:'0.487', r2:'0.71', n:'250' },
  quiet: { label:'Quiet day', inst:'XSES:D05', window:'27 – 28 Aug 2026', move:'+0.10%',
    moveColor:MUT, unex:'—', unexColor:MUT, verdict:'not_significant', verdictColor:MUT,
    bars:[bar('market',0.08,MUT),bar('sector',0.01,MUT),bar('style',0.00,MUT),
          bar('currency',0.00,MUT),bar('idiosyncratic',0.01,MUT)],
    sentence:'Nothing happened. The move is inside the noise band for this instrument, so no decomposition is reported and no cause is sought.',
    caveats:[], hasCauses:false, noCauses:false, causes:[],
    bm:'0.912', bs:'0.531', r2:'0.66', n:'250' }
};
class Component extends DCLogic {
  constructor(p){ super(p); this.state = { k:'market' }; }
  renderVals(){
    return { d: S[this.state.k],
      scenarios: Object.keys(S).map(k => ({ label:S[k].label, pick: () => this.setState({k}) })) };
  }
}
""")
