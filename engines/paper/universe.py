"""The index book's universe: which names a whole-market benchmark holds.

A YAML list (engines/paper/data/fbm100.yaml), because an index is a published
list someone reviews twice a year, not a rule to be computed. Loading it
refuses what would make the benchmark silently smaller or wrong - a duplicate
code, an id with no market, an empty list - and says nothing about whether
each code is the company it names. That is `check`'s job, and it can only be
done from what a price source printed beside the bars (`listed_name`).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

DEFAULT_UNIVERSE = Path(__file__).parent / "data" / "fbm100.yaml"

#: The verdicts `check` gives a member. Only DISAGREES keeps a name out of the
#: book: UNVERIFIED means no source has printed a name yet, which is the state
#: of every member until the collector's first fetch, and holding nothing until
#: then would make the benchmark wait on a check the bars do not need.
AGREES = "agrees"
DISAGREES = "DISAGREES"
UNVERIFIED = "unverified"

#: Words every Bursa listing carries in one spelling or another and that say
#: nothing about which company it is.
_NOISE = frozenset(
    {
        "bhd",
        "berhad",
        "holdings",
        "holding",
        "hldgs",
        "group",
        "grp",
        "corporation",
        "corp",
        "company",
        "co",
        "limited",
        "ltd",
        "plc",
        "inc",
        "the",
        "m",
        "and",
    }
)


@dataclass(frozen=True)
class Member:
    instrument_id: str
    name: str
    segment: str = ""


@dataclass(frozen=True)
class Universe:
    path: str
    as_of: date | None
    source: str
    review: str | None
    members: tuple[Member, ...]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(m.instrument_id for m in self.members)

    def describe(self) -> str:
        review = f", review {self.review}" if self.review else ""
        return f"{len(self.members)} names as of {self.as_of} ({self.source}{review})"


def load_universe(path: str | Path | None = None) -> Universe:
    p = Path(path) if path else DEFAULT_UNIVERSE
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"{p}: not YAML: {str(e).splitlines()[0]}") from e
    if not isinstance(raw, dict):
        raise ValueError(f"{p}: expected a mapping with a `names` list")
    rows = raw.get("names") or []
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{p}: `names` is empty; an index of nothing benchmarks nothing")
    members: list[Member] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not row.get("id") or not row.get("name"):
            raise ValueError(f"{p}: entry {i} needs an `id` and a `name`, got {row!r}")
        iid = str(row["id"]).strip()
        market, _, code = iid.partition(":")
        if not market or not code:
            raise ValueError(f"{p}: entry {i} id {iid!r} has no market prefix, e.g. MYX:1155")
        if iid in seen:
            raise ValueError(f"{p}: {iid} is listed twice; an equal-weight book would double it")
        seen.add(iid)
        members.append(Member(iid, str(row["name"]).strip(), str(row.get("segment") or "")))
    as_of = raw.get("as_of")
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)
    return Universe(
        path=str(p),
        as_of=as_of if isinstance(as_of, date) else None,
        source=str(raw.get("source") or "unstated"),
        review=str(raw["review"]) if raw.get("review") else None,
        members=tuple(members),
    )


def _letters(name: str) -> str:
    """Lowercase ASCII, punctuation gone, the listing noise gone, spaces closed."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split()
    return "".join(w for w in words if w not in _NOISE)


def _subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(ch in it for ch in short)


def name_verdict(expected: str, listed: str | None) -> str:
    """Whether the name a source printed can be the company the list expected.

    Lenient on purpose. Yahoo prints "MALAYAN BANKING BHD" where a list says
    "Malayan Banking", "Nestle (Malaysia) Berhad" where it says "Nestlé
    (Malaysia)", and "MR D.I.Y. GROUP (M) BHD" where it says "MR D.I.Y. Group":
    the test is that one name's letters run, in order, through the other's,
    starting with the same letter. What it catches is a code that belongs to
    another company altogether, which is the error a list written from memory
    makes. What it lets through is a sibling with a longer name ("Genting" for
    Genting Malaysia) - so the verdict is a floor, not a proof, and the page
    prints both names beside it.
    """
    if not listed:
        return UNVERIFIED
    a, b = _letters(expected), _letters(listed)
    if not a or not b:
        return UNVERIFIED
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return AGREES if short[0] == long[0] and _subsequence(short, long) else DISAGREES


@dataclass(frozen=True)
class MemberCheck:
    member: Member
    listed: str | None
    verdict: str

    @property
    def excluded(self) -> bool:
        return self.verdict == DISAGREES


def check(universe: Universe, feed) -> list[MemberCheck]:
    """Each member against the name its price source printed, from the cache."""
    ask = getattr(feed, "listed_name", None)
    out: list[MemberCheck] = []
    for m in universe.members:
        try:
            listed = ask(m.instrument_id) if ask is not None else None
        except Exception:  # a feed that cannot answer has printed nothing
            listed = None
        out.append(MemberCheck(m, listed, name_verdict(m.name, listed)))
    return out
