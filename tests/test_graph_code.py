"""The repository as a graph, and the two surfaces that query it.

The code graph is a maintainer's tool, so its tests are about honesty rather
than coverage: a static pass reads names, not bindings, and the edges it emits
have to say so.
"""
import ast
from datetime import date
from pathlib import Path

import pytest

from knowledge.graph.build import CODE_DB, build
from knowledge.graph.entity_graph import Confidence, EdgeKind, NodeKind
from knowledge.graph.extractors.code import (
    MODULE, STOPLIST, SYMBOL, CodeExtractor, python_files,
)
from knowledge.graph.store import GraphStore
from knowledge.graph.validate import parse

ASOF = date(2026, 8, 28)


def repo(tmp_path, files: dict[str, str]) -> Path:
    """Write a throwaway package. Paths are written literally.

    An earlier version took **kwargs and mapped `__` to a separator, which
    turned "__pycache__/skip.py" into "/pycache/skip.py" - an absolute path at
    the filesystem root. It passed locally only because that shell runs as root,
    and creating a directory outside its own tmpdir is worse than failing.
    """
    for name, body in files.items():
        assert not name.startswith("/"), f"{name!r} must be relative to tmp_path"
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return tmp_path


def extracted(root):
    return parse(CodeExtractor(root, asserted_from=ASOF).extract(), "code")


# -- what it sees -------------------------------------------------------------

def test_a_module_and_its_top_level_symbols_become_nodes(tmp_path):
    root = repo(tmp_path, {"a.py": "def one():\n    pass\n\nclass Two:\n    pass\n"})
    nodes, _ = extracted(root)
    kinds = {n.node_id: n.kind for n in nodes}
    assert kinds["PR:a"] is MODULE
    assert kinds["TE:a_one"] is SYMBOL and kinds["TE:a_two"] is SYMBOL


def test_a_symbol_is_classified_into_the_module_that_defines_it(tmp_path):
    root = repo(tmp_path, {"a.py": "def one():\n    pass\n"})
    _, edges = extracted(root)
    assert any(e.src == "TE:a_one" and e.dst == "PR:a"
               and e.kind is EdgeKind.CLASSIFIED_IN for e in edges)


def test_an_import_is_extracted_because_the_statement_names_its_target(tmp_path):
    root = repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})
    _, edges = extracted(root)
    imp = [e for e in edges if e.kind is EdgeKind.SUPPLIES]
    assert imp and imp[0].src == "PR:a" and imp[0].dst == "PR:b"
    assert imp[0].confidence is Confidence.EXTRACTED and imp[0].citable


def test_a_from_import_is_seen_too(tmp_path):
    root = repo(tmp_path, {"a.py": "from b import thing\n", "b.py": "thing = 1\n"})
    _, edges = extracted(root)
    assert any(e.dst == "PR:b" and e.kind is EdgeKind.SUPPLIES for e in edges)


# -- what it cannot see, and admits ------------------------------------------

def test_a_call_edge_is_inferred_because_a_static_pass_reads_names_not_bindings(tmp_path):
    """`self.store.record(...)` is recorded against whichever `record` is
    defined. Dynamic dispatch, getattr and rebinding decorators are invisible,
    so a call edge is a strong hint and never a fact."""
    root = repo(tmp_path, {"a.py": "import b\ndef go():\n    return b.unique_thing()\n",
                             "b.py": "def unique_thing():\n    return 1\n"})
    _, edges = extracted(root)
    calls = [e for e in edges if e.kind is EdgeKind.EXPOSED_TO]
    assert calls
    assert all(e.confidence is Confidence.INFERRED for e in calls)
    assert all(e.sourced and not e.citable for e in calls)


def test_an_ambiguous_name_produces_no_edge_at_all(tmp_path):
    """Two definitions of one name means the call cannot be resolved. No edge
    beats four wrong ones, which is what guessing would mint."""
    root = repo(tmp_path, {
        "a.py": "def shared():\n    pass\n",
        "b.py": "def shared():\n    pass\n",
        "c.py": "def go():\n    return shared()\n"})
    _, edges = extracted(root)
    assert not [e for e in edges if e.src == "PR:c" and e.kind is EdgeKind.EXPOSED_TO]


def test_a_common_name_is_stoplisted_rather_than_linked_from_everywhere(tmp_path):
    """graphify's god-node stop list in its original form. Without it, `run` and
    `main` collect an edge from every call site in the repository."""
    root = repo(tmp_path, {"a.py": "def main():\n    pass\n",
                             "b.py": "def go():\n    return main()\n"})
    _, edges = extracted(root)
    assert not [e for e in edges if e.kind is EdgeKind.EXPOSED_TO]
    assert "main" in STOPLIST and "run" in STOPLIST and "get" in STOPLIST


def test_a_file_that_will_not_parse_is_skipped_rather_than_guessed_at(tmp_path):
    root = repo(tmp_path, {"good.py": "def ok():\n    pass\n",
                             "bad.py": "def (((\n"})
    nodes, _ = extracted(root)
    ids = {n.node_id for n in nodes}
    assert "PR:good" in ids and "PR:bad" not in ids


def test_generated_and_vendored_directories_are_not_walked(tmp_path):
    root = repo(tmp_path, {"keep.py": "x = 1\n",
                             "__pycache__/skip.py": "x = 1\n",
                             "debug/skip.py": "x = 1\n",
                             "node_modules/skip.py": "x = 1\n"})
    assert [p.name for p in python_files(root)] == ["keep.py"]


def test_a_self_import_does_not_become_an_edge(tmp_path):
    root = repo(tmp_path, {"a.py": "import a\n"})
    _, edges = extracted(root)
    assert not [e for e in edges if e.src == e.dst]


def test_an_import_of_something_outside_the_repository_is_ignored(tmp_path):
    root = repo(tmp_path, {"a.py": "import json\nimport sqlite3\n"})
    _, edges = extracted(root)
    assert not [e for e in edges if e.kind is EdgeKind.SUPPLIES]


# -- the whole repository -----------------------------------------------------

def test_this_repository_parses_into_a_graph(tmp_path):
    nodes, edges = extracted(Path(__file__).resolve().parents[1])
    assert len(nodes) > 300 and len(edges) > 500
    ids = {n.node_id for n in nodes}
    assert "PR:knowledge_graph_entity_graph" in ids
    assert "TE:knowledge_graph_entity_graph_path_to_citations" in ids


def test_the_code_graph_builds_into_its_own_database(tmp_path):
    root = Path(__file__).resolve().parents[1]
    with GraphStore(tmp_path / "code.db") as s:
        report = build(s, extractors=[CodeExtractor(root, asserted_from=ASOF)],
                       skip_markets=True)
    assert set(report.per_extractor) == {"code"}
    assert report.citable < report.edges       # the call edges are not citable
    assert CODE_DB != "data/graph.db"          # never overwrites the finance graph


def test_building_the_code_graph_twice_is_byte_identical(tmp_path):
    root = repo(tmp_path / "src", {"a.py": "import b\ndef go():\n    pass\n",
                                     "b.py": "def helper():\n    pass\n"})
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    for p in (a, b):
        with GraphStore(p) as s:
            build(s, extractors=[CodeExtractor(root, asserted_from=ASOF)],
                  skip_markets=True)
    assert a.read_bytes() == b.read_bytes()


def test_the_hub_rule_generalises_to_a_codebase(tmp_path):
    """The same degree rule, on a graph it was not designed for. A test file
    importing forty modules is exactly the waypoint that connects everything."""
    root = Path(__file__).resolve().parents[1]
    nodes, edges = extracted(root)
    from knowledge.graph.entity_graph import EntityGraph, Node
    g = EntityGraph()
    for n in nodes:
        g.add_node(n)
    for e in edges:
        g.add_edge(e)
    assert g.hubs()                            # a real repository has hubs
    assert all(g.degree(h) >= 50 for h in g.hubs())


def test_what_breaks_if_i_change_this(tmp_path):
    """The question the code graph exists to answer, via the reverse index."""
    root = Path(__file__).resolve().parents[1]
    nodes, edges = extracted(root)
    from knowledge.graph.entity_graph import EntityGraph
    g = EntityGraph()
    for n in nodes:
        g.add_node(n)
    for e in edges:
        g.add_edge(e)
    callers = g.inbound("TE:engines_sizing_caps_cost_floor_bps")
    assert len(callers) >= 5
    assert all(not e.citable for e in callers)  # every one a name, not a binding


def test_a_code_edge_is_dated_so_it_is_traversable_at_all(tmp_path):
    root = repo(tmp_path, {"a.py": "import b\n", "b.py": "x = 1\n"})
    _, edges = extracted(root)
    assert all(e.valid_from == ASOF for e in edges)
    assert all(e.live_at(ASOF) for e in edges)


def test_an_empty_repository_produces_an_empty_graph(tmp_path):
    assert extracted(tmp_path) == ([], [])
