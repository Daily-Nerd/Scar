#!/usr/bin/env python3
"""SCAR Stop hook — drafts deadend candidates from abandoned approaches.

At session end, scans the transcript for abandonment signals. When they clear
a conservative threshold, blocks the stop ONCE and instructs the agent to
either (a) write a candidate scar to .scars/candidates/ or (b) log a false
positive to .scars/candidates/fp-log.txt. The fp-log is the gate-0.4
measurement instrument — false triggers are self-reporting.

Safety: exits silently when stop_hook_active is set (we already blocked once),
when a per-session marker exists, when the repo has no .scars/, or when
signals don't clear the threshold. Never blocks twice.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

STATE_DIR = Path.home() / ".claude" / "scar-state"

REVERT_RE = re.compile(
    r"revert(ing|ed)?\b|roll(ing|ed)? back|undo(ing)? (the|that|my)|"
    r"abandon(ing|ed)? (the|this|that)|scrap(ping)? (the|this|that)|"
    r"back to the (original|previous)", re.IGNORECASE)
USER_NEG_RE = re.compile(
    r"didn'?t work|doesn'?t work|still (broken|failing|not working)|"
    r"that broke|go back|revert|undo that|no funciona|sigue (roto|fallando)|"
    r"volv[ée] al?", re.IGNORECASE)


def find_scars_dir(start: Path):
    for d in [start, *start.parents]:
        if (d / ".scars").is_dir():
            return d / ".scars"
        if (d / ".git").exists():
            return None
    return None


def analyze(transcript_path):
    revert_hits = user_neg = errors = 0
    edits_per_file = {}
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            lines = fh.readlines()[-4000:]
    except OSError:
        return None
    for raw in lines:
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        etype = entry.get("type", "")
        msg = entry.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            blocks = content
        else:
            blocks = []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                if etype == "assistant" and REVERT_RE.search(b.get("text", "")):
                    revert_hits += 1
                if etype == "user" and USER_NEG_RE.search(b.get("text", "")):
                    user_neg += 1
            elif b.get("type") == "tool_use" and b.get("name") in ("Edit", "Write", "MultiEdit"):
                fp = (b.get("input") or {}).get("file_path", "")
                if fp:
                    edits_per_file[fp] = edits_per_file.get(fp, 0) + 1
            elif b.get("type") == "tool_result" and b.get("is_error"):
                errors += 1
    thrash = max(edits_per_file.values(), default=0)
    signals = {
        "revert_language": revert_hits,
        "user_corrections": user_neg,
        "tool_errors": errors,
        "max_edits_one_file": thrash,
    }
    triggered = (revert_hits >= 1) or (user_neg >= 2) or (errors >= 5 and thrash >= 4)
    return signals if triggered else None


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return
    if payload.get("stop_hook_active"):
        return  # we already blocked once this stop-cycle
    session = payload.get("session_id", "unknown")
    marker = STATE_DIR / f"drafted-{session}"
    if marker.exists():
        return
    cwd = Path(payload.get("cwd") or os.getcwd())
    scars_dir = find_scars_dir(cwd)
    if not scars_dir:
        return
    transcript = payload.get("transcript_path")
    if not transcript:
        return
    signals = analyze(transcript)
    if not signals:
        return

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    marker.touch()
    with open(STATE_DIR / "drafter-log.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "repo": str(scars_dir.parent), "session": session,
            "signals": signals,
        }) + "\n")

    candidates = scars_dir / "candidates"
    reason = (
        "SCAR auto-authorship check: this session shows abandonment signals "
        f"({', '.join(f'{k}={v}' for k, v in signals.items() if v)}). "
        "Before finishing: review the session. "
        f"(1) If an approach was genuinely tried and abandoned, write a short "
        f"candidate scar (<=15 lines, deadend/fence/landmine, with the why and "
        f"any evidence) to {candidates}/<slug>.md — it stays a candidate until "
        f"a human reviews it. "
        f"(2) If nothing was actually abandoned (false trigger), append one "
        f"line — date + one-phrase reason — to {candidates}/fp-log.txt. "
        "Then finish normally. Do exactly one of the two; do not ask the user."
    )
    print(json.dumps({"decision": "block", "reason": reason}))


if __name__ == "__main__":
    main()
