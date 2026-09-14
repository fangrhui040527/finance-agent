"""Cleaning, language and quality rules applied to every article before it is kept.

What arrives from a feed is not what an agent should read. Measured on the
2026-09-04 corpus:

  * GDELT titles carry tokenisation artefacts: `600 , 000 Students`,
    `Google ' s`, `Where Will Bitcoin Be in 2030 ?`. Indexed as-is, "600" and
    "000" are two tokens and "Google" never has a possessive.
  * RSS descriptions arrive as HTML - `<p>`, `<a>`, `&amp;`, `&nbsp;` - and a
    Google News description is a link list that repeats the headline.
  * 38% of the corpus was in languages nobody on this book reads, and none of
    those articles linked to a Bursa name.
  * A share of the English was not news: crypto price predictions, "should
    you buy" listicles, lottery results that mention a bank.

Each rule here is small, deterministic and tested. None of them calls a model:
docs/08 section 7 puts a rule filter in front of the API precisely so that
four in five articles never reach it.
"""

from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import urlparse

_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"<\s*(br|/p|/div|/li|/h[1-6])\s*/?>", re.IGNORECASE)
_WS = re.compile(r"\s+")
_ZERO_WIDTH = re.compile(r"[​-‏  ⁠﻿]")

#: GDELT-style spacing artefacts, in the order they are applied.
_SPACING_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(\d)\s+,\s+(\d{3})\b"), r"\1,\2"),  # 600 , 000 -> 600,000
    (re.compile(r"(\d)\s+\.\s+(\d)"), r"\1.\2"),  # 5 . 3 % -> 5.3 %
    (re.compile(r"(\w)\s*'\s+(s|t|re|ve|ll|d|m)\b"), r"\1'\2"),  # Google ' s -> Google's
    (re.compile(r"\s+([,.;:!?%])"), r"\1"),  # word , -> word,
    (re.compile(r"([(\[])\s+"), r"\1"),  # ( word -> (word
    (re.compile(r"\s+([)\]])"), r"\1"),  # word ) -> word)
    (re.compile(r"\s+-\s*$"), ""),  # a dangling trailing dash
)

#: A publisher suffix Google News appends: "Headline - The Star". Only a short,
#: non-numeric tail is a publisher; "Q3 results - 12% rise" is not.
_PUBLISHER_TAIL = re.compile(r"^(?P<title>.+?)\s+[-–—|]\s+(?P<publisher>[^-–—|]{2,48})$")


def strip_html(text: str) -> str:
    """Tags out, entities decoded, block breaks kept as spaces."""
    if not text:
        return ""
    text = _BREAK.sub(" ", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(html.unescape(text))  # feeds double-encode; &amp;amp; is common
    return text


def normalise_text(text: str) -> str:
    """The one normal form every stored title and body is in."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    text = _ZERO_WIDTH.sub("", text)
    text = strip_html(text)
    text = _WS.sub(" ", text).strip()
    for pattern, repl in _SPACING_FIXES:
        text = pattern.sub(repl, text)
    return text.strip()


def split_publisher(title: str) -> tuple[str, str | None]:
    """'Headline - Publisher' -> ('Headline', 'Publisher'); otherwise unchanged."""
    m = _PUBLISHER_TAIL.match(title.strip())
    if not m:
        return title.strip(), None
    publisher = m.group("publisher").strip()
    if any(ch.isdigit() for ch in publisher) and len(publisher) < 6:
        return title.strip(), None
    return m.group("title").strip(), publisher


# --- language -----------------------------------------------------------------------

#: Codes and names a feed might use -> the name GDELT uses, lowercased.
_LANGUAGE_KEYS = {
    "en": "english",
    "eng": "english",
    "en-us": "english",
    "en-gb": "english",
    "en-my": "english",
    "ms": "malay",
    "may": "malay",
    "msa": "malay",
    "zsm": "malay",
    "bahasa malaysia": "malay",
    "zh": "chinese",
    "zho": "chinese",
    "chi": "chinese",
    "zh-cn": "chinese",
    "zh-tw": "chinese",
    "zh-hans": "chinese",
    "zh-hant": "chinese",
    "ta": "tamil",
    "tam": "tamil",
}


def language_key(raw: str | None) -> str:
    """'en', 'English', 'en-US' -> 'english'. Unknown values pass through lowercased."""
    key = (raw or "").strip().lower()
    return _LANGUAGE_KEYS.get(key, key)


def language_allowed(raw: str | None, allow) -> bool:
    """True when `allow` is empty (no filter) or names this language."""
    allowed = {language_key(a) for a in (allow or ()) if a}
    if not allowed:
        return True
    key = language_key(raw)
    # An RSS feed that declares no language is assumed to be in the language
    # of the edition it was fetched from, which the caller allowed.
    return not key or key in allowed


# --- junk and low value --------------------------------------------------------------

#: Not news about a company, whatever it mentions. Dropped before dedup.
JUNK = re.compile(
    r"\b(casino|betting odds|horoscope|lottery|4d results|toto results|giveaway|"
    r"coupon|promo code|sweepstakes|recipe|obituar(y|ies)|sponsored content|"
    r"advertorial|paid partnership|press release distribution)\b",
    re.IGNORECASE,
)

#: `$MAYBANK (1155.MY)$` - the tag a retail social platform stamps on a user
#: post, which Google News then carries as if it were reporting. Matched only at
#: the START of a title.
#:
#: THIS IS THE CANONICAL COPY. `knowledge/retrieval/index.indexable` imports it
#: rather than keeping its own: one shape, one pattern, because two regexes for
#: one concept drift and the divergence is silent - the index would stop ranking
#: a row the corpus still scored as news, or the reverse.
#:
#: Introduced at the index seam in #65 with the measurement that settled the
#: shape: filtering the whole moomoo.com domain removed 23 rows, filtering on
#: this prefix removed 9. The domain is not the filter - 11 of moomoo's 19
#: articles are real syndicated journalism, including the Bursa reporting this
#: book is short of. The cashtag prefix separates the post from the reporting.
TICKER_TAG = re.compile(r"\$[^$]{1,40}\([A-Z0-9.]{1,10}\)\$")

#: Kept, but scored down: opinion listicles and crypto price talk that name a
#: company without reporting anything about it.
LOW_VALUE = re.compile(
    r"(^(should you|is it time to|why i('| a)m|here('| i)s why|prediction:)\b)|"
    r"(\b\d+ (stocks|reasons|things|ways)\b)|(\btop \d+\b)|(\bbest \w+ stocks?\b)|"
    r"(\bstocks? to buy\b)|(\bmillionaire\b)|(\bpassive income\b)|"
    r"(\bbefore it('| i)s too late\b)|(\bcould make you\b)|"
    r"(\b(bitcoin|crypto|dogecoin|shiba|xrp|solana|ethereum)\b.{0,40}\b(price|prediction|"
    r"rally|crash|2030|to the moon)\b)",
    re.IGNORECASE,
)

#: Domains whose reporting is edited and attributable. Not a whitelist - a
#: story from elsewhere is kept - but a small quality credit, and the tier the
#: catalyst engine's SOURCE_TRUST already recognises for wires.
TRUSTED_DOMAINS = frozenset(
    {
        "reuters.com",
        "bloomberg.com",
        "ft.com",
        "wsj.com",
        "cnbc.com",
        "marketwatch.com",
        "barrons.com",
        "nikkei.com",
        "asia.nikkei.com",
        "theedgemalaysia.com",
        "theedgemarkets.com",
        "thestar.com.my",
        "bernama.com",
        "nst.com.my",
        "freemalaysiatoday.com",
        "themalaysianreserve.com",
        "malaymail.com",
        "businesstoday.com.my",
        "sec.gov",
        "bursamalaysia.com",
        "bnm.gov.my",
        "apnews.com",
        "bbc.com",
        "bbc.co.uk",
        "scmp.com",
        "straitstimes.com",
        "businesstimes.com.sg",
        "finance.yahoo.com",
        "investing.com",
        "seekingalpha.com",
    }
)


def domain_of(url_or_domain: str) -> str:
    raw = (url_or_domain or "").strip().lower()
    if "://" in raw:
        raw = urlparse(raw).netloc
    return raw[4:] if raw.startswith("www.") else raw


def is_junk(title: str, body: str = "", domain: str = "") -> bool:
    # A ticker tag with nothing under it. #65 stopped these RANKING; this stops
    # them scoring as news, which is a different path and was still open: the
    # daily digest reads the corpus directly, and `quality_score` gave them 0.65
    # against `digest.MIN_QUALITY` of 0.4. Nine were stored by 2026-09-14 and
    # they were still arriving - `$SanDisk (SNDK.US)$ $Apple (AAPL.US)$ ...` on
    # 09-12, so the shape is not a Malaysian quirk.
    #
    # Same condition as `indexable`, deliberately: headline-only AND the tag at
    # the start. All nine stored rows are headline-only, so the guard costs
    # nothing today, and a post that ever arrives with real text under it is a
    # claim that can be cited - the two seams should not disagree about which.
    stripped = (title or "").strip()
    body_text = (body or "").strip()
    if (not body_text or body_text == stripped) and TICKER_TAG.match(stripped):
        return True
    return bool(JUNK.search(f"{title} {body}"))


def is_low_value(title: str, body: str = "", domain: str = "") -> bool:
    if domain_of(domain) in {"fool.com", "aol.com"} and LOW_VALUE.search(title):
        return True
    return bool(LOW_VALUE.search(title))


def quality_score(
    title: str,
    body: str,
    domain: str,
    *,
    linked: bool,
    language_ok: bool = True,
) -> float:
    """[0, 1]. Deterministic, explainable, and never a model's opinion.

    0.0 is junk. Around 0.5 is a bare headline from an unknown site. Above 0.8
    is an edited source, with a summary beyond the headline, attributed to a
    name in the book.
    """
    if is_junk(title, body, domain):
        return 0.0
    score = 0.5
    title = title or ""
    body = body or ""
    if len(body) >= len(title) + 40:
        score += 0.20  # there is a summary, not just a headline
    if linked:
        score += 0.15
    if domain_of(domain) in TRUSTED_DOMAINS:
        score += 0.15
    if is_low_value(title, body, domain):
        score -= 0.35  # a linked listicle lands at 0.30, under the digest's 0.40 floor
    if not language_ok:
        score -= 0.20
    if len(title) < 15:
        score -= 0.10
    return max(0.0, min(1.0, round(score, 2)))
