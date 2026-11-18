"""`scar lint --verify-promotion`: check each injecting scar's promotion act (#386).

The act is verified by an external program at ONE fixed absolute path. There is
no environment variable, flag or PATH lookup to move it: a configurable path
would let anything that can edit the environment swap in a verifier that always
says yes. Only `rechecked: true` means verified; every other outcome is
reported, with the verifier's own `reason` shown verbatim (this module does not
group or interpret reason codes). States are reported, never lint errors.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

from . import config, presence
from .model import Scar
from .record import promoted_record, promotion_subject
from .store import ScarStore

TIMEOUT_SECONDS = 10

UNVERIFIED = "unverified"
VERIFIED = "verified"
NO_VERIFIER = "not verified: verifier not installed"
NO_ORG = "not verified: no org in .scars/config.json"
NO_ROOT = "not verified: repository root commit unavailable"
FAILED = "not verified: verifier failed"


def load_org(store: ScarStore) -> str | None:
    # Reporting only: a config that cannot be read is "not verified", never an
    # error here. Promote reads the same file through config.load and refuses.
    try:
        return config.load(store.scars_dir).org
    except config.ConfigError:
        return None


def _recheck(payload: dict) -> str:
    try:
        proc = subprocess.run(
            [presence.CHECKER, "recheck-act"], input=json.dumps(payload, sort_keys=True),
            capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return FAILED
    if proc.returncode != 0:
        return FAILED
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return FAILED
    if not isinstance(out, dict) or "error" in out or "rechecked" not in out:
        return FAILED
    if out["rechecked"] is True:
        return VERIFIED
    return f"not proven: {out.get('reason', '')}"


def verify_scar(scar: Scar, *, org: str | None, repo: Path) -> str:
    if not scar.promotion_act:
        return UNVERIFIED
    if not os.path.exists(presence.CHECKER):
        return NO_VERIFIER
    if org is None:
        return NO_ORG
    subject = promotion_subject(repo, scar.id) if scar.id is not None else None
    if subject is None:
        return NO_ROOT
    return _recheck({
        "action": presence.ACTION,
        "consumer": presence.CONSUMER,
        "decision_b64": base64.b64encode(promoted_record(scar)).decode("ascii"),
        "expect_method": presence.METHOD,
        "jws": scar.promotion_act,
        "org": org,
        "subject": subject,
    })


def verify_store(store: ScarStore) -> list[tuple[Path, Scar, str]]:
    """One state per scar that still injects (active or challenged), the set
    store.firing() owns, so verification never defines its own."""
    org = load_org(store)
    return [(f, s, verify_scar(s, org=org, repo=store.root)) for f, s in store.firing()]
