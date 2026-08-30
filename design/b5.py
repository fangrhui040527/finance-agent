exec(open("gen.py").read())

# ─────────────────────────────── TRACE
page("Trace","trace", """
<div class="head">
  <div><h1>Trace</h1>
  <p>Every prompt, every guardrail decision, every dropped claim. Click a row to open it.</p></div>
  <div style="display:flex;gap:8px;align-items:center">
    <code style="font-size:12px;color:var(--muted)">20260828T152037-ad06ff</code>
    <span class="chip ok">0 errors</span>
  </div>
</div>

<div class="grid" style="grid-template-columns:repeat(5,1fr);margin-bottom:18px">
  <div class="card stat"><b>105</b><span>events</span></div>
  <div class="card stat"><b>16<span style="font-size:13px;color:var(--muted)"> / 16</span></b><span>agents fired</span></div>
  <div class="card stat"><b>4</b><span>model calls</span></div>
  <div class="card stat"><b style="color:var(--warn)">8</b><span>refusals</span></div>
  <div class="card stat"><b>111<span style="font-size:13px;color:var(--muted)">ms</span></b><span>wall clock</span></div>
</div>

<div class="grid" style="grid-template-columns:minmax(0,1fr) 300px;align-items:start">
  <div class="card" style="padding:0;overflow:hidden">
    <div style="padding:13px 18px;border-bottom:1px solid var(--rule);display:flex;gap:7px;flex-wrap:wrap">
      <sc-for list="{{filters}}" as="f" hint-placeholder-count="5">
        <button class="btn" onClick="{{ f.pick }}" style="{{f.style}}">{{f.label}}</button>
      </sc-for>
    </div>
    <div>
      <sc-for list="{{events}}" as="e" hint-placeholder-count="9">
        <div>
          <div onClick="{{ e.toggle }}" style="{{e.rowStyle}}">
            <span class="num" style="width:34px;color:var(--muted);font-size:11px;flex:none">{{e.seq}}</span>
            <span style="{{e.symStyle}}">{{e.sym}}</span>
            <div style="width:{{e.indent}}px;flex:none"></div>
            <code style="font-size:12.5px;font-weight:600;color:{{e.nameColor}}">{{e.name}}</code>
            <span class="chip {{e.cls}}" style="flex:none">{{e.kind}}</span>
            <span style="flex-grow:1"></span>
            <span class="num" style="font-size:11px;color:var(--muted);flex:none">{{e.meta}}</span>
          </div>
          <sc-if value="{{e.open}}" hint-placeholder-val="{{false}}">
            <div style="padding:0 18px 15px 60px;background:var(--panel-2)">
              <div class="term" style="margin-top:2px">
                <div class="term-bar"><i></i><i></i><i></i><b>{{e.blobLabel}}</b></div>
                <pre>{{e.blob}}</pre>
              </div>
            </div>
          </sc-if>
        </div>
      </sc-for>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <p class="eyebrow">Organs · what fired</p>
      <table><tbody>
        <tr><td><code style="font-size:11.5px;color:var(--accent)">a10_thesis</code></td>
            <td class="num" style="text-align:right;font-size:12px">1 call</td></tr>
        <tr><td><code style="font-size:11.5px;color:var(--accent)">a11_red_team</code></td>
            <td class="num" style="text-align:right;font-size:12px">1 call</td></tr>
        <tr><td><code style="font-size:11.5px;color:var(--accent)">a15_reflection</code></td>
            <td class="num" style="text-align:right;font-size:12px">1 call</td></tr>
        <tr><td><code style="font-size:11.5px;color:var(--accent)">a4_news_narrative</code></td>
            <td class="num" style="text-align:right;font-size:12px">1 call</td></tr>
        <tr><td><code style="font-size:11.5px;color:var(--muted)">12 others</code></td>
            <td class="num" style="text-align:right;font-size:12px;color:var(--muted)">no model</td></tr>
      </tbody></table>
      <p style="font-size:11.5px;color:var(--muted);margin:11px 0 0">
        Only four agents may call a model at all. The allowlist stops being a control the
        moment it names everything.</p>
    </div>

    <div class="box warn"><span class="lbl">Traces hold real prompts</span>
    <p><code style="font-size:11.5px">debug/</code> is gitignored. A trace carries verbatim
       prompts, model output and whatever positions were passed in. The permanent ledger
       stores a hash instead, for exactly this reason.</p></div>

    <div class="card" style="padding:15px 18px">
      <p class="eyebrow" style="margin-bottom:9px">Spend this run</p>
      <div class="num" style="font-size:21px;font-weight:600">RM 0.0082</div>
      <p style="font-size:11.5px;color:var(--muted);margin:4px 0 0">97 in / 80 out · windowed
        against a RM 25.00 daily ceiling</p>
    </div>
  </div>
</div>
""", logic="""
const ACC='#0F5C63', NEG='#9E3626', MUT='#606D71', INK='#15191B';
const EV = [
  {seq:31, kind:'allowed', name:'llm_complete', sym:'·', d:1, meta:'tool_allowlist',
   blobLabel:'rail decision', blob:'rail       tool\\nrule       default_allow\\nagent      a4_news_narrative\\ndecision   allow'},
  {seq:32, kind:'llm_call', name:'a4_news_narrative', sym:'~', d:1, meta:'haiku-4-5 · 0.01ms',
   blobLabel:'prompt → response',
   blob:'model      claude-haiku-4-5   tier cheap\\ntokens     25 in / 20 out    cost RM 0.000519\\n\\n--- prompt ---\\n[news_triage] demonstrate the inference seam for\\na4_news_narrative and record exactly what crossed it\\n\\n--- response ---\\n[claude-haiku-4-5] [news_triage] demonstrate the\\ninference seam for a4_news_narrative and record ex'},
  {seq:34, kind:'llm_call', name:'a10_thesis', sym:'~', d:1, meta:'opus-5 · 0.01ms',
   blobLabel:'prompt → response',
   blob:'model      claude-opus-5      tier reason\\ntokens     24 in / 20 out    cost RM 0.002573\\n\\n--- prompt ---\\n[thesis_synthesis] demonstrate the inference seam for\\na10_thesis and record exactly what crossed it'},
  {seq:39, kind:'denied', name:'llm_complete', sym:'!', d:1, meta:'tool_allowlist',
   blobLabel:'refusal',
   blob:'rail       tool\\nrule       tool_allowlist\\nagent      a0_supervisor\\n\\nreason     agent \\'a0_supervisor\\' may not call \\'llm_complete\\'\\n\\nRouting here is deterministic by design. A trace showing\\nA0 reasoning would mean that changed.'},
  {seq:43, kind:'agent', name:'a0_supervisor', sym:'@', d:0, meta:'plan · 3 questions',
   blobLabel:'agent span',
   blob:'agent      a0_supervisor\\nmethod     plan\\ntier       intent_routing\\n\\n3 questions planned, 1 refused out of scope.'},
  {seq:57, kind:'engine', name:'attribution.market-wide', sym:'#', d:1, meta:'market_driven',
   blobLabel:'deterministic result',
   blob:'move            -9.00%\\nmarket          -8.80%\\nsector          -1.00%\\nidiosyncratic   +0.92%\\nunexplained      8%\\n\\nverdict         market_driven'},
  {seq:71, kind:'verification', name:'a10_thesis', sym:'=', d:1, meta:'1 of 4 dropped',
   blobLabel:'citation check',
   blob:'proposed   4 claims\\nkept       3\\ndropped    1\\n\\nDROPPED: no citation verified against its chunk\\n  :: "margins are expected to recover through H2"\\n\\nThe quoted span was not found verbatim in the cited\\nchunk. The claim is dropped individually, not hedged.'},
  {seq:88, kind:'refusal', name:'grade_too_early', sym:'!', d:1, meta:'a15_reflection',
   blobLabel:'refusal',
   blob:'demo-1 grades on 2026-09-18; grading it on 2026-08-28\\nwould score noise and flatter the model.'},
  {seq:101, kind:'denied', name:'place_order', sym:'!', d:1, meta:'no_execution',
   blobLabel:'refusal',
   blob:'rail       tool\\nrule       no_execution\\n\\nreason     the system is a one-way door; it has no\\n           execution capability\\n\\nNo tool that places an order exists anywhere in the\\nrepository. A grep fails the build if one is added.'}
];
const CLS = { llm_call:'ok', denied:'no', refusal:'no', verification:'wait',
              allowed:'n', agent:'n', engine:'n' };
class Component extends DCLogic {
  constructor(p){ super(p); this.state = { open:{31:false}, filter:'all' }; }
  renderVals(){
    const f = this.state.filter;
    const shown = EV.filter(e => f==='all'
      || (f==='model' && e.kind==='llm_call')
      || (f==='refusals' && (e.kind==='denied'||e.kind==='refusal'))
      || (f==='claims' && e.kind==='verification')
      || (f==='engine' && (e.kind==='engine'||e.kind==='agent')));
    return {
      filters: [['all','All 105'],['model','Model calls'],['refusals','Refusals'],
                ['claims','Dropped claims'],['engine','Engines']].map(([k,label]) => ({
        label, pick: () => this.setState({ filter:k }),
        style:`font-size:12px;padding:4px 11px;${f===k
          ? 'background:#0F5C63;border-color:#0F5C63;color:#fff;font-weight:600'
          : 'font-weight:500'}` })),
      events: shown.map(e => {
        const open = !!this.state.open[e.seq];
        return { ...e, open, cls: CLS[e.kind]||'n', indent: e.d*16,
          nameColor: e.kind==='denied'||e.kind==='refusal' ? NEG
                   : e.kind==='llm_call' ? ACC : INK,
          symStyle:`width:14px;flex:none;text-align:center;font-family:"JetBrains Mono",monospace;
            font-weight:700;color:${e.kind==='denied'||e.kind==='refusal' ? NEG
              : e.kind==='llm_call' ? ACC : MUT}`,
          rowStyle:`display:flex;align-items:center;gap:9px;padding:9px 18px;cursor:pointer;
            border-bottom:1px solid #D6DBD9;background:${open?'#EFF2F1':'#FFFFFF'}`,
          toggle: () => this.setState({ open: {...this.state.open, [e.seq]: !open} }) };
      })
    };
  }
}
""")
