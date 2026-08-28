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

# Appended to every target's setup text below (#117) — runtime-neutral on
# purpose, same block regardless of MCP vs shell-only integration. Mirrors
# the reciprocal-duty wording hooks.py's session_notice() already puts in
# front of Claude Code (hooks.py ~132-141): same contract, same paths, so a
# human copying this into AGENTS.md gets the identical convention no matter
# which runtime reads it.
_AGENTS_MD_BLOCK = """\

Add this to AGENTS.md (or your runtime's always-loaded instructions file):

## SCAR — negative knowledge

Reciprocal duty: when you abandon an approach (deadend), keep intentional-
looking weirdness (fence), or discover non-obvious coupling (landmine),
record it. COPY .scars/template.md (YAML frontmatter is mandatory — scars
without it never fire), write to .scars/candidates/<slug>.md with
status: candidate, and never write directly into .scars/ — only a human
reviewer promotes.

Run `scar draft-check` before ending a session. It reads recent git history
(revert language, actual reverts/resets, high-churn files) and tells you
whether this session shows abandonment signals worth writing up — no
transcript access required, so it works the same for every runtime.
"""

# target -> setup text; adding a runtime is one entry here, no logic change
CONFIGS = {
    "codex": """\
Codex native setup (push injection — run this once):

  scar hook install --runtime codex

Writes ~/.codex/hooks.json (honours $CODEX_HOME). That file is shared with
every other tool you have wired into Codex, so it is merged, never
overwritten.

Then, and this step is not optional:

1. Open `/hooks` in Codex, review the three Scar entries, and trust them.
   Codex skips an untrusted hook SILENTLY — no error, no stderr — so an
   untrusted install looks exactly like a working one.
2. Re-trust after any upgrade that changes a hook definition.
3. Keep AGENTS.md committed at the repository root.

Once trusted, Scar pushes matching scars before Codex `Bash` and
`apply_patch` calls and checks successful patches for `violation:` tripwires.
All feedback is advisory and fail-open.

`scar hook status --runtime codex` reports what is in the file; it cannot see
Codex's trust record, so a hook shown as `installed` may still be untrusted.

For pull access as a companion or CLI-only fallback, expose SCAR through MCP
with command `scar mcp`. For direct shell use, ask the agent to run:
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
    # Windsurf is the one non-Claude runtime with both channels: MCP for pull
    # (the agent asks) and Cascade hooks for push (the anchor fires whether it
    # asks or not). Lead with the hooks — pull-only means anchors never fire
    # unless the agent thinks to query.
    "windsurf": """\
Cascade hooks (push injection — run this inside the repo):

  scar hook install --runtime windsurf

Writes .windsurf/hooks.json (workspace-level, committable; merged, never
overwritten). An armed scar matching a pending write or command blocks that
action once, with the scar on stderr where Cascade shows it to the agent; the
identical retry proceeds. Hooks do not load in a Restricted Mode workspace.

Plus, for pull access — """ + _MCP_SERVERS_SNIPPET,
}

# Every target carries the same reciprocal-duty + draft-check block (#117):
# one place to extend, applied uniformly, instead of appending it four times
# above where a fifth runtime could forget it.
CONFIGS = {target: text + _AGENTS_MD_BLOCK for target, text in CONFIGS.items()}

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
    findings.append("Codex hook trust: host-owned — run /hooks to confirm the "
                    "Scar plugin hooks are enabled and trusted")
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
