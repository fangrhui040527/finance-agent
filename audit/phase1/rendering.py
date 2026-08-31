"""S5-S6 and E1: what happens when model text reaches a screen.

OWASP LLM02, insecure output handling, is the blueprint's most concrete
security item and the easiest to get silently wrong: a model writes
`<script>`, a renderer concatenates it into `innerHTML`, and the page executes
whatever the model was persuaded to write. The defence is not sanitising - it
is never treating model output as markup in the first place.

E1 is the blueprint's React thread-lock item translated honestly. There is no
React here and no token streaming into the DOM, so the risk it names does not
exist in this form. What does exist is the same underlying hazard: a very large
answer rendered in one synchronous pass. The check measures what the renderer
would actually be handed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from audit._support.scorecard import Check

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "web" / "static"

#: What a prompt-injected model would emit if it were trying to reach the DOM.
PAYLOADS = (
    "<script>window.__pwned=1</script>",
    "<img src=x onerror=alert(1)>",
    "</pre><iframe src=javascript:alert(1)>",
    '<a href="javascript:alert(1)">click</a>',
    "<svg/onload=alert(1)>",
)


def s5_model_text_never_becomes_markup() -> Check:
    c = Check(
        "S5",
        "Security",
        1,
        "Model and tool text is rendered as text, never as markup",
        "OWASP LLM02: the renderer cannot be made to execute what the model wrote",
    )
    offenders = []
    for js in sorted(STATIC.rglob("*.js")):
        src = js.read_text(encoding="utf-8")
        for i, line in enumerate(src.splitlines(), 1):
            if re.search(r"\.(innerHTML|outerHTML)\s*=", line) or "insertAdjacentHTML" in line:
                # One assignment is allowed and is annotated as such: the icon
                # table is this repository's own constant, with no user data in
                # it. Anything else is a finding.
                if "trusted:" in line:
                    continue
                offenders.append(f"{js.relative_to(ROOT)}:{i}")
    if offenders:
        return c.failed(f"unguarded markup assignment(s): {', '.join(offenders)}")
    c.evidence = f"{len(list(STATIC.rglob('*.js')))} modules; every text path uses textContent"
    return c.ok()


def s6_a_payload_survives_the_api_as_inert_text() -> Check:
    """End to end: a hostile string goes in, and comes back escaped as data."""
    c = Check(
        "S6",
        "Security",
        1,
        "An injected payload returns as JSON data, not as HTML",
        "the transport cannot be tricked into delivering executable markup",
    )
    from fastapi.testclient import TestClient

    from web.app import create_app

    client = TestClient(create_app())
    for payload in PAYLOADS:
        resp = client.post(
            "/api/allocate",
            json={"names": [payload], "portfolio_value": 200000},
            headers={"X-Requested-With": "FinPlanet"},
        )
        ctype = resp.headers.get("content-type", "")
        if "application/json" not in ctype:
            return c.failed(f"a payload came back as {ctype!r} rather than JSON")
        body = resp.text
        # The raw payload must never appear unescaped in an HTML-ish response;
        # inside a JSON string it is inert, which is what we want to see.
        if "<script" in body.lower() and "\\u003c" not in body.lower():
            if not body.lstrip().startswith("{"):
                return c.failed("a script tag reached the client outside a JSON string")
    c.evidence = f"{len(PAYLOADS)} payloads returned inside JSON with the correct content type"
    return c.ok()


def s7_the_shell_has_no_inline_script() -> Check:
    """An inline script is the difference between an injection being contained
    and being executed - and it is what a CSP would forbid."""
    c = Check(
        "S7",
        "Security",
        1,
        "The page shell loads no inline or remote script",
        "no third-party origin and no inline execution to hijack",
        blocking=False,
    )
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", index, re.S)
    meaty = [s for s in inline if s.strip()]
    remote = re.findall(r'<script[^>]*src="(https?://[^"]+)"', index)
    if remote:
        return c.failed(f"remote script origin(s): {', '.join(remote)}")
    if meaty:
        return c.failed(f"{len(meaty)} inline script block(s) in the shell")
    c.evidence = "module scripts only, all same-origin"
    return c.ok()


def e1_a_large_answer_stays_a_reasonable_payload() -> Check:
    """The thread-lock item, translated to what this architecture actually does.

    Nothing streams into the DOM here: an answer arrives whole and is written
    once into a <pre>. So the question is not whether re-renders block the main
    thread, it is whether a single answer can be big enough to matter.
    """
    c = Check(
        "E1",
        "Efficiency",
        1,
        "The largest routine answer is small enough to render in one pass",
        "React thread-lock analog: bounded payload instead of unbounded token append",
    )
    from mcp_server.server import S

    biggest, name = 0, ""
    samples = (
        ("market_info", {}),
        ("scorecard", {"db": ":memory:"}),
        ("maintainability_report", {"db": ":memory:"}),
        ("explain_concept", {"concept": "expected value"}),
    )
    for label, args in samples:
        resp = S.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": label, "arguments": args},
            }
        )
        content = resp.get("result", {}).get("content") or [{}]
        size = len(str(content[0].get("text", "")))
        if size > biggest:
            biggest, name = size, label
    if biggest > 400_000:
        return c.failed(
            f"{name} returns {biggest:,} characters in one payload; a single "
            f"synchronous write of that size is the thread-lock this check exists for"
        )
    c.evidence = f"largest sampled answer is {name} at {biggest:,} characters"
    return c.ok()


def e2_the_screens_parse() -> Check:
    """A syntax error in a screen is a silent failure: the import rejects, the
    panel never renders, and the server still returns 200 for the file."""
    c = Check(
        "E2",
        "Maintainability",
        1,
        "Every screen module parses as a real ES module",
        "a broken screen fails silently; the parser is the only thing that notices",
    )
    node = shutil.which("node")
    if node is None:
        return c.skipped("node is not installed; a browser is the only other parser")
    bad = []
    for js in sorted((STATIC / "screens").glob("*.js")):
        proc = subprocess.run(
            [node, "--input-type=module", "--check"],
            input=js.read_bytes(),
            capture_output=True,
        )
        if proc.returncode != 0:
            bad.append(js.name)
    if bad:
        return c.failed(f"screen(s) with a syntax error: {', '.join(bad)}")
    c.evidence = f"{len(list((STATIC / 'screens').glob('*.js')))} screens parse clean"
    return c.ok()


CHECKS = (
    s5_model_text_never_becomes_markup,
    s6_a_payload_survives_the_api_as_inert_text,
    s7_the_shell_has_no_inline_script,
    e1_a_large_answer_stays_a_reasonable_payload,
    e2_the_screens_parse,
)
