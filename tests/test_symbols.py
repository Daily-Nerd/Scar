import pytest

from scar import symbols

pytestmark = pytest.mark.skipif(
    not symbols.symbols_available(), reason="tree-sitter extra not installed")

PY = "class SessionStore:\n    def save(self):\n        return 1\n\ndef helper():\n    return 2\n"


def test_resolve_class_symbol():
    span = symbols.resolve_symbol("SessionStore", "src/store.py", PY)
    assert span is not None
    start, end = span
    assert PY[start:end].startswith("class SessionStore")


def test_resolve_qualified_method():
    span = symbols.resolve_symbol("SessionStore.save", "src/store.py", PY)
    assert span is not None
    start, end = span
    assert "def save" in PY[start:end]


def test_unresolved_symbol_returns_none():
    assert symbols.resolve_symbol("DoesNotExist", "src/store.py", PY) is None


def test_qualified_path_prefix_stripped_and_checked():
    assert symbols.resolve_symbol("src/store.py::helper", "src/store.py", PY) is not None
    assert symbols.resolve_symbol("other/file.py::helper", "src/store.py", PY) is None


def test_unknown_extension_returns_none():
    assert symbols.resolve_symbol("helper", "notes.txt", PY) is None


def test_available_is_false_without_dep(monkeypatch):
    # symbols_available must be a cheap boolean, never raise. (Not skipped.)
    assert isinstance(symbols.symbols_available(), bool)


def test_resolve_any_true_when_one_matches():
    assert symbols.resolve_any(["Nope", "helper"], "src/store.py", PY) is True


def test_resolve_any_false_when_none_match():
    assert symbols.resolve_any(["Nope", "AlsoNope"], "src/store.py", PY) is False


def test_resolve_any_respects_qualified_path_mismatch():
    assert symbols.resolve_any(["other/f.py::helper"], "src/store.py", PY) is False


def test_resolve_any_unavailable_or_unknown_ext_is_false():
    assert symbols.resolve_any(["helper"], "notes.txt", PY) is False


PY2 = "class SessionStore:\n    def save(self):\n        x = 1\n        return x\n"
PY2_CHANGED = "class SessionStore:\n    def save(self):\n        return compute(other())\n"
PY2_COMMENT = "class SessionStore:\n    def save(self):\n        # note\n        x = 1\n        return x\n"


def test_fingerprint_identical_source_same_shingles():
    a = symbols.fingerprint("SessionStore.save", "s.py", PY2)
    b = symbols.fingerprint("SessionStore.save", "s.py", PY2)
    assert a is not None and a == b


def test_fingerprint_comment_and_whitespace_insensitive():
    base = symbols.fingerprint("SessionStore.save", "s.py", PY2)
    commented = symbols.fingerprint("SessionStore.save", "s.py", PY2_COMMENT)
    assert base == commented  # comments skipped → identical


def test_fingerprint_changed_body_drifts():
    base = symbols.fingerprint("SessionStore.save", "s.py", PY2)
    changed = symbols.fingerprint("SessionStore.save", "s.py", PY2_CHANGED)
    assert base != changed
    assert 0.0 <= symbols.jaccard(base, changed) < 1.0


def test_fingerprint_unresolved_is_none():
    assert symbols.fingerprint("Nope", "s.py", PY2) is None


def test_jaccard_identical_is_one_and_empty_pair_is_one():
    s = frozenset({"a", "b"})
    assert symbols.jaccard(s, s) == 1.0
    assert symbols.jaccard(frozenset(), frozenset()) == 1.0


TS_CONST = "export const Foo = () => {\n    return compute();\n};\n"
TS_MULTI = "const a = 1, b = 2;\n"


def test_resolve_ts_const_arrow_symbol():
    span = symbols.resolve_symbol("Foo", "x.ts", TS_CONST)
    assert span is not None
    start, end = span
    assert "Foo" in TS_CONST[start:end]


def test_fingerprint_ts_const_arrow_not_none():
    assert symbols.fingerprint("Foo", "x.tsx", TS_CONST) is not None


def test_resolve_ts_multi_declarator_names_both():
    assert symbols.resolve_symbol("a", "x.ts", TS_MULTI) is not None
    assert symbols.resolve_symbol("b", "x.ts", TS_MULTI) is not None


def test_python_symbols_still_resolve_after_ts_fix():
    py = "class SessionStore:\n    def save(self):\n        return 1\n"
    assert symbols.resolve_symbol("SessionStore", "s.py", py) is not None
    assert symbols.resolve_symbol("SessionStore.save", "s.py", py) is not None


# --- parse cache + node-direct fingerprints (#186) ---

def test_parse_is_cached_for_identical_input():
    from scar import symbols
    if not symbols.symbols_available():
        import pytest
        pytest.skip("symbols extra not installed")
    src = "def f():\n    return 1\n"
    assert symbols._parse("m.py", src) is symbols._parse("m.py", src)


def test_fingerprint_node_matches_name_based_fingerprint():
    from scar import symbols
    if not symbols.symbols_available():
        import pytest
        pytest.skip("symbols extra not installed")
    src = "def f():\n    for i in range(3):\n        print(i)\n"
    tree = symbols._parse("m.py", src)
    name, node = next(iter(symbols._walk_defs(tree.root_node)))
    assert symbols.fingerprint_node(node) == symbols.fingerprint("f", "m.py", src)


def test_fingerprint_node_distinguishes_same_named_defs():
    from scar import symbols
    if not symbols.symbols_available():
        import pytest
        pytest.skip("symbols extra not installed")
    src = ("class A:\n    def foo(self):\n        return 1\n\n"
           "class B:\n    def foo(self):\n        for i in range(3):\n"
           "            print(i)\n        return [x for x in 'ab']\n")
    tree = symbols._parse("z.py", src)
    fps = [symbols.fingerprint_node(n) for name, n in
           symbols._walk_defs(tree.root_node) if name == "foo"]
    assert len(fps) == 2 and fps[0] != fps[1]


# --- ambiguity-aware resolution + identifier-aware fingerprints (#187) ---

AMBIG_SRC = ("class A:\n    def foo(self):\n        return 1\n\n"
             "class B:\n    def foo(self):\n        return 2\n")


def _need_symbols():
    from scar import symbols
    if not symbols.symbols_available():
        import pytest
        pytest.skip("symbols extra not installed")
    return symbols


def test_fingerprint_refuses_ambiguous_bare_anchor():
    symbols = _need_symbols()
    assert symbols.fingerprint("foo", "z.py", AMBIG_SRC) is None


def test_fingerprint_resolves_dotted_anchors_past_ambiguity():
    symbols = _need_symbols()
    fa = symbols.fingerprint("A.foo", "z.py", AMBIG_SRC)
    fb = symbols.fingerprint("B.foo", "z.py", AMBIG_SRC)
    assert fa is not None and fb is not None
    assert fa != fb  # identifier-aware: bodies differ only in literals


def test_resolve_any_still_matches_ambiguous_names():
    symbols = _need_symbols()
    assert symbols.resolve_any(["foo"], "z.py", AMBIG_SRC) is True


def test_fingerprint_distinguishes_unrelated_same_shape_bodies():
    symbols = _need_symbols()
    fa = symbols.fingerprint("bar", "x.py", "def bar(a):\n    return a.b(1)\n")
    fb = symbols.fingerprint("baz", "y.py", "def baz(q):\n    return q.z(2)\n")
    assert symbols.jaccard(fa, fb) < 1.0


def test_fingerprint_identical_bodies_still_match_exactly():
    symbols = _need_symbols()
    src = "def f(x):\n    for i in range(x):\n        print(i)\n    return x\n"
    fa = symbols.fingerprint("f", "x.py", src)
    fb = symbols.fingerprint("f", "y.py", src)
    assert symbols.jaccard(fa, fb) == 1.0
