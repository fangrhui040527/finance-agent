"""The corpus as git carries it: parts, a manifest, and a stamp per checkout.

What these pin is what the fix must never do: publish a corpus with fewer rows
than the last, publish parts that do not reassemble the file, overwrite a local
file nobody can account for, or let a missing file read as an empty corpus.
Every test works on a tmp_path corpus; PART_BYTES is made small so a toy store
is several parts, the shape the real one has from its first split.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from knowledge import stores
from knowledge.corpus import Corpus
from knowledge.news.features import Article

SMALL = 8 * 4096


def _article(i: int, title: str | None = None) -> Article:
    return Article(
        doc_id=f"doc-{i:05d}",
        title=title or f"Story number {i} about the market " + "x" * 200,
        body="b" * 300,
        source_domain="example.com",
        published_at=datetime(2026, 10, 1, tzinfo=UTC),
        dup_hash=f"h{i:05d}",
        title_key=f"k{i:05d}",
    )


def _corpus(path: Path, n: int, start: int = 0) -> Path:
    with Corpus(path) as c:
        for i in range(start, start + n):
            c.add(_article(i), "fixture")
        c.record_sweep(f"run{start}", "fixture", datetime(2026, 10, 1, tzinfo=UTC), "ok")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def db(tmp_path) -> Path:
    return _corpus(tmp_path / "data" / "corpus.db", 200)


def test_split_then_assemble_gives_back_the_same_bytes(db):
    before = _sha(db)
    m = stores.split(db, part_bytes=SMALL)
    assert len(m.parts) > 2, "a toy corpus should be several parts at this size"
    assert all(p["bytes"] == SMALL for p in m.parts[:-1])
    assert 0 < m.parts[-1]["bytes"] <= SMALL
    assert m.rows == {"articles": 200, "sweeps": 1}
    assert {"articles_no_update", "articles_no_delete"} <= set(m.triggers)

    db.unlink()
    stores.stamp_path(db).unlink(missing_ok=True)
    assert stores.state(db).name == stores.MISSING
    done = stores.assemble(db)
    assert done.name == "assembled"
    assert _sha(db) == before
    assert stores.state(db).name == stores.CURRENT


def test_split_is_deterministic_and_rewrites_only_parts_that_changed(db):
    m1 = stores.split(db, part_bytes=SMALL)
    raw1 = stores.manifest_path(db).read_bytes()
    first = stores.parts_dir(db) / "part-000"
    mtime = first.stat().st_mtime_ns
    m2 = stores.split(db, part_bytes=SMALL)
    assert m1 == m2 and stores.manifest_path(db).read_bytes() == raw1
    assert first.stat().st_mtime_ns == mtime, "an unchanged part is not rewritten"
    assert "generated" not in raw1.decode() and "time" not in json.loads(raw1)


def test_a_store_that_shrinks_is_never_published(db, tmp_path):
    stores.split(db, part_bytes=SMALL)
    published = stores.manifest_path(db).read_bytes()
    # Another, smaller corpus in the same place: what a store started afresh
    # on a missing path would look like to a careless split.
    small = _corpus(tmp_path / "small" / "corpus.db", 10)
    db.write_bytes(small.read_bytes())
    with pytest.raises(stores.StoreError, match="fewer rows"):
        stores.split(db, part_bytes=SMALL)
    assert stores.manifest_path(db).read_bytes() == published


def test_a_store_that_lost_its_append_only_triggers_is_never_published(db):
    stores.split(db, part_bytes=SMALL)
    con = sqlite3.connect(db)
    con.execute("DROP TRIGGER articles_no_delete")
    con.commit()
    con.close()
    with pytest.raises(stores.StoreError, match="articles_no_delete"):
        stores.split(db, part_bytes=SMALL)


def test_split_checkpoints_rows_still_in_the_wal(db):
    """A row in the -wal is not in the file's bytes; publishing before the
    checkpoint would publish a corpus without it."""
    c = Corpus(db)
    c.add(_article(9999, "Written through a connection still open"), "fixture")
    assert db.with_name(db.name + "-wal").stat().st_size > 0
    stores.split(db, part_bytes=SMALL)
    c.close()
    rebuilt = db.parent / "rebuilt.db"
    m = stores.read_manifest(db)
    rebuilt.write_bytes(b"".join((stores.parts_dir(db) / p["name"]).read_bytes() for p in m.parts))
    con = sqlite3.connect(rebuilt)
    try:
        assert con.execute("SELECT count(*) FROM articles").fetchone()[0] == 201
    finally:
        con.close()


def test_a_tampered_part_is_refused_and_nothing_is_replaced(db):
    stores.split(db, part_bytes=SMALL)
    part = stores.parts_dir(db) / "part-001"
    data = bytearray(part.read_bytes())
    data[100] ^= 0xFF
    part.write_bytes(bytes(data))
    db.unlink()
    with pytest.raises(stores.StoreError, match="part-001"):
        stores.assemble(db)
    assert not db.exists()
    with pytest.raises(stores.StoreError):
        stores.verify(db)


def test_a_pull_that_moved_the_parts_reassembles_an_untouched_file(db):
    stores.split(db, part_bytes=SMALL)
    other = _publish_elsewhere(db, 50, 500)
    _pull(db, other)  # the parts and manifest change, the local file does not
    assert stores.state(db).name == stores.STALE
    assert stores.ensure(db).name == "assembled"
    assert _sha(db) == _sha(other)


def test_local_writes_on_the_current_version_are_ahead_and_kept(db):
    stores.split(db, part_bytes=SMALL)
    _corpus(db, 5, start=900)
    assert stores.state(db).name == stores.AHEAD
    before = _sha(db)
    assert stores.ensure(db).name == stores.AHEAD
    assert stores.assemble(db).name == stores.AHEAD
    assert _sha(db) == before, "a sweep's unsplit rows are never overwritten"


def test_local_writes_on_an_old_version_conflict_and_are_moved_aside_never_deleted(db):
    stores.split(db, part_bytes=SMALL)
    other = _publish_elsewhere(db, 50, 500)
    _pull(db, other)
    # ...and this checkout wrote rows of its own meanwhile.
    _corpus(db, 3, start=700)
    local = _sha(db)
    assert stores.state(db).name == stores.CONFLICT

    seen = []
    assert stores.ensure(db, log=seen.append).name == stores.CONFLICT
    assert _sha(db) == local and seen, "ensure leaves it, and says so"
    with pytest.raises(stores.StoreConflict):
        stores.assemble(db)
    assert _sha(db) == local

    done = stores.assemble(db, replace=True)
    assert "moved to" in done.detail
    aside = [p for p in db.parent.iterdir() if p.name.startswith("corpus.local-")]
    assert len(aside) == 1 and _sha(aside[0]) == local
    assert _sha(db) == _sha(other)


def _publish_elsewhere(db: Path, rows: int, start: int) -> Path:
    """What the collector does: the same corpus, grown and split, then pulled."""
    other = db.parent.parent / f"elsewhere-{start}" / "data" / "corpus.db"
    other.parent.mkdir(parents=True)
    other.write_bytes(db.read_bytes())
    _corpus(other, rows, start=start)
    stores.split(other, part_bytes=SMALL)
    return other


def _pull(db: Path, other: Path) -> None:
    for p in stores.parts_dir(db).iterdir():
        p.unlink()
    for p in stores.parts_dir(other).iterdir():
        (stores.parts_dir(db) / p.name).write_bytes(p.read_bytes())


def test_a_manifest_moved_back_never_costs_the_local_file_its_rows(db):
    """A split written locally and then discarded (`git checkout -- data/stores`)
    leaves the manifest OLDER than the file. A stamp could not tell that from a
    pull; which version holds which rows can."""
    stores.split(db, part_bytes=SMALL)
    published = {p.name: p.read_bytes() for p in stores.parts_dir(db).iterdir()}
    _corpus(db, 5, start=900)
    stores.split(db, part_bytes=SMALL)
    for name, data in published.items():  # the revert
        (stores.parts_dir(db) / name).write_bytes(data)
    local = _sha(db)
    assert stores.state(db).name == stores.AHEAD
    stores.ensure(db)
    assert _sha(db) == local


def test_a_file_that_does_not_hold_the_published_rows_is_never_published(db, tmp_path):
    """The collector worked on an older file than its parts describe (the size
    check had left the whole file out of the last commit). Row counts catch up
    within a day; the tip rows never do."""
    old = db.read_bytes()
    stores.split(db, part_bytes=SMALL)
    _corpus(db, 30, start=1000)  # run N: published as parts
    stores.split(db, part_bytes=SMALL)
    published = stores.manifest_path(db).read_bytes()
    db.write_bytes(old)  # run N+1 checks out the whole file from before run N
    _corpus(db, 35, start=2000)
    with pytest.raises(stores.StoreError, match="does not hold every row"):
        stores.split(db, part_bytes=SMALL)
    assert stores.manifest_path(db).read_bytes() == published


def test_a_whole_file_older_than_its_parts_is_reassembled_before_anyone_writes(db):
    """The same state, seen from the start of the next run: the manifest's
    version holds every row the checked-out file does, so it is stale - and
    ensure, which every command runs first, assembles the parts over it."""
    old = db.read_bytes()
    stores.split(db, part_bytes=SMALL)
    _corpus(db, 30, start=1000)
    stores.split(db, part_bytes=SMALL)
    newer = _sha(db)
    db.write_bytes(old)
    assert stores.state(db).name == stores.STALE
    assert stores.ensure(db).name == "assembled"
    assert _sha(db) == newer


def test_a_local_migration_is_not_a_conflict_once_the_collector_has_it_too(db):
    """A column added on first open (Corpus._migrate) changes the bytes, not the
    rows. When the parts that carry it arrive, the local file is stale, not a
    conflict that would pin the checkout to an old corpus for good."""
    stores.split(db, part_bytes=SMALL)
    other = db.parent.parent / "collector" / "data" / "corpus.db"
    other.parent.mkdir(parents=True)
    other.write_bytes(db.read_bytes())
    for path in (db, other):
        con = sqlite3.connect(path)
        con.execute("ALTER TABLE articles ADD COLUMN later TEXT NOT NULL DEFAULT ''")
        con.commit()
        con.close()
    assert stores.state(db).name == stores.AHEAD, "same rows, other bytes"
    _corpus(other, 10, start=3000)
    stores.split(other, part_bytes=SMALL)
    _pull(db, other)
    assert stores.state(db).name == stores.STALE
    stores.ensure(db)
    assert _sha(db) == _sha(other)


def test_an_orphaned_wal_beside_a_missing_corpus_is_never_paired_with_a_new_file(db):
    """A writer killed mid-session leaves a -wal with frames. Deleting the file
    to have it reassembled would put the new file beside that journal, and the
    next open would replay a different database's pages into it."""
    stores.split(db, part_bytes=SMALL)
    c = Corpus(db)
    c.add(_article(4242, "Written and never checkpointed"), "fixture")
    wal = db.with_name(db.name + "-wal").read_bytes()
    c.close()
    db.unlink()
    db.with_name(db.name + "-wal").write_bytes(wal)
    assert stores.state(db).name == stores.CONFLICT
    assert stores.ensure(db).name == stores.CONFLICT
    assert not db.exists()
    done = stores.assemble(db, replace=True)
    assert "moved to" in done.detail
    kept = [p for p in db.parent.iterdir() if p.name.endswith(".db-wal") and "local-" in p.name]
    assert len(kept) == 1 and kept[0].read_bytes() == wal, "the journal is kept, not deleted"
    con = sqlite3.connect(db)
    try:
        assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        con.close()


def test_a_local_file_that_is_not_a_database_conflicts_and_is_moved_aside(db):
    stores.split(db, part_bytes=SMALL)
    published = stores.read_manifest(db).sha256
    db.write_bytes(b"version https://git-lfs.github.com/spec/v1\n" * 200)
    assert stores.state(db).name == stores.CONFLICT
    stores.assemble(db, replace=True)
    assert _sha(db) == published
    assert any(p.name.startswith("corpus.local-") for p in db.parent.iterdir())


def test_assembly_waits_for_the_lock_and_gives_up_loudly(db, monkeypatch):
    stores.split(db, part_bytes=SMALL)
    db.unlink()
    monkeypatch.setattr(stores, "LOCK_SECONDS", 0.3)
    with stores._locked(db):
        with pytest.raises(stores.StoreBusy):
            stores.assemble(db)
        assert stores.ensure(db).name == stores.CONFLICT
    assert stores.ensure(db).name == "assembled"


def test_an_empty_file_counts_as_missing(db):
    stores.split(db, part_bytes=SMALL)
    before = stores.read_manifest(db).sha256
    db.write_bytes(b"")
    assert stores.state(db).name == stores.MISSING
    stores.ensure(db)
    assert _sha(db) == before


def test_a_governed_corpus_that_is_missing_raises_instead_of_starting_empty(db):
    """sqlite3.connect on a missing path makes an empty database, and a sweep
    reading its own history from it would refetch a slot it already ran."""
    stores.split(db, part_bytes=SMALL)
    db.unlink()
    with pytest.raises(stores.StoreError, match="stores assemble"):
        Corpus(db)
    assert not db.exists(), "the refusal creates no file"


def test_an_ungoverned_corpus_behaves_exactly_as_before(tmp_path):
    path = tmp_path / "fresh" / "corpus.db"
    assert stores.state(path).name == stores.UNGOVERNED
    assert stores.ensure(path).name == stores.UNGOVERNED
    with Corpus(path) as c:
        assert c.counts()["articles"] == 0
    assert stores.state(":memory:").name == stores.UNGOVERNED


def test_a_store_held_open_elsewhere_is_not_replaced(db):
    stores.split(db, part_bytes=SMALL)
    other = _publish_elsewhere(db, 20, 500)
    _pull(db, other)
    reader = sqlite3.connect(db)
    reader.execute("PRAGMA journal_mode=WAL")
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM articles").fetchone()
    try:
        with pytest.raises(stores.StoreBusy):
            stores.assemble(db)
    finally:
        reader.close()
    assert stores.assemble(db).name == "assembled"


def test_oversized_names_any_file_at_the_warning_size(tmp_path, monkeypatch):
    monkeypatch.setattr(stores, "WARN_BYTES", 1000)
    (tmp_path / "small.db").write_bytes(b"x" * 10)
    (tmp_path / "big.db").write_bytes(b"x" * 5000)
    assert [p.name for p, _ in stores.oversized(tmp_path)] == ["big.db"]


def test_the_committed_parts_reassemble_to_their_manifest():
    """A repository invariant, and the one the self-check's test run repeats
    every night: what git carries reassembles to what the manifest says."""
    repo = Path(__file__).resolve().parents[1]
    corpus = repo / "data" / "corpus.db"
    if not stores.governed(corpus):
        pytest.skip("no parts committed yet - the collector's first split writes them")
    m = stores.verify(corpus)
    assert all(p["bytes"] <= stores.PART_BYTES for p in m.parts)
    assert all(p["bytes"] < stores.REFUSE_BYTES for p in m.parts)
