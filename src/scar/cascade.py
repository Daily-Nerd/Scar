"""Windsurf/Cascade hook adapter — Cascade JSON on stdin, exit code out.

Cascade has no additionalContext equivalent. On exit 0 stdout reaches the
Cascade UI only, never the model; the single channel into the agent's context
is a BLOCKING exit, which surfaces stderr and cancels the pending action. So
injection here is **block-once**: an armed scar that matches bounces the
action exactly once per (trajectory, scar, target), renders compactly on
stderr, and lets the identical retry straight through. Cost is one bounced
action per firing — the advisory posture bends, it does not break.

Two consequences shape everything below.

1. The precision bar is higher than the Claude Code tier. A false firing
   there spends tokens; here it cancels a user-visible action. Only
   content-signal matches (the pattern hit the edit, the symbol resolved, the
   command IS the mistake) may block. Path-proximity matches prove the file,
   not the act — they go to the UI on stdout and never block.
2. Fire-once state is file-based and self-expiring: Cascade gives no
   session-end signal to clean up on. `trajectory_id` already scopes a key to
   one conversation, so the TTL and the size cap are garbage collection, not
   semantics.

Same fail-open contract as hooks.py, with one addition: a block is only ever
emitted when the fire was durably recorded, because a block we cannot
remember bounces the same action forever.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from .hooks import (
    _extract_edit_content,
    _log_firing,
    _log_violation_firing,
    _read_payload,
    _state_dir,
)
from .match import (MatchCensus, armed_scar_ids, find_violations,
                    has_content_signal, rank_and_census_for_edit)
from .render import compact_block, rule_line
from .store import ScarStore

# NOT 2, which is what Cascade reads as "block". argparse also exits 2 on an
# unknown subcommand, so an older scar-cli reached through a committed
# .windsurf/hooks.json would bounce every single write with a usage message.
# The installed command maps this sentinel to 2 instead, leaving version skew
# to degrade into a silent no-op (Cascade: any other code -> action proceeds).
BLOCK_EXIT = 20

RUNTIME = "windsurf"

# Hours-scale, deliberately longer than a working session: the key is already
# scoped by trajectory_id, so expiry only bounds the file's growth. A
# conversation still alive past the TTL re-blocks once, which is the same cost
# as a new conversation.
FIRE_TTL_SECONDS = 24 * 3600
FIRE_STATE_MAX = 1000


def fire_state_path() -> Path:
    """Where block-once state lives — same state dir (and SCAR_STATE_DIR
    override) as the drafter markers and the firing log, so there stays one
    knob for all hook state."""
    return _state_dir() / "cascade-fire-state.json"


def _load_fire_state() -> dict[str, float]:
    """Recorded fires that have not expired. Best-effort: a missing, corrupt,
    or unreadable file reads as empty — worst case a scar blocks once more
    than it strictly had to, never a crash."""
    try:
        raw = json.loads(fire_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    cutoff = time.time() - FIRE_TTL_SECONDS
    return {k: v for k, v in raw.items()
            if isinstance(k, str) and isinstance(v, (int, float)) and v >= cutoff}


def _remember_fired(keys: list[str]) -> bool:
    """Record `keys` as fired. Returns whether the write actually landed —
    the caller blocks ONLY on True, because an unrecordable fire means the
    retry would bounce again, and again, with no way out for the user.

    Written to a temp file in the same directory and swapped in with
    os.replace, mirroring gc.truncate_firing_log: a concurrent hook run can
    never observe a half-written state file.
    """
    now = time.time()
    state = _load_fire_state()
    state.update({k: now for k in keys})
    if len(state) > FIRE_STATE_MAX:
        newest = sorted(state.items(), key=lambda kv: -kv[1])[:FIRE_STATE_MAX]
        state = dict(newest)
    path = fire_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                        prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except Exception:
        return False
    return True


def _block_text(scars: list) -> str:
    """What the agent reads on the bounced action. Compact tier only — this
    renders as an error in the Cascade UI, and the agent needs the constraint,
    not the essay."""
    return (
        f"SCAR blocked this action once — {len(scars)} scar(s) record why it "
        "went wrong before. Read them, then retry: the identical action goes "
        "through, informed.\n\n"
        + compact_block(scars)
        + "\n\nFull records in .scars/ (`scar why <path>`).")


def _partition(matches: list, fired: dict[str, float], trajectory: str,
               target: str) -> tuple[list, list]:
    """Split matches into (block, surface-only). A match blocks when it
    carries an edit-content signal AND has not already fired for this
    trajectory+target; everything else reaches the human on stdout only."""
    to_block, to_show = [], []
    for m in matches:
        key = _fire_key(trajectory, m.scar.id, target)
        if has_content_signal(m) and key not in fired:
            to_block.append(m)
        else:
            to_show.append(m)
    return to_block, to_show


def _fire_key(trajectory: str, scar_id: int | None, target: str) -> str:
    return f"{trajectory}|{scar_id}|{target}"


def _respond(store: ScarStore, trajectory: str, target: str,
             matches: list, census: MatchCensus | None = None,
             anchor_kind: str | None = None) -> int:
    """The shared tail of both pre-hooks: decide, render, log, exit.

    `anchor_kind` is what tells the two pre-hooks apart on disk (#294). They
    share this tail and therefore share a row shape, so without it a blocked
    command and a blocked edit are indistinguishable to `scar stats`."""
    if not matches:
        return 0
    fired = _load_fire_state()
    to_block, to_show = _partition(matches, fired, trajectory, target)
    if to_block and not _remember_fired(
            [_fire_key(trajectory, m.scar.id, target) for m in to_block]):
        to_show, to_block = matches, []
    for m in to_show:
        # UI-only channel: the model never sees exit-0 output, so this is a
        # note for the human, not injection. Kept to one line per scar.
        print(f"scar [#{m.scar.id}] {m.scar.title} — {rule_line(m.scar.body)}")
    if not to_block:
        return 0
    scars = [m.scar for m in to_block]
    print(_block_text(scars), file=sys.stderr)
    # Firing log parity (#106) records what the AGENT saw, and here only a
    # block reaches it — a stdout line went to the human. So the surface-only
    # matches are NOT logged, and in particular not as `demoted_ids`: that
    # field means "shown to the model, collapsed to one line" and stays a
    # subset of scar_ids. Logging UI-only showings either way would inflate
    # every count downstream of `scar stats`.
    # Reached only past `if not to_block: return 0`, so this firing DID
    # refuse the action. Surface-only matches never reach the log at all
    # (see the comment above), so every windsurf row is block-capable.
    _log_firing(store, target, scars, runtime=RUNTIME, block_capable=True,
                matched=census, anchor_kind=anchor_kind)
    return BLOCK_EXIT


def pre_write_code(payload: dict) -> int:
    tool_info = payload.get("tool_info") or {}
    target = tool_info.get("file_path")
    if not target:
        return 0
    store = ScarStore.discover(Path(target))
    if store is None:
        return 0
    firing, _broken = store.scan()  # one directory pass, not two (#186)
    matches, census = rank_and_census_for_edit(store, Path(target),
                                               _extract_edit_content(tool_info),
                                               firing=firing)
    return _respond(store, _trajectory(payload), target, matches, census,
                    anchor_kind="edit")


def pre_run_command(payload: dict) -> int:
    tool_info = payload.get("tool_info") or {}
    command = str(tool_info.get("command_line") or "")
    if not command:
        return 0
    store = ScarStore.discover(Path(tool_info.get("cwd") or os.getcwd()))
    if store is None:
        return 0
    from .match import rank_and_census_for_command
    firing, _broken = store.scan()
    matches, census = rank_and_census_for_command(store, command, firing=firing)
    return _respond(store, _trajectory(payload), command, matches, census,
                    anchor_kind="command")


def post_write_code(payload: dict) -> int:
    """Violation tripwire on the edit that already landed. Post-hooks cannot
    block in Cascade (exit 2 is pre-hooks only), so this is the human channel
    plus the firing log — which is what keeps `scar stats` honest about
    fired-then-violated for this runtime."""
    tool_info = payload.get("tool_info") or {}
    target = tool_info.get("file_path")
    if not target:
        return 0
    store = ScarStore.discover(Path(target))
    if store is None:
        return 0
    try:
        rel_path = str(Path(target).resolve().relative_to(store.root))
    except ValueError:
        return 0
    armed = armed_scar_ids(store, rel_path)
    violations = find_violations(store, rel_path, _extract_edit_content(tool_info))
    # Nothing here was capable of producing a violation, so no verdict is owed
    # and the log must not grow on every unrelated edit in the repo.
    if not armed and not violations:
        return 0
    for v in violations:
        print(f"scar VIOLATION [#{v.scar.id}] {v.scar.title} — {v.path} now "
              f"matches this scar's violation pattern; `scar why {v.path}`")
    # Logged on the CLEAN path too (#293, the #277 mechanism reaching this
    # runtime). Silence from this hook must be distinguishable from a clean
    # verdict, and it only can be if a clean run leaves a row behind.
    _log_violation_firing(store, target, violations, runtime=RUNTIME,
                          armed_ids=armed)
    return 0


def _trajectory(payload: dict) -> str:
    """Cascade's conversation id — the natural session scope for block-once.
    A payload without one still gets a stable bucket rather than a per-call
    key, which would block the same action on every retry."""
    return str(payload.get("trajectory_id") or "unknown")


EVENTS = {"pre_write_code": pre_write_code, "pre_run_command": pre_run_command,
          "post_write_code": post_write_code}


def cascade_hook() -> int:
    payload = _read_payload(
        hint="scar cascade-hook expects a Cascade hook payload on stdin (it is "
             "run by Windsurf, not by hand). Wire it with: "
             "scar hook install --runtime windsurf")
    if not isinstance(payload, dict):
        # None is the tty path; anything else is valid JSON that simply is not
        # an object (`[]`, `null`, a bare number) and would explode on .get()
        # — the same shape that has bitten the firing-log readers before.
        return 0
    handler = EVENTS.get(payload.get("agent_action_name"))
    if handler is None:
        return 0
    try:
        return handler(payload)
    except Exception:
        # Same contract as hooks.py, with more at stake: an unexpected failure
        # must never block the user's edit or command. Fail OPEN.
        return 0
