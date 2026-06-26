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

from scar import installer
from scar.cli import main

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


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    claude = tmp_path / ".claude"
    monkeypatch.setattr(installer, "CLAUDE_DIR", claude)
    monkeypatch.setattr(installer, "HOOKS_DIR", claude / "hooks")
    monkeypatch.setattr(installer, "SETTINGS", claude / "settings.json")
    monkeypatch.setattr(installer, "find_scar", lambda: "/stable/bin/scar")
    return claude / "settings.json"


def test_cli_hook_install_then_uninstall(isolated_settings, capsys):
    assert main(["hook", "install"]) == 0
    settings = isolated_settings.read_text(encoding="utf-8")
    assert settings.count("/stable/bin/scar hook") == 3

    assert main(["hook", "uninstall"]) == 0
    settings = isolated_settings.read_text(encoding="utf-8")
    assert "/stable/bin/scar hook" not in settings
    assert "Scars themselves (.scars/ in repos) are untouched" in capsys.readouterr().out


def test_cli_hook_dry_run_does_not_create_settings(isolated_settings):
    assert main(["hook", "install", "--dry-run"]) == 0
    assert not isolated_settings.exists()


def test_cli_hook_status_reports_each_hook(isolated_settings, capsys):
    assert main(["hook", "status"]) == 0
    out = capsys.readouterr().out
    assert "precheck" in out
    assert "session-notice" in out
    assert "stop-drafter" in out
    assert out.count("not installed") == 3


def test_skill_install_dry_run_reports_target_without_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "CLAUDE_DIR", tmp_path / ".claude")
    monkeypatch.setattr(installer, "SKILLS_DIR", tmp_path / ".claude" / "skills")
    rc = main(["skill", "install", "--dry-run"])
    assert rc == 0
    assert not (tmp_path / ".claude" / "skills" / "scar-authoring").exists()


def test_skill_install_copies_skill_then_uninstall_removes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "CLAUDE_DIR", tmp_path / ".claude")
    monkeypatch.setattr(installer, "SKILLS_DIR", tmp_path / ".claude" / "skills")
    assert main(["skill", "install"]) == 0
    dest = tmp_path / ".claude" / "skills" / "scar-authoring" / "SKILL.md"
    assert dest.exists() and "scar-authoring" in dest.read_text()
    assert main(["skill", "uninstall"]) == 0
    assert not dest.parent.exists()


def test_skill_reinstall_over_existing_removes_stale_files(tmp_path, monkeypatch):
    # Reinstall must rmtree the existing dir before copy: a stale file left from
    # a prior install must NOT survive a second install. Guards against a refactor
    # to copytree(dirs_exist_ok=True), which would leave orphaned files behind.
    monkeypatch.setattr(installer, "CLAUDE_DIR", tmp_path / ".claude")
    monkeypatch.setattr(installer, "SKILLS_DIR", tmp_path / ".claude" / "skills")
    assert main(["skill", "install"]) == 0
    dest = tmp_path / ".claude" / "skills" / "scar-authoring"
    stale = dest / "STALE-LEFTOVER.txt"
    stale.write_text("orphan from a previous layout", encoding="utf-8")
    assert main(["skill", "install"]) == 0
    assert not stale.exists()
    assert (dest / "SKILL.md").exists()
