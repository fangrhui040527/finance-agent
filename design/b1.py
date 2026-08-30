exec(open("gen.py").read())

# ─────────────────────────────── ASK / dashboard
page("Main","ask", """
<div class="head">
  <div><h1>Ask</h1>
  <p>Every question is planned before it is answered — you see which agents will run and
     what it costs before anything is spent.</p></div>
  <div style="display:flex;gap:8px;align-items:center">
    <span class="chip ok">● feeds live</span><span class="chip wait">echo backend</span>
  </div>
</div>

<div style="display:flex;gap:10px;margin-bottom:8px">
  <div style="flex-grow:1;position:relative">
    <input value="{{q}}" onInput="{{ setQ }}" placeholder="Ask about a holding, a move, a risk…"
      style="width:100%;font-family:inherit;font-size:15px;padding:13px 16px;border-radius:4px;
      border:1px solid var(--rule-2);background:var(--panel);color:var(--ink);box-shadow:var(--shadow)">
  </div>
  <button class="btn pri" style="padding:0 22px;font-size:14px">Plan</button>
</div>
<div style="display:flex;gap:7px;flex-wrap:wrap;margin-bottom:22px">
  <sc-for list="{{examples}}" as="ex" hint-placeholder-count="4">
    <button class="btn" onClick="{{ ex.pick }}" style="font-size:12px;padding:5px 11px;font-weight:500">{{ex.short}}</button>
  </sc-for>
</div>

<div class="grid" style="grid-template-columns:1fr 320px;align-items:start">
  <div class="grid">
    <div class="card" style="padding:0;overflow:hidden">
      <div style="padding:15px 20px;border-bottom:1px solid var(--rule);display:flex;
                  justify-content:space-between;align-items:center">
        <div><p class="eyebrow" style="margin:0 0 3px">The plan</p>
        <div style="font-size:14px;color:var(--ink-2)">{{plan.title}}</div></div>
        <span class="chip n">{{plan.intent}}</span>
      </div>
      <sc-if value="{{plan.refused}}" hint-placeholder-val="{{false}}">
        <div style="padding:18px 20px">
          <div class="box stop"><span class="lbl">Refused</span>
          <p>{{plan.reason}}</p></div>
          <p style="font-size:12.5px;color:var(--muted);margin:12px 0 0">
            <b style="color:var(--ink-2)">What would help —</b> {{plan.help}}</p>
        </div>
      </sc-if>
      <sc-if value="{{plan.allowed}}" hint-placeholder-val="{{true}}">
        <div style="padding:16px 20px">
          <div style="display:flex;flex-direction:column;gap:1px">
            <sc-for list="{{plan.agents}}" as="a" hint-placeholder-count="5">
              <div style="display:flex;align-items:center;gap:12px;padding:8px 0;
                          border-bottom:1px solid var(--rule)">
                <code style="font-size:12px;color:var(--accent);font-weight:700;width:150px">{{a.id}}</code>
                <span style="font-size:13px;color:var(--ink-2);flex-grow:1">{{a.does}}</span>
                <span class="chip n">{{a.tier}}</span>
                <span class="num" style="font-size:12px;color:var(--muted);width:62px;text-align:right">{{a.cost}}</span>
              </div>
            </sc-for>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:baseline;padding-top:13px">
            <span style="font-size:12.5px;color:var(--muted)">Estimated before anything is spent</span>
            <span class="num" style="font-size:16px;font-weight:600">{{plan.cost}}</span>
          </div>
        </div>
      </sc-if>
    </div>

    <div class="box key"><span class="lbl">Refusals are the product</span>
    <p>A question the system cannot answer honestly comes back as a refusal with what would
       help — never as a cheaper wrong answer. If it never refuses, something is broken.</p></div>
  </div>

  <div class="grid">
    <div class="card">
      <p class="eyebrow">Needs you</p>
      <div style="display:flex;flex-direction:column;gap:11px">
        <div style="display:flex;gap:11px;align-items:flex-start">
          <span class="chip wait" style="margin-top:2px">3</span>
          <div><div style="font-weight:600;font-size:13px">Predictions reached horizon</div>
          <div style="font-size:12px;color:var(--muted)">Grade them before you look at the chart</div></div>
        </div>
        <div style="display:flex;gap:11px;align-items:flex-start">
          <span class="chip no" style="margin-top:2px">2</span>
          <div><div style="font-weight:600;font-size:13px">Breakers past review date</div>
          <div style="font-size:12px;color:var(--muted)">MYX:1155 · XNAS:NVDA</div></div>
        </div>
        <div style="display:flex;gap:11px;align-items:flex-start">
          <span class="chip ok" style="margin-top:2px">OK</span>
          <div><div style="font-weight:600;font-size:13px">Drawdown scalar 1.0</div>
          <div style="font-size:12px;color:var(--muted)">No mechanical de-risk in force</div></div>
        </div>
      </div>
    </div>

    <div class="card">
      <p class="eyebrow">Forward record</p>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        <div class="stat"><b>14</b><span>logged</span></div>
        <div class="stat"><b>6</b><span>graded</span></div>
      </div>
      <div style="height:5px;border-radius:3px;background:var(--panel-2);margin:15px 0 8px;overflow:hidden">
        <div style="width:20%;height:100%;background:var(--accent)"></div>
      </div>
      <p style="font-size:12px;color:var(--muted);margin:0">
        24 more graded calls before the calibration table measures skill rather than luck.</p>
    </div>

    <div class="card" style="padding:15px 18px">
      <p class="eyebrow" style="margin-bottom:7px">Sources</p>
      <div style="display:flex;flex-direction:column;gap:7px;font-size:12.5px">
        <div style="display:flex;justify-content:space-between"><span>GDELT news</span><span class="chip ok">live</span></div>
        <div style="display:flex;justify-content:space-between"><span>Stooq bars</span><span class="chip ok">live</span></div>
        <div style="display:flex;justify-content:space-between"><span>Filings</span><span class="chip n">unwired</span></div>
        <div style="display:flex;justify-content:space-between"><span>Ownership · macro</span><span class="chip n">unwired</span></div>
      </div>
    </div>
  </div>
</div>
""", logic="""
const PLANS = {
  why: {title:'why did maybank fall today', intent:'why_it_moved', allowed:true, refused:false,
    cost:'RM 0.99', agents:[
      {id:'a3_price_technical', does:'What price actually did', tier:'balanced', cost:'0.12'},
      {id:'a4_news_narrative', does:'Triage the day\\u2019s stories', tier:'cheap', cost:'0.01'},
      {id:'a5_catalyst_events', does:'Match candidates to base rates', tier:'balanced', cost:'0.12'},
      {id:'a6_macro_regime', does:'Rates, curve, regime', tier:'balanced', cost:'0.12'},
      {id:'a9_attribution', does:'Decompose before naming a cause', tier:'reason', cost:'0.62'}]},
  buy: {title:'should I add to my Maybank position', intent:'should_i_buy', allowed:true, refused:false,
    cost:'RM 1.49', agents:[
      {id:'a1_fundamentals', does:'Statements as reported, point-in-time', tier:'balanced', cost:'0.12'},
      {id:'a2_valuation', does:'Ranges and implied assumptions', tier:'balanced', cost:'0.12'},
      {id:'a10_thesis', does:'Assemble a stance, or decline one', tier:'reason', cost:'0.62'},
      {id:'a11_red_team', does:'The strongest case against', tier:'reason', cost:'0.62'}]},
  target: {title:'what price will Tenaga hit next month', intent:'out_of_scope',
    allowed:false, refused:true, cost:'RM 0.00', agents:[],
    reason:'Point price forecasts are refused. A number with no distribution behind it reads as knowledge and is not.',
    help:'Ask what the price already requires to be true, or what would falsify the thesis you hold.'},
  own: {title:'what am I actually exposed to', intent:'what_do_i_own', allowed:true, refused:false,
    cost:'RM 0.24', agents:[
      {id:'a12_portfolio_risk', does:'Concentration, heat, effective bets', tier:'balanced', cost:'0.12'},
      {id:'a8_ownership_flow', does:'Who else holds it', tier:'balanced', cost:'0.12'}]}
};
class Component extends DCLogic {
  constructor(p){ super(p); this.state = { key:'why' }; }
  renderVals(){
    const plan = PLANS[this.state.key];
    return {
      q: plan.title,
      setQ: () => {},
      plan,
      examples: [
        {key:'why', short:'why did it move'},
        {key:'buy', short:'should I add'},
        {key:'own', short:'what do I own'},
        {key:'target', short:'price target ⟶ refused'}
      ].map(e => ({ ...e, pick: () => this.setState({ key:e.key }) }))
    };
  }
}
""")
