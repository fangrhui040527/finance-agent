"""The open-question ledger across the nightly feedback pages.

Every page carries `open_questions` per name and `open_questions_carried` at
the top, each stamped with the date first asked, and until 2026-09-06 nothing
in the code read either: they were prose copied forward by hand, and a question
that had stood for a fortnight looked exactly like one asked yesterday.
"""

from __future__ import annotations

import json
from datetime import date

from knowledge.feedback_questions import (
    answered,
    malformed,
    open_questions,
    render,
    uncarried,
)

BURSA = "The six Bursa names have no fact-book coverage; which source is the shortest path?"


def write(directory, day: str, carried: list[str], names: list[dict] | None = None) -> None:
    (directory / f"{day}.json").write_text(
        json.dumps({"day": day, "open_questions_carried": carried, "names": names or []}),
        encoding="utf-8",
    )


def test_a_question_carried_for_weeks_reads_as_weeks_old(tmp_path):
    stamped = f"{BURSA} (since 2026-09-04)"
    for day in ("2026-09-04", "2026-09-11", "2026-09-25"):
        write(tmp_path, day, [stamped])
    (q,) = open_questions(tmp_path)
    assert q.first_asked == date(2026, 9, 4) and q.last_carried == date(2026, 9, 25)
    assert q.age_days == 21 and q.nights == 3
    assert "21d" in q.line()


def test_the_date_the_writer_stamped_beats_the_file_it_appears_in(tmp_path):
    """A question carried forward keeps its original date - that is the whole
    point of writing it there - so a page that first mentions it on the 25th
    still reports it as asked on the 4th."""
    write(tmp_path, "2026-09-25", [f"{BURSA} (since 2026-09-04)"])
    (q,) = open_questions(tmp_path)
    assert q.first_asked == date(2026, 9, 4) and q.age_days == 21


def test_a_question_the_writer_stops_carrying_is_closed(tmp_path):
    write(tmp_path, "2026-09-04", [f"{BURSA} (since 2026-09-04)"])
    write(tmp_path, "2026-09-05", [])
    assert open_questions(tmp_path) == []
    (closed,) = answered(tmp_path)
    assert closed.last_carried == date(2026, 9, 4)


def test_one_question_reworded_across_pages_is_not_four_questions(tmp_path):
    """The carried list is the ledger; the per-name sections are that night's
    asking. Counting both gave every question twice, and reworded between
    nights, four times over two pages."""
    named = [
        {
            "instrument_id": "XNAS:NVDA",
            "open_questions": ["Does the fact book pick up a filing for the transaction?"],
        }
    ]
    write(tmp_path, "2026-09-04", ["NVIDIA: filing for the transaction? (since 2026-09-04)"], named)
    write(
        tmp_path,
        "2026-09-05",
        ["NVIDIA: filing for the transaction? (since 2026-09-04)"],
        [
            {
                "instrument_id": "XNAS:NVDA",
                "open_questions": [
                    "Does the fact book pick up a filing or release for the transaction, "
                    "and does a 5-session decomposition hold?"
                ],
            }
        ],
    )
    assert len(open_questions(tmp_path)) == 1


def test_a_question_asked_under_a_name_but_never_carried_is_a_page_fault(tmp_path):
    """One left only under a name is one tomorrow will not see."""
    write(
        tmp_path,
        "2026-09-05",
        ["NVIDIA: filing for the transaction? (since 2026-09-04)"],
        [
            {"instrument_id": "XNAS:NVDA", "open_questions": ["NVIDIA: filing for it?"]},
            {"instrument_id": "MYX:5347", "open_questions": ["Did Tenaga print its results?"]},
        ],
    )
    assert uncarried(tmp_path) == [("MYX:5347", "Did Tenaga print its results?")]
    assert "NOT CARRIED" in render(tmp_path)


def test_a_page_that_will_not_parse_is_a_page_not_a_crash(tmp_path):
    write(tmp_path, "2026-09-04", [f"{BURSA} (since 2026-09-04)"])
    (tmp_path / "2026-09-05.json").write_text("{ not json", encoding="utf-8")
    assert len(open_questions(tmp_path)) == 1


def test_a_page_carrying_a_count_is_skipped_and_named_not_a_crash(tmp_path):
    """2026-09-24: the count of questions (23) sat where the list belongs. The
    page is skipped whole - reading the bad field as empty would close every
    question it meant to carry - and the ledger stands on the page before."""
    write(tmp_path, "2026-09-04", [f"{BURSA} (since 2026-09-04)"])
    (tmp_path / "2026-09-05.json").write_text(
        json.dumps({"day": "2026-09-05", "open_questions_carried": 23}), encoding="utf-8"
    )
    (q,) = open_questions(tmp_path)
    assert q.last_carried == date(2026, 9, 4)
    assert answered(tmp_path) == []
    (fault,) = malformed(tmp_path)
    assert fault[0] == "2026-09-05.json" and "int, not a list" in fault[1]
    assert "SKIPPED, NOT READ: 1" in render(tmp_path)


def test_a_name_whose_questions_are_not_a_list_is_skipped_too(tmp_path):
    write(tmp_path, "2026-09-04", [f"{BURSA} (since 2026-09-04)"])
    write(
        tmp_path,
        "2026-09-05",
        [f"{BURSA} (since 2026-09-04)"],
        [{"instrument_id": "MYX:1155", "open_questions": "is it an ex-date?"}],
    )
    assert uncarried(tmp_path) == []  # read from the good page, not the skipped one
    (fault,) = malformed(tmp_path)
    assert "MYX:1155" in fault[1]


def test_an_unparseable_page_is_named_as_skipped(tmp_path):
    (tmp_path / "2026-09-05.json").write_text("{ not json", encoding="utf-8")
    assert malformed(tmp_path)[0][0] == "2026-09-05.json"


def test_well_formed_pages_have_no_faults(tmp_path):
    write(tmp_path, "2026-09-04", [f"{BURSA} (since 2026-09-04)"])
    assert malformed(tmp_path) == []


def test_no_pages_says_so_rather_than_reporting_nothing_open(tmp_path):
    assert open_questions(tmp_path) == []
    assert "NO FEEDBACK PAGES" in render(tmp_path)
