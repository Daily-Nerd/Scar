"""`scar hook install/uninstall/status --git` (#117) — the post-commit trigger
for `scar draft-check`. Separate lifecycle from the Claude Code hooks in
installer.py's HOOKS list: this writes into the REPO's .git/hooks/, not the
user's global ~/.claude/settings.json.

Real git repos throughout: .git/hooks/ resolution (git rev-parse --git-dir)
is a git fact, matching test_evidence.py / test_draftcheck.py's rationale.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scar import installer
from scar.cli import main

FAKE_SCAR = "/stable/bin/scar"


def _git(tmp: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(tmp), *args], check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    monkeypatch.setattr(installer, "find_scar", lambda: FAKE_SCAR)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _hook_path(repo: Path) -> Path:
    return repo / ".git" / "hooks" / "post-commit"


# ---------------------------------------------------------------------------
# fresh install
# ---------------------------------------------------------------------------

def test_fresh_install_writes_shebang_and_invocation(git_repo, capsys):
    assert main(["hook", "install", "--git"]) == 0
    hook = _hook_path(git_repo)
    assert hook.exists()
    content = hook.read_text(encoding="utf-8")
    assert content.startswith("#!/bin/sh\n")
    assert f"{FAKE_SCAR} draft-check --from-hook 2>/dev/null || true" in content


def test_fresh_install_makes_hook_executable(git_repo):
    assert main(["hook", "install", "--git"]) == 0
    hook = _hook_path(git_repo)
    assert hook.stat().st_mode & 0o111  # at least one executable bit


def test_dry_run_creates_nothing(git_repo):
    assert main(["hook", "install", "--git", "--dry-run"]) == 0
    assert not _hook_path(git_repo).exists()


# ---------------------------------------------------------------------------
# idempotent re-install
# ---------------------------------------------------------------------------

def test_reinstall_is_idempotent(git_repo, capsys):
    assert main(["hook", "install", "--git"]) == 0
    first = _hook_path(git_repo).read_text(encoding="utf-8")
    assert main(["hook", "install", "--git"]) == 0
    second = _hook_path(git_repo).read_text(encoding="utf-8")
    assert first == second
    assert second.count("draft-check --from-hook") == 1


# ---------------------------------------------------------------------------
# existing post-commit: append exactly one line, preserve existing bytes
# ---------------------------------------------------------------------------

def test_existing_hook_gets_exactly_one_appended_line(git_repo):
    hook = _hook_path(git_repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    existing = "#!/bin/sh\necho 'custom pre-existing hook'\n"
    hook.write_text(existing, encoding="utf-8")

    assert main(["hook", "install", "--git"]) == 0
    content = hook.read_text(encoding="utf-8")
    assert content.startswith(existing)  # original bytes preserved, byte-for-byte
    assert content.count("draft-check --from-hook") == 1


def test_existing_hook_reinstall_stays_idempotent(git_repo):
    hook = _hook_path(git_repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho 'custom'\n", encoding="utf-8")

    assert main(["hook", "install", "--git"]) == 0
    first = hook.read_text(encoding="utf-8")
    assert main(["hook", "install", "--git"]) == 0
    second = hook.read_text(encoding="utf-8")
    assert first == second
    assert second.count("draft-check --from-hook") == 1


# ---------------------------------------------------------------------------
# core.hooksPath / husky / lefthook -> skip, print manual instructions
# ---------------------------------------------------------------------------

def test_core_hooks_path_set_is_untouched(git_repo, capsys):
    custom_dir = git_repo / "custom-hooks"
    custom_dir.mkdir()
    _git(git_repo, "config", "core.hooksPath", "custom-hooks")

    assert main(["hook", "install", "--git"]) == 0
    assert not (custom_dir / "post-commit").exists()
    assert not _hook_path(git_repo).exists()
    out = capsys.readouterr().out
    assert "core.hooksPath" in out
    assert FAKE_SCAR in out  # manual instructions include the resolved binary


def test_husky_dir_is_untouched(git_repo, capsys):
    (git_repo / ".husky").mkdir()
    assert main(["hook", "install", "--git"]) == 0
    assert not _hook_path(git_repo).exists()
    out = capsys.readouterr().out
    assert "husky" in out.lower()


def test_lefthook_config_is_untouched(git_repo, capsys):
    (git_repo / "lefthook.yml").write_text("pre-commit:\n  commands: {}\n")
    assert main(["hook", "install", "--git"]) == 0
    assert not _hook_path(git_repo).exists()
    out = capsys.readouterr().out
    assert "lefthook" in out.lower()


# ---------------------------------------------------------------------------
# uninstall: removes exactly our line; file-only-ours removes the whole file
# ---------------------------------------------------------------------------

def test_uninstall_removes_file_when_it_was_ours_only(git_repo):
    assert main(["hook", "install", "--git"]) == 0
    assert _hook_path(git_repo).exists()
    assert main(["hook", "uninstall", "--git"]) == 0
    assert not _hook_path(git_repo).exists()


def test_uninstall_preserves_pre_existing_hook_removing_only_our_line(git_repo):
    hook = _hook_path(git_repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho 'custom pre-existing hook'\n", encoding="utf-8")

    assert main(["hook", "install", "--git"]) == 0
    assert main(["hook", "uninstall", "--git"]) == 0

    assert hook.exists()
    content = hook.read_text(encoding="utf-8")
    assert "draft-check --from-hook" not in content
    assert "custom pre-existing hook" in content


def test_uninstall_when_nothing_installed_is_a_safe_noop(git_repo, capsys):
    assert main(["hook", "uninstall", "--git"]) == 0
    assert not _hook_path(git_repo).exists()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def test_status_reports_not_installed(git_repo, capsys):
    assert main(["hook", "status", "--git"]) == 0
    out = capsys.readouterr().out
    assert "not installed" in out


def test_status_reports_installed(git_repo, capsys):
    assert main(["hook", "install", "--git"]) == 0
    capsys.readouterr()
    assert main(["hook", "status", "--git"]) == 0
    out = capsys.readouterr().out
    assert "installed" in out


def test_status_reports_externally_managed(git_repo, capsys):
    (git_repo / ".husky").mkdir()
    assert main(["hook", "status", "--git"]) == 0
    out = capsys.readouterr().out
    assert "externally managed" in out


# ---------------------------------------------------------------------------
# missing binary
# ---------------------------------------------------------------------------

def test_install_fails_without_scar_on_path(git_repo, monkeypatch, capsys):
    monkeypatch.setattr(installer, "find_scar", lambda: None)
    assert main(["hook", "install", "--git"]) == 1
    out = capsys.readouterr().out
    assert "not found" in out


# ---------------------------------------------------------------------------
# non-git dir
# ---------------------------------------------------------------------------

def test_install_outside_git_repo_reports_and_returns_one(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "find_scar", lambda: FAKE_SCAR)
    monkeypatch.chdir(tmp_path)
    assert main(["hook", "install", "--git"]) == 1
