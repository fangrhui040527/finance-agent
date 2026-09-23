"""The caps, the phase calendar and the fundable set.

Every rule here is arithmetic the decider cannot argue with: a target book
that breaches one is refused with every breach named, and nothing is written.
The phase calendar (docs/22 section 5) is dated from `[paper] start_date`:
two observe weeks in which decisions are recorded and graded but never
applied, four ramp weeks capped at 40% invested, then the full conservative
profile.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal

from core.market.calendar import PROVISIONAL, UNKNOWN, last_session_close, price_state
from core.market.feed import PriceFeedError
from engines.paper.fx import FxQuote
from engines.paper.pricing import (
    currency_of,
    last_close,
    lot_size,
    round_trip_pct,
)
from engines.paper.settings import PaperSettings
from engines.paper.store import BookState
from markets.registry import mic_of

PRE, OBSERVE, RAMP, FULL = "pre", "observe", "ramp", "full"


@dataclass(frozen=True)
class Phase:
    name: str
    week: int
    max_invested: Decimal
    review_due: bool = False

    @property
    def invests(self) -> bool:
        return self.name in (RAMP, FULL)

    def describe(self) -> str:
        if self.name == PRE:
            return "before the start date: nothing is recorded yet"
        if self.name == OBSERVE:
            return f"week {self.week}, observe: decisions are logged and graded, never applied"
        if self.name == RAMP:
            return f"week {self.week}, ramp: invested weight capped at {self.max_invested:.0%}"
        tail = "; P16 review due" if self.review_due else ""
        return f"week {self.week}, full caps: invested weight up to {self.max_invested:.0%}{tail}"


def phase_for(day: date, settings: PaperSettings) -> Phase:
    if day < settings.start_date:
        return Phase(PRE, 0, Decimal(0))
    week = (day - settings.start_date).days // 7 + 1
    if week <= settings.observe_weeks:
        return Phase(OBSERVE, week, Decimal(0))
    if week <= settings.observe_weeks + settings.ramp_weeks:
        return Phase(RAMP, week, settings.ramp_max_invested)
    return Phase(FULL, week, settings.max_invested, review_due=week >= 14)


def min_names(settings: PaperSettings, invested: Decimal) -> int:
    """The agreed reading of "at least four names when invested".

    Four whole lots cannot fit under the ramp ceiling at USD 1,000, so the
    four-name rule binds only once invested weight exceeds that ceiling; below
    it the arithmetic minimum applies - as many names as the per-name cap
    forces, at least one.
    """
    if invested <= 0:
        return 0
    if invested > settings.ramp_max_invested:
        return settings.min_names_when_invested
    return max(1, math.ceil(float(invested / settings.max_weight_per_name) - 1e-9))


def names_rule(settings: PaperSettings, invested: Decimal) -> str:
    if invested > settings.ramp_max_invested:
        return f"at least {settings.min_names_when_invested} names (invested above {settings.ramp_max_invested:.0%})"
    return (
        f"at least ceil(invested / {settings.max_weight_per_name:.0%}) names while invested is "
        f"at or below {settings.ramp_max_invested:.0%}"
    )


# -- fundable set --------------------------------------------------------------------------


@dataclass(frozen=True)
class Fundable:
    instrument_id: str
    currency: str
    lot: int
    price_local: Decimal
    close_day: date | None
    lot_usd: Decimal
    lot_weight: Decimal
    max_lots: int
    round_trip: Decimal
    error: str = ""
    #: Whether `price_local` is a settled close, the session so far, or
    #: unknowable. `core.market.calendar.price_state` owns the rule; this field
    #: only carries its answer so the page can say which it printed.
    price_state: str = UNKNOWN
    #: The market's latest finished session when it is newer than `close_day`:
    #: the price is an older session's close, and the cache holds no bar for
    #: the session the market has closed since. None when the price is current.
    newer_session: date | None = None

    @property
    def fundable(self) -> bool:
        return not self.error and self.max_lots >= 1

    @property
    def provisional(self) -> bool:
        """The price was pulled while its market was still trading."""
        return self.price_state == PROVISIONAL

    def row(self) -> str:
        if self.error:
            return f"  {self.instrument_id:<12} NO DATA  {self.error[:60]}"
        verdict = (
            f"up to {self.max_lots} lot(s)" if self.fundable else "not fundable at this equity"
        )
        mark = "  [the session so far, not the close]" if self.provisional else ""
        if self.newer_session is not None:
            mark += (
                f"  [the {self.close_day} close; {mic_of(self.instrument_id)} has since "
                f"closed {self.newer_session}]"
            )
        return (
            f"  {self.instrument_id:<12} {self.lot:>4} x {self.price_local:>10.4f} {self.currency}"
            f"  = USD {self.lot_usd:>8.2f}  ({self.lot_weight:.1%} of equity)"
            f"  round trip {self.round_trip:.2%}  {verdict}{mark}"
        )


def fundables(
    feed,
    cfg,
    equity: Decimal,
    quote: FxQuote,
    day: date,
    settings: PaperSettings,
    names: tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> list[Fundable]:
    """Each watchlist name at its last close: one lot in USD, and how many the cap allows.

    Each row also says when its close is OLDER than the latest session its
    market has finished by the end of `day` (or by `now`, when that is
    earlier). On 2026-09-22 the table priced every Bursa name at Monday's
    close under the heading "the last close" while Tuesday's session had
    closed eight hours earlier: the cache held no Tuesday bar, and nothing on
    the page could tell the reader so.
    """
    out: list[Fundable] = []
    end_of_day = datetime.combine(day, time.max, tzinfo=UTC)
    as_of = min(now or datetime.now(UTC), end_of_day)
    cap = settings.max_weight_per_name * equity
    for iid in names or tuple(cfg.watchlist):
        lot = lot_size(iid)
        ccy = currency_of(iid)
        try:
            close, close_day = last_close(feed, iid, day)
        except PriceFeedError as e:
            out.append(
                Fundable(
                    iid,
                    ccy,
                    lot,
                    Decimal(0),
                    None,
                    Decimal(0),
                    Decimal(0),
                    0,
                    Decimal(0),
                    str(e).splitlines()[0],
                )
            )
            continue
        price_usd = close / quote.rate if ccy == "MYR" else close
        lot_usd = price_usd * lot
        weight = lot_usd / equity if equity > 0 else Decimal(0)
        max_lots = int(cap // lot_usd) if lot_usd > 0 else 0
        rt = round_trip_pct(mic_of(iid), getattr(cfg, "broker", None), close * lot, close)
        state = price_state(mic_of(iid), close_day, _fetched_at(feed, iid))
        shut = last_session_close(mic_of(iid), as_of)
        newer = shut.date() if shut is not None and shut.date() > close_day else None
        out.append(
            Fundable(
                iid, ccy, lot, close, close_day, lot_usd, weight, max_lots, rt, "", state, newer
            )
        )
    return out


def _fetched_at(feed, instrument_id: str):
    """The feed's fetch instant, or None from a feed that does not keep one.

    Tolerant on purpose: the tests build feeds out of fixtures, and a fixture
    that cannot say when it was pulled should read `unknown` rather than break
    the fundable table it is not testing.
    """
    ask = getattr(feed, "fetched_at", None)
    if ask is None:
        return None
    try:
        return ask(instrument_id)
    except Exception:  # pragma: no cover - a feed that refuses is simply unknown
        return None


# -- validation ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Refusal:
    code: str
    message: str


def validate_targets(
    weights: dict[str, Decimal],
    *,
    state: BookState,
    current: dict[str, Decimal],
    equity: Decimal,
    fundable: list[Fundable],
    phase: Phase,
    settings: PaperSettings,
    halted: bool,
    pending_stops: set[str],
    turnover_used: Decimal,
    watchlist: tuple[str, ...],
) -> list[Refusal]:
    """Every breach at once. An empty list means the target book may be recorded."""
    out: list[Refusal] = []
    by_id = {f.instrument_id: f for f in fundable}
    max_w = settings.max_weight_per_name

    if phase.name == PRE:
        out.append(
            Refusal(
                "start", f"the book opens on {settings.start_date}; nothing is recorded before it"
            )
        )

    for iid, w in weights.items():
        if iid not in watchlist:
            out.append(
                Refusal(
                    "universe",
                    f"{iid} is not in the watchlist; the book chooses among the names you listed",
                )
            )
        if w < 0:
            out.append(
                Refusal(
                    "negative", f"{iid}: a negative weight is a short, and the book is long-only"
                )
            )
        if w > max_w:
            out.append(Refusal("per_name", f"{iid}: {w:.2%} exceeds the {max_w:.0%} per-name cap"))

    invested = sum((w for w in weights.values() if w > 0), Decimal(0))
    if phase.name == OBSERVE and invested > 0:
        # Recorded, graded, never applied - the rule is about application, so
        # an observe-week target book is validated against the ramp ceiling it
        # would face, and marked as observed rather than refused.
        ceiling = settings.ramp_max_invested
    else:
        ceiling = phase.max_invested
    if invested > ceiling and phase.name != PRE:
        out.append(
            Refusal(
                "invested",
                f"invested weight {invested:.2%} exceeds this phase's ceiling of {ceiling:.0%} ({phase.describe()})",
            )
        )

    n = sum(1 for w in weights.values() if w > 0)
    need = min_names(settings, invested)
    if 0 < n < need:
        out.append(
            Refusal(
                "names",
                f"{n} name(s) targeted; the rule in force is {names_rule(settings, invested)}",
            )
        )

    for iid, w in weights.items():
        if w <= 0:
            continue
        f = by_id.get(iid)
        if f is None or f.error:
            out.append(Refusal("price", f"{iid}: no cached close to size against"))
            continue
        if not f.fundable:
            out.append(
                Refusal(
                    "lot",
                    f"{iid}: one lot is USD {f.lot_usd:.2f} = {f.lot_weight:.1%} of equity, above the "
                    f"{max_w:.0%} cap; not fundable at this equity",
                )
            )
        elif w * equity < f.lot_usd:
            out.append(
                Refusal(
                    "lot",
                    f"{iid}: {w:.2%} is below one lot (USD {f.lot_usd:.2f} = {f.lot_weight:.1%} of equity)",
                )
            )

    if halted:
        raised = [iid for iid, w in weights.items() if w > current.get(iid, Decimal(0))]
        if raised:
            out.append(
                Refusal(
                    "halt",
                    f"drawdown from peak is at or past {settings.drawdown_halt:.0%}: no new entries; "
                    f"reductions only. Raised: {', '.join(raised)}",
                )
            )

    for iid in sorted(pending_stops):
        if weights.get(iid, Decimal(0)) > 0:
            out.append(
                Refusal(
                    "stop",
                    f"{iid}: a stop is pending; it cannot be targeted above zero until it has exited",
                )
            )

    # Only increases count against the cap at decision time: a reduction or a
    # stop is never blocked by turnover (docs/22 section 5), though every
    # change counts toward the window once it has happened.
    increases = sum(
        (
            max(Decimal(0), weights.get(iid, Decimal(0)) - current.get(iid, Decimal(0))) * equity
            for iid in set(weights) | set(current)
        ),
        Decimal(0),
    )
    cap_usd = settings.weekly_turnover_cap * equity
    if increases > 0 and turnover_used + increases > cap_usd:
        out.append(
            Refusal(
                "turnover",
                f"turnover: USD {turnover_used:.2f} changed in the last five weekdays plus "
                f"USD {increases:.2f} of increases proposed exceeds the cap of USD {cap_usd:.2f} "
                f"({settings.weekly_turnover_cap:.0%} of equity); reductions are not counted here",
            )
        )
    return out
