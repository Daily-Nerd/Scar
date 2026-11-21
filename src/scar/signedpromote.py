"""Promote one scar with a presence signature (#392).

The invariant this module exists to hold: no active file is written, no
candidate is removed and no id is used up unless the signer exited 0 AND the
checker answered `valid: true` for those exact bytes, this subject and the
consumer `scar`. Every other path raises SignedPromoteError and has touched
nothing on disk, because the only disk write is the last line of promote_one.

Re-signing (#425) reuses the same pieces for a scar that is already active:
`resign_one` signs the record as it stands and swaps only the act and receipt
lines, through the one write gate.

Order per scar: prepare (no I/O), head, sign (the touch), first-acceptance
check, then set the act and the receipt and write. The head is fetched fresh
for each scar, as the profile requires, never reused from a previous one.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import config, presence
from .record import promoted_record, promotion_subject, record_hash
from .model import _FRONTMATTER_RE, ParseError, Scar, parse_scar_text
from .store import PreparedPromotion, ScarStore, act_matches_record, write_active_scar


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


def machine_checks(store: ScarStore, org: str, subject_id: int) -> dict:
    """The checks that need no record and no person, shared by promotion and
    re-signing: both programs exist at their fixed paths, the repository can
    name its root (judged with `subject_id`), the checker has a head, and this
    machine's key is still enrolled (a removed member's key can still sign, and
    the checker would only refuse it after the touch). Returns the head so the
    render check can use it. Touches no file and asks for no signature."""
    for path in (presence.SIGNER, presence.CHECKER):
        if not os.path.exists(path):
            raise SignedPromoteError(f"not installed at its fixed path: {path}")
    if promotion_subject(store.root, subject_id) is None:
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
    return head


def render_check(store: ScarStore, org: str, scar: Scar, head: dict, name: str) -> None:
    """Ask the signer to show `scar`'s record in full, without signing. `scar`
    must carry the id it will be signed under: it is in the bytes and in the
    subject, and a record at the size limit must be judged with the right
    digits. `name` only labels the error."""
    subject = promotion_subject(store.root, scar.id)
    try:
        presence.render(presence.decision_input(org, subject, promoted_record(scar), head))
    except presence.PresenceError as exc:
        raise SignedPromoteError(
            f"{name}: the signer cannot show this record: {exc.reason}.") from None


def preflight(store: ScarStore, org: str, targets: list[Path], reviewer: str,
              reviewer_source: str | None) -> None:
    """Everything that can be refused without a person, checked before the
    first prompt so a batch never starts what it cannot finish: the machine
    checks, then the render check of every candidate as it will be promoted.
    Touches no file and asks for no signature."""
    base = store.next_id()
    head = machine_checks(store, org, base)
    for i, target in enumerate(targets):
        prepared = store.prepare_promotion(target, reviewer, reviewer_source)
        # The id this scar will get once the ones before it are written.
        prepared.scar.id = base + i
        render_check(store, org, prepared.scar, head, target.name)


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


_ACT_LINE_RE = re.compile(r"^\s*promotion_(?:act|receipt):")
_ANCHORS_LINE_RE = re.compile(r"^anchors:")


def with_act(text: str, act: str, receipt: str) -> str:
    """`text` with its `promotion_act` and `promotion_receipt` lines set, and
    every other byte as it was. A surgical edit of the frontmatter, never a
    parse and reserialize (that would reorder and requote lines, and the record
    the person saw would no longer be the record on disk). Existing lines of
    either key are removed and the pair goes where the first one was; with none,
    before `anchors:` (where Scar.to_text writes them), else at the end of the
    frontmatter. The body is never read as frontmatter. The line ending of the
    file is kept."""
    m = _FRONTMATTER_RE.match(text)
    if m is None:
        raise ValueError("no frontmatter to put the act in")
    eol = "\r" if text.startswith("---\r\n") else ""
    lines = text[m.start(1):m.end(1)].split("\n")
    first = next((i for i, line in enumerate(lines) if _ACT_LINE_RE.match(line)), None)
    if first is None:
        first = next((i for i, line in enumerate(lines) if _ANCHORS_LINE_RE.match(line)),
                     len(lines))
    kept_before = sum(1 for line in lines[:first] if not _ACT_LINE_RE.match(line))
    lines = [line for line in lines if not _ACT_LINE_RE.match(line)]
    lines[kept_before:kept_before] = [f"promotion_act: {act}{eol}",
                                      f"promotion_receipt: {receipt}{eol}"]
    return text[:m.start(1)] + "\n".join(lines) + text[m.end(1):]


def resign_target(store: ScarStore, scar_id: int) -> tuple[Path, Scar]:
    """The active scar `scar_id` and its file, or a refusal that touched
    nothing: an unknown id (a candidate has none), a scar that is not active,
    or one whose act already matches its record (nothing to sign)."""
    for path, scar in store.parsed():
        if scar.id == scar_id:
            break
    else:
        raise SignedPromoteError(f"no scar with id {scar_id}")
    if scar.status != "active":
        raise SignedPromoteError(
            f"scar #{scar_id} is {scar.status}: only an active scar is signed")
    if act_matches_record(scar):
        raise SignedPromoteError(
            f"scar #{scar_id} already carries an act that matches its record: "
            "nothing to sign")
    return path, scar


def signing_org(store: ScarStore) -> str:
    """The org this repository signs under, or a refusal that names why it
    cannot: an unreadable config, no org, or a config lint would refuse. One
    owner for `scar resign` and the tools that sign in place, so none of them
    reads `.scars/config.json` its own way."""
    try:
        cfg = config.load(store.scars_dir)
    except config.ConfigError as exc:
        raise SignedPromoteError(str(exc)) from None
    if cfg.org is None:
        raise SignedPromoteError(
            "no org in .scars/config.json, so there is nothing to sign for")
    problem = config.signing_problem(cfg, store.next_id())
    if problem:
        raise SignedPromoteError(problem)
    return cfg.org


def is_signed(scar: Scar) -> bool:
    """True when `scar` carries an act that matches its record: the scars the
    write gate protects, and the only ones a rewriting tool signs in place. An
    act that is stale or cannot be read is not signed in this sense (`scar
    resign` is its remedy), and neither is a scar with no act."""
    return act_matches_record(scar) is True


def check_signable_status(scar: Scar) -> None:
    """Raise unless `scar` is active, the rule `scar resign` applies: a signed
    scar that is challenged (or orphaned) is not signed again by a rewrite."""
    if scar.status != "active":
        raise SignedPromoteError(
            f"scar #{scar.id} is {scar.status}: only an active scar is signed")


def new_record_checks(store: ScarStore, org: str, path: Path, new_text: str,
                      head: dict) -> Scar:
    """What can be refused about the record `new_text` would sign, without a
    person: the record is built from the parse of the text, so the bytes the
    signer shows are the bytes that get written; the signer's render check on
    it; and the text to be written. The record holds neither the act nor the
    receipt, so that text is built with placeholders: if those lines would
    change what parses as the record, the touch is never asked for. Returns the
    scar parsed from `new_text`. Touches no file and asks for no signature."""
    try:
        scar = parse_scar_text(new_text)
    except ParseError as exc:
        raise SignedPromoteError(f"{path.name}: the new text does not parse: {exc}") from None
    render_check(store, org, scar, head, path.name)
    if record_hash(parse_scar_text(with_act(new_text, "-", "-"))) != record_hash(scar):
        raise SignedPromoteError(
            f"{path.name}: putting the act in the file would change its record.")
    return scar


def resign_preflight(store: ScarStore, org: str, path: Path, scar: Scar) -> str:
    """Everything that can be refused before the touch when re-signing: the
    machine checks, then `new_record_checks` on the file as it stands. Returns
    the file text the checks were made on."""
    original = path.read_bytes().decode("utf-8")
    head = machine_checks(store, org, scar.id)
    new_record_checks(store, org, path, original, head)
    return original


def resign_one(store: ScarStore, org: str, path: Path, original: str, new_text: str) -> None:
    """Sign the record `new_text` holds and store the act and receipt in it:
    head, sign, first-acceptance check, then one write of the text with the
    act in it. `original` is the file as it was judged and `new_text` is what
    it is to become before the act goes in. `scar resign` is the case where the
    two are equal; a tool that rewrites anchors passes the rewritten text. The
    file is read again before the write, and one that changed during the touch
    is left alone."""
    name = path.name
    jws, receipt = sign_record(store, parse_scar_text(new_text), org, name)
    if path.read_bytes().decode("utf-8") != original:
        raise SignedPromoteError(
            f"the touch was spent and the act was accepted, but {name} changed while it "
            f"was being signed. Nothing was written: {name} is as the other change left it.",
            touch_spent=True)
    try:
        write_active_scar(path, with_act(new_text, jws, receipt))
    except ValueError as exc:
        raise SignedPromoteError(
            f"the touch was spent and the act was accepted, but {exc}. Nothing was "
            f"written: {name} is unchanged.", touch_spent=True) from None
