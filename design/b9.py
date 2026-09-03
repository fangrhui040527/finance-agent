from gen import page  # run via `python -m design.build` or from design/

page(
    "Agents",
    "ask",
    """
<div class="head">
  <div><h1>Agents</h1>
  <p>Sixteen registered capabilities in five layers. Evidence agents never talk to each other —
     they talk to their own store and emit typed, cited facts. Click one for what it is doing.</p></div>
  <div style="display:flex;gap:8px;align-items:center">
    <span class="chip ok">16 registered</span><span class="chip n">4 may reason</span>
  </div>
</div>

<div class="grid" style="grid-template-columns:minmax(0,1fr) 470px;align-items:start">

  <div class="grid">
    <sc-for list="{{layers}}" as="L" hint-placeholder-count="5">
      <div>
        <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:8px">
          <p class="eyebrow" style="margin:0">{{L.name}}</p>
          <span style="flex-grow:1;height:1px;background:var(--rule)"></span>
          <span style="font-size:11px;color:var(--muted)">{{L.note}}</span>
        </div>
        <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px">
          <sc-for list="{{L.agents}}" as="a" hint-placeholder-count="4">
            <div onClick="{{ a.pick }}" style="{{a.style}}">
              <div style="display:flex;align-items:center;gap:7px;margin-bottom:5px">
                <span style="{{a.pulse}}"></span>
                <code style="font-size:11.5px;font-weight:700;color:{{a.idColor}}">{{a.short}}</code>
                <span style="flex-grow:1"></span>
                <span class="chip {{a.tierCls}}" style="font-size:9.5px;padding:1px 5px">{{a.tier}}</span>
              </div>
              <div style="font-size:12px;color:{{a.nameColor}};font-weight:600;line-height:1.3">{{a.name}}</div>
              <div style="font-size:10.5px;color:var(--muted);margin-top:4px">{{a.status}}</div>
            </div>
          </sc-for>
        </div>
      </div>
    </sc-for>

    <div class="box key"><span class="lbl">Why they cannot talk to each other</span>
    <p>Evidence agents read one store and emit typed findings. Nothing passes between them
       directly — that is what stops two agents synthesising a claim neither one&rsquo;s evidence
       supports.</p></div>
  </div>

  <div class="card" style="padding:0;overflow:hidden;position:sticky;top:0">
    <div style="padding:16px 20px;border-bottom:1px solid var(--rule);background:var(--panel-2)">
      <div style="display:flex;align-items:center;gap:9px;margin-bottom:6px">
        <span style="{{d.pulse}}"></span>
        <code style="font-size:14px;font-weight:700;color:var(--accent)">{{d.id}}</code>
        <span style="flex-grow:1"></span>
        <span class="chip {{d.stateCls}}">{{d.state}}</span>
      </div>
      <div style="font-size:15px;font-weight:600;margin-bottom:3px">{{d.name}}</div>
      <p style="margin:0;font-size:12.5px;color:var(--ink-2);line-height:1.5">{{d.job}}</p>
    </div>

    <div style="padding:15px 20px;border-bottom:1px solid var(--rule)">
      <p class="eyebrow" style="margin-bottom:8px">Doing right now</p>
      <div class="term">
        <div class="term-bar"><i></i><i></i><i></i><b>{{d.id}} · live</b></div>
        <pre>{{d.now}}</pre>
      </div>
    </div>

    <div style="padding:15px 20px;border-bottom:1px solid var(--rule)">
      <p class="eyebrow" style="margin-bottom:8px">Trace · last invocation</p>
      <div style="display:flex;flex-direction:column;gap:0">
        <sc-for list="{{d.trace}}" as="t" hint-placeholder-count="5">
          <div style="display:flex;gap:9px;align-items:flex-start;padding:6px 0;
                      border-bottom:1px solid var(--rule)">
            <span style="{{t.sym}}">{{t.mark}}</span>
            <div style="flex-grow:1;min-width:0">
              <div style="font-size:12px;color:{{t.color}};line-height:1.45">{{t.what}}</div>
              <sc-if value="{{t.hasDetail}}" hint-placeholder-val="{{false}}">
                <code style="font-size:10.5px;color:var(--muted);display:block;margin-top:2px">{{t.detail}}</code>
              </sc-if>
            </div>
            <span class="num" style="font-size:10.5px;color:var(--muted);flex:none">{{t.ms}}</span>
          </div>
        </sc-for>
      </div>
    </div>

    <div style="padding:15px 20px;border-bottom:1px solid var(--rule)">
      <div class="grid" style="grid-template-columns:1fr 1fr;gap:14px">
        <div>
          <p class="eyebrow" style="margin-bottom:7px">Reads from</p>
          <sc-if value="{{d.hasStores}}" hint-placeholder-val="{{true}}">
            <div style="display:flex;gap:5px;flex-wrap:wrap">
              <sc-for list="{{d.stores}}" as="s" hint-placeholder-count="2">
                <span class="chip n" style="font-family:'JetBrains Mono',monospace">{{s.n}}</span>
              </sc-for>
            </div>
          </sc-if>
          <sc-if value="{{d.noStores}}" hint-placeholder-val="{{false}}">
            <p style="font-size:12px;color:var(--muted);margin:0">Nothing — it may not retrieve.</p>
          </sc-if>
        </div>
        <div>
          <p class="eyebrow" style="margin-bottom:7px">Model tier</p>
          <span class="chip {{d.tierCls}}">{{d.tier}}</span>
          <div style="font-size:11px;color:var(--muted);margin-top:4px">{{d.model}}</div>
        </div>
      </div>
    </div>

    <div style="padding:15px 20px;border-bottom:1px solid var(--rule)">
      <p class="eyebrow" style="margin-bottom:8px">Tools it may call · {{d.nTools}}</p>
      <div style="display:flex;gap:5px;flex-wrap:wrap">
        <sc-for list="{{d.tools}}" as="t" hint-placeholder-count="5">
          <code style="{{t.style}}">{{t.n}}</code>
        </sc-for>
      </div>
    </div>

    <div style="padding:15px 20px">
      <div class="box stop"><span class="lbl">What it may not do</span>
      <p>{{d.cannot}}</p></div>
      <div style="display:flex;justify-content:space-between;font-size:11.5px;color:var(--muted);
                  margin-top:12px">
        <span>eval suite <code style="font-size:11px">{{d.suite}}</code></span>
        <span>{{d.cases}} cases · {{d.negs}} negative</span>
      </div>
    </div>
  </div>
</div>
""",
    logic="""
const ACC='#0F5C63', NEG='#9E3626', POS='#3B6A38', MUT='#606D71', INK='#15191B', WARN='#8A5B12';
const T = { reason:'claude-opus-5', balanced:'claude-sonnet-5', cheap:'claude-haiku-4-5',
            none:'no model — deterministic' };
const A = {
 a0_supervisor:{name:'Supervisor',layer:0,tier:'cheap',model:'regex routing — no model',state:'idle',
  job:'Plans, routes, budgets and refuses. Never analyses anything itself.',
  stores:[],tools:['plan','budget','route','refuse'],suite:'a0_routing.yaml',cases:10,negs:5,
  now:'idle · last plan 14s ago\\n  intent    why_it_moved\\n  routed    5 agents, RM 0.99 estimated\\n  refused   1 of 4 questions (price target)',
  cannot:'It may not retrieve. If the supervisor read evidence itself, nobody could audit which store backed a claim — so retrieve() raises PermissionError by design.',
  trace:[['·','enforce plan','rail tool · allowed',0.2],['#','classify intent','why_it_moved',0.1],
         ['#','estimate cost','5 classes → RM 0.99',0.1],['!','refuse','point forecast, out of scope',0.3]]},
 a1_fundamentals:{name:'Fundamentals',layer:1,tier:'balanced',model:T.balanced,state:'idle',
  job:'Reads the statements as reported, at the time they were knowable.',
  stores:['kb_filings'],tools:['retrieve','get_statement','dupont','accrual_ratio','restatement_diff'],
  suite:'a1_fundamentals.yaml',cases:7,negs:2,
  now:'idle · waiting on a filings feed\\n  point-in-time store has 0 facts\\n  every query returns "not yet public as at <date>"',
  cannot:'It may not use a figure published after the as-at date. Every lookup goes through known_at, and a lookahead raises rather than quietly returning the later number.',
  trace:[['·','guard get_statement','rail tool · allowed',0.1],
         ['?','as_known_at','net_income @ 2026-08-25 → none',0.4],
         ['=','emit finding','unavailable — not yet public',0.1]]},
 a2_valuation:{name:'Valuation',layer:1,tier:'balanced',model:T.balanced,state:'idle',
  job:'Ranges and implied assumptions. Never a point target.',
  stores:['kb_method_valuation'],tools:['retrieve','multiple_vs_history','reverse_dcf','peer_multiples'],
  suite:'a2_valuation.yaml',cases:8,negs:3,
  now:'idle\\n  last: reverse DCF on MYX:1155\\n  implied 4.8% annual growth for 10y at a 9% discount',
  cannot:'It may not emit a price target, and may not apply a method its archetype forbids — no DCF on a bank, no trailing P/E on a cyclical.',
  trace:[['·','guard reverse_dcf','rail tool · allowed',0.1],
         ['#','bisect implied growth','60 iterations → 4.8%',1.2],
         ['=','emit finding','implied_assumption + caveat',0.1]]},
 a3_price_technical:{name:'Price & technical',layer:1,tier:'balanced',model:T.balanced,state:'running',
  job:'Describes what price did. Never predicts from a pattern.',
  stores:['kb_method_technical','price_bars'],tools:['retrieve','ohlcv','atr','drawdown','base_rate'],
  suite:'a3_price_technical.yaml',cases:7,negs:2,
  now:'running · ATR over 60 bars for MYX:1155\\n  20d ADV   RM 5,456k\\n  20d ATR   0.105\\n  drawdown  -12.4% from the 52w high',
  cannot:'It may not infer direction from a chart shape. A pattern is described and given its base rate, or it is not mentioned.',
  trace:[['·','guard ohlcv','rail tool · allowed',0.1],
         ['<','fetch bars','stooq · 2,847 held, 60 used',18.3],
         ['#','atr(20)','0.105',0.6],['#','drawdown','-12.4%',0.2],
         ['=','emit findings','3 findings, 1 caveat',0.1]]},
 a4_news_narrative:{name:'News & narrative',layer:1,tier:'cheap',model:T.cheap,state:'running',
  job:'Triages prose into five dimensions, dedups the wire, escalates only what touches you.',
  stores:['kb_news'],tools:['retrieve','search_news','extract_features','source_reliability','llm_complete'],
  suite:'a4_news_narrative.yaml',cases:8,negs:3,
  now:'running · 15-minute poll\\n  fetched    47 records\\n  duplicate  31 collapsed to one hash\\n  escalated  2 of 16 unique',
  cannot:'It may not call a model on volume. An article earns one only if relevance clears 0.34 AND its entity is one you hold or watch — both, never either.',
  trace:[['<','poll gdelt','47 records, 250 cap',412],
         ['#','near_duplicate_hash','31 wire copies collapsed',2.1],
         ['#','extract_features','16 unique · lexicon-v1',3.4],
         ['~','llm_complete','haiku · 2 escalated · RM 0.0010',890],
         ['=','emit findings','2 findings',0.1]]},
 a5_catalyst_events:{name:'Catalyst & events',layer:1,tier:'balanced',model:T.balanced,state:'idle',
  job:'Matches candidate causes to base rates on six factors. Mostly declines to name one.',
  stores:['kb_events','event_base_rates'],tools:['retrieve','events_in_window','base_rate','blackout_check'],
  suite:'a5_catalyst_events.yaml',cases:8,negs:3,
  now:'idle\\n  base-rate table is EMPTY — no cell has 30 observations\\n  every match reports BaseRate.thin until it fills',
  cannot:'It may not name a cause that scored below threshold. A rejected candidate is reported as rejected, never rendered under the move as though it explained it.',
  trace:[['·','guard base_rate','rail tool · allowed',0.1],
         ['?','events_in_window','2 candidates in ±3 sessions',0.3],
         ['#','score six factors','0.78 and 0.31',0.4],
         ['=','emit finding','1 matched, 1 below threshold',0.1]]},
 a6_macro_regime:{name:'Macro & regime',layer:1,tier:'balanced',model:T.balanced,state:'idle',
  job:'Labels the regime from series, and says when the label is unstable.',
  stores:['kb_macro'],tools:['series','regime_label','country_stress'],
  suite:'a6_macro_regime.yaml',cases:7,negs:2,
  now:'idle · waiting on a macro feed\\n  no series ingested\\n  regime_label returns "unavailable", not a guess',
  cannot:'It may not label a regime from fewer observations than the window needs. An unstable label is reported as unstable rather than rounded to the nearest story.',
  trace:[['·','guard regime_label','rail tool · allowed',0.1],
         ['?','series','no macro store wired → empty',0.1],
         ['=','emit finding','unavailable + caveat',0.1]]},
 a7_sector_technology:{name:'Sector & supply chain',layer:1,tier:'balanced',model:T.balanced,state:'idle',
  job:'Traverses the entity graph for read-across, with the path attached.',
  stores:['kb_sector','kb_supply_chain'],tools:['retrieve','traverse','peers','sector_primer'],
  suite:'a7_sector_technology.yaml',cases:7,negs:2,
  now:'idle\\n  graph holds 400 nodes / 2,400 edges (fixture)\\n  last traverse: Red Sea → shipping → 2 hops, weight 0.26',
  cannot:'It may not claim an impact without a path. No path, no claim — and the path plus its per-hop decay ships with every impact assertion.',
  trace:[['·','guard traverse','rail tool · allowed',0.1],
         ['?','traverse','2 hops, decay 0.26',3.1],
         ['=','emit finding','impact + path attached',0.1]]},
 a8_ownership_flow:{name:'Ownership & flow',layer:1,tier:'balanced',model:T.balanced,state:'idle',
  job:'Who else holds it, who is leaving, and how crowded the trade is.',
  stores:['kb_ownership'],tools:['insider_activity','ownership_change','short_interest_trend'],
  suite:'a8_ownership_flow.yaml',cases:7,negs:2,
  now:'idle · waiting on an ownership feed\\n  no substantial-shareholder data ingested',
  cannot:'It may not read intent into a transaction. An insider sale is reported as a sale with its context, never as a signal about the seller\\u2019s view.',
  trace:[['·','guard ownership_change','rail tool · allowed',0.1],
         ['?','insider_activity','no store wired → empty',0.1],
         ['=','emit finding','unavailable',0.1]]},
 a9_attribution:{name:'Attribution',layer:2,tier:'reason',model:'no model — deterministic',state:'running',
  job:'Decomposes a move into market, sector, style, currency and residual before any cause is named.',
  stores:['factor_returns','price_bars','event_base_rates'],
  tools:['decompose','abnormal_return','candidate_causes','long_horizon_decompose'],
  suite:'a9_attribution.yaml',cases:6,negs:3,
  now:'running · MYX:1155 over 1 session\\n  market   -8.80%\\n  sector   -1.00%\\n  residual +0.92%\\n  verdict  market_driven · 8% unexplained',
  cannot:'It may not report a verdict on a non-finite input. A NaN return once reached a decision as "nan% unexplained" — that path now raises before it can become an answer.',
  trace:[['·','guard decompose','rail tool · allowed',0.1],
         ['#','huber_fit','250 obs · r² 0.71',4.2],
         ['#','decompose','5 components + residual',0.3],
         ['#','corrado_rank_z','z = -0.41, not significant',0.2],
         ['=','emit findings','verdict market_driven',0.1]]},
 a10_thesis:{name:'Thesis',layer:2,tier:'reason',model:T.reason,state:'running',
  job:'Assembles findings into a stance, or declines to take one.',
  stores:[],tools:['compose','check_coverage','llm_complete'],
  suite:'a10_thesis.yaml',cases:7,negs:3,
  now:'running · MYX:1155\\n  evidence  4 findings across a1, a2, a5, a6\\n  gaps      none\\n  breakers  2 checkable\\n  stance    accumulate · confidence 0.51',
  cannot:'It may not retrieve, and it may not fill a gap. A missing evidence agent is reported as a gap and docks confidence — it is never quietly reasoned around.',
  trace:[['·','guard compose','rail tool · allowed',0.1],
         ['#','check_coverage','0 gaps of 4 required',0.1],
         ['~','llm_complete','opus · RM 0.0026',1240],
         ['=','verify claims','3 of 4 kept, 1 dropped',0.4]]},
 a11_red_team:{name:'Red team',layer:2,tier:'reason',model:T.reason,state:'running',
  job:'Attacks the thesis from six fixed angles plus whatever its structure invites.',
  stores:['kb_failures','kb_news','kb_filings'],
  tools:['retrieve','find_disconfirming','check_crowding','llm_complete'],
  suite:'a11_red_team.yaml',cases:7,negs:2,
  now:'running · attacking the MYX:1155 thesis\\n  structural  1 fatal (no valuation range)\\n  standing    5 material\\n  verdict     thesis_rejected',
  cannot:'It may not use the same sources the thesis used. Its evidence set excludes every source cited in support — otherwise it agrees with itself.',
  trace:[['·','guard find_disconfirming','rail tool · allowed',0.1],
         ['?','retrieve','kb_failures, sources excluded',2.2],
         ['#','structural_challenges','1 fatal, 1 minor',0.2],
         ['~','llm_complete','opus · RM 0.0026',1180],
         ['=','verdict','thesis_rejected',0.1]]},
 a12_portfolio_risk:{name:'Portfolio risk',layer:3,tier:'balanced',model:'no model — deterministic',state:'idle',
  job:'Concentration, heat, effective bets and every breach across the book.',
  stores:['kb_method_risk','holdings'],
  tools:['concentration_check','heat','effective_bets','drawdown_state','stress'],
  suite:'a12_portfolio_risk.yaml',cases:7,negs:2,
  now:'idle\\n  3 positions · HHI 0.103 · effective bets 3.00\\n  4 breaches open (3 single-name, 1 sector)',
  cannot:'It may not soften a limit. A breach is reported at every size, and effective bets below the floor is surfaced even when HHI looks healthy.',
  trace:[['·','guard concentration_check','rail tool · allowed',0.1],
         ['#','hhi','0.103',0.2],['#','effective_bets','3.00 — below the 5.0 floor',1.1],
         ['=','emit findings','1 summary + 4 breaches',0.1]]},
 a13_sizing:{name:'Sizing',layer:3,tier:'balanced',model:'no model — deterministic',state:'idle',
  job:'Turns a stance into a lot count, or into a documented refusal.',
  stores:['ledger','holdings','goals'],
  tools:['investable_capital','risk_budget_cap','kelly_cap','concentration_cap','liquidity_cap',
         'cost_floor','vol_target_scalar','lot_round'],
  suite:'a13_sizing.yaml',cases:8,negs:3,
  now:'idle\\n  last: MYX:1155 at RM 200k portfolio\\n  binding  risk budget · RM 15,500\\n  floor    RM 4,705.88 on XKLS',
  cannot:'It may not return a position below the market\\u2019s cost floor, and it may not accept a non-positive portfolio value — a negative one inverts the caps that bound it.',
  trace:[['·','guard risk_budget_cap','rail tool · allowed',0.1],
         ['#','five caps','risk binds at RM 15,500',0.4],
         ['#','cost_floor_value','bisect → RM 4,705.88',1.8],
         ['#','lot_round','2,500 units in lots of 100',0.1],
         ['=','emit finding','sized',0.1]]},
 a14_teacher:{name:'Teacher',layer:4,tier:'balanced',model:T.balanced,state:'idle',
  job:'Explains one concept at a time, in an order the graph enforces.',
  stores:['kb_craft'],tools:['retrieve','explain','next_concept','quiz'],
  suite:'a14_teacher.yaml',cases:7,negs:2,
  now:'idle\\n  learner knows 3 of 30 concepts\\n  next available: volatility\\n  kelly blocked behind 5 prerequisites',
  cannot:'It may not teach a concept whose prerequisites are unmet, and it may not record mastery of a concept that does not exist.',
  trace:[['·','guard explain','rail tool · allowed',0.1],
         ['#','prerequisites','5 missing for kelly',0.2],
         ['=','emit finding','prerequisite — refused',0.1]]},
 a15_reflection:{name:'Reflection',layer:4,tier:'reason',model:T.reason,state:'idle',
  job:'Reviews graded outcomes and mostly declines to write a lesson.',
  stores:['kb_lessons','outcomes'],
  tools:['grade_queue','propose_lesson','calibrate','curate','llm_complete'],
  suite:'a15_reflection.yaml',cases:7,negs:4,
  now:'idle\\n  3 due for grading, 8 pending\\n  0 lessons written this quarter\\n  Brier 0.241 on 6 graded',
  cannot:'It may not grade before the horizon, and may not write a lesson without 5 instances across 3 distinct instruments at a 60% hit rate. If instruments are unknown, it refuses rather than passing.',
  trace:[['·','guard grade_queue','rail tool · allowed',0.1],
         ['!','grade','refused — 21 days early',0.2],
         ['#','propose_lesson','no_lesson · 1 instrument of 3',0.3],
         ['#','calibrate','Brier 0.241 · n=6',0.2]]}
};
const LAYERS = [
 ['Orchestration','plans, never analyses'],['Evidence','one store each, no cross-talk'],
 ['Synthesis','assembles and attacks'],['Portfolio','bounds what may be held'],
 ['Learning','the loop that mostly declines']];
const SYM = {'·':MUT,'#':INK,'?':ACC,'<':ACC,'~':ACC,'=':POS,'!':NEG};
class Component extends DCLogic {
  constructor(p){ super(p); this.state = { sel:'a9_attribution' }; }
  renderVals(){
    const selId = this.state.sel, a = A[selId];
    const tierCls = t => t==='reason' ? 'no' : (t==='cheap' ? 'n' : 'wait');
    const pulse = st => `width:7px;height:7px;border-radius:50%;flex:none;background:${
      st==='running' ? POS : '#C2C9C6'}`;
    return {
      layers: LAYERS.map(([name,note],i) => ({ name, note,
        agents: Object.keys(A).filter(k => A[k].layer===i).map(k => {
          const x = A[k], on = k===selId;
          return { short:k.split('_')[0], name:x.name, status:x.state==='running'?'running':'idle',
            tier:x.tier, tierCls:tierCls(x.tier), pulse:pulse(x.state),
            idColor: on ? ACC : (x.state==='running' ? INK : MUT),
            nameColor: on ? INK : (x.state==='running' ? INK : MUT),
            style:`padding:10px 11px;border-radius:3px;cursor:pointer;
              border:1px solid ${on?ACC:'#D6DBD9'};
              background:${on?'#DCEAEA':'#FFFFFF'};
              box-shadow:${on?'none':'0 1px 2px rgba(21,25,27,.05)'}`,
            pick: () => this.setState({ sel:k }) };
        }) })),
      d: { id:selId, name:a.name, job:a.job, now:a.now, cannot:a.cannot,
        state:a.state, stateCls: a.state==='running' ? 'ok' : 'n',
        pulse: pulse(a.state), tier:a.tier, tierCls:tierCls(a.tier), model:a.model,
        suite:a.suite, cases:String(a.cases), negs:String(a.negs),
        hasStores:a.stores.length>0, noStores:a.stores.length===0,
        stores:a.stores.map(n=>({n})), nTools:String(a.tools.length),
        tools:a.tools.map(n=>({ n, style:`font-size:11px;padding:2px 7px;border-radius:3px;
          background:${n==='llm_complete'?'#DCEAEA':'#EFF2F1'};
          color:${n==='llm_complete'?ACC:'#3A4448'};
          font-weight:${n==='llm_complete'?'700':'400'}` })),
        trace:a.trace.map(([mark,what,detail,ms]) => ({ mark, what, detail,
          hasDetail: !!detail, ms: ms+'ms',
          color: mark==='!' ? NEG : INK,
          sym:`width:13px;flex:none;text-align:center;font-family:'JetBrains Mono',monospace;
            font-weight:700;font-size:12px;color:${SYM[mark]}` })) }
    };
  }
}
""",
    w=1440,
    h=1180,
)
