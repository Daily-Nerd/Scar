"""The bytes a promotion signs: the only producer of a promoted record (#382).

Signing at promotion and verifying later must see identical bytes from the same
scar, so every caller goes through promoted_record() and nothing else rebuilds
them. The record holds only what a person decides (id, type, title, severity,
anchors, body), built from parsed values so frontmatter quoting, key order and
line endings cannot change it. Lifecycle fields are never included.

Anchors are in on purpose: moving one changes where the scar fires. Nothing
here refuses an oversize record: the signer does that before any confirmation,
and render_fit only predicts it so an author hears about it while drafting.
"""
from __future__ import annotations

import base64
import hashlib
import subprocess
from dataclasses import dataclass
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


# The signer's render limits for the block that shows a record (#419). It
# refuses, never truncates, so a record over either cannot be signed.
MAX_RECORD_BYTES = 2048
MAX_RECORD_LINES = 32
# An escaped line wraps into continuation lines of at most this many characters.
_WRAP_WIDTH = 96


@dataclass(frozen=True)
class RenderFit:
    bytes: int
    lines: int

    @property
    def bytes_over(self) -> int:
        return max(0, self.bytes - MAX_RECORD_BYTES)

    @property
    def lines_over(self) -> int:
        return max(0, self.lines - MAX_RECORD_LINES)

    @property
    def fits(self) -> bool:
        return self.bytes_over == 0 and self.lines_over == 0


def _block_lines(raw: bytes) -> int:
    """Block lines the signer renders for `raw`. Per source line: a printable
    ASCII byte (0x20 to 0x7E) is one character, any other byte is the four
    characters `\\xHH`; an escape is never split, so a line wraps before the
    escape that would pass the width. A final LF ends the last line, it does
    not start another; an empty line still takes one block line."""
    pieces = raw.split(b"\n")
    if pieces[-1] == b"":
        pieces.pop()
    total = 0
    for piece in pieces:
        total += 1
        size = 0
        for byte in piece:
            width = 1 if 0x20 <= byte <= 0x7E else 4
            if size + width > _WRAP_WIDTH:
                total += 1
                size = 0
            size += width
    return total


def render_fit(scar: Scar) -> RenderFit:
    """Size of the record a promotion would sign, in the two units the signer
    limits. Like promoted_record it needs the scar as it will be promoted (an
    id, and promoted_from when promote sets it)."""
    raw = promoted_record(scar)
    return RenderFit(bytes=len(raw), lines=_block_lines(raw))
