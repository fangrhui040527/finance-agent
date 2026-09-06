"""Plain-text readings of the fact book, shared by the CLI and the MCP tools.

One formatter per question, so `ask.py facts` and the `fact_snapshot` tool
cannot drift apart - and so the nightly routine, which may have either, reads
the same page.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from knowledge.facts import FactBook, Observation


def _fmt(value: Decimal | None, text: str = "") -> str:
    if value is None:
        return text or "-"
    if abs(value) >= 1_000_000_000:
        return f"{value / Decimal(1_000_000_000):,.2f}bn"
    if abs(value) >= 1_000_000:
        return f"{value / Decimal(1_000_000):,.1f}m"
    return (
        f"{value.normalize():f}"
        if value == value.to_integral()
        else f"{value:,.4f}".rstrip("0").rstrip(".")
    )


def fact_snapshot(
    book: FactBook, instrument_id: str, now: datetime | None = None, days: int = 30
) -> str:
    """Latest figure per concept, recent and upcoming events, documents held."""
    now = now or datetime.now(UTC)
    lines = [f"{instrument_id}: what the collector holds"]

    latest: dict[str, Observation] = {}
    for o in book.observations(instrument_id, limit=2000):
        latest.setdefault(o.concept, o)  # ordered newest period, newest vintage first
    if latest:
        lines.append("")
        lines.append("  figures (latest period, latest vintage)")
        for concept in sorted(latest):
            o = latest[concept]
            period = f" for {o.period_end}" if o.period_end else ""
            unit = f" {o.currency or o.unit}".rstrip()
            lines.append(
                f"    {concept:<28} {_fmt(o.value, o.text):>14}{unit}{period}"
                f"  (knowable {o.known_at}, {o.source})"
            )

    recent = book.events(instrument_id, since=now - timedelta(days=days), until=now, limit=50)
    if recent:
        lines.append("")
        lines.append(f"  events, last {days} days")
        for e in recent:
            when = (e.effective_at or e.announced_at).date()
            lines.append(f"    {when}  {e.kind:<18} {e.title[:90]}  ({e.source})")

    upcoming = book.events(instrument_id, since=now, until=now + timedelta(days=60), limit=20)
    if upcoming:
        lines.append("")
        lines.append("  scheduled, next 60 days")
        for e in upcoming:
            when = (e.effective_at or e.announced_at).date()
            lines.append(f"    {when}  {e.kind:<18} {e.title[:90]}  ({e.source})")

    docs = book.documents(instrument_id, limit=5)
    if docs:
        lines.append("")
        lines.append("  documents")
        for d in docs:
            lines.append(
                f"    {d.published_at.date()}  {d.kind:<12} {d.title[:70]}  "
                f"({len(d.body):,} chars, {d.source})"
            )

    if len(lines) == 1:
        from knowledge.sources import catalog
        from markets.registry import mic_of

        try:
            mic = mic_of(instrument_id)
            # Per-name news feeds, plus any market-bound collector (the Taiwan
            # sources name XTAI and run for the read-only names).
            covering = [
                s.name
                for s in catalog.CATALOG.values()
                if (s.per_instrument or s.markets) and s.covers(mic)
            ]
        except ValueError:
            covering = []
        lines.append(
            f"  NOTHING COLLECTED. The sources that cover this name are "
            f"{', '.join(covering) or 'none registered'}; `ask.py sources` shows which are "
            f"enabled and which keys are set. A blank here is an empty store, not a quiet "
            f"company."
        )
    return "\n".join(lines)


def macro_context(
    book: FactBook, series_id: str = "", points: int = 5, now: datetime | None = None
) -> str:
    """Every recorded series at its latest point; or one series' last `points`.

    Every row carries the age of its newest observation, and a series past the
    cadence declared in knowledge/sources/freshness.py is marked STALE there.
    Without it a policy rate fifteen months old prints in the same column, in
    the same shape, as a Treasury yield from Thursday.
    """
    from knowledge.sources.freshness import age_label

    now = now or datetime.now(UTC)
    today = now.date()
    if series_id:
        pts = book.series(series_id)
        if not pts:
            return f"NO SERIES {series_id!r} recorded. Recorded: {', '.join(book.series_ids()) or 'none'}"
        title = (pts[-1].payload or {}).get("title", series_id)
        rows = [f"{series_id}  {title}  [newest {age_label(series_id, pts[-1].obs_date, today)}]"]
        for p in pts[-max(1, points) :]:
            rows.append(
                f"    {p.obs_date}  {_fmt(p.value):>12}   (vintage {p.known_at}, {p.source})"
            )
        return "\n".join(rows)

    ids = book.series_ids()
    if not ids:
        return (
            "NO MACRO SERIES recorded yet. The fred (FRED_API_KEY), bnm_opr and dosm_cpi "
            "collectors fill them; `ask.py sources` shows their state."
        )
    rows = ["macro series, latest point, its age and the change over the last 20 observations"]
    for sid in ids:
        pts = book.series(sid)
        latest = pts[-1]
        base = pts[-21] if len(pts) > 20 else pts[0]
        change = latest.value - base.value
        title = (latest.payload or {}).get("title", "")
        rows.append(
            f"    {sid:<16} {_fmt(latest.value):>12}  {latest.obs_date}  "
            f"{age_label(sid, latest.obs_date, today):<19} "
            f"{'+' if change >= 0 else ''}{_fmt(change)}  {title}"
        )
    rows += macro_calendar(book, now)
    return "\n".join(rows)


def macro_calendar(book: FactBook, now: datetime | None = None, days: int = 7) -> list[str]:
    """Prints of the last day and releases of the next `days`, from the
    `MACRO:<country>` events the Jin10 calendar and FRED's release dates leave
    in the fact book. Empty when neither has run: nothing is invented."""
    now = now or datetime.now(UTC)
    out: list[str] = []
    prints = [
        e
        for e in book.events(kind="macro_print", since=now - timedelta(days=1), until=now, limit=60)
        if e.instrument_id.startswith("MACRO:")
    ]
    if prints:
        out += ["", "prints, last 24h (actual vs consensus)"]
        for e in sorted(prints, key=lambda e: e.announced_at):
            star = e.payload.get("star")
            out.append(
                f"    {e.announced_at:%m-%d %H:%M}Z  {e.title}"
                + (f"  [{'*' * int(star)}]" if isinstance(star, int) and star else "")
            )
    coming = [
        e
        for e in book.events(
            kind="macro_release", since=now, until=now + timedelta(days=days), limit=120
        )
        if e.instrument_id.startswith("MACRO:")
    ]
    if coming:
        out += ["", f"releases, next {days} days"]
        for e in sorted(coming, key=lambda e: e.announced_at)[:40]:
            when = e.announced_at
            stamp = f"{when:%m-%d}" if e.payload.get("time") else f"{when:%m-%d %H:%M}Z"
            out.append(f"    {stamp:<13} {e.title}  ({e.source})")
    return out
