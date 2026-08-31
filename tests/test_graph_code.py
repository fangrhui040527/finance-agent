"""The repository as a graph, and the two surfaces that query it.

The code graph is a maintainer's tool, so its tests are about honesty rather
than coverage: a static pass reads names, not bindings, and the edges it emits
have to say so.
"""

from datetime import date
from pathlib import Path

from knowledge.graph.build import CODE_DB, build
from knowledge.graph.entity_graph import Confidence, EdgeKind, NodeKind
from knowledge.graph.extractors.code import (
    MODULE,
    STOPLIST,
    SYMBOL,
    CodeExtractor,
    python_files,
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
    assert any(
        e.src == "TE:a_one" and e.dst == "PR:a" and e.kind is EdgeKind.CLASSIFIED_IN for e in edges
    )


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
    root = repo(
        tmp_path,
        {
            "a.py": "import b\ndef go():\n    return b.unique_thing()\n",
            "b.py": "def unique_thing():\n    return 1\n",
        },
    )
    _, edges = extracted(root)
    calls = [e for e in edges if e.kind is EdgeKind.EXPOSED_TO]
    assert calls
    assert all(e.confidence is Confidence.INFERRED for e in calls)
    assert all(e.sourced and not e.citable for e in calls)


def test_an_ambiguous_name_produces_no_edge_at_all(tmp_path):
    """Two definitions of one name means the call cannot be resolved. No edge
    beats four wrong ones, which is what guessing would mint."""
    root = repo(
        tmp_path,
        {
            "a.py": "def shared():\n    pass\n",
            "b.py": "def shared():\n    pass\n",
            "c.py": "def go():\n    return shared()\n",
        },
    )
    _, edges = extracted(root)
    assert not [e for e in edges if e.src == "PR:c" and e.kind is EdgeKind.EXPOSED_TO]


def test_a_common_name_is_stoplisted_rather_than_linked_from_everywhere(tmp_path):
    """graphify's god-node stop list in its original form. Without it, `run` and
    `main` collect an edge from every call site in the repository."""
    root = repo(
        tmp_path, {"a.py": "def main():\n    pass\n", "b.py": "def go():\n    return main()\n"}
    )
    _, edges = extracted(root)
    assert not [e for e in edges if e.kind is EdgeKind.EXPOSED_TO]
    assert "main" in STOPLIST and "run" in STOPLIST and "get" in STOPLIST


def test_a_file_that_will_not_parse_is_skipped_rather_than_guessed_at(tmp_path):
    root = repo(tmp_path, {"good.py": "def ok():\n    pass\n", "bad.py": "def (((\n"})
    nodes, _ = extracted(root)
    ids = {n.node_id for n in nodes}
    assert "PR:good" in ids and "PR:bad" not in ids


def test_generated_and_vendored_directories_are_not_walked(tmp_path):
    root = repo(
        tmp_path,
        {
            "keep.py": "x = 1\n",
            "__pycache__/skip.py": "x = 1\n",
            "debug/skip.py": "x = 1\n",
            "node_modules/skip.py": "x = 1\n",
        },
    )
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
        report = build(s, extractors=[CodeExtractor(root, asserted_from=ASOF)], skip_markets=True)
    assert set(report.per_extractor) == {"code"}
    assert report.citable < report.edges  # the call edges are not citable
    assert CODE_DB != "data/graph.db"  # never overwrites the finance graph


def test_building_the_code_graph_twice_is_byte_identical(tmp_path):
    root = repo(
        tmp_path / "src",
        {"a.py": "import b\ndef go():\n    pass\n", "b.py": "def helper():\n    pass\n"},
    )
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    for p in (a, b):
        with GraphStore(p) as s:
            build(s, extractors=[CodeExtractor(root, asserted_from=ASOF)], skip_markets=True)
    assert a.read_bytes() == b.read_bytes()


def test_the_hub_rule_generalises_to_a_codebase(tmp_path):
    """The same degree rule, on a graph it was not designed for. A test file
    importing forty modules is exactly the waypoint that connects everything."""
    root = Path(__file__).resolve().parents[1]
    nodes, edges = extracted(root)
    from knowledge.graph.entity_graph import EntityGraph

    g = EntityGraph()
    for n in nodes:
        g.add_node(n)
    for e in edges:
        g.add_edge(e)
    assert g.hubs()  # a real repository has hubs
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


# -- tests, docs, and the question this graph exists to answer ---------------


def test_a_test_module_gets_a_tests_edge_not_an_import_edge(tmp_path):
    """A test importing a module is the specific relation. Emitting the generic
    `supplies` alongside it would say the test depends on the module, which is
    true and much less useful than saying it covers it."""
    root = repo(tmp_path, {"lib.py": "def f():\n    pass\n", "tests/test_lib.py": "import lib\n"})
    _, edges = extracted(root)
    kinds = {e.kind for e in edges if e.src == "PR:tests_test_lib"}
    assert EdgeKind.TESTS in kinds
    assert EdgeKind.SUPPLIES not in kinds


def test_a_tests_edge_is_inferred_because_importing_is_not_testing(tmp_path):
    """Test files import fixtures and helpers too. Importing is the best static
    evidence available and still only evidence."""
    root = repo(tmp_path, {"lib.py": "def f():\n    pass\n", "tests/test_lib.py": "import lib\n"})
    _, edges = extracted(root)
    t = next(e for e in edges if e.kind is EdgeKind.TESTS)
    assert t.confidence is Confidence.INFERRED and not t.citable


def test_a_file_named_test_something_counts_wherever_it_lives(tmp_path):
    root = repo(tmp_path, {"lib.py": "def f():\n    pass\n", "pkg/test_lib.py": "import lib\n"})
    _, edges = extracted(root)
    assert any(e.kind is EdgeKind.TESTS for e in edges)


def test_a_page_naming_a_module_documents_it_and_the_edge_is_citable(tmp_path):
    """The one place EXTRACTED is fully earned in this graph: the markdown
    contains the path as a literal string, so a reader can open it and check."""
    root = repo(
        tmp_path,
        {
            "core/thing.py": "def f():\n    pass\n",
            "docs/guide.md": "See `core/thing.py` for details.\n",
        },
    )
    nodes, edges = extracted(root)
    assert any(n.kind is NodeKind.DOCUMENT for n in nodes)
    d = next(e for e in edges if e.kind is EdgeKind.DOCUMENTS)
    assert d.dst == "PR:core_thing"
    assert d.confidence is Confidence.EXTRACTED and d.citable


def test_a_page_naming_no_code_is_not_treated_as_a_code_document(tmp_path):
    root = repo(
        tmp_path, {"lib.py": "x = 1\n", "docs/prose.md": "A page about nothing in particular.\n"}
    )
    nodes, _ = extracted(root)
    assert not [n for n in nodes if n.kind is NodeKind.DOCUMENT]


def test_documents_match_on_the_path_not_the_dotted_name(tmp_path):
    """Prose says 'core/llm/tiers.py' and almost never 'core.llm.tiers'; matching
    the dotted form would fire on ordinary sentences containing dots."""
    root = repo(
        tmp_path,
        {
            "core/thing.py": "x = 1\n",
            "docs/a.md": "core/thing.py\n",
            "docs/b.md": "core.thing is a nice idea.\n",
        },
    )
    nodes, _ = extracted(root)
    docs = {n.label for n in nodes if n.kind is NodeKind.DOCUMENT}
    assert docs == {"a.md"}


def test_docs_can_be_turned_off(tmp_path):
    root = repo(tmp_path, {"lib.py": "x = 1\n", "docs/g.md": "lib.py\n"})
    nodes, _ = parse(CodeExtractor(root, asserted_from=ASOF, docs=False).extract(), "code")
    assert not [n for n in nodes if n.kind is NodeKind.DOCUMENT]


def test_which_module_has_nothing_testing_it(tmp_path):
    from knowledge.graph.analyze import untested_modules
    from knowledge.graph.entity_graph import EntityGraph

    root = repo(
        tmp_path,
        {
            "covered.py": "def a():\n    pass\n",
            "bare.py": "def b():\n    pass\n",
            "tests/test_covered.py": "import covered\n",
        },
    )
    nodes, edges = extracted(root)
    g = EntityGraph()
    for n in nodes:
        g.add_node(n)
    for e in edges:
        g.add_edge(e)
    assert untested_modules(g, ignore=("tests_",)) == ["PR:bare"]


def test_a_module_that_defines_nothing_is_not_reported_as_untested(tmp_path):
    """Two thirds of the first run were empty __init__.py package markers. A
    list nobody can read is the same as no list."""
    from knowledge.graph.analyze import untested_modules
    from knowledge.graph.entity_graph import EntityGraph

    root = repo(tmp_path, {"pkg/__init__.py": "", "pkg/real.py": "def f():\n    pass\n"})
    nodes, edges = extracted(root)
    g = EntityGraph()
    for n in nodes:
        g.add_node(n)
    for e in edges:
        g.add_edge(e)
    assert "PR:pkg" not in untested_modules(g)
    assert "PR:pkg_real" in untested_modules(g)


def test_the_docstring_no_longer_claims_to_answer_the_eval_question():
    """It did, and it did not. core/registry/loader.py runs check_suite at load,
    so an agent without an eval suite refuses to register - a graph query would
    be a weaker second answer to something already refused outright."""
    import knowledge.graph.extractors.code as mod

    doc = mod.__doc__
    # The phrase still appears - inside the correction that retracts it.
    assert 'does NOT answer "which agent has no eval"' in doc
    assert "refuses to register" in doc
    # And the promise it does make is the one the code delivers.
    assert "which module has nothing testing it" in doc


def test_paths_are_posix_regardless_of_host_separator(tmp_path):
    """Node paths and doc ids must use `/` on every OS. With `str(Path)` the
    Windows build wrote `core\thing.py`, so the same repository produced a
    different graph per machine and prose naming `core/thing.py` never matched."""
    root = repo(tmp_path, {"core/thing.py": "x = 1\n", "docs/guide.md": "core/thing.py\n"})
    nodes, _ = parse(CodeExtractor(root, asserted_from=ASOF).extract(), "code")
    paths = [n.metadata.get("path") for n in nodes if n.metadata.get("path")]
    assert paths, "expected at least one node carrying a path"
    assert all("\\" not in p for p in paths), paths
    assert "core/thing.py" in paths
    doc = next(n for n in nodes if n.kind is NodeKind.DOCUMENT)
    assert doc.node_id.endswith("docs_guide.md") or "/" in doc.metadata.get("path", "")
