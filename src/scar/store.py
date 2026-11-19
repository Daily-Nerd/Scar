"""ScarStore — filesystem layer: discovery, listing, init, promotion.

All path conventions live here and nowhere else. README/template content is
embedded so `scar init` works from a bare install with no data files.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .lint import lint_text
from .model import TYPES, ParseError, Scar, parse_scar_text

SLUG_MAX = 60
_TYPE_EXT_CHAIN = re.compile(r"(?:\.(?:%s))+$" % "|".join(TYPES))


def _strip_link_targets(s: str) -> str:
    """Drop markdown link targets and bare URLs. Shared by display-text cleaning
    and slug building: in both, a URL must not eat the text or the 60-char slug
    budget (#159)."""
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)  # [text](url) -> text
    return re.sub(r"https?://\S+", "", s)


_CODE_SPAN = re.compile(r"`([^`]*)`")
_PLACEHOLDER_CHARS = re.compile("[\ue000\ue001]")
_PLACEHOLDER = re.compile("\ue000(\\d+)\ue001")
# A run of `_`/`*` is an emphasis delimiter unless word characters sit on both
# sides of it (CommonMark's intraword rule). For `_` that is exact; `*` is
# treated the same way for simplicity, so `a*b` keeps its star although
# CommonMark would let it emphasise.
_EMPHASIS_RUN = re.compile(r"(?<!\w)[*_]+|[*_]+(?!\w)")


def sanitize_signal_text(s: str) -> str:
    """Clean mined git free text (revert subjects, squash-merge subjects) for
    use as a candidate title. Markdown links lose their targets, bare URLs go
    (#159), whitespace collapses, backticks are dropped but code-span content
    is kept verbatim, and `_`/`*` are removed only where they can be emphasis
    delimiters, never between two word characters, so `retry_count` survives
    (#402). Identifiers (keys, paths, components) must not be passed through
    here at all. This is DISPLAY text; a filename must not reuse the rule, see
    `slugify` (#365)."""
    # Mask code spans first so link and emphasis rules see the whole string
    # (a link may wrap a code span) while span content is never rewritten. The
    # placeholder is private-use, hence not \w, exactly like the backtick it
    # replaces, so adjacent `_`/`*` classify the same. Stray private-use
    # input is dropped so it cannot forge a placeholder.
    spans: list[str] = []

    def _mask(m: re.Match) -> str:
        spans.append(m.group(1))
        return f"\ue000{len(spans) - 1}\ue001"

    s = _PLACEHOLDER_CHARS.sub("", s)
    s = _CODE_SPAN.sub(_mask, s)
    s = _EMPHASIS_RUN.sub("", _strip_link_targets(s)).replace("`", "")
    s = _PLACEHOLDER.sub(lambda m: spans[int(m.group(1))], s)
    return " ".join(s.split())


def slugify(text: str, fallback: str = "scar") -> str:
    """The one slug normaliser (#352): every writer of a scar filename (promote,
    the MCP draft tool, harvest --write) goes through here so the rules cannot
    drift apart. Drop link targets/URLs, transliterate accents to ASCII (NFKD,
    combining marks dropped), lowercase, turn every other non-alnum run
    (including `_`, `*`, backticks) into one '-', cap at SLUG_MAX without
    leaving a sliced-mid-token fragment. Output stays ASCII on purpose:
    non-ASCII names can be stored in different normal forms per filesystem."""
    s = unicodedata.normalize("NFKD", _strip_link_targets(text))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    if len(s) > SLUG_MAX:
        cut = s[:SLUG_MAX]
        if s[SLUG_MAX] != "-" and "-" in cut:
            cut = cut.rsplit("-", 1)[0]
        s = cut.rstrip("-")
    return s or fallback


def active_slug(stem: str, scar_type: str) -> str:
    """Candidate stem -> slug for the active `NNNN-<slug>.<type>.md` name.
    A trailing `.{type}` chain (any type) is the extension leaking into the
    stem; a trailing `-{type}` is redundant only for the scar's own type."""
    stem = _TYPE_EXT_CHAIN.sub("", stem)
    slug = slugify(stem, fallback="scar")
    suffix = "-" + scar_type
    if slug.endswith(suffix):
        slug = slug[: -len(suffix)] or slug
    return slug

SKIP_NAMES = {"readme.md", "template.md"}

# #377: the ONE table of legal lifecycle moves. promote() and transition()
# both consult it, so no caller can make a status change the table does not
# name. archived is terminal on purpose: retired knowledge is brought back by
# writing a new scar, never by flipping a flag (revives_if only reports it).
LIFECYCLE_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "candidate": ("active", "archived"),
    "active": ("challenged", "archived", "orphaned"),
    "challenged": ("active", "archived", "orphaned"),
    "orphaned": ("active", "challenged", "archived"),
    "archived": (),
    "template": (),
}
# candidate -> active is promote()'s move alone: it needs a fresh id and a
# reviewer, which transition() has no way to supply.
PROMOTE_ONLY = {("candidate", "active")}


class UnparseableCandidate(ValueError):
    """A candidate whose frontmatter cannot be parsed. Still a ValueError so
    existing callers keep catching it; promote's lint step reports it."""


def _check_transition(old: str, new: str, *, via_promote: bool = False) -> None:
    if old == "archived":
        raise ValueError(
            f"status is archived, and archived is terminal (cannot become {new}). "
            "To bring the knowledge back, write a new candidate in "
            ".scars/candidates/ that cites the archived scar, then a human "
            "promotes it")
    if new not in LIFECYCLE_TRANSITIONS.get(old, ()) or (
            ((old, new) in PROMOTE_ONLY) != via_promote):
        raise ValueError(f"illegal lifecycle transition {old} -> {new}")

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
4. **Evidence required.** A scar without a pr/issue/url/commit/incident
   reference is an opinion and can be challenged on sight. Prefer durable
   pr/issue/url refs — feature-branch commit SHAs orphan on squash-merge.

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
# authors = the RUNTIME that DRAFTED it (claude-code, codex, cursor...). Replace the placeholder; never the human reviewer (promoted_by, written by promote).
authors: ["<your-runtime>"]
anchors:
  - path: src/module/      # file or directory this protects
  - pattern: "regex"       # optional: fires when matching code appears in ANY new/edited file
violation: "regex"         # optional: post-edit tripwire — added code matching this on anchored files = the scar was violated
revives_if: "regex"        # optional: for an ARCHIVED scar — what would make the hazard relevant again (reported, never auto-rearmed)
evidence:
  - pr: 123                # at least one receipt: pr, issue, url, commit, incident, or note
  # Prefer pr/issue/url over a bare commit: SHA — feature-branch SHAs orphan on squash-merge.
  - issue: 50
  - url: https://github.com/org/repo/commit/abc1234
expires:
  condition: "what change would make this scar obsolete"
  review_after: 1971-01-01
status: template           # candidate | active | challenged | archived  (template = never parsed)
---

Body: 5-15 lines of prose. What was tried/observed, why it failed or why the
weirdness is intentional, and what a future editor must do instead. Write it
for someone (human or agent) with zero context. Cite the evidence inline.
"""


EXAMPLE_SEED_NAME = "example-first-scar.md"

# A real, lint-clean candidate seeded on FRESH init only (#136 item 3): shows
# the format, the path anchor, and the candidate -> promote lifecycle before
# any real pain accumulates. status: candidate means it never injects and is
# never orphan-checked. It carries NO pattern anchor on purpose: a candidate
# pattern that matches no tracked file draws a lint warning (#404), and the
# seed must not demonstrate a shape lint calls a mistake.
EXAMPLE_SEED = """\
---
type: fence
title: Example scar — this is what recorded negative knowledge looks like
severity: low
confidence: 1.0
authors: ["scar-init"]
anchors:
  - path: .scars/candidates/example-first-scar.md
evidence:
  - note: seeded by `scar init` as a worked example
status: candidate
---

A scar records something a past attempt PROVED: an approach that failed
(deadend), weirdness that is intentional (fence), or a change here that breaks
something there (landmine). Agents get matching scars injected before they
edit anchored code — pain recorded once, never repeated.

This file is a live example of the format. The `path:` anchor points at a file
(this one). A `pattern:` anchor fires when matching code appears in any edit,
and lint warns if it matches nothing. `status: candidate` means it does NOT
inject yet — a human reviews and runs `scar promote example-first-scar.md` to
activate it, which assigns an id and moves it to `.scars/`.

Delete this file whenever you like — it exists only to be read once.
"""


def init_scars(repo_root: Path) -> Path:
    """Create .scars/ layout. Idempotent; never clobbers existing files."""
    scars = Path(repo_root) / ".scars"
    scars.mkdir(exist_ok=True)
    ScarStore(root=Path(repo_root), scars_dir=scars).ensure_candidates_dir()
    for name, content in (("README.md", README), ("template.md", TEMPLATE)):
        f = scars / name
        if not f.exists():
            f.write_text(content, encoding="utf-8")
    return scars


@dataclass
class PreparedPromotion:
    """A promotion that exists only in memory: the candidate it came from, the
    scar to write (id and lifecycle already set) and where it will land."""
    candidate: Path
    scar: Scar
    destination: Path


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

    def seen_receipts(self) -> list[str]:
        """Receipt ids of every scar's promotion act, whatever its status
        (archived and orphaned included), for the checker's `seen` list: an act
        already used must stay refused after its scar retires, so one act
        cannot be replayed for another scar. Sorted and unique, so the checker
        input does not depend on file order. A file that cannot be parsed is
        skipped with the rest of parsed(): its receipt cannot be read."""
        return sorted({s.promotion_receipt for _f, s in self.parsed() if s.promotion_receipt})

    def active(self) -> list[tuple[Path, Scar]]:
        return [(f, s) for f, s in self.parsed() if s.status == "active"]

    def firing(self) -> list[tuple[Path, Scar]]:
        """Scars that still inject: active, plus challenged (disputed but
        not yet resolved by a human — suppressing them would let a mere
        objection silently delete knowledge)."""
        return [(f, s) for f, s in self.parsed()
                if s.status in ("active", "challenged")]

    def scan(self) -> tuple[list[tuple[Path, Scar]], list[Path]]:
        """One directory pass returning (firing, broken) together (#186).
        The hook hot path needs both; calling firing() then broken() re-reads
        and re-parses every scar file a second time per edit. Deliberately a
        fresh read, not an instance cache — commands that write scars and
        re-read through the same store must never see stale results."""
        firing: list[tuple[Path, Scar]] = []
        broken: list[Path] = []
        for f in self._scar_files():
            try:
                scar = parse_scar_text(f.read_text(encoding="utf-8"))
            except ParseError:
                broken.append(f)
                continue
            except OSError:
                continue
            if scar.status in ("active", "challenged"):
                firing.append((f, scar))
        return firing, broken

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

    @property
    def candidates_dir(self) -> Path:
        return self.scars_dir / "candidates"

    def ensure_candidates_dir(self) -> Path:
        """The one place candidates/ is created (#370). Idempotent. Also keeps
        an empty .gitkeep so the folder survives the last promotion in git;
        candidates() and lint only look at *.md, so it is never a candidate."""
        cand = self.candidates_dir
        cand.mkdir(exist_ok=True)
        keep = cand / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
        return cand

    def candidates(self) -> list[Path]:
        cand = self.candidates_dir
        return sorted(p for p in cand.glob("*.md")) if cand.is_dir() else []

    def pending_candidates(self) -> list[Path]:
        """Candidates still awaiting review. An archived candidate stays in
        candidates/ (history kept, lint still checks it) but is not pending.
        An unparseable file is kept: it still needs a human."""
        out = []
        for p in self.candidates():
            try:
                status = parse_scar_text(p.read_text(encoding="utf-8")).status
            except (ParseError, OSError):
                status = "candidate"
            if status != "archived":
                out.append(p)
        return out

    def archive_candidate(self, candidate: Path, reason: str, date: str) -> Path:
        """Retire a candidate in place: same file, same name, status archived,
        reason recorded as an evidence note (as transition() does for numbered
        scars). It stays in candidates/ so its history sits next to its
        siblings; the table makes archived terminal, so promote() refuses it."""
        candidate = self.candidate_path(candidate)
        scar = self._parse_candidate_file(candidate)
        _check_transition(scar.status, "archived")
        scar.status = "archived"
        scar.evidence.append(f"note: archived {date}: {reason}")
        candidate.write_text(scar.to_text(), encoding="utf-8")
        return candidate

    def candidate_path(self, path: Path) -> Path:
        """The one owner of "is this file inside .scars/candidates/" (#394).
        Returns *path* resolved (cwd-relative, dotted and symlinked spellings
        all collapse to one absolute path), or raises ValueError. Every
        lifecycle step that takes a candidate goes through here, so a path
        typed any way gets one answer and the same recorded form."""
        try:
            resolved = Path(path).resolve()
            inside = resolved.is_relative_to(self.candidates_dir.resolve())
        except OSError:
            inside = False
        if not inside:
            raise ValueError(
                f"{Path(path).name}: not inside {self.candidates_dir.name}/")
        return resolved

    def _parse_candidate_file(self, path: Path) -> Scar:
        path = self.candidate_path(path)
        try:
            return parse_scar_text(path.read_text(encoding="utf-8"))
        except (ParseError, OSError) as exc:
            raise UnparseableCandidate(f"{path.name}: unreadable ({exc})") from None

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

    def scars_for_path(self, rel: str) -> list[tuple[Path, Scar]]:
        """History of pain for a repo-relative path, any status. Bidirectional:
        query under an anchor (editing inside a protected dir) OR anchor under
        the query (asking a parent dir for its history)."""
        rel = rel.rstrip("/")
        return [(f, s) for f, s in self.parsed()
                if any(rel.startswith(p.rstrip("/")) or p.rstrip("/").startswith(rel)
                       for p in s.path_anchors)]

    def transition(self, scar_id: int, new_status: str, reason: str, date: str) -> Path:
        """Flip a scar's status in place, appending the reason as an evidence
        note. The file keeps its name and id — archived/challenged scars stay
        findable (`scar why`); orphaned != deleted, ever."""
        for f, s in self.parsed():
            if s.id == scar_id:
                if s.status == new_status:
                    raise ValueError(f"scar #{scar_id} is already {new_status}")
                try:
                    _check_transition(s.status, new_status)
                except ValueError as exc:
                    raise ValueError(f"scar #{scar_id}: {exc}") from None
                s.status = new_status
                s.evidence.append(f"note: {new_status} {date}: {reason}")
                f.write_text(s.to_text(), encoding="utf-8")
                return f
        raise ValueError(f"no scar with id {scar_id}")

    def append_evidence_note(self, scar_id: int, note: str) -> Path:
        """Append *note* as a new evidence entry on a scar, leaving status
        and every other field untouched. Same parse -> append -> reserialize
        mechanism transition() uses for its own note (#296): an existing
        evidence: list gains an entry and keeps the ones it already had, a
        scar with no evidence: block gets one, both via Scar.to_text()'s own
        rendering (nothing here special-cases either shape)."""
        for f, s in self.parsed():
            if s.id == scar_id:
                s.evidence.append(note)
                f.write_text(s.to_text(), encoding="utf-8")
                return f
        raise ValueError(f"no scar with id {scar_id}")

    def check_promotable(self, candidate: Path) -> None:
        """Raise ValueError unless *candidate* may be promoted. Touches
        nothing: no write, no unlink, no id allocated (#377). The CLI runs it
        over a whole batch before promoting any file of it."""
        try:
            current = self._parse_candidate_file(candidate)
        except UnparseableCandidate:
            current = None  # unparseable: lint below reports it as an error
        if current is not None:
            try:
                _check_transition(current.status, "active", via_promote=True)
            except ValueError:
                raise ValueError(
                    f"refusing to promote {candidate.name}: status is "
                    f"{current.status}, only candidate can be promoted") from None
        findings = lint_text(candidate.read_text(encoding="utf-8"))
        errors = [f for f in findings if f.level == "error"]
        if errors:
            raise ValueError(f"refusing to promote, lint errors: {'; '.join(f.message for f in errors)}")

    def _destination(self, candidate: Path, scar: Scar, scar_id: int) -> Path:
        """Where promoting *candidate* as *scar_id* will land. Raises if that
        name is taken (#352: never clobber an existing active scar)."""
        slug = active_slug(candidate.stem, scar.type)
        new_path = self.scars_dir / f"{scar_id:04d}-{slug}.{scar.type}.md"
        if new_path.exists():
            raise ValueError(
                f"refusing to promote {candidate.name}, active file already "
                f"exists: {new_path.name}")
        return new_path

    def plan_promotions(self, candidates: list[Path]) -> tuple[list[Path], list[str]]:
        """Destinations for a batch, in order, computed with the ids promote
        will hand out (next_id() + position) and without writing anything.
        Returns (destinations, errors); a non-empty errors list means the
        batch must not run. Callers validate each file with check_promotable
        first. Ids in a batch are distinct, so only an existing file can collide."""
        base = self.next_id()
        dests: list[Path] = []
        errors: list[str] = []
        for i, cand in enumerate(candidates):
            try:
                scar = self._parse_candidate_file(cand)
                dest = self._destination(cand, scar, base + i)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            dests.append(dest)
        return dests, errors

    def prepare_promotion(self, candidate: Path, reviewer: str,
                          reviewer_source: str | None = None) -> "PreparedPromotion":
        """Everything promote will write, computed and written nowhere: no
        file, no unlink, no id used up (#392). A signing step can sit between
        this and commit_promotion without a half-promoted state to undo."""
        # #377: gate on the lifecycle table BEFORE anything is allocated or
        # written, so a refusal changes no file and burns no id.
        self.check_promotable(candidate)
        candidate = self.candidate_path(candidate)
        text = candidate.read_text(encoding="utf-8")
        scar = parse_scar_text(text)
        scar.id = self.next_id()
        scar.status = "active"
        # #287: the reviewer is who VOUCHED, its own key. Never appended to
        # authors (that was #182's dedup, which an agent could turn into a
        # silent no-op by pre-seeding the reviewer's handle), and always
        # OVERWRITTEN: a candidate cannot have been promoted, so whatever it
        # carried here was a claim nobody made. Empty reviewer stays empty
        # rather than guessing.
        scar.promoted_by = reviewer or ""
        # #295: reviewer_source ("explicit" | "git-config") records whether
        # the CLI got the name from --reviewer or from git config user.name.
        # Only meaningful next to an actual reviewer, and always overwritten
        # like promoted_by itself: whatever the candidate carried here was
        # never a real vouch either.
        scar.promoted_by_source = reviewer_source if reviewer else None
        # Always overwritten, like promoted_by: only promote knows the real
        # source. candidate is already resolved and known to sit in
        # candidates/, so this is the same string however the path was typed.
        scar.promoted_from = candidate.relative_to(
            self.scars_dir.resolve()).as_posix()
        # Like promoted_by, cleared: a candidate cannot have been signed, so an
        # act or receipt it carries is a claim nobody made (an agent can write
        # one). Only the signing step sets them, on the prepared scar.
        scar.promotion_act = ""
        scar.promotion_receipt = ""
        return PreparedPromotion(
            candidate=candidate, scar=scar,
            destination=self._destination(candidate, scar, scar.id))

    def commit_promotion(self, prepared: "PreparedPromotion") -> Path:
        """Write the prepared scar, then remove its candidate. The write is an
        exclusive create: a file that appeared since prepare_promotion is never
        overwritten (#352), and a refusal leaves the candidate in place."""
        try:
            with open(prepared.destination, "x", encoding="utf-8") as fh:
                fh.write(prepared.scar.to_text())
        except FileExistsError:
            raise ValueError(
                f"refusing to promote {prepared.candidate.name}, active file already "
                f"exists: {prepared.destination.name}") from None
        prepared.candidate.unlink()
        return prepared.destination

    def promote(self, candidate: Path, reviewer: str,
                reviewer_source: str | None = None) -> Path:
        return self.commit_promotion(
            self.prepare_promotion(candidate, reviewer, reviewer_source))
