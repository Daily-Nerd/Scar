"""The bytes a promotion signs: the only producer of a promoted record (#382).

Signing at promotion and verifying later must see identical bytes from the same
scar, so every caller goes through promoted_record() and nothing else rebuilds
them. The record holds only what a person decides (id, type, title, severity,
anchors, body), built from parsed values so frontmatter quoting, key order and
line endings cannot change it. Lifecycle fields are never included.

Anchors are in on purpose: moving one changes where the scar fires. There is no
size check here; the signer refuses an oversize record before any confirmation.
"""
from __future__ import annotations

import base64
import hashlib
import subprocess
from pathlib import Path

from .model import Scar


def promotion_subject(repo: Path | str, scar_id: int) -> str | None:
    """`<id>-<first 16 hex of the first-parent root commit>`: ties a promotion
    act to one scar in one repository. None when git cannot name a root (not a
    repo, no commits): a caller must report that, never invent a subject."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-list", "--first-parent",
             "--max-parents=0", "HEAD"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    roots = proc.stdout.split()
    if proc.returncode != 0 or len(roots) != 1:
        return None
    return f"{scar_id}-{roots[0][:16]}"


def promoted_record(scar: Scar) -> bytes:
    if scar.id is None:
        raise ValueError("promoted_record needs a scar with an id; promoted scars always have one")
    lines = [
        f"id: {scar.id}",
        f"type: {scar.type}",
        f"title: {scar.title}",
        f"severity: {scar.severity}",
    ]
    # Present only when set, so the bytes of every scar promoted before the
    # field existed are unchanged.
    if scar.promoted_from:
        lines.append(f"promoted_from: {scar.promoted_from}")
    lines.append("anchors:")
    for kind, values in (
        ("path", scar.path_anchors),
        ("pattern", scar.pattern_anchors),
        ("symbol", scar.symbol_anchors),
        ("command", scar.command_anchors),
    ):
        lines += [f"  - {kind}: {v}" for v in values]
    body = scar.body.replace("\r\n", "\n").replace("\r", "\n").rstrip()
    lines += ["---", body]
    return ("\n".join(lines) + "\n").encode("utf-8")


def record_hash(scar: Scar) -> str:
    """The hash string an act stores as `inputs_hash` for this scar's record:
    "u" plus unpadded base64url of 0x12 0x20 (sha256 multihash prefix) and the
    sha256 of promoted_record(). Computed from the file every time, never
    stored, so an act can be compared against the record it sits on."""
    digest = hashlib.sha256(promoted_record(scar)).digest()
    return "u" + base64.urlsafe_b64encode(b"\x12\x20" + digest).decode("ascii").rstrip("=")
