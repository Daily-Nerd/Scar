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


def _symbol_name(node) -> str | None:
    name = node.child_by_field_name("name")
    return name.text.decode("utf8") if name is not None else None


def _walk_defs(node):
    """Yield (name, node) for every named definition node in the subtree."""
    for child in node.children:
        if child.type in _DEF_NODES:
            nm = _symbol_name(child)
            if nm:
                yield nm, child
        yield from _walk_defs(child)


def resolve_symbol(anchor: str, rel_path: str, source: str) -> tuple[int, int] | None:
    if not symbols_available():
        return None
    # qualified form: path::Symbol.member — the path must match this file.
    name = anchor
    if "::" in anchor:
        qpath, name = anchor.split("::", 1)
        if qpath and qpath != rel_path:
            return None
    lang = _lang_for(rel_path)
    if lang is None:
        return None
    from tree_sitter import Parser
    tree = Parser(lang).parse(bytes(source, "utf8"))
    parts = name.split(".")
    defs = dict(_walk_defs(tree.root_node))
    node = defs.get(parts[0])
    # Walk dotted members (Class.method) into the matched subtree.
    for part in parts[1:]:
        if node is None:
            return None
        node = dict(_walk_defs(node)).get(part)
    if node is None:
        return None
    return (node.start_byte, node.end_byte)
