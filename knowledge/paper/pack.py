"""The paper pack: the deterministic half of the nightly paper journal.

docs/22 section 9. Code prepares every number the page may carry - the
status, the day's position changes with their fees, both books' marks and
day returns, the attribution of each held name's move, the predictions due
and graded - and writes it as one markdown file. The routine reasons over
that file: what the book did, whether the decision or the market did it,
what it got wrong, what it would look for tomorrow. Nothing in the page may
be a number the pack did not carry.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from engines.paper.store import CONTROL, DECIDED, INDEX, PaperStore

PAPER_DIR = Path("knowledge/paper")


def _fmt_pct(x: Decimal | float | None) -> str:
    return "-" if x is None else f"{float(x):+.2%}"


def _index_changes(changes, resolved) -> list[str]:
    """The index book's day as totals, and every target it could not apply."""
    out: list[str] = []
    if changes:
        counts: dict[str, int] = {}
        for c in changes:
            counts[c.action] = counts.get(c.action, 0) + 1
        fees = sum((c.fee_usd for c in changes), Decimal(0))
        spread = sum((c.fx_spread_usd for c in changes), Decimal(0))
        slip = sum((c.slippage_usd for c in changes), Decimal(0))
        cash = sum((c.cash_delta_usd for c in changes), Decimal(0))
        out.append(
            f"- {len(changes)} position change(s): "
            + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
            + f"; fees USD {fees:,.2f}, fx spread {spread:,.2f}, slippage {slip:,.2f}, "
            f"cash {cash:+,.2f}"
        )
    by_status: dict[str, list[str]] = {}
    for t, a in resolved:
        by_status.setdefault(a.status, []).append(f"{t.instrument_id} ({a.detail})")
    for status_, rows in sorted(by_status.items()):
        out.append(f"- {status_} {len(rows)}: " + "; ".join(rows))
    out.append("")
    return out


def build_paper_pack(
    cfg,
    day: date | None = None,
    *,
    store: PaperStore | None = None,
    store_path: str | Path | None = None,
    learning_path: str | Path | None = None,
    feed=None,
    fx=None,
    now: datetime | None = None,
    previous_dir: str | Path | None = PAPER_DIR,
) -> str:
    """The pack for `day`, as markdown. NO BOOK is a page, not an error."""
    from core.market.feed import default_feed
    from engines.paper.book import fx_for
    from engines.paper.report import status
    from knowledge.graph.ids import display_names
    from knowledge.graph.ids import instrument_id as canonical
    from knowledge.pack import measure

    now = now or datetime.now(UTC)
    day = day or now.date()
    settings = cfg.paper
    out = [
        f"# Paper pack {day}",
        "",
        f"Prepared {now:%Y-%m-%d %H:%M} UTC by `ask.py paper pack`. Every number below is "
        "measured or copied from the paper ledger and the price cache; the page written "
        "from it copies them and estimates nothing. The contract is knowledge/paper/README.md.",
        "",
    ]
    own = store is None
    if own:
        store = PaperStore.open_existing(store_path or settings.database)
    if store is None or not store.has_books():
        out.append("NO BOOK: `ask.py paper init` has not been run. There is nothing to journal.")
        return "\n".join(out)
    try:
        feed = feed or default_feed()
        fx = fx or fx_for(cfg)
        names = display_names()

        st = status(store, cfg, feed, fx, day=day)
        out += ["## Status", "", "```", st.render(), "```", ""]

        books = (DECIDED, CONTROL) + ((INDEX,) if store.has_book(INDEX) else ())
        out += ["## Today's changes", ""]
        for book in books:
            changes = store.changes(book, start=day, end=day)
            resolved = [
                (t, a)
                for t, a in store.applications(book, since=day)
                if a.resolved_on == day and a.status != "applied"
            ]
            if not changes and not resolved:
                out.append(f"- {book}: no change")
                continue
            out.append(f"### {book}")
            out.append("")
            if book == INDEX:
                # A rebalance is a hundred rows; the page needs the totals.
                out += _index_changes(changes, resolved)
                continue
            if changes:
                out.append(
                    "| action | name | units | price | fee USD | fx spread USD | slippage USD | cash USD | realised USD |"
                )
                out.append("|---|---|---|---|---|---|---|---|---|")
                for c in changes:
                    out.append(
                        f"| {c.action} | {c.instrument_id} | {c.units_delta:+d} | {c.price_local} {c.currency} | "
                        f"{c.fee_usd:.2f} | {c.fx_spread_usd:.2f} | {c.slippage_usd:.2f} | "
                        f"{c.cash_delta_usd:+,.2f} | {c.realised_pnl_usd:+,.2f} |"
                    )
                out.append("")
            for t, a in resolved:
                out.append(
                    f"- {a.status}: {t.instrument_id} {t.weight:.2%} ({t.reason}) - {a.detail}"
                )
            out.append("")

        out += [
            "## Marks",
            "",
            "| book | day | equity USD | cash | positions | day return | drawdown | halted |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for book in books:
            marks = store.marks(book)
            today = [m for m in marks if m.day == day]
            m = today[-1] if today else (marks[-1] if marks else None)
            if m is None:
                out.append(f"| {book} | - | not marked | | | | | |")
                continue
            prev = [x for x in marks if x.day < m.day]
            day_ret = (
                (m.equity_usd / prev[-1].equity_usd - 1)
                if prev and prev[-1].equity_usd > 0
                else None
            )
            out.append(
                f"| {book} | {m.day} | {m.equity_usd:,.2f} | {m.cash_usd:,.2f} | {m.positions_usd:,.2f} | "
                f"{_fmt_pct(day_ret)} | {m.drawdown:.2%} | {'yes' if m.halted else 'no'} |"
            )
        out.append("")

        latest = store.latest_mark(DECIDED, on_or_before=day)
        out += ["## Attribution of held names (engines/attribution, USD legs at the mid)", ""]
        if latest is None or not latest.positions:
            out.append("- no positions held; nothing to attribute")
        else:
            out.append("| name | 1d | market 1d | 5d | market 5d | decomposition |")
            out.append("|---|---|---|---|---|---|")
            book_market = book_idio = Decimal(0)
            for p in latest.positions:
                iid = p["instrument_id"]
                label = names.get(canonical(iid) or iid) or iid
                mv = measure(feed, iid, label, day, "USD")
                out.append(mv.row())
                w = Decimal(str(p.get("weight", "0")))
                if not mv.error:
                    book_market += w * Decimal(str(mv.components.get("market", 0.0)))
                    book_idio += w * Decimal(str(mv.components.get("idiosyncratic", 0.0)))
            out.append("")
            out.append(
                f"- weighted by position: market {book_market:+.2%}, idiosyncratic {book_idio:+.2%} of "
                "the book's day. A story is warranted only for names whose verdict is not "
                "market_driven or not_significant."
            )
        out.append("")

        out += ["## Predictions", ""]
        try:
            from agents.learning.store import LearningStore

            with LearningStore(learning_path or cfg.database) as learning:
                due = [
                    p
                    for p in learning.pending()
                    if p.agent == "paper" and p.grade_on <= day + timedelta(days=7)
                ]
                graded = [
                    o
                    for o in learning.graded(since=day - timedelta(days=7))
                    if o.prediction_id.startswith("paper-")
                ]
            if due:
                out.append("due within seven days:")
                out += [
                    f"- {p.prediction_id}  {p.statement[:100]}  grades on {p.grade_on}" for p in due
                ]
            else:
                out.append("- nothing due within seven days")
            if graded:
                out.append("graded in the last seven days:")
                out += [
                    f"- {o.prediction_id}  realised {o.realised_return:+.2%}  control {o.benchmark_return:+.2%}  "
                    f"{'correct' if o.correct else 'wrong'}  {o.note}"
                    for o in graded
                ]
        except Exception as e:  # a missing learning store is a fact to report, not a crash
            out.append(f"- prediction log unreadable: {str(e).splitlines()[0]}")
        out.append("")

        if previous_dir is not None:
            prev = sorted(
                p.name for p in Path(previous_dir).glob("????-??-??.md") if p.name < f"{day}.md"
            )[-3:]
            out += ["## Previous pages", ""]
            out += [f"- knowledge/paper/{p}" for p in prev] or ["- none yet"]
            out.append("")
    finally:
        if own:
            store.close()
    return "\n".join(out)


def write_paper_pack(text: str, day: date, root: str | Path = PAPER_DIR) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{day}.pack.md"
    path.write_text(text + "\n", encoding="utf-8")
    return path
