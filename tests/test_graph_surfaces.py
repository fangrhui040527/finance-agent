"""`ask.py graph` and the explain_path MCP tool.

Both answer the same question from the same graph, so both are tested against
the same refusals - a surface that guesses where the other refuses is the drift
that makes two front doors dangerous.
"""

import pytest

from knowledge.graph.build import build
from knowledge.graph.store import GraphStore

ASOF = "2026-08-28"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    p = tmp_path_factory.mktemp("graph") / "g.db"
    with GraphStore(p) as s:
        build(s)
    return str(p)


def cli(db, *args, capsys):
    import ask

    code = ask.main(["graph", "--db", db, "--asof", ASOF, *args])
    out = capsys.readouterr()
    return code, out.out, out.err


# -- peer coverage ------------------------------------------------------------


def test_coverage_names_the_one_company_with_no_peer(db, capsys):
    """Seven of the nine had none on 2026-09-06 and nothing said so. Press Metal
    is the honest exception: no other primary aluminium smelter is on Bursa."""
    code, out, _ = cli(db, "--coverage", capsys=capsys)
    assert code == 0
    assert "8 of 9 names have a peer" in out
    assert "no peer at all: MYX:8869" in out
    assert "Maybank" in out and "Electric Utilities" in out


def test_coverage_counts_verified_peers_separately(db, capsys):
    """Every row in the checked-in file is curated, so the verified column is
    zero everywhere - and says so rather than implying a document backs them."""
    import re

    _, out, _ = cli(db, "--coverage", capsys=capsys)
    assert "verified" in out
    # stated / sub-sector / verified, in that order, before the sub-sector name.
    rows = re.findall(r"^(?:MYX|XNAS):\S+.*?\s(\d+)\s+(\d+)\s+(\d+)\s\s", out, re.M)
    assert len(rows) == 9
    assert sum(int(v) for _, _, v in rows) == 0
    assert sum(int(d) for d, _, _ in rows) > 0, "some name has a stated peer"


# -- paths --------------------------------------------------------------------


def test_a_path_comes_back_with_its_evidence(db, capsys):
    code, out, _ = cli(db, "--path", "CM:aluminium", "Press Metal", capsys=capsys)
    assert code == 0
    assert "Aluminium --affects--> Press Metal" in out
    assert "curated:supply_chain#presmetal-aluminium" in out
    assert "Primary aluminium smelting" in out


def test_a_plain_name_resolves_the_same_way_an_instrument_id_does(db, capsys):
    _, by_name, _ = cli(db, "--path", "Crude oil", "Petronas Chemicals", capsys=capsys)
    _, by_id, _ = cli(db, "--path", "CM:crude_oil", "MYX:5183", capsys=capsys)
    assert by_name == by_id


def test_no_path_is_reported_as_not_found_cheaply_never_as_unconnected(db, capsys):
    """Traversal is a best-first heuristic. A surface that phrases an empty
    result as 'unrelated' invites a negative claim the graph cannot support."""
    code, out, _ = cli(db, "--path", "MISC", "NVIDIA", capsys=capsys)
    assert code == 0
    assert "not found cheaply" in out
    assert "never proof they are unconnected" in out


def test_an_unknown_entity_is_refused_rather_than_guessed(db, capsys):
    code, _, err = cli(db, "--path", "Atlantis", "MISC", capsys=capsys)
    assert code == 2 and "not in the graph" in err


def test_an_ambiguous_name_is_refused_with_the_options_not_broken_by_enum_order(db, capsys):
    """'Aluminium' is both a sub-sector and a commodity in the shipped data.
    Silently preferring one answers a question the user did not ask."""
    code, _, err = cli(db, "--path", "Aluminium", "Press Metal", capsys=capsys)
    assert code == 2
    assert "ambiguous" in err
    assert "CM:aluminium" in err and "SUB:aluminium" in err


# -- impact -------------------------------------------------------------------


def test_impact_ranks_what_a_shock_reaches(db, capsys):
    code, out, _ = cli(db, "--impact", "crude oil", capsys=capsys)
    assert code == 0
    assert "Petronas Chemicals" in out and "MISC" in out
    assert out.index("Petronas Chemicals") < out.index("MISC")  # nearer first


def test_impact_can_be_narrowed_to_what_you_hold(db, capsys):
    code, out, _ = cli(db, "--impact", "crude oil", "--holding", "MISC", capsys=capsys)
    assert code == 0 and "MISC" in out and "Petronas Chemicals:" not in out


def test_impact_on_something_that_reaches_nothing_says_so(db, capsys):
    code, out, _ = cli(db, "--impact", "Securities Commission Malaysia", capsys=capsys)
    assert code == 0 and "nothing reachable" in out


# -- report and benchmark -----------------------------------------------------


def test_the_report_renders_from_the_cli(db, capsys):
    code, out, _ = cli(db, "--report", capsys=capsys)
    assert code == 0 and "AWAITING A HUMAN RULING" in out


def test_the_benchmark_runs_from_the_cli(db, capsys):
    code, out, _ = cli(db, "--benchmark", "CM:aluminium", "Press Metal", capsys=capsys)
    assert code == 0 and "ratio" in out and "subgraph" in out


def test_a_diff_against_an_identical_build_is_no_change(db, capsys, tmp_path):
    other = tmp_path / "same.db"
    with GraphStore(other) as s:
        build(s)
    code, out, _ = cli(db, "--diff", str(other), capsys=capsys)
    assert code == 0 and out.strip() == "no change"


def test_asking_nothing_is_an_error_not_an_empty_success(db, capsys):
    code, _, err = cli(db, capsys=capsys)
    assert code == 2 and "nothing asked" in err


def test_a_missing_database_explains_how_to_build_one(capsys, tmp_path):
    import ask

    code = ask.main(["graph", "--db", str(tmp_path / "absent.db"), "--report"])
    assert code == 2
    assert "make graph" in capsys.readouterr().err


# -- the MCP tool -------------------------------------------------------------


def test_explain_path_returns_the_chain_and_one_citation_per_link(db):
    from mcp_server.tools import explain_path

    out = explain_path("Crude oil", "MISC", asof=ASOF, db=db)
    assert "Crude oil --affects--> Petronas Chemicals" in out
    assert out.count("[curated:supply_chain#") == 2  # one per hop
    assert "strength: indirect (2 hops" in out


def test_explain_path_refuses_an_entity_it_does_not_hold(db):
    from mcp_server.tools import explain_path

    out = explain_path("Atlantis", "MISC", asof=ASOF, db=db)
    assert out.startswith("REFUSED:") and "rather than inferring" in out


def test_explain_path_refuses_a_bad_date_rather_than_defaulting_to_today(db):
    from mcp_server.tools import explain_path

    assert explain_path("MISC", "Maybank", asof="last tuesday", db=db).startswith("REFUSED:")


def test_explain_path_refuses_to_answer_without_a_built_graph(tmp_path):
    from mcp_server.tools import explain_path

    out = explain_path("a", "b", db=str(tmp_path / "none.db"))
    assert "REFUSED" in out and "make graph" in out
    assert "guessing" in out


def test_explain_path_refuses_an_entity_against_itself(db):
    from mcp_server.tools import explain_path

    assert "same entity" in explain_path("MISC", "MISC", asof=ASOF, db=db)


def test_explain_path_never_calls_an_empty_result_unconnected(db):
    from mcp_server.tools import explain_path

    out = explain_path("MISC", "NVIDIA", asof=ASOF, db=db)
    assert "NOT FOUND CHEAPLY" in out
    assert "Do not report them as unrelated" in out


def test_explain_path_flags_a_speculative_chain(db):
    """Three or more hops carries the same caveat A7 attaches to a finding."""
    from mcp_server.tools import explain_path

    out = explain_path("Thermal coal", "Maybank", asof=ASOF, db=db)
    assert "NOT FOUND CHEAPLY" in out or "speculative" in out
    assert "REFUSED" not in out


def test_the_tool_is_registered_on_the_server():
    from mcp_server.server import S

    assert "explain_path" in S.tools
    schema = S.tools["explain_path"]
    assert "a" in str(schema) and "b" in str(schema)


def test_an_edge_outside_its_validity_is_invisible_to_the_tool(db):
    """Point-in-time, through the surface a model actually calls."""
    from mcp_server.tools import explain_path

    assert "No path" in explain_path("CM:aluminium", "Press Metal", asof="2019-01-01", db=db)
