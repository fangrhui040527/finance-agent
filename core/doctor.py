"""Preflight: is this installation able to do what the user is about to ask?

Every check answers with a `CheckResult` naming its impact, so "n of m ready"
comes with WHICH capability is missing rather than a bare failure count. A
critical failure exits non-zero; a degraded-but-usable state does not.

Offline by default from CI (`--offline` skips the two network probes); the
probes never retry - one packet each is the whole point.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from core.env import parse as parse_env

OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    message: str
    impact: str
    critical: bool = False


def run_checks(offline: bool = False) -> list[CheckResult]:
    out: list[CheckResult] = []

    # -- config and bounds
    try:
        from core.config import load

        cfg = load()
        out.append(CheckResult("config", OK, f"loaded from {cfg.source}", "settings"))
    except Exception as e:
        out.append(CheckResult("config", FAIL, str(e), "everything", critical=True))
        return out  # nothing below is meaningful without config

    # -- registry and eval ratchet
    try:
        from core.registry.loader import load as load_registry

        reg = load_registry("agents/registry.yaml")
        out.append(
            CheckResult(
                "registry", OK, f"{len(reg.agents)} agents, {len(reg.knowledge)} stores", "routing"
            )
        )
    except Exception as e:
        out.append(CheckResult("registry", FAIL, str(e), "every agent", critical=True))

    # -- stores: writable, and still append-only
    for name, path, impact in (
        ("provenance", Path("data/provenance.db"), "cost accounting and budget checks"),
        ("learning", Path("data/learning.db"), "predictions and calibration"),
        ("paper", Path("data/paper.db"), "the paper book's ledger (ask.py paper)"),
    ):
        if not path.exists():
            out.append(CheckResult(name, WARN, f"{path} absent (created on first write)", impact))
            continue
        try:
            conn = sqlite3.connect(path)
            try:
                tables = {
                    r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                triggers = conn.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='trigger'"
                ).fetchone()[0]
            finally:
                conn.close()
            if triggers == 0:
                out.append(
                    CheckResult(
                        name,
                        FAIL,
                        f"{path}: no append-only triggers present",
                        impact,
                        critical=True,
                    )
                )
            else:
                out.append(
                    CheckResult(name, OK, f"{len(tables)} tables, {triggers} triggers", impact)
                )
        except sqlite3.DatabaseError as e:
            out.append(CheckResult(name, FAIL, f"{path}: {e}", impact, critical=True))

    graph = Path("data/graph.db")
    out.append(
        CheckResult(
            "graph",
            OK if graph.exists() else WARN,
            str(graph)
            if graph.exists()
            else "data/graph.db absent - `python ask.py stores graph` rebuilds it from the "
            "corpus where git carries the corpus as parts, `make graph` builds the spine",
            "multi-hop exposure claims",
        )
    )

    # -- the corpus as git carries it, and anything nearing GitHub's file limit
    from knowledge import stores

    corpus = Path("data/corpus.db")
    st = stores.state(corpus)
    big = stores.oversized("data")
    if big:
        status = WARN
        detail = "; ".join(f"{p} {n / 2**20:.1f} MiB" for p, n in big) + (
            " - GitHub warns at 50 MiB and refuses a push over 100 MiB"
        )
    elif st.name in (stores.UNGOVERNED, stores.CURRENT, stores.AHEAD):
        status, detail = OK, f"{corpus} {st}"
    else:
        status, detail = WARN, f"{corpus} {st}. `python ask.py stores` says more"
    out.append(CheckResult("stores", status, detail, "the corpus every reader opens"))

    # -- .env: loaded, or deliberately skipped?
    from pathlib import Path as _P

    dotenv = _P(".env")
    if os.environ.get("FINPLANET_NO_DOTENV", "").strip():
        out.append(
            CheckResult("dotenv", OK, "FINPLANET_NO_DOTENV set: file ignored on purpose", "secrets")
        )
    elif dotenv.is_file():
        names = sorted(parse_env(dotenv.read_text(encoding="utf-8")))
        out.append(
            CheckResult(
                "dotenv", OK, f".env present, {len(names)} key(s): {', '.join(names)}", "secrets"
            )
        )
    else:
        out.append(
            CheckResult(
                "dotenv", WARN, ".env absent - keys must be exported in the environment", "secrets"
            )
        )

    # -- retrieval vectors
    #
    # Named here because the two backends behave differently in a way no error
    # message would ever surface: the keyless one only relates words it has seen
    # in THIS corpus, so a question phrased in outside vocabulary reaches
    # nothing and looks exactly like a corpus with no answer in it.
    from knowledge.retrieval.embedding import EMBED_KEY_ENV, default_embedder

    embedder = default_embedder()
    keyed = os.environ.get(EMBED_KEY_ENV, "").strip()
    out.append(
        CheckResult(
            "embedder",
            OK,
            (
                f"{type(embedder).__name__}"
                + (
                    ""
                    if keyed
                    else f"; no {EMBED_KEY_ENV}, so vectors are learned from the corpus and "
                    "a question in words the corpus has never used reaches nothing"
                )
            ),
            "how well search answers a question phrased in its own words",
        )
    )

    # -- model backend
    from core.llm.backends import backend_from_env

    backend, reason = backend_from_env()
    kind = type(backend).__name__
    out.append(
        CheckResult(
            "backend",
            OK if kind != "EchoBackend" else WARN,
            reason,
            "narrative output (numbers are engine-computed either way)",
        )
    )
    out.append(spend_cap(cfg, backend))

    # -- debug retention
    debug = Path(os.environ.get("FINPLANET_DEBUG_DIR", "debug"))
    if debug.is_dir():
        runs = [p for p in debug.iterdir() if p.is_dir()]
        size_mb = sum(f.stat().st_size for f in debug.rglob("*") if f.is_file()) / 1e6
        status = WARN if size_mb > 200 else OK
        out.append(
            CheckResult(
                "traces",
                status,
                f"{len(runs)} runs, {size_mb:.0f} MB (verbatim prompts; pruned on next run)",
                "disk and privacy",
            )
        )

    # -- network probes (one packet each, never retried)
    if offline:
        out.append(CheckResult("stooq", SKIP, "--offline", "price history"))
        out.append(CheckResult("gdelt", SKIP, "--offline", "news"))
    else:
        import urllib.error
        import urllib.request

        for name, url, impact in (
            ("stooq", "https://stooq.com/q/d/l/?s=spy.us&i=d", "price history"),
            (
                "gdelt",
                os.environ.get("GDELT_DOC_API", "https://api.gdeltproject.org/api/v2/doc/doc")
                + "?query=domainis:reuters.com&mode=artlist&format=json&maxrecords=1&timespan=15min",
                "news",
            ),
        ):
            req = urllib.request.Request(
                url, headers={"User-Agent": "finplanet-analyst-mind/0.1 (doctor)"}
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    body = resp.read(200)
                walled = body.lstrip()[:1] == b"<" and name == "stooq"
                out.append(
                    CheckResult(
                        name,
                        WARN if walled else OK,
                        "answers, but with an HTML browser check - the chain will use Yahoo"
                        if walled
                        else "reachable",
                        impact,
                    )
                )
            except (urllib.error.URLError, OSError) as e:
                out.append(CheckResult(name, WARN, f"unreachable: {e}", impact))

    return out


def month_to_date_usd(cfg, now=None):
    """This month's USD spend: the committed evidence spend in `cfg.corpus_db`
    plus the local provenance ledger at `cfg.provenance_db`.

    The ledger is opened only if it exists - a preflight that created the file
    it was asked about would report on a ledger of its own making - and read
    through `spend.month_to_date_usd`, the same function the client's cap uses.
    """
    from types import SimpleNamespace

    from core.llm import spend

    path = Path(cfg.provenance_db)
    con = None
    ledger = None
    if str(cfg.provenance_db) != ":memory:" and path.is_file() and path.stat().st_size > 0:
        con = sqlite3.connect(path, timeout=10)
        has = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_calls'"
        ).fetchone()
        if has:
            ledger = SimpleNamespace(conn=con)
    try:
        return spend.month_to_date_usd(ledger, cfg.corpus_db, now=now)
    finally:
        if con is not None:
            con.close()


def spend_cap(cfg, backend=None, now=None) -> CheckResult:
    """The owner's monthly USD cap, and how much of it this month has used.

    WARN when no cap is set - the rolling 24h rail alone lets a month add up to
    thirty of them - or when month-to-date has reached
    `spend.MONTHLY_WARN_FRACTION` of it, so the operator hears about the cap
    before the first refusal rather than from it. The model pin and effort,
    when set, are still named: they decide how fast the month is spent.
    """
    from decimal import Decimal

    from core.llm import spend
    from core.llm.backends import pricing_of
    from core.llm.tiers import MESSAGES_TIERS, selection_note

    impact = "spend (the hard monthly limit on the API key)"
    notes: list[str] = []
    zero = (Decimal(0), Decimal(0))
    free = backend is not None and all(pricing_of(backend, tier) == zero for tier in MESSAGES_TIERS)
    if free:
        # Nothing on the reasoning path bills, so an absent cap is not a risk
        # worth a warning there (docs/21); the month is still reported.
        notes.append("every Messages tier priced at zero on the free provider")
    note = selection_note()
    if note:
        notes.append(note)
    tail = "".join(f"; {n}" for n in notes)

    month = spend.month_start(now)
    try:
        spent = month_to_date_usd(cfg, now=now)
    except (sqlite3.Error, OSError, ArithmeticError) as e:
        return CheckResult(
            "spend-cap", WARN, f"month-to-date spend cannot be read: {e}{tail}", impact
        )

    cap = cfg.monthly_budget_usd
    if cap is None:
        return CheckResult(
            "spend-cap",
            OK if free else WARN,
            f"no monthly cap ([budget] monthly_usd unset) - USD {spent:.2f} spent since "
            f"{month:%Y-%m-%d} and nothing stops the next call but the 24h rail{tail}",
            impact,
        )
    share = spent / cap
    evidence = cfg.evidence_budget_usd
    sub = f", evidence sub-cap USD {evidence:.2f}" if evidence is not None else ""
    near = share >= spend.MONTHLY_WARN_FRACTION
    return CheckResult(
        "spend-cap",
        WARN if near else OK,
        f"USD {spent:.2f} of the USD {cap:.2f} monthly cap spent since {month:%Y-%m-%d} "
        f"({share:.0%}{sub})"
        + (
            f" - past {spend.MONTHLY_WARN_FRACTION:.0%}; calls are refused once the next "
            "one's worst case would cross the cap"
            if near
            else ""
        )
        + tail,
        impact,
    )


def render(results: list[CheckResult]) -> str:
    icon = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL ", SKIP: " skip "}
    lines = ["PREFLIGHT", "-" * 66]
    for r in results:
        lines.append(f"[{icon[r.status]}] {r.name:<12} {r.message}")
        if r.status in (WARN, FAIL):
            lines.append(f"           affects: {r.impact}")
    ready = sum(1 for r in results if r.status == OK)
    lines.append("-" * 66)
    lines.append(f"{ready}/{len(results)} ready")
    return "\n".join(lines)
