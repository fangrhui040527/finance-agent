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
    it closing was linked to Malayan Banking and escalated as bank news. Malay
    puts the noun first, so a Malay building name is read from the other side -
    see MALAY_BUILDING.

  * **No short alias matches when a corporate suffix follows it** - see
    CORPORATE_SUFFIX. "Shree TNB Polymers" is an Indian listing, not Tenaga.

  * **No alias matches inside a fund's name** - see _IN_A_FUNDS_NAME. "FAST
    Maybank ACI Opp ... (0P0001STK3)" is a fund's quote page, not the bank.

  * **No alias matches behind another market's exchange code** - see
    OTHER_EXCHANGES. "UPCOM:TNB" is Nha Be Steel on Vietnam's UPCoM.

  * **No alias matches inside a name, or near a word, entities.yaml says is
    not the company** - see NOT_THE_COMPANY. "Apple Hospitality" is a REIT.

  * **Stored links are re-read by these rules** - see EntityLinker.recheck. A
    link is written when a row is stored, and a rule added later would
    otherwise never reach the rows before it.

A word boundary here is "not preceded or followed by an ASCII letter or
digit", which is stricter than `\\b`: it keeps "Apple's" and "Maybank," while
refusing "pineapple" and "Amdocs".

  * **A shorter alias never fires inside a longer alias's match** - see
    EntityLinker.link and OTHER_NAMES. "Genting" is Genting Berhad (MYX:3182),
    and until 2026-10-10 it also matched inside "Genting Malaysia" (MYX:4715),
    "Genting Singapore", "Genting Plantations" and "Genting Highlands", so
    stories about other listed companies and a mountain resort filled Genting
    Berhad's slice of the corpus, and some were escalated.

  * **A bank named as the AUTHOR of a broker's note is still linked**, and
    `EntityLinker.authored` says it is only the author - see `_writes_the_note`.
    "Maybank keeps THB 75.00 target for Airports of Thailand stock" mentions the
    bank and is news about the airport operator; the digest reads the
    difference.

  * **Another company's contract win, and a crash involving the company's
    lorry, are linked**, and `EntityLinker.incidental` says which kind of not-
    news they are - MENTION or INCIDENT_BYSTANDER, never a cause.

Longest alias first, as before, so "Maybank Islamic" resolves to the subsidiary
and the "Maybank" inside it is spent on that match, not linked to the parent.
The parent is linked when the text names it separately.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
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

#: Malay names a building generic-noun FIRST - "Menara" (tower), "Wisma",
#: "Bangunan" (building), "Kompleks" - so THOROUGHFARE, which reads the word
#: AFTER an alias, never sees one. "The marriage was solemnised ... at Menara
#: Merdeka Maybank" (fmt_business, 2026-10-10, a wedding) was linked to MYX:1155
#: on that last word alone. An alias preceded by one of these AND another name
#: word is the tail of that building's name. Directly after the noun it is NOT
#: excluded: "Menara Maybank" is Maybank Tower, the company's own building,
#: which THOROUGHFARE keeps for the reason it keeps "Tower". A story about the
#: company held at the venue names the company again and is linked on that.
#: Measured over the 19,330 stored rows to 2026-10-16: this fires once, on the
#: wedding.
MALAY_BUILDING = ("Menara", "Wisma", "Bangunan", "Kompleks")
#: Title Case or ALL CAPS, as a headline sets it ("AT MENARA MERDEKA MAYBANK"),
#: never lowercase - the same reading the aliases themselves get.
_BUILDING_NOUNS = "|".join(MALAY_BUILDING + tuple(w.upper() for w in MALAY_BUILDING))
_IN_A_BUILDING_NAME = re.compile(
    rf"(?<!{_ASCII_WORD})(?:{_BUILDING_NOUNS})(?:\s+[A-Z0-9][\w'’-]*){{1,2}}\s+\Z"
)

#: A SHORT alias followed by one of these is the first word of ANOTHER
#: company's registered name: "Shree TNB Polymers Limited" (BSE:SHREETNB) is an
#: Indian IPO, and 21 rows about it were linked to Tenaga (MYX:5347) from
#: 2026-09-24. The 2026-10-10 span rule did not stop them - it spends an alias
#: inside a longer name only when somebody LISTED that name, and nobody lists
#: every company whose initials collide with a book name. Three or four capitals
#: are cheap to share ("AMD Industries" is another BSE listing); a full name is
#: rarely reused, and where one is, OTHER_NAMES lists it.
#:
#: Matched case-sensitively, like THOROUGHFARE. Left out on purpose: the words
#: a company names its OWN arms and legal form with - "IHH Healthcare", "CIMB
#: Group", "CIMB Securities", "MISC Bhd", "TNB Electron", "IHH Consultants"
#: (its panel doctors) - which are the company, or are in the index already.
#: "Preceded by another proper noun" ("Shree TNB") is NOT a rule here, though it
#: was the first idea: in Title Case headlines the true mentions look the same -
#: "Why AMD", "How TNB Electron", "Could Undermine TNB And" - 79 of them in the
#: stored rows. Measured over the 19,330 stored rows to 2026-10-16: an acronym
#: alias is followed by one of these 21 times, every one Shree TNB Polymers.
CORPORATE_SUFFIX = (
    "Polymers",
    "Plastics",
    "Industries",
    "Textiles",
    "Pharma",
    "Limited",
    "Ltd",
    "Pvt",
)
#: Title Case or ALL CAPS: "SHREE TNB POLYMERS LIMITED" is the same company.
_SUFFIXES = "|".join(CORPORATE_SUFFIX + tuple(w.upper() for w in CORPORATE_SUFFIX))
_NOT_ANOTHER_COMPANY = rf"(?!\s+(?:{_SUFFIXES})(?!{_ASCII_WORD}))"

#: A fund's quote page names the FUND, and a fund is often named after the bank
#: that runs or sells it: "Fidelity FAST Maybank ACI OppAMCDIST(G)$ (0P0001STK3)
#: Interactive Stock Chart" (ca.finance.yahoo.com, 2026-10-16) was linked to
#: MYX:1155. Such a page closes the name with the fund's own identifier in
#: parentheses - a Morningstar id ("0P" and eight more) or a mutual-fund code
#: ending ".MF" - and an alias that identifier closes within the same name is a
#: word of the fund's name. News ABOUT the bank's funds ("Maybank's Nasdaq Fund
#: Surpasses RM100 Million AUM", "Maybank Tokenises SGD Money Market Fund")
#: carries no identifier and stays linked. Measured over the 12,846 linked rows
#: to 2026-10-20: this fires on 3, all fund quote pages.
#:
#: Being NEAR an identifier is not enough: "Maybank launches Asia Income Fund
#: (0P0001ABCD) for retail investors" is the bank's own news. The alias and the
#: identifier must be one name - every word between them starts with a capital,
#: a digit or a symbol, as "ACI", "OppAMCDIST(G)$", "A-MINCOME(G)-USD" and
#: "Market-I" do - so a lowercase word between them ends the name.
_FUND_ID = r"\((?:0P[0-9A-Z]{8}|[A-Z0-9]{6,16}\.MF)\)"
_FUND_NAME_WORD = r"[A-Z0-9$&(][^\s]*"
_IN_A_FUNDS_NAME = re.compile(rf"\A(?:\s+{_FUND_NAME_WORD}){{0,8}}?\s+{_FUND_ID}")

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

# --- an exchange-qualified ticker names the company listed THERE -----------------
#
# "Nha Be Steel JSC Estimates – UPCOM:TNB" (tradingview.com, 2026-10-25) is a
# Vietnamese steelmaker's page, TNB its ticker on UPCoM, and two such rows were
# linked to Tenaga (MYX:5347) on the token alone. An alias written directly
# behind an exchange's code and a colon links only when that exchange is the
# instrument's own market, both read through markets.registry: "MYX:TENAGA" and
# "KLSE:TENAGA" are XKLS, "NASDAQ:AMD" and "NasdaqGS:AMD" are XNAS. A rejected
# match is an excluded mention, as a building's name is: it claims its span,
# links nothing, and `recheck` drops a stored link it made.
#
# Codes markets.registry has no adapter for, so no book name is listed on them.
# A code in neither list is not read as an exchange. Measured over the 26,518
# stored rows to 2026-10-28: an alias sits directly behind a known code in 16
# rows (NASDAQ:AMD, MYX:TENAGA, ...), and the guard rejects the two UPCOM:TNB
# rows and nothing else.
OTHER_EXCHANGES = (
    "UPCOM",
    "HOSE",
    "HNX",
    "NYSE",
    "NYSEARCA",
    "NYSEAMERICAN",
    "AMEX",
    "BATS",
    "OTC",
    "OTCMKTS",
    "BSE",
    "SET",
    "IDX",
    "PSE",
    "SSE",
    "SZSE",
    "TSX",
    "TSXV",
)
#: Nasdaq's three tiers, as Yahoo and S&P write the prefix: "NasdaqGS:AMD".
NASDAQ_TIERS = ("NASDAQGS", "NASDAQGM", "NASDAQCM")
_EXCHANGE_CODE = re.compile(rf"(?<!{_ASCII_WORD})([A-Za-z]{{2,12}}):\Z")


@lru_cache(maxsize=64)
def _exchange_mic(code: str) -> str | None:
    """The market an exchange code names, or None when it is not one we know."""
    from markets.registry import known_prefixes, resolve_mic

    up = code.upper()
    if up in NASDAQ_TIERS:
        return resolve_mic("NASDAQ")
    if up in known_prefixes():
        return resolve_mic(up)
    return up if up in OTHER_EXCHANGES else None


def _listed_elsewhere(text: str, start: int, iid: str) -> bool:
    """Is the alias at text[start:] written behind ANOTHER market's code?"""
    from markets.registry import mic_of

    m = _EXCHANGE_CODE.search(text, max(0, start - 13), start)
    there = _exchange_mic(m.group(1)) if m else None
    if there is None:
        return False
    try:
        return there != mic_of(iid)
    except ValueError:
        return False  # an id with no market prefix has nothing to compare


# --- names and contexts that are not the company, per instrument -----------------
#
# OTHER_NAMES above is the linker's own list. Per-instrument exclusions are
# NOT_THE_COMPANY below, keyed by instrument id, and reach every linker built
# by `linker_for`. They live here, not in entities.yaml: that file is
# human-authored and no agent writes it.
#
#   * `names`: longer names that contain an alias and name something else.
#     "Apple Hospitality REIT (NYSE:APLE)" and "Apple Bank" were linked to
#     XNAS:AAPL; over the 26,518 stored rows to 2026-10-28 this drops 23 AAPL
#     links, every one an Apple Hospitality row. Each is matched like an alias
#     and spends its span, as an OTHER_NAMES entry does.
#   * `near`: alias -> words that, within NEAR_WORDS words of it on either
#     side, say the alias is another listing's ticker ("TNB" beside UPCOM,
#     HOSE or Hanoi is Nha Be Steel, not Tenaga). Title Case or ALL CAPS, as
#     written, never lowercase: "hose" is a word.
NEAR_WORDS = 3
_TOKEN = re.compile(rf"{_ASCII_WORD}+")


@dataclass(frozen=True)
class NotTheCompany:
    """One instrument's entry in NOT_THE_COMPANY."""

    names: tuple[str, ...] = ()
    near: tuple[tuple[str, tuple[str, ...]], ...] = ()


NOT_THE_COMPANY: dict[str, NotTheCompany] = {
    "XNAS:AAPL": NotTheCompany(
        names=(
            "Apple Hospitality",  # NYSE:APLE, a hotel REIT
            "Apple Bank",  # Apple Bank for Savings, New York
        ),
    ),
    "MYX:5347": NotTheCompany(
        # TNB is also Nha Be Steel's ticker on Vietnam's UPCoM ("UPCOM:TNB")
        near=(("TNB", ("UPCOM", "UPCoM", "HOSE", "HNX", "Hanoi", "Vietnam", "Vietnamese")),),
    ),
}


def configured_exclusions() -> dict[str, NotTheCompany]:
    """NOT_THE_COMPANY, keyed by instrument id."""
    return NOT_THE_COMPANY


# --- a bank named as the AUTHOR of a broker's note -------------------------------
#
# A bank's broking arm publishes calls on other companies, and the press names
# the bank: on 2026-10-15 six of Maybank's eight digest rows were Maybank
# Securities / Maybank IB notes on Merdeka Copper Gold, Grab, Airports of
# Thailand, Vietnam's market and Singapore's banks, two of them starred. The
# bank IS named, so the row stays linked - retrieval, the graph's mention edges
# and per-source coverage read mentions - but it is not news about the bank.
# `EntityLinker.authored` names the ids a text mentions ONLY in these shapes,
# and the digest leaves such a row off that name's page. The shapes are
# broker vocabulary, not a list of banks, so they apply to any linked name
# ("CIMB keeps 'Buy' call on TNB" is CIMB's note and Tenaga's news).
#
# A broker's OUTPUT, in forms a company's own news does not take: a call, or a
# price target - "target price", "TP", an amount in a currency. Never a bare
# "target" or "rating": "Maybank sets RM80bil sustainable finance target for
# 2030" and "Maybank keeps AAA rating" are the bank's own. Earnings forecasts
# are left out for the same reason ("cuts loan growth forecast"), at the cost
# of "Maybank cuts DigiPlus forecasts" still reading as Maybank news.
#
# And the call must be ON ANOTHER NAMED COMPANY - a capitalised name between
# the verb and the call, or "on/for <Name>" after it. Without that the name
# before the verb is the one being rated: "IHH Healthcare maintains Buy,
# target price lowered" and "Apple upgraded to Buy" were both stored rows.
_ARM = r"(?:Securities|IBG?|Investment\s+Bank(?:ing)?(?:\s+(?:Bhd|Berhad))?|Kim\s+Eng|Research)"
_ACTS = (
    r"(?i:keeps?|kept|maintains?|cuts?|raises?|lifts?|holds?|sets?|starts?|rates?"
    r"|initiates?|reiterates?|upgrades?|downgrades?|lowers?|trims?|slash(?:es)?)"
)
_RATINGS = r"(?i:buy|hold|sell|outperform|underperform|neutral)"
#: A recommendation: BUY, 'buy', "at buy", "to hold", "Buy rating", "Sell call".
_RECOMMENDS = (
    r"(?:\b(?:BUY|HOLD|SELL|OUTPERFORM|UNDERPERFORM|NEUTRAL)\b"
    rf"|['‘\"“]{_RATINGS}['’\"”]"
    rf"|\b(?i:at|to)\s+{_RATINGS}\b"
    r"|\b(?i:buy|hold|sell)['’\"”]?\s+(?i:rating|call)\b)"
)
#: An amount in a currency, the code or symbol touching the digits or one space
#: away: "THB 75.00", "RM14.60", "RM 14.60", "S$0.09", "US$ 4.42".
_AMOUNT = r"(?:\b[A-Z]{3}|RM|S\$|US\$|\$)\s?\d[\d,]*(?:\.\d+)?"
_PRICE_TARGET = (
    r"(?:\b(?i:target\s+price|price\s+target)\b|\bTP\b"
    rf"|{_AMOUNT}\s+(?i:target)\b)"
)
#: Another company's name: capitalised words, not a recommendation itself.
_OTHER_NAME = r"(?!(?i:buy|hold|sell)\b)[A-Z][\w&'’-]*"
#: Within one clause: a full stop ends it unless a digit or letter follows
#: ("THB 75.00 target") or it closes a short capitalised abbreviation inside a
#: name ("Maybank Starts Frasers Log. & Comm. Trust at BUY").
_CLAUSE = r"(?:\b[A-Z][a-z]{1,4}\.(?=\s)|[^.?!;]|\.(?=\S))"
#: A bare "target" counts only as a target for a STOCK: "Maybank holds target
#: for Bank Rakyat Indonesia stock".
_STOCK_TARGET = rf"\b(?i:target)(?=\s+for\s+{_CLAUSE}{{1,60}}?\s+(?i:stock|shares)\b)"
#: A figure a bank "expects" for ANOTHER company's stock: "Results due November
#: 3: Maybank expects THB 13.4 billion for Advanced Info Service stock"
#: (ad-hoc-news.de, 2026-10-17) is Maybank's research on a Thai company, and it
#: stayed on Maybank's page. These are forecast verbs, which a bank uses of
#: itself too ("Maybank expects loan growth of 5%"), so they count only with an
#: amount in a currency followed by "for <Name> ... stock/shares" - the words
#: that say whose stock the figure is for.
_FORECASTS = r"(?i:expects?|sees?|forecasts?|projects?)"
_STOCK_FIGURE = (
    rf"{_AMOUNT}(?:\s*(?i:billion|million|bn|mil|mn)\b)?"
    rf"(?=\s+for\s+[A-Z]{_CLAUSE}{{0,60}}?\s+(?i:stock|shares)\b)"
)
#: A rating set after the stock it rates, in Title Case: "Maybank rates Mitra
#: Keluarga stock Buy" (ad-hoc-news.de, 2026-10-19).
_STOCK_RATED = rf"\b(?i:stock|shares)\s+{_RATINGS}\b"
#: A forecast for one company, then the call in the same clause: "Maybank
#: Forecasts Robust 4Q26 Recovery for ADVANC, Maintains 'Buy' With THB387 Target".
_FORECAST_THEN_CALL = (
    rf"{_FORECASTS}\b{_CLAUSE}{{0,60}}?\s+for\s+[A-Z]{_CLAUSE}{{0,30}}?,?\s+{_ACTS}\s+"
    rf"(?:{_RECOMMENDS}|{_PRICE_TARGET})"
)
#: "Maybank keeps Singtel at buy", "Maybank IB upgrades SD Guthrie to 'buy'",
#: "Maybank Maintains Frencken BUY" (the company, then the call), and
#: "Maybank Keeps BUY on ISOTeam", "Maybank keeps Buy rating for Singtel",
#: "Maybank kept IDR 3,800 target for Merdeka Copper Gold stock" (the call,
#: then the company), and "Maybank expects THB 13.4 billion for Advanced Info
#: Service stock" (a figure for another company's stock).
_BANK_MAKES_A_CALL = re.compile(
    rf"\A(?:\s+{_ARM})?\s+(?:{_ACTS}\b"
    rf"(?:\s+{_OTHER_NAME}{_CLAUSE}{{0,50}}?(?:{_RECOMMENDS}|{_PRICE_TARGET}|{_STOCK_RATED})"
    rf"|{_CLAUSE}{{0,30}}?(?:{_RECOMMENDS}|{_PRICE_TARGET}|{_STOCK_TARGET})\s+(?i:on|for)\s+[A-Z])"
    rf"|{_FORECASTS}\b{_CLAUSE}{{0,30}}?{_STOCK_FIGURE}\s+for\s+[A-Z]"
    rf"|{_FORECAST_THEN_CALL})"
)
#: "Grab Holdings Keeps BUY Rating as Maybank Cuts Target Price": the call is
#: on the company named before "as".
_AS_OR_AFTER = re.compile(r"\b(?i:as|after)\s+\Z")
_BANK_REVISES = re.compile(
    rf"\A(?:\s+{_ARM})?\s+{_ACTS}\b{_CLAUSE}{{0,30}}?(?:{_RECOMMENDS}|{_PRICE_TARGET}|\b(?i:rating|call)\b)"
)
#: "GEN Bhd downgraded by Maybank", "Marco Polo Marine Keeps BUY at Maybank".
_RATED_BY = re.compile(
    r"(?:\b(?i:upgraded|downgraded|initiated|rated)\s+by|\b(?:BUY|HOLD|SELL)\s+(?i:at|from))\s+\Z"
)
#: "Maybank Securities weighs in", "Maybank IB cautions", "Maybank Investment
#: Bank says": the research arm giving a view.
_ARM_SPEAKS = re.compile(
    rf"\A\s+{_ARM}(?:\s+(?i:CEO|chief\s+executive|economists?|analysts?))?"
    r"\s+(?i:weighs\s+in|says|said|sees|expects|expecting|cautions|warns|believes)\b"
)
#: "...from market status upgrade: Maybank Securities CEO", "... — Maybank IB":
#: the arm signing the headline.
_SIGNED = re.compile(r"[:\-–—]\s*\Z")
_ARM_SIGNS = re.compile(
    rf"\A\s+{_ARM}(?:\s+(?i:CEO|economists?|analysts?))?\s*(?:\Z|[?!]|\.(?:\s|\Z))"
)


def _writes_the_note(text: str, start: int, end: int) -> bool:
    """Is the alias at text[start:end] the author of a broker's call or view?"""
    before, after = text[max(0, start - 40) : start], text[end : end + 160]
    return bool(
        _BANK_MAKES_A_CALL.match(after)
        or _ARM_SPEAKS.match(after)
        or (_AS_OR_AFTER.search(before) and _BANK_REVISES.match(after))
        or _RATED_BY.search(before)
        or (_SIGNED.search(before) and _ARM_SIGNS.match(after))
    )


# --- stories that name a company without being news about it ---------------------
#
# Linked, because the name is written, and listed on its page as what they are,
# but never a CAUSE: the digest neither stars nor ranks them above a story about
# the company (`EntityLinker.incidental`, read by knowledge/digest.py, the
# ingest's escalation gate, the news agent's citations and ragqa). Two kinds:
#
#   * MENTION - another company's contract win. "Bintai Kinden wins RM20.3mil
#     TNB contract" was starred on Tenaga's page on 2026-10-27 and 10-28, and
#     the Jati Tinggi, Kerjaya Prospek and SKA Capital awards before it: the
#     event is the contractor's, and for the utility it is one more purchase
#     order. Read as the winner, a win verb, up to six words, then the alias
#     followed by a contract noun ("TNB-linked jobs", "Tenaga contract"), or a
#     contract noun "from"/"by" the alias ("Deal from Tenaga Nasional"), all in
#     ONE clause: a comma after the verb, or "as", "against", "amid" and the
#     rest of _CLAUSE_BREAK, ends it. "Dell wins as Nvidia orders surge" and
#     "Apple wins appeal against Microsoft deal" are two clauses, and Nvidia's
#     orders are not what Dell won. "TNB awards RM568m contract" names TNB as
#     the actor and stays its news.
#   * INCIDENT_BYSTANDER - an accident, crash or fire that involves the
#     company's vehicle, staff or premises and not its business. "3 family
#     members killed in Dungun crash" ("MPV and TNB vehicle collide") was ranked
#     first for Tenaga on 2026-10-25 at quality 1.00. Read per thing (_THINGS):
#     the company's thing ("TNB lorry", "a company vehicle"), an incident word
#     that can happen to THAT thing within a few words of it - a crash to a
#     vehicle, a fire to an office, a death or injury to staff, written after
#     them - and no word of the business anywhere: a substation fire that
#     "triggered JB power outage" is the utility's news and stays it.
#     "Microsoft Office crash" is software, and "Apple Vision Pro is dead,
#     Apple staff say" is a product: neither is an incident.
#
# Measured over the 26,518 stored rows to 2026-10-28: MENTION on 24 rows - 20
# Tenaga contractor awards, and "NiSource Lands Major Microsoft Power Deal",
# "Qualcomm Secures Apple Licensing Deal", Intel's foundry orders from Nvidia
# and HPE's AMD rack deal - and INCIDENT_BYSTANDER on 3, the two Dungun crash
# rows and the cable-theft electrocution. Not caught: "Bintai Kindenko Builds
# Strong Comeback with RM20.28 Million TNB Contract" (no win verb).
MENTION = "mention"
INCIDENT_BYSTANDER = "incident_bystander"
#: Kinds a cause is never drawn from.
NOT_A_CAUSE = frozenset({MENTION, INCIDENT_BYSTANDER})

_WIN_VERB = (
    r"(?i:wins?|won|bags?|bagged|secures?|secured|clinch(?:es|ed)?|lands?|landed"
    r"|nets?|netted|accepts?|accepted|obtains?|obtained|awarded)"
)
_CONTRACT_NOUN = r"(?i:(?:sub)?contracts?|jobs?|tenders?|deals?|projects?|works|orders?|packages?)"
#: Words that start another clause, so a win on one side of them is not a
#: contract from the name on the other.
_CLAUSE_BREAK = (
    r"(?i:as|while|after|before|against|amid|despite|but|though|although|when|whereas"
    r"|because|since|over|until|unlike)\b"
)
#: One word of the win's clause.
_CLAUSE_WORD = rf"(?!{_CLAUSE_BREAK})[^\s;?!]+"
#: ...and one between the verb and the name, which a comma also ends.
_OBJECT_WORD = rf"(?!{_CLAUSE_BREAK})[^\s;?!]*[^\s,;?!]"
#: "Jati Tinggi, Kerjaya Prospek consortium wins RM568m ", "SKA Capital unit
#: accepts RM89.28mil award for ": a capitalised winner, the verb, up to six
#: words, in one clause.
_WINNER_WINS = re.compile(
    rf"[A-Z0-9][^\s;?!]*(?:\s+{_CLAUSE_WORD}){{0,6}}?\s+{_WIN_VERB}"
    rf"(?:\s+{_OBJECT_WORD}){{0,6}}?\s+\Z"
)
#: "TNB contract", "TNB-linked jobs", "Tenaga's substation contract".
_CONTRACT_AFTER = re.compile(
    rf"\A(?:[-‐](?i:linked|related|owned)|['’]s)?(?:\s+{_OBJECT_WORD}){{0,3}}?"
    rf"\s+{_CONTRACT_NOUN}\b"
)
#: "Bags MYR13 Million Deal from Tenaga Nasional".
_CONTRACT_FROM = re.compile(rf"\b{_CONTRACT_NOUN}\s+(?i:from|by)\s+\Z")


def _wins_its_contract(text: str, start: int, end: int) -> bool:
    """Is the alias at text[start:end] the CLIENT in another company's contract win?"""
    before, after = text[max(0, start - 160) : start], text[end : end + 60]
    return bool(
        _WINNER_WINS.search(before)
        and (_CONTRACT_AFTER.match(after) or _CONTRACT_FROM.search(before))
    )


_HARM = r"killed|dies|died|dead|injured|electrocuted|electric\s+shock"
_FIRE = r"fire|blaze"
_CRASH = r"crash(?:es|ed)?|collisions?|collided?|collides|accidents?|mishaps?|overturned|skidded"
#: How far, in words and within one sentence, an incident word may sit from the thing.
INCIDENT_WORDS = 8


@dataclass(frozen=True)
class _Thing:
    """A kind of thing a company owns, and what can happen to it."""

    #: The alias and then its thing: "TNB lorry", "TNB's van", "(TNB) cable".
    after_alias: re.Pattern[str]
    #: "a collision involving a company vehicle".
    of_a_company: re.Pattern[str]
    incident: re.Pattern[str]
    #: Words looked at before the alias, and after the thing.
    before: int
    after: int


def _thing(nouns: str, incidents: str, before: int, after: int) -> _Thing:
    return _Thing(
        re.compile(rf"\A\)?(?:['’]s)?\s+(?i:{nouns})\b"),
        re.compile(rf"\b(?i:company|company['’]s)\s+(?i:{nouns})\b"),
        re.compile(rf"\b(?i:{incidents})\b"),
        before,
        after,
    )


_THINGS = (
    _thing(
        r"lorry|lorries|vans?|trucks?|vehicles?|pick-?ups?|bus|buses|motorcycles?",
        f"{_CRASH}|{_FIRE}|{_HARM}",
        INCIDENT_WORDS,
        INCIDENT_WORDS,
    ),
    _thing(r"cables?", _HARM, INCIDENT_WORDS, INCIDENT_WORDS),
    # An office or a building burns; "Microsoft Office crash" is software.
    _thing(r"premises|office|depot|building", _FIRE, INCIDENT_WORDS, INCIDENT_WORDS),
    # People are hurt AFTER they are named - "TNB workers injured", "TNB
    # technician electrocuted" - and "is dead, Apple staff say" is a quote.
    _thing(r"workers?|staff|employees?|technicians?|linem[ae]n", _HARM, 0, 3),
)
_SENTENCE_WORD = r"[^\s.;!?|]"
_BUSINESS = re.compile(
    r"\b(?i:outages?|blackouts?|power\s+(?:cuts?|supply|disruptions?)|supply\s+disruptions?"
    r"|substations?|tariffs?|shares|stocks?|earnings|profits?|revenue|dividends?|contracts?"
    r"|guidance)\b"
)


def _near_incident(text: str, start: int, end: int, thing: _Thing) -> bool:
    """Is one of the thing's incident words within its window of text[start:end]?"""
    before = re.search(
        rf"(?:{_SENTENCE_WORD}+\s+){{0,{thing.before}}}{_SENTENCE_WORD}*\Z",
        text[max(0, start - 160) : start],
    )
    after = re.match(
        rf"{_SENTENCE_WORD}*(?:\s+{_SENTENCE_WORD}+){{0,{thing.after}}}",
        text[end : end + 160],
    )
    return bool(
        (thing.before and before and thing.incident.search(before.group()))
        or (after and thing.incident.search(after.group()))
    )


def _its_thing_in_an_incident(text: str, alias_spans: Iterable[tuple[int, int]]) -> bool:
    """Does the text put a company's vehicle, cable, premises or staff in an incident?"""
    spans = list(alias_spans)
    for thing in _THINGS:
        found = [(m.start(), m.end()) for m in thing.of_a_company.finditer(text)]
        for start, end in spans:
            m = thing.after_alias.match(text[end : end + 40])
            if m:
                found.append((start, end + m.end()))
        if any(_near_incident(text, s, e, thing) for s, e in found):
            return True
    return False


def alias_pattern(surface: str, *, bare: bool = False) -> re.Pattern[str]:
    """The compiled matcher for one alias, per the rules in the module doc.

    `bare` leaves out the exclusions that read the words after it (a
    thoroughfare, a corporate suffix): it answers "is the alias written here
    at all", which `EntityLinker.recheck` needs to tell an excluded mention
    from no mention."""
    surface = surface.strip()
    if not surface:
        raise ValueError("an empty alias matches everything and nothing")
    escaped = re.escape(surface)
    if _NON_ASCII.search(surface):
        return re.compile(escaped, re.IGNORECASE)

    not_a_place = "" if bare else _NOT_A_PLACE
    bounded = rf"(?<!{_ASCII_WORD}){{}}(?!{_ASCII_WORD}){not_a_place}"
    is_one_word = " " not in surface
    if is_one_word and surface.isupper() and len(surface) <= ACRONYM_MAX_CHARS:
        # case-sensitive acronym, and not the first word of another company
        return re.compile(bounded.format(escaped) + ("" if bare else _NOT_ANOTHER_COMPANY))
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

    def __init__(
        self, index: dict[str, str], exclusions: Mapping[str, NotTheCompany] | None = None
    ) -> None:
        # Longest first so a subsidiary's longer name wins over its parent's.
        # OTHER_NAMES and each instrument's NOT_THE_COMPANY names ride along
        # with no instrument (''), unless the index names them, in which case
        # the index entry is the one kept.
        exclusions = exclusions or {}
        not_names = [n for e in exclusions.values() for n in e.names]
        named = {**{n: "" for n in (*OTHER_NAMES, *not_names)}, **index}
        #: (alias, iid) -> the words that, near it, make it another listing's.
        self._near: dict[tuple[str, str], frozenset[str]] = {
            (alias, iid): frozenset(w for word in words for w in (word, word.upper()))
            for iid, e in exclusions.items()
            for alias, words in e.near
        }
        self._entries: tuple[tuple[str, str, re.Pattern[str]], ...] = tuple(
            (surface, iid, alias_pattern(surface))
            for surface, iid in sorted(named.items(), key=lambda kv: (-len(kv[0]), kv[0]))
            if surface.strip()
        )
        self._surfaces: dict[str, list[str]] = {}
        for surface, iid, _ in self._entries:
            if iid:
                self._surfaces.setdefault(iid, []).append(surface)
        self._bare: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
            (iid, alias_pattern(surface, bare=True)) for surface, iid, _ in self._entries if iid
        )

    def _matches(self, text: str) -> list[tuple[int, int, int, str]]:
        """(start, end, alias order, iid) for every match no longer match contains.

        Aliases are tried longest first and each accepted match claims its
        span; a shorter alias whose match overlaps a claimed span is the same
        words read twice. Searching each alias on its own, as this did until
        2026-10-10, linked "Genting Malaysia posts higher quarterly revenue"
        to Genting Malaysia AND Genting Berhad. Every occurrence is checked,
        not just the first, so "Genting Malaysia ... Genting Berhad" still
        links both. A match of an OTHER_NAMES or NOT_THE_COMPANY name
        claims its span and links nothing, and so does one that ends a Malay
        building's name, is a word of a fund's name, sits behind another
        market's exchange code or near a NOT_THE_COMPANY word.
        """
        claimed: list[tuple[int, int]] = []
        out: list[tuple[int, int, int, str]] = []
        for order, (surface, iid, pattern) in enumerate(self._entries):
            for m in pattern.finditer(text):
                start, end = m.span()
                if any(start < e and s < end for s, e in claimed):
                    continue
                claimed.append((start, end))
                # Checked here, not in alias_pattern: the name words between
                # "Menara" and the alias, or between the alias and a fund's
                # identifier, vary in number, and a lookaround cannot.
                if (
                    iid
                    and not _IN_A_BUILDING_NAME.search(text, max(0, start - 80), start)
                    and not _IN_A_FUNDS_NAME.match(text[end : end + 80])
                    and not _listed_elsewhere(text, start, iid)
                    and not self._near_another_listing(text, start, end, surface, iid)
                ):
                    out.append((start, end, order, iid))
        return out

    def _near_another_listing(
        self, text: str, start: int, end: int, surface: str, iid: str
    ) -> bool:
        words = self._near.get((surface, iid))
        if not words:
            return False
        before = _TOKEN.findall(text[max(0, start - 80) : start])[-NEAR_WORDS:]
        after = _TOKEN.findall(text[end : end + 80])[:NEAR_WORDS]
        return any(w in words for w in (*before, *after))

    def link(self, text: str) -> list[str]:
        """Instrument ids the text names, in order of first mention.

        A name inside a longer name is not a mention: "Maybank Islamic" is the
        subsidiary alone, and the parent is listed only where the text names
        it separately. Ties at the same position keep alias order.
        """
        first_at: dict[str, tuple[int, int]] = {}
        for start, _end, order, iid in self._matches(text):
            key = (start, order)
            if iid not in first_at or key < first_at[iid]:
                first_at[iid] = key
        return [iid for iid, _ in sorted(first_at.items(), key=lambda kv: kv[1])]

    def recheck(self, text: str, stored: Iterable[str]) -> list[str]:
        """The stored links this linker would still make, in stored order.

        A row's links are written once, when it is stored, and the corpus is
        append-only, so an exclusion added later never reaches the rows before
        it: 22 *Shree TNB Polymers* rows stored before 2026-10-18 still put an
        Indian listing on Tenaga's page after that day's merge excluded it. An
        id is dropped when the text writes one of its aliases and no mention
        survives today's rules. An id whose alias the text never writes is
        kept: until 2026-10-10 a vendor's ticker was stored as the link, and
        whether those rows are about the company is not a question the rules
        here can answer from the text.
        """
        stored = list(stored)
        if not stored:
            return []
        linked = set(self.link(text))
        written = {iid for iid, pattern in self._bare if pattern.search(text)}
        return [iid for iid in stored if iid in linked or iid not in written]

    def mentions(self, text: str, instrument_ids: list[str]) -> int:
        """How many times any alias of the given instruments occurs, counting
        only matches no longer alias contains, the same reading as `link`."""
        wanted = set(instrument_ids)
        return sum(1 for _start, _end, _order, iid in self._matches(text) if iid in wanted)

    def authored(self, text: str) -> set[str]:
        """Linked ids the text names ONLY as the author of a broker's note.

        "Maybank keeps THB 75.00 target for Airports of Thailand stock" is
        linked to MYX:1155 by `link` and returned here: it mentions the bank,
        and it is news about Airports of Thailand. One mention in any other
        role ("...; Maybank shares rose 1%") and the id is not returned. See
        the shapes above `_writes_the_note`.
        """
        only: dict[str, bool] = {}
        for start, end, _order, iid in self._matches(text):
            only[iid] = only.get(iid, True) and _writes_the_note(text, start, end)
        return {iid for iid, yes in only.items() if yes}

    def incidental(self, text: str) -> dict[str, str]:
        """Linked ids the story names without being news about them, and how.

        INCIDENT_BYSTANDER for every linked id of a story about an accident,
        crash or fire involving a company's vehicle, staff or premises and not
        its business; MENTION for an id the text names ONLY as the client in
        another company's contract win - one mention in any other role
        ("...; TNB shares rose 1%") and the id is not returned. Both kinds are
        in NOT_A_CAUSE. See the shapes above `_wins_its_contract`.
        """
        matches = self._matches(text)
        if not matches:
            return {}
        if not _BUSINESS.search(text) and _its_thing_in_an_incident(
            text, ((start, end) for start, end, _o, _i in matches)
        ):
            return {iid: INCIDENT_BYSTANDER for _s, _e, _o, iid in matches}
        only: dict[str, bool] = {}
        for start, end, _order, iid in matches:
            only[iid] = only.get(iid, True) and _wins_its_contract(text, start, end)
        return {iid: MENTION for iid, yes in only.items() if yes}

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
    return EntityLinker(dict(items), configured_exclusions())


def relinked(article, linker: EntityLinker):
    """The article with its stored links read by today's linker rules.

    Features scored for the old links go with them, so a caller that scores the
    row scores it again on the names it is linked to now. (A row read from the
    corpus carries none - they are not stored - but an article built in memory
    might.) The digest and the graph's rebuild both read stored rows this way,
    so the two cannot disagree about who a story names."""
    from dataclasses import replace

    ids = linker.recheck(article.text, article.instruments)
    if ids == list(article.instruments):
        return article
    return replace(article, instruments=ids, features=None)


def linker_for(index: dict[str, str]) -> EntityLinker:
    """A linker for this index, compiled once per distinct index, with the
    exclusions in NOT_THE_COMPANY."""
    return _cached(tuple(sorted(index.items())))


def link_entities(text: str, index: dict[str, str]) -> list[str]:
    """Surface form -> instrument_id, longest match first. Whole words only."""
    if not index:
        return []
    return linker_for(index).link(text)
