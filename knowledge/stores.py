"""The corpus as git carries it: fixed-size parts and a manifest.

GitHub warns at 50 MiB a file and refuses a push holding one over 100 MiB.
data/corpus.db was 17.5 MB on 2026-10-22 and growing 0.45-0.6 MB a day, which
puts the warning near Christmas and the refusal in the spring - and the
refusal is not a warning on the run. `git push` fails, the retry loop in
collect.yml fails three times, and the day's collection stays on a runner that
is then thrown away. Every day after that does the same.

Nothing here touches a row. The corpus is append-only and is its own evidence,
so the fix moves BYTES, not records:

    data/stores/corpus/part-000      the file's first 16 MiB
    data/stores/corpus/part-001      the next 16 MiB, and so on
    data/stores/corpus/manifest.json what the parts concatenate to

`split` writes the parts after a sweep; `assemble` concatenates them back into
the one data/corpus.db every reader opens, byte for byte. No part is ever larger
than `PART_BYTES`, so no committed file grows toward either limit: more data is
more parts. Git's deltas still work across versions of a part (measured on the
10-21 and 10-22 corpora: about 0.15 MB a version, what the whole file costs).

Why not the alternatives, measured on copies of the real stores (2026-10-22):

  - Git LFS stores every version whole: ~72 MB a day of quota that can never be
    reclaimed against 10 GiB, and over the bandwidth cap checkouts get pointer
    files and the sweep crashes on "file is not a database".
  - Monthly partition files break the one-file contract every reader relies
    on: dedup is one INSERT against one file's keys, the sweeps watermarks
    read all history, and pages resolve doc_ids with one `substr` query.
  - Shrinking the file (hashed ids, compression, WITHOUT ROWID) rewrites rows
    the triggers forbid touching, and buys weeks: the 50 MiB date moves from
    December to January at best.

WHICH VERSION HOLDS WHICH ROWS decides everything, never a stamp and never a
row count. Every table is append-only and its rowids only grow, so a version
holds every row of another exactly when it holds the other's LAST row of each
table at the same rowid with the same values - its tip. The manifest records
the tips of the version it describes. That is how:

  - a split refuses a file that does not hold every row already published
    (the collector working on an older file than its parts describe, after the
    size check left the whole file out of a commit, would otherwise publish a
    corpus missing the run before it - row counts catch up within a day);
  - a pull is told from a revert: replacing the local file is allowed only
    when the manifest's version holds every row the local file does;
  - a local file written to since (a sweep's rows, a migration's column) is
    never overwritten; with `replace=True` it is moved aside, never deleted.

See docs/14-OPERATIONS-RUNBOOK.md, "Large stores".
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

#: 16 MiB. A multiple of every SQLite page size, so a part boundary never cuts
#: a page in two (it would not matter to the bytes, but it keeps a part diff
#: readable as whole pages). Small enough that the corpus is several parts from
#: the first split, so the multi-part path runs in production from day one
#: rather than from the day the file first crosses the size.
PART_BYTES = 16 * 1024 * 1024

#: GitHub's limits, in the units GitHub states them in (MiB).
WARN_BYTES = 45 * 1024 * 1024
"""Five MiB under GitHub's 50 MiB warning: early enough to act on."""
REFUSE_BYTES = 95 * 1024 * 1024
"""Five MiB under the 100 MiB refusal: a store this size is not pushed."""

#: The stores git carries as parts, by file name. The graph is not here: it is
#: rebuilt from the corpus (knowledge/graph/derive.py), not carried at all.
SPLIT = ("corpus.db",)

MANIFEST = "manifest.json"
FORMAT = 1
_PART = re.compile(r"part-(\d{3})")

#: How long a second process waits for the first to finish assembling.
LOCK_SECONDS = 120

# The states a local file can be in against its manifest.
UNGOVERNED = "ungoverned"
"""No manifest: this store is not carried as parts, and nothing here applies."""
MISSING = "missing"
"""No file, or an empty one (what `sqlite3.connect` leaves on a missing path)."""
CURRENT = "current"
"""The file is exactly what the manifest describes."""
AHEAD = "ahead"
"""Holds every row the manifest's version does, and more (a sweep's rows, not
yet split) or the same rows in other bytes (a migration's column)."""
STALE = "stale"
"""The manifest's version holds every row the local file does, and more: a pull."""
CONFLICT = "conflict"
"""Each holds rows the other lacks, or the local file is not a database."""


class StoreError(RuntimeError):
    """A store could not be split, verified or assembled. Nothing was replaced."""


class StoreConflict(StoreError):
    """The local file holds rows the manifest's version does not."""


class StoreBusy(StoreError):
    """Another connection holds the database open; replacing it now would
    strand that connection's journal beside a file it no longer describes."""


@dataclass(frozen=True)
class Manifest:
    file: str
    bytes: int
    sha256: str
    page_size: int
    part_bytes: int
    parts: tuple[dict, ...]
    rows: dict[str, int]
    triggers: tuple[str, ...]
    tips: dict[str, dict] = field(default_factory=dict)
    """Per table, the last row: {"rowid": n, "columns": {name: sha256[:16]}}."""

    def to_json(self) -> str:
        return (
            json.dumps(
                {
                    "format": FORMAT,
                    "file": self.file,
                    "bytes": self.bytes,
                    "sha256": self.sha256,
                    "page_size": self.page_size,
                    "part_bytes": self.part_bytes,
                    "parts": list(self.parts),
                    "rows": self.rows,
                    "tips": self.tips,
                    "triggers": list(self.triggers),
                },
                indent=1,
                sort_keys=True,
            )
            + "\n"
        )

    @classmethod
    def from_json(cls, raw: str) -> Manifest:
        d = json.loads(raw)
        if d.get("format") != FORMAT:
            raise StoreError(f"manifest format {d.get('format')!r}; this code reads {FORMAT}")
        return cls(
            file=d["file"],
            bytes=int(d["bytes"]),
            sha256=d["sha256"],
            page_size=int(d["page_size"]),
            part_bytes=int(d["part_bytes"]),
            parts=tuple(d["parts"]),
            rows={k: int(v) for k, v in d["rows"].items()},
            triggers=tuple(d["triggers"]),
            tips=dict(d.get("tips") or {}),
        )


@dataclass(frozen=True)
class State:
    name: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.name}: {self.detail}" if self.detail else self.name


# -- paths -------------------------------------------------------------------


def parts_dir(db: str | Path) -> Path:
    """data/corpus.db -> data/stores/corpus/. Beside the database it describes,
    so a test's tmp_path corpus is never governed by the repository's manifest."""
    db = Path(db)
    return db.parent / "stores" / db.stem


def manifest_path(db: str | Path) -> Path:
    return parts_dir(db) / MANIFEST


def stamp_path(db: str | Path) -> Path:
    db = Path(db)
    return db.with_name(db.name + ".stamp.json")


def _side(db: Path, suffix: str) -> Path:
    return db.with_name(db.name + suffix)


def governed(db: str | Path) -> bool:
    """True when git carries this store as parts. A path of ':memory:' never is."""
    return str(db) != ":memory:" and manifest_path(db).exists()


def read_manifest(db: str | Path) -> Manifest | None:
    path = manifest_path(db)
    if not path.exists():
        return None
    return Manifest.from_json(path.read_text(encoding="utf-8"))


def _manifest(db: Path) -> Manifest:
    m = read_manifest(db)
    if m is None:
        raise StoreError(f"no manifest at {manifest_path(db)}")
    return m


# -- hashing -----------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _wal_bytes(db: Path) -> int:
    wal = _side(db, "-wal")
    return wal.stat().st_size if wal.exists() else 0


def _db_sha(db: Path) -> str:
    """The file's sha256, cached in the stamp against size and mtime.

    Hashing 17 MB costs ~40 ms; the stamp makes the common case (nothing
    changed since the last command) a single stat. It is a cache and nothing
    else - no decision reads it - and it is written only when the file did not
    change while it was being hashed."""
    before = db.stat()
    try:
        stamp = json.loads(stamp_path(db).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stamp = {}
    if stamp.get("size") == before.st_size and stamp.get("mtime_ns") == before.st_mtime_ns:
        return stamp["db_sha256"]
    sha = _sha256_file(db)
    after = db.stat()
    if (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns):
        body = {"db_sha256": sha, "size": after.st_size, "mtime_ns": after.st_mtime_ns}
        try:
            _atomic_write(stamp_path(db), (json.dumps(body, sort_keys=True) + "\n").encode())
        except OSError:
            pass  # a read-only checkout still answers; it just hashes again next time
    return sha


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    with tmp.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _value_sha(value) -> str:
    if isinstance(value, bytes):
        raw = b"b" + value
    else:
        raw = json.dumps(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


# -- tips: which version holds which rows ------------------------------------


def _tables(con: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]


def _tips(con: sqlite3.Connection) -> dict[str, dict]:
    """Each table's last row, by rowid, as per-column hashes."""
    out = {}
    for t in _tables(con):
        cols = _columns(con, t)
        quoted = ", ".join(f'"{c}"' for c in cols)
        row = con.execute(
            f'SELECT rowid, {quoted} FROM "{t}" ORDER BY rowid DESC LIMIT 1'
        ).fetchone()
        if row is None:
            out[t] = {"rowid": 0, "columns": {}}
        else:
            out[t] = {
                "rowid": row[0],
                "columns": {c: _value_sha(v) for c, v in zip(cols, row[1:], strict=True)},
            }
    return out


def _holds(con: sqlite3.Connection, tips: dict[str, dict]) -> bool:
    """Does this database hold every row the version with these tips held?

    Append-only tables whose rowids only grow: holding the tip row - same
    rowid, same values in every column both versions have - is holding every
    row before it. Columns one side lacks are a migration, not a row."""
    have = set(_tables(con))
    for table, tip in tips.items():
        if not tip.get("rowid"):
            continue
        if table not in have:
            return False
        common = [c for c in _columns(con, table) if c in tip["columns"]]
        if not common:
            return False
        quoted = ", ".join(f'"{c}"' for c in common)
        row = con.execute(
            f'SELECT {quoted} FROM "{table}" WHERE rowid = ?', (tip["rowid"],)
        ).fetchone()
        if row is None:
            return False
        if any(_value_sha(v) != tip["columns"][c] for c, v in zip(common, row, strict=True)):
            return False
    return True


_HEADER = b"SQLite format 3\x00"


def _is_sqlite(path: Path) -> bool:
    """From the bytes, before any connection: SQLite opening a file that is not
    a database still opens its -wal, and on close checkpoints the journal into
    the file and unlinks it - the rows in it go with it."""
    try:
        with path.open("rb") as f:
            return f.read(len(_HEADER)) == _HEADER
    except OSError:
        return False


def _connect(db: Path) -> sqlite3.Connection:
    """Read-only, by the URI: a classification must never checkpoint, recover
    or unlink anyone's journal on close, which a read-write connection does
    even under `PRAGMA query_only`."""
    return sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=10)


# -- the lock ----------------------------------------------------------------


@contextmanager
def _locked(db: Path, seconds: float | None = None) -> Iterator[None]:
    """One process at a time decides about and replaces `db`.

    Without it, two commands started together after a pull both judge the file
    stale, and the slower one's replace lands on the faster one's file after
    that one had already written to it."""
    lock = _side(db, ".lock")
    deadline = time.monotonic() + (LOCK_SECONDS if seconds is None else seconds)
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        handle = lock.open("a+b")
    except OSError as e:  # a read-only checkout: busy, which ensure reports and goes on
        raise StoreBusy(f"{lock} cannot be opened: {e}") from e
    with handle as f:
        if os.name == "nt":  # pragma: no cover - exercised on the windows CI cell
            import msvcrt

            while True:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise StoreBusy(f"{lock} is held by another process") from None
                    time.sleep(0.1)
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise StoreBusy(f"{lock} is held by another process") from None
                    time.sleep(0.1)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# -- state -------------------------------------------------------------------


def _build(db: Path, m: Manifest) -> Path:
    """The manifest's file, concatenated beside `db` and verified. The caller
    either swaps it in or removes it."""
    out = parts_dir(db)
    tmp = _side(db, f".assembling-{os.getpid()}")
    h = hashlib.sha256()
    total = 0
    try:
        with tmp.open("wb") as f:
            for p in m.parts:
                path = out / p["name"]
                if not path.exists():
                    raise StoreError(f"{path}: missing")
                data = path.read_bytes()
                if len(data) != p["bytes"] or hashlib.sha256(data).hexdigest() != p["sha256"]:
                    raise StoreError(f"{path}: does not match the manifest")
                h.update(data)
                total += len(data)
                f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if total != m.bytes or h.hexdigest() != m.sha256:
            raise StoreError(f"{out}: the parts do not concatenate to the manifest's file")
    except BaseException:
        _remove(tmp)
        raise
    return tmp


def _remove(path: Path) -> None:
    for p in (path, _side(path, "-wal"), _side(path, "-shm")):
        if p.exists():
            p.unlink()


def _classify(db: Path, m: Manifest, *, keep: bool = False) -> tuple[State, Path | None]:
    """The state, and - when `keep` and the manifest's file had to be built to
    decide - that file, so the caller can swap it in rather than build it twice."""
    wal = _wal_bytes(db)
    if not db.exists() or db.stat().st_size == 0:
        if wal:
            return State(
                CONFLICT,
                f"{_side(db, '-wal')} holds {wal:,} bytes of a database that is not there. "
                "A new file beside it would be read through it and corrupted; "
                "`ask.py stores assemble --replace` moves it aside first",
            ), None
        return State(MISSING, f"{db} is not assembled"), None
    if not wal and _db_sha(db) == m.sha256:
        return State(CURRENT, f"{len(m.parts)} parts, {m.bytes:,} bytes"), None
    if not _is_sqlite(db):
        return State(
            CONFLICT,
            f"{db} is not a database (an LFS pointer, a partial copy?). "
            "`ask.py stores assemble --replace` moves it and its journal aside",
        ), None
    try:
        con = _connect(db)
        try:
            if _holds(con, m.tips):
                return State(
                    AHEAD,
                    "holds every row of the manifest's version"
                    + (" and more" if _counts(con) != m.rows else ", in other bytes")
                    + "; `ask.py stores split` publishes it",
                ), None
            local = _tips(con)
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        return State(CONFLICT, f"{db} cannot be read as a database ({e})"), None
    built = _build(db, m)
    try:
        con = _connect(built)
        try:
            newer = _holds(con, local)
        finally:
            con.close()
    except BaseException:
        _remove(built)
        raise
    if newer:
        if keep:
            return State(STALE, "the manifest's version holds every row it does, and more"), built
        _remove(built)
        return State(STALE, "the manifest's version holds every row it does, and more"), None
    _remove(built)
    return State(
        CONFLICT,
        f"{db} and the manifest's version each hold rows the other lacks. "
        "`ask.py stores assemble --replace` moves it aside and assembles the manifest's",
    ), None


def _counts(con: sqlite3.Connection) -> dict[str, int]:
    return {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in _tables(con)}


def state(db: str | Path) -> State:
    """Where the local file stands against the manifest. Changes no row."""
    db = Path(db)
    m = read_manifest(db)
    if m is None:
        return State(UNGOVERNED)
    return _classify(db, m)[0]


# -- split -------------------------------------------------------------------


def _inspect(
    db: Path,
) -> tuple[int, dict[str, int], tuple[str, ...], dict[str, dict], sqlite3.Connection]:
    """Checkpoint the WAL into the file and read what the manifest records.

    A row still in the -wal is not in the file's bytes; splitting before the
    checkpoint would publish a corpus without it. Returns the open connection,
    query-only, so the caller can check descent against the last manifest."""
    con = sqlite3.connect(str(db), timeout=10)
    try:
        busy, _log, _done = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            raise StoreBusy(f"{db}: a connection is writing; the WAL could not be checkpointed")
        check = con.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            raise StoreError(f"{db}: quick_check says {check!r}; not publishing it")
        con.execute("PRAGMA query_only = 1")
        page_size = con.execute("PRAGMA page_size").fetchone()[0]
        rows = _counts(con)
        triggers = tuple(
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
            )
        )
        tips = _tips(con)
    except sqlite3.DatabaseError as e:
        con.close()
        raise StoreError(f"{db}: {e}; not publishing it") from e
    except BaseException:
        con.close()
        raise
    return page_size, rows, triggers, tips, con


def split(db: str | Path, *, part_bytes: int = PART_BYTES) -> Manifest:
    """Write `db` as parts plus a manifest. Refuses rather than publish less.

    Refused, with nothing written: a WAL that will not checkpoint, a file that
    fails quick_check, a file that does not hold every row of the last
    published version (its tips), an append-only trigger the last manifest
    recorded and the file no longer has. Every refusal comes before the first
    byte is written; a part whose bytes did not change is not rewritten, and
    the manifest is replaced last, after the parts on disk are read back.
    """
    db = Path(db)
    if not db.exists() or db.stat().st_size == 0:
        raise StoreError(f"{db}: nothing to split")
    if part_bytes <= 0 or part_bytes % 4096:
        raise StoreError(f"part size {part_bytes} is not a positive multiple of 4096")
    prior = read_manifest(db)
    page_size, rows, triggers, tips, con = _inspect(db)
    try:
        if prior is not None:
            fewer = {t: (rows.get(t, 0), n) for t, n in prior.rows.items() if rows.get(t, 0) < n}
            if fewer:
                raise StoreError(
                    f"{db}: fewer rows than the last published version "
                    + ", ".join(f"{t} {now} < {was}" for t, (now, was) in sorted(fewer.items()))
                    + "; an append-only store never shrinks, so this file is not the corpus"
                )
            if not _holds(con, prior.tips):
                raise StoreError(
                    f"{db} does not hold every row of the last published version (its last "
                    "rows differ), so it was not built on it. Publishing it would drop the "
                    "rows only the parts hold; `ask.py stores` says which version is which"
                )
            lost = sorted(set(prior.triggers) - set(triggers))
            if lost:
                raise StoreError(f"{db}: append-only triggers missing: {', '.join(lost)}")
        data = db.read_bytes()
    finally:
        con.close()
    if _wal_bytes(db):
        raise StoreBusy(
            f"{db}: its -wal is not empty after a checkpoint; another connection is open"
        )
    whole = hashlib.sha256(data).hexdigest()
    chunks = [data[i : i + part_bytes] for i in range(0, len(data), part_bytes)]

    out = parts_dir(db)
    out.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, chunk in enumerate(chunks):
        name = f"part-{i:03d}"
        sha = hashlib.sha256(chunk).hexdigest()
        target = out / name
        if not (target.exists() and _sha256_file(target) == sha):
            _atomic_write(target, chunk)
        parts.append({"name": name, "bytes": len(chunk), "sha256": sha})
    for stale in sorted(out.iterdir()):
        hit = _PART.fullmatch(stale.name)
        if hit and int(hit.group(1)) >= len(chunks):
            stale.unlink()

    # The round trip, from what is on disk now, before anything says it is so.
    h = hashlib.sha256()
    for p in parts:
        h.update((out / p["name"]).read_bytes())
    if h.hexdigest() != whole:
        raise StoreError(f"{out}: the parts written do not concatenate to {db}")
    if _sha256_file(db) != whole:
        raise StoreBusy(f"{db}: changed while it was being split")

    manifest = Manifest(
        file=db.name,
        bytes=len(data),
        sha256=whole,
        page_size=page_size,
        part_bytes=part_bytes,
        parts=tuple(parts),
        rows=rows,
        triggers=triggers,
        tips=tips,
    )
    _atomic_write(manifest_path(db), manifest.to_json().encode("utf-8"))
    return manifest


# -- verify and assemble -----------------------------------------------------


def verify(db: str | Path) -> Manifest:
    """Every part matches the manifest and they concatenate to its sha256."""
    db = Path(db)
    m = _manifest(db)
    _remove(_build(db, m))
    return m


def _release(db: Path) -> None:
    """Make sure nothing else holds `db`, or its journal, before replacing it.

    Opening and closing a connection lets SQLite fold its own -wal into the
    file and remove the -wal and -shm when it is the last one. If they are
    still there afterwards, someone else has the file open - a reader that, on
    its own close, would checkpoint into or delete the journal of a file it no
    longer describes. Where the file itself is gone, a -wal with frames in it
    belongs to nothing that can be asked, and a new file beside it would be
    read through it: refused, never deleted."""
    if not db.exists() or db.stat().st_size == 0:
        if _wal_bytes(db):
            raise StoreConflict(
                f"{_side(db, '-wal')} holds frames of a database that is not there; "
                "`ask.py stores assemble --replace` moves it aside"
            )
        for suffix in ("-wal", "-shm"):
            side = _side(db, suffix)
            if side.exists():
                side.unlink()
        return
    if not _is_sqlite(db):
        return  # not a database: nothing of SQLite's holds it, and no connection may touch its journal
    try:
        con = sqlite3.connect(str(db), timeout=10)
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return
    for suffix in ("-wal", "-shm"):
        side = _side(db, suffix)
        if side.exists():
            raise StoreBusy(
                f"{side} is still there after a clean close: another process has {db} open"
            )


def _signature(db: Path) -> tuple | None:
    """What a committed write would have changed by the time `_release` has
    folded any journal into the file: its size and mtime. (Not the -wal: a
    read-only connection may leave an empty one, and `_release` removes it.)"""
    if not db.exists():
        return None
    st = db.stat()
    return (st.st_size, st.st_mtime_ns)


def _move_aside(db: Path) -> Path:
    """The local file and its journal, renamed together. Never deleted."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    aside = db.with_name(f"{db.stem}.local-{stamp}{db.suffix}")
    if db.exists() and db.stat().st_size:
        try:
            _release(db)
        except StoreConflict:
            pass
    for suffix in ("", "-wal", "-shm"):
        src = _side(db, suffix)
        if src.exists():
            os.replace(src, _side(aside, suffix))
    return aside


def assemble(db: str | Path, *, replace: bool = False) -> State:
    """Make `db` the file the manifest describes, if that loses nothing.

    Writes only when the file is missing, empty, or stale - the manifest's
    version holds every row it does. A file that is ahead is left alone (its
    rows are the next split's). A conflict raises; with `replace=True` the local
    file and its journal are moved to `<stem>.local-<UTC>.db` first and the
    move is reported, so nothing is ever deleted to make room.

    A file that needs nothing is answered without the lock (every command asks).
    One that needs writing is decided again under the lock, so a second process
    cannot judge the same file stale and replace the first one's writes; and
    the file must be unchanged since that decision when it is replaced - a
    writer that committed in between (the lock does not stop a Corpus) makes it
    busy, not overwritten.
    """
    db = Path(db)
    m = read_manifest(db)
    if m is None:
        raise StoreError(f"no manifest at {manifest_path(db)}; {db} is not carried as parts")
    first = _classify(db, m)[0]
    if first.name in (CURRENT, AHEAD):
        return first
    with _locked(db):
        seen = _signature(db)
        now, built = _classify(db, m, keep=True)
        if now.name in (CURRENT, AHEAD):
            return now
        aside = None
        try:
            if now.name == CONFLICT:
                if not replace:
                    raise StoreConflict(now.detail)
                aside = _move_aside(db)
                seen = _signature(db)
            if built is None:
                built = _build(db, m)
            _release(db)
            if _signature(db) != seen:
                raise StoreBusy(f"{db} was written to while it was being assembled; left as it is")
            try:
                os.replace(built, db)
            except PermissionError as e:  # Windows: another process has it open
                raise StoreBusy(f"{db} could not be replaced: {e}") from e
        except BaseException:
            if built is not None:
                _remove(built)
            raise
        _db_sha(db)
    detail = f"{len(m.parts)} parts, {m.bytes:,} bytes, {sum(m.rows.values()):,} rows"
    if aside is not None:
        detail += f"; the local file was moved to {aside}"
    return State("assembled", detail)


def ensure(db: str | Path, *, log=None) -> State:
    """The quiet version, for an entry point: assemble when missing or stale.

    Never raises for a conflict or a busy file - the command goes on against
    the local file, exactly as it would have before parts existed - but says
    so, because a reader on a file nobody can account for is the thing worth
    knowing about. A part that does not match its manifest still raises: that
    is damage, not drift."""
    db = Path(db)
    if not governed(db):
        return State(UNGOVERNED)
    try:
        now = assemble(db)
    except StoreConflict as e:
        now = State(CONFLICT, str(e))
    except StoreBusy as e:
        now = State(CONFLICT, f"not assembled: {e}")
    if now.name == CONFLICT and log is not None:
        log(f"warning: {db} is {now}")
    return now


def oversized(root: str | Path = "data") -> list[tuple[Path, int]]:
    """Files under `root` at or past the warning size, largest first. A part
    never is; anything listed here is a store that is not split."""
    root = Path(root)
    if not root.exists():
        return []
    hits = [
        (p, p.stat().st_size)
        for p in root.rglob("*")
        if p.is_file() and p.stat().st_size >= WARN_BYTES
    ]
    return sorted(hits, key=lambda h: -h[1])
