"""Free replacements for the macro series that died, asked of the providers.

WHY THIS EXISTS. Fifteen DBnomics series froze upstream in mid-2025 (see
details/07 section 12). Most of them are context this book can live without.
Four are not, and one of those four sets a number the valuations actually use:

  DBN:GOVT_YIELD_MY   498 days old, and it is the RINGGIT RISK-FREE RATE -
                      engines/valuation/cost_of_capital.py discounts every
                      Malaysian name off it. A 2025 rate under a 2026 valuation.
  DBN:ALUMINIUM_USD   467 days old. Press Metal (MYX:8869) is an aluminium
                      smelter; this is its input price.
  DBN:BRENT_USD       467 days old. Petronas Chemicals (MYX:5183) is a
                      petrochemical; this is its feedstock.
  DBN:LNG_ASIA_USD    467 days old. Same name, the other feedstock.

TWO OF THE FIFTEEN NEEDED NO REPLACEMENT AT ALL, which is worth saying before
anyone goes shopping: `DBN:POLICY_RATE_MY` is already carried live by BNM:OPR
(8 days old against 467, and the dead one reads 3.0 where BNM says 2.75 - stale
AND wrong), and `DBN:CPI_MY` is already carried by DOSM:CPI_HEADLINE. Palm oil
is dropped from this list because no name in the current book is a plantation.

THE HYPOTHESIS THIS TESTS. FRED already has a working key and a working
collector here, and it republishes a great deal of the same IMF material. So
the question is not "where can this data be bought" but "is the SAME NUMBER
already reachable through a source that is already wired up". The discriminator
is the PUBLISHER behind the series, not the provider in front of it: a FRED
series sourced from IMF PCPS will be exactly as frozen as DBnomics was, and one
sourced from the EIA or a central bank will not. Both kinds are probed on
purpose, so the answer distinguishes them instead of assuming.

Read-only. FRED needs FRED_API_KEY (already a repository secret); the World
Bank and BNM candidates are keyless. Stores nothing. Manual: the answer changes
when a publisher stops, not hourly.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

TIMEOUT = 30
UA = "finance-agent-probe/1.0 (+https://github.com/fangrhui040527/finance-agent)"

#: Past this, a monthly series is not "between prints" - it has stopped. Same
#: figure knowledge/sources/freshness.MONTHLY uses, for the same reason.
MONTHLY_LIMIT = 70
#: A daily market series: a long weekend and a holiday.
DAILY_LIMIT = 7

#: concept -> (what it is for, [(provider, id, publisher behind it)])
#: The publisher column is the point: it predicts the answer before the fetch.
WANTED: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "malaysia government bond yield": (
        "the RINGGIT RISK-FREE RATE - cost_of_capital discounts every Bursa name off it",
        [
            ("fred", "INTGSBMYM193N", "IMF IFS (expected frozen - same source that died)"),
            ("fred", "IRLTLT01MYM156N", "OECD MEI (Malaysia is not OECD; may not exist)"),
            ("bnm", "msb/1.13", "Bank Negara, monthly statistical bulletin"),
            ("bnm", "interest-rate", "Bank Negara"),
            ("bnm", "base-rate", "Bank Negara"),
        ],
    ),
    "aluminium": (
        "Press Metal MYX:8869 is an aluminium smelter - this is its input price",
        [
            ("fred", "PALUMUSDM", "IMF PCPS (expected frozen - same source that died)"),
            ("worldbank", "pink-sheet", "World Bank commodity 'Pink Sheet'"),
        ],
    ),
    "brent crude": (
        "Petronas Chemicals MYX:5183 feedstock",
        [
            ("fred", "DCOILBRENTEU", "US EIA, DAILY - a different publisher entirely"),
            ("fred", "POILBREUSDM", "IMF PCPS (expected frozen)"),
        ],
    ),
    "lng, asia": (
        "Petronas Chemicals MYX:5183, the other feedstock",
        [
            ("fred", "PNGASJPUSDM", "IMF PCPS (expected frozen)"),
            ("fred", "DHHNGSP", "US EIA Henry Hub, DAILY - US gas, an imperfect proxy"),
        ],
    ),
}


def get(url: str, headers: dict | None = None) -> tuple[int, bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 - fixed hosts
            return r.status, r.read(), ""
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:400], ""
    except Exception as e:
        return 0, b"", f"{type(e).__name__}: {e}"


def newest_from_fred(series_id: str, key: str) -> tuple[date | None, str]:
    """(newest observation day, note). FRED answers JSON with a dated list."""
    if not key:
        return None, "FRED_API_KEY not set on this runner"
    q = urllib.parse.urlencode(
        {
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 5,
        }
    )
    status, body, err = get(f"https://api.stlouisfed.org/fred/series/observations?{q}")
    if err:
        return None, err
    if status != 200:
        # FRED says why in the body, and "does not exist" is a real answer here.
        text = body.decode("utf-8", "replace")
        return None, f"HTTP {status}: {text[:120]}"
    try:
        obs = json.loads(body).get("observations") or []
    except json.JSONDecodeError:
        return None, "not JSON"
    for o in obs:  # newest first; "." is FRED's missing-value marker
        if o.get("value") not in (".", "", None):
            try:
                return date.fromisoformat(o["date"]), f"value {o['value']}"
            except (KeyError, ValueError):
                continue
    return None, "answered, but every recent observation is empty"


def newest_from_bnm(path: str) -> tuple[date | None, str]:
    """Bank Negara's open API. Shapes differ per endpoint, so this reports what
    it found rather than pretending to a schema it has not seen."""
    status, body, err = get(
        f"https://api.bnm.gov.my/public/{path}",
        {"Accept": "application/vnd.BNM.API.v1+json"},
    )
    if err:
        return None, err
    if status != 200:
        return None, f"HTTP {status}"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None, "not JSON"
    data = payload.get("data")
    if not data:
        return None, f"no data key; keys were {list(payload)[:6]}"
    rows = data if isinstance(data, list) else [data]
    best: date | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        for k, v in row.items():
            if "date" in k.lower() and isinstance(v, str):
                try:
                    d = date.fromisoformat(v[:10])
                except ValueError:
                    continue
                best = d if best is None else max(best, d)
    shape = list(rows[0])[:8] if isinstance(rows[0], dict) else type(rows[0]).__name__
    return best, f"{len(rows)} rows, fields {shape}"


def newest_from_worldbank(_: str) -> tuple[date | None, str]:
    """The Pink Sheet is a spreadsheet, not an API. Reported as such rather
    than scraped: a monthly XLSX is a different kind of integration and the
    point of this probe is what can be fetched as data today."""
    status, _body, err = get(
        "https://thedocs.worldbank.org/en/doc/"
        "18675f1d1639c7a34d463f59263ba0a2-0050012025/related/CMO-Historical-Data-Monthly.xlsx"
    )
    if err:
        return None, f"unreachable: {err}"
    if status != 200:
        return None, f"HTTP {status} - no keyless JSON route; XLSX only"
    return None, "reachable, but XLSX only - would need a parser, not a collector"


FETCHERS = {"fred": newest_from_fred, "bnm": newest_from_bnm, "worldbank": newest_from_worldbank}


def verdict(newest: date | None, today: date, daily: bool) -> str:
    if newest is None:
        return "NO DATA"
    age = (today - newest).days
    limit = DAILY_LIMIT if daily else MONTHLY_LIMIT
    return f"{'LIVE  ' if age <= limit else 'FROZEN'} newest {newest} ({age}d)"


def main() -> int:
    key = os.environ.get("FRED_API_KEY", "")
    today = datetime.now().date()
    out: list[str] = ["## Free replacements for the dead macro series", ""]
    out.append(f"FRED_API_KEY {'is set' if key else 'IS NOT SET - FRED rows will all say so'}")
    usable: list[str] = []

    for concept, (why, candidates) in WANTED.items():
        out.append(f"\n=== {concept} " + "=" * max(0, 52 - len(concept)))
        out.append(f"  why it matters: {why}")
        for provider, ident, publisher in candidates:
            daily = ident in {"DCOILBRENTEU", "DHHNGSP"}
            newest, note = (
                FETCHERS[provider](ident, key)
                if provider == "fred"
                else (FETCHERS[provider](ident))
            )
            v = verdict(newest, today, daily)
            out.append(f"  {provider:<10} {ident:<16} {v}")
            out.append(f"  {'':10} {'':16} publisher: {publisher}")
            out.append(f"  {'':10} {'':16} {note}")
            if v.startswith("LIVE"):
                usable.append(f"  {concept:<32} {provider}:{ident}  ({publisher})")

    out.append("\n" + "=" * 64)
    if usable:
        out.append("USABLE TODAY - these are live and reachable with what is already wired:")
        out += usable
        out.append("")
        out.append("A FRED row here is a one-line change: add the id to")
        out.append("knowledge/sources/fred.SERIES and a cadence to freshness.MAX_AGE_DAYS.")
    else:
        out.append("NOTHING LIVE FOUND. If every FRED row also says FROZEN, that is the")
        out.append("finding: the publisher stopped, not the aggregator, and no free")
        out.append("re-routing exists - which makes it a purchasing decision, recorded.")

    text = "\n".join(out)
    sys.stdout.write(text + "\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("```\n" + text + "\n```\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
