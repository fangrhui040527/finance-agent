"""Why every DBnomics series stops in mid-2025, answered from the API itself.

On 2026-09-08 all fifteen configured series carried a current `fetched_at` and
a newest observation between 2025-05 and 2025-07 - fourteen months stale while
the fetch itself succeeded. Nothing in the collector explains it: `observations=1`
is DBnomics' include-observations flag, not a count, and `periods[-30:]` takes
the newest thirty of whatever came back. So the staleness is in what the API
returns, and only the API can say which of three things it is:

  RETIRED    our code is no longer published; siblings in the same dataset are
             current. Fix: find the replacement code.
  FROZEN     the whole dataset stops at the same period. Nothing is wrong with
             our code; the provider or DBnomics stopped ingesting. Fix: re-source.
  CURRENT    the API has fresh observations and the store does not, which would
             make it a collector bug after all.

The sibling check is what separates the first two, and it is the reason this
script exists rather than a loop over `ask.py sources --probe`: a per-series
fetch can only ever say "this one is old", which is what we already know.

Read-only. Stores nothing, needs no key.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime

sys.path.insert(0, ".")

from knowledge.sources.dbnomics import SERIES  # noqa: E402

BASE = "https://api.db.nomics.world/v22"
UA = "finance-agent-probe/1.0 (+https://github.com/fangrhui040527/finance-agent)"

#: A monthly series more than this stale is not simply "between prints".
STALE_DAYS = 120

#: How many siblings to read per dataset. Enough to tell a frozen dataset from
#: a retired code without pulling a provider's whole catalogue.
SIBLINGS = 40


def get(path: str, params: dict) -> dict:
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as resp:  # noqa: S310 - fixed host
        return json.loads(resp.read())


def newest_period(doc: dict) -> str:
    periods = doc.get("period") or doc.get("period_start_day") or []
    return str(periods[-1]) if periods else ""


def as_day(period: str) -> date | None:
    p = (period or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(p, fmt).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    if "Q" in p and len(p) == 7:  # 2026-Q2
        try:
            return date(int(p[:4]), (int(p[6]) - 1) * 3 + 1, 1)
        except ValueError:
            return None
    return None


def fetch_one(key: str) -> dict | None:
    payload = get("series", {"series_ids": key, "observations": "1", "format": "json"})
    docs = (payload.get("series") or {}).get("docs") or []
    return docs[0] if docs else None


def dataset_freshness(provider: str, dataset: str) -> tuple[str, int]:
    """The newest period any sibling in this dataset reaches, and how many read.

    A dataset whose best sibling is as stale as our series is FROZEN; one with
    current siblings means our particular code went away.
    """
    payload = get(
        "series",
        {
            "provider_code": provider,
            "dataset_code": dataset,
            "observations": "1",
            "format": "json",
            "limit": str(SIBLINGS),
        },
    )
    docs = (payload.get("series") or {}).get("docs") or []
    best = ""
    for doc in docs:
        p = newest_period(doc)
        if p > best:
            best = p
    return best, len(docs)


def main() -> int:
    today = datetime.now(UTC).date()
    rows: list[tuple[str, ...]] = []
    verdicts: dict[str, int] = {}
    dataset_cache: dict[tuple[str, str], tuple[str, int]] = {}

    for spec in SERIES:
        try:
            doc = fetch_one(spec.key)
        except Exception as e:  # noqa: BLE001 - a probe reports, never raises
            rows.append((spec.series_id, spec.key, "FETCH FAILED", str(e)[:60], "", ""))
            verdicts["FETCH FAILED"] = verdicts.get("FETCH FAILED", 0) + 1
            continue

        if doc is None:
            rows.append((spec.series_id, spec.key, "NO SERIES", "code unknown to DBnomics", "", ""))
            verdicts["NO SERIES"] = verdicts.get("NO SERIES", 0) + 1
            continue

        ours = newest_period(doc)
        day = as_day(ours)
        age = (today - day).days if day else -1
        fresh = age >= 0 and age <= STALE_DAYS

        if fresh:
            verdict, note = "CURRENT", "api is fresh - if the store is not, the collector is"
        else:
            ds = (spec.provider, spec.dataset)
            if ds not in dataset_cache:
                try:
                    dataset_cache[ds] = dataset_freshness(*ds)
                except Exception as e:  # noqa: BLE001
                    dataset_cache[ds] = (f"ERR {str(e)[:30]}", 0)
            best, n = dataset_cache[ds]
            best_day = as_day(best)
            best_age = (today - best_day).days if best_day else -1
            if best_day is not None and 0 <= best_age <= STALE_DAYS:
                verdict = "RETIRED"
                note = f"dataset reaches {best} across {n} siblings - this code did not"
            else:
                verdict = "FROZEN"
                note = f"best of {n} siblings is {best or 'nothing'} - the dataset stopped"

        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        rows.append((spec.series_id, spec.key, verdict, note, ours, str(age)))

    out = [
        f"## DBnomics probe - {today.isoformat()}",
        "",
        f"{len(SERIES)} configured series. Stale means a newest observation over "
        f"{STALE_DAYS} days old.",
        "",
        "| series | dbnomics key | verdict | newest obs | age (days) | what it means |",
        "|---|---|---|---|---|---|",
    ]
    for sid, key, verdict, note, newest, age in rows:
        out.append(f"| `{sid}` | `{key}` | **{verdict}** | {newest or '-'} | {age} | {note} |")
    out += ["", "### Tally", ""]
    for verdict, n in sorted(verdicts.items(), key=lambda kv: -kv[1]):
        out.append(f"- **{verdict}** - {n}")
    out += [
        "",
        "RETIRED means look for the replacement code on db.nomics.world and edit "
        "`SERIES` in `knowledge/sources/dbnomics.py`. FROZEN means the code is "
        "right and the upstream stopped - re-source that series elsewhere, or "
        "accept the gap and say so on the surface that reads it. CURRENT on a "
        "series the store has stale is the one answer that makes this a bug in "
        "the collector rather than in the data.",
    ]

    text = "\n".join(out)
    sys.stdout.write(text + "\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
