"""Shape tests for action.yml (#113) — composite GitHub Action.

Same regex-not-PyYAML posture as test_precommit_hooks.py: action.yml is a
small, deliberately flat manifest, so a line-based parser is enough to keep
this stdlib-only.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
ACTION_YML = ROOT / "action.yml"


def _text() -> str:
    return ACTION_YML.read_text(encoding="utf-8")


def test_action_yml_exists():
    assert ACTION_YML.is_file(), "action.yml must exist at repo root"


def test_action_is_composite():
    assert re.search(r"(?m)^\s*using:\s*[\"']?composite[\"']?\s*$", _text())


def test_action_declares_expected_inputs_with_defaults():
    text = _text()
    inputs = re.search(r"(?ms)^inputs:\n(.*?)^(?:runs|outputs|branding):", text)
    assert inputs, "inputs: block not found"
    block = inputs.group(1)

    expected_defaults = {
        "fail-orphans": "'true'",
        "version": "''",
        "args": "''",
    }
    for name, default in expected_defaults.items():
        name_match = re.search(rf"(?m)^\s{{2}}{re.escape(name)}:\s*$", block)
        assert name_match, f"input '{name}' not declared"
        after = block[name_match.end():]
        default_match = re.search(r"(?m)^\s*default:\s*(.+?)\s*$", after)
        assert default_match, f"input '{name}' has no default"
        assert default_match.group(1) == default, (
            f"input '{name}' default {default_match.group(1)!r} != {default!r}")


def test_action_installs_scar_cli_from_pypi():
    text = _text()
    assert "pip install" in text
    assert "scar-cli" in text


def test_action_runs_scar_lint():
    text = _text()
    assert re.search(r"scar lint", text)


def test_action_uses_fail_orphans_input_conditionally():
    text = _text()
    assert "inputs.fail-orphans" in text
    assert "--fail-orphans" in text


def test_action_forwards_extra_args_input():
    text = _text()
    assert "inputs.args" in text
