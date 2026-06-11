#!/usr/bin/env python3
"""SCAR PreToolUse hook — injects relevant scars before an agent edits a file.

Reads the Claude Code hook payload from stdin, finds the repo's .scars/
directory (walking up from the target file), matches anchors, and emits the
top-3 scars as additionalContext. Silent no-op (exit 0, no output) when there
is no .scars/ directory or nothing matches.

Uses plain python3 + stdlib only: this runs on every Edit/Write, so the
latency budget (<150ms) rules out uv/venv startup and third-party YAML.

Install: copy to ~/.claude/hooks/ and register a PreToolUse hook for
Edit|Write|MultiEdit|NotebookEdit. Canonical source: fabcap/hook/.
"""

import json
import re
import sys
from pathlib import Path

MAX_SCARS = 3
MAX_BODY_CHARS = 700  # ~120 words
SEVERITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def find_scars_dir(start: Path):
    cur = start if start.is_dir() else start.parent
    for d in [cur, *cur.parents]:
        scars = d / ".scars"
        if scars.is_dir():
            return scars, d
        if (d / ".git").exists():
            return None, None  # repo root reached without .scars/
    return None, None


SKIP_NAMES = {"readme.md", "template.md"}


def parse_scar(path: Path):
    """Returns a scar dict, the string 'unparseable', or None (inactive/skip)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return "unparseable"  # knowledge exists but can never fire — must be loud
    front, body = m.groups()

    def field(name, default=""):
        fm = re.search(rf"^{name}:\s*(.+)$", front, re.MULTILINE)
        return fm.group(1).strip() if fm else default

    if field("status", "active") != "active":
        return None
    anchors_paths = re.findall(r"^\s*-\s*path:\s*(.+)$", front, re.MULTILINE)
    anchors_patterns = re.findall(r"^\s*-\s*pattern:\s*\"?([^\"\n]+)\"?\s*$", front, re.MULTILINE)
    try:
        confidence = float(field("confidence", "0.5"))
    except ValueError:
        confidence = 0.5
    return {
        "file": path.name,
        "id": field("id", "?"),
        "type": field("type", "fence"),
        "title": field("title", path.stem),
        "severity": field("severity", "medium"),
        "confidence": confidence,
        "paths": [p.strip() for p in anchors_paths],
        "patterns": anchors_patterns,
        "body": body.strip()[:MAX_BODY_CHARS],
    }


def match(scar, rel_path: str, new_content: str):
    score = 0.0
    for p in scar["paths"]:
        if rel_path.startswith(p.rstrip("/")) or rel_path.startswith(p):
            score = max(score, 2.0)  # path anchors are specific
    for pat in scar["patterns"]:
        try:
            rx = re.compile(pat, re.IGNORECASE)
        except re.error:
            continue
        if rx.search(rel_path):
            score = max(score, 1.5)
        if new_content and rx.search(new_content):
            score = max(score, 2.5)  # approach reappearing in new code: strongest signal
    return score


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return
    tool_input = payload.get("tool_input", {})
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not target:
        return
    target = Path(target)
    scars_dir, root = find_scars_dir(target)
    if not scars_dir:
        return
    try:
        rel_path = str(target.relative_to(root))
    except ValueError:
        return
    new_content = " ".join(
        str(tool_input.get(k, ""))
        for k in ("content", "new_string", "new_source")
    )

    hits, broken = [], []
    for f in sorted(scars_dir.glob("*.md")):
        if f.name.lower() in SKIP_NAMES or f.name.startswith("_"):
            continue
        scar = parse_scar(f)
        if scar == "unparseable":
            broken.append(f.name)
            continue
        if not scar:
            continue
        m = match(scar, rel_path, new_content)
        if m > 0:
            rank = m * SEVERITY_WEIGHT.get(scar["severity"], 2) * scar["confidence"]
            hits.append((rank, scar))
    if not hits and not broken:
        return

    hits.sort(key=lambda h: -h[0])
    blocks = []
    for _, s in hits[:MAX_SCARS]:
        blocks.append(
            f"[{s['type']} #{s['id']} | severity: {s['severity']} | "
            f"confidence: {s['confidence']}] {s['title']} ({s['file']})\n{s['body']}"
        )
    parts = []
    if hits:
        parts.append(
            "SCAR pre-edit check — this repository records negative knowledge "
            f"anchored to code you are about to modify ({len(hits)} match(es), "
            f"top {min(len(hits), MAX_SCARS)} shown). Honor these unless the user "
            "explicitly overrides them; full records in .scars/.\n\n"
            + "\n\n".join(blocks)
        )
    if broken:
        parts.append(
            f"SCAR warning: {len(broken)} scar file(s) are unparseable and can "
            f"NEVER fire: {', '.join(broken)}. Their knowledge is silently dead. "
            f"Fix the YAML frontmatter (copy {scars_dir}/template.md) or tell the user."
        )
    context = "\n\n".join(parts)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": context,
        }
    }))


if __name__ == "__main__":
    main()
