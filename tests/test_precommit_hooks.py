"""Shape tests for .pre-commit-hooks.yaml (#113).

No PyYAML dependency: the file is a small, deliberately flat subset (a list
of `- id:` blocks with single-line scalar values), so a regex-based parser
is enough to keep this stdlib-only — same posture as test_plugin.py's
regex-over-tomllib version check.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from scar.cli import build_parser

ROOT = Path(__file__).parent.parent
HOOKS_YAML = ROOT / ".pre-commit-hooks.yaml"

_ID_RE = re.compile(r"^- id:\s*(\S+)\s*$")
_KV_RE = re.compile(r"^\s{2,}(\w+):\s*(.+?)\s*$")


def _parse_hooks() -> dict[str, dict[str, str]]:
    """Split '.pre-commit-hooks.yaml' into {id: {key: value}} — one dict per
    top-level '- id:' block. Deliberately dumb: no nesting, no quoting rules,
    no anchors — the file must stay simple enough for this to hold."""
    text = HOOKS_YAML.read_text(encoding="utf-8")
    hooks: dict[str, dict[str, str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        id_match = _ID_RE.match(line)
        if id_match:
            current = id_match.group(1)
            hooks[current] = {}
            continue
        kv_match = _KV_RE.match(line)
        if kv_match and current is not None:
            hooks[current][kv_match.group(1)] = kv_match.group(2)
    return hooks


def test_pre_commit_hooks_file_exists():
    assert HOOKS_YAML.is_file(), ".pre-commit-hooks.yaml must exist at repo root"


def test_pre_commit_hooks_declares_scar_lint_and_scar_check():
    hooks = _parse_hooks()
    assert set(hooks) == {"scar-lint", "scar-check"}


def test_scar_lint_hook_shape():
    hooks = _parse_hooks()
    h = hooks["scar-lint"]
    assert h["entry"] == "scar lint --fail-orphans"
    assert h["language"] == "python"
    assert h["pass_filenames"] == "false"
    assert h["always_run"] == "true"


def test_scar_check_hook_shape():
    hooks = _parse_hooks()
    h = hooks["scar-check"]
    assert h["entry"] == "scar check --exit-code"
    assert h["language"] == "python"
    # pass_filenames must NOT be false — staged filenames are the whole point
    # of wiring #106's gate through pre-commit.
    assert h.get("pass_filenames", "true") == "true"


def test_hook_entries_reference_real_subcommands_and_flags():
    """Every entry (minus the leading 'scar') must parse cleanly against the
    real argparse tree — keeps the YAML honest as the CLI evolves (#113)."""
    hooks = _parse_hooks()
    parser = build_parser()
    for hook_id, spec in hooks.items():
        tokens = shlex.split(spec["entry"])
        assert tokens[0] == "scar", f"{hook_id}: entry must start with 'scar'"
        args = parser.parse_args(tokens[1:])
        assert args.command in {"lint", "check"}
