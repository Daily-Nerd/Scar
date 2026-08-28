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

from .match import find_violations, has_content_signal, rank_matches_for_edit
from .render import injection_context
from .store import ScarStore

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


def firing_log_path() -> Path:
    """Path to the firing-observability log (#106) — same state dir/override
    (SCAR_STATE_DIR) as the drafter markers, so there is one knob for all
    hook state, not two."""
    return _state_dir() / "firing-log.jsonl"


COOLDOWN_SECONDS = 4 * 3600  # fixed constant — deliberately not a config knob


def _recently_fired(repo: str, target: str) -> set[int]:
    """Scar ids shown FULL-BODY for this repo+target within the cooldown.
    One-liner (demoted) showings don't count — a later content-signal match
    still deserves the full body. Best-effort: any failure returns empty set
    (module contract: never fail or delay the edit)."""
    try:
        # 200-line tail cap is LINE-COUNT-based, not time-based: on a busy log
        # a recent full-body showing can scroll out past 200 lines before the
        # cooldown window elapses, so the scar re-renders full body early.
        # Under-collapsing fails safe (over-inform), so this is not fixed.
        lines = firing_log_path().read_text(encoding="utf-8").splitlines()[-200:]
    except Exception:
        return set()
    cutoff = time.time() - COOLDOWN_SECONDS
    recent: set[int] = set()
    for line in lines:
        try:
            rec = json.loads(line)
            if rec.get("repo") != repo or rec.get("target") != target:
                continue
            ts = time.mktime(time.strptime(rec["ts"], "%Y-%m-%dT%H:%M:%S"))
            if ts >= cutoff:
                recent.update(set(rec.get("scar_ids", []))
                              - set(rec.get("demoted_ids", [])))
        except Exception:
            continue
    return recent


_TTY_HINT = ("scar hook expects a hook payload on stdin (it is run by the "
             "agent harness, not by hand). Try: echo '{}' | scar hook <kind>")


def _read_payload(hint: str = _TTY_HINT) -> dict | None:
    """Hook payload from stdin; None means 'tty — hint printed, do nothing'."""
    if sys.stdin.isatty():
        # interactive invocation: never hang waiting for a payload that
        # only a hook harness would pipe in, and never mix the human hint
        # with machine JSON (both found live)
        print(hint)
        return None
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _extract_edit_content(tool_input: dict) -> str:
    """Best-effort join of all edit content in a tool_input payload.

    Covers Write/Edit (top-level `content`/`new_string`/`new_source`) AND
    MultiEdit, whose payload carries no top-level `new_string` at all — it
    sends `edits: [{old_string, new_string}, ...]` instead. Reading only the
    top-level keys meant MultiEdit content was always empty, so injections
    and violations silently never fired for it. Blanket-tolerant: a
    malformed `edits` (not a list, or elements that aren't dicts) is skipped
    rather than raised — this helper must never fail the caller (module
    contract: a hook must NEVER fail or delay the user's action)."""
    parts = [str(tool_input.get(k, ""))
             for k in ("content", "new_string", "new_source")]
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for e in edits:
            if isinstance(e, dict):
                parts.append(str(e.get("new_string", "")))
    return " ".join(parts)


def _emit(event: str, context: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event, "additionalContext": context}}))


def _zero_hit_logging() -> bool:
    """Opt-in: log precheck passes that matched nothing (#217).

    Off by default because it is one log line per edit on the hot path, plus
    gc pressure on the state dir. Turning it on buys the retrieval
    DENOMINATOR — 'edits the hook saw' — without which the retrieval number
    can only ever be a floor.
    """
    return os.environ.get("SCAR_LOG_ZERO_HITS", "").strip() not in ("", "0")


def _log_firing(store: ScarStore, target: str, hits: list,
                demoted_ids: list | None = None,
                runtime: str = "claude-code",
                edit_id: str | None = None) -> None:
    """Append one line to the firing log when precheck actually injects scars.
    Best-effort only: this is a hot path (#91) and must NEVER raise or delay
    the caller, so any failure (permissions, disk full, bad SCAR_STATE_DIR,
    whatever) is swallowed here rather than propagated. Scope: FIRING COUNTS
    only — this cannot and does not know whether the agent honored the
    injected scar, only that it was shown to it.

    `runtime` attributes the firing to the surface that produced it (#197):
    the log is machine-global and now has more than one writer, so a count
    that mixes runtimes silently is a count nobody can act on."""
    try:
        log_path = firing_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "repo": str(store.root),
            "target": target,
            "scar_ids": [s.id for s in hits],
            "count": len(hits),
            "demoted_ids": demoted_ids or [],
            "runtime": runtime,
        }
        # Correlation key (#217), omitted rather than nulled when the harness
        # does not supply one — ts is second-resolution and cannot order a
        # same-second precheck/posttool pair on its own.
        if edit_id:
            record["edit_id"] = edit_id
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def _log_violation_firing(store: ScarStore, target: str, violations: list,
                          runtime: str = "claude-code",
                          edit_id: str | None = None) -> None:
    """Append one line to the firing log when posttool actually flags a
    violation. Best-effort only, mirroring _log_firing: this must NEVER raise
    or delay the caller, so any failure (permissions, disk full, bad
    SCAR_STATE_DIR, whatever) is swallowed here rather than propagated."""
    try:
        log_path = firing_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "repo": str(store.root),
            "target": target,
            "violation_ids": [v.scar.id for v in violations],
            "count": len(violations),
            "runtime": runtime,
        }
        if edit_id:
            record["edit_id"] = edit_id
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def posttool() -> int:
    payload = _read_payload()
    if payload is None:
        return 0
    tool_input = payload.get("tool_input", {})
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not target:
        return 0
    try:
        store = ScarStore.discover(Path(target))
        if store is None:
            return 0
        new_content = _extract_edit_content(tool_input)
        try:
            rel_path = str(Path(target).resolve().relative_to(store.root))
        except ValueError:
            return 0
        violations = find_violations(store, rel_path, new_content)
        if not violations:
            return 0
        lines = [
            f"[{v.scar.id}] {v.scar.title}: this file now contains code "
            "matching this scar's violation pattern — reconsider before "
            f"proceeding; run `scar why {v.path}` for the full record"
            for v in violations
        ]
        _emit("PostToolUse", "\n".join(lines))
        vid = payload.get("tool_use_id")
        _log_violation_firing(store, target, violations,
                              edit_id=vid if isinstance(vid, str) else None)
    except Exception:
        # Contract (module docstring): a hook must NEVER fail or delay the
        # user's action. Fail OPEN on any unexpected error — emit nothing
        # rather than raise.
        return 0
    return 0


def precheck() -> int:
    payload = _read_payload()
    if payload is None:
        return 0
    tool_input = payload.get("tool_input", {})
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not target:
        return 0
    try:
        store = ScarStore.discover(Path(target))
        if store is None:
            return 0
        new_content = _extract_edit_content(tool_input)
        firing, broken = store.scan()  # one directory pass, not two (#186)
        matches = rank_matches_for_edit(store, Path(target), new_content,
                                        firing=firing)
        recent = _recently_fired(str(store.root), target)
        full, demoted = [], []
        for m in matches:
            if not has_content_signal(m):
                demoted.append((m.scar, "path-only match"))
            elif m.scar.id is not None and m.scar.id in recent:
                demoted.append((m.scar, "already shown in the last 4h"))
            else:
                full.append(m.scar)
        context = injection_context(full, broken, store.scars_dir,
                                    demoted=demoted)
        hits = [m.scar for m in matches]
        if context:
            _emit("PreToolUse", context)
        edit_id = payload.get("tool_use_id")
        if hits or _zero_hit_logging():
            _log_firing(store, target, hits,
                        demoted_ids=[s.id for s, _ in demoted],
                        edit_id=edit_id if isinstance(edit_id, str) else None)
    except Exception:
        # Contract (module docstring): a hook must NEVER fail or delay the user's
        # edit. Fail OPEN on any unexpected error — inject nothing rather than
        # raise (#91). Scoped to the ranking/render path, not the whole handler.
        return 0
    return 0


def precheck_command() -> int:
    """PreToolUse on Bash (#175): fire command-anchored scars on the shell
    command about to execute — the surface edit anchors structurally cannot
    cover. Store discovery is cwd-based (a command has no file_path). Same
    fail-open contract and 4h collapse as precheck; the firing-log target is
    the command string itself."""
    payload = _read_payload()
    if payload is None:
        return 0
    command = str(payload.get("tool_input", {}).get("command", ""))
    if not command:
        return 0
    try:
        store = ScarStore.discover(Path(payload.get("cwd") or os.getcwd()))
        if store is None:
            return 0
        from .match import rank_matches_for_command
        firing, broken = store.scan()  # one directory pass, not two (#186)
        matches = rank_matches_for_command(store, command, firing=firing)
        if not matches:
            return 0
        recent = _recently_fired(str(store.root), command)
        full, demoted = [], []
        for m in matches:
            if m.scar.id is not None and m.scar.id in recent:
                demoted.append((m.scar, "already shown in the last 4h"))
            else:
                full.append(m.scar)
        context = injection_context(full, broken, store.scars_dir,
                                    demoted=demoted)
        if context:
            _emit("PreToolUse", context)
        _log_firing(store, command, [m.scar for m in matches],
                    demoted_ids=[s.id for s, _ in demoted])
    except Exception:
        # Contract (module docstring): a hook must NEVER fail or delay the
        # user's command. Fail OPEN on any unexpected error.
        return 0
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
    # #237: this notice promises that scars are injected before edits. If the
    # precheck hook is missing that promise is false, and #236 showed the
    # failure is otherwise silent for weeks. Say so on the one surface a user
    # sees every session. Unknown (no settings file) stays quiet.
    from .installer import precheck_installed
    if precheck_installed() is False:
        warn += (" WARNING: the scar precheck hook is NOT installed, so NO "
                 "scar will be injected before an edit in this session — the "
                 "automatic injection described below is not actually running. "
                 "Run `scar hook install` to restore it.")
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


HANDLERS = {"precheck": precheck, "precheck-command": precheck_command,
            "posttool": posttool, "session-notice": session_notice,
            "stop-drafter": stop_drafter}
