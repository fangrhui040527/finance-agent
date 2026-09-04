"""The paper book can see prices and a fee card; it can never see a broker."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_MODULES = ("core.broker", "markets.sources.moomoo_quotes", "moomoo", "futu")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def test_the_paper_packages_import_nothing_that_can_reach_a_broker():
    for pkg in ("engines/paper", "knowledge/paper"):
        for path in (ROOT / pkg).glob("*.py"):
            bad = [
                m
                for m in _imports(path)
                if any(m == f or m.startswith(f + ".") for f in FORBIDDEN_MODULES)
            ]
            assert not bad, f"{path.relative_to(ROOT)} imports {bad}"


def test_no_paper_module_names_a_gateway_host_or_port():
    for path in (ROOT / "engines" / "paper").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "11111" not in text and "127.0.0.1" not in text, path.name


def test_the_documents_say_what_the_book_is():
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "engines/paper/" in security and "test_paper_boundary.py" in security
    assert "no simulated fill anywhere" not in security  # the claim that was already false
    assert (ROOT / "docs" / "22-PAPER-BOOK.md").exists()
    assert "22-PAPER-BOOK.md" in (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "22-PAPER-BOOK" in (ROOT / "docs" / "20-FEEDBACK-ROUTINE.md").read_text(encoding="utf-8")
    assert "ask.py paper" in (ROOT / "details" / "09-RUNBOOK.md").read_text(encoding="utf-8")
    contract = (ROOT / "knowledge" / "paper" / "README.md").read_text(encoding="utf-8")
    assert (
        "Bands, never verbs" in contract and (ROOT / "knowledge" / "paper" / "TEMPLATE.md").exists()
    )
