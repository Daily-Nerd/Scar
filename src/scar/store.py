"""ScarStore — filesystem layer: discovery, listing, init, promotion.

All path conventions live here and nowhere else. README/template content is
embedded so `scar init` works from a bare install with no data files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .lint import lint_text
from .model import ParseError, Scar, parse_scar_text

SKIP_NAMES = {"readme.md", "template.md"}

README = """\
# .scars/ — Negative knowledge for this repo

This directory records what this codebase **refused to be**: approaches that
were tried and failed (`deadend`), configuration that looks wrong but is
intentional (`fence`), and changes that break non-obvious things elsewhere
(`landmine`).

Before "cleaning up" anything these files anchor to — read the scar first.
Every scar carries evidence (commits, PRs, incidents). If a scar is stale,
challenge it: update or archive it with a note, don't ignore it.

## The contract (humans and agents)

1. **New scars start as candidates.** Copy `template.md`, write to
   `candidates/<slug>.md` with `status: candidate`. Never write directly
   into `.scars/` — only a human reviewer promotes (`scar promote`).
2. **YAML frontmatter is mandatory.** A scar without it is unparseable and
   will NEVER fire in any tool. `scar lint` checks; the hooks warn loudly.
3. **Promotion** = human review: `scar promote candidates/<slug>.md`.
4. **Evidence required.** A scar without a commit/PR/incident reference is
   an opinion and can be challenged on sight.

Format details: `template.md`. Project: SCAR.
"""

TEMPLATE = """\
---
# COPY THIS FILE — do not edit the template itself.
# New scars: write to .scars/candidates/<slug>.md with status: candidate.
# A human reviewer promotes via `scar promote` (assigns id + renames).
id: 0                      # assigned at promotion
type: deadend              # deadend = tried+failed | fence = looks wrong, intentional | landmine = touching A breaks B
title: One line, searchable, says the constraint
severity: medium           # low | medium | high | critical
confidence: 0.7            # 0..1 — how sure are we this still holds
created: 1970-01-01
authors: ["claude-code"]   # reviewer added at promotion
anchors:
  - path: src/module/      # file or directory this protects
  - pattern: "regex"       # optional: fires when matching code appears in ANY new/edited file
evidence:
  - commit: abc1234        # at least one receipt: commit, pr, incident, or note
expires:
  condition: "what change would make this scar obsolete"
  review_after: 1971-01-01
status: template           # candidate | active | challenged | archived  (template = never parsed)
---

Body: 5-15 lines of prose. What was tried/observed, why it failed or why the
weirdness is intentional, and what a future editor must do instead. Write it
for someone (human or agent) with zero context. Cite the evidence inline.
"""


def init_scars(repo_root: Path) -> Path:
    """Create .scars/ layout. Idempotent; never clobbers existing files."""
    scars = Path(repo_root) / ".scars"
    scars.mkdir(exist_ok=True)
    (scars / "candidates").mkdir(exist_ok=True)
    for name, content in (("README.md", README), ("template.md", TEMPLATE)):
        f = scars / name
        if not f.exists():
            f.write_text(content, encoding="utf-8")
    return scars


@dataclass
class ScarStore:
    root: Path        # repo root
    scars_dir: Path   # root/.scars

    @classmethod
    def discover(cls, start: Path) -> "ScarStore | None":
        cur = Path(start).resolve()
        cur = cur if cur.is_dir() else cur.parent
        for d in [cur, *cur.parents]:
            if (d / ".scars").is_dir():
                return cls(root=d, scars_dir=d / ".scars")
            if (d / ".git").exists():
                return None
        return None

    def _scar_files(self):
        return [f for f in sorted(self.scars_dir.glob("*.md"))
                if f.name.lower() not in SKIP_NAMES and not f.name.startswith("_")]

    def parsed(self) -> list[tuple[Path, Scar]]:
        out = []
        for f in self._scar_files():
            try:
                out.append((f, parse_scar_text(f.read_text(encoding="utf-8"))))
            except (ParseError, OSError):
                continue
        return out

    def active(self) -> list[tuple[Path, Scar]]:
        return [(f, s) for f, s in self.parsed() if s.status == "active"]

    def firing(self) -> list[tuple[Path, Scar]]:
        """Scars that still inject: active, plus challenged (disputed but
        not yet resolved by a human — suppressing them would let a mere
        objection silently delete knowledge)."""
        return [(f, s) for f, s in self.parsed()
                if s.status in ("active", "challenged")]

    def broken(self) -> list[Path]:
        out = []
        for f in self._scar_files():
            try:
                parse_scar_text(f.read_text(encoding="utf-8"))
            except ParseError:
                out.append(f)
            except OSError:
                pass
        return out

    def candidates(self) -> list[Path]:
        cand = self.scars_dir / "candidates"
        return sorted(p for p in cand.glob("*.md")) if cand.is_dir() else []

    def next_id(self) -> int:
        ids = [scar.id for _, scar in self.active() if scar.id is not None]
        for f in self._scar_files():  # count non-active numbered scars too
            try:
                s = parse_scar_text(f.read_text(encoding="utf-8"))
                if s.id is not None:
                    ids.append(s.id)
            except (ParseError, OSError):
                continue
        return max(ids, default=0) + 1

    def transition(self, scar_id: int, new_status: str, reason: str, date: str) -> Path:
        """Flip a scar's status in place, appending the reason as an evidence
        note. The file keeps its name and id — archived/challenged scars stay
        findable (`scar why`); orphaned != deleted, ever."""
        for f, s in self.parsed():
            if s.id == scar_id:
                if s.status == new_status:
                    raise ValueError(f"scar #{scar_id} is already {new_status}")
                s.status = new_status
                s.evidence.append(f"note: {new_status} {date}: {reason}")
                f.write_text(s.to_text(), encoding="utf-8")
                return f
        raise ValueError(f"no scar with id {scar_id}")

    def promote(self, candidate: Path, reviewer: str) -> Path:
        text = candidate.read_text(encoding="utf-8")
        findings = lint_text(text)
        errors = [f for f in findings if f.level == "error"]
        if errors:
            raise ValueError(f"refusing to promote, lint errors: {'; '.join(f.message for f in errors)}")
        scar = parse_scar_text(text)
        scar.id = self.next_id()
        scar.status = "active"
        if reviewer and reviewer not in scar.authors:
            scar.authors.append(reviewer)
        slug = candidate.stem
        new_path = self.scars_dir / f"{scar.id:04d}-{slug}.{scar.type}.md"
        new_path.write_text(scar.to_text(), encoding="utf-8")
        candidate.unlink()
        return new_path
