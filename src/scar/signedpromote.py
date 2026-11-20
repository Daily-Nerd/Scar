"""Promote one scar with a presence signature (#392).

The invariant this module exists to hold: no active file is written, no
candidate is removed and no id is used up unless the signer exited 0 AND the
checker answered `valid: true` for those exact bytes, this subject and the
consumer `scar`. Every other path raises SignedPromoteError and has touched
nothing on disk, because the only disk write is the last line of promote_one.

Order per scar: prepare (no I/O), head, sign (the touch), first-acceptance
check, then set the act and the receipt and write. The head is fetched fresh
for each scar, as the profile requires, never reused from a previous one.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import presence
from .record import promoted_record, promotion_subject
from .model import Scar
from .store import PreparedPromotion, ScarStore


class SignedPromoteError(Exception):
    """A promotion that wrote nothing. `touch_spent` is True only when a
    signature was made and then not accepted, so the output can say the
    person's touch bought nothing."""

    def __init__(self, message: str, *, touch_spent: bool = False, cancelled: bool = False):
        super().__init__(message)
        self.touch_spent = touch_spent
        self.cancelled = cancelled


def subject_for(store: ScarStore, scar: Scar, name: str) -> str:
    subject = promotion_subject(store.root, scar.id)
    if subject is None:
        raise SignedPromoteError(
            f"{name}: repository root commit unavailable, so the "
            "promotion cannot be bound to this repository. Nothing was signed.")
    return subject


def preflight(store: ScarStore, org: str, targets: list[Path], reviewer: str,
              reviewer_source: str | None) -> None:
    """Everything that can be refused without a person, checked before the
    first prompt so a batch never starts what it cannot finish: both programs
    exist at their fixed paths, the repository can name its root, the checker
    has a head, this machine's key is still enrolled (a removed member's key
    can still sign, and the checker would only refuse it after the touch), and
    the signer can show every record in full. Touches no file and asks for no
    signature."""
    for path in (presence.SIGNER, presence.CHECKER):
        if not os.path.exists(path):
            raise SignedPromoteError(f"not installed at its fixed path: {path}")
    base = store.next_id()
    if promotion_subject(store.root, base) is None:
        raise SignedPromoteError(
            "repository root commit unavailable, so a promotion cannot be bound to "
            "this repository.")
    try:
        head = presence.head(org)
    except presence.PresenceError as exc:
        raise SignedPromoteError(f"no chain head from the checker: {exc.reason}.") from None
    try:
        presence.enrolled(org, presence.local_kid())
    except presence.PresenceError as exc:
        raise SignedPromoteError(f"this machine's key is not enrolled: {exc.reason}.") from None
    for i, target in enumerate(targets):
        prepared = store.prepare_promotion(target, reviewer, reviewer_source)
        # The id this scar will get once the ones before it are written: it is
        # in the bytes and in the subject, and a record at the size limit must
        # be judged with the right digits.
        prepared.scar.id = base + i
        subject = promotion_subject(store.root, prepared.scar.id)
        try:
            presence.render(presence.decision_input(
                org, subject, promoted_record(prepared.scar), head))
        except presence.PresenceError as exc:
            raise SignedPromoteError(
                f"{target.name}: the signer cannot show this record: {exc.reason}.") from None


def sign_record(store: ScarStore, scar: Scar, org: str, name: str) -> tuple[str, str]:
    """Obtain an accepted act and receipt for `scar`'s record as it stands, or
    raise SignedPromoteError. The one owner of the sequence: head, sign (the
    touch), first-acceptance check. It writes nothing and assumes neither a
    candidate nor a file being created, so promotion and any later re-signing
    share it. `name` only labels errors. Preflight is not run here: a batch
    runs `preflight` once for every target before the first touch."""
    record = promoted_record(scar)
    subject = subject_for(store, scar, name)
    try:
        head = presence.head(org)
    except presence.PresenceError as exc:
        raise SignedPromoteError(
            f"{name}: no chain head from the checker: {exc.reason}. Nothing was "
            "signed and nothing was written.") from None
    try:
        jws, _signer_receipt = presence.sign(
            presence.decision_input(org, subject, record, head))
    except presence.Cancelled:
        raise SignedPromoteError(
            f"cancelled at {name}: nothing was signed and nothing was written.",
            cancelled=True) from None
    except presence.PresenceError as exc:
        raise SignedPromoteError(
            f"{name}: the signer gave no signature: {exc.reason}. Nothing was "
            "signed and nothing was written.") from None
    try:
        receipt = presence.ingest(org, subject, record, jws, store.seen_receipts())
    except presence.PresenceError as exc:
        raise SignedPromoteError(
            f"the touch was spent, but the checker did not accept the act: {exc.reason}. "
            f"Nothing was written: {name} is unchanged and its id is not used up.",
            touch_spent=True) from None
    return jws, receipt


def promote_one(store: ScarStore, prepared: PreparedPromotion, org: str) -> Path:
    name = prepared.candidate.name
    jws, receipt = sign_record(store, prepared.scar, org, name)
    prepared.scar.promotion_act = jws
    prepared.scar.promotion_receipt = receipt
    try:
        return store.commit_promotion(prepared)
    except ValueError as exc:
        raise SignedPromoteError(
            f"the touch was spent and the act was accepted, but {exc}. Nothing was "
            f"written: {name} is unchanged.", touch_spent=True) from None
