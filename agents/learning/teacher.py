"""A14, the teacher. P14.

docs/02 A14 and docs/06 section 5. The system's stated purpose is to teach the
user to become an investor, not to hand them answers. So the teacher is the one
agent whose success is measured by the user needing it less.

Two design rules make that real:
  1. A concept cannot be taught before its prerequisites. The graph is checked,
     not suggested - explaining Kelly to someone who has not met expected value
     produces confident nonsense downstream.
  2. Licence tiers govern what may be quoted. Textbook material is `link_only`;
     the teacher explains in its own words and points at the source. It never
     reproduces a chapter (docs/06 section 7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from agents.base import Agent, Finding
from core.llm.tiers import TaskClass


class Level(int, Enum):
    L1_MONEY = 1          # what a share is, what a market is
    L2_STATEMENTS = 2     # reading the three statements
    L3_VALUATION = 3      # what a business is worth and why ranges beat points
    L4_PRICE = 4          # what price action can and cannot tell you
    L5_NEWS = 5           # why the stock moved, and why most explanations are wrong
    L6_RISK = 6           # position sizing, correlation, ruin
    L7_PORTFOLIO = 7      # the book as one object
    L8_PROCESS = 8        # journals, base rates, calibration, changing your mind


class Licence(str, Enum):
    OPEN = "open"                 # may be quoted in full
    ATTRIBUTED = "attributed"     # may be quoted with attribution
    LINK_ONLY = "link_only"       # title and URL only, never the body


@dataclass(frozen=True)
class Concept:
    key: str
    level: Level
    title: str
    one_line: str
    requires: tuple[str, ...] = ()
    misconception: str = ""
    check: str = ""               # the question that proves it landed
    sources: tuple[tuple[str, str, Licence], ...] = ()

    def citable_sources(self) -> list[tuple[str, str, Licence]]:
        return [s for s in self.sources if s[2] is not Licence.LINK_ONLY]


#: The curriculum. Small on purpose - eight levels the user can actually finish
#: beats forty they abandon. Every entry names the misconception it exists to kill.
CURRICULUM: tuple[Concept, ...] = (
    Concept("share", Level.L1_MONEY, "What a share actually is",
            "A share is a fractional claim on a real business's future cash, not a ticket whose price is the point.",
            misconception="Treating the ticker as the asset. The business is the asset; the ticker is a queue of opinions about it.",
            check="If the exchange closed for five years, how would you decide what your holding is worth?"),
    Concept("market_maker", Level.L1_MONEY, "Who is on the other side",
            "Every trade has a counterparty who thinks the opposite, and one of you is wrong.",
            requires=("share",),
            misconception="'The market' is a thing with intentions. It is a crowd of people with different horizons and different constraints.",
            check="Name three reasons someone would sell a stock they still think is cheap."),
    Concept("compounding", Level.L1_MONEY, "Compounding and the cost of interruption",
            "Returns multiply; a single large loss is not offset by an equal-sized gain.",
            requires=("share",),
            misconception="Down 50% then up 50% gets you back. It leaves you 25% down.",
            check="What gain is needed to recover from a 40% drawdown?"),

    Concept("income_statement", Level.L2_STATEMENTS, "The income statement",
            "Revenue to profit, with every subtraction a management choice worth questioning.",
            requires=("share",),
            misconception="Profit is cash. It is an accounting opinion about timing."),
    Concept("cash_flow", Level.L2_STATEMENTS, "Cash flow and why it outranks earnings",
            "Cash from operations is the hardest number in the statements to fake.",
            requires=("income_statement",),
            misconception="Growing earnings mean a healthy company. Earnings rising while operating cash flow falls is the single most reliable warning sign there is.",
            check="A company reports record profit and negative operating cash flow for the third year. What are you looking at?"),
    Concept("balance_sheet", Level.L2_STATEMENTS, "The balance sheet",
            "What is owned, what is owed, and how long before the owing comes due.",
            requires=("income_statement",),
            misconception="Debt is bad. Debt is a maturity schedule; the question is always when, not whether."),
    Concept("restatement", Level.L2_STATEMENTS, "Restatements and point-in-time truth",
            "The figure you see today is not the figure the market saw then.",
            requires=("cash_flow",),
            misconception="Historical data is fixed. It is revised, and backtests that use revised data are lying to you.",
            check="Why does this system store known_at alongside period_end?"),

    Concept("intrinsic_value", Level.L3_VALUATION, "Value as discounted cash",
            "A business is worth the cash it will produce, discounted for time and uncertainty.",
            requires=("cash_flow", "compounding"),
            misconception="A DCF gives you the answer. It gives you a way to see which assumption you are actually betting on."),
    Concept("multiples", Level.L3_VALUATION, "Multiples are shorthand, not analysis",
            "A P/E is a DCF with every assumption hidden inside one number.",
            requires=("intrinsic_value",),
            misconception="Low P/E means cheap. It usually means the market expects earnings to fall, and the market is often right."),
    Concept("reverse_dcf", Level.L3_VALUATION, "Reverse DCF: what is priced in",
            "Instead of guessing the future, solve for the growth the current price already requires.",
            requires=("intrinsic_value", "multiples"),
            misconception="You need a forecast to value something. You need to know what forecast you are being asked to believe.",
            check="The price implies 18% growth for ten years. What is your job now?"),
    Concept("range_not_point", Level.L3_VALUATION, "Ranges beat point targets",
            "A single price target hides the uncertainty that is the whole subject.",
            requires=("reverse_dcf",),
            misconception="A precise target shows rigour. It shows the opposite."),

    Concept("liquidity", Level.L4_PRICE, "Liquidity and what your own order does",
            "Above a few percent of daily volume, you are not taking the price, you are making it.",
            requires=("market_maker",),
            misconception="The quoted price is the price you get. It is the price for a small order right now."),
    Concept("volatility", Level.L4_PRICE, "Volatility as a measurement, not a mood",
            "Volatility is the size of typical moves; it says nothing about direction.",
            requires=("compounding",),
            misconception="High volatility means risk of loss. It means dispersion; the risk is in the sizing, not the number."),
    Concept("trend_vs_noise", Level.L4_PRICE, "Trend, mean reversion, and noise",
            "Most short-horizon price movement carries no information at all.",
            requires=("volatility",),
            misconception="Every move has a reason. Most moves are the market's normal variation and naming a cause for them is storytelling.",
            check="A stock is down 1.2% on a day the index is down 1.4%. What happened to the company?"),

    Concept("factor_decomposition", Level.L5_NEWS, "Splitting a move before explaining it",
            "Market, sector, style and currency first; only the residual needs a story.",
            requires=("trend_vs_noise", "volatility"),
            misconception="The headline explains the move. The headline is usually explaining the market's move, attributed to one stock.",
            check="Of a 6.0% fall, 4.7 points were market and sector. What is left to explain?"),
    Concept("base_rate", Level.L5_NEWS, "Base rates over narratives",
            "How this event type has resolved historically beats how this instance feels.",
            requires=("factor_decomposition",),
            misconception="This time is different. Sometimes it is; the base rate tells you how often that claim has been true."),
    Concept("catalyst_quality", Level.L5_NEWS, "What makes a cause credible",
            "Proximity, specificity, direction, magnitude and source trust - all five, or it is a coincidence.",
            requires=("base_rate",),
            misconception="A news story published the same day is the cause. Same-day is one weak input out of six."),
    Concept("no_catalyst", Level.L5_NEWS, "The honest answer: no identified catalyst",
            "A significant unexplained move is a real finding, and historically tends to reverse more often than a news-driven one continues.",
            requires=("catalyst_quality",),
            misconception="Not knowing means you failed. Inventing a reason is the failure."),

    Concept("expected_value", Level.L6_RISK, "Expected value and edge",
            "Win rate alone is meaningless without payoff; both together are still an estimate.",
            requires=("compounding", "base_rate"),
            misconception="A high win rate means a good strategy. Nine small wins and one large loss is a losing strategy."),
    Concept("position_sizing", Level.L6_RISK, "Sizing bounds the loss, not the position",
            "Decide what you can lose if the stop fills, then work backwards to the size.",
            requires=("expected_value", "volatility"),
            misconception="Sizing is about conviction. Sizing is about survival; conviction is the input that gets people ruined.",
            check="Portfolio RM100,000, risk 0.75% per trade, stop 12% below entry. Maximum position?"),
    Concept("kelly", Level.L6_RISK, "Kelly, and why a quarter of it",
            "Full Kelly maximises growth only if your edge estimate is exact, which it never is.",
            requires=("position_sizing",),
            misconception="Kelly tells you how much to bet. It tells you the ceiling above which you are certainly overbetting."),
    Concept("ruin", Level.L6_RISK, "Risk of ruin and path dependence",
            "You do not get the average outcome; you get one path, and it can end.",
            requires=("kelly",),
            misconception="Positive expectancy makes you safe. Positive expectancy with too much size still goes to zero."),

    Concept("correlation", Level.L7_PORTFOLIO, "Correlation is the hidden concentration",
            "Ten names that move together is one position wearing ten costumes.",
            requires=("ruin",),
            misconception="More holdings means diversified. Effective number of bets, not position count, is the measure.",
            check="Ten equally weighted banks, all correlated 0.8. How many bets do you have?"),
    Concept("effective_bets", Level.L7_PORTFOLIO, "Effective number of bets",
            "The count of genuinely independent exposures, which is always lower than it looks.",
            requires=("correlation",),
            misconception="HHI is enough. HHI sees weights; it cannot see that every weight is in the same trade."),
    Concept("portfolio_heat", Level.L7_PORTFOLIO, "Total heat across open positions",
            "Each stop is survivable; all stops filling in one week is the scenario that matters.",
            requires=("correlation", "position_sizing"),
            misconception="Per-trade risk is the constraint. The constraint is the sum of them in a correlated selloff."),
    Concept("currency", Level.L7_PORTFOLIO, "Currency is a position you did not choose",
            "Holding a foreign stock is two bets: the company, and the exchange rate.",
            requires=("effective_bets",),
            misconception="A US stock up 10% made you 10%. In ringgit it may have made you 3% or 17%."),

    Concept("journal", Level.L8_PROCESS, "Writing the thesis before the entry",
            "A thesis written after the trade is a memory, and memory edits itself.",
            requires=("portfolio_heat",),
            misconception="You will remember why you bought it. You will remember a version that makes you look better."),
    Concept("breakers", Level.L8_PROCESS, "Breakers: deciding now how you will be proven wrong",
            "Two to four checkable conditions that, if they occur, end the position without a new debate.",
            requires=("journal",),
            misconception="Discipline is willpower. Discipline is a decision made before the money was at stake.",
            check="Write two breakers for a thesis that a retailer's margins will recover."),
    Concept("calibration", Level.L8_PROCESS, "Calibration: are your 70%s actually 70%?",
            "Track stated confidence against realised outcomes, in bands.",
            requires=("breakers", "base_rate"),
            misconception="Being right often means being well calibrated. Being right at the rate you claimed is what counts."),
    Concept("changing_mind", Level.L8_PROCESS, "Changing your mind cheaply",
            "New evidence should move a view by an amount proportional to its weight, not trigger a defence.",
            requires=("calibration",),
            misconception="Consistency is integrity. Updating slowly on strong evidence is the expensive habit."),
)

BY_KEY: dict[str, Concept] = {c.key: c for c in CURRICULUM}


class PrerequisiteError(Exception):
    """Teaching out of order produces confident nonsense downstream."""


def prerequisites(key: str, seen: set[str] | None = None) -> list[str]:
    """Full transitive closure, in teachable order."""
    seen = seen if seen is not None else set()
    c = BY_KEY.get(key)
    if c is None:
        raise KeyError(f"{key!r} is not in the curriculum")
    out: list[str] = []
    for r in c.requires:
        if r in seen:
            continue
        seen.add(r)
        out.extend(prerequisites(r, seen))
        out.append(r)
    return out


def validate_graph() -> None:
    """No dangling edges, no cycles, no level inversions. Run at import time in
    tests, because a broken curriculum fails silently at teach time."""
    for c in CURRICULUM:
        for r in c.requires:
            if r not in BY_KEY:
                raise PrerequisiteError(f"{c.key} requires unknown concept {r!r}")
            if BY_KEY[r].level > c.level:
                raise PrerequisiteError(
                    f"{c.key} (L{c.level.value}) requires {r} from a later level "
                    f"L{BY_KEY[r].level.value}"
                )
    for c in CURRICULUM:
        prerequisites(c.key)          # recursion depth guards against cycles


@dataclass
class Learner:
    """What the user has actually demonstrated, not what they were shown."""

    known: set[str] = field(default_factory=set)
    level: Level = Level.L1_MONEY

    def mastered(self, key: str) -> None:
        """Record a demonstrated concept. An unknown key is refused HERE.

        It used to be accepted and then raised a bare KeyError from `level` on
        the next call - a failure at a place with no idea what was mis-typed.
        Worse, a mis-typed prerequisite also reads as unmet forever, so the
        learner is told to go back to a concept they have already done.
        """
        if key not in BY_KEY:
            raise KeyError(
                f"{key!r} is not in the curriculum; mastery cannot be recorded for a "
                f"concept that does not exist ({len(CURRICULUM)} concepts, L1-L8)"
            )
        self.known.add(key)
        highest = max((BY_KEY[k].level for k in self.known), default=Level.L1_MONEY)
        self.level = highest


class A14Teacher(Agent):
    """Explains in its own words, in order, and checks that it landed."""

    agent_id = "a14_teacher"
    collections = ("kb_craft",)
    tools = ("retrieve", "explain", "next_concept", "quiz")
    tier = TaskClass.ADHOC_QUERY

    def run(self, key: str, learner: Learner | None = None) -> list[Finding]:
        self._guard_tool("explain")
        learner = learner or Learner()
        c = BY_KEY.get(key)
        if c is None:
            return [Finding(self.agent_id, "unknown_concept",
                            f"{key!r} is not in the curriculum",
                            caveats=[f"available levels L1-L8, {len(CURRICULUM)} concepts"])]

        missing = [p for p in prerequisites(key) if p not in learner.known]
        if missing:
            first = BY_KEY[missing[0]]
            return [Finding(
                self.agent_id, "prerequisite",
                f"before {c.title.lower()}, {first.title.lower()} has to come first: {first.one_line}",
                numbers={"missing": float(len(missing))},
                caveats=[f"teaching order: {' -> '.join(missing + [key])}"],
            )]

        out = [Finding(self.agent_id, "explain", f"{c.title}. {c.one_line}")]
        if c.misconception:
            out.append(Finding(self.agent_id, "misconception",
                               f"The common error: {c.misconception}"))
        if c.check:
            out.append(Finding(self.agent_id, "check", c.check,
                               caveats=["answer this before the concept counts as known"]))
        for title, url, lic in c.sources:
            if lic is Licence.LINK_ONLY:
                out.append(Finding(self.agent_id, "further_reading",
                                   f"{title} - {url}",
                                   caveats=["licence permits the link only; the text is "
                                            "not reproduced here"]))
            else:
                out.append(Finding(self.agent_id, "further_reading", f"{title} - {url}"))
        return out

    def next_concept(self, learner: Learner) -> Finding:
        """The next thing they can actually learn, not the next thing on a list."""
        self._guard_tool("next_concept")
        for c in CURRICULUM:
            if c.key in learner.known:
                continue
            if all(r in learner.known for r in c.requires):
                return Finding(self.agent_id, "next",
                               f"next: {c.title} (L{c.level.value}) - {c.one_line}")
        return Finding(self.agent_id, "next",
                       "the curriculum is complete; from here the teaching is the journal "
                       "and the calibration table")

    def syllabus(self, learner: Learner | None = None) -> list[Finding]:
        learner = learner or Learner()
        out = []
        for level in Level:
            items = [c for c in CURRICULUM if c.level is level]
            done = sum(1 for c in items if c.key in learner.known)
            out.append(Finding(
                self.agent_id, "syllabus",
                f"L{level.value} {level.name.split('_', 1)[1].lower()}: {done}/{len(items)}",
                numbers={"done": float(done), "total": float(len(items))},
            ))
        return out
