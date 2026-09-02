"""Codex hook adapter — native payloads in, advisory SCAR context out.

Codex exposes file edits as one apply_patch program in `tool_input.command`,
not as Claude Code's structured `file_path` and replacement fields. This
module keeps that wire-format translation separate while reusing the same
store, ranking, rendering, cooldown, and firing-log contracts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .hooks import (
    DEMOTED_COOLDOWN,
    DEMOTED_PATH_ONLY,
    _context_bytes,
    _demoted_for_render,
    _demotion_reasons,
    _emit,
    _log_firing,
    _log_violation_firing,
    _precheck_command_payload,
    _read_payload,
    _recently_fired,
    _session_notice_payload,
    _zero_hit_logging,
)
from .match import (
    find_violations_for_targets,
    has_content_signal,
    rank_matches_for_targets,
)
from .render import injection_context
from .store import ScarStore

RUNTIME = "codex"

_BEGIN = "*** Begin Patch"
_END = "*** End Patch"
_ADD = "*** Add File: "
_UPDATE = "*** Update File: "
_DELETE = "*** Delete File: "
_MOVE = "*** Move to: "


@dataclass(frozen=True)
class PatchTarget:
    path: str
    added_content: str


def parse_apply_patch(command: str) -> list[PatchTarget]:
    """Parse Codex's apply_patch program into touched path/content pairs.

    Only added lines are content signals, matching SCAR's unified-diff path.
    Deletes still return their path so path/symbol anchors can surface. A move
    touches both names. Unknown directives fail closed *inside the parser*;
    the hook then fails open by emitting no context.
    """
    if not isinstance(command, str):
        return []
    lines = command.strip().splitlines()
    if len(lines) < 3 or lines[0] != _BEGIN or lines[-1] != _END:
        return []

    targets: list[PatchTarget] = []
    kind: str | None = None
    path: str | None = None
    move_to: str | None = None
    added: list[str] = []

    def flush() -> None:
        if path is None:
            return
        content = "\n".join(added)
        targets.append(PatchTarget(path, content))
        if move_to is not None:
            targets.append(PatchTarget(move_to, content))

    for line in lines[1:-1]:
        directive = next(
            ((name, line[len(prefix):].strip())
             for prefix, name in ((_ADD, "add"), (_UPDATE, "update"),
                                  (_DELETE, "delete"))
             if line.startswith(prefix)),
            None,
        )
        if directive is not None:
            flush()
            kind, path = directive
            if not path:
                return []
            move_to = None
            added = []
            continue
        if line.startswith(_MOVE):
            if kind != "update" or move_to is not None:
                return []
            move_to = line[len(_MOVE):].strip()
            if not move_to:
                return []
            continue
        if line == "*** End of File":
            continue
        if line.startswith("*** "):
            return []
        if path is None:
            if line.strip():
                return []
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif kind == "add" and line:
            return []

    flush()
    if not targets:
        return []

    merged: dict[str, list[str]] = {}
    for target in targets:
        merged.setdefault(target.path, []).append(target.added_content)
    return [PatchTarget(path, "\n".join(filter(None, contents)))
            for path, contents in merged.items()]


def _targets(payload: dict) -> tuple[ScarStore | None, list[tuple[Path, str]]]:
    tool_input = payload.get("tool_input", {})
    parsed = parse_apply_patch(tool_input.get("command", ""))
    if not parsed:
        return None, []

    cwd = Path(payload.get("cwd") or os.getcwd()).resolve()
    store = ScarStore.discover(cwd)
    if store is None:
        return None, []

    resolved: dict[Path, list[str]] = {}
    for target in parsed:
        candidate = Path(target.path)
        candidate = candidate if candidate.is_absolute() else cwd / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(store.root)
        except ValueError:
            continue
        resolved.setdefault(candidate, []).append(target.added_content)
    return store, [
        (path, "\n".join(filter(None, contents)))
        for path, contents in resolved.items()
    ]


def _edit_id(payload: dict) -> str | None:
    value = payload.get("tool_use_id")
    return value if isinstance(value, str) else None


def pretool() -> int:
    payload = _read_payload()
    if payload is None:
        return 0
    try:
        tool_name = payload.get("tool_name")
        if tool_name == "Bash":
            return _precheck_command_payload(payload, runtime=RUNTIME)
        if tool_name != "apply_patch":
            return 0

        store, targets = _targets(payload)
        if store is None or not targets:
            return 0
        firing, broken = store.scan()
        matches = rank_matches_for_targets(store, targets, firing=firing)

        full, demoted = [], []
        for match in matches:
            target = str((store.root / match.path).resolve())
            recent = _recently_fired(str(store.root), target)
            if not has_content_signal(match):
                demoted.append((match.scar, DEMOTED_PATH_ONLY))
            elif match.scar.id is not None and match.scar.id in recent:
                demoted.append((match.scar, DEMOTED_COOLDOWN))
            else:
                full.append(match.scar)

        context = injection_context(full, broken, store.scars_dir,
                                    demoted=_demoted_for_render(demoted))
        if context:
            _emit("PreToolUse", context)

        demoted_ids = {scar.id for scar, _ in demoted}
        # #284: one row per path, so the reasons are sliced per row exactly
        # the way demoted_ids is, and the two can never disagree about which
        # ids a row carries.
        reasons = _demotion_reasons(demoted)
        by_path: dict[str, list] = {}
        for match in matches:
            by_path.setdefault(match.path, []).append(match.scar)
        edit_id = _edit_id(payload)
        # #279: omitted when this host supplies no transcript path, which is
        # the honest state rather than a zero.
        ctx = _context_bytes(payload)
        for rel_path, scars in by_path.items():
            target = str((store.root / rel_path).resolve())
            row_ids = [s.id for s in scars if s.id in demoted_ids]
            _log_firing(store, target, scars,
                        demoted_ids=row_ids,
                        demotion_reasons={str(i): reasons[str(i)] for i in row_ids
                                          if str(i) in reasons},
                        runtime=RUNTIME, edit_id=edit_id, context_bytes=ctx)
        if not matches and _zero_hit_logging():
            for target, _ in targets:
                _log_firing(store, str(target), [], runtime=RUNTIME,
                            edit_id=edit_id, context_bytes=ctx)
    except Exception:
        return 0
    return 0


def _tool_succeeded(response: object) -> bool:
    if isinstance(response, dict):
        code = response.get("exit_code")
        if isinstance(code, int):
            return code == 0
        if response.get("success") is True or response.get("status") == "completed":
            return True
        return False
    if not isinstance(response, str):
        return False
    for line in response.splitlines():
        if line.lower().startswith("exit code:"):
            try:
                return int(line.split(":", 1)[1].strip()) == 0
            except ValueError:
                return False
    return "Success. Updated the following files:" in response


def posttool() -> int:
    payload = _read_payload()
    if payload is None:
        return 0
    try:
        if payload.get("tool_name") != "apply_patch":
            return 0
        if not _tool_succeeded(payload.get("tool_response")):
            return 0
        store, targets = _targets(payload)
        if store is None or not targets:
            return 0
        violations = find_violations_for_targets(store, targets)
        if not violations:
            return 0
        lines = [
            f"[{v.scar.id}] {v.scar.title}: {v.path} now contains code "
            "matching this scar's violation pattern — reconsider before "
            f"proceeding; run `scar why {v.path}` for the full record"
            for v in violations
        ]
        _emit("PostToolUse", "\n".join(lines))
        edit_id = _edit_id(payload)
        for path in dict.fromkeys(v.path for v in violations):
            scoped = [v for v in violations if v.path == path]
            _log_violation_firing(
                store, str((store.root / path).resolve()), scoped,
                runtime=RUNTIME, edit_id=edit_id)
    except Exception:
        return 0
    return 0


def session_notice() -> int:
    payload = _read_payload()
    if payload is None:
        return 0
    try:
        return _session_notice_payload(payload, verify_claude_hook=False)
    except Exception:
        return 0


HANDLERS = {
    "codex-pretool": pretool,
    "codex-posttool": posttool,
    "codex-session-notice": session_notice,
}
