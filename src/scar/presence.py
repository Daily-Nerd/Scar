"""The presence signer and checker: the one place that owns their fixed paths,
the words a promotion act is made of, and the calls Scar makes to them (#392).

Both programs are run only by an absolute path. There is no environment
variable, flag or PATH lookup to move either: a configurable path would let
anything that can edit the environment swap in a signer or checker that always
says yes. Tests replace the module attributes below; production code never does.

Every refusal carries the program's own reason code, verbatim. This module does
not group, translate or soften them: the person reading the output sees what
the checker said.
"""
from __future__ import annotations

import json
import subprocess

_BIN = "/Library/Application Support/handsal/bin"
SIGNER = f"{_BIN}/handsal-sign"
CHECKER = f"{_BIN}/handsal-check"

# Signed domain separation: renaming CONSUMER makes every act already stored
# consumer_mismatch on recheck-act. It must never change (a test pins it).
CONSUMER = "scar"
ACTION = "promote"
METHOD = "local:handsal.decision"

# How long a freshly signed act stays acceptable to the first-acceptance check.
# A decimal string, as the signer requires. A constant, not config: Scar cannot
# know the org's maximum and keeps no copy of it. An org that allows less than
# this answers validity_too_long at acceptance, after the touch; the remedy is a
# deliberate change to this constant.
EXPIRES_IN_S = "3600"

# The checker answers in milliseconds. The signer waits for a person at a
# prompt, so its bound is generous: a person who walked away must not hang a
# batch forever, but one who is reading the render must not be cut off.
CHECKER_TIMEOUT_S = 10
SIGNER_TIMEOUT_S = 300


class PresenceError(Exception):
    """Base of every failure below. `reason` is the program's own word."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Unavailable(PresenceError):
    """No usable answer: the program is missing, crashed, timed out, or said
    something that is not its documented output. Nothing is assumed."""


class Refused(PresenceError):
    """The program answered, and the answer was no. `reason` is its code."""


class Cancelled(PresenceError):
    """The person declined the touch (signer exit 1)."""


def _canonical(payload: dict) -> str:
    # The signer reads its stdin through the canonical subset: no whitespace,
    # no numbers, no booleans. Sorted keys, compact, ASCII.
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _run(binary: str, argv: str, payload: dict, timeout: float, who: str):
    try:
        proc = subprocess.run(
            [binary, argv], input=_canonical(payload),
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Unavailable(f"{who} did not answer in {timeout:g}s") from None
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unavailable(f"{who} could not run: {exc}") from None
    return proc


def _last_json(proc, who: str) -> dict:
    lines = proc.stdout.strip().splitlines()
    try:
        out = json.loads(lines[-1])
    except (ValueError, IndexError):
        raise Unavailable(f"{who} gave no JSON answer") from None
    if not isinstance(out, dict):
        raise Unavailable(f"{who} gave a non-object answer")
    return out


def _checker(command: str, payload: dict) -> dict:
    """One checker call. The checker exits 0 always; anything else is a crash.
    An {"error": code} answer is the checker refusing our input, not a verdict
    on a person, so it is Unavailable with the code shown."""
    proc = _run(CHECKER, command, payload, CHECKER_TIMEOUT_S, "the checker")
    if proc.returncode != 0:
        raise Unavailable(f"the checker exited {proc.returncode}")
    out = _last_json(proc, "the checker")
    if "error" in out:
        raise Unavailable(f"the checker said: {out['error']}")
    return out


def head(org: str) -> dict:
    """The chain head to name in a draft, exactly as the checker gives it (no
    key stripped, none added). Refused with the checker's reason when no head
    is acceptable: a refused view has none, and a touch must not be spent on an
    act this machine would refuse."""
    out = _checker("head", {"org": org})
    reason = out.get("reason")
    value = out.get("head")
    if reason != "ok":
        if isinstance(reason, str) and reason:
            raise Refused(reason)
        raise Unavailable("the checker gave no reason")
    if (not isinstance(value, dict) or set(value) != {"id", "kind"}
            or not isinstance(value["id"], str) or value["kind"] != "chain"):
        raise Unavailable("the checker gave an ok head of an unknown shape")
    return value
