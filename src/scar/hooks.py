"""Claude Code hook handlers — harness payload on stdin, hook JSON on stdout.

One library code path replaces three standalone scripts (which drifted within
two days of birth — gate 0.4 findings). Contract per handler: silent no-op on
any problem; a hook must NEVER fail or delay the user's action.

State (drafter markers, firing log) lives in ~/.claude/scar-state/, overridable
via SCAR_STATE_DIR for tests.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from .match import rank_for_edit
from .store import ScarStore

MAX_BODY_CHARS = 700

REVERT_RE = re.compile(
    r"revert(ing|ed)?\b|roll(ing|ed)? back|undo(ing)? (the|that|my)|"
    r"abandon(ing|ed)? (the|this|that|this approach)|scrap(ping)? (the|this|that)|"
    r"back to the (original|previous)", re.IGNORECASE)
USER_NEG_RE = re.compile(
    r"didn'?t work|doesn'?t work|still (broken|failing|not working)|"
    r"that broke|go back|revert|undo that|no funciona|sigue (roto|fallando)|"
    r"volv[ée] al?", re.IGNORECASE)


def _state_dir() -> Path:
    return Path(os.environ.get("SCAR_STATE_DIR",
                               str(Path.home() / ".claude" / "scar-state")))


def _read_payload() -> dict | None:
    """Hook payload from stdin; None means 'tty — hint printed, do nothing'."""
    if sys.stdin.isatty():
        # interactive invocation: never hang waiting for a payload that
        # only a hook harness would pipe in, and never mix the human hint
        # with machine JSON (both found live)
        print("scar hook expects a hook payload on stdin (it is run by the "
              "agent harness, not by hand). Try: echo '{}' | scar hook <kind>")
        return None
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _emit(event: str, context: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event, "additionalContext": context}}))


def precheck() -> int:
    payload = _read_payload()
    if payload is None:
        return 0
    tool_input = payload.get("tool_input", {})
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not target:
        return 0
    store = ScarStore.discover(Path(target))
    if store is None:
        return 0
    new_content = " ".join(str(tool_input.get(k, ""))
                           for k in ("content", "new_string", "new_source"))
    hits = rank_for_edit(store, Path(target), new_content)
    broken = store.broken()
    parts = []
    if hits:
        blocks = [f"[{'challenged ' if s.status == 'challenged' else ''}{s.type} "
                  f"#{s.id} | severity: {s.severity} | confidence: "
                  f"{s.confidence}] {s.title}\n{s.body[:MAX_BODY_CHARS]}" for s in hits]
        parts.append(
            "SCAR pre-edit check — negative knowledge anchored to code you are "
            f"about to modify ({len(hits)} match(es)). Honor these unless the "
            "user explicitly overrides; full records in .scars/.\n\n" + "\n\n".join(blocks))
    if broken:
        parts.append(
            f"SCAR warning: {len(broken)} scar file(s) unparseable and can NEVER "
            f"fire: {', '.join(b.name for b in broken)}. Their knowledge is "
            f"silently dead. Fix the frontmatter (copy {store.scars_dir}/template.md).")
    if parts:
        _emit("PreToolUse", "\n\n".join(parts))
    return 0


def session_notice() -> int:
    payload = _read_payload()
    if payload is None:
        return 0
    cwd = Path(payload.get("cwd") or os.getcwd())
    store = ScarStore.discover(cwd)
    if store is None:
        return 0
    active, broken, cands = store.active(), store.broken(), store.candidates()
    state = (f"{len(active)} active scar(s)" if active
             else "0 scars yet — the convention is live, be the first to record one")
    pending = f", {len(cands)} candidate(s) pending review" if cands else ""
    warn = (f" WARNING: {len(broken)} unparseable scar file(s) that can never "
            f"fire: {', '.join(b.name for b in broken)} — fix their frontmatter."
            if broken else "")
    _emit("SessionStart", (
        f"SCAR: this repository records negative knowledge in {store.scars_dir} "
        f"({state}{pending}).{warn} Relevant scars are injected automatically "
        "before you edit anchored code — honor them unless the user overrides. "
        "Reciprocal duty: when you abandon an approach (deadend), keep "
        "intentional-looking weirdness (fence), or discover non-obvious coupling "
        f"(landmine), record it. Contract: COPY {store.scars_dir}/template.md "
        "(YAML frontmatter is mandatory — scars without it never fire), write to "
        f"{store.scars_dir}/candidates/<slug>.md with status: candidate, and "
        "never write directly into .scars/ — only a human reviewer promotes."))
    return 0


def _analyze_transcript(path: str) -> dict | None:
    revert_hits = user_neg = errors = 0
    edits_per_file: dict[str, int] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()[-4000:]
    except OSError:
        return None
    for raw in lines:
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        etype = entry.get("type", "")
        content = (entry.get("message") or {}).get("content")
        blocks = ([{"type": "text", "text": content}] if isinstance(content, str)
                  else content if isinstance(content, list) else [])
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
    signals = {"revert_language": revert_hits, "user_corrections": user_neg,
               "tool_errors": errors, "max_edits_one_file": thrash}
    # Trigger on assistant revert/abandon language only. Field data (gate 0.4,
    # 6 firings): revert_language >= 1 was present in both true positives and
    # absent in all four false positives; the user_corrections and
    # tool_errors+thrash paths went 0/4 (normal debugging, policy denials).
    # The other signals stay in the log so future FN evidence can re-add a path.
    triggered = revert_hits >= 1
    return signals if triggered else None


def stop_drafter() -> int:
    payload = _read_payload()
    if payload is None or payload.get("stop_hook_active"):
        return 0
    session = payload.get("session_id", "unknown")
    state = _state_dir()
    marker = state / f"drafted-{session}"
    if marker.exists():
        return 0
    store = ScarStore.discover(Path(payload.get("cwd") or os.getcwd()))
    if store is None:
        return 0
    transcript = payload.get("transcript_path")
    if not transcript:
        return 0
    signals = _analyze_transcript(transcript)
    if not signals:
        return 0

    state.mkdir(parents=True, exist_ok=True)
    marker.touch()
    with open(state / "drafter-log.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                             "repo": str(store.root), "session": session,
                             "signals": signals}) + "\n")
    candidates = store.scars_dir / "candidates"
    print(json.dumps({"decision": "block", "reason": (
        "SCAR auto-authorship check: this session shows abandonment signals "
        f"({', '.join(f'{k}={v}' for k, v in signals.items() if v)}). "
        "Before finishing: review the session. "
        f"(1) If an approach was genuinely tried and abandoned, write a short "
        f"candidate scar (<=15 lines) to {candidates}/<slug>.md — COPY the "
        f"format from {store.scars_dir}/template.md (YAML frontmatter "
        "mandatory, status: candidate); it stays a candidate until a human "
        "reviews it. (2) If nothing was actually abandoned (false trigger), "
        f"append one line — date + one-phrase reason — to "
        f"{candidates}/fp-log.txt. Then finish normally. Do exactly one of "
        "the two; do not ask the user.")}))
    return 0


HANDLERS = {"precheck": precheck, "session-notice": session_notice,
            "stop-drafter": stop_drafter}
