"""Full-system trace: every prompt, every decision, every refusal.

WHY THIS IS SEPARATE FROM THE PROVENANCE LEDGER

The ledger stores `prompt_hash`, never prompt text (core/provenance/ledger.py).
That is deliberate: it is an append-only permanent audit record, it must stay
small, and it must be safe to keep forever. Debugging needs the opposite - the
full prompt, the full response, the intermediate values, everything - and only
until the bug is found.

So this is a second, parallel channel with the opposite trade-offs:

    ledger      permanent · hashed · small · append-only · safe to keep
    trace       ephemeral · verbatim · large · rewritable · gitignored

The trace writes under `debug/<run_id>/` which is gitignored, and every report
it produces carries a header saying it may contain sensitive text. Do not
commit one, and do not paste one into an issue without reading it first.

WHY IT IS OFF BY DEFAULT

A tracer that costs something when disabled is a tracer people turn off and then
cannot turn on when they need it. `is_tracing()` is a module-level identity check
against None, and every emit site is guarded by it, so the disabled path is one
attribute lookup and a branch.

    from core.trace import start_run, end_run, span, emit

    with start_run("nightly") as run:
        with span("harvest", source="gdelt"):
            emit("fetch", count=42)

Nested spans record their own depth, so the report can render the call tree -
which is the point: not a flat log, but the shape of what actually ran.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Where runs are written. Gitignored; see .gitignore.
DEBUG_ROOT = Path(os.environ.get("FINPLANET_DEBUG_DIR", "debug"))

#: Values longer than this are truncated in trace.jsonl and written whole to
#: prompts/ instead, so the machine-readable log stays greppable.
INLINE_LIMIT = 2000

_local = threading.local()


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Event:
    """One thing that happened. `kind` is the vocabulary the report speaks."""

    seq: int
    at: str
    kind: str
    name: str
    depth: int
    run_id: str
    span_id: str
    parent_id: str | None = None
    duration_ms: float | None = None
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_json(self) -> str:
        return json.dumps(asdict(self), default=str, ensure_ascii=False)


class Tracer:
    """One run. Writes trace.jsonl incrementally so a crash still leaves a trace."""

    def __init__(
        self, label: str = "run", root: Path | None = None, run_id: str | None = None
    ) -> None:
        self.run_id = run_id or f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
        self.label = label
        self.dir = Path(root or DEBUG_ROOT) / self.run_id
        self.prompts_dir = self.dir / "prompts"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.prompts_dir.mkdir(exist_ok=True)

        self.events: list[Event] = []
        self.started = time.perf_counter()
        self.started_at = _now()
        self._seq = 0
        self._depth = 0
        self._stack: list[str] = []
        self._lock = threading.Lock()
        self._blob_n = 0
        # Opened once and flushed per event: a crash mid-run must still leave
        # everything up to the crash on disk. A trace you only get on clean exit
        # is useless for the failures worth tracing.
        self._fh = (self.dir / "trace.jsonl").open("w", encoding="utf-8")

    # -- emitting ------------------------------------------------------------

    def emit(self, kind: str, name: str, **data) -> Event:
        with self._lock:
            self._seq += 1
            ev = Event(
                seq=self._seq,
                at=_now(),
                kind=kind,
                name=name,
                depth=self._depth,
                run_id=self.run_id,
                span_id=uuid.uuid4().hex[:8],
                parent_id=self._stack[-1] if self._stack else None,
                data=self._externalise(name, data),
            )
            self.events.append(ev)
            self._fh.write(ev.as_json() + "\n")
            self._fh.flush()
            return ev

    def _externalise(self, name: str, data: dict) -> dict:
        """Long values go to prompts/ and leave a pointer behind.

        A 40KB prompt inline makes trace.jsonl unreadable and ungreppable, and
        the prompt is exactly the thing you most want to read whole.
        """
        out: dict[str, Any] = {}
        for k, v in data.items():
            if isinstance(v, str) and len(v) > INLINE_LIMIT:
                self._blob_n += 1
                safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40]
                fname = f"{self._blob_n:04d}-{safe}-{k}.txt"
                (self.prompts_dir / fname).write_text(v, encoding="utf-8")
                out[k] = {"_blob": f"prompts/{fname}", "chars": len(v), "head": v[:200]}
            else:
                out[k] = v
        return out

    # -- spans ---------------------------------------------------------------

    @contextmanager
    def span(self, name: str, kind: str = "span", **data) -> Iterator[Event]:
        start = self.emit(kind, name, **data)
        with self._lock:
            self._depth += 1
            self._stack.append(start.span_id)
        t0 = time.perf_counter()
        try:
            yield start
        except Exception as e:
            start.error = f"{type(e).__name__}: {e}"
            self.emit("error", name, error=start.error, exc_type=type(e).__name__)
            raise
        finally:
            with self._lock:
                self._depth -= 1
                self._stack.pop()
            start.duration_ms = (time.perf_counter() - t0) * 1000
            self.emit("span_end", name, duration_ms=start.duration_ms, ok=start.error is None)

    # -- finishing -----------------------------------------------------------

    def close(self) -> dict:
        summary = self.summary()
        (self.dir / "summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8"
        )
        self._fh.close()
        return summary

    def summary(self) -> dict:
        kinds: dict[str, int] = {}
        for e in self.events:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
        slow = sorted(
            (e for e in self.events if e.duration_ms is not None),
            key=lambda e: -(e.duration_ms or 0),
        )[:15]
        errors = [e for e in self.events if e.kind == "error"]
        cost = sum(
            float(e.data.get("cost_myr", 0) or 0) for e in self.events if e.kind == "llm_call"
        )
        tokens_in = sum(
            int(e.data.get("input_tokens", 0) or 0) for e in self.events if e.kind == "llm_call"
        )
        tokens_out = sum(
            int(e.data.get("output_tokens", 0) or 0) for e in self.events if e.kind == "llm_call"
        )
        return {
            "run_id": self.run_id,
            "label": self.label,
            "started_at": self.started_at,
            "wall_ms": round((time.perf_counter() - self.started) * 1000, 2),
            "events": len(self.events),
            "by_kind": dict(sorted(kinds.items())),
            "errors": [{"name": e.name, "error": e.data.get("error")} for e in errors],
            "llm": {
                "calls": kinds.get("llm_call", 0),
                "cost_myr": round(cost, 6),
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
            },
            "agents_seen": sorted({a for e in self.events if (a := e.data.get("agent"))}),
            "refusals": sum(1 for e in self.events if e.kind in ("refusal", "denied")),
            "slowest": [
                {"name": e.name, "kind": e.kind, "ms": round(e.duration_ms or 0, 2)} for e in slow
            ],
            "dir": str(self.dir),
        }


# -- module-level API -------------------------------------------------------


def active() -> Tracer | None:
    return getattr(_local, "tracer", None)


def is_tracing() -> bool:
    """One attribute lookup. The disabled path must cost effectively nothing."""
    return getattr(_local, "tracer", None) is not None


@contextmanager
def start_run(
    label: str = "run", root: Path | None = None, run_id: str | None = None
) -> Iterator[Tracer]:
    previous = active()
    tracer = Tracer(label, root, run_id)
    _local.tracer = tracer
    tracer.emit("run_start", label, label=label)
    try:
        yield tracer
    finally:
        tracer.emit("run_end", label)
        tracer.close()
        _local.tracer = previous


def end_run() -> dict | None:
    t = active()
    if t is None:
        return None
    out = t.close()
    _local.tracer = None
    return out


def emit(kind: str, name: str = "", **data) -> None:
    """No-op when not tracing. Safe to call from anywhere, including hot paths."""
    t = active()
    if t is not None:
        t.emit(kind, name or kind, **data)


@contextmanager
def span(name: str, kind: str = "span", **data) -> Iterator[Any]:
    t = active()
    if t is None:
        yield None
        return
    with t.span(name, kind, **data) as ev:
        yield ev
