"""Claude Code plugin manifest validity + skill mirror drift guard."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_plugin_version_matches_pyproject():
    # plugin.json carries its own version; release-please bumps only pyproject,
    # so the two silently drift unless guarded. Regex (not tomllib) keeps this
    # stdlib-only and Python 3.10-safe.
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', pyproject)
    assert match, "version not found in pyproject.toml"
    py_version = match.group(1)
    plugin = json.loads((ROOT / "plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert plugin["version"] == py_version, (
        f"plugin.json version {plugin['version']!r} != pyproject {py_version!r}")


def test_plugin_manifest_is_valid_and_declares_four_hook_events():
    manifest = json.loads((ROOT / "plugin" / "plugin.json").read_text())
    assert manifest["name"] == "scar"
    assert set(manifest["hooks"]) == {"PreToolUse", "PostToolUse", "SessionStart", "Stop"}


def test_marketplace_manifest_lists_the_plugin():
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    names = [p["name"] for p in market["plugins"]]
    assert "scar" in names


def test_plugin_skill_mirror_is_byte_identical_to_canonical():
    canonical = (ROOT / "src" / "scar" / "skills" / "scar-authoring" / "SKILL.md").read_bytes()
    mirror = (ROOT / "plugin" / "skills" / "scar-authoring" / "SKILL.md").read_bytes()
    assert mirror == canonical
    can_tmpl = (ROOT / "src" / "scar" / "skills" / "scar-authoring" / "assets" / "template.md").read_bytes()
    mir_tmpl = (ROOT / "plugin" / "skills" / "scar-authoring" / "assets" / "template.md").read_bytes()
    assert mir_tmpl == can_tmpl


def test_skill_teaches_violation_field():
    # #151: agents author scars by following SKILL.md's instructions; the
    # violation: tripwire must be taught there, not only hinted at in a
    # template comment, or scars ship unarmed and the fired→violated
    # metric loses coverage.
    skill = (ROOT / "src" / "scar" / "skills" / "scar-authoring" / "SKILL.md").read_text(encoding="utf-8")
    assert "violation:" in skill
    assert "check --diff" in skill  # verify-both-cases instruction


def test_plugin_hooks_invoke_resolver_wrapper():
    """#113: hooks must go through the plugin-root wrapper, never a bare
    `scar` — a bare command silently no-ops forever when the plugin
    runtime's PATH misses the binary, and nobody is ever told."""
    manifest = json.loads((ROOT / "plugin" / "plugin.json").read_text())
    expected = {
        "PreToolUse": "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh precheck",
        "PostToolUse": "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh posttool",
        "SessionStart": "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh session-notice",
        "Stop": "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh stop-drafter",
    }
    for event, command in expected.items():
        commands = [h["command"]
                    for group in manifest["hooks"][event]
                    for h in group["hooks"]]
        assert command in commands, f"{event} must invoke the wrapper: '{command}'"


def test_plugin_wrapper_script_is_shipped_and_executable():
    run_sh = ROOT / "plugin" / "hooks" / "run.sh"
    assert run_sh.is_file(), "plugin/hooks/run.sh missing — plugin.json points at it"
    assert run_sh.stat().st_mode & 0o111, "run.sh must be executable"
