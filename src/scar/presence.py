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

import base64
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


def _run(binary: str, argv: str, payload: dict | None, timeout: float, who: str):
    try:
        proc = subprocess.run(
            [binary, argv], input="" if payload is None else _canonical(payload),
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


def enrolled(org: str, device_kid: str) -> None:
    """Refuse, with the checker's reason, unless this machine holds a valid pin
    for the org and `device_kid` is an active accepted device of it. Run before
    any touch so a removed key is refused at the terminal and not after a
    prompt. It does not prove a person is present (the signature does)."""
    out = _checker("enrolled", {"device_kid": device_kid, "org": org})
    if out.get("enrolled") is True:
        return
    reason = out.get("reason")
    if out.get("enrolled") is False and isinstance(reason, str) and reason:
        raise Refused(reason)
    raise Unavailable("the checker gave an answer that is neither enrolled nor refused")


def decision_input(org: str, subject: str, record: bytes, head: dict) -> dict:
    """The one builder of the signer's input for a promotion: the draft and the
    record bytes as the attachment the person will see. The signer fills the
    nonce, the time, the performer and the key id; the head is the checker's,
    copied unchanged."""
    return {
        "attachments": {"decision_b64": base64.b64encode(record).decode("ascii")},
        "draft": {
            "body": {"action": ACTION, "consumer": CONSUMER, "subject": subject},
            "expires_in_s": EXPIRES_IN_S,
            "head": head,
            "method": METHOD,
            "org": org,
        },
    }


def _signer(command: str, payload: dict | None, timeout: float) -> dict:
    """One signer call. Exit 0 is the result. Exit 1 is the person declining.
    Every other exit (2 input rejected, 3 no key, 4 cannot ask, 5 internal)
    writes {"error": code} and is a Refused with that code."""
    proc = _run(SIGNER, command, payload, timeout, "the signer")
    if proc.returncode == 1:
        raise Cancelled("user_cancel")
    if proc.returncode != 0:
        try:
            out = _last_json(proc, "the signer")
        except Unavailable:
            raise Unavailable(f"the signer exited {proc.returncode} with no error code") from None
        code = out.get("error")
        if isinstance(code, str) and code:
            raise Refused(code)
        raise Unavailable(f"the signer exited {proc.returncode} with no error code")
    out = _last_json(proc, "the signer")
    if "error" in out:
        raise Unavailable(f"the signer exited 0 but said: {out['error']}")
    return out


def local_kid() -> str:
    """The key id of this machine's signing key. `pubkey` reads a public value
    and shows no prompt."""
    kid = _signer("pubkey", None, CHECKER_TIMEOUT_S).get("kid")
    if not isinstance(kid, str) or not kid:
        raise Unavailable("the signer gave no key id")
    return kid


def render(request: dict) -> dict:
    """Ask the signer to render the draft without signing it: it refuses a
    record the person could not see in full (over 2048 bytes or 32 lines) and
    shows no prompt. Its words come back verbatim."""
    out = _signer("render", request, CHECKER_TIMEOUT_S)
    if not isinstance(out.get("first_line"), str) or not isinstance(out.get("text"), str):
        raise Unavailable("the signer gave a render of an unknown shape")
    return {"first_line": out["first_line"], "text": out["text"]}


def sign(request: dict) -> tuple[str, str]:
    """One signature, one touch. Returns (jws, receipt_id). Raises Cancelled
    when the person declines and Refused/Unavailable for everything else; no
    signature exists unless this returns."""
    out = _signer("sign", request, SIGNER_TIMEOUT_S)
    jws, receipt = out.get("jws"), out.get("receipt_id")
    if (not isinstance(jws, str) or not jws or not isinstance(receipt, str) or not receipt):
        raise Unavailable("the signer gave a signature of an unknown shape")
    return jws, receipt
