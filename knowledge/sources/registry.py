"""Structured source names -> collector classes. Unknown names refuse.

The twin of knowledge/feeds/registry.py for sources that produce numbers,
events and documents rather than headlines. `config.toml [sources] enabled`
may name either kind; `core.config` validates a name against both.
"""

from __future__ import annotations

from knowledge.sources.alphavantage import AlphaVantageNews
from knowledge.sources.base import Collector
from knowledge.sources.bnm import BnmOprCollector
from knowledge.sources.bursa import BursaAnnouncements
from knowledge.sources.dbnomics import DbnomicsCollector
from knowledge.sources.dosm import DosmCpiCollector
from knowledge.sources.edgar import EdgarFilings
from knowledge.sources.eodhd import EodhdFundamentals
from knowledge.sources.finmind import FinMindCollector
from knowledge.sources.finnhub import FinnhubCollector
from knowledge.sources.fmp import FmpCollector
from knowledge.sources.fred import FredCollector
from knowledge.sources.jin10 import Jin10CalendarCollector, Jin10FlashCollector
from knowledge.sources.sec_xbrl import SecCompanyFacts
from knowledge.sources.twse import TwseOpenApiCollector


class UnknownCollector(ValueError):
    """A configured structured source with no collector."""


COLLECTORS: dict[str, type[Collector]] = {
    "finnhub": FinnhubCollector,
    "fmp": FmpCollector,
    "alphavantage_news": AlphaVantageNews,
    "fred": FredCollector,
    "bnm_opr": BnmOprCollector,
    "dosm_cpi": DosmCpiCollector,
    "edgar": EdgarFilings,
    "bursa_announcements": BursaAnnouncements,
    "jin10_flash": Jin10FlashCollector,
    "jin10_calendar": Jin10CalendarCollector,
    "dbnomics": DbnomicsCollector,
    "twse_openapi": TwseOpenApiCollector,
    "finmind": FinMindCollector,
    "sec_xbrl": SecCompanyFacts,
    "eodhd": EodhdFundamentals,
}


def is_collector(name: str) -> bool:
    return name in COLLECTORS


def collector_for(name: str, **kwargs) -> Collector:
    try:
        cls = COLLECTORS[name]
    except KeyError:
        raise UnknownCollector(
            f"no collector registered for structured source {name!r}. "
            f"Known: {', '.join(sorted(COLLECTORS))}."
        ) from None
    return cls(**kwargs)
