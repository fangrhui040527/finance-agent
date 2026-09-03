from gen import page  # run via `python -m design.build` or from design/

page(
    "Sizing",
    "size",
    """
<div class="head">
  <div><h1>Sizing</h1>
  <p>Five caps run on every position. The smallest one binds — and below the market&rsquo;s cost
     floor there is no position to take at any edge.</p></div>
  <span class="chip n">XKLS · Bursa Malaysia</span>
</div>

<div class="grid" style="grid-template-columns:296px minmax(0,1fr);align-items:start">

  <div class="card">
    <p class="eyebrow">Inputs</p>
    <div style="display:flex;flex-direction:column;gap:17px">
      <div>
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px">
          <span style="font-size:12.5px;color:var(--ink-2);font-weight:600">Portfolio value</span>
          <span class="num" style="font-size:14px;font-weight:600">{{f.pv}}</span>
        </div>
        <input type="range" min="2000" max="400000" step="1000" value="{{f.pvRaw}}"
          onInput="{{ setPv }}" style="width:100%;accent-color:#0F5C63;cursor:pointer">
        <div style="display:flex;justify-content:space-between;font-size:10.5px;color:var(--muted)">
          <span>RM 2k</span><span>RM 400k</span></div>
      </div>

      <div>
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px">
          <span style="font-size:12.5px;color:var(--ink-2);font-weight:600">Stop distance</span>
          <span class="num" style="font-size:14px;font-weight:600">{{f.stopPct}}</span>
        </div>
        <input type="range" min="3" max="25" step="0.5" value="{{f.stopRaw}}"
          onInput="{{ setStop }}" style="width:100%;accent-color:#0F5C63;cursor:pointer">
        <div style="font-size:11px;color:var(--muted)">entry {{f.price}} · stop {{f.stop}}</div>
      </div>

      <div>
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px">
          <span style="font-size:12.5px;color:var(--ink-2);font-weight:600">20-day ADV</span>
          <span class="num" style="font-size:14px;font-weight:600">{{f.adv}}</span>
        </div>
        <input type="range" min="50000" max="3000000" step="50000" value="{{f.advRaw}}"
          onInput="{{ setAdv }}" style="width:100%;accent-color:#0F5C63;cursor:pointer">
      </div>

      <div style="border-top:1px solid var(--rule);padding-top:14px">
        <div style="display:flex;justify-content:space-between;font-size:12.5px;padding:3px 0">
          <span style="color:var(--muted)">Risk per trade</span><span class="num">0.75%</span></div>
        <div style="display:flex;justify-content:space-between;font-size:12.5px;padding:3px 0">
          <span style="color:var(--muted)">Single-name cap</span><span class="num">8.0%</span></div>
        <div style="display:flex;justify-content:space-between;font-size:12.5px;padding:3px 0">
          <span style="color:var(--muted)">Max participation</span><span class="num">5.0%</span></div>
        <div style="display:flex;justify-content:space-between;font-size:12.5px;padding:3px 0">
          <span style="color:var(--muted)">Board lot</span><span class="num">100</span></div>
      </div>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <p class="eyebrow">The five caps · smallest binds</p>
      <sc-for list="{{caps}}" as="c" hint-placeholder-count="5">
        <div style="margin-bottom:13px">
          <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px">
            <span style="font-size:12.5px;color:{{c.labelColor}};font-weight:{{c.weight}}">{{c.name}}</span>
            <span class="num" style="font-size:13px;color:{{c.labelColor}};font-weight:{{c.weight}}">{{c.val}}</span>
          </div>
          <div style="height:9px;background:var(--panel-2);border-radius:2px;overflow:hidden">
            <div style="{{c.style}}"></div>
          </div>
        </div>
      </sc-for>

      <div style="margin-top:16px;padding-top:14px;border-top:1px dashed var(--neg)">
        <div style="display:flex;justify-content:space-between;align-items:baseline">
          <span style="font-size:12.5px;color:var(--neg);font-weight:700">Cost floor · minimum economic position</span>
          <span class="num" style="font-size:13px;color:var(--neg);font-weight:700">{{floor}}</span>
        </div>
        <p style="font-size:11.5px;color:var(--muted);margin:5px 0 0">
          60 bps round trip on Bursa. Below this the spread and the RM 8 brokerage minimum
          eat more than any realistic edge returns.</p>
      </div>
    </div>

    <div class="card" style="border-left:3px solid {{res.color}};background:{{res.bg}}">
      <div style="display:flex;align-items:baseline;gap:12px;margin-bottom:7px">
        <code style="font-size:14px;font-weight:700;color:{{res.color}}">{{res.verdict}}</code>
        <span class="chip n">bound by {{res.binding}}</span>
      </div>
      <div class="num" style="font-size:29px;font-weight:600;color:var(--ink);letter-spacing:-.02em;
                              line-height:1.15;margin-bottom:6px">{{res.headline}}</div>
      <p style="margin:0;font-size:13.5px;color:var(--ink-2);line-height:1.6">{{res.why}}</p>
    </div>

    <div class="grid" style="grid-template-columns:1fr 1fr">
      <div class="card" style="padding:15px 18px">
        <p class="eyebrow" style="margin-bottom:9px">Round-trip cost at this size</p>
        <div class="num" style="font-size:21px;font-weight:600;color:{{res.bpsColor}}">{{res.bps}}</div>
        <p style="font-size:11.5px;color:var(--muted);margin:4px 0 0">
          brokerage 0.10% (min RM 8) · clearing 0.03% (cap RM 1,000) · stamp 0.10% (cap RM 1,000)</p>
      </div>
      <div class="card" style="padding:15px 18px">
        <p class="eyebrow" style="margin-bottom:9px">If the stop fills</p>
        <div class="num" style="font-size:21px;font-weight:600">{{res.atRisk}}</div>
        <p style="font-size:11.5px;color:var(--muted);margin:4px 0 0">
          {{res.atRiskPct}} of the book — inside the 0.75% per-trade budget</p>
      </div>
    </div>
  </div>
</div>
""",
    logic="""
const ACC='#0F5C63', NEG='#9E3626', MUT='#606D71', INK='#15191B';
const fmt = n => n.toLocaleString('en-MY',{minimumFractionDigits:2,maximumFractionDigits:2});
function bursaOneSide(v){
  return Math.max(v*0.001, 8) + Math.min(v*0.0003, 1000) + Math.min(v*0.001, 1000);
}
class Component extends DCLogic {
  constructor(p){ super(p); this.state = { pv:200000, stop:9.7, adv:900000 }; }
  renderVals(){
    const { pv, stop, adv } = this.state;
    const price = 6.20, lot = 100;
    const stopFrac = stop/100, stopPrice = price*(1-stopFrac);
    const risk = pv*0.0075/stopFrac, conc = pv*0.08, liq = adv*0.05;
    const FLOOR = 4705.88;
    const caps = [
      {name:'Risk budget', v:risk}, {name:'Concentration', v:conc},
      {name:'Liquidity (5% of ADV)', v:liq},
      {name:'Kelly', v:null}, {name:'Volatility target', v:pv*0.11}
    ];
    const live = caps.filter(c=>c.v!==null);
    const binding = live.reduce((a,b)=> b.v<a.v?b:a);
    const scale = Math.max(...live.map(c=>c.v), FLOOR);
    const value = binding.v;
    const units = Math.floor(Math.floor(value/price)/lot)*lot;
    const notional = units*price;
    const ok = units>=lot && notional>=FLOOR;
    const bps = notional>0 ? (bursaOneSide(notional)*2/notional*10000) : 0;

    let res;
    if (units < lot) {
      res = { verdict:'NO POSITION', color:NEG, bg:'#F3E0DC', binding:binding.name.toLowerCase(),
        headline:'Not one lot', bps:'—', bpsColor:MUT, atRisk:'RM 0.00', atRiskPct:'0%',
        why:'The binding cap does not fund a single 100-share lot at RM 6.20. There is no smaller position — a lot is the unit.' };
    } else if (notional < FLOOR) {
      res = { verdict:'NO POSITION', color:NEG, bg:'#F3E0DC', binding:binding.name.toLowerCase(),
        headline:'RM '+fmt(notional)+' · below the floor',
        bps:bps.toFixed(0)+' bps', bpsColor:NEG,
        atRisk:'RM '+fmt(notional*stopFrac), atRiskPct:(notional*stopFrac/pv*100).toFixed(2)+'%',
        why:'This size can be funded, but the round trip costs more than the floor allows. A position that cannot pay its own spread is not a smaller position — it is not a position.' };
    } else {
      res = { verdict:'SIZED', color:ACC, bg:'#DCEAEA', binding:binding.name.toLowerCase(),
        headline:units.toLocaleString()+' units · RM '+fmt(notional),
        bps:bps.toFixed(0)+' bps', bpsColor: bps>60?NEG:ACC,
        atRisk:'RM '+fmt(notional*stopFrac), atRiskPct:(notional*stopFrac/pv*100).toFixed(2)+'%',
        why:'Bound by the '+binding.name.toLowerCase()+' cap, rounded down to whole board lots. Every other cap is looser at this portfolio size.' };
    }

    return {
      f: { pv:'RM '+pv.toLocaleString(), pvRaw:pv, stopPct:stop.toFixed(1)+'%', stopRaw:stop,
           price:'RM 6.20', stop:'RM '+stopPrice.toFixed(2),
           adv:'RM '+(adv/1000).toFixed(0)+'k', advRaw:adv },
      caps: caps.map(c => {
        const isB = c.v !== null && c.v === value;
        return { name:c.name,
          val: c.v===null ? 'not set' : 'RM '+fmt(c.v),
          labelColor: c.v===null ? MUT : (isB ? ACC : INK),
          weight: isB ? '700' : '500',
          style: c.v===null
            ? 'width:0%;height:100%'
            : `width:${Math.min(c.v/scale*100,100)}%;height:100%;background:${isB?ACC:'#C2C9C6'};border-radius:2px` };
      }),
      floor:'RM '+fmt(FLOOR),
      res,
      setPv: e => this.setState({ pv: Number(e.target.value) }),
      setStop: e => this.setState({ stop: Number(e.target.value) }),
      setAdv: e => this.setState({ adv: Number(e.target.value) })
    };
  }
}
""",
)
