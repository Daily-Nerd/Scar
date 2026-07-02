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

LEGACY_SCRIPTS = ("scar-precheck.py", "scar-session-notice.py", "scar-stop-drafter.py")
OURS_RE = re.compile(r"(scar[^ ]*) hook (precheck|session-notice|stop-drafter)"
                     r"|" + "|".join(re.escape(s) for s in LEGACY_SCRIPTS))

HOOKS = [
    {"kind": "precheck", "event": "PreToolUse",
     "matcher": "Edit|Write|MultiEdit|NotebookEdit",
     "timeout": 10, "status": "Checking scars..."},
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
    return any(OURS_RE.search(h.get("command", ""))
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
        groups = hooks_cfg.setdefault(spec["event"], [])
        ours = [g for g in groups if is_ours(g)]
        desired = _entry(spec, scar_path)
        if ours == [desired]:
            print(f"[{spec['kind']}] settings: up-to-date ({spec['event']})")
            continue
        if ours:
            print(f"[{spec['kind']}] settings: migrate legacy entry -> scar hook {spec['kind']}")
            hooks_cfg[spec["event"]] = [g for g in groups if not is_ours(g)]
        else:
            print(f"[{spec['kind']}] settings: register under {spec['event']}")
        hooks_cfg[spec["event"]].append(desired)
        changed = True
    _remove_legacy_scripts(dry)
    if changed:
        save_settings(settings, dry)
    print("install: done" + (" (dry-run, nothing written)" if dry else
          f". All hooks route through {scar_path}."))
    return 0


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
        ours = [g for g in hooks_cfg.get(spec["event"], []) if is_ours(g)]
        commands = [h.get("command", "") for g in ours for h in g.get("hooks", [])]
        legacy = any(any(script in command for script in LEGACY_SCRIPTS)
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
