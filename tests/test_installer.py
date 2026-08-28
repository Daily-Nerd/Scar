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


def _commands(settings_path, event):
    import json
    cfg = json.loads(settings_path.read_text(encoding="utf-8"))["hooks"]
    return [h.get("command", "")
            for group in cfg.get(event, []) for h in group.get("hooks", [])]


def test_cli_hook_install_then_uninstall(isolated_settings, capsys):
    assert main(["hook", "install"]) == 0
    settings = isolated_settings.read_text(encoding="utf-8")
    assert settings.count("/stable/bin/scar hook") == len(installer.HOOKS)

    assert main(["hook", "uninstall"]) == 0
    settings = isolated_settings.read_text(encoding="utf-8")
    assert "/stable/bin/scar hook" not in settings
    assert "Scars themselves (.scars/ in repos) are untouched" in capsys.readouterr().out


def test_install_keeps_every_spec_that_shares_an_event(isolated_settings):
    # Scar #236: `precheck` and `precheck-command` both live on PreToolUse.
    # Ownership used to be event-scoped, so installing the second spec stripped
    # the first and left the tool with no pre-edit injection at all. Both must
    # survive an install into an empty settings file.
    assert main(["hook", "install"]) == 0
    commands = _commands(isolated_settings, "PreToolUse")
    assert "/stable/bin/scar hook precheck" in commands
    assert "/stable/bin/scar hook precheck-command" in commands


def test_install_is_idempotent_on_a_shared_event(isolated_settings, capsys):
    assert main(["hook", "install"]) == 0
    capsys.readouterr()
    assert main(["hook", "install"]) == 0
    out = capsys.readouterr().out
    assert out.count("up-to-date") == len(installer.HOOKS)
    commands = _commands(isolated_settings, "PreToolUse")
    assert sorted(commands) == ["/stable/bin/scar hook precheck",
                                "/stable/bin/scar hook precheck-command"]


def test_install_migrates_a_legacy_precheck_without_eating_its_neighbour(
        isolated_settings, capsys):
    # A legacy script entry belongs to `precheck` alone. Migrating it must not
    # disturb the `precheck-command` entry sharing the same event.
    import json
    isolated_settings.parent.mkdir(parents=True, exist_ok=True)
    isolated_settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Edit|Write|MultiEdit|NotebookEdit",
         "hooks": [{"type": "command",
                    "command": "python3 ~/.claude/hooks/scar-precheck.py"}]},
    ]}}), encoding="utf-8")
    assert main(["hook", "install"]) == 0
    out = capsys.readouterr().out
    assert "[precheck] settings: migrate legacy entry" in out
    commands = _commands(isolated_settings, "PreToolUse")
    assert sorted(commands) == ["/stable/bin/scar hook precheck",
                                "/stable/bin/scar hook precheck-command"]


def test_install_preserves_foreign_hooks_on_a_shared_event(isolated_settings):
    import json
    isolated_settings.parent.mkdir(parents=True, exist_ok=True)
    isolated_settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command",
                                       "command": "/usr/local/bin/other-tool guard"}]},
    ]}}), encoding="utf-8")
    assert main(["hook", "install"]) == 0
    assert "/usr/local/bin/other-tool guard" in _commands(isolated_settings, "PreToolUse")


def test_status_distinguishes_kinds_sharing_one_event(isolated_settings, capsys):
    # Only `precheck-command` is present. Status must not report `precheck`
    # as installed just because a sibling kind occupies the same event.
    import json
    isolated_settings.parent.mkdir(parents=True, exist_ok=True)
    isolated_settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command",
                                       "command": "/stable/bin/scar hook precheck-command"}]},
    ]}}), encoding="utf-8")
    assert main(["hook", "status"]) == 0
    lines = {line.split()[0]: line for line in capsys.readouterr().out.splitlines()
             if line.startswith(("precheck", "posttool", "session", "stop"))}
    assert "not installed" in lines["precheck"]
    assert lines["precheck-command"].endswith("installed")


def test_install_fails_loudly_when_the_written_file_lost_a_hook(
        isolated_settings, monkeypatch, capsys):
    # Scar #236 reported success while dropping an entry. Install verifies its
    # own result against what actually landed on disk.
    real_save = installer.save_settings

    def lossy_save(settings, dry):
        groups = settings["hooks"]["PreToolUse"]
        settings["hooks"]["PreToolUse"] = [
            g for g in groups
            if not installer.owns_kind(g, "precheck")]
        real_save(settings, dry)

    monkeypatch.setattr(installer, "save_settings", lossy_save)
    assert main(["hook", "install"]) == 1
    assert "precheck" in capsys.readouterr().out


def test_cli_hook_dry_run_does_not_create_settings(isolated_settings):
    assert main(["hook", "install", "--dry-run"]) == 0
    assert not isolated_settings.exists()


def test_cli_hook_status_reports_each_hook(isolated_settings, capsys):
    assert main(["hook", "status"]) == 0
    out = capsys.readouterr().out
    assert "precheck" in out
    assert "precheck-command" in out
    assert "session-notice" in out
    assert "stop-drafter" in out
    assert "posttool" in out
    assert out.count("not installed") == 5


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


def test_hook_specs_include_bash_command_precheck():
    from scar.installer import HOOKS
    spec = next(s for s in HOOKS if s["kind"] == "precheck-command")
    assert spec["event"] == "PreToolUse"
    assert spec["matcher"] == "Bash"


# --- Cascade hooks (.windsurf/hooks.json) -----------------------------------
# Workspace-level and committable, so a teammate's older scar-cli reads the
# same file: the install shape is part of the contract, not an implementation
# detail.

import json  # noqa: E402  (grouped with the Cascade block it serves)


@pytest.fixture
def cascade_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "find_scar", lambda: "/stable/bin/scar")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _hooks_json(repo: Path) -> dict:
    return json.loads((repo / ".windsurf" / "hooks.json").read_text(encoding="utf-8"))


def test_cascade_install_wires_both_pre_events(cascade_repo, capsys):
    assert main(["hook", "install", "--runtime", "windsurf"]) == 0
    hooks = _hooks_json(cascade_repo)["hooks"]
    assert set(hooks) == {"pre_write_code", "pre_run_command", "post_write_code"}
    for event, entries in hooks.items():
        assert len(entries) == 1, event
        assert "cascade-hook" in entries[0]["command"]


def test_cascade_install_maps_the_sentinel_exit_to_a_block(cascade_repo):
    """An older scar-cli without the subcommand exits 2 from argparse, which
    Cascade reads as 'block' — every write would bounce. The wired command
    maps our own sentinel to 2 instead, so version skew degrades to a no-op."""
    from scar.cascade import BLOCK_EXIT

    command = _install_and_read_command(cascade_repo)
    assert str(BLOCK_EXIT) in command and "exit 2" in command


def _install_and_read_command(repo: Path) -> str:
    assert main(["hook", "install", "--runtime", "windsurf"]) == 0
    return _hooks_json(repo)["hooks"]["pre_write_code"][0]["command"]


def test_cascade_install_merges_with_an_existing_config(cascade_repo):
    existing = {
        "hooks": {
            "pre_write_code": [{"command": "./scripts/team-lint.sh"}],
            "post_run_command": [{"command": "./scripts/notify.sh"}],
        },
        "someOtherKey": {"kept": True},
    }
    (cascade_repo / ".windsurf").mkdir()
    (cascade_repo / ".windsurf" / "hooks.json").write_text(
        json.dumps(existing), encoding="utf-8")

    assert main(["hook", "install", "--runtime", "windsurf"]) == 0
    config = _hooks_json(cascade_repo)
    assert config["someOtherKey"] == {"kept": True}
    assert config["hooks"]["post_run_command"] == [{"command": "./scripts/notify.sh"}]
    commands = [h["command"] for h in config["hooks"]["pre_write_code"]]
    assert "./scripts/team-lint.sh" in commands
    assert any("cascade-hook" in c for c in commands)


def test_cascade_install_is_idempotent(cascade_repo, capsys):
    assert main(["hook", "install", "--runtime", "windsurf"]) == 0
    assert main(["hook", "install", "--runtime", "windsurf"]) == 0
    hooks = _hooks_json(cascade_repo)["hooks"]
    assert len(hooks["pre_write_code"]) == 1
    assert "up-to-date" in capsys.readouterr().out


def test_cascade_uninstall_keeps_foreign_hooks(cascade_repo):
    (cascade_repo / ".windsurf").mkdir()
    (cascade_repo / ".windsurf" / "hooks.json").write_text(json.dumps(
        {"hooks": {"pre_write_code": [{"command": "./scripts/team-lint.sh"}]}}),
        encoding="utf-8")
    assert main(["hook", "install", "--runtime", "windsurf"]) == 0
    assert main(["hook", "uninstall", "--runtime", "windsurf"]) == 0
    config = _hooks_json(cascade_repo)
    assert config["hooks"]["pre_write_code"] == [{"command": "./scripts/team-lint.sh"}]


def test_cascade_uninstall_of_our_only_hooks_leaves_a_valid_config(cascade_repo):
    assert main(["hook", "install", "--runtime", "windsurf"]) == 0
    assert main(["hook", "uninstall", "--runtime", "windsurf"]) == 0
    assert _hooks_json(cascade_repo) == {"hooks": {}}


def test_cascade_status_reports_each_event(cascade_repo, capsys):
    assert main(["hook", "status", "--runtime", "windsurf"]) == 0
    out = capsys.readouterr().out
    assert out.count("not installed") == 3
    assert main(["hook", "install", "--runtime", "windsurf"]) == 0
    capsys.readouterr()
    assert main(["hook", "status", "--runtime", "windsurf"]) == 0
    assert capsys.readouterr().out.count("installed") == 3


def test_cascade_status_mentions_restricted_mode(cascade_repo, capsys):
    """Hooks do not load in a Restricted Mode workspace — a documented no-op,
    never an install failure."""
    assert main(["hook", "status", "--runtime", "windsurf"]) == 0
    assert "Restricted Mode" in capsys.readouterr().out


def test_cascade_dry_run_writes_nothing(cascade_repo):
    assert main(["hook", "install", "--runtime", "windsurf", "--dry-run"]) == 0
    assert not (cascade_repo / ".windsurf" / "hooks.json").exists()


def test_git_and_runtime_targets_are_mutually_exclusive(cascade_repo):
    """Three lifecycles, three different files. Picking two targets in one
    invocation can only mean one of them is silently ignored."""
    with pytest.raises(SystemExit):
        main(["hook", "install", "--git", "--runtime", "windsurf"])


def test_cascade_install_leaves_an_unreadable_config_alone(cascade_repo, capsys):
    """A hand-edited hooks.json that no longer parses is the team's file —
    refuse loudly rather than overwrite it."""
    (cascade_repo / ".windsurf").mkdir()
    (cascade_repo / ".windsurf" / "hooks.json").write_text("{not json", encoding="utf-8")
    assert main(["hook", "install", "--runtime", "windsurf"]) == 1
    assert (cascade_repo / ".windsurf" / "hooks.json").read_text() == "{not json"
