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
