"""Installer binary resolution: hooks must never bind to a venv shim.

scar 0003 (landmine): `shutil.which("scar")` is PATH-order dependent — with a
virtualenv active it resolves `.venv/bin/scar`, pins the hooks to a path that
dies with the venv, and reports "up-to-date" on rerun. find_scar() must skip
paths under $VIRTUAL_ENV.
"""

import importlib.util
import os
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "scar_hooks", Path(__file__).parent.parent / "hook" / "scar-hooks.py")
scar_hooks = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(scar_hooks)


def _fake_scar(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / "scar"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    return exe


@pytest.fixture
def venv_and_global(tmp_path):
    venv_bin = tmp_path / ".venv" / "bin"
    global_bin = tmp_path / "local" / "bin"
    return _fake_scar(venv_bin), _fake_scar(global_bin)


def test_find_scar_skips_active_venv(venv_and_global, monkeypatch):
    venv_exe, global_exe = venv_and_global
    monkeypatch.setenv("VIRTUAL_ENV", str(venv_exe.parent.parent))
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(venv_exe.parent), str(global_exe.parent)]))
    assert scar_hooks.find_scar() == str(global_exe)


def test_find_scar_returns_none_when_only_venv_copy_exists(tmp_path, monkeypatch):
    venv_exe = _fake_scar(tmp_path / ".venv" / "bin")
    monkeypatch.setenv("VIRTUAL_ENV", str(venv_exe.parent.parent))
    monkeypatch.setenv("PATH", str(venv_exe.parent))
    assert scar_hooks.find_scar() is None


def test_find_scar_uses_path_order_when_no_venv_active(venv_and_global, monkeypatch):
    venv_exe, global_exe = venv_and_global
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(venv_exe.parent), str(global_exe.parent)]))
    assert scar_hooks.find_scar() == str(venv_exe)


def test_install_explains_venv_shadowing_when_no_global_scar(
        tmp_path, monkeypatch, capsys):
    venv_exe = _fake_scar(tmp_path / ".venv" / "bin")
    monkeypatch.setenv("VIRTUAL_ENV", str(venv_exe.parent.parent))
    monkeypatch.setenv("PATH", str(venv_exe.parent))
    assert scar_hooks.install(dry=True) == 1
    out = capsys.readouterr().out
    assert "VIRTUAL_ENV" in out or "venv" in out
