"""Claude Code hook lifecycle management.

The user invokes these commands explicitly. SCAR never installs global hooks
as a side effect of package installation, ``scar init``, or an agent action.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
HOOKS_DIR = CLAUDE_DIR / "hooks"
SETTINGS = CLAUDE_DIR / "settings.json"

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
