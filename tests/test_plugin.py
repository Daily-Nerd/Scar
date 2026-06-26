"""Claude Code plugin manifest validity + skill mirror drift guard."""

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_plugin_manifest_is_valid_and_declares_three_hook_events():
    manifest = json.loads((ROOT / "plugin" / "plugin.json").read_text())
    assert manifest["name"] == "scar"
    assert set(manifest["hooks"]) == {"PreToolUse", "SessionStart", "Stop"}


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
