"""Entity linking: surface form -> instrument id, with word boundaries.

`link_entities` used to be `surface.lower() in text.lower()`. That is the
cheapest possible linker and it is wrong in exactly the way that poisons a
corpus quietly: "Intel" matched "intelligence", "AMD" matched "Amdocs", "MISC"
matched "miscellaneous" and "Apple" matched "pineapple". Every one of those
produced an article attributed to a company it never mentioned, an INFERRED
graph edge from a story about something else, and a relevance score that let
it through the escalation gate. Nothing crashed. The corpus simply filled with
confident, wrong attributions - measured on 2026-09-04: 4 of 4 "Intel" links
in the corpus were the word intelligence.

The rule set here is small and deliberate, and every branch has a test:

  * **CJK and other non-ASCII aliases** match as substrings, case-insensitively.
    Chinese has no word boundaries, so `\\b` would never fire; "英伟达" inside a
    Chinese headline is the mention.
  * **Short all-caps aliases (<= 4 characters)** are acronyms - AMD, TNB, IHH,
    MISC, CIMB - and match case-SENSITIVELY as whole words. "misc." and "amd"
    in running text are not the companies.
  * **Single Titlecase words** - Apple, Tenaga, Intel, Genting, Maybank - match
    as whole words in Titlecase or ALL CAPS, never lowercase. "tenaga" is the
    Malay word for energy; "Tenaga" is the utility. "apple" is a fruit.
  * **Everything else** (multi-word names, mixed case) matches as a whole
    phrase, case-insensitively: "Malayan Banking Berhad", "Apple Inc", "Nvidia
    Corporation".

  * **No ASCII alias matches when a thoroughfare word follows it** - see
    THOROUGHFARE. "Maybank Highway" is a road in Charleston, and the story about
    it closing was linked to Malayan Banking and escalated as bank news.

A word boundary here is "not preceded or followed by an ASCII letter or
digit", which is stricter than `\\b`: it keeps "Apple's" and "Maybank," while
refusing "pineapple" and "Amdocs".

  * **A shorter alias never fires inside a longer alias's match** - see
    EntityLinker.link and OTHER_NAMES. "Genting" is Genting Berhad (MYX:3182),
    and until 2026-10-10 it also matched inside "Genting Malaysia" (MYX:4715),
    "Genting Singapore", "Genting Plantations" and "Genting Highlands", so
    stories about other listed companies and a mountain resort filled Genting
    Berhad's slice of the corpus, and some were escalated.

Longest alias first, as before, so "Maybank Islamic" resolves to the subsidiary
and the "Maybank" inside it is spent on that match, not linked to the parent.
The parent is linked when the text names it separately.
"""

from __future__ import annotations

import re
from functools import lru_cache

_ASCII_WORD = r"[A-Za-z0-9]"
#: Characters outside ASCII letters/digits/space/punctuation: CJK, Arabic,
#: accented Latin. Any alias carrying one is matched as a plain substring.
_NON_ASCII = re.compile(r"[^\x00-\x7F]")

ACRONYM_MAX_CHARS = 4
INFLECTION_MAX_CHARS = 2

#: A company's name followed by one of these is a PLACE named after it, not the
#: company: "Maybank Highway at Main Road closed after early morning multi-
#: vehicle crash" was linked to MYX:1155 and escalated to the review queue as
#: Maybank news. Matched case-SENSITIVELY even inside the case-insensitive
#: patterns, because these are proper-name components: "Maybank Highway" is a
#: road, "interchange fees" is banking.
#:
#: The list is thoroughfares ONLY, and the exclusions are the point. "Park",
#: "Tower", "Plaza", "Centre", "Stadium" and "Arena" are things a company names
#: after ITSELF - Apple Park, Maybank Tower - so a story about one usually IS
#: about the company. "Drive", "Lane", "Bridge" and "Interchange" are ordinary
#: English or product vocabulary ("NVLink Bridge") and would cost real mentions.
#: Measured over all 1,342 collected articles: one alias is followed by any of
#: the words above, and it is the road closure. Nothing legitimate is lost.
THOROUGHFARE = (
    "Highway",
    "Freeway",
    "Expressway",
    "Parkway",
    "Boulevard",
    "Avenue",
    "Street",
    "Road",
    "Roundabout",
)
_NOT_A_PLACE = rf"(?!\s+(?-i:(?:{'|'.join(THOROUGHFARE)}))(?!{_ASCII_WORD}))"

#: Longer names that CONTAIN a book alias and name something else: the group's
#: other listed companies and the resort named after it. Each is matched like
#: an alias and consumes its span, so the "Genting" inside it is not Genting
#: Berhad, but links to nothing itself - "Genting Singapore beats second-quarter
#: expectations" was escalated as MYX:3182 news on the live corpus. A name that
#: IS in the entity index (as "Genting Malaysia" is, MYX:4715) is linked to its
#: own instrument instead; adding "Genting Plantations" to entities.yaml is
#: enough to make that one link to MYX:2291.
OTHER_NAMES = (
    "Genting Singapore",
    "Genting Plantations",
    "Genting Highlands",
    "Resorts World Genting",
)


def alias_pattern(surface: str) -> re.Pattern[str]:
    """The compiled matcher for one alias, per the rules in the module doc."""
    surface = surface.strip()
    if not surface:
        raise ValueError("an empty alias matches everything and nothing")
    escaped = re.escape(surface)
    if _NON_ASCII.search(surface):
        return re.compile(escaped, re.IGNORECASE)

    bounded = rf"(?<!{_ASCII_WORD}){{}}(?!{_ASCII_WORD}){_NOT_A_PLACE}"
    is_one_word = " " not in surface
    if is_one_word and surface.isupper() and len(surface) <= ACRONYM_MAX_CHARS:
        return re.compile(bounded.format(escaped))  # case-sensitive acronym
    if is_one_word and surface[0].isupper() and surface[1:].islower() and surface.isalpha():
        # Titlecase proper noun: Titlecase or ALL CAPS, never lowercase. An
        # inflectional tail of up to two lowercase letters is allowed on names
        # of five letters or more - "Microsofts" (Danish genitive), "Applea"
        # (Croatian) - measured on the live corpus as the only true mentions
        # the strict boundary lost. Two letters, not more: "Intel" + "ligence"
        # is seven, and that is the false positive this module exists to stop.
        tail = f"[a-z]{{0,{INFLECTION_MAX_CHARS}}}" if len(surface) >= 5 else ""
        forms = f"(?:{escaped}{tail}|{re.escape(surface.upper())})"
        return re.compile(bounded.format(forms))
    return re.compile(bounded.format(escaped), re.IGNORECASE)


class EntityLinker:
    """A compiled index. Build once per process; `link` is then cheap."""

    def __init__(self, index: dict[str, str]) -> None:
        # Longest first so a subsidiary's longer name wins over its parent's.
        # OTHER_NAMES ride along with no instrument (''), unless the index
        # names them, in which case the index entry is the one kept.
        named = {**{n: "" for n in OTHER_NAMES}, **index}
        self._entries: tuple[tuple[str, str, re.Pattern[str]], ...] = tuple(
            (surface, iid, alias_pattern(surface))
            for surface, iid in sorted(named.items(), key=lambda kv: (-len(kv[0]), kv[0]))
            if surface.strip()
        )
        self._surfaces: dict[str, list[str]] = {}
        for surface, iid, _ in self._entries:
            if iid:
                self._surfaces.setdefault(iid, []).append(surface)

    def _matches(self, text: str) -> list[tuple[int, int, str]]:
        """(start, alias order, iid) for every match no longer match contains.

        Aliases are tried longest first and each accepted match claims its
        span; a shorter alias whose match overlaps a claimed span is the same
        words read twice. Searching each alias on its own, as this did until
        2026-10-10, linked "Genting Malaysia posts higher quarterly revenue"
        to Genting Malaysia AND Genting Berhad. Every occurrence is checked,
        not just the first, so "Genting Malaysia ... Genting Berhad" still
        links both. A match of an OTHER_NAMES entry claims its span and links
        nothing.
        """
        claimed: list[tuple[int, int]] = []
        out: list[tuple[int, int, str]] = []
        for order, (_surface, iid, pattern) in enumerate(self._entries):
            for m in pattern.finditer(text):
                start, end = m.span()
                if any(start < e and s < end for s, e in claimed):
                    continue
                claimed.append((start, end))
                if iid:
                    out.append((start, order, iid))
        return out

    def link(self, text: str) -> list[str]:
        """Instrument ids the text names, in order of first mention.

        A name inside a longer name is not a mention: "Maybank Islamic" is the
        subsidiary alone, and the parent is listed only where the text names
        it separately. Ties at the same position keep alias order.
        """
        first_at: dict[str, tuple[int, int]] = {}
        for start, order, iid in self._matches(text):
            key = (start, order)
            if iid not in first_at or key < first_at[iid]:
                first_at[iid] = key
        return [iid for iid, _ in sorted(first_at.items(), key=lambda kv: kv[1])]

    def mentions(self, text: str, instrument_ids: list[str]) -> int:
        """How many times any alias of the given instruments occurs, counting
        only matches no longer alias contains, the same reading as `link`."""
        wanted = set(instrument_ids)
        return sum(1 for _start, _order, iid in self._matches(text) if iid in wanted)

    def surfaces(self, instrument_id: str) -> list[str]:
        """Every alias of one instrument, longest first."""
        return list(self._surfaces.get(instrument_id, []))

    def names_for(self, instrument_ids: list[str]) -> list[str]:
        """The surface forms of several instruments - what a text feature
        extractor should be handed instead of the ids, which never appear in
        prose and therefore scored every article's relevance as zero."""
        out: list[str] = []
        for iid in instrument_ids:
            out.extend(self.surfaces(iid) or [iid])
        return out


@lru_cache(maxsize=8)
def _cached(items: tuple[tuple[str, str], ...]) -> EntityLinker:
    return EntityLinker(dict(items))


def linker_for(index: dict[str, str]) -> EntityLinker:
    """A linker for this index, compiled once per distinct index."""
    return _cached(tuple(sorted(index.items())))


def link_entities(text: str, index: dict[str, str]) -> list[str]:
    """Surface form -> instrument_id, longest match first. Whole words only."""
    if not index:
        return []
    return linker_for(index).link(text)
