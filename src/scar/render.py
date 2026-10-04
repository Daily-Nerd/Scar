"""The ONE injection formatter — hooks, CLI, and MCP render scars identically.

Same single-source rule as the parser in model.py: format divergence between
surfaces is silent drift, so every block goes through here.
"""

from __future__ import annotations

from pathlib import Path

from .model import Scar

MAX_BODY_CHARS = 700  # ~120 words — the fatigue budget is a format guarantee


def label_line(scar: Scar) -> str:
    label = f"challenged {scar.type}" if scar.status == "challenged" else scar.type
    return (f"[{label} #{scar.id} | severity: {scar.severity} | "
            f"confidence: {scar.confidence}] {scar.title}")


def rule_line(body: str, max_chars: int = 140) -> str:
    """The one actionable line of a scar body: its first sentence (or first
    line, whichever ends sooner), trimmed. The unit of every compact tier —
    `scar brief` and the Cascade block text both render from here."""
    text = " ".join(body.split())
    cut = text.find(". ")
    rule = text[:cut + 1] if cut != -1 else text
    return rule[:max_chars]


def cut_total(shown: int, matched_total: int | None) -> int | None:
    """The pre-cap total, but only when it is a believable claim that the cap
    cut something (#321). None means no census (the target was outside the
    store, or the caller never counted), which is not a zero: the header then
    says nothing about a total. A total at or below what is shown cannot mean
    a cut, so it is dropped too rather than rendered as a nonsense ratio."""
    if matched_total is None or matched_total <= shown:
        return None
    return matched_total


def compact_block(scars: list[Scar]) -> str:
    """The most conservative tier: label line + one rule line per scar, no
    bodies. For channels where the render itself costs the user something —
    Cascade surfaces it as an error on a cancelled action — so the budget is
    tighter than the injection block's MAX_BODY_CHARS."""
    return "\n\n".join(f"{label_line(s)}\n  {rule_line(s.body)}" for s in scars)


def injection_context(scars: list[Scar], broken: list[Path],
                      scars_dir: Path, max_body: int = MAX_BODY_CHARS,
                      demoted: list[tuple[Scar, str]] | None = None,
                      matched_total: int | None = None) -> str:
    """The additionalContext payload: full matched blocks + demoted one-liners
    + broken-file warning. Demotion is visible, never silent (principle 3):
    a demoted scar keeps its label line plus the reason it was demoted.

    `matched_total` is the number of distinct scars that matched BEFORE the
    fatigue cap (#321). The cap is the one silent path, so when it cut, the
    header says how many were shown out of how many matched."""
    parts = []
    demoted = demoted or []
    total = len(scars) + len(demoted)
    if total:
        blocks = [f"{label_line(s)}\n{s.body[:max_body]}" for s in scars]
        blocks += [f"{label_line(s)} — {reason}; full record: `scar why` on the path"
                   for s, reason in demoted]
        cut = cut_total(total, matched_total)
        count = (f"{total} match(es)" if cut is None else
                 f"{total} of {cut} matched shown; the rest: `scar why` on the path")
        parts.append(
            "SCAR pre-edit check — negative knowledge anchored to code you are "
            f"about to modify ({count}). Honor these unless the "
            "user explicitly overrides; full records in .scars/.\n\n"
            + "\n\n".join(blocks))
    if broken:
        parts.append(
            f"SCAR warning: {len(broken)} scar file(s) unparseable and can NEVER "
            f"fire: {', '.join(b.name for b in broken)}. Their knowledge is "
            f"silently dead. Fix the frontmatter (copy {scars_dir}/template.md).")
    return "\n\n".join(parts)
