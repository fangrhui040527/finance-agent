"""The repository as a graph, over stdlib `ast`.

The maintainer's version of the same question the finance graph answers: what
breaks if I change this, and which module has nothing testing it. Deliberately
last, because it is a tool for working ON the system rather than part of what the
system does.

It does NOT answer "which agent has no eval", and an earlier version of this
docstring claimed it did. That question cannot arise: core/registry/loader.py
runs check_suite on every agent at load, so an agent without a usable eval suite
does not produce a report - it refuses to register. A graph query for it would be
a weaker second answer to something already refused outright.

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

#: Node kinds are reused where an honest analogue exists: a module is a PRODUCT
#: of the repository, a class or function a TECHNOLOGY in it. DOCUMENT is not a
#: reuse - it was added to the shared vocabulary because a filing describing a
#: company is the same relation as a page describing a module, and neither
#: domain could express it before.
MODULE = NodeKind.PRODUCT
SYMBOL = NodeKind.TECHNOLOGY
DOC = NodeKind.DOCUMENT

#: Anything under here is a test. Convention, checked once, in one place.
TEST_DIRS = ("tests",)
TEST_PREFIX = "test_"

#: Prose that describes the code. Read as text, not parsed.
DOC_SUFFIXES = (".md",)

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

    def __init__(self, root: Path | str = ".", asserted_from: date | None = None,
                 docs: bool = True) -> None:
        self.root = Path(root).resolve()
        self.docs = docs
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
            is_test = _is_test(modules[mod], self.root)
            for target in _imports(tree):
                dst = node(MODULE, target)["id"]
                if dst not in declared or dst == src:
                    continue
                if is_test:
                    # A test module importing a repository module is the best
                    # static evidence that it exercises it - and only evidence.
                    # Test files import helpers and fixtures too, so importing is
                    # not testing, and the edge says INFERRED because of it.
                    edges.append(edge(src, dst, EdgeKind.TESTS,
                                      doc=f"code:{mod}",
                                      confidence=Confidence.INFERRED, weight=0.8,
                                      valid_from=self.asserted_from))
                else:
                    # An import statement names its target literally.
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
        if self.docs:
            n2, e2 = self._documents(declared, modules)
            nodes += n2
            edges += e2
        return sorted_payload(nodes, _dedupe(edges))

    def _documents(self, declared: set[str], modules: dict[str, Path]):
        """Prose that names a module, as an edge from the page to the code.

        EXTRACTED, and this is the one place in the code graph where that word
        is fully earned: the markdown contains the path as a literal string, so
        the document really does say what the edge claims. A reader can open it
        and see the reference.

        Matching is on the file path (`knowledge/graph/store.py`), not the
        dotted name - prose says "core/llm/tiers.py" and almost never
        "core.llm.tiers", and matching the dotted form would fire on ordinary
        sentences that happen to contain dots.
        """
        nodes: list[dict] = []
        edges: list[dict] = []
        by_path = {str(p.relative_to(self.root)): mod for mod, p in modules.items()}
        pages = sorted(p for p in self.root.rglob("*")
                       if p.suffix in DOC_SUFFIXES
                       and not any(part in SKIP_DIRS for part in p.parts))
        for page in pages:
            try:
                text = page.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            rel = str(page.relative_to(self.root))
            hits = sorted({by_path[path] for path in by_path if path in text})
            if not hits:
                continue                      # a page naming no code is not a code doc
            page_node = node(DOC, rel, page.name, path=rel)
            nodes.append(page_node)
            for mod in hits:
                dst = node(MODULE, mod)["id"]
                if dst in declared:
                    edges.append(edge(page_node["id"], dst, EdgeKind.DOCUMENTS,
                                      doc=f"doc:{rel}",
                                      confidence=Confidence.EXTRACTED, weight=0.9,
                                      valid_from=self.asserted_from))
        return nodes, edges


def _is_test(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    return rel.parts[0] in TEST_DIRS or rel.name.startswith(TEST_PREFIX)


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
