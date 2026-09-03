"""Which agent hosts exist on this machine, and which channel already serves
each one. Pure detection: no writes, no prompts, no network (#303).

`present` is a filesystem or PATH fact. `channel` is filled by
resolve_channels() from the installer's own ownership checks. A missing or
unreadable registry never counts as "plugin": missing is not a value.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Host:
    name: str
    present: bool
    signal: str
    wirable: bool
    channel: str = "none"
    hint: str | None = None


# name, home-relative config dir (None = repo-scoped), binary on PATH,
# wirable for hooks, wirable for the skill
_SPECS = (
    ("claude", ".claude", "claude", True, True),
    ("codex", ".codex", "codex", True, False),
    ("windsurf", None, "windsurf", True, False),
    ("cursor", ".cursor", "cursor", False, False),
    ("opencode", ".config/opencode", "opencode", False, False),
)


def detect_hosts(
    home: Path,
    repo: Path | None,
    *,
    kind: str = "hook",
    path_env: str | None = None,
    codex_dir: Path | None = None,
) -> list[Host]:
    which_path = path_env if path_env is not None else os.environ.get("PATH", "")
    found: list[Host] = []
    for name, rel, binary, hook_ok, skill_ok in _SPECS:
        signal = ""
        cfg = None
        if name == "codex" and codex_dir is not None:
            cfg = codex_dir
        elif rel is not None:
            cfg = home / rel
        elif repo is not None:
            cfg = repo / ".windsurf"
        if cfg is not None and cfg.is_dir():
            signal = str(cfg)
        elif shutil.which(binary, path=which_path):
            signal = f"{binary} on PATH"
        wirable = skill_ok if kind == "skill" else hook_ok
        hint = None
        if not wirable:
            hint = (
                f"scar skill install for {name} is not supported yet"
                if kind == "skill"
                else f"scar agent config {name}"
            )
        found.append(Host(name, bool(signal), signal, wirable, hint=hint))
    return found


def render_table(found: list[Host]) -> str:
    lines = []
    for h in found:
        state = "present" if h.present else "absent"
        tail = f"channel={h.channel}" if h.wirable else f"not wirable, {h.hint}"
        line = f"{h.name:9} {state:8} {tail}"
        if h.present:
            line += f"  ({h.signal})"
        lines.append(line)
    return "\n".join(lines)
