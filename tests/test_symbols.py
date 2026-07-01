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
