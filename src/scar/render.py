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


def injection_context(scars: list[Scar], broken: list[Path],
                      scars_dir: Path, max_body: int = MAX_BODY_CHARS) -> str:
    """The additionalContext payload: matched blocks + broken-file warning."""
    parts = []
    if scars:
        blocks = [f"{label_line(s)}\n{s.body[:max_body]}" for s in scars]
        parts.append(
            "SCAR pre-edit check — negative knowledge anchored to code you are "
            f"about to modify ({len(scars)} match(es)). Honor these unless the "
            "user explicitly overrides; full records in .scars/.\n\n"
            + "\n\n".join(blocks))
    if broken:
        parts.append(
            f"SCAR warning: {len(broken)} scar file(s) unparseable and can NEVER "
            f"fire: {', '.join(b.name for b in broken)}. Their knowledge is "
            f"silently dead. Fix the frontmatter (copy {scars_dir}/template.md).")
    return "\n\n".join(parts)
