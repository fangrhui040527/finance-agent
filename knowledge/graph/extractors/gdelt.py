"""News events, linked to the companies their articles name.

EVERY EDGE HERE IS `INFERRED`, AND THAT IS THE POINT.

knowledge/feeds/adapter.link_entities is a longest-first substring match over
surface forms. It establishes that an article MENTIONS a company. It cannot
establish that the event AFFECTS it - "Maybank was not among the banks named in
the probe" links Maybank exactly as strongly as a story about Maybank's own
probe does.

So these edges are traversable and displayable, and `Edge.citable` refuses them:
an INFERRED edge may never back an emitted claim. They accumulate for the
phase-3 review surface, where a person reads one and either promotes it into
data/supply_chain.yaml with a real basis, or throws it away.

That is the whole reason the confidence split exists. A binary citable flag
would have forced a choice between discarding the news layer and letting a
substring match cite itself as evidence.
"""

from __future__ import annotations

from datetime import UTC

from knowledge.graph.entity_graph import Confidence, EdgeKind, NodeKind
from knowledge.graph.extractors.base import Extractor, company_node, edge, node, sorted_payload
from knowledge.graph.ids import node_id

#: An event is news, and news decays. A story stops describing the world after a
#: while, and an AFFECTS edge with no end would still be routing a 2021 port
#: closure into today's exposure answer.
EVENT_VALID_DAYS = 90

#: A substring match is weak evidence of a relationship even when it is right.
MENTION_WEIGHT = 0.5


class GdeltExtractor(Extractor):
    name = "gdelt"

    def __init__(self, articles=()) -> None:
        self.articles = list(articles)

    @classmethod
    def from_fixture(cls, path, entity_index=None, **kw):
        """Offline by default: the fixture feed reads newline-delimited JSON."""
        from datetime import datetime

        from knowledge.feeds.adapter import FixtureFeed

        feed = FixtureFeed(path=path)
        index = entity_index if entity_index is not None else _index_from_aliases()
        since = kw.pop("since", datetime(1970, 1, 1, tzinfo=UTC))
        articles, _ = feed.normalize(feed.fetch(since), entity_index=index, **kw)
        return cls(articles)

    @classmethod
    def from_corpus(cls, corpus, since=None, limit: int = 500):
        """The stored articles, rather than a fixture.

        `from_fixture` is the offline path a test uses; this is the scheduled
        one. It reads what a sweep already normalised and linked, so the graph
        and the corpus cannot disagree about which company a story named.
        """
        return cls(corpus.articles(since=since, limit=limit))

    def extract(self) -> dict:
        from datetime import timedelta

        nodes: list[dict] = []
        edges: list[dict] = []
        for art in self.articles:
            if not art.instruments:
                continue  # nothing to attach it to
            ev = node(NodeKind.EVENT, art.doc_id, art.title, source_domain=art.source_domain)
            nodes.append(ev)
            opened = art.published_at.date()
            for iid in sorted(set(art.instruments)):
                cid = node_id(NodeKind.COMPANY, iid)
                nodes.append(company_node(iid))
                edges.append(
                    edge(
                        ev["id"],
                        cid,
                        EdgeKind.AFFECTS,
                        doc=art.doc_id,
                        confidence=Confidence.INFERRED,
                        weight=MENTION_WEIGHT,
                        valid_from=opened,
                        valid_to=opened + timedelta(days=EVENT_VALID_DAYS),
                    )
                )
        return sorted_payload(nodes, edges)


def _index_from_aliases() -> dict[str, str]:
    """Reuse data/entities.yaml as the linker's index rather than a second list.

    The surface forms the graph knows and the surface forms the news layer
    matches on must be the same set, or a company is linked in one and invisible
    in the other.
    """
    import yaml

    from knowledge.graph.ids import ENTITIES_FILE

    if not ENTITIES_FILE.exists():
        return {}
    raw = yaml.safe_load(ENTITIES_FILE.read_text()) or {}
    out: dict[str, str] = {}
    for iid, surfaces in (raw.get("companies") or {}).items():
        for surface in surfaces or []:
            out[str(surface)] = iid
    return out


def entity_index() -> dict[str, str]:
    """The linker's index, for callers outside this module.

    A scheduled sweep links entities before this extractor ever runs, and it
    must use the SAME surface forms - a company linked by one and not the other
    is in the corpus and invisible in the graph.
    """
    return _index_from_aliases()
