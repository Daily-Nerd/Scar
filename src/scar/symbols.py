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


def _resolve_node(root_node, anchor: str, rel_path: str):
    """The tree-sitter node for a symbol anchor, or None. Shares the qualified-
    path + dotted-member logic with _resolve_in_tree."""
    name = anchor
    if "::" in anchor:
        qpath, name = anchor.split("::", 1)
        if qpath and qpath != rel_path:
            return None
    parts = name.split(".")
    node = dict(_walk_defs(root_node)).get(parts[0])
    for part in parts[1:]:
        if node is None:
            return None
        node = dict(_walk_defs(node)).get(part)
    return node


def _resolve_in_tree(root_node, anchor: str, rel_path: str) -> tuple[int, int] | None:
    node = _resolve_node(root_node, anchor, rel_path)
    return (node.start_byte, node.end_byte) if node is not None else None


def _type_sequence(node) -> list[str]:
    """Pre-order node TYPES in the subtree, skipping comment nodes."""
    out: list[str] = []
    if node.type == "comment":
        return out
    out.append(node.type)
    for child in node.children:
        out.extend(_type_sequence(child))
    return out


def fingerprint(anchor: str, rel_path: str, source: str) -> frozenset[str] | None:
    tree = _parse(rel_path, source)
    if tree is None:
        return None
    node = _resolve_node(tree.root_node, anchor, rel_path)
    if node is None:
        return None
    seq = _type_sequence(node)
    if len(seq) < 3:
        return frozenset(seq)  # too small for 3-grams → raw type set
    return frozenset(
        f"{seq[i]}|{seq[i + 1]}|{seq[i + 2]}" for i in range(len(seq) - 2))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _parse(rel_path: str, source: str):
    """Parse source for rel_path's language; None if unavailable/unknown ext."""
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
