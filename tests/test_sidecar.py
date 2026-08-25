"""P0: telemetry sits beside content, never inside it."""
import json
from pathlib import Path

from core.provenance import sidecar


def test_bump_creates_sidecar_not_content(tmp_path: Path):
    (tmp_path / "lesson-1.md").write_text("# a lesson\nbody\n")
    sidecar.bump(tmp_path, "lesson-1.md")
    assert (tmp_path / ".usage.json").exists()
    assert (tmp_path / "lesson-1.md").read_text() == "# a lesson\nbody\n"


def test_counter_increments_and_timestamps(tmp_path: Path):
    sidecar.bump(tmp_path, "k")
    sidecar.bump(tmp_path, "k")
    s = sidecar.stats(tmp_path, "k")
    assert s["retrieved"] == 2 and "last_used_at" in s


def test_corrupt_sidecar_never_raises(tmp_path: Path):
    (tmp_path / ".usage.json").write_text("{not json")
    assert sidecar.load(tmp_path) == {}
    sidecar.bump(tmp_path, "k")  # must not raise
    assert sidecar.stats(tmp_path, "k")["retrieved"] == 1


def test_write_is_atomic_no_temp_left(tmp_path: Path):
    sidecar.bump(tmp_path, "k")
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []
    json.loads((tmp_path / ".usage.json").read_text())
