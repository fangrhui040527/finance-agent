"""The sweep's ledger: one honest line per source, and the run's verdict on it.

WHY THIS EXISTS. On 2026-10-27 the ALL_SECRET blob was overwritten and the
FINNHUB, FRED and ALPHAVANTAGE keys went with it; FMP's key was refused with
HTTP 401. Every collect run that day concluded `success`. A source with no key
was written `ok` with a "skipped:" detail and exited 0 by design ("the job
stays green until someone adds it"), and the 401 was one `failed` among the
GDELT throttles that exit 3 every day and are, rightly, only a warning. Nobody
was told, because nothing that pages anyone could tell a missing key from a
quiet night.

So the run writes this ledger beside its stores (`ask.py sweep --ledger`), and
collect.yml reads it twice: as a per-source table on the run's summary page,
and AFTER the data commit, as the step that turns the run red when

  * a source was skipped because its key is not set (`skipped_no_key`), or its
    key was refused (`auth_failed`) - at all, every time: a key does not come
    back on its own; or
  * an expected source came back nearly empty two firings of its slot in a
    row: under EXPECTED_ROWS_FRACTION of its median over the firings before
    (`row_shortfalls`).

Red is what makes GitHub e-mail the owner. It comes after the commit so the
collection is never lost to it. The monitor (`core.monitor`, rules
`source_keys` and `expected_rows`) reads the same tables, so the alert log
opens and resolves on the same facts.

Nothing here prints a key: a detail is scrubbed (`knowledge.redact`) before
it is written, and a missing key is named by its variable, never its value.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from knowledge.corpus import KEY_PROBLEMS
from knowledge.redact import scrub

#: How many firings of a slot a source's usual row count is the median of.
EXPECTED_ROWS_HISTORY = 20
#: Fewer firings than this and there is no "usual" to fall short of yet.
EXPECTED_ROWS_MIN_HISTORY = 5
#: Under this share of the median is a shortfall; zero rows always is one.
EXPECTED_ROWS_FRACTION = 0.2
#: How many firings in a row must fall short. One thin night is news; two is
#: a source that changed under us.
EXPECTED_ROWS_STREAK = 2
#: A source whose median run brings fewer rows than this is too sparse to
#: judge: EDGAR's filings for a nine-name book (median 1) and GDELT's Bursa
#: names (2) are empty on ordinary days.
EXPECTED_ROWS_MIN_MEDIAN = 5

#: The market whose sessions decide whether a slot's firing was a trading day.
#: A holiday or a weekend firing is left out of the history and the streak, so
#: a quiet Hari Raya is not a dead source. `weekly` fires on Sunday and has no
#: session; every firing counts.
SLOT_MARKET: dict[str, str] = {
    "bursa_close": "XKLS",
    "us_preopen": "XNAS",
    "us_close": "XNAS",
}

#: How much of a source's detail a ledger line keeps.
LEDGER_DETAIL_CHARS = 300


@dataclass(frozen=True)
class RowShortfall:
    """An expected source that came back nearly empty, firing after firing."""

    source: str
    slot: str
    #: Rows on the last EXPECTED_ROWS_STREAK firings, newest first.
    recent: tuple[int, ...]
    median: float
    #: How many firings the median was taken over.
    history: int

    def line(self) -> str:
        got = ", ".join(str(n) for n in self.recent)
        return (
            f"{self.source} in {self.slot}: {got} rows on its last {len(self.recent)} "
            f"firings, under {EXPECTED_ROWS_FRACTION:.0%} of its median {self.median:g} "
            f"over the {self.history} before"
        )

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "slot": self.slot,
            "recent": list(self.recent),
            "median": self.median,
            "history": self.history,
        }


def market_session(slot: str, day: date) -> bool:
    """Whether `day` was a trading session of the market `slot` collects after."""
    mic = SLOT_MARKET.get(slot)
    if mic is None:
        return True
    try:
        from markets.registry import get

        return get(mic).calendar.is_session(day)
    except (KeyError, ValueError):
        # A calendar that cannot be built judges every day: one false alert
        # is better than a check that quietly stopped looking.
        return True


def _firings(rows, slot: str, is_session: Callable[[str, date], bool]) -> list[int]:
    """Row counts per firing of `slot`, newest firing first.

    A run belongs to the firing before it (`core.monitor.slot_window_start`),
    and a firing answered twice - the cron and the catch-up, or a forced
    re-run - counts its BEST run. A re-run an hour later over a one-hour
    window reads less than the run that read the whole window, so it cannot
    pull the firing down; and a `force` re-run after the source was repaired
    reads more, so it clears the firing's shortfall at once instead of at the
    next scheduled firing.
    """
    from core.monitor import slot_window_start

    best: dict[datetime, int] = {}
    for r in rows:
        at = datetime.fromisoformat(r["at"])
        at = at if at.tzinfo else at.replace(tzinfo=UTC)
        fired = slot_window_start(slot, at)
        if not is_session(slot, fired.date()):
            continue
        best[fired] = max(best.get(fired, 0), int(r["fetched"] or 0))
    return [best[f] for f in sorted(best, reverse=True)]


def row_shortfalls(
    corpus,
    slot: str,
    sources: Iterable[str],
    is_session: Callable[[str, date], bool] | None = None,
) -> list[RowShortfall]:
    """The sources in `sources` whose last EXPECTED_ROWS_STREAK firings of
    `slot` each read under EXPECTED_ROWS_FRACTION of the median of the
    EXPECTED_ROWS_HISTORY firings before them.

    Only runs that READ count (`Corpus.slot_history`): a failure or a missing
    key is already its own signal, and a run that read nothing because it
    could not read is not a run that found nothing. The row count is the
    sweep row's `fetched`: what the source returned, before deduplication, so
    a night of repeats is not a shortfall.
    """
    session = is_session or market_session
    need = EXPECTED_ROWS_STREAK + EXPECTED_ROWS_HISTORY
    out: list[RowShortfall] = []
    for name in sources:
        counts = _firings(corpus.slot_history(name, slot), slot, session)[:need]
        recent, before = counts[:EXPECTED_ROWS_STREAK], counts[EXPECTED_ROWS_STREAK:]
        if len(recent) < EXPECTED_ROWS_STREAK or len(before) < EXPECTED_ROWS_MIN_HISTORY:
            continue
        median = float(statistics.median(before))
        if median < EXPECTED_ROWS_MIN_MEDIAN:
            continue
        if all(n < EXPECTED_ROWS_FRACTION * median for n in recent):
            out.append(RowShortfall(name, slot, tuple(recent), median, len(before)))
    return out


# --- one run's ledger ----------------------------------------------------------------


def build(report, shortfalls: Iterable[RowShortfall] = ()) -> dict:
    """The ledger of one `run_sweep` report, as JSON-ready data."""
    return {
        "run_id": report.run_id,
        "slot": report.slot,
        "exit_code": report.exit_code,
        "could_not_run": scrub(report.could_not_run),
        "already_ran": scrub(report.already_ran),
        "sources": [
            {
                "name": r.name,
                "status": r.status,
                "fetched": r.fetched,
                "stored": r.stored,
                "facts": r.structured_stored,
                "seconds": round(r.seconds, 1),
                "detail": scrub(" ".join(r.detail.split()))[:LEDGER_DETAIL_CHARS],
            }
            for r in report.results
        ],
        "row_shortfalls": [s.as_dict() for s in shortfalls],
    }


def write(path: str | Path, ledger: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read(path: str | Path) -> dict | None:
    """The ledger at `path`, or None when the sweep wrote none."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def problems(ledger: dict) -> list[str]:
    """What turns the run red, one line each, naming the source and why."""
    out = [
        f"{s['name']}: {s['status']} - {s['detail']}"
        for s in ledger.get("sources", ())
        if s.get("status") in KEY_PROBLEMS
    ]
    for s in ledger.get("row_shortfalls", ()):
        out.append(
            "expected rows: "
            + RowShortfall(
                s["source"], s["slot"], tuple(s["recent"]), s["median"], s["history"]
            ).line()
        )
    return out


def summary_markdown(ledger: dict | None) -> str:
    """The per-source table for $GITHUB_STEP_SUMMARY."""
    if ledger is None:
        return "## Sweep ledger\n\nNo ledger was written: the sweep did not reach its end.\n"
    lines = [f"## Sweep ledger: {ledger.get('slot')} (run {ledger.get('run_id')})", ""]
    if ledger.get("could_not_run"):
        return "\n".join(lines + [f"Could not run: {ledger['could_not_run']}", ""])
    if ledger.get("already_ran"):
        return "\n".join(lines + [f"Nothing collected: {ledger['already_ran']}", ""])
    lines += [
        "| source | status | fetched | stored | facts | detail |",
        "|---|---|---:|---:|---:|---|",
    ]
    for s in ledger.get("sources", ()):
        detail = str(s.get("detail", "")).replace("|", "/")[:160]
        mark = "**" if s.get("status") in KEY_PROBLEMS else ""
        lines.append(
            f"| {s['name']} | {mark}{s['status']}{mark} | {s.get('fetched', 0)} | "
            f"{s.get('stored', 0)} | {s.get('facts', 0)} | {detail} |"
        )
    found = problems(ledger)
    lines.append("")
    if found:
        lines += ["**This run fails after its commit:**", ""] + [f"- {p}" for p in found]
    else:
        lines.append("No missing or refused key, and no expected source ran short.")
    return "\n".join(lines) + "\n"


def summary_text(ledger: dict | None) -> str:
    """A short plain-text body for the dead-man ping. Never a key."""
    if ledger is None:
        return "no sweep ledger: the sweep did not reach its end"
    head = f"sweep {ledger.get('run_id')} slot {ledger.get('slot')} exit {ledger.get('exit_code')}"
    if ledger.get("could_not_run"):
        return f"{head}\ncould not run: {ledger['could_not_run']}"
    if ledger.get("already_ran"):
        return f"{head}\nalready collected"
    rows = [f"{s['name']} {s['status']} {s.get('fetched', 0)}" for s in ledger.get("sources", ())]
    return "\n".join([head, *rows, *problems(ledger)])


def main(argv: list[str] | None = None) -> int:
    """`python -m knowledge.ledger summary|text|check PATH`.

    summary  the markdown table, for $GITHUB_STEP_SUMMARY; exits 0
    text     the plain-text body for the dead-man ping; exits 0
    check    prints what turns the run red and exits 1, else exits 0. A run
             with no ledger exits 0: the sweep's own exit code already says
             it could not run, and another step fails the job for that.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] not in ("summary", "text", "check"):
        print("usage: python -m knowledge.ledger summary|text|check PATH", file=sys.stderr)
        return 2
    command, path = args
    ledger = read(path)
    if command == "summary":
        print(summary_markdown(ledger), end="")
        return 0
    if command == "text":
        print(summary_text(ledger))
        return 0
    if ledger is None:
        print(f"no sweep ledger at {path}; nothing to judge")
        return 0
    found = problems(ledger)
    if not found:
        print("ledger: no missing or refused key, and no expected source ran short")
        return 0
    for p in found:
        print(f"::error title=sweep ledger::{p}")
    print(
        "The data is committed. Set or replace the key as a repository secret, or see "
        "docs/14-OPERATIONS-RUNBOOK.md, Sweep ledger."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
