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
