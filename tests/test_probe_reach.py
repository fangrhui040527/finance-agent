"""The reachability table both runner probes print, exercised without the network.

The probes run only on a runner - this environment has no route to Bursa, BNM,
DOSM or the Malaysian outlets - so one dispatch has to record, for every
target, enough to tell a moved file from a bot wall: status, content type,
final URL, bytes and the first of them. What is checked here is that the dates
asked for are the right ones, that a row survives being put in a markdown
table, that one failure does not stop the rest, and that the targets the
2026-10-28 research asked for are on the lists.
"""

from __future__ import annotations

import importlib.util
import io
import re
import urllib.error
import urllib.response
from datetime import date
from email.message import Message
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / ".github" / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bursa = _load("bursa_feeds_probe")
macro = _load("macro_alternatives_probe")
reach = bursa.reach


@pytest.fixture(autouse=True)
def _fresh_budget(monkeypatch):
    """The probe's run budget counts from import unless main() resets it; a slow
    suite (Windows CI) can spend BUDGET before these tests call fetch() directly."""
    monkeypatch.setattr(bursa, "STARTED", bursa.time.monotonic())


def test_the_last_trading_day_is_the_weekday_before_the_run():
    assert reach.last_trading_day(date(2026, 10, 28)) == date(2026, 10, 27)  # Wed -> Tue
    assert reach.last_trading_day(date(2026, 10, 26)) == date(2026, 10, 23)  # Mon -> Fri
    assert reach.last_trading_day(date(2026, 10, 25)) == date(2026, 10, 23)  # Sun -> Fri
    assert reach.last_trading_day(date(2026, 10, 24)) == date(2026, 10, 23)  # Sat -> Fri


def test_the_first_bytes_are_printable_and_cannot_break_a_table_row():
    head = reach.printable(b"%PDF-1.7\n\x00\xff a|b`c\\d", 200)
    assert head == "%PDF-1.7\\n\\x00\\xff a\\x7cb\\x60c\\x5cd"
    assert len(reach.printable(b"x" * 500)) == reach.HEAD


def test_a_row_names_status_type_final_url_bytes_and_head():
    r = reach.Reach(
        "net_short_position_20260717",
        "https://www.bursamalaysia.com/misc/missftp/sblnt/NetShortPosition20260717.pdf",
        status=200,
        content_type="application/pdf",
        final_url="https://www.bursamalaysia.com/elsewhere.pdf",
        length="51234",
        head="%PDF-1.4",
    )
    row = reach.table_row(r)
    assert row.count("|") == 8, row  # seven cells, nothing inside them split a column
    for part in ("200", "application/pdf", "elsewhere.pdf", "51234", "`%PDF-1.4`"):
        assert part in row, (part, row)
    err = reach.table_row(reach.Reach("gdelt", "http://x/", error="URLError: a | b"))
    assert "ERR URLError: a \\| b" in err and len(re.findall(r"(?<!\\)\|", err)) == 8, err
    assert reach.table([r]).splitlines()[0].startswith("| target | url | status")


def test_fetch_records_an_error_page_and_a_dead_host_without_raising(monkeypatch):
    hdrs = Message()
    hdrs["Content-Type"] = "text/html"

    def http_error(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url, 403, "Forbidden", hdrs, io.BytesIO(b"<html>denied</html>")
        )

    monkeypatch.setattr(reach.urllib.request, "urlopen", http_error)
    r = reach.fetch("t", "https://example.invalid/a.pdf", "probe/1.0")
    assert (r.status, r.content_type, r.length, r.head) == (
        403,
        "text/html",
        "19",
        "<html>denied</html>",
    )
    assert not r.error

    def dead(req, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(reach.urllib.request, "urlopen", dead)
    r = reach.fetch("t", "https://example.invalid/", "probe/1.0")
    assert r.status == 0 and r.error == "TimeoutError: timed out"


def test_fetch_records_a_redirected_answer_and_caps_what_it_reads(monkeypatch):
    hdrs = Message()
    hdrs["Content-Type"] = "application/octet-stream"
    hdrs["Content-Length"] = "9000000"
    monkeypatch.setattr(reach, "MAX_BYTES", 10)

    def ok(req, timeout):
        body = io.BytesIO(b"PAR1" + b"\x00" * 50)
        return urllib.response.addinfourl(body, hdrs, "https://moved.example/ipi.parquet", 200)

    monkeypatch.setattr(reach.urllib.request, "urlopen", ok)
    r = reach.fetch("dosm_ipi_parquet", "https://storage.example/ipi.parquet", "probe/1.0")
    assert (r.status, r.final_url, r.length) == (
        200,
        "https://moved.example/ipi.parquet",
        "9000000",
    )
    assert r.head.startswith("PAR1\\x00") and len(r.body) == 10
    assert "-> https://moved.example/ipi.parquet" in reach.log_line(r)


def test_fetch_sends_the_honest_user_agent(monkeypatch):
    seen = {}

    def capture(req, timeout):
        seen["ua"] = req.get_header("User-agent")
        seen["timeout"] = timeout
        raise OSError("offline")

    monkeypatch.setattr(reach.urllib.request, "urlopen", capture)
    bursa.fetch("https://www.bursamalaysia.com/robots.txt", "bursa_robots")
    assert seen["ua"] == bursa.UA and "finance-agent-probe" in seen["ua"]
    assert seen["timeout"] <= 15
    assert bursa.FETCHED[-1].name == "bursa_robots"


def test_bursa_targets_ask_the_last_weekday_and_the_known_day():
    urls = dict(bursa.bursa_targets(date(2026, 10, 26)))  # a Monday
    missftp = "https://www.bursamalaysia.com/misc/missftp/"
    assert urls["bursa_robots"] == "https://www.bursamalaysia.com/robots.txt"
    assert urls["shortsell_trades_20261023"] == (
        missftp + "securities/securities_equities_rss_20261023.pdf"
    )
    assert urls["shortsell_trades_20260710"].endswith("securities_equities_rss_20260710.pdf")
    assert urls["net_short_position_20260717"] == missftp + "sblnt/NetShortPosition20260717.pdf"
    assert urls["daily_scoreboard_20260923"].endswith("daily_scoreboard_20260923.pdf")
    assert urls["key_indicators_20260707"].endswith("keyindicators_20260707.pdf")
    assert urls["trading_participation"].endswith("/market_information/market_statistic/securities")
    assert any("lseg.com" in u for u in urls.values())


def test_the_new_outlets_extend_the_existing_list():
    hosts = {u.split("/")[2] for pages, cands in bursa.OUTLETS.values() for u in (*pages, *cands)}
    for host in (
        "www.malaymail.com",
        "themalaysianreserve.com",
        "www.businesstoday.com.my",
        "bernamabiz.com",
        "www.thestar.com.my",
    ):
        assert host in hosts, host
    assert "https://www.thestar.com.my/RSS" in bursa.OUTLETS["thestar_business"][1]
    assert "https://www.malaymail.com/feed/rss/money" in bursa.OUTLETS["malaymail_money"][1]


def test_an_fbm100_link_is_read_off_a_page():
    html = (
        b'<a href="/misc/system/assets/1/FBM100_Constituents_List_Jun2026.pdf">list</a>'
        b'<a href="/other.pdf">x</a>'
    )
    links = reach.links_matching(html, "https://www.bursamalaysia.com/a/b", bursa.FBM100_HREF)
    assert links == [
        "https://www.bursamalaysia.com/misc/system/assets/1/FBM100_Constituents_List_Jun2026.pdf"
    ]


def test_the_macro_targets_are_keyless_and_cover_the_research_list():
    urls = [u for u, _ in macro.REACH_TARGETS.values()]
    for want in (
        "api.bnm.gov.my/public/msb/1.13",
        "api.bnm.gov.my/public/interest-rate",
        "api.bnm.gov.my/public/kl-usd-reference-rate",
        "www.bnm.gov.my/government-securities-yield",
        "finance/chart/BZ%3DF",
        "finance/chart/ALI%3DF",
        "singlebuyer.com.my",
        "worldbank.org/en/research/commodity-markets",
        "storage.dosm.gov.my/ipi/ipi.parquet",
        "storage.dosm.gov.my/trade/trade_sitc_1d.parquet",
        "data.gdeltproject.org/gdeltv2/lastupdate.txt",
    ):
        assert any(want in u for u in urls), want
    # The table prints URLs, so nothing in it may carry a key.
    assert not any("api_key" in u or "stlouisfed" in u for u in urls)
    bnm = macro.REACH_TARGETS["bnm_msb_1.13"][1]
    assert bnm == {"Accept": "application/vnd.BNM.API.v1+json"}


def test_one_dead_target_does_not_stop_the_rest_and_the_pink_sheet_is_followed(monkeypatch):
    asked: list[str] = []

    def fake(name, url, ua, headers=None, timeout=reach.TIMEOUT):
        asked.append(url)
        if "singlebuyer" in url:
            return reach.Reach(name, url, error="URLError: refused")
        body = b""
        if url == macro.WORLDBANK_CMO:
            body = b'<a href="https://thedocs.worldbank.org/x/CMO-Historical-Data-Monthly.xlsx">'
        return reach.Reach(name, url, status=200, final_url=url, body=body)

    monkeypatch.setattr(reach, "fetch", fake)
    rows = macro.probe_reach()
    assert len(rows) == len(macro.REACH_TARGETS) + 1
    xlsx = "https://thedocs.worldbank.org/x/CMO-Historical-Data-Monthly.xlsx"
    assert asked[asked.index(macro.WORLDBANK_CMO) + 1] == xlsx
    assert any(r.error for r in rows) and rows[-1].name == "gdelt_lastupdate"


def test_the_bursa_day_skips_its_holidays_and_falls_back_to_weekdays(monkeypatch):
    # 2026-11-09 is Deepavali on the XKLS calendar: the day after asks for Friday.
    assert bursa.last_bursa_day(date(2026, 11, 10)) == date(2026, 11, 6)
    assert bursa.last_bursa_day(date(2026, 10, 26)) == date(2026, 10, 23)
    monkeypatch.setitem(__import__("sys").modules, "markets.registry", None)  # import fails
    assert bursa.last_bursa_day(date(2026, 11, 10)) == date(2026, 11, 9)


def test_fbm100_links_resolve_after_redirects_and_fbm100_comes_first(monkeypatch):
    page = "https://www.lseg.com/en/ftse-russell/indices/ftse-bursa-malaysia"
    moved = "https://www.lseg.com/content/dam/ftse/index/"
    others = "".join(f'<a href="KLCI_{i}_constituents.pdf">x</a>' for i in range(6))
    html = (others + '<a href="FBM100_Constituents_List_Jun2026.pdf">x</a>').encode()
    asked: list[str] = []

    def fake(name, url, ua, headers=None, timeout=reach.TIMEOUT):
        asked.append(url)
        final = moved if url == page else url
        return reach.Reach(name, url, status=200, final_url=final, body=html)

    monkeypatch.setattr(bursa, "FETCHED", [])
    monkeypatch.setattr(bursa, "FBM100_PAGES", (page,))
    monkeypatch.setattr(reach, "fetch", fake)
    bursa.probe_bursa_data(date(2026, 10, 28))
    followed = asked[asked.index(page) + 1 :]
    assert len(followed) == 5, followed
    assert followed[0] == moved + "FBM100_Constituents_List_Jun2026.pdf"


def test_past_the_budget_a_url_is_recorded_as_skipped_not_asked(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("asked the network past the budget")

    monkeypatch.setattr(bursa, "FETCHED", [])
    monkeypatch.setattr(reach, "fetch", boom)
    monkeypatch.setattr(bursa, "STARTED", bursa.time.monotonic() - bursa.BUDGET - 1)
    status, body, err = bursa.fetch("https://www.bursamalaysia.com/robots.txt", "bursa_robots")
    assert (status, body) == (0, b"") and err.startswith("skipped")
    assert bursa.FETCHED[-1].error == err and bursa.FETCHED[-1].name == "bursa_robots"


def test_bursa_data_alone_gets_no_outlet_verdict_and_blocks_reach_the_summary(
    monkeypatch, tmp_path
):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(bursa.sys, "argv", ["probe", "bursa_data"])
    monkeypatch.setattr(bursa, "FETCHED", [])
    blocks: list[str] = []
    real_emit = bursa.emit

    def spy(lines):
        blocks.append("\n".join(lines))
        real_emit(lines)

    monkeypatch.setattr(bursa, "emit", spy)
    monkeypatch.setattr(
        reach,
        "fetch",
        lambda name, url, ua, headers=None, timeout=15: reach.Reach(name, url, status=404),
    )
    assert bursa.main() == 0
    text = summary.read_text(encoding="utf-8")
    assert "NO USABLE FEED" not in text and "Malaysian business feeds" not in text
    assert "bursa_data" in text and "Reachability from this runner" in text
    assert len(blocks) == 1, blocks


def test_each_outlet_block_is_written_as_it_finishes(monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(bursa.sys, "argv", ["probe", "bernamabiz", "malaymail_money"])
    monkeypatch.setattr(bursa, "FETCHED", [])
    seen_at_fetch: list[str] = []

    def fake(name, url, ua, headers=None, timeout=15):
        seen_at_fetch.append(summary.read_text(encoding="utf-8") if summary.exists() else "")
        return reach.Reach(name, url, error="URLError: refused")

    monkeypatch.setattr(reach, "fetch", fake)
    bursa.main()
    # By the time bernamabiz is asked, malaymail's block is already in the summary.
    assert "=== malaymail_money" in seen_at_fetch[-1]
    assert "=== bernamabiz" not in seen_at_fetch[-1]
    assert "NO USABLE FEED FOUND" in summary.read_text(encoding="utf-8")
