#!/usr/bin/env python3
"""SCAR SessionStart hook — announces the scar convention when a repo uses it.

If the session's working directory (or an ancestor) contains .scars/, injects
a one-line reminder with live counts so any agent — even one that has never
seen SCAR — learns that (a) scars will fire on edits and (b) it is expected
to author candidates when it abandons an approach or finds a fence.

Silent no-op when the repo has no .scars/ directory.
"""

import json
import os
import re
import sys
from pathlib import Path


def find_scars_dir(start: Path):
    for d in [start, *start.parents]:
        scars = d / ".scars"
        if scars.is_dir():
            return scars
        if (d / ".git").exists():
            return None
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}
    cwd = Path(payload.get("cwd") or os.getcwd())
    scars_dir = find_scars_dir(cwd)
    if not scars_dir:
        return

    active, broken = 0, []
    for f in scars_dir.glob("*.md"):
        if f.name.lower() in ("readme.md", "template.md") or f.name.startswith("_"):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if not text.startswith("---\n"):
            broken.append(f.name)
            continue
        m = re.search(r"^status:\s*(\S+)", text, re.MULTILINE)
        if not m or m.group(1) == "active":
            active += 1
    candidates = len(list((scars_dir / "candidates").glob("*.md"))) \
        if (scars_dir / "candidates").is_dir() else 0

    state = (f"{active} active scar(s)" if active
             else "0 scars yet — the convention is live, be the first to record one")
    pending = f", {candidates} candidate(s) pending review" if candidates else ""
    warn = (f" WARNING: {len(broken)} unparseable scar file(s) that can never "
            f"fire: {', '.join(broken)} — fix their frontmatter." if broken else "")
    context = (
        f"SCAR: this repository records negative knowledge in {scars_dir} "
        f"({state}{pending}).{warn} Relevant scars are injected automatically "
        "before you edit anchored code — honor them unless the user overrides. "
        "Reciprocal duty: when you abandon an approach (deadend), keep "
        "intentional-looking weirdness (fence), or discover non-obvious "
        "coupling (landmine), record it. Contract: COPY "
        f"{scars_dir}/template.md (YAML frontmatter is mandatory — scars "
        "without it never fire), write to "
        f"{scars_dir}/candidates/<slug>.md with status: candidate, and never "
        "write directly into .scars/ — only a human reviewer promotes."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))


if __name__ == "__main__":
    main()
