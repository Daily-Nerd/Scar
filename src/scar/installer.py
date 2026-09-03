"""Claude Code hook lifecycle management.

The user invokes these commands explicitly. SCAR never installs global hooks
as a side effect of package installation, ``scar init``, or an agent action.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
HOOKS_DIR = CLAUDE_DIR / "hooks"
SETTINGS = CLAUDE_DIR / "settings.json"
SKILLS_DIR = CLAUDE_DIR / "skills"
SKILL_NAME = "scar-authoring"

# The pre-command-anchor script each kind was migrated from. Only these three
# kinds ever had one; the mapping is per-kind so a legacy `precheck` script is
# never mistaken for ownership of a different kind on the same event.
LEGACY_SCRIPT_FOR_KIND = {
    "precheck": "scar-precheck.py",
    "session-notice": "scar-session-notice.py",
    "stop-drafter": "scar-stop-drafter.py",
}
LEGACY_SCRIPTS = tuple(LEGACY_SCRIPT_FOR_KIND.values())
OURS_RE = re.compile(r"(scar[^ ]*) hook (precheck|posttool|session-notice|stop-drafter)"
                     r"|" + "|".join(re.escape(s) for s in LEGACY_SCRIPTS))

HOOKS = [
    {"kind": "precheck", "event": "PreToolUse",
     "matcher": "Edit|Write|MultiEdit|NotebookEdit",
     "timeout": 10, "status": "Checking scars..."},
    {"kind": "precheck-command", "event": "PreToolUse",
     "matcher": "Bash",
     "timeout": 10, "status": "Checking command scars..."},
    {"kind": "posttool", "event": "PostToolUse",
     "matcher": "Edit|Write|MultiEdit|NotebookEdit",
     "timeout": 10, "status": "Checking for violations..."},
    {"kind": "session-notice", "event": "SessionStart",
     "matcher": None, "timeout": 10, "status": "Checking scar conventions..."},
    {"kind": "stop-drafter", "event": "Stop",
     "matcher": None, "timeout": 15, "status": "Checking for abandoned approaches..."},
]


def find_scar() -> str | None:
    # scar 0003: never bind hooks to a venv shim that may disappear.
    venv = os.environ.get("VIRTUAL_ENV")
    if not venv:
        return shutil.which("scar")
    venv_path = Path(venv).resolve()
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep)
            if d and not Path(d).resolve().is_relative_to(venv_path)]
    return shutil.which("scar", path=os.pathsep.join(dirs))


def load_settings() -> dict:
    return json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}


def save_settings(settings: dict, dry: bool) -> None:
    if dry:
        return
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    if SETTINGS.exists():
        backup = SETTINGS.with_name(f"settings.json.scar-backup-{int(time.time())}")
        shutil.copy2(SETTINGS, backup)
        backup_note = f" (backup: {backup.name})"
    else:
        backup_note = ""
    SETTINGS.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(f"  settings.json written{backup_note}")


def is_ours(group: dict) -> bool:
    """Any scar-owned entry, regardless of kind. Used by uninstall, which
    clears every kind off an event at once."""
    return any(OURS_RE.search(h.get("command", ""))
               for h in group.get("hooks", []) if isinstance(h, dict))


def _kind_pattern(kind: str) -> re.Pattern[str]:
    # scar 0016: `precheck` is a string PREFIX of `precheck-command`, so the
    # kind must be matched exactly — the trailing (?!\S) is what stops one
    # kind from claiming the other's entry.
    alternatives = [rf"scar[^ ]* hook {re.escape(kind)}(?!\S)"]
    legacy = LEGACY_SCRIPT_FOR_KIND.get(kind)
    if legacy:
        alternatives.append(re.escape(legacy))
    return re.compile("|".join(alternatives))


_KIND_RE = {spec["kind"]: _kind_pattern(spec["kind"]) for spec in HOOKS}


def owns_kind(group: dict, kind: str) -> bool:
    """Ownership scoped to one hook kind.

    Scar #236: `precheck` and `precheck-command` share the PreToolUse event.
    Stripping by event dropped whichever spec was installed first, leaving no
    pre-edit injection at all while still printing success.
    """
    pattern = _KIND_RE[kind]
    return any(pattern.search(h.get("command", ""))
               for h in group.get("hooks", []) if isinstance(h, dict))


def precheck_installed() -> bool | None:
    """Is the pre-edit `precheck` hook present in the settings file?

    Returns None when that cannot be determined — no settings file, or one we
    cannot parse. Absence of evidence is NOT evidence of a broken install: a
    user may drive scar from another harness entirely, and warning them about
    a file they never had would be noise. #237.
    """
    if not SETTINGS.exists():
        return None
    try:
        hooks_cfg = load_settings().get("hooks", {})
    except (OSError, ValueError):
        return None
    if not isinstance(hooks_cfg, dict):
        return None
    groups = hooks_cfg.get("PreToolUse", [])
    if not isinstance(groups, list):
        return None
    return any(owns_kind(g, "precheck") for g in groups if isinstance(g, dict))


def _entry(spec: dict, scar_path: str) -> dict:
    hook = {"type": "command", "command": f"{scar_path} hook {spec['kind']}",
            "timeout": spec["timeout"], "statusMessage": spec["status"]}
    group = {"hooks": [hook]}
    if spec["matcher"]:
        group["matcher"] = spec["matcher"]
    return group


def _remove_legacy_scripts(dry: bool) -> None:
    for name in LEGACY_SCRIPTS:
        path = HOOKS_DIR / name
        if path.exists():
            print(f"[migrate] remove legacy script {path}")
            if not dry:
                path.unlink()


def install(dry: bool = False) -> int:
    scar_path = find_scar()
    if not scar_path:
        print("scar binary not found on PATH.")
        if os.environ.get("VIRTUAL_ENV"):
            print("Note: an active venv is ignored on purpose — hooks must "
                  "bind to a stable install, not a venv shim (scar 0003).")
        print("Install it first: uv tool install scar-cli")
        return 1
    settings = load_settings()
    hooks_cfg = settings.setdefault("hooks", {})
    changed = False
    for spec in HOOKS:
        kind = spec["kind"]
        groups = hooks_cfg.setdefault(spec["event"], [])
        ours = [g for g in groups if owns_kind(g, kind)]
        desired = _entry(spec, scar_path)
        if ours == [desired]:
            print(f"[{kind}] settings: up-to-date ({spec['event']})")
            continue
        if ours:
            print(f"[{kind}] settings: migrate legacy entry -> scar hook {kind}")
            hooks_cfg[spec["event"]] = [g for g in groups if not owns_kind(g, kind)]
        else:
            print(f"[{kind}] settings: register under {spec['event']}")
        hooks_cfg[spec["event"]].append(desired)
        changed = True
    _remove_legacy_scripts(dry)
    if changed:
        save_settings(settings, dry)
    if not dry:
        missing = _missing_after_write()
        if missing:
            print("install: FAILED — these hooks are not in the written "
                  f"settings: {', '.join(missing)}")
            return 1
    print("install: done" + (" (dry-run, nothing written)" if dry else
          f". All hooks route through {scar_path}."))
    return 0


def _missing_after_write() -> list[str]:
    """Read back what actually landed on disk.

    Scar #236 printed `register` for a hook it then deleted, so the install
    was reported as a success for a month while the tool's core feature was
    off. Success is now asserted against the file, not against intent.
    """
    hooks_cfg = load_settings().get("hooks", {})
    return [spec["kind"] for spec in HOOKS
            if not any(owns_kind(g, spec["kind"])
                       for g in hooks_cfg.get(spec["event"], []))]


def uninstall(dry: bool = False) -> int:
    settings = load_settings()
    hooks_cfg = settings.get("hooks", {})
    changed = False
    for spec in HOOKS:
        groups = hooks_cfg.get(spec["event"], [])
        keep = [g for g in groups if not is_ours(g)]
        if len(keep) != len(groups):
            print(f"[{spec['kind']}] settings: removing from {spec['event']}")
            hooks_cfg[spec["event"]] = keep
            if not keep:
                del hooks_cfg[spec["event"]]
            changed = True
    _remove_legacy_scripts(dry)
    if changed:
        save_settings(settings, dry)
    print("uninstall: done" + (" (dry-run, nothing written)" if dry else
          ". Scars themselves (.scars/ in repos) are untouched."))
    return 0


def status() -> int:
    scar_path = find_scar()
    print(f"scar binary: {scar_path or 'NOT FOUND (uv tool install scar-cli)'}")
    hooks_cfg = load_settings().get("hooks", {})
    for spec in HOOKS:
        ours = [g for g in hooks_cfg.get(spec["event"], [])
                if owns_kind(g, spec["kind"])]
        commands = [h.get("command", "") for g in ours for h in g.get("hooks", [])]
        legacy_script = LEGACY_SCRIPT_FOR_KIND.get(spec["kind"])
        legacy = bool(legacy_script) and any(legacy_script in command
                                             for command in commands)
        state = ("legacy (run install to migrate)" if legacy
                 else "installed" if ours else "not installed")
        print(f"{spec['kind']:16} {spec['event']:13} {state}")
    return 0


def claude_hooks_present() -> bool:
    """Every HOOKS kind owned in settings.json. Kind-exact (scar 0016)."""
    hooks_cfg = load_settings().get("hooks", {})
    return all(any(owns_kind(g, spec["kind"]) for g in hooks_cfg.get(spec["event"], []))
               for spec in HOOKS)


# ---------------------------------------------------------------------------
# git post-commit hook (#117) — the trigger for `scar draft-check`. Separate
# lifecycle from the Claude Code hooks above: this writes into the REPO's
# .git/hooks/, not the user's global ~/.claude/settings.json, and is invoked
# per-repo (`scar hook install --git` from inside the repo), not once
# globally.
# ---------------------------------------------------------------------------

GIT_HOOK_NAME = "post-commit"
GIT_HOOK_MARKER = "draft-check --from-hook"


def _repo_git_dir(repo: Path) -> Path | None:
    """The repo's real .git dir (worktrees, submodules use a `.git` FILE
    pointing elsewhere — `git rev-parse --git-dir` is the only correct way
    to resolve it). None means *repo* is not inside a git repository."""
    proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-dir"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    git_dir = Path(proc.stdout.strip())
    return git_dir if git_dir.is_absolute() else (repo / git_dir)


def _hooks_externally_managed(repo: Path) -> str | None:
    """A short human-readable reason when this repo's hooks are managed by
    something else (husky, lefthook, a custom core.hooksPath) — in which
    case `scar hook install --git` must NOT touch .git/hooks/ (issue #117:
    print manual instructions instead). None means safe to install."""
    proc = subprocess.run(["git", "-C", str(repo), "config", "core.hooksPath"],
                          capture_output=True, text=True)
    configured = proc.stdout.strip()
    if proc.returncode == 0 and configured:
        return f"core.hooksPath is set to '{configured}'"
    if (repo / ".husky").is_dir():
        return "a .husky/ directory was found (husky-managed hooks)"
    for name in ("lefthook.yml", "lefthook.yaml", ".lefthook.yml", ".lefthook.yaml"):
        if (repo / name).is_file():
            return f"{name} was found (lefthook-managed hooks)"
    return None


def git_hook_install(repo: Path, dry: bool = False) -> int:
    scar_path = find_scar()
    if not scar_path:
        print("scar binary not found on PATH.")
        if os.environ.get("VIRTUAL_ENV"):
            print("Note: an active venv is ignored on purpose — hooks must "
                  "bind to a stable install, not a venv shim (scar 0003).")
        print("Install it first: uv tool install scar-cli")
        return 1

    # 2>/dev/null: a stale binary (installed hook, older global scar without
    # draft-check) must not spray usage errors on every commit — nudges ride
    # stdout, breakage stays silent. `|| true`: post-commit advisory, never
    # fail the commit. Found live during #117 smoke testing.
    line = f"{scar_path} draft-check --from-hook 2>/dev/null || true"

    reason = _hooks_externally_managed(repo)
    if reason:
        print(f"[git-hook] skipped: {reason}.")
        print("  Add this line to your managed post-commit hook yourself:")
        print(f"    {line}")
        return 0

    git_dir = _repo_git_dir(repo)
    if git_dir is None:
        print(f"[git-hook] {repo} is not a git repository.")
        return 1

    hooks_dir = git_dir / "hooks"
    hook_path = hooks_dir / GIT_HOOK_NAME

    if hook_path.exists():
        content = hook_path.read_text(encoding="utf-8")
        if GIT_HOOK_MARKER in content:
            print(f"[git-hook] {hook_path}: up-to-date")
            return 0
        print(f"[git-hook] {hook_path}: appending scar draft-check trigger")
        if not dry:
            new_content = content if content.endswith("\n") else content + "\n"
            new_content += line + "\n"
            hook_path.write_text(new_content, encoding="utf-8")
        return 0

    print(f"[git-hook] {hook_path}: create (post-commit)")
    if not dry:
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(f"#!/bin/sh\n{line}\n", encoding="utf-8")
        hook_path.chmod(0o755)
    return 0


def git_hook_uninstall(repo: Path, dry: bool = False) -> int:
    git_dir = _repo_git_dir(repo)
    if git_dir is None:
        print(f"[git-hook] {repo} is not a git repository.")
        return 1

    hook_path = git_dir / "hooks" / GIT_HOOK_NAME
    if not hook_path.exists():
        print(f"[git-hook] {hook_path}: not installed")
        return 0

    content = hook_path.read_text(encoding="utf-8")
    if GIT_HOOK_MARKER not in content:
        print(f"[git-hook] {hook_path}: no scar draft-check line found")
        return 0

    kept = [ln for ln in content.splitlines(keepends=True)
           if GIT_HOOK_MARKER not in ln]
    remaining = [ln for ln in kept if ln.strip() and not ln.strip().startswith("#!")]

    if dry:
        verb = "would remove the file (ours only)" if not remaining else "would remove our line"
        print(f"[git-hook] {hook_path}: {verb}")
        return 0

    if not remaining:
        hook_path.unlink()
        print(f"[git-hook] {hook_path}: removed (the file was ours only)")
    else:
        hook_path.write_text("".join(kept), encoding="utf-8")
        print(f"[git-hook] {hook_path}: removed our line")
    return 0


def git_hook_status(repo: Path) -> int:
    reason = _hooks_externally_managed(repo)
    if reason:
        print(f"git-hook: externally managed ({reason})")
        return 0
    git_dir = _repo_git_dir(repo)
    if git_dir is None:
        print(f"git-hook: {repo} is not a git repository")
        return 0
    hook_path = git_dir / "hooks" / GIT_HOOK_NAME
    if not hook_path.exists():
        print(f"git-hook: not installed ({hook_path})")
        return 0
    content = hook_path.read_text(encoding="utf-8")
    state = "installed" if GIT_HOOK_MARKER in content else "post-commit exists, not ours"
    print(f"git-hook: {state} ({hook_path})")
    return 0


# ---------------------------------------------------------------------------
# Windsurf/Cascade hooks (#197). Third lifecycle in this module, third target:
# Claude Code's hooks live in the USER's ~/.claude/settings.json, the git hook
# lives in the repo's .git/hooks/ (untracked), and this one is COMMITTED —
# .windsurf/hooks.json is workspace-level config a team shares. That makes
# merge-never-overwrite non-negotiable, and it means a teammate on an older
# scar-cli will run whatever command string we write here.
# ---------------------------------------------------------------------------

CASCADE_CONFIG_RELPATH = Path(".windsurf") / "hooks.json"
CASCADE_EVENTS = ("pre_write_code", "pre_run_command", "post_write_code")
CASCADE_MARKER = "cascade-hook"


def cascade_command(scar_path: str) -> str:
    """The shell command wired into every Cascade event.

    `scar cascade-hook` signals a block with its own sentinel code, never 2:
    argparse exits 2 on an unknown subcommand, so a committed hooks.json read
    by an older scar-cli would block every write with a usage message. Mapping
    the sentinel here keeps version skew a silent no-op — Cascade treats any
    code that is neither 0 nor 2 as "action proceeds normally".
    """
    from .cascade import BLOCK_EXIT
    return f"{scar_path} cascade-hook; [ $? -eq {BLOCK_EXIT} ] && exit 2; exit 0"


def _cascade_entry(scar_path: str) -> dict:
    # show_output surfaces the UI-only channel (path-proximity one-liners the
    # model never sees) to the human who can act on them.
    return {"command": cascade_command(scar_path), "show_output": True}


def _cascade_is_ours(entry: object) -> bool:
    return isinstance(entry, dict) and CASCADE_MARKER in str(entry.get("command", ""))


def _load_cascade_config(path: Path) -> dict | None:
    """The repo's existing Cascade config, or {} when absent. None means the
    file exists but does not parse — the team's file, hand-edited; refuse
    rather than overwrite (this one is committed, unlike settings.json which
    we back up and rewrite)."""
    if not path.exists():
        return {}
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return config if isinstance(config, dict) else None


def _save_cascade_config(path: Path, config: dict, dry: bool) -> None:
    if dry:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"  {path} written")


def cascade_install(repo: Path, dry: bool = False) -> int:
    scar_path = find_scar()
    if not scar_path:
        print("scar binary not found on PATH.")
        if os.environ.get("VIRTUAL_ENV"):
            print("Note: an active venv is ignored on purpose — hooks must "
                  "bind to a stable install, not a venv shim (scar 0003).")
        print("Install it first: uv tool install scar-cli")
        return 1

    path = repo / CASCADE_CONFIG_RELPATH
    config = _load_cascade_config(path)
    if config is None:
        print(f"[cascade] {path} exists but is not valid JSON — left untouched. "
              "Fix or remove it, then re-run.")
        return 1

    hooks_cfg = config.setdefault("hooks", {})
    if not isinstance(hooks_cfg, dict):
        print(f"[cascade] {path} has a non-object 'hooks' key — left untouched.")
        return 1

    desired = _cascade_entry(scar_path)
    changed = False
    for event in CASCADE_EVENTS:
        entries = hooks_cfg.get(event)
        entries = entries if isinstance(entries, list) else []
        ours = [e for e in entries if _cascade_is_ours(e)]
        if ours == [desired]:
            print(f"[cascade] {event}: up-to-date")
            hooks_cfg[event] = entries
            continue
        verb = "update" if ours else "register"
        print(f"[cascade] {event}: {verb}")
        # Foreign hooks keep their order and their position relative to each
        # other; ours is appended so a team lint/format hook still runs first.
        hooks_cfg[event] = [e for e in entries if not _cascade_is_ours(e)] + [desired]
        changed = True

    if changed:
        _save_cascade_config(path, config, dry)
    print("cascade install: done" + (" (dry-run, nothing written)" if dry else
          f". All {len(CASCADE_EVENTS)} Cascade hooks route through {scar_path}."))
    print("  Note: Cascade does not load hooks in a Restricted Mode workspace "
          "— there they silently no-op.")
    return 0


def cascade_uninstall(repo: Path, dry: bool = False) -> int:
    path = repo / CASCADE_CONFIG_RELPATH
    config = _load_cascade_config(path)
    if config is None:
        print(f"[cascade] {path} is not valid JSON — left untouched.")
        return 1
    hooks_cfg = config.get("hooks")
    if not isinstance(hooks_cfg, dict):
        print(f"[cascade] {path}: nothing of ours to remove")
        return 0

    changed = False
    for event in CASCADE_EVENTS:
        entries = hooks_cfg.get(event)
        if not isinstance(entries, list):
            continue
        keep = [e for e in entries if not _cascade_is_ours(e)]
        if len(keep) != len(entries):
            print(f"[cascade] {event}: removing")
            changed = True
        if keep:
            hooks_cfg[event] = keep
        else:
            hooks_cfg.pop(event, None)

    if changed:
        _save_cascade_config(path, config, dry)
    print("cascade uninstall: done" + (" (dry-run, nothing written)" if dry else
          ". Scars themselves (.scars/ in repos) are untouched."))
    return 0


def cascade_status(repo: Path) -> int:
    path = repo / CASCADE_CONFIG_RELPATH
    print(f"cascade config: {path}")
    config = _load_cascade_config(path)
    if config is None:
        print("  not valid JSON — install would refuse to touch it")
        return 0
    hooks_cfg = config.get("hooks")
    hooks_cfg = hooks_cfg if isinstance(hooks_cfg, dict) else {}
    for event in CASCADE_EVENTS:
        entries = hooks_cfg.get(event)
        entries = entries if isinstance(entries, list) else []
        state = "installed" if any(_cascade_is_ours(e) for e in entries) else "not installed"
        print(f"{event:17} {state}")
    print("  Note: hooks do not load in a Restricted Mode workspace.")
    return 0


def cascade_hooks_present(repo: Path) -> bool:
    path = repo / CASCADE_CONFIG_RELPATH
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    hooks_cfg = data.get("hooks", {}) if isinstance(data, dict) else {}
    return all(any(_cascade_is_ours(e) for e in (hooks_cfg.get(ev) or []))
               for ev in CASCADE_EVENTS)


# ---------------------------------------------------------------------------
# Codex hooks (~/.codex/hooks.json) — #246. Codex's user-level hooks file is
# the direct analogue of Claude's settings.json and carries the same entry
# shape, so the wire format is reused verbatim. Two things differ and both
# are load-bearing:
#
#   1. The file is SHARED with every other tool the user has wired into
#      Codex. It is merged, never rewritten wholesale, and a parse failure
#      refuses rather than clobbers (as with .windsurf/hooks.json).
#   2. Codex will not run a hook until its definition hash is trusted, and it
#      skips untrusted hooks SILENTLY — no error, no stderr. Verified on
#      codex-cli 0.147.0: a hook config parsed, printed its own timeout
#      warning, and never executed. An install that stops at "written" is
#      therefore reporting a success the user does not yet have, so the
#      trust step is part of the install's own output.
# ---------------------------------------------------------------------------

CODEX_CONFIG_NAME = "hooks.json"

# Codex exposes edits as one `apply_patch` program and commands as `Bash`;
# Edit/Write/MultiEdit are Claude tool names and match nothing here.
CODEX_HOOKS = [
    {"kind": "codex-session-notice", "event": "SessionStart",
     "matcher": None, "timeout": 10, "status": "Checking scar conventions..."},
    {"kind": "codex-pretool", "event": "PreToolUse",
     "matcher": "Bash|apply_patch",
     "timeout": 10, "status": "Checking scars..."},
    {"kind": "codex-posttool", "event": "PostToolUse",
     "matcher": "apply_patch",
     "timeout": 10, "status": "Checking for violations..."},
]

# scar 0016, one file over: `posttool` is a proper prefix of `codex-posttool`,
# so ownership is matched on the exact kind. The trailing (?!\S) is what keeps
# a Claude-runtime entry that happens to share this file from being claimed.
_CODEX_KIND_RE = {
    spec["kind"]: re.compile(rf"scar[^ ]* hook {re.escape(spec['kind'])}(?!\S)")
    for spec in CODEX_HOOKS
}


# A [hooks.state."<key>"] block and the plain `key = value` lines under it,
# stopping at the next table header. requires-python is >=3.10, so tomllib
# (3.11+) is unavailable and the project carries no TOML dependency — this
# reads the one narrow shape Codex writes, nothing more.
_HOOK_STATE_RE = re.compile(
    r'^\[hooks\.state\."([^"]+)"\][^\n]*\n((?:(?!\[)[^\n]*\n?)*)', re.MULTILINE)
def _codex_plugin_ref(path: Path) -> str | None:
    """The `<plugin>@<marketplace>` ref Codex keys hook state by, derived from
    a materialized plugin path .../plugins/cache/<marketplace>/<plugin>/<ver>/
    hooks/hooks.json. None when the path is not that shape."""
    parts = path.parts
    try:
        i = len(parts) - 1 - parts[::-1].index("cache")
    except ValueError:
        return None
    rest = parts[i + 1:]
    if len(rest) < 2:
        return None
    marketplace, plugin = rest[0], rest[1]
    return f"{plugin}@{marketplace}"


def _codex_plugin_hooks_silenced(path: Path) -> bool:
    """Has Codex been told to stop running THIS plugin's Scar hooks?

    #252: #251 detected a second channel by the plugin file merely existing,
    so the warning survived its own remedy and became permanent noise for the
    users who complied.

    Scoped to one plugin's own `<plugin>@<marketplace>:` key prefix — scar
    0016 again. A substring match on `hooks/hooks.json:` reads ANOTHER tool's
    plugin state, and one unrelated enabled plugin then keeps our warning on
    forever. That is exactly how this was first written and shipped.

    Fails TOWARD the warning (#237's rule): a missing, unreadable or
    unrecognisable config means we cannot show the channel is silent, and a
    missed duplicate silently doubles every measurement while a spurious
    warning is only annoying. An omitted `enabled` is Codex's default of
    enabled, so ONLY an explicit `enabled = false` counts as silenced.
    """
    ref = _codex_plugin_ref(path)
    if ref is None:
        return False
    try:
        text = (codex_home() / "config.toml").read_text(encoding="utf-8")
    except OSError:
        return False
    prefix = f"{ref}:"
    seen = False
    for key, body in _HOOK_STATE_RE.findall(text):
        if not key.startswith(prefix):
            continue
        seen = True
        if not re.search(r"^\s*enabled\s*=\s*false\s*$", body, re.MULTILINE):
            return False
    return seen


def codex_plugin_channels() -> list[Path]:
    """Codex plugin hook files that register a Scar handler — a SECOND live
    push channel alongside ~/.codex/hooks.json.

    #250: #246 concluded the plugin could not deliver hooks because the
    materialized cache carried no hooks.json. That cache was merely STALE.
    Once Codex re-materializes it the plugin channel goes live, both files are
    trusted independently, and one apply_patch fires Scar twice — doubling
    every firing-log row and injecting the same scars twice.

    Matched on our own handler names so another tool's plugin in the same
    cache is never mistaken for ours.
    """
    root = codex_home() / "plugins"
    if not root.is_dir():
        return []
    kinds = tuple(spec["kind"] for spec in CODEX_HOOKS)
    found = []
    for path in sorted(root.rglob("hooks.json")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not any(kind in text for kind in kinds):
            continue
        if _codex_plugin_hooks_silenced(path):
            continue
        found.append(path)
    return found


def _warn_duplicate_channels(paths: list[Path], config: Path) -> None:
    if not paths:
        return
    print("  WARNING: Scar is wired into Codex through TWO channels. Both "
          "fire on the same tool call, so every firing is counted twice and "
          "each scar is injected twice (#250):")
    print(f"    1. {config}")
    for i, path in enumerate(paths, start=2):
        print(f"    {i}. {path}  (Codex plugin)")
    print("  Disable one. Either remove the Scar plugin in Codex, or run "
          "`scar hook uninstall --runtime codex` and keep the plugin.")


def codex_home() -> Path:
    """Codex honours $CODEX_HOME. So must we — otherwise the install lands in
    a directory the running agent never reads, and reports success."""
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def codex_config_path() -> Path:
    return codex_home() / CODEX_CONFIG_NAME


def _codex_owns_kind(group: object, kind: str) -> bool:
    if not isinstance(group, dict):
        return False
    pattern = _CODEX_KIND_RE[kind]
    return any(pattern.search(h.get("command", ""))
               for h in group.get("hooks", []) if isinstance(h, dict))


def _codex_is_ours(group: object) -> bool:
    """Any Codex-runtime entry of ours, whatever the kind. Uninstall clears
    every kind off an event at once; it must still leave another tool's
    entries — and our own Claude-runtime kinds — alone."""
    return any(_codex_owns_kind(group, spec["kind"]) for spec in CODEX_HOOKS)


def _load_codex_config(path: Path) -> dict | None:
    """The existing Codex hooks file, or {} when absent. None means it exists
    but does not parse — other tools register here, so refuse rather than
    overwrite work that is not ours."""
    if not path.exists():
        return {}
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return config if isinstance(config, dict) else None


def _save_codex_config(path: Path, config: dict, dry: bool) -> None:
    if dry:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(f"{path.name}.scar-backup-{int(time.time())}")
        shutil.copy2(path, backup)
        print(f"  backup: {backup.name}")
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"  {path} written")


def _codex_missing_after_write(path: Path) -> list[str]:
    """Read back what actually landed. #236 printed `register` for a hook it
    then dropped and called that a success for a month."""
    config = _load_codex_config(path)
    if config is None:
        return [spec["kind"] for spec in CODEX_HOOKS]
    hooks_cfg = config.get("hooks")
    hooks_cfg = hooks_cfg if isinstance(hooks_cfg, dict) else {}
    missing = []
    for spec in CODEX_HOOKS:
        groups = hooks_cfg.get(spec["event"])
        groups = groups if isinstance(groups, list) else []
        if not any(_codex_owns_kind(g, spec["kind"]) for g in groups):
            missing.append(spec["kind"])
    return missing


def codex_install(dry: bool = False) -> int:
    scar_path = find_scar()
    if not scar_path:
        print("scar binary not found on PATH.")
        if os.environ.get("VIRTUAL_ENV"):
            print("Note: an active venv is ignored on purpose — hooks must "
                  "bind to a stable install, not a venv shim (scar 0003).")
        print("Install it first: uv tool install scar-cli")
        return 1

    path = codex_config_path()
    config = _load_codex_config(path)
    if config is None:
        print(f"[codex] {path} exists but is not valid JSON — left untouched. "
              "Fix or remove it, then re-run.")
        return 1

    hooks_cfg = config.setdefault("hooks", {})
    if not isinstance(hooks_cfg, dict):
        print(f"[codex] {path} has a non-object 'hooks' key — left untouched.")
        return 1

    changed = False
    for spec in CODEX_HOOKS:
        kind, event = spec["kind"], spec["event"]
        groups = hooks_cfg.get(event)
        groups = groups if isinstance(groups, list) else []
        ours = [g for g in groups if _codex_owns_kind(g, kind)]
        desired = _entry(spec, scar_path)
        if ours == [desired]:
            print(f"[{kind}] hooks.json: up-to-date ({event})")
            hooks_cfg[event] = groups
            continue
        print(f"[{kind}] hooks.json: "
              f"{'update' if ours else 'register'} under {event}")
        # Foreign entries keep their relative order; ours is appended so a
        # tool that was already there still runs first.
        hooks_cfg[event] = [g for g in groups
                            if not _codex_owns_kind(g, kind)] + [desired]
        changed = True

    if changed:
        _save_codex_config(path, config, dry)
    if not dry:
        missing = _codex_missing_after_write(path)
        if missing:
            print("codex install: FAILED — these hooks are not in the written "
                  f"file: {', '.join(missing)}")
            return 1
    print("codex install: done" + (" (dry-run, nothing written)" if dry else
          f". All {len(CODEX_HOOKS)} Codex hooks route through {scar_path}."))
    print("  NOT ACTIVE YET: Codex runs a hook only once you have trusted its "
          "definition. Open `/hooks` in Codex, review these entries, and trust "
          "them. Until then Codex skips them silently — no error is printed.")
    print("  Re-trust after every scar upgrade that changes a hook definition.")
    _warn_duplicate_channels(codex_plugin_channels(), path)
    return 0


def codex_uninstall(dry: bool = False) -> int:
    path = codex_config_path()
    config = _load_codex_config(path)
    if config is None:
        print(f"[codex] {path} is not valid JSON — left untouched.")
        return 1
    hooks_cfg = config.get("hooks")
    if not isinstance(hooks_cfg, dict):
        print(f"[codex] {path}: nothing of ours to remove")
        return 0

    changed = False
    for spec in CODEX_HOOKS:
        event = spec["event"]
        groups = hooks_cfg.get(event)
        if not isinstance(groups, list):
            continue
        keep = [g for g in groups if not _codex_is_ours(g)]
        if len(keep) != len(groups):
            print(f"[{spec['kind']}] hooks.json: removing from {event}")
            changed = True
        if keep:
            hooks_cfg[event] = keep
        else:
            hooks_cfg.pop(event, None)

    if changed:
        _save_codex_config(path, config, dry)
    print("codex uninstall: done" + (" (dry-run, nothing written)" if dry else
          ". Scars themselves (.scars/ in repos) are untouched."))
    print("  Codex keeps its own trust record for hooks it has seen; removing "
          "them here is enough to stop them running.")
    return 0


def codex_status() -> int:
    scar_path = find_scar()
    path = codex_config_path()
    print(f"scar binary: {scar_path or 'NOT FOUND (uv tool install scar-cli)'}")
    print(f"codex config: {path}")
    config = _load_codex_config(path)
    if config is None:
        print("  not valid JSON — install would refuse to touch it")
        return 0
    hooks_cfg = config.get("hooks")
    hooks_cfg = hooks_cfg if isinstance(hooks_cfg, dict) else {}
    for spec in CODEX_HOOKS:
        groups = hooks_cfg.get(spec["event"])
        groups = groups if isinstance(groups, list) else []
        state = ("installed" if any(_codex_owns_kind(g, spec["kind"])
                                    for g in groups) else "not installed")
        print(f"{spec['kind']:22} {spec['event']:13} {state}")
    print("  `installed` means present in the file. Codex additionally "
          "requires the definition to be trusted in `/hooks` before it runs; "
          "an untrusted hook is skipped silently.")
    _warn_duplicate_channels(codex_plugin_channels(), path)
    return 0


def codex_hooks_present() -> bool:
    path = codex_config_path()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    hooks_cfg = data.get("hooks", {}) if isinstance(data, dict) else {}
    return all(any(_codex_owns_kind(g, spec["kind"]) for g in hooks_cfg.get(spec["event"], []))
               for spec in CODEX_HOOKS)


def _skill_source() -> Path:
    from importlib.resources import files
    return Path(str(files("scar").joinpath("skills") / SKILL_NAME))


def skill_install(dry: bool = False) -> int:
    src = _skill_source()
    if not src.is_dir():
        print(f"skill source not found: {src}")
        return 1
    dest = SKILLS_DIR / SKILL_NAME
    print(f"[skill] install {SKILL_NAME} -> {dest}")
    if dry:
        print("install: done (dry-run, nothing written)")
        return 0
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    print("install: done.")
    return 0


def skill_uninstall(dry: bool = False) -> int:
    dest = SKILLS_DIR / SKILL_NAME
    if not dest.exists():
        print(f"[skill] {SKILL_NAME}: not installed")
        return 0
    print(f"[skill] remove {dest}")
    if not dry:
        shutil.rmtree(dest)
    print("uninstall: done" + (" (dry-run, nothing written)" if dry else "."))
    return 0


def skill_status() -> int:
    dest = SKILLS_DIR / SKILL_NAME
    print(f"skill {SKILL_NAME}: {'installed' if dest.exists() else 'not installed'} ({dest})")
    return 0


def skill_present() -> bool:
    return (SKILLS_DIR / SKILL_NAME).exists()
