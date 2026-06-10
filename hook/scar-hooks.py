#!/usr/bin/env python3
"""SCAR hook lifecycle manager: install, uninstall, status.

Manages the three SCAR hooks in ~/.claude/ (Claude Code):
  - scar-precheck.py        PreToolUse   inject scars before edits
  - scar-session-notice.py  SessionStart announce convention + counts
  - scar-stop-drafter.py    Stop         draft deadend candidates

Idempotent: install skips/updates existing entries; uninstall removes only
SCAR-owned entries (matched by script filename in the command). settings.json
is backed up before every mutation.

Usage:
  python3 scar-hooks.py install   [--dry-run]
  python3 scar-hooks.py uninstall [--dry-run]
  python3 scar-hooks.py status
"""

import json
import shutil
import sys
import time
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = Path.home() / ".claude"
HOOKS_DIR = CLAUDE_DIR / "hooks"
SETTINGS = CLAUDE_DIR / "settings.json"

HOOKS = [
    {
        "script": "scar-precheck.py",
        "event": "PreToolUse",
        "entry": {
            "matcher": "Edit|Write|MultiEdit|NotebookEdit",
            "hooks": [{
                "type": "command",
                "command": "python3 ~/.claude/hooks/scar-precheck.py",
                "timeout": 10,
                "statusMessage": "Checking scars...",
            }],
        },
    },
    {
        "script": "scar-session-notice.py",
        "event": "SessionStart",
        "entry": {
            "hooks": [{
                "type": "command",
                "command": "python3 ~/.claude/hooks/scar-session-notice.py",
                "timeout": 10,
                "statusMessage": "Checking scar conventions...",
            }],
        },
    },
    {
        "script": "scar-stop-drafter.py",
        "event": "Stop",
        "entry": {
            "hooks": [{
                "type": "command",
                "command": "python3 ~/.claude/hooks/scar-stop-drafter.py",
                "timeout": 15,
                "statusMessage": "Checking for abandoned approaches...",
            }],
        },
    },
]


def load_settings():
    if not SETTINGS.exists():
        return {}
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


def save_settings(settings, dry):
    if dry:
        return
    backup = SETTINGS.with_name(f"settings.json.scar-backup-{int(time.time())}")
    shutil.copy2(SETTINGS, backup)
    SETTINGS.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(f"  settings.json written (backup: {backup.name})")


def is_ours(group, script):
    return any(script in h.get("command", "")
               for h in group.get("hooks", []) if isinstance(h, dict))


def install(dry):
    settings = load_settings()
    hooks_cfg = settings.setdefault("hooks", {})
    changed = False
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    for spec in HOOKS:
        src, dst = SRC_DIR / spec["script"], HOOKS_DIR / spec["script"]
        action = "update" if dst.exists() else "copy"
        same = dst.exists() and src.read_bytes() == dst.read_bytes()
        print(f"[{spec['script']}] script: {'up-to-date' if same else action}")
        if not same and not dry:
            shutil.copy2(src, dst)
            dst.chmod(0o755)
        groups = hooks_cfg.setdefault(spec["event"], [])
        if any(is_ours(g, spec["script"]) for g in groups):
            print(f"[{spec['script']}] settings: already registered ({spec['event']})")
        else:
            print(f"[{spec['script']}] settings: register under {spec['event']}")
            groups.append(spec["entry"])
            changed = True
    if changed:
        save_settings(settings, dry)
    print("install: done" + (" (dry-run, nothing written)" if dry else
          ". Hooks reload automatically; open /hooks to verify."))


def uninstall(dry):
    settings = load_settings()
    hooks_cfg = settings.get("hooks", {})
    changed = False
    for spec in HOOKS:
        groups = hooks_cfg.get(spec["event"], [])
        keep = [g for g in groups if not is_ours(g, spec["script"])]
        if len(keep) != len(groups):
            print(f"[{spec['script']}] settings: removing from {spec['event']}")
            hooks_cfg[spec["event"]] = keep
            if not keep:
                del hooks_cfg[spec["event"]]
            changed = True
        dst = HOOKS_DIR / spec["script"]
        if dst.exists():
            print(f"[{spec['script']}] script: remove {dst}")
            if not dry:
                dst.unlink()
    if changed:
        save_settings(settings, dry)
    print("uninstall: done" + (" (dry-run, nothing written)" if dry else
          ". Scars themselves (.scars/ in repos) are untouched."))


def status():
    settings = load_settings()
    hooks_cfg = settings.get("hooks", {})
    for spec in HOOKS:
        script_ok = (HOOKS_DIR / spec["script"]).exists()
        reg = any(is_ours(g, spec["script"])
                  for g in hooks_cfg.get(spec["event"], []))
        src_same = script_ok and \
            (SRC_DIR / spec["script"]).read_bytes() == (HOOKS_DIR / spec["script"]).read_bytes()
        state = ("installed" if script_ok and reg else
                 "partial" if script_ok or reg else "not installed")
        extra = "" if not script_ok else (" (current)" if src_same else " (outdated copy)")
        print(f"{spec['script']:28} {spec['event']:13} {state}{extra}")


if __name__ == "__main__":
    args = sys.argv[1:]
    dry = "--dry-run" in args
    cmd = next((a for a in args if not a.startswith("-")), "status")
    {"install": lambda: install(dry),
     "uninstall": lambda: uninstall(dry),
     "status": status}.get(cmd, status)()
