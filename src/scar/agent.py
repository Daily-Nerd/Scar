"""Agent integration helpers."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# Where the marketplace plugin's wrapper (plugin/hooks/run.sh) looks for the
# scar binary when it is not on the plugin runtime's PATH (#113). Keep this
# list byte-in-sync with run.sh — doctor reports what the PLUGIN would
# resolve, which is a different question from find_scar() (venv-aware, only
# serves `scar hook install`).
_PLUGIN_CANDIDATE_DIRS = [
    "~/.local/bin/scar",
    "~/.pipx/venvs/scar-cli/bin/scar",
    "~/.local/pipx/venvs/scar-cli/bin/scar",
    "~/.local/share/uv/tools/scar-cli/bin/scar",
]


def plugin_resolve() -> str | None:
    """Resolve scar the way plugin/hooks/run.sh does: PATH first, then the
    fixed candidate list. None means the plugin's hooks silently no-op."""
    found = shutil.which("scar")
    if found:
        return found
    for cand in _PLUGIN_CANDIDATE_DIRS:
        p = Path(os.path.expanduser(cand))
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None

_MCP_SERVERS_SNIPPET = """\
Configure a local MCP server named "scar" with:

{
  "mcpServers": {
    "scar": {
      "command": "scar",
      "args": ["mcp"]
    }
  }
}
"""

# target -> setup text; adding a runtime is one entry here, no logic change
CONFIGS = {
    "codex": """\
Codex-compatible setup:

1. Keep AGENTS.md committed at the repository root.
2. Expose SCAR through MCP with command: scar mcp
3. For direct shell use, ask the agent to run:
   scar inject --path <path> --content <new-content>
   scar inject --diff <unified-diff>
""",
    "cursor": _MCP_SERVERS_SNIPPET,
    "opencode": """\
Add this to opencode.jsonc:

{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "scar": {
      "type": "local",
      "command": ["scar", "mcp"],
      "enabled": true
    }
  }
}
""",
    "windsurf": "Cascade: " + _MCP_SERVERS_SNIPPET,
}

TARGETS = tuple(sorted(CONFIGS))


def doctor(repo: Path) -> list[str]:
    root = repo.resolve()
    findings = []
    findings.append(f"AGENTS.md: {'present' if (root / 'AGENTS.md').exists() else 'missing'}")
    findings.append(f".scars/: {'present' if (root / '.scars').is_dir() else 'missing'}")
    findings.append(f"scar binary: {shutil.which('scar') or 'not found on PATH'}")
    resolved = plugin_resolve()
    findings.append(f"plugin PATH resolution: {resolved}" if resolved else
                    "plugin PATH resolution: not resolvable — plugin hooks will no-op")
    findings.append("MCP command: scar mcp")
    return findings


def config(target: str) -> str:
    if target not in CONFIGS:
        raise ValueError(f"unknown target '{target}' (expected: {', '.join(TARGETS)})")
    return CONFIGS[target]


def skill() -> str:
    """Return the full scar-authoring SKILL.md body (packaged, runtime-neutral)."""
    from importlib.resources import files
    resource = files("scar").joinpath("skills/scar-authoring/SKILL.md")
    return resource.read_text(encoding="utf-8")
