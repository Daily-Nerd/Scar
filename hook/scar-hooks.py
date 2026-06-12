#!/usr/bin/env python3
"""SCAR hook lifecycle manager: install, uninstall, status.

Registers the three SCAR hooks in ~/.claude/settings.json as `scar hook
<kind>` commands (absolute path to the scar binary — hook environments do not
guarantee PATH). Migrates installs from the legacy standalone-script era:
their settings entries and ~/.claude/hooks/ copies are removed on install.

Run BY THE USER, never by an agent (see scar 0002): consent is the execution.

Usage:
  python3 scar-hooks.py install   [--dry-run]
  python3 scar-hooks.py uninstall [--dry-run]
  python3 scar-hooks.py status
"""

import json
import os
import re
import shutil
import sys
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
    # scar 0003: never bind hooks to a venv shim — it dies with the venv.
    # With $VIRTUAL_ENV active its bin/ shadows PATH, so search without it.
    venv = os.environ.get("VIRTUAL_ENV")
    if not venv:
        return shutil.which("scar")
    venv = Path(venv).resolve()
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep)
            if d and not Path(d).resolve().is_relative_to(venv)]
    return shutil.which("scar", path=os.pathsep.join(dirs))


def load_settings():
    return json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}


def save_settings(settings, dry):
    if dry:
        return
    backup = SETTINGS.with_name(f"settings.json.scar-backup-{int(time.time())}")
    shutil.copy2(SETTINGS, backup)
    SETTINGS.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(f"  settings.json written (backup: {backup.name})")


def is_ours(group) -> bool:
    return any(OURS_RE.search(h.get("command", ""))
               for h in group.get("hooks", []) if isinstance(h, dict))


def _entry(spec, scar_path):
    hook = {"type": "command", "command": f"{scar_path} hook {spec['kind']}",
            "timeout": spec["timeout"], "statusMessage": spec["status"]}
    group = {"hooks": [hook]}
    if spec["matcher"]:
        group["matcher"] = spec["matcher"]
    return group


def _remove_legacy_scripts(dry):
    for name in LEGACY_SCRIPTS:
        f = HOOKS_DIR / name
        if f.exists():
            print(f"[migrate] remove legacy script {f}")
            if not dry:
                f.unlink()


def install(dry):
    scar_path = find_scar()
    if not scar_path:
        print("scar binary not found on PATH.")
        if os.environ.get("VIRTUAL_ENV"):
            print("Note: an active venv is ignored on purpose — hooks must "
                  "bind to a stable install, not a venv shim (scar 0003).")
        print("Install it first:  cd <scar-repo> && uv tool install -e .")
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


def uninstall(dry):
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


def status():
    scar_path = find_scar()
    print(f"scar binary: {scar_path or 'NOT FOUND (uv tool install -e .)'}")
    hooks_cfg = load_settings().get("hooks", {})
    for spec in HOOKS:
        ours = [g for g in hooks_cfg.get(spec["event"], []) if is_ours(g)]
        cmds = [h.get("command", "") for g in ours for h in g.get("hooks", [])]
        legacy = any(any(s in c for s in LEGACY_SCRIPTS) for c in cmds)
        state = ("legacy (run install to migrate)" if legacy
                 else "installed" if ours else "not installed")
        print(f"{spec['kind']:16} {spec['event']:13} {state}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    dry = "--dry-run" in args
    cmd = next((a for a in args if not a.startswith("-")), "status")
    sys.exit({"install": lambda: install(dry),
              "uninstall": lambda: uninstall(dry),
              "status": status}.get(cmd, status)())
