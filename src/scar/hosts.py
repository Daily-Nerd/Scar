"""Which agent hosts exist on this machine, and which channel already serves
each one. Pure detection: no writes, no prompts, no network (#303).

`present` is a filesystem or PATH fact. `channel` is filled by
resolve_channels() from the installer's own ownership checks. A missing or
unreadable registry never counts as "plugin": missing is not a value.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


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


def _in_git_repo(start: Path) -> bool:
    """Walk up from `start` looking for a `.git` entry. It is a directory in a
    normal clone and a file in a linked worktree, so existence is the test."""
    try:
        here = start.resolve()
    except OSError:
        return False
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return True
    return False


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
        # windsurf is the one repo-scoped host: cascade_install writes
        # <cwd>/.windsurf/hooks.json with no repo guard of its own, so the
        # binary alone must not make it a candidate. Inside a git repo that
        # path is a file the user can review and commit; outside one it is a
        # stray directory in whatever folder the shell happened to be in.
        on_path = bool(shutil.which(binary, path=which_path))
        if name == "windsurf" and on_path:
            on_path = repo is not None and _in_git_repo(repo)
        if cfg is not None and cfg.is_dir():
            signal = str(cfg)
        elif on_path:
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


def claude_plugin_enabled(claude_dir: Path, ref: str = "scar@scar") -> bool:
    """Installed in installed_plugins.json AND not explicitly disabled in
    settings.json enabledPlugins. Unreadable or missing means False: a
    missing registry never counts as "plugin"."""
    reg = claude_dir / "plugins" / "installed_plugins.json"
    try:
        plugins = json.loads(reg.read_text(encoding="utf-8")).get("plugins") or {}
    except (OSError, ValueError, AttributeError):
        return False
    if ref not in plugins:
        return False
    try:
        enabled = (json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
                   .get("enabledPlugins") or {}).get(ref)
    except (OSError, ValueError, AttributeError):
        enabled = None
    return enabled is not False


def resolve_channels(
    found: list[Host], *, claude_dir: Path, repo: Path | None, kind: str = "hook"
) -> list[Host]:
    from . import installer  # lazy: installer binds paths at import

    for h in found:
        if not (h.present and h.wirable):
            continue
        if h.name == "claude":
            if claude_plugin_enabled(claude_dir):
                h.channel = "plugin"
            elif (installer.skill_present() if kind == "skill"
                  else installer.claude_hooks_present()):
                h.channel = "settings"
        elif h.name == "codex" and installer.codex_hooks_present():
            h.channel = "settings"
        elif h.name == "windsurf" and repo is not None and installer.cascade_hooks_present(repo):
            h.channel = "settings"
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


@dataclass
class Decision:
    install: list[str]
    lines: list[str]


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def ask_yes_no(question: str) -> bool:
    try:
        answer = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def decide(found: list[Host], *, interactive: bool, all_flag: bool,
           ask: Callable[[str], bool], command: str) -> Decision:
    # Re-running install is the documented post-upgrade step: the installed
    # skill is a static copy and hooks re-bind the scar binary path, so a
    # host already served by `settings` must stay reachable through the
    # no-flag path. Only `plugin` takes a host out of the running: the
    # plugin channel is a different distribution entirely, not something
    # `scar hook`/`scar skill` writes over.
    candidates = [h for h in found if h.present and h.wirable and h.channel != "plugin"]
    plugin_served = [h for h in found if h.present and h.wirable and h.channel == "plugin"]
    lines = [f"{h.name}: already served by plugin, not asked" for h in plugin_served]
    if not candidates:
        # An empty machine and a machine whose hosts are all served are two
        # different answers. Blaming the plugin when nothing was detected
        # sends the reader looking for a plugin that is not there.
        if not any(h.present for h in found):
            lines.append("nothing to install: no agent host detected on this machine")
        else:
            lines.append("nothing to install: every detected host is served by "
                         "the plugin or not wirable")
        return Decision([], lines)
    names = [h.name for h in candidates]
    if all_flag:
        return Decision(names, lines)
    if interactive:
        chosen = []
        for h in candidates:
            question = (f"{h.name} is already wired, refresh?" if h.channel == "settings"
                       else f"wire {h.name}?")
            if ask(question):
                chosen.append(h.name)
        return Decision(chosen, lines)
    if len(candidates) == 1:
        only = candidates[0]
        if only.channel == "settings":
            lines.append(f"{only.name}: only host detected, refreshing without asking "
                         f"(no terminal to ask on)")
        else:
            lines.append(f"{only.name}: only unserved host detected, installing without asking "
                         f"(no terminal to ask on)")
        return Decision([only.name], lines)
    lines.append("several hosts detected and no terminal to ask on; nothing written. Run one of:")
    lines.extend(f"  scar {command} install --runtime {n}" for n in names)
    lines.append(f"  scar {command} install --all")
    return Decision([], lines)
