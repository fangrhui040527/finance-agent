exec(open("gen.py").read())

# ─────────────────────────────── LEARN
page("Learn","learn", """
<div class="head">
  <div><h1>Learn</h1>
  <p>Thirty concepts in an order the code enforces. Asking for one whose prerequisites you
     have not demonstrated is refused, not warned about.</p></div>
  <span class="chip n">{{done}} of 30 mastered</span>
</div>

<div class="grid" style="grid-template-columns:300px minmax(0,1fr);align-items:start">
  <div class="card" style="padding:0;overflow:hidden">
    <div style="padding:14px 18px;border-bottom:1px solid var(--rule)">
      <p class="eyebrow" style="margin:0">Curriculum · click to mark known</p>
    </div>
    <div style="max-height:640px;overflow-y:auto">
      <sc-for list="{{levels}}" as="L" hint-placeholder-count="8">
        <div>
          <div style="padding:9px 18px 5px;background:var(--panel-2);font-size:10.5px;
                      font-weight:700;letter-spacing:.11em;text-transform:uppercase;color:var(--muted)">
            {{L.name}} <span style="float:right">{{L.count}}</span></div>
          <sc-for list="{{L.items}}" as="c" hint-placeholder-count="4">
            <div onClick="{{ c.toggle }}" style="{{c.style}}">
              <span style="{{c.dot}}"></span>
              <span style="font-size:13px;color:{{c.color}}">{{c.title}}</span>
            </div>
          </sc-for>
        </div>
      </sc-for>
    </div>
  </div>

  <div class="grid">
    <sc-if value="{{lesson.blocked}}" hint-placeholder-val="{{true}}">
      <div class="card" style="border-left:3px solid var(--warn);background:var(--warn-soft)">
        <span class="lbl" style="font-size:10.5px;font-weight:700;letter-spacing:.13em;
          text-transform:uppercase;color:var(--warn);display:block;margin-bottom:6px">Not yet</span>
        <p style="margin:0;font-size:14.5px;color:var(--ink);line-height:1.55">{{lesson.blockText}}</p>
        <code style="display:block;margin-top:12px;font-size:11.5px;color:var(--muted);
          line-height:1.8">{{lesson.chain}}</code>
      </div>
    </sc-if>
    <sc-if value="{{lesson.open}}" hint-placeholder-val="{{false}}">
      <div class="card">
        <h2 style="font-size:20px;margin-bottom:8px">{{lesson.title}}</h2>
        <p style="margin:0;font-size:15px;color:var(--ink-2);line-height:1.6">{{lesson.line}}</p>
        <div class="box stop" style="margin-top:16px"><span class="lbl">The common error</span>
        <p>{{lesson.error}}</p></div>
        <div class="box key" style="margin-top:11px"><span class="lbl">Check it landed</span>
        <p>{{lesson.check}}</p></div>
      </div>
    </sc-if>

    <div class="box"><p style="color:var(--ink-2)">
      <b>Order is enforced, not suggested.</b> Kelly sits behind expected value, which sits
      behind base rates, which sit behind what a share actually is. The graph is acyclic and
      checked at load — you cannot be taught position sizing before you can price a coin flip.</p></div>
  </div>
</div>
""", logic="""
const ACC='#0F5C63', MUT='#606D71', INK='#15191B';
const CUR = [
  ['L1 · Money', [['share','What a share actually is'],['compounding','Compounding, and its cost'],['inflation','Real versus nominal']]],
  ['L2 · Statements', [['revenue','Revenue is not cash'],['margin','Where margin comes from'],['accruals','Accruals and the cash gap'],['debt','Debt, and when it bites']]],
  ['L3 · Valuation', [['multiple','What a multiple encodes'],['reverse_dcf','Reverse DCF'],['archetype','Valuing by archetype'],['range','Ranges, never targets']]],
  ['L4 · Price', [['volatility','Volatility is not risk'],['trend_vs_noise','Trend versus noise'],['drawdown','Drawdown arithmetic']]],
  ['L5 · News', [['base_rate','Base rates first'],['catalyst','What a catalyst is'],['narrative','Narrative is not cause'],['factor_decomposition','Decomposing a move']]],
  ['L6 · Risk', [['expected_value','Expected value'],['position_sizing','Position sizing'],['kelly','Kelly, and why a quarter of it'],['ruin','Risk of ruin']]],
  ['L7 · Portfolio', [['correlation','Correlation eats diversification'],['effective_bets','Effective bets'],['heat','Portfolio heat'],['rebalance','Rebalancing']]],
  ['L8 · Process', [['calibration','Calibration'],['journal','Why you write it down first'],['survivorship','Survivorship'],['overfitting','Overfitting your own history']]]
];
const CHAIN = ['share','compounding','volatility','trend_vs_noise','factor_decomposition',
               'base_rate','expected_value','position_sizing'];
const TITLES = {}; CUR.forEach(([,xs]) => xs.forEach(([k,t]) => TITLES[k]=t));
class Component extends DCLogic {
  constructor(p){ super(p); this.state = { known:{ share:1, compounding:1, volatility:1 } }; }
  renderVals(){
    const known = this.state.known;
    const missing = CHAIN.filter(k => !known[k]);
    const nDone = Object.keys(known).filter(k=>known[k]).length;
    const lesson = missing.length
      ? { blocked:true, open:false,
          blockText:'Before Kelly, and why a quarter of it — ' + TITLES[missing[0]].toLowerCase() +
                    ' has to come first.',
          chain:'teaching order:  ' + missing.concat(['kelly']).join('  →  ') }
      : { blocked:false, open:true, title:'Kelly, and why a quarter of it',
          line:'Full Kelly maximises long-run growth only if your edge estimate is exact, which it never is. Overestimate the edge and Kelly overbets in the same proportion — which is why practitioners size at a quarter of it.',
          error:'Kelly tells you how much to bet. It does not — it tells you the ceiling above which you are certainly overbetting.',
          check:'Your edge estimate is off by half. What happens to the growth rate at full Kelly, and at a quarter?' };
    return {
      done:String(nDone), lesson,
      levels: CUR.map(([name, items]) => ({
        name, count: items.filter(([k]) => known[k]).length + '/' + items.length,
        items: items.map(([k,title]) => {
          const on = !!known[k];
          return { title, color: on ? INK : MUT,
            style:`display:flex;align-items:center;gap:10px;padding:7px 18px;cursor:pointer;
              border-bottom:1px solid #EFF2F1;background:${on?'#FFFFFF':'#FFFFFF'}`,
            dot:`width:7px;height:7px;border-radius:50%;flex:none;
              background:${on?ACC:'transparent'};border:1.5px solid ${on?ACC:'#C2C9C6'}`,
            toggle: () => this.setState({ known: {...known, [k]: on?0:1} }) };
        })
      }))
    };
  }
}
""")

# ─────────────────────────────── SETTINGS
page("Settings","set", """
<div class="head">
  <div><h1>Settings</h1>
  <p>Where the numbers come from, what is wired, and the bounds a config file cannot widen.</p></div>
</div>

<div class="grid" style="grid-template-columns:minmax(0,1fr) 380px;align-items:start">
  <div class="grid">
    <div class="card" style="border-left:3px solid {{b.color}};background:{{b.bg}}">
      <p class="eyebrow" style="color:{{b.color}}">Which model is answering</p>
      <div style="display:flex;align-items:baseline;gap:12px;margin-bottom:6px">
        <code style="font-size:16px;font-weight:700;color:var(--ink)">{{b.name}}</code>
        <button class="btn" onClick="{{ flip }}" style="font-size:11.5px;padding:3px 10px">
          {{b.action}}</button>
      </div>
      <p style="margin:0;font-size:13.5px;color:var(--ink-2)">{{b.reason}}</p>
    </div>

    <div class="card">
      <p class="eyebrow">Tier routing · callers never choose this</p>
      <table>
        <thead><tr><th>Task class</th><th>Tier</th><th>Model</th>
          <th style="text-align:right">In / Out per Mtok</th></tr></thead>
        <tbody>
          <tr><td>thesis · red team · attribution</td><td><span class="chip n">reason</span></td>
            <td><code style="font-size:12px">claude-opus-5</code></td>
            <td class="num" style="text-align:right">$5.00 / $25.00</td></tr>
          <tr><td>fundamentals · valuation · macro</td><td><span class="chip n">balanced</span></td>
            <td><code style="font-size:12px">claude-sonnet-5</code></td>
            <td class="num" style="text-align:right;color:var(--neg)">$3.00 / $15.00</td></tr>
          <tr><td>routing · news triage · dedup</td><td><span class="chip n">cheap</span></td>
            <td><code style="font-size:12px">claude-haiku-4-5</code></td>
            <td class="num" style="text-align:right">$1.00 / $5.00</td></tr>
          <tr><td>embeddings</td><td><span class="chip wait">embed</span></td>
            <td><code style="font-size:12px;color:var(--muted)">placeholder</code></td>
            <td class="num" style="text-align:right;color:var(--muted)">unwired</td></tr>
        </tbody>
      </table>
      <div class="box warn" style="margin-top:14px"><span class="lbl">Balanced tier is billed wrong</span>
      <p>Sonnet 5 lists at $2.00 / $10.00 — the table above carries Sonnet 4.6&rsquo;s rate. Cost is
         overstated by 50% on that tier, and an inflated estimate makes the budget rail refuse
         early. A wrong refusal is worse than a wrong bill.</p></div>
    </div>

    <div class="card">
      <p class="eyebrow">Markets</p>
      <table>
        <thead><tr><th>MIC</th><th>Market</th><th style="text-align:right">Cost floor</th>
          <th style="text-align:right">Minimum economic position</th></tr></thead>
        <tbody>
          <tr><td><code style="font-size:12px;font-weight:600">XNAS</code></td><td>NASDAQ · USD</td>
            <td class="num" style="text-align:right">5 bps</td>
            <td class="num" style="text-align:right;color:var(--pos)">USD 1</td></tr>
          <tr><td><code style="font-size:12px;font-weight:600">XSES</code></td><td>Singapore · SGD</td>
            <td class="num" style="text-align:right">30 bps</td>
            <td class="num" style="text-align:right">SGD 9,091</td></tr>
          <tr><td><code style="font-size:12px;font-weight:600">XKLS</code></td><td>Bursa Malaysia · MYR</td>
            <td class="num" style="text-align:right">60 bps</td>
            <td class="num" style="text-align:right">RM 4,706</td></tr>
          <tr><td><code style="font-size:12px;font-weight:600">XHKG</code></td><td>Hong Kong · HKD</td>
            <td class="num" style="text-align:right;color:var(--neg)">95 bps</td>
            <td class="num" style="text-align:right;color:var(--neg);font-weight:700">HKD 28,081</td></tr>
        </tbody>
      </table>
      <p style="font-size:12px;color:var(--muted);margin:12px 0 0">
        Hong Kong is the most expensive of the four, not the cheapest — uncapped stamp duty on
        both sides plus 0.25% retail brokerage. Ranking markets by how developed they are gets
        the cost ranking backwards.</p>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <p class="eyebrow">Bounds a config file cannot widen</p>
      <table><tbody>
        <tr><td>Single-name cap</td><td class="num" style="text-align:right">8.0%
          <span style="color:var(--muted);font-size:11px">≤ 15%</span></td></tr>
        <tr><td>Effective bets floor</td><td class="num" style="text-align:right">5.0
          <span style="color:var(--muted);font-size:11px">≥ 3.0</span></td></tr>
        <tr><td>Portfolio heat</td><td class="num" style="text-align:right">6.0%</td></tr>
        <tr><td>Risk per trade</td><td class="num" style="text-align:right">0.75%</td></tr>
        <tr><td>Graded calls for calibration</td><td class="num" style="text-align:right">30</td></tr>
      </tbody></table>
    </div>

    <div class="card">
      <p class="eyebrow">Budgets · windowed 24h</p>
      <div style="display:flex;flex-direction:column;gap:12px">
        <div>
          <div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:4px">
            <span>Interactive</span><span class="num">RM 0.41 / 25.00</span></div>
          <div style="height:6px;background:var(--panel-2);border-radius:2px">
            <div style="width:2%;height:100%;background:var(--accent);border-radius:2px"></div></div>
        </div>
        <div>
          <div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:4px">
            <span>Unattended</span><span class="num">RM 0.00 / 10.00</span></div>
          <div style="height:6px;background:var(--panel-2);border-radius:2px"></div>
        </div>
      </div>
      <p style="font-size:11.5px;color:var(--muted);margin:12px 0 0">
        Separate ceilings so a runaway background job cannot eat the budget you need the
        next morning.</p>
    </div>

    <div class="card">
      <p class="eyebrow">Watching</p>
      <sc-if value="{{empty}}" hint-placeholder-val="{{true}}">
        <div class="box stop"><span class="lbl">Nothing can escalate</span>
        <p>Holdings and watchlist are both empty, so the gate can never fire and nothing will
           ever be surfaced as worth your attention.</p></div>
      </sc-if>
      <button class="btn" onClick="{{ fill }}" style="margin-top:11px;width:100%">
        {{fillLabel}}</button>
      <sc-if value="{{filled}}" hint-placeholder-val="{{false}}">
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:12px">
          <span class="chip ok">MYX:1155</span><span class="chip ok">MYX:5347</span>
          <span class="chip n">XNAS:NVDA</span><span class="chip n">XSES:D05</span>
        </div>
      </sc-if>
    </div>
  </div>
</div>
""", logic="""
const ACC='#0F5C63', WARN='#8A5B12';
class Component extends DCLogic {
  constructor(p){ super(p); this.state = { real:false, filled:false }; }
  renderVals(){
    const b = this.state.real
      ? { name:'AnthropicBackend', color:ACC, bg:'#DCEAEA', action:'switch to echo',
          reason:'A key is set, so a real model answers. Every call is ledgered with its tier, tokens and cost.' }
      : { name:'EchoBackend', color:WARN, bg:'#F5E9D4', action:'set a key',
          reason:'No ANTHROPIC_API_KEY. Narrative output is deterministic placeholder text — a system running on this for a week is indistinguishable from one that works, which is why this panel exists.' };
    return { b, flip: () => this.setState({ real: !this.state.real }),
      empty: !this.state.filled, filled: this.state.filled,
      fillLabel: this.state.filled ? 'Edit watchlist' : 'Add holdings and watchlist',
      fill: () => this.setState({ filled: !this.state.filled }) };
  }
}
""")
