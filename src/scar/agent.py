"""Agent integration helpers."""

from __future__ import annotations

import shutil
from pathlib import Path


TARGETS = ("codex", "cursor", "opencode", "windsurf")


def doctor(repo: Path) -> list[str]:
    root = repo.resolve()
    findings = []
    findings.append(f"AGENTS.md: {'present' if (root / 'AGENTS.md').exists() else 'missing'}")
    findings.append(f".scars/: {'present' if (root / '.scars').is_dir() else 'missing'}")
    findings.append(f"scar binary: {shutil.which('scar') or 'not found on PATH'}")
    findings.append("MCP command: scar mcp")
    return findings


def config(target: str) -> str:
    if target not in TARGETS:
        raise ValueError(f"unknown target '{target}' (expected: {', '.join(TARGETS)})")
    if target == "opencode":
        return """\
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
"""
    if target == "cursor":
        return """\
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
    if target == "windsurf":
        return """\
Configure a local Cascade MCP server named "scar" with:

{
  "mcpServers": {
    "scar": {
      "command": "scar",
      "args": ["mcp"]
    }
  }
}
"""
    return """\
Codex-compatible setup:

1. Keep AGENTS.md committed at the repository root.
2. Expose SCAR through MCP with command: scar mcp
3. For direct shell use, ask the agent to run:
   scar inject --path <path> --content <new-content>
   scar inject --diff <unified-diff>
"""
