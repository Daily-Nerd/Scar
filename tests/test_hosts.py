import json
from pathlib import Path

from scar import hosts


def _bin(tmp_path: Path, name: str) -> Path:
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_text("#!/bin/sh\n", encoding="utf-8")
    f.chmod(0o755)
    return d


def _nobin(tmp_path: Path) -> str:
    d = tmp_path / "nobin"
    d.mkdir(exist_ok=True)
    return str(d)


def test_detect_hosts_reports_every_known_host_absent_on_empty_home(tmp_path):
    found = hosts.detect_hosts(tmp_path, None, path_env=_nobin(tmp_path))
    assert [h.name for h in found] == ["claude", "codex", "windsurf", "cursor", "opencode"]
    assert all(not h.present for h in found)
    assert all(h.channel == "none" for h in found)


def test_config_dir_counts_as_present(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".config" / "opencode").mkdir(parents=True)
    by = {h.name: h for h in hosts.detect_hosts(tmp_path, None, path_env=_nobin(tmp_path))}
    assert by["claude"].present and by["claude"].signal == str(tmp_path / ".claude")
    assert by["opencode"].present
    assert not by["codex"].present


def test_binary_on_path_counts_as_present(tmp_path):
    bindir = _bin(tmp_path, "codex")
    by = {h.name: h for h in hosts.detect_hosts(tmp_path, None, path_env=str(bindir))}
    assert by["codex"].present and by["codex"].signal == "codex on PATH"


def test_codex_dir_override_honours_codex_home(tmp_path):
    custom = tmp_path / "elsewhere"
    custom.mkdir()
    by = {
        h.name: h
        for h in hosts.detect_hosts(tmp_path, None, path_env=_nobin(tmp_path), codex_dir=custom)
    }
    assert by["codex"].present and by["codex"].signal == str(custom)


def test_windsurf_is_repo_scoped(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".windsurf").mkdir(parents=True)
    with_repo = {h.name: h for h in hosts.detect_hosts(tmp_path, repo, path_env=_nobin(tmp_path))}
    without = {h.name: h for h in hosts.detect_hosts(tmp_path, None, path_env=_nobin(tmp_path))}
    assert with_repo["windsurf"].present
    assert not without["windsurf"].present


def test_wirable_and_hints_for_hook_kind(tmp_path):
    by = {h.name: h for h in hosts.detect_hosts(tmp_path, None, path_env=_nobin(tmp_path))}
    assert by["claude"].wirable and by["codex"].wirable and by["windsurf"].wirable
    assert not by["cursor"].wirable and by["cursor"].hint == "scar agent config cursor"
    assert not by["opencode"].wirable and by["opencode"].hint == "scar agent config opencode"


def test_skill_kind_wires_claude_only(tmp_path):
    by = {h.name: h for h in hosts.detect_hosts(tmp_path, None, kind="skill", path_env=_nobin(tmp_path))}
    assert by["claude"].wirable
    assert not by["codex"].wirable and "not supported yet" in by["codex"].hint


def test_render_table_one_line_per_host(tmp_path):
    (tmp_path / ".claude").mkdir()
    out = hosts.render_table(hosts.detect_hosts(tmp_path, None, path_env=_nobin(tmp_path)))
    lines = out.splitlines()
    assert len(lines) == 5
    assert lines[0].startswith("claude")
    assert "present" in lines[0] and "channel=none" in lines[0]
    assert "absent" in lines[1]
    assert "scar agent config cursor" in lines[3]


def _registry(claude_dir: Path, *, installed: bool = True, enabled: bool | None = None):
    plugins = claude_dir / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    reg = {"version": 2, "plugins": {"scar@scar": [{"scope": "user"}]} if installed else {}}
    (plugins / "installed_plugins.json").write_text(json.dumps(reg), encoding="utf-8")
    if enabled is not None:
        (claude_dir / "settings.json").write_text(
            json.dumps({"enabledPlugins": {"scar@scar": enabled}}), encoding="utf-8")


def test_plugin_enabled_requires_registry_entry(tmp_path):
    claude = tmp_path / ".claude"
    assert hosts.claude_plugin_enabled(claude) is False
    _registry(claude, installed=True)
    assert hosts.claude_plugin_enabled(claude) is True


def test_plugin_explicitly_disabled_is_off(tmp_path):
    claude = tmp_path / ".claude"
    _registry(claude, installed=True, enabled=False)
    assert hosts.claude_plugin_enabled(claude) is False


def test_unreadable_registry_is_not_plugin(tmp_path):
    claude = tmp_path / ".claude"
    (claude / "plugins").mkdir(parents=True)
    (claude / "plugins" / "installed_plugins.json").write_text("{not json", encoding="utf-8")
    assert hosts.claude_plugin_enabled(claude) is False


def test_resolve_channels_marks_plugin_then_settings(tmp_path, monkeypatch):
    from scar import installer
    claude = tmp_path / ".claude"
    claude.mkdir()
    monkeypatch.setattr(installer, "CLAUDE_DIR", claude)
    monkeypatch.setattr(installer, "SETTINGS", claude / "settings.json")
    found = hosts.detect_hosts(tmp_path, None, path_env=str(tmp_path))
    by = {h.name: h for h in hosts.resolve_channels(found, claude_dir=claude, repo=None)}
    assert by["claude"].channel == "none"
    _registry(claude, installed=True)
    by = {h.name: h for h in hosts.resolve_channels(found, claude_dir=claude, repo=None)}
    assert by["claude"].channel == "plugin"


def test_resolve_channels_settings_when_hooks_owned(tmp_path, monkeypatch):
    from scar import installer
    claude = tmp_path / ".claude"
    claude.mkdir()
    monkeypatch.setattr(installer, "CLAUDE_DIR", claude)
    monkeypatch.setattr(installer, "SETTINGS", claude / "settings.json")
    monkeypatch.setattr(installer, "claude_hooks_present", lambda: True)
    found = hosts.detect_hosts(tmp_path, None, path_env=str(tmp_path))
    by = {h.name: h for h in hosts.resolve_channels(found, claude_dir=claude, repo=None)}
    assert by["claude"].channel == "settings"


def _host(name, present=True, wirable=True, channel="none"):
    return hosts.Host(name, present, "sig" if present else "", wirable, channel)


def test_all_flag_installs_every_unserved_wirable_host():
    found = [_host("claude"), _host("codex"), _host("windsurf", channel="settings"),
             _host("cursor", wirable=False)]
    d = hosts.decide(found, interactive=False, all_flag=True, ask=lambda q: False, command="hook")
    assert d.install == ["claude", "codex"]


def test_no_tty_single_candidate_installs_and_says_why():
    found = [_host("claude"), _host("codex", present=False)]
    d = hosts.decide(found, interactive=False, all_flag=False, ask=lambda q: False, command="hook")
    assert d.install == ["claude"]
    assert any("only" in ln and "claude" in ln for ln in d.lines)


def test_no_tty_several_candidates_installs_nothing_and_prints_commands():
    found = [_host("claude"), _host("codex")]
    d = hosts.decide(found, interactive=False, all_flag=False, ask=lambda q: False, command="hook")
    assert d.install == []
    joined = "\n".join(d.lines)
    assert "scar hook install --runtime claude" in joined
    assert "scar hook install --runtime codex" in joined
    assert "scar hook install --all" in joined


def test_no_tty_zero_candidates_reports_served_hosts():
    found = [_host("claude", channel="plugin")]
    d = hosts.decide(found, interactive=False, all_flag=False, ask=lambda q: False, command="hook")
    assert d.install == []
    assert any("plugin" in ln for ln in d.lines)


def test_tty_asks_once_per_candidate_default_no():
    asked = []
    def ask(q):
        asked.append(q)
        return "codex" in q
    found = [_host("claude"), _host("codex"), _host("windsurf", channel="settings")]
    d = hosts.decide(found, interactive=True, all_flag=False, ask=ask, command="hook")
    assert len(asked) == 2
    assert d.install == ["codex"]


def test_ask_yes_no_defaults_to_no(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert hosts.ask_yes_no("wire claude?") is False
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert hosts.ask_yes_no("wire claude?") is True


def test_is_interactive_requires_both_streams(monkeypatch):
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert hosts.is_interactive() is False


def test_tty_zero_candidates_served_only_reports_and_never_asks():
    asked = []
    found = [_host("claude", channel="plugin")]
    d = hosts.decide(found, interactive=True, all_flag=False,
                      ask=lambda q: asked.append(q) or True, command="hook")
    assert d.install == []
    assert asked == []
    assert any("nothing to install" in ln for ln in d.lines)


def test_tty_zero_candidates_completely_empty_reports_and_never_asks():
    asked = []
    d = hosts.decide([], interactive=True, all_flag=False,
                      ask=lambda q: asked.append(q) or True, command="hook")
    assert d.install == []
    assert asked == []
    assert any("nothing to install" in ln for ln in d.lines)


def test_all_flag_zero_candidates_reports_and_installs_nothing():
    found = [_host("claude", channel="plugin")]
    d = hosts.decide(found, interactive=False, all_flag=True, ask=lambda q: False, command="hook")
    assert d.install == []
    assert any("nothing to install" in ln for ln in d.lines)
