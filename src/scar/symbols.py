"""Tree-sitter symbol resolution — the ONLY module that imports tree-sitter.

Gated behind the optional `scar-cli[symbols]` extra so the core parser and the
read hot path stay stdlib-only (see #90). Every public function degrades to a
no-op (`None` / `False`) when the dependency is absent — it must NEVER raise on
the hot path.
"""

from __future__ import annotations

import functools

# Extension -> grammar module name. Long-tail languages are intentionally
# absent — they degrade to path/pattern anchors.
_LANGS = {
    ".py": "tree_sitter_python",
    ".ts": "tree_sitter_typescript",
    ".tsx": "tree_sitter_typescript",
    ".js": "tree_sitter_typescript",
    ".jsx": "tree_sitter_typescript",
}

# tree-sitter node types that define a named symbol, per grammar family.
_DEF_NODES = {
    "function_definition", "class_definition",           # python
    "function_declaration", "class_declaration",         # ts/js
    "method_definition", "generator_function_declaration",
    "lexical_declaration", "variable_declaration",       # const/let arrow fns
}


def symbols_available() -> bool:
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_python  # noqa: F401
    except ImportError:
        return False
    return True


@functools.lru_cache(maxsize=8)
def _language(module_name: str):
    """Load a tree_sitter Language, or None if the grammar isn't installed."""
    try:
        import importlib

        from tree_sitter import Language
        mod = importlib.import_module(module_name)
        if module_name == "tree_sitter_typescript":
            return Language(mod.language_typescript())
        return Language(mod.language())
    except Exception:
        return None


def _lang_for(rel_path: str):
    for ext, mod in _LANGS.items():
        if rel_path.endswith(ext):
            return _language(mod)
    return None


def _named_defs(node):
    """Yield (name, node) for the named symbol(s) a definition node introduces.

    Most def nodes carry the name on a direct `name` field. TS/JS variable
    declarations (`const Foo = () => {}`, `let a = 1, b = 2`) instead nest the
    name on one-or-more `variable_declarator` children — descend into those.
    Destructuring patterns (`const {x} = ...`) have no plain identifier name and
    are skipped. The yielded node is the declarator (so its fingerprint captures
    the initializer/body), or the def node itself for direct-name defs.
    """
    if node.type in ("lexical_declaration", "variable_declaration"):
        for child in node.children:
            if child.type == "variable_declarator":
                nm = child.child_by_field_name("name")
                if nm is not None and nm.type == "identifier":
                    yield nm.text.decode("utf8"), child
    else:
        nm = node.child_by_field_name("name")
        if nm is not None:
            yield nm.text.decode("utf8"), node


def _walk_defs(node):
    """Yield (name, node) for every named definition node in the subtree."""
    for child in node.children:
        if child.type in _DEF_NODES:
            yield from _named_defs(child)
        yield from _walk_defs(child)


def _resolve_all(root_node, anchor: str, rel_path: str) -> list:
    """EVERY node a symbol anchor could mean. The old dict(_walk_defs()) form
    silently kept the LAST same-named definition (#187 last-wins collision) —
    a bare anchor `foo` in a file with A.foo and B.foo pointed at B's body
    with no signal. Dotted parts filter at each level."""
    name = anchor
    if "::" in anchor:
        qpath, name = anchor.split("::", 1)
        if qpath and qpath != rel_path:
            return []
    parts = name.split(".")
    nodes = [n for nm, n in _walk_defs(root_node) if nm == parts[0]]
    for part in parts[1:]:
        nodes = [inner for n in nodes
                 for nm, inner in _walk_defs(n) if nm == part]
    return nodes


def _resolve_node(root_node, anchor: str, rel_path: str):
    """The UNIQUE node for a symbol anchor, or None — including None when the
    anchor is ambiguous (#187). Shape-sensitive consumers (fingerprint, drift,
    reanchor) must never silently pick one of several same-named definitions;
    refusing is honest, guessing points the scar at the wrong symbol. Matcher
    existence checks go through _resolve_in_tree, which accepts any match."""
    nodes = _resolve_all(root_node, anchor, rel_path)
    return nodes[0] if len(nodes) == 1 else None


def _resolve_in_tree(root_node, anchor: str, rel_path: str) -> tuple[int, int] | None:
    # Matcher semantics: the anchor names a symbol that EXISTS here — with two
    # same-named definitions, an edit to this file is relevant to either, so
    # any match counts (ambiguity must not stop a scar from firing).
    nodes = _resolve_all(root_node, anchor, rel_path)
    return (nodes[0].start_byte, nodes[0].end_byte) if nodes else None


# Cap leaf-token text folded into fingerprints: identifiers are short; a long
# string literal would bloat the shingle set without adding discriminating
# power beyond its prefix.
_TOKEN_CAP = 32


def _type_sequence(node) -> list[str]:
    """Pre-order node types, with LEAF tokens carrying their text (#187).
    Type-only sequences made semantically unrelated functions with the same
    control-flow shape fingerprint-identical (measured jaccard 1.0) —
    defeating exactly the generic-name disambiguation the anchor-survival
    experiment prescribed fingerprints for. Leaves whose text just repeats
    their type (punctuation: '(', ':') stay bare. Comments skipped as before."""
    out: list[str] = []
    if node.type == "comment":
        return out
    if not node.children:
        text = node.text.decode("utf8", "replace")[:_TOKEN_CAP]
        out.append(f"{node.type}={text}" if text != node.type else node.type)
        return out
    out.append(node.type)
    for child in node.children:
        out.extend(_type_sequence(child))
    return out


def fingerprint_node(node) -> frozenset[str]:
    """Fingerprint an already-resolved definition node (#186): callers that
    hold the node from _walk_defs must not re-resolve by name — that re-parses
    the file per definition AND collapses same-named definitions onto the
    last one (the _resolve_node last-wins collision)."""
    seq = _type_sequence(node)
    if len(seq) < 3:
        return frozenset(seq)  # too small for 3-grams → raw type set
    return frozenset(
        f"{seq[i]}|{seq[i + 1]}|{seq[i + 2]}" for i in range(len(seq) - 2))


def fingerprint(anchor: str, rel_path: str, source: str) -> frozenset[str] | None:
    tree = _parse(rel_path, source)
    if tree is None:
        return None
    node = _resolve_node(tree.root_node, anchor, rel_path)
    if node is None:
        return None
    return fingerprint_node(node)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


@functools.lru_cache(maxsize=64)
def _parse(rel_path: str, source: str):
    """Parse source for rel_path's language; None if unavailable/unknown ext.
    lru_cache'd (#186): one hook invocation resolves symbol anchors for every
    symbol-anchored scar against the SAME edited file — uncached, that was one
    full tree-sitter parse per scar. Keyed on (rel_path, source); a re-passed
    identical source string hits the str's cached hash, so the key cost is
    one hash of the source on first sight, not per call."""
    if not symbols_available():
        return None
    lang = _lang_for(rel_path)
    if lang is None:
        return None
    from tree_sitter import Parser
    return Parser(lang).parse(bytes(source, "utf8"))


def resolve_symbol(anchor: str, rel_path: str, source: str) -> tuple[int, int] | None:
    tree = _parse(rel_path, source)
    if tree is None:
        return None
    return _resolve_in_tree(tree.root_node, anchor, rel_path)


def resolve_any(anchors, rel_path: str, source: str) -> bool:
    """True iff ANY anchor resolves, parsing `source` exactly once."""
    tree = _parse(rel_path, source)
    if tree is None:
        return False
    root = tree.root_node
    return any(_resolve_in_tree(root, a, rel_path) is not None for a in anchors)
