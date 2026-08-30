"""The repository as a graph, over stdlib `ast`.

The maintainer's version of the same question the finance graph answers: what
breaks if I change this, and which agent has no eval. Deliberately last, because
it is a tool for working ON the system rather than part of what the system does.

`ast` rather than tree-sitter: graphify pins 27 grammar packages to parse many
languages; this repository is Python, and `ast` is in the standard library. The
two-dependency rule is not negotiable for a convenience.

WHAT THIS CANNOT SEE, and why the edges are INFERRED because of it. A static
pass reads names, not bindings. `self.store.record(...)` is recorded as a call to
`record` wherever `record` is defined - possibly several places. Dynamic
dispatch, getattr, decorators that rebind, and re-exports are all invisible. So
a `calls` edge means "this name is referenced here and defined there", which is
a strong hint and not a fact, and `Edge.citable` refuses it exactly as it
refuses a substring match in the news layer.

The one exception is `imports`: an import statement names its target literally,
so those edges are EXTRACTED.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

from knowledge.graph.entity_graph import Confidence, EdgeKind, NodeKind
from knowledge.graph.extractors.base import Extractor, edge, node, sorted_payload

#: Node kinds are reused rather than invented. A module is a PRODUCT of the
#: repository and a function is a TECHNOLOGY in it - the labels are a stretch,
#: and adding two NodeKinds that only the code graph uses would put finance and
#: maintenance vocabulary in one enum, which is worse.
MODULE = NodeKind.PRODUCT
SYMBOL = NodeKind.TECHNOLOGY

SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", "debug", "data",
             "htmlcov", ".pytest_cache", "build", "dist"}

#: Names so common that an edge to them says nothing about this codebase.
#: graphify's god-node stop list, in its original form: without it, `run`,
#: `main` and `get` accumulate an edge from every call site in the repository.
STOPLIST = frozenset({
    "main", "run", "get", "set", "add", "load", "save", "close", "open",
    "read", "write", "describe", "check", "print", "len", "str", "int",
    "float", "list", "dict", "set_defaults", "append", "extend", "format",
    "join", "split", "strip", "items", "keys", "values", "update", "sorted",
    "range", "enumerate", "zip", "min", "max", "sum", "abs", "round", "type",
    "isinstance", "hasattr", "getattr", "setattr", "super", "property",
})


def _module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = [p for p in rel.parts if p != "__init__"]
    return ".".join(parts) or rel.stem


def python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py")
                  if not any(part in SKIP_DIRS for part in p.parts))


class CodeExtractor(Extractor):
    """One module node per file, one symbol node per top-level def or class."""

    name = "code"

    def __init__(self, root: Path | str = ".", asserted_from: date | None = None) -> None:
        self.root = Path(root).resolve()
        #: Structure holds as of when the pass ran. A code graph has no history:
        #: git does, and duplicating it here badly would be worse than not.
        self.asserted_from = asserted_from or date.today()

    def extract(self) -> dict:
        nodes: list[dict] = []
        edges: list[dict] = []
        files = python_files(self.root)
        modules = {_module_name(p, self.root): p for p in files}
        #: symbol name -> the module ids that define it. A name defined once is
        #: resolvable; a name defined in five places is not, and pretending
        #: otherwise mints four wrong edges per call site.
        defined: dict[str, list[str]] = {}

        parsed: dict[str, ast.Module] = {}
        for mod, path in modules.items():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError):
                continue                       # a file we cannot read is skipped, not guessed at
            parsed[mod] = tree
            mid = node(MODULE, mod, mod, path=str(path.relative_to(self.root)))
            nodes.append(mid)
            for item in tree.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    sym = f"{mod}.{item.name}"
                    nodes.append(node(SYMBOL, sym, item.name, module=mod,
                                      symbol="class" if isinstance(item, ast.ClassDef)
                                             else "function"))
                    defined.setdefault(item.name, []).append(
                        node(SYMBOL, sym)["id"])
                    edges.append(edge(
                        node(SYMBOL, sym)["id"], mid["id"], EdgeKind.CLASSIFIED_IN,
                        doc=f"code:{mod}", confidence=Confidence.EXTRACTED,
                        valid_from=self.asserted_from))

        declared = {n["id"] for n in nodes}
        for mod, tree in parsed.items():
            src = node(MODULE, mod)["id"]
            for target in _imports(tree):
                dst = node(MODULE, target)["id"]
                if dst in declared and dst != src:
                    # An import statement names its target literally. This is the
                    # only edge here a document actually states.
                    edges.append(edge(src, dst, EdgeKind.SUPPLIES,
                                      doc=f"code:{mod}",
                                      confidence=Confidence.EXTRACTED,
                                      valid_from=self.asserted_from))
            for called in _calls(tree):
                if called in STOPLIST:
                    continue
                where = defined.get(called, [])
                if len(where) != 1:
                    continue           # ambiguous or unknown: no edge beats a wrong one
                dst = where[0]
                if dst in declared and dst != src:
                    edges.append(edge(src, dst, EdgeKind.EXPOSED_TO,
                                      doc=f"code:{mod}",
                                      confidence=Confidence.INFERRED,
                                      weight=0.6,
                                      valid_from=self.asserted_from))
        return sorted_payload(nodes, _dedupe(edges))


def _imports(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            out.add(n.module)
            out.update(f"{n.module}.{a.name}" for a in n.names)
    return out


def _calls(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Name):
            out.add(f.id)
        elif isinstance(f, ast.Attribute):
            out.add(f.attr)
    return out


def _dedupe(edges: list[dict]) -> list[dict]:
    seen: dict[tuple, dict] = {}
    for e in edges:
        seen.setdefault((e["source"], e["target"], e["relation"], e["valid_from"]), e)
    return list(seen.values())
