"""finbert_tone: a daily headline-tone line per name, scored here by FinBERT.

A collector that reads the CORPUS, not the network. It runs after the news
sources in its slot (it is last in the catalogue, and the sweep runs sources
in catalogue order), scores the English titles the linker attached to each of
the slot's names, and stores per name per UTC day:

  * `finbert_sentiment` - the mean of P(positive) - P(negative), in [-1, 1];
  * `finbert_count` - how many titles that mean is over.

The payload carries the positive / negative / neutral split (the label with
the highest probability) and the model version, so a number can be traced to
the weights that produced it (knowledge/news/finbert.py).

Like the vendor feeds before it, each pull scores from the START of the UTC
day its window opens in, so a later vintage of a day is over at least as many
titles as an earlier one and `latest()` never reads a partial day over a whole
one. A day with no linked English title stores nothing: an absent row is "not
scored", never a neutral 0.

The model is the expensive part, so it is built only when there is a title to
score, and a test hands the collector its own `scorer` and never loads it.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from knowledge.facts import Observation
from knowledge.sources.base import Collector, Pull, SourceError

#: The stored language values that mean English: GDELT writes its own names,
#: the other adapters ISO codes.
ENGLISH = ("en", "English")

Scorer = Callable[[Sequence[str]], list[dict[str, float]]]


def _default_corpus() -> str:
    try:
        from core.config import load

        return str(load().corpus_db)
    except Exception:  # a broken config is reported by the sweep, not here
        return "data/corpus.db"


class FinbertTone(Collector):
    name = "finbert_tone"
    key_env = None

    def __init__(
        self,
        *args,
        corpus_path: str | None = None,
        scorer: Scorer | None = None,
        version: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._corpus_path = corpus_path
        self._scorer = scorer
        self._version = version or ("injected scorer" if scorer else "")

    def _model(self) -> Scorer:
        if self._scorer is None:
            from knowledge.news.finbert import FinBert, FinbertUnavailable

            try:
                model = FinBert()
            except FinbertUnavailable as e:
                raise SourceError(f"finbert_tone: {e}") from e
            self._scorer, self._version = model.score, model.version
        return self._scorer

    def titles(
        self, start: datetime, instruments: Sequence[str]
    ) -> dict[tuple[str, str], list[str]]:
        """(instrument, day) -> the distinct English titles linked to it since `start`."""
        path = self._corpus_path or _default_corpus()
        if not Path(path).exists():
            raise SourceError(f"finbert_tone: no corpus at {path}")
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT title, published_at, instruments_json FROM articles "
                f"WHERE published_at >= ? AND language IN ({','.join('?' * len(ENGLISH))}) "
                "ORDER BY published_at",
                (start.isoformat(), *ENGLISH),
            ).fetchall()
        except sqlite3.Error as e:
            raise SourceError(f"finbert_tone: could not read the corpus: {e}") from e
        finally:
            conn.close()
        wanted = set(instruments)
        out: dict[tuple[str, str], list[str]] = defaultdict(list)
        for title, published, linked in rows:
            try:
                names = set(json.loads(linked or "[]")) & wanted
            except ValueError:
                continue
            title = " ".join(str(title or "").split())
            if not names or not title:
                continue
            day = str(published)[:10]
            for iid in names:
                if title not in out[(iid, day)]:
                    out[(iid, day)].append(title)
        return out

    def collect(
        self, since: datetime, instruments: tuple[str, ...] = (), slot: str = "all"
    ) -> Pull:
        pull = Pull()
        if not instruments:
            return pull
        since = since if since.tzinfo else since.replace(tzinfo=UTC)
        start = since.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        grouped = self.titles(start, instruments)
        pull.asked = list(instruments)
        if not grouped:
            pull.notes.append("no linked English title in the window; nothing scored")
            return pull
        keys = sorted(grouped)
        flat = [t for k in keys for t in grouped[k]]
        scores = self._model()(flat)
        if len(scores) != len(flat):
            raise SourceError(f"finbert_tone: {len(scores)} scores for {len(flat)} titles")
        today = self.today()
        i = 0
        for iid, day in keys:
            n = len(grouped[(iid, day)])
            chunk, i = scores[i : i + n], i + n
            split = {"positive": 0, "negative": 0, "neutral": 0}
            for s in chunk:
                top = max(("positive", "negative", "neutral"), key=lambda k, s=s: s.get(k, 0.0))
                split[top] += 1
            mean = Decimal(str(sum(s["score"] for s in chunk) / n)).quantize(Decimal("0.0001"))
            period = date.fromisoformat(day)
            known = max(today, period)
            pull.observations.append(
                Observation(
                    self.name,
                    iid,
                    "finbert_sentiment",
                    known_at=known,
                    value=mean,
                    period_end=period,
                    unit="score[-1,1]",
                    payload={"titles": n, **split, "model": self._version},
                )
            )
            pull.observations.append(
                Observation(
                    self.name,
                    iid,
                    "finbert_count",
                    known_at=known,
                    value=Decimal(n),
                    period_end=period,
                    unit="titles",
                )
            )
        pull.notes.append(f"scored {len(flat)} titles with {self._version}")
        return pull
