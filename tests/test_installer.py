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


# These name --runtime claude explicitly: since #303 a bare `hook install`
# detects hosts and may decide to write nothing, which would make every
# assertion below depend on what the developer happens to have on PATH.
def test_cli_hook_install_then_uninstall(isolated_settings, capsys):
    assert main(["hook", "install", "--runtime", "claude"]) == 0
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
    assert main(["hook", "install", "--runtime", "claude"]) == 0
    commands = _commands(isolated_settings, "PreToolUse")
    assert "/stable/bin/scar hook precheck" in commands
    assert "/stable/bin/scar hook precheck-command" in commands


def test_install_is_idempotent_on_a_shared_event(isolated_settings, capsys):
    assert main(["hook", "install", "--runtime", "claude"]) == 0
    capsys.readouterr()
    assert main(["hook", "install", "--runtime", "claude"]) == 0
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
    assert main(["hook", "install", "--runtime", "claude"]) == 0
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
    assert main(["hook", "install", "--runtime", "claude"]) == 0
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
    assert main(["hook", "install", "--runtime", "claude"]) == 1
    assert "precheck" in capsys.readouterr().out


def test_cli_hook_dry_run_does_not_create_settings(isolated_settings):
    assert main(["hook", "install", "--runtime", "claude", "--dry-run"]) == 0
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


# --- Codex hooks (~/.codex/hooks.json) --------------------------------------
# #246: PR #244 shipped the Codex adapter behind a plugin whose materialized
# cache never carried its hooks.json, so the runtime was unreachable on a real
# install. ~/.codex/hooks.json is Codex's user-level equivalent of Claude's
# settings.json and is SHARED with other tools, so merge behaviour is part of
# the contract, not an implementation detail.


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    home = tmp_path / "codexhome"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(installer, "find_scar", lambda: "/stable/bin/scar")
    return home


def _codex_json(home: Path) -> dict:
    return json.loads((home / "hooks.json").read_text(encoding="utf-8"))


def test_codex_install_wires_every_handler_the_adapter_implements(codex_home, capsys):
    assert main(["hook", "install", "--runtime", "codex"]) == 0
    hooks = _codex_json(codex_home)["hooks"]
    assert set(hooks) == {"SessionStart", "PreToolUse", "PostToolUse"}
    commands = {event: [h["command"] for g in groups for h in g["hooks"]]
                for event, groups in hooks.items()}
    assert commands["SessionStart"] == ["/stable/bin/scar hook codex-session-notice"]
    assert commands["PreToolUse"] == ["/stable/bin/scar hook codex-pretool"]
    assert commands["PostToolUse"] == ["/stable/bin/scar hook codex-posttool"]


def test_codex_install_matches_codex_tool_names_not_claude_ones(codex_home):
    """Codex exposes edits as `apply_patch`, not Edit/Write/MultiEdit. A
    Claude-shaped matcher is why the shipped plugin could never have fired."""
    hooks = (main(["hook", "install", "--runtime", "codex"]),
             _codex_json(codex_home)["hooks"])[1]
    assert hooks["PreToolUse"][0]["matcher"] == "Bash|apply_patch"
    assert hooks["PostToolUse"][0]["matcher"] == "apply_patch"
    assert "matcher" not in hooks["SessionStart"][0]


def test_codex_install_honors_codex_home(tmp_path, monkeypatch):
    elsewhere = tmp_path / "somewhere-else"
    monkeypatch.setenv("CODEX_HOME", str(elsewhere))
    monkeypatch.setattr(installer, "find_scar", lambda: "/stable/bin/scar")
    assert main(["hook", "install", "--runtime", "codex"]) == 0
    assert (elsewhere / "hooks.json").exists()


def test_codex_install_merges_with_another_tools_config(codex_home):
    """~/.codex/hooks.json is shared. Another tool's SessionStart, Stop and
    SessionEnd entries must survive untouched."""
    codex_home.mkdir(parents=True)
    foreign = {
        "hooks": {
            "SessionStart": [{"matcher": "startup|resume", "hooks": [
                {"type": "command", "command": "python3 ~/other/start.py",
                 "timeout": 10}]}],
            "Stop": [{"hooks": [
                {"type": "command", "command": "python3 ~/other/stop.py",
                 "timeout": 10}]}],
            "SessionEnd": [{"hooks": [
                {"type": "command", "command": "python3 ~/other/end.py",
                 "timeout": 3}]}],
        },
        "someOtherKey": {"kept": True},
    }
    (codex_home / "hooks.json").write_text(json.dumps(foreign), encoding="utf-8")

    assert main(["hook", "install", "--runtime", "codex"]) == 0
    config = _codex_json(codex_home)
    assert config["someOtherKey"] == {"kept": True}
    assert config["hooks"]["Stop"] == foreign["hooks"]["Stop"]
    assert config["hooks"]["SessionEnd"] == foreign["hooks"]["SessionEnd"]
    start = [h["command"] for g in config["hooks"]["SessionStart"] for h in g["hooks"]]
    assert "python3 ~/other/start.py" in start
    assert "/stable/bin/scar hook codex-session-notice" in start


def test_codex_install_is_idempotent(codex_home, capsys):
    assert main(["hook", "install", "--runtime", "codex"]) == 0
    assert main(["hook", "install", "--runtime", "codex"]) == 0
    assert len(_codex_json(codex_home)["hooks"]["PreToolUse"]) == 1
    assert "up-to-date" in capsys.readouterr().out


def test_codex_uninstall_keeps_another_tools_hooks(codex_home):
    codex_home.mkdir(parents=True)
    (codex_home / "hooks.json").write_text(json.dumps({"hooks": {"SessionStart": [
        {"hooks": [{"type": "command", "command": "python3 ~/other/start.py"}]}]}}),
        encoding="utf-8")
    assert main(["hook", "install", "--runtime", "codex"]) == 0
    assert main(["hook", "uninstall", "--runtime", "codex"]) == 0
    config = _codex_json(codex_home)
    assert config["hooks"]["SessionStart"] == [
        {"hooks": [{"type": "command", "command": "python3 ~/other/start.py"}]}]
    assert "PreToolUse" not in config["hooks"]


def test_codex_uninstall_does_not_touch_claude_kinds(codex_home):
    """`posttool` is a substring of `codex-posttool`. Ownership stays scoped
    to the exact kind — scar 0016, one file over."""
    codex_home.mkdir(parents=True)
    (codex_home / "hooks.json").write_text(json.dumps({"hooks": {"PostToolUse": [
        {"hooks": [{"type": "command", "command": "/other/scar hook posttool"}]}]}}),
        encoding="utf-8")
    assert main(["hook", "install", "--runtime", "codex"]) == 0
    assert main(["hook", "uninstall", "--runtime", "codex"]) == 0
    commands = [h["command"] for g in _codex_json(codex_home)["hooks"]["PostToolUse"]
                for h in g["hooks"]]
    assert commands == ["/other/scar hook posttool"]


def test_codex_dry_run_writes_nothing(codex_home):
    assert main(["hook", "install", "--runtime", "codex", "--dry-run"]) == 0
    assert not (codex_home / "hooks.json").exists()


def test_codex_install_says_the_hooks_start_untrusted(codex_home, capsys):
    """Codex skips untrusted hooks SILENTLY — verified on codex-cli 0.147.0:
    the config parsed, a timeout warning printed, and the hook never ran. An
    install that does not say so reports a success the user does not have."""
    assert main(["hook", "install", "--runtime", "codex"]) == 0
    out = capsys.readouterr().out
    assert "/hooks" in out
    assert "trust" in out.lower()


def test_codex_status_reports_each_kind_from_the_file(codex_home, capsys):
    assert main(["hook", "status", "--runtime", "codex"]) == 0
    before = capsys.readouterr().out
    assert before.count("not installed") == 3
    assert main(["hook", "install", "--runtime", "codex"]) == 0
    capsys.readouterr()
    assert main(["hook", "status", "--runtime", "codex"]) == 0
    after = capsys.readouterr().out
    assert "not installed" not in after
    for kind in ("codex-session-notice", "codex-pretool", "codex-posttool"):
        assert kind in after


def test_codex_install_leaves_an_unreadable_config_alone(codex_home, capsys):
    codex_home.mkdir(parents=True)
    (codex_home / "hooks.json").write_text("{not json", encoding="utf-8")
    assert main(["hook", "install", "--runtime", "codex"]) == 1
    assert (codex_home / "hooks.json").read_text() == "{not json"


def test_codex_install_reports_a_missing_binary(codex_home, monkeypatch, capsys):
    monkeypatch.setattr(installer, "find_scar", lambda: None)
    assert main(["hook", "install", "--runtime", "codex"]) == 1
    assert not (codex_home / "hooks.json").exists()
    assert "not found" in capsys.readouterr().out


def test_codex_install_verifies_against_the_written_file(codex_home, monkeypatch,
                                                         capsys):
    """#236: success is asserted against what landed on disk, never intent."""
    real_save = installer._save_codex_config

    def drop_one(path, config, dry):
        config["hooks"].pop("PreToolUse", None)
        return real_save(path, config, dry)

    monkeypatch.setattr(installer, "_save_codex_config", drop_one)
    assert main(["hook", "install", "--runtime", "codex"]) == 1
    assert "FAILED" in capsys.readouterr().out


# --- #250: the plugin is a SECOND live Codex channel ------------------------
# #246 concluded the plugin could not deliver hooks because the cache carried
# no hooks.json. That cache was merely STALE — once re-materialized the plugin
# channel goes live, and Scar fires twice per tool call.


def _codex_plugin(home: Path, *, handler: str = "codex-pretool") -> Path:
    p = home / "plugins" / "cache" / "scar" / "scar" / "local" / "hooks"
    p.mkdir(parents=True, exist_ok=True)
    cfg = p / "hooks.json"
    cfg.write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": [
        {"type": "command",
         "command": f'"${{PLUGIN_ROOT}}/hooks/run.sh" {handler}'}]}]}}),
        encoding="utf-8")
    return cfg


def test_codex_install_warns_when_the_plugin_is_a_second_channel(codex_home,
                                                                 capsys):
    plugin = _codex_plugin(codex_home)
    assert main(["hook", "install", "--runtime", "codex"]) == 0
    out = capsys.readouterr().out
    assert "TWO" in out or "two" in out
    assert str(plugin) in out


def test_codex_install_is_quiet_when_no_plugin_channel_exists(codex_home,
                                                              capsys):
    """The warning has to stay rare or it becomes wallpaper."""
    assert main(["hook", "install", "--runtime", "codex"]) == 0
    assert "second" not in capsys.readouterr().out.lower()


def test_codex_install_ignores_a_plugin_that_registers_no_scar_handler(
        codex_home, capsys):
    """Another tool's plugin in the same cache is not our duplicate."""
    _codex_plugin(codex_home, handler="someone-elses-hook")
    assert main(["hook", "install", "--runtime", "codex"]) == 0
    assert "second" not in capsys.readouterr().out.lower()


def test_codex_install_still_succeeds_with_a_duplicate_channel(codex_home):
    """Warn, never refuse: the plugin may be present but stale or untrusted —
    exactly the #246 state, where the installer still has to work."""
    _codex_plugin(codex_home)
    assert main(["hook", "install", "--runtime", "codex"]) == 0
    assert (codex_home / "hooks.json").exists()


def test_codex_status_reports_the_plugin_channel(codex_home, capsys):
    plugin = _codex_plugin(codex_home)
    assert main(["hook", "status", "--runtime", "codex"]) == 0
    assert str(plugin) in capsys.readouterr().out


# --- #252: the warning must be silenceable ---------------------------------
# #251 detected a plugin channel by file existence alone, so the warning
# survived its own remedy. A warning that cannot be silenced stops being
# signal.


def _codex_hook_state(home: Path, entries: dict[str, bool | None]) -> Path:
    """Write ~/.codex/config.toml hooks.state blocks. None = omit `enabled`,
    which Codex treats as enabled."""
    home.mkdir(parents=True, exist_ok=True)
    cfg = home / "config.toml"
    out = ['model = "gpt-5"', ""]
    for key, enabled in entries.items():
        out.append(f'[hooks.state."{key}"]')
        out.append('trusted_hash = "sha256:deadbeef"')
        if enabled is not None:
            out.append(f"enabled = {'true' if enabled else 'false'}")
        out.append("")
    cfg.write_text("\n".join(out), encoding="utf-8")
    return cfg


_PLUGIN_KEYS = ["scar@scar:hooks/hooks.json:pre_tool_use:0:0",
                "scar@scar:hooks/hooks.json:post_tool_use:0:0",
                "scar@scar:hooks/hooks.json:session_start:0:0"]


def test_codex_no_warning_once_every_plugin_entry_is_disabled(codex_home, capsys):
    _codex_plugin(codex_home)
    _codex_hook_state(codex_home, {k: False for k in _PLUGIN_KEYS})
    assert main(["hook", "status", "--runtime", "codex"]) == 0
    assert "TWO channels" not in capsys.readouterr().out


def test_codex_still_warns_when_one_plugin_entry_is_left_enabled(codex_home,
                                                                 capsys):
    _codex_plugin(codex_home)
    state = {k: False for k in _PLUGIN_KEYS}
    state[_PLUGIN_KEYS[0]] = True
    _codex_hook_state(codex_home, state)
    assert main(["hook", "status", "--runtime", "codex"]) == 0
    assert "TWO channels" in capsys.readouterr().out


def test_codex_treats_an_omitted_enabled_flag_as_enabled(codex_home, capsys):
    """Codex's default is enabled, so only an explicit false silences us."""
    _codex_plugin(codex_home)
    state = {k: False for k in _PLUGIN_KEYS}
    state[_PLUGIN_KEYS[1]] = None
    _codex_hook_state(codex_home, state)
    assert main(["hook", "status", "--runtime", "codex"]) == 0
    assert "TWO channels" in capsys.readouterr().out


@pytest.mark.parametrize("content", [None, "{ not toml at all", ""])
def test_codex_warns_when_the_config_cannot_be_trusted(codex_home, capsys,
                                                       content):
    """Fail TOWARD the warning. A missed duplicate silently doubles every
    measurement; a spurious warning is merely annoying (#237's rule)."""
    _codex_plugin(codex_home)
    if content is not None:
        codex_home.mkdir(parents=True, exist_ok=True)
        (codex_home / "config.toml").write_text(content, encoding="utf-8")
    assert main(["hook", "status", "--runtime", "codex"]) == 0
    assert "TWO channels" in capsys.readouterr().out


def test_codex_install_agrees_with_status_about_a_disabled_plugin(codex_home,
                                                                  capsys):
    _codex_plugin(codex_home)
    _codex_hook_state(codex_home, {k: False for k in _PLUGIN_KEYS})
    assert main(["hook", "install", "--runtime", "codex"]) == 0
    assert "TWO channels" not in capsys.readouterr().out


def test_codex_ignores_another_tools_plugin_hook_state(codex_home, capsys):
    """scar 0016, third time: the first version of this check matched any key
    containing `hooks/hooks.json:`, so ANOTHER tool's enabled plugin kept our
    warning on forever — the warning survived its own remedy on a real
    machine. State must be scoped to our own <plugin>@<marketplace> prefix."""
    _codex_plugin(codex_home)
    state = {k: False for k in _PLUGIN_KEYS}
    state["daimon@daimon:hooks/hooks.json:session_start:0:0"] = True
    _codex_hook_state(codex_home, state)
    assert main(["hook", "status", "--runtime", "codex"]) == 0
    assert "TWO channels" not in capsys.readouterr().out


def test_codex_plugin_ref_is_derived_from_the_cache_path(codex_home):
    from scar.installer import _codex_plugin_ref

    plugin = _codex_plugin(codex_home)
    assert _codex_plugin_ref(plugin) == "scar@scar"
    assert _codex_plugin_ref(Path("/nowhere/hooks.json")) is None


def test_claude_hooks_present_only_when_every_kind_is_owned(isolated_settings):
    assert installer.claude_hooks_present() is False
    assert main(["hook", "install", "--runtime", "claude"]) == 0
    assert installer.claude_hooks_present() is True


def test_codex_hooks_present_reads_codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codexhome"))
    monkeypatch.setattr(installer, "find_scar", lambda: "/stable/bin/scar")
    assert installer.codex_hooks_present() is False
    assert main(["hook", "install", "--runtime", "codex"]) == 0
    assert installer.codex_hooks_present() is True


def test_cascade_hooks_present_is_repo_scoped(cascade_repo):
    assert installer.cascade_hooks_present(cascade_repo) is False
    assert main(["hook", "install", "--runtime", "windsurf"]) == 0
    assert installer.cascade_hooks_present(cascade_repo) is True


def test_skill_present_follows_skill_dir(tmp_path, monkeypatch):
    claude = tmp_path / ".claude"
    monkeypatch.setattr(installer, "CLAUDE_DIR", claude)
    monkeypatch.setattr(installer, "SKILLS_DIR", claude / "skills")
    assert installer.skill_present() is False
    assert main(["skill", "install"]) == 0
    assert installer.skill_present() is True


# --- detection-driven install (#303): no --runtime means "look, then ask" ---

@pytest.fixture
def no_hosts(tmp_path, monkeypatch, isolated_settings):
    """Fake home with only the claude dir, empty PATH, no codex, no windsurf."""
    from scar import hosts
    monkeypatch.setenv("PATH", str(tmp_path / "nobin"))
    (tmp_path / "nobin").mkdir(exist_ok=True)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codexhome"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hosts, "is_interactive", lambda: False)
    installer.CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_hook_install_no_flag_no_tty_single_host_installs_claude(no_hosts, capsys):
    assert main(["hook", "install"]) == 0
    out = capsys.readouterr().out
    assert "only unserved host" in out
    assert installer.claude_hooks_present()


def test_hook_install_no_flag_no_tty_two_hosts_writes_nothing(no_hosts, capsys):
    (no_hosts / "codexhome").mkdir()
    assert main(["hook", "install"]) == 0
    out = capsys.readouterr().out
    assert "scar hook install --runtime codex" in out
    assert not installer.claude_hooks_present()
    assert not installer.codex_hooks_present()


def test_hook_install_all_wires_every_candidate(no_hosts):
    (no_hosts / "codexhome").mkdir()
    assert main(["hook", "install", "--all"]) == 0
    assert installer.claude_hooks_present()
    assert installer.codex_hooks_present()


def test_hook_install_tty_asks_and_honours_answers(no_hosts, monkeypatch):
    from scar import hosts
    (no_hosts / "codexhome").mkdir()
    monkeypatch.setattr(hosts, "is_interactive", lambda: True)
    monkeypatch.setattr(hosts, "ask_yes_no", lambda q: "codex" in q)
    assert main(["hook", "install"]) == 0
    assert not installer.claude_hooks_present()
    assert installer.codex_hooks_present()


def test_hook_install_claude_skipped_when_plugin_serves(no_hosts, capsys):
    plugins = installer.CLAUDE_DIR / "plugins"
    plugins.mkdir()
    (plugins / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {"scar@scar": [{"scope": "user"}]}}),
        encoding="utf-8")
    assert main(["hook", "install", "--runtime", "claude"]) == 0
    out = capsys.readouterr().out
    assert "provided by the scar plugin" in out and "--force" in out
    assert not installer.claude_hooks_present()
    assert main(["hook", "install", "--runtime", "claude", "--force"]) == 0
    assert installer.claude_hooks_present()


def test_hook_status_no_flag_prints_host_table_then_claude_table(no_hosts, capsys):
    assert main(["hook", "status"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("claude")
    assert "channel=none" in out
    assert out.count("not installed") == 5


def test_hook_flags_are_exclusive(tmp_path, monkeypatch, capsys):
    """Picking two targets is argparse's job: exit 2 with a usage line, the
    same as every other bad flag combination here. `--force` is not a target,
    so it fails in the handler, and the handler never raises to the shell."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        main(["hook", "install", "--all", "--runtime", "claude"])
    with pytest.raises(SystemExit):
        main(["hook", "install", "--all", "--git"])
    capsys.readouterr()
    assert main(["hook", "install", "--force"]) == 2
    assert "--force needs --runtime" in capsys.readouterr().out


def test_hook_install_all_reaches_the_windsurf_writer(no_hosts):
    """Windsurf is repo-scoped, so detection sees it from the cwd, not $HOME."""
    (no_hosts / ".windsurf").mkdir()
    assert main(["hook", "install", "--all"]) == 0
    assert installer.claude_hooks_present()
    assert installer.cascade_hooks_present(no_hosts)
