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

The local file's state is decided against a stamp written beside it
(`<db>.stamp.json`), never by counting rows: a file that has local writes and
a file that is merely old can hold the same number of rows. A local file this
module did not write and cannot account for is never overwritten; with
`replace=True` it is moved aside, never deleted.

See docs/14-OPERATIONS-RUNBOOK.md, "Large stores".
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
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

# The states a local file can be in against its manifest.
UNGOVERNED = "ungoverned"
"""No manifest: this store is not carried as parts, and nothing here applies."""
MISSING = "missing"
"""No file, or an empty one (what `sqlite3.connect` leaves on a missing path)."""
CURRENT = "current"
"""The file is exactly what the manifest describes."""
AHEAD = "ahead"
"""Written to locally since it was last current: a sweep's rows, not yet split."""
STALE = "stale"
"""Untouched since it was last current, and the manifest has moved on: a pull."""
CONFLICT = "conflict"
"""Neither: local writes on an older version, or a file nothing here wrote."""


class StoreError(RuntimeError):
    """A store could not be split, verified or assembled. Nothing was replaced."""


class StoreConflict(StoreError):
    """The local file holds what neither the manifest nor the stamp accounts for."""


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


# -- hashing and the stamp ---------------------------------------------------


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _wal_bytes(db: Path) -> int:
    wal = db.with_name(db.name + "-wal")
    return wal.stat().st_size if wal.exists() else 0


def _read_stamp(db: Path) -> dict | None:
    path = stamp_path(db)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_stamp(db: Path, manifest_sha: str, db_sha: str) -> None:
    st = db.stat()
    _atomic_write(
        stamp_path(db),
        json.dumps(
            {
                "manifest_sha256": manifest_sha,
                "db_sha256": db_sha,
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
            },
            indent=1,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n",
    )


def _db_sha(db: Path, stamp: dict | None) -> str:
    """The file's sha256, from the stamp when size and mtime say it is unchanged.

    Hashing 17 MB costs ~40 ms, paid once per process at most; the stamp makes
    the common case (nothing changed since the last command) a single stat."""
    st = db.stat()
    if stamp and stamp.get("size") == st.st_size and stamp.get("mtime_ns") == st.st_mtime_ns:
        return stamp["db_sha256"]
    return _sha256_file(db)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    with tmp.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# -- state -------------------------------------------------------------------


def state(db: str | Path) -> State:
    """Where the local file stands against the manifest. Reads, never writes."""
    db = Path(db)
    m = read_manifest(db)
    if m is None:
        return State(UNGOVERNED)
    if not db.exists() or db.stat().st_size == 0:
        return State(MISSING, f"{db} is not assembled")
    stamp = _read_stamp(db)
    touched_wal = _wal_bytes(db) > 0
    sha = _db_sha(db, stamp)
    if sha == m.sha256 and not touched_wal:
        return State(CURRENT, f"{len(m.parts)} parts, {m.bytes:,} bytes")
    if stamp and stamp.get("manifest_sha256") == m.sha256:
        return State(AHEAD, "written to since it was assembled; `ask.py stores split` publishes it")
    if stamp and stamp.get("db_sha256") == sha and not touched_wal:
        return State(STALE, "untouched since it was last current; the manifest has moved on")
    return State(
        CONFLICT,
        f"{db} matches neither the manifest nor the last version this checkout saw. "
        "`ask.py stores assemble --replace` moves it aside and assembles the manifest's",
    )


# -- split -------------------------------------------------------------------


def _inspect(db: Path) -> tuple[int, dict[str, int], tuple[str, ...]]:
    """Checkpoint the WAL into the file and read what the manifest records.

    A row still in the -wal is not in the file's bytes; splitting before the
    checkpoint would publish a corpus without it."""
    con = sqlite3.connect(str(db), timeout=10)
    try:
        busy, _log, _done = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            raise StoreBusy(f"{db}: a connection is writing; the WAL could not be checkpointed")
        check = con.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            raise StoreError(f"{db}: quick_check says {check!r}; not publishing it")
        page_size = con.execute("PRAGMA page_size").fetchone()[0]
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        rows = {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
        triggers = tuple(
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
            )
        )
    finally:
        con.close()
    if _wal_bytes(db):
        raise StoreBusy(
            f"{db}: its -wal is not empty after a checkpoint; another connection is open"
        )
    return page_size, rows, triggers


def split(db: str | Path, *, part_bytes: int = PART_BYTES) -> Manifest:
    """Write `db` as parts plus a manifest. Refuses rather than publish less.

    Refused, with nothing written: a WAL that will not checkpoint, a file that
    fails quick_check, a table with fewer rows than the last manifest says, an
    append-only trigger the last manifest recorded and the file no longer has.
    The parts are verified to concatenate back to the file before the manifest
    is replaced, and a part whose bytes did not change is not rewritten.
    """
    db = Path(db)
    if not db.exists() or db.stat().st_size == 0:
        raise StoreError(f"{db}: nothing to split")
    if part_bytes <= 0 or part_bytes % 4096:
        raise StoreError(f"part size {part_bytes} is not a positive multiple of 4096")
    prior = read_manifest(db)
    page_size, rows, triggers = _inspect(db)
    if prior is not None:
        fewer = {t: (rows.get(t, 0), n) for t, n in prior.rows.items() if rows.get(t, 0) < n}
        if fewer:
            raise StoreError(
                f"{db}: fewer rows than the last published version "
                + ", ".join(f"{t} {now} < {was}" for t, (now, was) in sorted(fewer.items()))
                + "; an append-only store never shrinks, so this file is not the corpus"
            )
        lost = sorted(set(prior.triggers) - set(triggers))
        if lost:
            raise StoreError(f"{db}: append-only triggers missing: {', '.join(lost)}")

    data = db.read_bytes()
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
    )
    _atomic_write(manifest_path(db), manifest.to_json().encode("utf-8"))
    _write_stamp(db, whole, whole)
    return manifest


# -- verify and assemble -----------------------------------------------------


def verify(db: str | Path) -> Manifest:
    """Every part matches the manifest and they concatenate to its sha256."""
    db = Path(db)
    m = read_manifest(db)
    if m is None:
        raise StoreError(f"no manifest at {manifest_path(db)}")
    out = parts_dir(db)
    h = hashlib.sha256()
    total = 0
    for p in m.parts:
        path = out / p["name"]
        if not path.exists():
            raise StoreError(f"{path}: missing")
        data = path.read_bytes()
        if len(data) != p["bytes"] or hashlib.sha256(data).hexdigest() != p["sha256"]:
            raise StoreError(f"{path}: does not match the manifest")
        h.update(data)
        total += len(data)
    if total != m.bytes or h.hexdigest() != m.sha256:
        raise StoreError(f"{out}: the parts do not concatenate to the manifest's file")
    return m


def _release(db: Path) -> None:
    """Make sure no connection holds `db` before its bytes are replaced.

    Opening and closing a connection lets SQLite remove its own -wal and -shm
    when it is the last one. If they are still there afterwards, someone else
    has the file open - a reader that, on its own close, would checkpoint into
    or delete the journal of a file it no longer describes."""
    if not db.exists():
        return
    con = sqlite3.connect(str(db), timeout=10)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    for suffix in ("-wal", "-shm"):
        side = db.with_name(db.name + suffix)
        if side.exists():
            raise StoreBusy(
                f"{side} is still there after a clean close: another process has {db} open"
            )


def _move_aside(db: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    aside = db.with_name(f"{db.stem}.local-{stamp}{db.suffix}")
    _release(db)
    os.replace(db, aside)
    return aside


def assemble(db: str | Path, *, replace: bool = False) -> State:
    """Make `db` the file the manifest describes, if that is safe to do.

    Writes only when the file is missing, empty or stale. A file that is ahead
    is left alone (its rows are the next split's). A conflict raises; with
    `replace=True` the local file is moved to `<stem>.local-<UTC>.db` first and
    the move is reported, so nothing is ever deleted to make room.
    """
    db = Path(db)
    now = state(db)
    if now.name == UNGOVERNED:
        raise StoreError(f"no manifest at {manifest_path(db)}; {db} is not carried as parts")
    if now.name in (CURRENT, AHEAD):
        if now.name == CURRENT:
            m = _manifest(db)
            _write_stamp(db, m.sha256, m.sha256)
        return now
    aside = None
    if now.name == CONFLICT:
        if not replace:
            raise StoreConflict(now.detail)
        aside = _move_aside(db)

    m = verify(db)
    tmp = db.with_name(db.name + f".assembling-{os.getpid()}")
    h = hashlib.sha256()
    with tmp.open("wb") as f:
        for p in m.parts:
            data = (parts_dir(db) / p["name"]).read_bytes()
            h.update(data)
            f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if h.hexdigest() != m.sha256:
        tmp.unlink()
        raise StoreError(f"{tmp}: assembled bytes do not match the manifest")
    try:
        _release(db)
    except StoreBusy:
        tmp.unlink()
        raise
    os.replace(tmp, db)
    _write_stamp(db, m.sha256, m.sha256)
    detail = f"{len(m.parts)} parts, {m.bytes:,} bytes, {sum(m.rows.values()):,} rows"
    if aside is not None:
        detail += f"; the local file was moved to {aside}"
    return State("assembled", detail)


def ensure(db: str | Path, *, log=None) -> State:
    """The quiet version, for an entry point: assemble when missing or stale.

    Never raises for a conflict - the command goes on against the local file,
    exactly as it would have before parts existed - but says so, because a
    reader on a file nobody can account for is the thing worth knowing about.
    A part that does not match its manifest still raises: that is damage, not
    drift."""
    db = Path(db)
    if not governed(db):
        return State(UNGOVERNED)
    now = state(db)
    if now.name in (MISSING, STALE):
        return assemble(db)
    if now.name == CURRENT:
        # A pull that brought the file and its manifest together (while the
        # whole file is still committed beside its parts) leaves an old stamp;
        # refreshed, a later local write reads as AHEAD rather than CONFLICT.
        m = _manifest(db)
        st, stamp = db.stat(), _read_stamp(db) or {}
        if (stamp.get("manifest_sha256"), stamp.get("size"), stamp.get("mtime_ns")) != (
            m.sha256,
            st.st_size,
            st.st_mtime_ns,
        ):
            _write_stamp(db, m.sha256, m.sha256)
    if now.name == CONFLICT and log is not None:
        log(f"warning: {now.detail}")
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
