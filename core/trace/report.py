"""Turn a trace into something a person can read.

Three outputs, because three different questions get asked of a trace:

  session.log   what happened, in order, indented by call depth
                -> "walk me through the run"
  anatomy.md    what exists and what called what, aggregated
                -> "what are the moving parts and which ones fired"
  report.html   the same, navigable, with prompts inline
                -> "let me dig"

All three are written from trace.jsonl, so a run that crashed still produces
them - the jsonl is flushed per event precisely so this works on the runs worth
reporting on.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

BANNER = (
    "CONTAINS VERBATIM PROMPTS AND RESPONSES. This is a debugging artefact, not "
    "an audit record - it may hold portfolio positions, instrument ids and model "
    "output. debug/ is gitignored. Read before sharing."
)

#: Rendering hints per event kind: symbol, and which data keys to summarise.
KINDS = {
    "run_start":    ("*", []),
    "run_end":      ("*", []),
    "span":         ("+", []),
    "span_end":     ("",  []),
    "agent":        ("@", ["agent", "method"]),
    "llm_call":     ("~", ["model_id", "tier", "input_tokens", "output_tokens",
                           "cost_myr", "latency_ms"]),
    "retrieval":    ("?", ["corpus", "n_hits", "grade", "relevance"]),
    "verification": ("=", ["proposed", "kept", "answered", "confidence"]),
    "allowed":      (".", ["rail", "rule"]),
    "denied":       ("!", ["rail", "rule", "reason"]),
    "refusal":      ("!", ["reason"]),
    "error":        ("X", ["error"]),
    "engine":       ("#", []),
    "feed":         ("<", ["source", "count"]),
}


def load(run_dir: Path) -> list[dict]:
    path = Path(run_dir) / "trace.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"no trace.jsonl in {run_dir}")
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue          # a torn final line from a hard crash
    return out


def _val(v) -> str:
    if isinstance(v, dict) and "_blob" in v:
        return f"[{v['chars']} chars -> {v['_blob']}]"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


# -- session.log --------------------------------------------------------------

def session_log(events: list[dict]) -> str:
    lines = [BANNER, "=" * 100, ""]
    for e in events:
        if e["kind"] == "span_end":
            continue
        sym, keys = KINDS.get(e["kind"], ("-", []))
        indent = "  " * e["depth"]
        bits = [f"{k}={_val(e['data'][k])}" for k in keys if k in e["data"]]
        extra = f"   {' '.join(bits)}" if bits else ""
        dur = f"  ({e['duration_ms']:.1f}ms)" if e.get("duration_ms") else ""
        lines.append(f"{e['seq']:>5} {e['at'][11:23]} {indent}{sym} "
                     f"{e['kind']:<13} {e['name']}{extra}{dur}")
        if e["kind"] == "llm_call":
            for field in ("system", "prompt", "response"):
                v = e["data"].get(field)
                if not v:
                    continue
                lines.append(f"{'':>5} {'':>12} {indent}    {field}: {_val(v)}")
                if isinstance(v, str):
                    for ln in v.splitlines()[:12]:
                        lines.append(f"{'':>5} {'':>12} {indent}      | {ln}")
        if e["kind"] == "verification" and e["data"].get("dropped"):
            for d in e["data"]["dropped"]:
                lines.append(f"{'':>5} {'':>12} {indent}    DROPPED: "
                             f"{d.get('why')} :: {str(d.get('text'))[:90]}")
        if e["kind"] in ("denied", "error"):
            lines.append(f"{'':>5} {'':>12} {indent}    -> "
                         f"{e['data'].get('reason') or e['data'].get('error')}")
    return "\n".join(lines) + "\n"


# -- anatomy.md ---------------------------------------------------------------

def anatomy(events: list[dict], summary: dict) -> str:
    agents: dict[str, dict] = {}
    for e in events:
        a = e["data"].get("agent")
        if not a:
            continue
        rec = agents.setdefault(a, {"calls": 0, "llm": 0, "retrievals": 0,
                                    "denied": 0, "cost": 0.0, "tools": set(),
                                    "ms": 0.0})
        if e["kind"] == "agent":
            rec["calls"] += 1
        elif e["kind"] == "llm_call":
            rec["llm"] += 1
            rec["cost"] += float(e["data"].get("cost_myr", 0) or 0)
        elif e["kind"] == "retrieval":
            rec["retrievals"] += 1
        elif e["kind"] == "denied":
            rec["denied"] += 1
        if e["kind"] in ("allowed", "denied"):
            rec["tools"].add(e["data"].get("action", "?"))
        if e.get("duration_ms"):
            rec["ms"] += e["duration_ms"]

    edges: dict[tuple[str, str], int] = {}
    stack: list[str] = []
    for e in events:
        if e["kind"] == "agent":
            if stack:
                edges[(stack[-1], e["data"]["agent"])] = \
                    edges.get((stack[-1], e["data"]["agent"]), 0) + 1
            stack.append(e["data"]["agent"])
        elif e["kind"] == "span_end" and stack:
            stack.pop()

    L = [f"# Anatomy of run `{summary.get('run_id', '?')}`", "",
         f"> {BANNER}", "",
         f"**{summary.get('label')}** · {summary.get('events')} events · "
         f"{summary.get('wall_ms', 0):.0f} ms wall · "
         f"{summary['llm']['calls']} model calls · "
         f"RM {summary['llm']['cost_myr']:.4f}", ""]

    L += ["## Organs — what fired", "",
          "| Agent | Invocations | Model calls | Retrievals | Tools used | Denied | Cost MYR | ms |",
          "|---|--:|--:|--:|--:|--:|--:|--:|"]
    for a, r in sorted(agents.items()):
        L.append(f"| `{a}` | {r['calls']} | {r['llm']} | {r['retrievals']} | "
                 f"{len(r['tools'])} | {r['denied']} | {r['cost']:.4f} | {r['ms']:.0f} |")
    if not agents:
        L.append("| *(none)* | | | | | | | |")

    L += ["", "## Circulation — what handed work to what", ""]
    if edges:
        L.append("```mermaid")
        L.append("graph LR")
        for (src, dst), n in sorted(edges.items()):
            L.append(f"  {src}-->|{n}|{dst}")
        L.append("```")
    else:
        L.append("*No nested agent calls in this run — every agent was invoked "
                 "at the top level.*")

    counts = summary.get("by_kind", {})
    L += ["", "## Skeleton — event vocabulary", "",
          "| Kind | Count | Means |", "|---|--:|---|"]
    meaning = {
        "llm_call": "a model was called; full prompt and response in prompts/",
        "allowed": "a guardrail permitted an action",
        "denied": "a guardrail refused an action",
        "retrieval": "a knowledge store was queried",
        "verification": "claims checked against their cited chunks",
        "agent": "an agent method ran",
        "error": "an exception escaped a span",
        "span": "a named stage began",
        "span_end": "a named stage finished",
        "run_start": "the run began", "run_end": "the run finished",
        "feed": "an external source was polled",
        "engine": "a deterministic engine computed something",
    }
    for k, n in sorted(counts.items()):
        L.append(f"| `{k}` | {n} | {meaning.get(k, '')} |")

    if summary.get("errors"):
        L += ["", "## Pathology — what went wrong", ""]
        for err in summary["errors"]:
            L.append(f"- **{err['name']}** — {err['error']}")

    L += ["", "## Slowest", "", "| Stage | Kind | ms |", "|---|---|--:|"]
    for s in summary.get("slowest", [])[:10]:
        L.append(f"| {s['name']} | {s['kind']} | {s['ms']:.1f} |")

    L += ["", "---", "",
          "`trace.jsonl` holds every event, one JSON object per line.",
          "`prompts/` holds every value too long to inline — prompts, responses, ",
          "and any other large string, referenced from the trace by filename.", ""]
    return "\n".join(L)


# -- report.html --------------------------------------------------------------

def report_html(events: list[dict], summary: dict) -> str:
    rows = []
    for e in events:
        if e["kind"] == "span_end":
            continue
        sym, keys = KINDS.get(e["kind"], ("-", []))
        pad = e["depth"] * 22
        bits = " ".join(f"<span class=k>{html.escape(k)}</span>="
                        f"<span class=v>{html.escape(_val(e['data'][k]))}</span>"
                        for k in keys if k in e["data"])
        detail = ""
        if e["kind"] == "llm_call":
            parts = []
            for f in ("system", "prompt", "response"):
                v = e["data"].get(f)
                if not v:
                    continue
                body = v["head"] + f"\n\n... full text in {v['_blob']}" \
                    if isinstance(v, dict) else str(v)
                parts.append(f"<h4>{f}</h4><pre>{html.escape(body)}</pre>")
            detail = f"<details><summary>prompt / response</summary>{''.join(parts)}</details>"
        elif e["kind"] == "verification" and e["data"].get("dropped"):
            items = "".join(
                f"<li><code>{html.escape(str(d.get('why')))}</code> — "
                f"{html.escape(str(d.get('text'))[:200])}</li>"
                for d in e["data"]["dropped"])
            detail = f"<details><summary>dropped claims</summary><ul>{items}</ul></details>"
        elif e["kind"] in ("denied", "error"):
            detail = (f"<div class=bad>"
                      f"{html.escape(str(e['data'].get('reason') or e['data'].get('error')))}"
                      f"</div>")
        dur = f"{e['duration_ms']:.1f}ms" if e.get("duration_ms") else ""
        rows.append(
            f"<tr class='r {html.escape(e['kind'])}'>"
            f"<td class=seq>{e['seq']}</td>"
            f"<td class=t>{html.escape(e['at'][11:23])}</td>"
            f"<td><span style='padding-left:{pad}px'>{sym} "
            f"<b>{html.escape(e['name'])}</b></span> "
            f"<span class=kind>{html.escape(e['kind'])}</span> {bits}{detail}</td>"
            f"<td class=dur>{dur}</td></tr>")

    llm = summary.get("llm", {})
    return f"""<title>Trace {html.escape(str(summary.get('run_id')))}</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#e5e5e5;--bad:#b00020;--acc:#0b5cad}}
:root:not([data-theme=light]){{@media (prefers-color-scheme:dark){{
--bg:#111;--fg:#e8e8e8;--mut:#999;--line:#2a2a2a;--bad:#ff6b6b;--acc:#6fb2ff}}}}
:root[data-theme=dark]{{--bg:#111;--fg:#e8e8e8;--mut:#999;--line:#2a2a2a;--bad:#ff6b6b;--acc:#6fb2ff}}
body{{background:var(--bg);color:var(--fg);font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;margin:0;padding:1.5rem}}
h1{{font-size:1.2rem;margin:0 0 .3rem}}
.banner{{border-left:3px solid var(--bad);padding:.6rem .8rem;margin:1rem 0;color:var(--mut)}}
.stats{{display:flex;flex-wrap:wrap;gap:1.5rem;margin:1rem 0;padding:.8rem 0;border-block:1px solid var(--line)}}
.stat b{{display:block;font-size:1.3rem;color:var(--acc)}}
.stat span{{color:var(--mut);font-size:.85rem}}
.wrap{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;min-width:640px}}
td{{border-bottom:1px solid var(--line);padding:.35rem .5rem;vertical-align:top}}
.seq,.t,.dur{{color:var(--mut);white-space:nowrap;font-size:.85rem}}
.kind{{color:var(--mut);font-size:.8rem;border:1px solid var(--line);padding:0 .3rem;border-radius:3px}}
.k{{color:var(--mut)}} .v{{color:var(--acc)}}
.bad{{color:var(--bad);margin-top:.3rem}}
tr.denied,tr.error{{background:color-mix(in srgb,var(--bad) 8%,transparent)}}
tr.llm_call{{background:color-mix(in srgb,var(--acc) 6%,transparent)}}
pre{{white-space:pre-wrap;word-break:break-word;background:color-mix(in srgb,var(--fg) 5%,transparent);padding:.6rem;overflow-x:auto;max-height:28rem}}
details{{margin-top:.3rem}} summary{{cursor:pointer;color:var(--acc)}}
h4{{margin:.6rem 0 .2rem;color:var(--mut);font-size:.8rem;text-transform:uppercase}}
</style>
<h1>Trace {html.escape(str(summary.get('run_id')))} — {html.escape(str(summary.get('label')))}</h1>
<div class=banner>{html.escape(BANNER)}</div>
<div class=stats>
<div class=stat><b>{summary.get('events', 0)}</b><span>events</span></div>
<div class=stat><b>{summary.get('wall_ms', 0):.0f}</b><span>ms wall</span></div>
<div class=stat><b>{llm.get('calls', 0)}</b><span>model calls</span></div>
<div class=stat><b>{llm.get('input_tokens', 0):,}</b><span>tokens in</span></div>
<div class=stat><b>{llm.get('output_tokens', 0):,}</b><span>tokens out</span></div>
<div class=stat><b>RM {llm.get('cost_myr', 0):.4f}</b><span>cost</span></div>
<div class=stat><b>{summary.get('refusals', 0)}</b><span>refusals</span></div>
<div class=stat><b>{len(summary.get('errors', []))}</b><span>errors</span></div>
</div>
<div class=wrap><table>{''.join(rows)}</table></div>
"""


def write_all(run_dir: Path) -> dict:
    """Render every report from trace.jsonl. Safe on a crashed run."""
    run_dir = Path(run_dir)
    events = load(run_dir)
    sfile = run_dir / "summary.json"
    summary = json.loads(sfile.read_text()) if sfile.exists() else {
        "run_id": run_dir.name, "label": "?", "events": len(events),
        "by_kind": {}, "llm": {"calls": 0, "cost_myr": 0}, "slowest": [], "errors": [],
    }
    (run_dir / "session.log").write_text(session_log(events), encoding="utf-8")
    (run_dir / "anatomy.md").write_text(anatomy(events, summary), encoding="utf-8")
    (run_dir / "report.html").write_text(report_html(events, summary), encoding="utf-8")
    return summary
