"""scar — the CLI. Thin argparse layer; all logic lives in the library.

Adding a command = one _cmd_* function + one subparser block. Commands that
read scars resolve the store once via _require_store; commands return exit
codes (0 ok, 1 user-visible failure) and never raise to the shell.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version as _dist_version
from pathlib import Path

from rich_argparse import RichHelpFormatter

from .lint import _is_redos_prone, lint_text
from .match import (
    find_violations_for_diff,
    has_content_signal,
    rank_matches_for_diff,
    rank_matches_for_edit,
    rank_matches_for_paths,
)
from .model import Scar, parse_scar_text
from .evidence import _git, unreachable_evidence
from .orphan import (
    GitError,
    anchors_all_dead,
    build_repo_context,
    detect_orphans,
    detect_partial_rot,
    detect_revivals,
    detect_symbol_drift,
)
from .reanchor import (
    dead_symbol_anchors,
    format_reanchor_note,
    propose_path_reanchors,
    propose_symbol_reanchors_for_scar,
)
from .render import injection_context, label_line, rule_line
from .renames import apply_anchor_rewrite, apply_rename_fix
from .store import ScarStore, init_scars
from . import output


def _require_store(start: Path | None = None) -> ScarStore | None:
    store = ScarStore.discover(start or Path.cwd())
    if store is None:
        print("no .scars/ directory found (walked up to repo root). Run: scar init")
    return store


def _has_git_history(repo: Path) -> bool:
    """True iff the repo has at least one commit. `git rev-parse --verify HEAD`
    fails both outside git and in a zero-commit repo — either way there is
    nothing for harvest to mine, so init keeps its legacy two-line output."""
    import subprocess
    try:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
                              capture_output=True).returncode == 0
    except OSError:
        return False


def _cmd_init(args) -> int:
    from .store import EXAMPLE_SEED, EXAMPLE_SEED_NAME
    fresh = not (Path.cwd() / ".scars").is_dir()
    scars = init_scars(Path.cwd())
    # Example seed (#136 item 3): FRESH init only — the user deleting it is a
    # decision, not damage, so re-running init never resurrects it.
    seeded = False
    if fresh and not getattr(args, "no_seed", False):
        example = scars / "candidates" / EXAMPLE_SEED_NAME
        example.write_text(EXAMPLE_SEED, encoding="utf-8")
        seeded = True
    has_history = _has_git_history(Path.cwd())
    data = {"scars": str(scars), "seeded": seeded,
            "example": EXAMPLE_SEED_NAME, "has_history": has_history}

    def plain():
        print(f"initialized {scars} (README.md, template.md, candidates/)")
        print("convention: new scars -> candidates/, humans promote via `scar promote`")
        if seeded:
            print(f"seeded a worked example: candidates/{EXAMPLE_SEED_NAME} "
                  "(read once, delete anytime; --no-seed skips)")
        if has_history:
            print("next steps (this repo has minable history):")
            print("  preview candidates:  scar harvest --top-k 10")
            print("  write top drafts:    scar harvest --write 5  (-> .scars/candidates/, review + promote)")
            print("  wire your agent:     scar hook install        (Claude Code)")
            print("                       scar hook install --git  (any agent, git-native)")

    output.render(data=data, json_flag=False,
                  tty=lambda: _init_rich(data), plain=plain)
    return 0


def _dead_path_text(finding, anchor: str) -> str:
    """One dead path anchor's display text — 'renamed: old -> new' when git
    resolved an unambiguous, currently-tracked rename target (#109), else the
    bare anchor (plain dead, e.g. deleted-not-renamed)."""
    target = finding.renamed.get(anchor)
    return f"renamed: {anchor} -> {target}" if target else anchor


def _dead_anchor_summary(finding) -> str:
    """Shared rendering of a finding's dead anchors — used by both orphan and
    partial-rot surfaces so the two never drift on how a dead anchor reads."""
    dead = []
    if finding.dead_path_anchors:
        dead.append("paths: " + ", ".join(
            _dead_path_text(finding, p) for p in finding.dead_path_anchors))
    if finding.dead_pattern_anchors:
        dead.append("patterns: " + ", ".join(f"/{p}/" for p in finding.dead_pattern_anchors))
    # Dead top-level alternation branches (#213). getattr: OrphanFinding has no
    # such field — an all-dead scar's branches are moot, and the summary is
    # shared with the orphan surface.
    branches = getattr(finding, "dead_pattern_branches", None)
    if branches:
        dead.append("pattern branches: " + ", ".join(f"/{b}/" for b in branches))
    return "; ".join(dead)


def _violation_migration_hint(finding) -> str:
    """Appended when a finding's dead anchors include a pattern (#156): a
    zero-match pattern is ALWAYS reported as rot — never silently 'armed' —
    but a pattern meant to guard against REINTRODUCTION (healthy when absent)
    has a proper home in the violation: field, where re-adding the forbidden
    shape is logged instead of the anchor reading dead."""
    if not finding.dead_pattern_anchors:
        return ""
    return (" — hint: a pattern that guards against reintroduction (healthy "
            "when absent) belongs in violation:, not anchors")


def _orphan_reason(finding) -> str:
    """Human description of why a finding is an orphan — distinguishes a scar
    with NO anchors (protects nothing) from one whose every anchor went dead."""
    if not finding.dead_path_anchors and not finding.dead_pattern_anchors:
        return "no anchors — scar protects nothing"
    return ("all anchors dead (" + _dead_anchor_summary(finding) + ")"
            + _violation_migration_hint(finding))


def _partial_rot_reason(finding) -> str:
    """Human description of partial rot — which specific anchors went dead while
    the scar keeps firing on its survivors (#35)."""
    return ("partial rot — dead anchor(s) (" + _dead_anchor_summary(finding) + ")"
            + _violation_migration_hint(finding))


def _symbol_drift_reason(finding) -> str:
    """Human description of symbol drift — a symbol anchor that still resolves
    by name but whose body shape changed since the scar's evidence commit."""
    pct = round(finding.similarity * 100)
    return f"{finding.symbol} ~{pct}% similar since {finding.sha[:7]}"


# ---------------------------------------------------------------------------
# Rich renderers (Issue #78). These run ONLY on a real tty — never under capsys
# or when piped, where the legacy plain output is the byte-preserved contract.
# They consume the same structured data the --json branch emits (or, for check/
# why, the parsed Scar objects) so the three surfaces never drift in substance.
# ---------------------------------------------------------------------------
_TYPE_STYLE = {"deadend": "red", "fence": "yellow", "landmine": "magenta"}


def _type_label(t: str) -> str:
    return f"[{_TYPE_STYLE.get(t, 'white')}]{t}[/]"


def _init_rich(data: dict) -> None:
    from rich.panel import Panel
    lines = [f"initialized [bold]{data['scars']}[/] (README.md, template.md, candidates/)",
             "convention: new scars -> [bold]candidates/[/], humans promote via `scar promote`"]
    if data["seeded"]:
        lines.append(f"seeded a worked example: [bold]candidates/{data['example']}[/] "
                     "(read once, delete anytime; --no-seed skips)")
    if data["has_history"]:
        lines += ["", "[bold]next steps[/] (this repo has minable history):",
                  "  preview candidates:  [cyan]scar harvest --top-k 10[/]",
                  "  write top drafts:    [cyan]scar harvest --write 5[/]  "
                  "(-> .scars/candidates/, review + promote)",
                  "  wire your agent:     [cyan]scar hook install[/]        (Claude Code)",
                  "                       [cyan]scar hook install --git[/]  (any agent, git-native)"]
    output.console.print(Panel("\n".join(lines), title="scar init", border_style="green"))


def _promote_rich(data: dict) -> None:
    from rich.panel import Panel
    lines = [f"[green]promoted[/] -> [bold]{data['promoted']}[/]"]
    if data["reviewer_from_git"]:
        lines.append(f"reviewer: {data['reviewer']} [dim](from git config)[/]")
    if data["born_orphan"]:
        lines.append("[yellow]advisory:[/] this scar's anchors resolve to nothing in the "
                     "current tree (born orphan-detected) — confirm the anchors are right")
    if data.get("reviewer_case_warning"):
        lines.append("[yellow]warning:[/] --reviewer differs from git user.name only "
                     "by case — pick one spelling or author identity drifts (#182)")
    output.console.print(Panel("\n".join(lines), title="scar promote", border_style="green"))


def _harvest_rich(repo_name: str, total: int, result: dict) -> None:
    # Text (not markup) for candidate lines — they carry literal [brackets]
    # that rich markup would eat (same trap the recall lines hit in daimon).
    from rich.panel import Panel
    from rich.text import Text
    console = output.console
    console.print(Panel(f"{total} raw candidate(s) — curation required, "
                        "expect ~13% precision",
                        title=f"scar harvest — {repo_name}", border_style="cyan"))
    for key, title, _fmt in _HARVEST_SECTIONS:
        console.print(f"[bold]{title}[/] ({len(result[key])})")
        for c in result[key]:
            console.print(Text(_harvest_line(key, c)))
        console.print("")


def _harvest_top_rich(repo_name: str, total: int, top) -> None:
    from rich.panel import Panel
    from rich.text import Text
    console = output.console
    console.print(Panel(f"top {len(top)} of {total} raw, cross-section by raw score — "
                        "curation required, expect ~13% precision",
                        title=f"scar harvest — {repo_name}", border_style="cyan"))
    for key, c in top:
        console.print(Text(_harvest_line(key, c)))


def _agent_doctor_rich(lines) -> None:
    from rich.panel import Panel
    from rich.text import Text
    body = Text()
    for i, line in enumerate(lines):
        if i:
            body.append("\n")
        bad = "missing" in line or "not found" in line or "no-op" in line
        body.append(line, style="yellow" if bad else "")
    output.console.print(Panel(body, title="scar agent doctor", border_style="cyan"))


def _status_rich(data: dict) -> None:
    from rich.panel import Panel
    from rich.table import Table

    console = output.console
    c = data["counts"]
    console.print(Panel.fit(
        f"[bold]{data['scars_dir']}[/]\n"
        f"{c['active']} active · {c['candidates']} candidate(s) pending review",
        title="scar status"))

    if data["active"]:
        t = Table(title="Active scars", show_edge=False, expand=False)
        t.add_column("type")
        t.add_column("id", justify="right")
        t.add_column("severity")
        t.add_column("title")
        for s in data["active"]:
            t.add_row(_type_label(s["type"]), f"#{s['id']}", s["severity"], s["title"])
        console.print(t)
    for s in data["challenged"]:
        console.print(f"  [dim]challenged[/] {_type_label(s['type'])} #{s['id']} {s['title']}")
    for name in data["candidates"]:
        console.print(f"  [cyan]candidate:[/] {name}")
    for s in data["review_due"]:
        console.print(f"  [yellow]REVIEW DUE[/] {_type_label(s['type'])} #{s['id']} "
                      f"review_after {s['review_after']}")

    for r in data["firing_review"]:
        console.print(f"  [yellow]REVIEW DUE[/] {_type_label(r['type'])} #{r['id']} "
                      f"{r['reason']}")

    console.print(f"  [bold]{c['orphan_detected']}[/] orphan-detected · "
                  f"[bold]{c['orphaned']}[/] orphaned (persisted) · "
                  f"[bold]{c['partial_rot']}[/] partial-rot")
    for o in data["orphan_detected"]:
        console.print(f"    [red]orphan-detected[/] [#{o['scar_id']}] {o['reason']}")
    for s in data["orphaned"]:
        console.print(f"    [dim]orphaned[/] {_type_label(s['type'])} #{s['id']} {s['title']}")
    for pr in data["partial_rot"]:
        console.print(f"    [yellow]partial-rot[/] [#{pr['scar_id']}] {pr['reason']}")
    if data["broken"]:
        console.print(f"  [bold red]WARNING:[/] {len(data['broken'])} unparseable "
                      f"(can NEVER fire): " + ", ".join(data["broken"]))


def _lint_rich(data: dict) -> None:
    from rich.table import Table

    console = output.console
    if data["findings"]:
        t = Table(title="Lint findings", show_edge=False)
        t.add_column("file")
        t.add_column("level")
        t.add_column("message")
        for f in data["findings"]:
            style = "red" if f["level"] == "error" else "yellow"
            t.add_row(f["file"], f"[{style}]{f['level']}[/]", f["message"])
        console.print(t)
    for o in data["orphans"]:
        console.print(f"[yellow]WARNING orphan-detected:[/] scar #{o['scar_id']} — {o['reason']}")
    for pr in data["partial_rot"]:
        console.print(f"[cyan]HINT partial-rot:[/] scar #{pr['scar_id']} — {pr['reason']}")
    for d in data["symbol_drift"]:
        console.print(f"[magenta]HINT symbol-drift:[/] scar #{d['scar_id']} — "
                      f"{d['symbol']} ~{round(d['similarity'] * 100)}% similar since {d['sha'][:7]}")
    for h in data["reverse_hints"]:
        console.print(f"[cyan]HINT:[/] scar #{h['id']} marked orphaned but anchors live again")
    for r in data["firing_review"]:
        console.print(f"[yellow]WARNING firing-review:[/] scar #{r['scar_id']} — "
                      f"{r['reason']}")
    if data["shallow_clone"]:
        console.print("[dim]note: shallow clone — evidence-reachability check skipped[/]")
    for ue in data["unreachable_evidence"]:
        console.print(f"[yellow]WARNING evidence-unreachable:[/] scar #{ue['scar_id']} — "
                      f"commit {ue['sha']} {ue['reason']}")
    style = "red" if data["failed"] else "green"
    console.print(f"[{style}]lint:[/] {data['files']} file(s), {data['failed']} with errors, "
                  f"{len(data['orphans'])} orphan(s), {len(data['partial_rot'])} partial-rot, "
                  f"{len(data['unreachable_evidence'])} unreachable-evidence, "
                  f"{len(data['firing_review'])} firing-review")


def _check_rich(label: str, hits, violations=()) -> None:
    from rich.panel import Panel

    console = output.console
    if not hits:
        console.print(f"[green]no scars anchored to[/] {label}")
    else:
        for s in hits:
            title = (f"{_type_label(s.type)} #{s.id} · severity: {s.severity} · "
                     f"confidence: {s.confidence}")
            console.print(Panel(s.body[:200].strip(), title=title,
                                subtitle=f"[bold]{s.title}[/]", title_align="left"))
    for v in violations:
        console.print(f"[red]VIOLATION[/] scar #{v.scar.id} on {v.path}: {v.scar.title}")


def _why_rich(rel: str, records) -> None:
    from rich.panel import Panel

    console = output.console
    if not records:
        console.print(f"[green]no recorded pain for[/] {rel}")
        return
    console.print(f"[bold]History of pain for[/] {rel}")
    for f, s in records:
        title = f"[{s.status}] {_type_label(s.type)} #{s.id} — {s.title}"
        console.print(Panel(s.body[:300].strip(), title=title, subtitle=f"[dim]{f.name}[/]",
                            title_align="left"))


def _stats_rich(data: dict) -> None:
    from rich.panel import Panel
    from rich.table import Table

    console = output.console
    console.print(Panel.fit(
        f"{data['total_firings']} firing(s) recorded — {data['repo']}\n"
        f"most-fired: {'#' + str(data['most_fired']) if data['most_fired'] is not None else '(none yet)'} · "
        f"last fired: {data['last_fired'] or '(never)'}",
        title="scar stats"))
    if data["per_scar"]:
        t = Table(title="Per-scar firing counts", show_edge=False, expand=False)
        t.add_column("id", justify="right")
        t.add_column("firings", justify="right")
        for e in data["per_scar"]:
            row = f"#{e['id']}", str(e["count"])
            if e.get("violations", 0) > 0:
                row = (f"#{e['id']}", f"{e['count']} (violations: x{e['violations']})")
            t.add_row(*row)
        console.print(t)
    if data["never_fired"]:
        console.print("[yellow]never fired:[/] "
                      + ", ".join(f"#{i}" for i in data["never_fired"]))
    # Health first (#237): if the instrument is disconnected the stages below
    # cannot be believed, so the reader must meet that before the numbers.
    for line in _health_lines(data):
        console.print(f"[red]{line}[/]" if data["instrument_disconnected"] else line)
    # The three measurement stages (#214). These must stay in step with the
    # plain renderer — shipping them to one branch only is how #225 happened.
    for line in _stage_lines(data, data["per_scar"], data["total_firings"]):
        console.print(line)
    for adv in data.get("advisories", []):
        console.print(f"[red]advisory:[/] scar #{adv['id']} accounts for "
                      f"{int(adv['share'] * 100)}% of firings — {adv['note']}")
    console.print(f"[dim]{RETRIEVAL_FLOOR_NOTE}[/]")
    console.print("[dim]note: firing + violation counts — whether the agent honored an "
                  "injected scar is unobservable from inside the hook[/]")


def _stats_all_repos_rich(data: dict, log_path: Path) -> None:
    from rich.panel import Panel
    from rich.table import Table

    console = output.console
    console.print(Panel.fit(
        f"{len(data['repos'])} repo(s) recorded in {log_path}",
        title="scar stats --all-repos"))
    for g in data["repos"]:
        t = Table(title=f"{g['repo']} — {g['total_firings']} firing(s)",
                  show_edge=False, expand=False)
        t.add_column("id", justify="right")
        t.add_column("firings", justify="right")
        for e in g["per_scar"]:
            row = f"#{e['id']}", str(e["count"])
            if e.get("violations", 0) > 0:
                row = (f"#{e['id']}", f"{e['count']} (violations: x{e['violations']})")
            t.add_row(*row)
        console.print(t)
        for line in _health_lines(g):
            console.print(f"  [red]{line}[/]" if g["instrument_disconnected"]
                          else f"  {line}")
        for line in _stage_lines(g, g["per_scar"], g["total_firings"]):
            console.print(f"  {line}")
    console.print("[dim]note: ids are per-repo — the same number in two repos "
                  "is two different scars[/]")
    console.print(f"[dim]{RETRIEVAL_FLOOR_NOTE}[/]")


def _gc_rich(data: dict, *, days: int, max_firings: int, state_dir: Path) -> None:
    from rich.panel import Panel
    from rich.table import Table

    console = output.console
    verb, fverb = ("would remove", "would drop") if data["dry_run"] else ("removed", "dropped")
    console.print(Panel.fit(
        f"{verb} {data['removed_markers']} stale marker(s) (older than {days}d)\n"
        f"{fverb} {data['dropped_firings']} firing-log line(s) (kept newest {max_firings})\n"
        f"[dim]{state_dir}[/]",
        title="scar gc" + (" (dry-run)" if data["dry_run"] else "")))

    candidates = data["candidates"]
    if candidates:
        t = Table(title="Candidates pending review (oldest first)",
                  show_edge=False, expand=False)
        t.add_column("name")
        t.add_column("age (days)", justify="right")
        for c in candidates:
            t.add_row(c["name"], str(c["age_days"]))
        console.print(t)
        console.print("[dim]review with `scar promote <path>` or delete rejected files[/]")
    else:
        console.print("[dim]0 candidates pending review[/]")

    fp_log = data["fp_log"]
    if fp_log["present"]:
        console.print(f"[dim]fp-log.txt: {fp_log['lines']} line(s), {fp_log['size']} byte(s) "
                      "— drafter-precision instrument data, not auto-cleaned[/]")
    else:
        console.print("[dim]fp-log.txt: not present[/]")


def _orphan_rich(findings, partial) -> None:
    console = output.console
    if not findings:
        console.print("[green]no orphan-detected scars[/]")
    else:
        for of in findings:
            console.print(f"[red]orphan-detected[/] [#{of.scar_id}] {_orphan_reason(of)}")
        console.print(f"[bold]{len(findings)}[/] orphan(s) detected — review, then "
                      "`scar orphan --apply --id N --reason ...` to persist")
    for pr in partial:
        console.print(f"[yellow]partial-rot[/] [#{pr.scar_id}] {_partial_rot_reason(pr)}")
    if partial:
        console.print(f"[bold]{len(partial)}[/] partial-rot — advisory; re-anchor the dead "
                      "anchor(s). Not an orphan (still firing on survivors).")


def _repo_context(store):
    """Tracked-file context for orphan detection, or None when git is unavailable
    (not a repo / git failed). Callers skip orphan detection on None rather than
    treating an absent tree as 'every scar orphaned' — a false CI gate (#91)."""
    try:
        return build_repo_context(store.root)
    except GitError:
        return None


def _firing_reviews(store):
    """Scars whose firing count crossed their review threshold (#274).

    Reads the machine-global firing log through the same reader `stats` uses,
    so dedup (#250) and the blanket per-line tolerance (landmine #12) apply
    here too. Never raises: an unreadable log means "nothing to escalate",
    never a broken `status` or `lint`.
    """
    try:
        from .hooks import firing_log_path
        from .review import firing_reviews
        return firing_reviews(store, _read_firing_log(firing_log_path()))
    except Exception:
        return []


def _cmd_lint(args) -> int:
    store = _require_store()
    if store is None:
        return 1
    failed = 0
    files = store._scar_files() + store.candidates()
    findings_by_file: list[tuple[str, list]] = []
    for f in files:
        findings = lint_text(f.read_text(encoding="utf-8"))
        findings_by_file.append((str(f.relative_to(store.root)), findings))
        if any(fi.level == "error" for fi in findings):
            failed += 1

    # Cross-store author drift (#182): the per-file rule in lint_text cannot
    # see a handle split ACROSS scars. Group every author by casefold; a fold
    # with >1 spelling gets a synthetic warning finding attached to its first
    # offending file, so plain/rich/--json renderers all inherit it for free.
    from .lint import Finding
    from .model import ParseError
    parsed_files: list[tuple[str, Scar]] = []
    for f in files:
        try:
            parsed_files.append((str(f.relative_to(store.root)),
                                 parse_scar_text(f.read_text(encoding="utf-8"))))
        except ParseError:
            continue
    author_files: dict[str, dict[str, list[str]]] = {}
    for rel, parsed in parsed_files:
        for author in parsed.authors:
            author_files.setdefault(author.casefold(), {}).setdefault(
                author, []).append(rel)
    for spellings in author_files.values():
        if len(spellings) > 1:
            detail = "; ".join(
                f"'{sp}' in {', '.join(sorted(set(rels)))}"
                for sp, rels in sorted(spellings.items()))
            first = min(r for rels in spellings.values() for r in rels)
            findings_by_file.append((first, [Finding(
                "warning", "author identity drift across the store — "
                f"spellings differing only by case: {detail}")]))

    ctx = _repo_context(store)
    if ctx is None:
        orphans, partial, reverse_hints, drift, revivals = [], [], [], [], []
    else:
        orphans = detect_orphans(store, ctx, repo=store.root)
        partial = detect_partial_rot(store, ctx, repo=store.root)
        revivals = detect_revivals(store, ctx)
        reverse_hints = [s for _f, s in store.parsed()
                         if s.status == "orphaned" and not anchors_all_dead(s, ctx)]
        drift = detect_symbol_drift(store, store.root)

    # Over-broad path anchors (#189): an anchor covering a large share of
    # tracked files carries almost no information about edit relevance —
    # field-measured driver of one-liner noise (75% of demoted showings)
    # and of F-inflation in the compliance metric (daimon #5: 97% of window
    # F from one whole-repo anchor). Warn at >=25% coverage: every measured
    # field offender (29-58%) trips it while deliberate guard anchors like
    # this repo's own `.scars/` (13%) stay quiet. Warning only — breadth is
    # sometimes intentional. Uses the SAME shared predicate as injection.
    # Floor: below ~20 tracked files a single file already exceeds 5%, so the
    # share is quantization noise, not breadth signal (the predicted
    # small-repo instability). Skip the check entirely rather than warn on it.
    if ctx is not None and len(ctx.tracked_paths) >= 20:
        from .match import _path_anchor_matches
        n_tracked = len(ctx.tracked_paths)
        for rel, parsed in parsed_files:
            if parsed.status not in ("active", "challenged"):
                continue
            for anchor in parsed.path_anchors:
                covered = sum(1 for p in ctx.tracked_paths
                              if _path_anchor_matches(anchor, p))
                share = covered / n_tracked
                if share >= 0.25:
                    findings_by_file.append((rel, [Finding(
                        "warning", f"path anchor '{anchor}' matches "
                        f"{share:.0%} of tracked files ({covered}/{n_tracked}) "
                        "— too broad to discriminate; narrow it to the "
                        "files the scar actually protects")]))

    # Firing-count review (#274): a scar that keeps firing without being
    # revised. Advisory like every other lifecycle signal here — only the
    # opt-in --fail-firing-review turns it into an exit code.
    reviews = _firing_reviews(store)

    # evidence reachability (#43, scar #5): commit-SHA receipts that no longer
    # resolve from HEAD. None = shallow clone, reachability indeterminate → skip.
    unreachable = unreachable_evidence(store, store.root)
    shallow = unreachable is None
    if shallow:
        unreachable = []

    data = {
        "files": len(files),
        "findings": [{"file": rel, "level": fi.level, "message": fi.message}
                     for rel, fs in findings_by_file for fi in fs],
        "orphans": [{"scar_id": of.scar_id, "reason": _orphan_reason(of)} for of in orphans],
        "partial_rot": [{"scar_id": pr.scar_id, "reason": _partial_rot_reason(pr)} for pr in partial],
        "symbol_drift": [{"scar_id": d.scar_id, "symbol": d.symbol,
                          "sha": d.sha, "similarity": d.similarity} for d in drift],
        "revivals": [{"scar_id": r.scar_id, "predicate": r.predicate} for r in revivals],
        "reverse_hints": [{"id": s.id} for s in reverse_hints],
        "firing_review": [{"scar_id": r.scar_id, "count": r.count,
                           "threshold": r.threshold, "since": r.since,
                           "undated": r.undated, "reason": r.reason()}
                          for r in reviews],
        "shallow_clone": shallow,
        "unreachable_evidence": [{"scar_id": ue.scar_id, "sha": ue.sha, "reason": ue.reason}
                                 for ue in unreachable],
        "failed": failed,
    }

    def plain():
        for rel, findings in findings_by_file:
            for finding in findings:
                print(f"{rel}: {finding}")
        for of in orphans:
            print(f"WARNING orphan-detected: scar #{of.scar_id} — {_orphan_reason(of)}")
        # partial rot (#35): firing scars with a dead anchor among live ones.
        # Advisory only — never a blocking gate, even under --fail-orphans.
        for pr in partial:
            print(f"HINT partial-rot: scar #{pr.scar_id} — {_partial_rot_reason(pr)} "
                  "— re-anchor to restore full coverage")
        # revives_if (#205): an ARCHIVED scar whose resurrection condition
        # matches the tree again. Advisory and human-gated — lint reports it
        # and never re-arms the scar itself.
        for r in revivals:
            print(f"HINT revives-if: scar #{r.scar_id} is archived but its "
                  f"revives_if predicate /{r.predicate}/ matches the tree again "
                  "— review for re-promotion")
        # symbol drift (#99): a symbol anchor that still resolves by name but
        # whose body shape changed since the evidence commit. Advisory only.
        for d in drift:
            print(f"HINT symbol-drift: scar #{d.scar_id} — {_symbol_drift_reason(d)} "
                  "— re-verify the scar still describes this symbol")
        # reverse hint: persisted-orphaned scars whose anchors resolve again
        for s in reverse_hints:
            print(f"HINT: scar #{s.id} is marked orphaned but its anchors live "
                  "again — consider re-activating (scar challenge/archive note)")
        # firing-count review (#274): the count is the signal, so print it
        # even though the scar is otherwise healthy.
        for r in reviews:
            print(f"WARNING firing-review: scar #{r.scar_id} — {r.reason()}")
        if shallow:
            print("note: shallow clone — evidence-reachability check skipped "
                  "(actions/checkout defaults to depth 1; use fetch-depth: 0)")
        for ue in unreachable:
            print(f"WARNING evidence-unreachable: scar #{ue.scar_id} — commit "
                  f"{ue.sha} {ue.reason}, not reachable from HEAD")
        print(f"lint: {len(files)} file(s), {failed} with errors, "
              f"{len(orphans)} orphan(s), {len(partial)} partial-rot, "
              f"{len(unreachable)} unreachable-evidence, "
              f"{len(reviews)} firing-review")

    output.render(data=data, json_flag=getattr(args, "json", False),
                  tty=lambda: _lint_rich(data), plain=plain)

    if failed:
        return 1
    if orphans and getattr(args, "fail_orphans", False):
        return 1
    if reviews and getattr(args, "fail_firing_review", False):
        return 1
    return 0


def _cmd_status(args) -> int:
    store = _require_store()
    if store is None:
        return 1
    active, broken, cands = store.active(), store.broken(), store.candidates()
    challenged = [(f, s) for f, s in store.parsed() if s.status == "challenged"]
    today = time.strftime("%Y-%m-%d")
    due = [s for _, s in store.firing() if s.review_after and s.review_after < today]

    # Orphans: detected (firing scars whose anchors all died — not yet persisted)
    # and persisted (already flipped to status: orphaned, invisible until now).
    ctx = _repo_context(store)
    if ctx is None:
        detected, partial = [], []
    else:
        detected = detect_orphans(store, ctx, repo=store.root)
        partial = detect_partial_rot(store, ctx, repo=store.root)
    persisted = [s for _, s in store.parsed() if s.status == "orphaned"]
    reviews = _firing_reviews(store)

    data = {
        "scars_dir": str(store.scars_dir),
        "active": [{"type": s.type, "id": s.id, "severity": s.severity, "title": s.title}
                   for _f, s in active],
        "challenged": [{"type": s.type, "id": s.id, "title": s.title} for _f, s in challenged],
        "candidates": [c.name for c in cands],
        "review_due": [{"type": s.type, "id": s.id, "review_after": s.review_after} for s in due],
        # Count-based review trigger (#274), kept SEPARATE from review_due:
        # one is a date passing, the other is accumulated firings, and a
        # consumer that merges them cannot tell which obligation it is looking
        # at or what would discharge it.
        "firing_review": [{"type": r.type, "id": r.scar_id, "count": r.count,
                           "threshold": r.threshold, "since": r.since,
                           "undated": r.undated, "reason": r.reason()}
                          for r in reviews],
        "orphan_detected": [{"scar_id": of.scar_id, "reason": _orphan_reason(of)} for of in detected],
        "orphaned": [{"type": s.type, "id": s.id, "title": s.title} for s in persisted],
        "partial_rot": [{"scar_id": pr.scar_id, "reason": _partial_rot_reason(pr)} for pr in partial],
        "broken": [b.name for b in broken],
        "counts": {
            "active": len(active),
            "candidates": len(cands),
            "orphan_detected": len(detected),
            "orphaned": len(persisted),
            "partial_rot": len(partial),
            "broken": len(broken),
            "firing_review": len(reviews),
        },
    }

    def plain():
        print(f"{store.scars_dir}: {len(active)} active, {len(cands)} candidate(s) pending review")
        for f, s in active:
            print(f"  [{s.type} #{s.id} | {s.severity}] {s.title}")
        for f, s in challenged:
            print(f"  [challenged {s.type} #{s.id}] {s.title}")
        for c in cands:
            print(f"  candidate: {c.name}")
        for s in due:
            print(f"  REVIEW DUE [{s.type} #{s.id}] review_after {s.review_after} — "
                  "re-verify, then update the date or archive")
        for r in reviews:
            print(f"  REVIEW DUE [{r.type} #{r.scar_id}] {r.reason()}")
        print(f"  {len(detected)} orphan-detected (firing, anchors gone), "
              f"{len(persisted)} orphaned (persisted), "
              f"{len(partial)} partial-rot (firing, ≥1 anchor dead)")
        for of in detected:
            print(f"    orphan-detected [#{of.scar_id}] {_orphan_reason(of)}")
        for s in persisted:
            print(f"    orphaned [{s.type} #{s.id}] {s.title}")
        for pr in partial:
            print(f"    partial-rot [#{pr.scar_id}] {_partial_rot_reason(pr)}")
        if broken:
            print(f"  WARNING: {len(broken)} unparseable (can NEVER fire): "
                  + ", ".join(b.name for b in broken))

    output.render(data=data, json_flag=getattr(args, "json", False),
                  tty=lambda: _status_rich(data), plain=plain)
    return 0


def _cmd_promote(args) -> int:
    store = _require_store()
    if store is None:
        return 1
    matches = [c for c in store.candidates() if args.candidate in c.name]
    if len(matches) != 1:
        opts = ", ".join(c.name for c in store.candidates()) or "(none)"
        print(f"need exactly one candidate matching '{args.candidate}'; have: {opts}")
        return 1
    # Promote is human-run by contract; when --reviewer is omitted the git
    # identity of whoever runs it is the reviewer. Unset identity degrades to
    # the old behavior (no reviewer appended).
    reviewer = args.reviewer
    from_git = False
    if not reviewer:
        reviewer = _git(store.root, "config", "user.name").stdout.strip()
        from_git = bool(reviewer)
    # #295: the same distinction from_git already carries, in the vocabulary
    # promoted_by_source is written with.
    reviewer_source = "git-config" if from_git else ("explicit" if reviewer else None)
    try:
        new_path = store.promote(matches[0], reviewer=reviewer,
                                  reviewer_source=reviewer_source)
    except ValueError as exc:
        print(str(exc))
        return 1
    # Non-blocking advisory: a freshly promoted scar whose anchors already
    # resolve to nothing is born orphan-detected. Promote still succeeds — the
    # reviewer may anchor to code that does not exist yet on purpose.
    promoted = parse_scar_text(new_path.read_text(encoding="utf-8"))
    ctx = _repo_context(store)
    born_orphan = ctx is not None and anchors_all_dead(promoted, ctx)
    rel = new_path.relative_to(store.root)
    # Explicit --reviewer differing from the git identity only by case is the
    # exact keystroke behind the field drift in #182 — warn, don't block.
    git_name = _git(store.root, "config", "user.name").stdout.strip()
    case_warning = (args.reviewer and git_name and git_name != args.reviewer
                    and git_name.casefold() == args.reviewer.casefold())
    data = {"promoted": str(rel), "reviewer": reviewer or None,
            "reviewer_from_git": from_git, "born_orphan": born_orphan,
            "reviewer_case_warning": bool(case_warning)}

    def plain():
        print(f"promoted -> {rel}")
        if from_git:
            print(f"  reviewer: {reviewer} (from git config)")
        if case_warning:
            print(f"  warning: --reviewer '{args.reviewer}' differs from git "
                  f"user.name '{git_name}' only by case — pick one spelling "
                  "or author identity drifts (#182)")
        if born_orphan:
            print("  advisory: this scar's anchors resolve to nothing in the current "
                  "tree (born orphan-detected) — confirm the anchors are right")

    output.render(data=data, json_flag=False,
                  tty=lambda: _promote_rich(data), plain=plain)
    return 0


def _cmd_check(args) -> int:
    """CI gate for humans and CI (README). Accepts one or more paths, or a
    --diff (same union-of-changed-files discovery as `inject --diff`, #106).
    --exit-code turns a fire into a non-zero exit; without it, behavior is
    unchanged (always 0) so existing callers never regress (back-compat)."""
    diff_text = None
    if args.diff:
        try:
            diff_text = Path(args.diff).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            # ValueError covers NUL-byte paths; mirror inject's fallback so a
            # raw diff string on the command line works the same way.
            diff_text = args.diff

    if diff_text is not None:
        store = _require_store()
        label = "diff"
    elif args.path:
        store = _require_store(Path(args.path[0]).resolve())
        label = ", ".join(args.path)
    else:
        print("check requires a path or --diff")
        return 1
    if store is None:
        return 1

    if diff_text is not None:
        matches = rank_matches_for_diff(store, diff_text, top_k=args.top_k)
        violations = find_violations_for_diff(store, diff_text)
    else:
        matches = rank_matches_for_paths(store, args.path, args.content or "",
                                         top_k=args.top_k)
        violations = []
    hits = [m.scar for m in matches]

    data = {
        "paths": [] if diff_text is not None else list(args.path),
        "scars": [{"type": s.type, "id": s.id, "severity": s.severity,
                   "confidence": s.confidence, "status": s.status, "title": s.title,
                   "body": s.body[:200]} for s in hits],
    }
    if diff_text is not None:
        # --diff mode only: violations is ALWAYS present ([] when none) — a
        # separate, stronger signal from the ranked "scars" list (a scar can
        # fire on path-anchor proximity alone without its violation regex
        # ever matching the added content).
        data["violations"] = [{"scar_id": v.scar.id, "title": v.scar.title,
                                "path": v.path, "excerpt": v.excerpt} for v in violations]
    if diff_text is None and len(args.path) == 1:
        data["path"] = args.path[0]  # back-compat: single-path shape unchanged

    def plain():
        if not hits:
            print(f"no scars anchored to {label}")
        else:
            for s in hits:
                print(label_line(s))
                print("  " + s.body[:200].replace("\n", "\n  "))
        for v in violations:
            print(f"VIOLATION scar #{v.scar.id} on {v.path}: {v.scar.title}")

    output.render(data=data, json_flag=getattr(args, "json", False),
                  tty=lambda: _check_rich(label, hits, violations), plain=plain)

    if getattr(args, "exit_code", False) and (hits or violations):
        return 1
    return 0


def _cmd_challenge(args) -> int:
    return _cmd_transition(args, "challenged")


def _cmd_archive(args) -> int:
    return _cmd_transition(args, "archived")


def _cmd_transition(args, new_status: str) -> int:
    store = _require_store()
    if store is None:
        return 1
    try:
        path = store.transition(args.id, new_status, reason=args.reason,
                                date=time.strftime("%Y-%m-%d"))
    except ValueError as exc:
        print(str(exc))
        return 1
    verb = ("still fires, marked as disputed — resolve by archiving or "
            "re-validating" if new_status == "challenged"
            else "never fires again; history kept (scar why still shows it)")
    print(f"{new_status} -> {path.relative_to(store.root)} ({verb})")
    return 0


def _orphan_fix_renames(store: ScarStore, findings, args) -> int:
    """`scar orphan --fix-renames`: surgically rewrite the anchor line for
    every dead path anchor that resolved to an unambiguous, currently-tracked
    git rename target (#109). Read-only stays the default; this is explicit
    opt-in and touches ONLY orphan-detected scars — never partial-rot (the
    fix there is re-anchoring, same posture as everywhere else partial-rot is
    handled).

    Each rewrite is a single-line text replace (renames.apply_rename_fix) —
    never a parse+reserialize (landmine #4: promote's roundtrip once silently
    dropped expires/evidence on a hand-authored file).
    """
    id_to_path = {s.id: f for f, s in store.parsed() if s.id is not None}
    fixed: list[tuple[int | None, dict[str, str]]] = []
    skipped: list[int | None] = []
    for of in findings:
        path = id_to_path.get(of.scar_id)
        if not of.renamed or path is None:
            if of.dead_path_anchors:
                skipped.append(of.scar_id)
            continue
        if apply_rename_fix(path, of.renamed):
            fixed.append((of.scar_id, of.renamed))
        else:
            skipped.append(of.scar_id)

    data = {
        "fixed": [{"scar_id": sid, "renamed": renamed} for sid, renamed in fixed],
        "skipped": [{"scar_id": sid} for sid in skipped],
    }

    def plain():
        for sid, renamed in fixed:
            for old, new in renamed.items():
                print(f"fixed [#{sid}] renamed anchor {old} -> {new}")
        for sid in skipped:
            print(f"not fixed [#{sid}] no unambiguous, currently-tracked "
                  "rename target — anchor left as-is")
        if not fixed and not skipped:
            print("no orphan-detected scars with a dead path anchor to fix")
        print(f"{len(fixed)} scar(s) fixed, {len(skipped)} left unfixed")

    def tty():
        console = output.console
        for sid, renamed in fixed:
            for old, new in renamed.items():
                console.print(f"[green]fixed[/] [#{sid}] renamed anchor {old} -> {new}")
        for sid in skipped:
            console.print(f"[yellow]not fixed[/] [#{sid}] no unambiguous, "
                          "currently-tracked rename target")
        if not fixed and not skipped:
            console.print("[green]no orphan-detected scars with a dead path anchor to fix[/]")
        console.print(f"[bold]{len(fixed)}[/] scar(s) fixed, [bold]{len(skipped)}[/] left unfixed")

    output.render(data=data, json_flag=getattr(args, "json", False), tty=tty, plain=plain)
    return 0


def _cmd_orphan(args) -> int:
    """List firing scars whose every anchor is dead. Read-only by default;
    --apply persists status: orphaned via store.transition() (human-only);
    --fix-renames rewrites dead-but-renamed anchor lines (also human-only,
    also opt-in — #109)."""
    store = _require_store()
    if store is None:
        return 1
    ctx = _repo_context(store)
    if ctx is None:
        # Orphan detection is the whole job here; without git we cannot know the
        # tracked set. Surface it rather than report a spurious all-orphaned tree.
        print("orphan: git unavailable (not a repository?) — cannot determine "
              "tracked files; orphan detection skipped")
        return 1
    findings = detect_orphans(store, ctx, repo=store.root)

    if getattr(args, "fix_renames", False):
        return _orphan_fix_renames(store, findings, args)

    if not args.apply:
        partial = detect_partial_rot(store, ctx, repo=store.root)
        data = {
            "orphan_detected": [{"scar_id": of.scar_id, "reason": _orphan_reason(of)}
                                for of in findings],
            "partial_rot": [{"scar_id": pr.scar_id, "reason": _partial_rot_reason(pr)}
                            for pr in partial],
        }

        def plain():
            if not findings:
                print("no orphan-detected scars")
            else:
                for of in findings:
                    print(f"orphan-detected [#{of.scar_id}] {_orphan_reason(of)}")
                print(f"{len(findings)} orphan(s) detected — review, then "
                      "`scar orphan --apply --id N --reason ...` to persist")
            # Partial rot is advisory and surfaced separately — never persisted as
            # orphaned (the fix is re-anchoring, not a status transition). #35.
            for pr in partial:
                print(f"partial-rot [#{pr.scar_id}] {_partial_rot_reason(pr)}")
            if partial:
                print(f"{len(partial)} partial-rot — advisory; re-anchor the dead "
                      "anchor(s). Not an orphan (still firing on survivors).")

        output.render(data=data, json_flag=getattr(args, "json", False),
                      tty=lambda: _orphan_rich(findings, partial), plain=plain)
        return 0

    # --apply: persist. Human-only (never wire into CI/lint).
    if args.id is None:
        print("--apply requires --id N (persist one reviewed orphan at a time)")
        return 1
    target = next((of for of in findings if of.scar_id == args.id), None)
    if target is None:
        ids = ", ".join(str(of.scar_id) for of in findings) or "(none)"
        print(f"scar #{args.id} is not orphan-detected; detected ids: {ids}")
        return 1
    note = f"{args.reason} [orphan: {_orphan_reason(target)}]"
    try:
        path = store.transition(args.id, "orphaned", reason=note,
                                date=time.strftime("%Y-%m-%d"))
    except ValueError as exc:
        print(str(exc))
        return 1
    print(f"orphaned -> {path.relative_to(store.root)} "
          "(never fires again; history kept, anchors-live-again hint will surface "
          "if the code returns)")
    return 0


# ---------------------------------------------------------------------------
# scar reanchor (#111) — orphan recovery v1: propose new anchors for
# orphaned/partial-rot scars. Consumes BOTH detect_orphans and
# detect_partial_rot findings for the path side, plus its own symbol-liveness
# scan (orphan.py never tracked symbol anchors — #90 stdlib-only core), plus
# a diagnostic-only surface for dead pattern anchors (never an applyable
# proposal — regenerating a regex is a v1 cut, #54 discipline).
# ---------------------------------------------------------------------------

def _pattern_diagnostic(repo: Path, scar_id: int | None, pattern: str) -> dict:
    """One dead pattern anchor's diagnostic line: 'last matched at <sha7>'
    from a single bounded `git log -G -n 1`, or a skip note. Never an
    applyable proposal — pattern regeneration is an explicit v1 cut.

    Landmine #6 (single-escape / ReDoS anchors): reuse lint's own
    `_is_redos_prone` guard before ever handing a user-authored pattern to
    git's `-G` regex engine — a pathological pattern must never run, even in
    a read-only diagnostic.
    """
    if _is_redos_prone(pattern):
        return {"scar_id": scar_id, "dead_anchor": pattern,
                "diagnostic": "skipped — pathological pattern (ReDoS risk)"}
    try:
        proc = _git(repo, "log", "-G", pattern, "-n", "1", "--format=%H")
    except OSError:
        return {"scar_id": scar_id, "dead_anchor": pattern, "diagnostic": "no prior match found"}
    sha = proc.stdout.strip() if proc.returncode == 0 else ""
    if not sha:
        return {"scar_id": scar_id, "dead_anchor": pattern, "diagnostic": "no prior match found"}
    return {"scar_id": scar_id, "dead_anchor": pattern, "diagnostic": f"last matched at {sha[:7]}"}


def _reanchor_collect(store: ScarStore, ctx, repo: Path):
    """Every reanchor proposal + pattern diagnostic across the repo.
    Read-only. Path proposals skip any dead anchor #109's rename-follower
    already resolved (`finding.renamed`) — reanchor is the residue rename-
    following doesn't cover, not a second opinion on the same anchor."""
    tracked = set(ctx.tracked_paths)
    orphans = detect_orphans(store, ctx, repo=repo)
    partial = detect_partial_rot(store, ctx, repo=repo)

    proposals = []
    pattern_diag = []
    for finding in (*orphans, *partial):
        for anchor in finding.dead_path_anchors:
            if anchor in finding.renamed:
                continue  # already solved by git rename-following (#109)
            proposals.extend(propose_path_reanchors(repo, finding.scar_id, anchor, tracked))
        for pattern in finding.dead_pattern_anchors:
            pattern_diag.append(_pattern_diagnostic(repo, finding.scar_id, pattern))

    for _f, scar in store.firing():
        proposals.extend(propose_symbol_reanchors_for_scar(repo, scar, tracked))

    return proposals, pattern_diag


def _reanchor_data(proposals, pattern_diag) -> dict:
    return {
        "proposals": [
            {"scar_id": p.scar_id, "anchor_kind": p.anchor_kind, "dead_anchor": p.dead_anchor,
             "proposed_anchor": p.proposed_anchor, "confidence": p.confidence,
             "signal": p.signal, "evidence": p.evidence}
            for p in proposals
        ],
        "pattern_diagnostics": pattern_diag,
    }


def _reanchor_plain(proposals, pattern_diag) -> None:
    if not proposals and not pattern_diag:
        print("no reanchor proposals")
        return
    for p in proposals:
        print(f"reanchor [#{p.scar_id}] {p.anchor_kind} {p.dead_anchor} -> "
              f"{p.proposed_anchor} ({p.confidence}, signal {p.signal}) — {p.evidence}")
    for d in pattern_diag:
        print(f"pattern [#{d['scar_id']}] {d['dead_anchor']}: {d['diagnostic']} "
              "(diagnostic only — never auto-applied)")
    print(f"{len(proposals)} proposal(s), {len(pattern_diag)} pattern diagnostic(s) — "
          "review, then `scar reanchor --apply --id N` to persist")


def _reanchor_rich(proposals, pattern_diag) -> None:
    console = output.console
    if not proposals and not pattern_diag:
        console.print("[green]no reanchor proposals[/]")
        return
    for p in proposals:
        style = "green" if p.confidence == "high" else "yellow"
        console.print(f"[{style}]reanchor[/] [#{p.scar_id}] {p.anchor_kind} {p.dead_anchor} -> "
                      f"{p.proposed_anchor} ({p.confidence})")
    for d in pattern_diag:
        console.print(f"[dim]pattern[/] [#{d['scar_id']}] {d['dead_anchor']}: {d['diagnostic']}")
    console.print(f"[bold]{len(proposals)}[/] proposal(s), "
                  f"[bold]{len(pattern_diag)}[/] pattern diagnostic(s)")


def _reanchor_apply(store: ScarStore, ctx, repo: Path, args) -> int:
    """`scar reanchor --apply --id N`: rewrite ONLY the dead anchors on scar
    #N that have exactly one high-confidence candidate (target is already
    guaranteed tracked — every proposal's candidate comes from the tracked
    set). A dead anchor with zero candidates, or two-or-more (any tier —
    ambiguity is the signal itself), is reported but left untouched: partial
    re-anchoring on a multi-anchor scar is expected, not an error. Never
    flips status: the anchors-live-again hint on the next `scar lint` is how
    a human notices and reactivates (same posture as #109's --fix-renames)."""
    tracked = set(ctx.tracked_paths)
    id_to_path = {s.id: f for f, s in store.parsed() if s.id is not None}
    id_to_scar = {s.id: s for _f, s in store.parsed() if s.id is not None}
    scar_file = id_to_path.get(args.id)
    scar = id_to_scar.get(args.id)

    orphans = detect_orphans(store, ctx, repo=repo)
    partial = detect_partial_rot(store, ctx, repo=repo)
    finding = next((f for f in (*orphans, *partial) if f.scar_id == args.id), None)

    dead_anchors: list[tuple[str, str]] = []
    if finding is not None:
        for anchor in finding.dead_path_anchors:
            if anchor not in finding.renamed:  # #109 already solved this one
                dead_anchors.append(("path", anchor))
    if scar is not None:
        for anchor in dead_symbol_anchors(scar, repo, tracked):
            dead_anchors.append(("symbol", anchor))

    if scar_file is None or not dead_anchors:
        print(f"scar #{args.id} has no reanchor proposals")
        return 1

    proposals, _pattern_diag = _reanchor_collect(store, ctx, repo)
    groups: dict[tuple[str, str], list] = {}
    for p in proposals:
        if p.scar_id == args.id:
            groups.setdefault((p.anchor_kind, p.dead_anchor), []).append(p)

    fixed: list[tuple[str, str, str]] = []
    skipped: list[tuple[str, str, str]] = []
    renamed_by_kind: dict[str, dict[str, str]] = {"path": {}, "symbol": {}}
    # One note per applied rewrite, formatted before the file is touched so
    # the confidence tier is read off the proposal, not re-derived later.
    notes: list[str] = []
    apply_date = time.strftime("%Y-%m-%d")
    for kind, dead in dead_anchors:
        plist = groups.get((kind, dead), [])
        if not plist:
            skipped.append((kind, dead, "no candidate found"))
            continue
        if len(plist) > 1:
            skipped.append((kind, dead, f"ambiguous — {len(plist)} candidates"))
            continue
        only = plist[0]
        if only.confidence != "high":
            skipped.append((kind, dead, "low confidence — needs human review"))
            continue
        renamed_by_kind[kind][dead] = only.proposed_anchor
        fixed.append((kind, dead, only.proposed_anchor))
        notes.append(format_reanchor_note(
            apply_date, kind, dead, only.proposed_anchor, only.confidence))

    for kind, renamed in renamed_by_kind.items():
        if renamed:
            apply_anchor_rewrite(scar_file, kind, renamed)

    # Evidence notes (#296): recorded AFTER the anchor rewrite lands, via a
    # fresh parse of the (already-rewritten) file, so this never races the
    # surgical rewrite above with a stale in-memory anchor value.
    for note in notes:
        store.append_evidence_note(args.id, note)

    data = {
        "fixed": [{"anchor_kind": k, "dead_anchor": d, "proposed_anchor": n} for k, d, n in fixed],
        "skipped": [{"anchor_kind": k, "dead_anchor": d, "reason": r} for k, d, r in skipped],
    }

    def plain():
        for k, d, n in fixed:
            print(f"fixed [#{args.id}] {k} {d} -> {n}")
        for k, d, r in skipped:
            print(f"not fixed [#{args.id}] {k} {d}: {r}")
        print(f"{len(fixed)} anchor(s) fixed, {len(skipped)} left unfixed — status unchanged "
              "(anchors-live-again hint will surface on next `scar lint`)")

    def tty():
        console = output.console
        for k, d, n in fixed:
            console.print(f"[green]fixed[/] [#{args.id}] {k} {d} -> {n}")
        for k, d, r in skipped:
            console.print(f"[yellow]not fixed[/] [#{args.id}] {k} {d}: {r}")
        console.print(f"[bold]{len(fixed)}[/] anchor(s) fixed, [bold]{len(skipped)}[/] left unfixed")

    output.render(data=data, json_flag=getattr(args, "json", False), tty=tty, plain=plain)
    return 0


def _cmd_reanchor(args) -> int:
    """List (default) or apply (--apply --id N) reanchor proposals for
    orphaned/partial-rot scars — orphan recovery v1 (#111). Read-only by
    default; --apply is human-only, one reviewed scar at a time, and never
    touches status (same posture as `orphan --fix-renames`)."""
    store = _require_store()
    if store is None:
        return 1
    ctx = _repo_context(store)
    if ctx is None:
        print("reanchor: git unavailable (not a repository?) — cannot trace anchors; "
              "reanchor skipped")
        return 1

    if args.apply:
        if args.id is None:
            print("--apply requires --id N (persist one reviewed scar's eligible anchors at a time)")
            return 1
        return _reanchor_apply(store, ctx, store.root, args)

    proposals, pattern_diag = _reanchor_collect(store, ctx, store.root)
    data = _reanchor_data(proposals, pattern_diag)
    output.render(data=data, json_flag=getattr(args, "json", False),
                 tty=lambda: _reanchor_rich(proposals, pattern_diag),
                 plain=lambda: _reanchor_plain(proposals, pattern_diag))
    return 0


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _cmd_brief(args) -> int:
    """Paste-ready scar block for sub-agent launch prompts (#176). Plain text
    only, on tty and non-tty alike — the output IS the machine artifact
    (scar #8: never rich on a machine path), so no rendering layer here."""
    store = ScarStore.discover(Path.cwd())
    if store is None:
        print("no .scars/ directory found", file=sys.stderr)
        return 1
    firing = store.firing()
    selected = []
    for source, scar in firing:
        if args.paths and not scar.command_anchors:
            # Overlap in either direction: anchor covers the path, or the
            # given path (a directory) covers the anchor. Pattern anchors
            # are tested against the path string, mirroring injection.
            from .match import _path_anchor_matches, _pattern_anchor_matches
            hit = any(_path_anchor_matches(a, p) or p.rstrip("/") in ("", ".")
                      or a.startswith(p.rstrip("/") + "/")
                      for a in scar.path_anchors for p in args.paths)
            if not hit:
                hit = any(_pattern_anchor_matches(pat, p)
                          for pat in scar.pattern_anchors for p in args.paths)
            if not hit:
                continue
        selected.append(scar)
    selected.sort(key=lambda s: (SEVERITY_ORDER.get(s.severity, 2),
                                 -s.confidence, s.id or 0))
    if not selected:
        return 0
    header = (f"Repo negative knowledge ({len(selected)} scar(s)) — do not "
              "repeat these recorded mistakes:")
    lines, omitted = [header], 0
    budget = max(args.max_chars - len(header), 0)
    for scar in selected:
        sid = f"#{scar.id}" if scar.id is not None else "cand"
        entry = (f"- [{scar.type} {sid} | {scar.severity}] {scar.title}"
                 f" — {rule_line(scar.body)}")
        if len(entry) + 1 > budget:
            omitted += 1
            continue
        budget -= len(entry) + 1
        lines.append(entry)
    if omitted:
        lines.append(f"(+{omitted} more scar(s) omitted by --max-chars — "
                     "run `scar status` for the full set)")
    print("\n".join(lines))
    return 0


def _cmd_why(args) -> int:
    """History of pain for a path: every scar that anchors it, any status."""
    store = _require_store(Path(args.path).resolve())
    if store is None:
        return 1
    rel = str(Path(args.path).resolve().relative_to(store.root))
    records = store.scars_for_path(rel)
    data = {
        "path": rel,
        "records": [{"status": s.status, "type": s.type, "id": s.id, "title": s.title,
                     "file": f.name, "body": s.body[:300]} for f, s in records],
    }

    def plain():
        for f, s in records:
            print(f"[{s.status} {s.type} #{s.id}] {s.title}  ({f.name})")
            print("  " + s.body[:300].replace("\n", "\n  ") + "\n")
        if not records:
            print(f"no recorded pain for {rel}")

    output.render(data=data, json_flag=getattr(args, "json", False),
                  tty=lambda: _why_rich(rel, records), plain=plain)
    return 0


def _dedup_key(rec: dict) -> tuple | None:
    """Identity of one recorded tool call, or None when the record cannot be
    identified and must therefore never be merged.

    #250: Scar can be wired into Codex through BOTH ~/.codex/hooks.json and
    the plugin's hooks/hooks.json. Both are separate processes appending to
    the same log, so one apply_patch writes two rows sharing an `edit_id` and
    every Codex firing is counted twice. Write-time dedup cannot work across
    two racing processes; collapsing on read also repairs data already on
    disk.

    `ts` is deliberately NOT in the key — the two channels can land in
    different seconds. The id lists ARE, so a PreToolUse firing and its
    PostToolUse violation row (same edit_id, different ids) stay distinct.

    Bails out to None unless `edit_id` is a non-empty string: rows written
    before that field existed carry no call identity, and merging them would
    silently delete real independent firings.
    """
    edit_id = rec.get("edit_id")
    if not isinstance(edit_id, str) or not edit_id:
        return None
    try:
        # scar 0012: any shape can appear here. repr never raises for
        # JSON-derived values, so the key cannot become a new crash site on
        # a path that must stay fail-open.
        return (edit_id, repr(rec.get("repo")), repr(rec.get("target")),
                repr(rec.get("scar_ids")), repr(rec.get("demoted_ids")),
                repr(rec.get("violation_ids")))
    except Exception:
        return None


def _read_firing_log(log_path: Path) -> list[dict]:
    """Read the machine-global firing log, keeping only dict records. The log
    is written best-effort from a fail-open hook, so ANY JSON shape can appear
    on a line (landmine #12) — `null`, `[]`, numbers all parse fine and then
    crash at rec.get(); skip everything that isn't a dict.

    Rows describing the same tool call are collapsed (#250) — see _dedup_key.
    """
    records = []
    seen: set[tuple] = set()
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                # scar 0012 prescribes a BLANKET per-line guard, not a narrow
                # except tuple: a reader that escapes into precheck's outer
                # fail-open kills injection permanently.
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            key = _dedup_key(rec)
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            records.append(rec)
    return records


def _parse_window_bound(text: str, *, end_of_day: bool):
    """Parse an ISO date or datetime for --since/--until. None if unparseable.

    A BARE DATE is asymmetric on purpose: as --since it means 00:00:00 that
    day, as --until it means the end of that day. `--since D --until D` is
    therefore the whole of day D, which is what someone naming two dates means.
    An explicit time is used verbatim in both directions.
    """
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(text.strip())
    except Exception:
        return None
    if end_of_day and len(text.strip()) == 10:  # 'YYYY-MM-DD', no time given
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt


def _filter_window(records: list[dict], lo, hi) -> tuple[list[dict], int]:
    """Keep records whose `ts` falls within [lo, hi]; both bounds optional.

    Returns (kept, excluded_undated). A record whose `ts` is missing, not a
    string, unparseable, or not comparable to the bounds (naive vs aware) is
    EXCLUDED and counted — never silently kept, and never silently dropped
    either: the caller reports the count so a window states what it could not
    place. Per landmine #12 every line is guarded blanket-style, because the
    log is written best-effort and any shape can appear.
    """
    from datetime import datetime
    if lo is None and hi is None:
        return records, 0
    kept, undated = [], 0
    for rec in records:
        ts = rec.get("ts")
        dt = None
        if isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts)
            except Exception:
                dt = None
        if dt is None:
            undated += 1
            continue
        try:
            if (lo is not None and dt < lo) or (hi is not None and dt > hi):
                continue
        except Exception:
            undated += 1  # naive vs aware, or anything else uncomparable
            continue
        kept.append(rec)
    return kept, undated


# The measurement stages (#214). ONE definition, so a new stage or a new
# surface cannot silently reach only some of them — that has now happened
# twice (#225 Rich, #227 --all-repos). tests/test_cli.py asserts every stats
# surface exposes exactly these keys.
STAGE_KEYS = frozenset({"retrieval_misses", "demotions",
                        "demotions_path_only", "demotions_cooldown",
                        "demotions_reason_unknown",
                        "census_known", "census_unknown", "cofires_per_edit",
                        "edits_multi_fire", "path_only_ratio",
                        "edits_observed", "injection_rate",
                        "command_firings", "firings_kind_unknown",
                        "armed_firings", "armed_unknown",
                        "firings_block_capable", "firings_advisory",
                        "firings_block_unknown", "all_firings_advisory",
                        "firings_context_known", "firings_context_unknown"})


def _stage_block(agg: dict) -> dict:
    """The stage fields for one aggregated repo, keyed by STAGE_KEYS."""
    return {k: agg[k] for k in STAGE_KEYS}


# Instrument health (#237). Separate from STAGE_KEYS on purpose: the stages
# are measurements, these say whether the measurements can be believed at
# all. Same parity discipline — one definition, every surface.
HEALTH_KEYS = frozenset({"instrument_disconnected", "last_fired_age_days",
                         "posttool_silent", "verdicts_expected",
                         "verdicts_observed", "verdicts_unresolved",
                         "verdicts_unplaceable"})


def _health_block(agg: dict) -> dict:
    """The health fields for one aggregated repo, keyed by HEALTH_KEYS."""
    return {k: agg[k] for k in HEALTH_KEYS}


INSTRUMENT_WARNING = (
    "WARNING: violations recorded but NO precheck records — the precheck hook "
    "is almost certainly not installed, so no scar can ever have fired. This "
    "is a broken install, not a measurement. Run `scar hook install`, then "
    "`scar hook status` to confirm all hooks are present."
)


POSTTOOL_SILENT_WARNING = (
    "WARNING: armed scars fired but NO posttool verdict was ever recorded — "
    "the posttool hook is almost certainly not installed, so a violation "
    "could not have been recorded even if one occurred. Zero violations in "
    "this window is a broken install, not compliance. Run `scar hook "
    "install`, then `scar hook status` to confirm all hooks are present."
)


def _health_lines(block: dict) -> list[str]:
    """Health lines, shared by every renderer. The firing age is REPORTED and
    never thresholded: a 'stale' cutoff would be an unvalidated fitted
    parameter, which this metric set has refused since #54/#95."""
    lines = []
    if block["instrument_disconnected"]:
        lines.append(INSTRUMENT_WARNING)
    # The mirror of the above (#277). The pre-#277 guard only observed the
    # precheck half dying; this one observes the posttool half dying, which is
    # the direction that FLATTERS the result and so is the more dangerous of
    # the two to leave unwatched.
    if block.get("posttool_silent"):
        lines.append(POSTTOOL_SILENT_WARNING)
    unresolved = block.get("verdicts_unresolved") or 0
    if unresolved and not block.get("posttool_silent"):
        lines.append(f"{unresolved} armed firing(s) have no posttool verdict — "
                     "unresolved, NOT clean")
    age = block["last_fired_age_days"]
    if age is not None:
        lines.append(f"newest firing: {age} day(s) old")
    return lines


def _stage_lines(block: dict, per_scar: list[dict], total_firings: int) -> list[str]:
    """The three stage lines, shared by every renderer so plain and Rich can
    never disagree about what a stage says."""
    violations = sum(e.get("violations", 0) for e in per_scar)
    rate = block.get("injection_rate")
    misses = block["retrieval_misses"]
    if misses is None:
        # Refused, not zero. Every violation in a disconnected window looks
        # like a retrieval miss by construction (#236) — reporting the count
        # would publish an artifact of the broken install as a finding.
        retrieval = ("retrieval: SUPPRESSED — the instrument is disconnected, "
                     "so this window cannot measure retrieval")
    elif rate is not None:
        retrieval = (f"retrieval: {rate:.1%} of {block['edits_observed']} observed "
                     f"edit(s) injected a scar; {misses} missed firing(s)")
    else:
        retrieval = f"retrieval: {misses} missed firing(s) — LOWER BOUND"
    # #294: what the retrieval line LEFT OUT. "of N observed edit(s)" is only
    # true because command firings and rows that predate `anchor_kind` are
    # excluded from N, and an exclusion nobody can see reads as an absence.
    commands = block.get("command_firings") or 0
    kind_unknown = block.get("firings_kind_unknown") or 0
    anchors = []
    if commands:
        anchors.append(f"{commands} command firing(s), not observed edits")
    if kind_unknown:
        anchors.append(f"{kind_unknown} row(s) of unknown anchor kind "
                       "(logged before it was recorded)")
    anchor_line = ("anchors: " + "; ".join(anchors)
                   + ", excluded from the edit denominators") if anchors else None
    # #266: the enforcement denominator that can actually fail. A firing on a
    # scar with no tripwire could never have been recorded as violated, so the
    # armed count is the honest denominator; `armed_unknown` names the rows
    # that predate the field rather than folding them into either side.
    armed = block.get("armed_firings")
    unknown = block.get("armed_unknown") or 0
    enforcement = f"enforcement: {violations} violation(s) / {total_firings} firing(s)"
    if armed is not None:
        enforcement += f"; {armed} on violation-armed scar(s)"
        if unknown:
            enforcement += f", {unknown} unplaceable (logged before arming was recorded)"
    # #278: what the enforcement number is EVIDENCE of. Zero violations where
    # refusing an action was never possible is a different claim from zero
    # violations where it was, and the two are numerically identical.
    capable = block.get("firings_block_capable")
    advisory = block.get("firings_advisory") or 0
    cap_unknown = block.get("firings_block_unknown") or 0
    if block.get("all_firings_advisory"):
        enforcement += ("; every firing was ADVISORY — nothing here could "
                        "refuse an action, so this measures what the agent "
                        "did, never what it was prevented from doing")
    elif capable:
        enforcement += (f"; {capable} firing(s) could refuse the action, "
                        f"{advisory} advisory")
    if cap_unknown:
        enforcement += (f", {cap_unknown} of unknown capability (logged "
                        "before it was recorded)")
    # #284: what the demotions are EVIDENCE of. A path-only demotion is a
    # near-miss on the file, a cooldown demotion is a content hit we chose not
    # to repeat; one blended number hides which precision story this repo
    # is telling. Unknown rows are named, never folded into either side.
    demotions = f"demotions: {block['demotions']} scar(s) rendered as one-liners"
    path_only = block.get("demotions_path_only") or 0
    cooldown = block.get("demotions_cooldown") or 0
    reason_unknown = block.get("demotions_reason_unknown") or 0
    if path_only or cooldown:
        demotions += f"; {path_only} path-only, {cooldown} cooldown"
    if reason_unknown:
        demotions += (f"{',' if (path_only or cooldown) else ';'} {reason_unknown} "
                      "of unknown reason (logged before it was recorded)")
    # #286: co-fires per edit is the leading indicator for the compliance
    # cliff (two anchored lessons on one edit, pulling apart), and the
    # path-only share is a PROXY for the false-anchor rate, never the rate:
    # the true rate needs ground truth about applicability that nothing
    # here produces. Both are refused, not zeroed, when no row carries a
    # census, and rows without one are named rather than absorbed.
    known = block.get("census_known") or 0
    unknown_census = block.get("census_unknown") or 0
    per_edit = block.get("cofires_per_edit")
    ratio = block.get("path_only_ratio")
    if per_edit is None:
        cofires = "co-fires: UNKNOWN, no row carries a census yet"
    else:
        cofires = (f"co-fires: {per_edit:.1f} per edit over {known} row(s), "
                   f"{block.get('edits_multi_fire') or 0} edit(s) with 2+")
        if ratio is not None:
            cofires += (f"; path-only share {ratio:.1%} "
                        "(a proxy for false-anchor rate, not the rate)")
    if unknown_census:
        cofires += f"; {unknown_census} row(s) without a census (logged before it was recorded)"
    lines = [
        enforcement,
        retrieval,
    ]
    if anchor_line:
        lines.append(anchor_line)
    lines += [demotions, cofires]
    return lines


RETRIEVAL_FLOOR_NOTE = (
    "note: retrieval is a LOWER BOUND, not a rate — it counts only violations "
    "with no prior firing on that target. Misses on scars with no violation: "
    "pattern are not observable from this log."
)


# Mirrors hooks.VERDICT_EXPECTED_KEY. Imported lazily there, duplicated here
# as a module constant so the read path stays free of a hook import.
_VERDICT_EXPECTED_KEY = "verdict_expected"
_CONTEXT_BYTES_KEY = "context_bytes"


def _aggregate_firings(records: list[dict]) -> dict:
    """Aggregate firing-log records into per-scar counts. Callers must pass
    records from ONE repo only — scar ids are per-repo sequential ints, so
    aggregating across repos sums different scars under one id (#137)."""
    from .hooks import DEMOTED_COOLDOWN, DEMOTED_PATH_ONLY  # one spelling, both sides
    counts: dict[int, int] = {}
    violations: dict[int, int] = {}
    # (scar_id, target) -> earliest firing ts, for the retrieval-miss check.
    fired_on: dict[tuple[int, str], str] = {}
    pending_violations: list[tuple[int, str, str]] = []
    demotions = 0
    # #284: WHY. path-only is the weak signal (file in scope, edit matched
    # nothing), cooldown is the strong one (content matched, suppressed to
    # avoid repeating). A row that predates `demotion_reasons` is UNKNOWN,
    # never folded into path-only — that is the flattering reading.
    demotions_path_only = 0
    demotions_cooldown = 0
    demotions_reason_unknown = 0
    # #286: the pre-truncation census. `count` on a row is min(matched,
    # top_k), so co-fires per edit were unrecordable until `matched` existed.
    # Unit is FIRING ROWS (one edit on one file), not scar-firings. A row
    # without the field is unknown, never a 1.
    census_known = 0
    census_unknown = 0
    census_total_sum = 0
    census_path_only_sum = 0
    edits_multi_fire = 0
    edits_observed = 0      # precheck passes recorded (#217 denominator)
    zero_hit_edits = 0      # ...of which matched nothing
    # #294: the edit denominators above are EDIT rows only. A command firing
    # is not an observed edit: the command path returns early on no match, so
    # it can never contribute a zero-hit row and only ever adds to the
    # numerator. Rows without `anchor_kind` predate the field and go in
    # neither side; folding them into edits is the flattering reading.
    command_firings = 0
    firings_kind_unknown = 0
    # #266: firings on scars that carried a violation: tripwire, and firings we
    # cannot place because the row predates the field. NEVER inferred from
    # today's .scars/ — a scar armed last week was not armed last month.
    armed_firings = 0
    armed_unknown = 0
    # #277: verdict accounting. `expected` are joinable armed firings (an
    # armed_ids list with at least one id, plus an edit_id to join on);
    # `observed` are posttool rows that actually reached a verdict. Anything
    # that can be neither joined nor placed is UNPLACEABLE and is counted
    # apart, never folded into either side.
    verdict_expected_ids: set = set()
    verdict_seen_ids: set = set()
    verdicts_observed = 0
    verdicts_unplaceable = 0
    # #278: could each firing have refused the action? Read from the row and
    # never derived from `runtime`, so a host whose hook capability changes
    # cannot retroactively rewrite what old rows claimed.
    firings_block_capable = 0
    firings_advisory = 0
    firings_block_unknown = 0
    # #279: did the host actually hand us a context size? Shipping a field
    # that may never populate is how you get a blind column nobody notices,
    # so the population rate is itself reported. One command then answers
    # whether the signal exists on a given host, instead of an assumption
    # sitting in a design doc.
    firings_context_known = 0
    firings_context_unknown = 0
    last_fired = None
    for rec in records:
        target = rec.get("target")
        ts_rec = rec.get("ts")
        sids = rec.get("scar_ids", [])
        # #294: read once, used by every edit-scoped counter below. `is_edit`
        # is a positive test on the recorded value, never `!= "command"`,
        # which would silently absorb every legacy row into the edit side.
        kind = rec.get("anchor_kind")
        is_edit = kind == "edit"
        if isinstance(sids, list) and "scar_ids" in rec:
            if is_edit:
                edits_observed += 1
                if not sids:
                    zero_hit_edits += 1
            elif kind == "command":
                command_firings += 1
            else:
                firings_kind_unknown += 1
        if isinstance(sids, list):
            for sid in sids:
                if isinstance(sid, int):
                    counts[sid] = counts.get(sid, 0) + 1
                    if isinstance(target, str) and isinstance(ts_rec, str):
                        key = (sid, target)
                        prev = fired_on.get(key)
                        if prev is None or ts_rec < prev:
                            fired_on[key] = ts_rec
        if isinstance(sids, list) and sids:
            # #294: EDIT rows only. A command match is content-signal by
            # construction (match.py never matches a command on path), so
            # every command row pushes path_only_ratio toward 0 and
            # cofires_per_edit toward 1, both flattering, and neither is a
            # fact about editing. Kind-unknown rows are excluded too: a
            # per-edit mean over rows that may not be edits is not a mean.
            if is_edit:
                matched = rec.get("matched")
                total = matched.get("total") if isinstance(matched, dict) else None
                path_only = matched.get("path_only") if isinstance(matched, dict) else None
                if isinstance(total, int) and isinstance(path_only, int):
                    census_known += 1
                    census_total_sum += total
                    census_path_only_sum += path_only
                    if total >= 2:
                        edits_multi_fire += 1
                else:
                    census_unknown += 1
            armed = rec.get("armed_ids")
            eid = rec.get("edit_id")
            if isinstance(armed, list):
                armed_firings += sum(1 for a in armed if isinstance(a, int))
                # A verdict is owed only when something armed actually fired,
                # AND only when the row was written by a version that could
                # resolve it. The marker carries the second fact; armed_ids
                # alone carries only the first (hooks.VERDICT_EXPECTED_KEY).
                expects = rec.get(_VERDICT_EXPECTED_KEY)
                if any(isinstance(a, int) for a in armed):
                    if expects is not True:
                        # Predates the verdict mechanism, or explicitly
                        # expects nothing: unplaceable, never unresolved.
                        verdicts_unplaceable += 1
                    elif isinstance(eid, str) and eid:
                        verdict_expected_ids.add(eid)
                    else:
                        # Armed and expected, but no correlation key: it can
                        # be neither resolved nor called unresolved.
                        verdicts_unplaceable += 1
            else:
                # Missing or malformed: unplaceable, not unarmed. Blanket
                # tolerance per landmine #12 — any shape appears in this log.
                armed_unknown += sum(1 for x in sids if isinstance(x, int))
                # Predates armed_ids, so whether a verdict was owed is
                # unknowable. Counting it unresolved would make every
                # historical window look permanently broken (#266).
                verdicts_unplaceable += 1
        if isinstance(sids, list) and "scar_ids" in rec:
            # Counted per SCAR-FIRING, not per row, so these share the
            # `total_firings` denominator they are printed beside. Counting
            # rows here would put two different units in one sentence and
            # read as though the remainder were known.
            n = sum(1 for x in sids if isinstance(x, int))
            ctx = rec.get(_CONTEXT_BYTES_KEY)
            if isinstance(ctx, int) and not isinstance(ctx, bool):
                firings_context_known += n
            else:
                firings_context_unknown += n
            capable = rec.get("block_capable")
            if capable is True:
                firings_block_capable += n
            elif capable is False:
                firings_advisory += n
            else:
                # Missing or malformed: unknown. NOT advisory — a row that
                # predates the field says nothing about capability, and
                # reading it as "could not block" would understate a host
                # that could (landmine #12 tolerance, #266 convention).
                firings_block_unknown += n
        if rec.get("verdict_observed") is True:
            verdicts_observed += 1
            eid = rec.get("edit_id")
            if isinstance(eid, str) and eid:
                verdict_seen_ids.add(eid)
        dids = rec.get("demoted_ids", [])
        reasons = rec.get("demotion_reasons")
        if isinstance(dids, list):
            for d in dids:
                if not isinstance(d, int):
                    continue
                demotions += 1
                # str(id) keys: JSON has no int keys. A missing dict OR a
                # missing key is the same fact — this demotion's reason was
                # never recorded — and both land in unknown.
                reason = reasons.get(str(d)) if isinstance(reasons, dict) else None
                if reason == DEMOTED_PATH_ONLY:
                    demotions_path_only += 1
                elif reason == DEMOTED_COOLDOWN:
                    demotions_cooldown += 1
                else:
                    demotions_reason_unknown += 1
        vids = rec.get("violation_ids", [])
        if isinstance(vids, list) and isinstance(target, str) and isinstance(ts_rec, str):
            for vid in vids:
                if isinstance(vid, int):
                    pending_violations.append((vid, target, ts_rec))
        if isinstance(vids, list):
            # Guard: violation_ids might be garbage in corrupted records —
            # skip the whole record unless it's a list, and skip any
            # non-int element within it, rather than coercing or crashing.
            for vid in vids:
                if isinstance(vid, int):
                    violations[vid] = violations.get(vid, 0) + 1
        # `last_fired` means LAST FIRED. Only records where a scar actually
        # fired may move it — not posttool violation records, not zero-hit
        # precheck passes. Otherwise a repo with zero firings still reports a
        # "last fired" date, and #237's age line contradicts the count above
        # it on the very output that is meant to expose a dead instrument.
        fired_here = isinstance(sids, list) and any(isinstance(x, int) for x in sids)
        if fired_here and isinstance(ts_rec, str) and (last_fired is None
                                                       or ts_rec > last_fired):
            last_fired = ts_rec
    # A violation whose scar never fired on that target BEFORE the violation
    # was recorded is a RETRIEVAL miss: the hazard was hit but the scar was
    # never surfaced for that file. Deliberately window-free — "any earlier
    # firing on this target" needs no lookback constant, so no unvalidated
    # fitted parameter enters the metric (#54/#95 discipline). This is a
    # FLOOR, not a rate: misses on scars carrying no violation: pattern are
    # not observable from this log at all.
    retrieval_misses = 0
    for vid, target, vts in pending_violations:
        first = fired_on.get((vid, target))
        if first is None or first > vts:
            retrieval_misses += 1

    # Merge counts and violations: include all scars that fired OR violated
    all_scar_ids = set(counts.keys()) | set(violations.keys())
    per_scar = sorted(({"id": sid, "count": counts.get(sid, 0), "violations": violations.get(sid, 0)}
                       for sid in all_scar_ids),
                      key=lambda e: (-e["count"], e["id"]))
    # A rate is only defensible once zero-hit passes are being logged. Without
    # them the log contains ONLY edits that matched, so firings/edits is 100%
    # by construction — a flattering number that measures nothing (#217).
    injection_rate = (round((edits_observed - zero_hit_edits) / edits_observed, 3)
                      if zero_hit_edits and edits_observed else None)

    # #237: posttool recording violations while precheck records NOTHING is
    # impossible in a healthy install — a scar cannot be violated on a file
    # without having been matchable on that same file a moment earlier. When
    # we see it, the instrument is disconnected (#236 held this state for a
    # month). This reports an OBSERVED impossibility; it is never a clean
    # bill of health, so an empty log is not "disconnected", it is silent.
    #
    # The denominator here is EVERY precheck row, not `edits_observed` (#294).
    # The question is whether the precheck hook recorded anything at all, and
    # a command row or a row that predates `anchor_kind` answers it just as
    # well as an edit row does. Narrowing this to edits would raise the alarm
    # on every log written before the field existed.
    precheck_rows = edits_observed + command_firings + firings_kind_unknown
    instrument_disconnected = bool(violations) and precheck_rows == 0
    if instrument_disconnected:
        # Every violation in such a window has no prior firing BY
        # CONSTRUCTION, so the miss count measures the broken install, not
        # retrieval. Refuse it, the way injection_rate is refused (#235).
        retrieval_misses = None

    return {"counts": counts, "per_scar": per_scar, "last_fired": last_fired,
            "retrieval_misses": retrieval_misses,
            "demotions": demotions,
            "demotions_path_only": demotions_path_only,
            "demotions_cooldown": demotions_cooldown,
            "demotions_reason_unknown": demotions_reason_unknown,
            # #286. Both ratios are REFUSED (null) when nothing carries a
            # census: 0 would read as "no co-fires" and "no path-only noise".
            # path_only_ratio is a PROXY for the false-anchor rate, labelled
            # as one wherever it renders: the true rate needs ground truth
            # about applicability that nothing here produces.
            "census_known": census_known,
            "census_unknown": census_unknown,
            "cofires_per_edit": (census_total_sum / census_known
                                 if census_known else None),
            "edits_multi_fire": edits_multi_fire,
            "path_only_ratio": (census_path_only_sum / census_total_sum
                                if census_total_sum else None),
            "edits_observed": edits_observed,
            # #294. `edits_observed`, `zero_hit_edits`, `injection_rate`,
            # `cofires_per_edit` and `path_only_ratio` are all EDIT-scoped;
            # these two name what was left out and why, so the excluded rows
            # are visible rather than merely absent.
            "command_firings": command_firings,
            "firings_kind_unknown": firings_kind_unknown,
            "armed_firings": armed_firings,
            "armed_unknown": armed_unknown,
            # #277: the posttool half's own liveness. `posttool_silent` is
            # ONE-DIRECTIONAL like every other health field here — it can show
            # the half is dead, and observing a verdict never certifies it
            # healthy, it only withdraws the alarm.
            "verdicts_expected": len(verdict_expected_ids),
            "verdicts_observed": verdicts_observed,
            "verdicts_unresolved": len(verdict_expected_ids - verdict_seen_ids),
            "verdicts_unplaceable": verdicts_unplaceable,
            "firings_block_capable": firings_block_capable,
            "firings_advisory": firings_advisory,
            "firings_block_unknown": firings_block_unknown,
            # "Entirely" is a universal claim, so ONE unplaceable row is
            # enough to make it unsayable. Reported false in that case, which
            # means "cannot say", not "some firing could block".
            "firings_context_known": firings_context_known,
            "firings_context_unknown": firings_context_unknown,
            "all_firings_advisory": (firings_advisory > 0
                                     and firings_block_capable == 0
                                     and firings_block_unknown == 0),
            "posttool_silent": bool(verdict_expected_ids) and verdicts_observed == 0,
            "injection_rate": injection_rate,
            "instrument_disconnected": instrument_disconnected,
            "last_fired_age_days": _age_in_days(last_fired),
            "total": sum(counts.values())}


def _age_in_days(ts: str | None) -> int | None:
    """Whole days between a firing-log timestamp and now, or None if it can't
    be read. scar 0012: firing-log readers need blanket guards — a corrupt ts
    must never raise out of a reporting command."""
    if not isinstance(ts, str):
        return None
    try:
        when = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    try:
        return max(0, (datetime.now(tz=when.tzinfo) - when).days)
    except (OverflowError, OSError, ValueError):
        return None


def _cmd_stats(args) -> int:
    """Aggregate the firing log the precheck hook writes (#106): total
    firings, per-scar counts, the most-fired scar, the last-fired timestamp,
    and which currently-firing scars have never fired. The log is
    machine-global but scar ids are per-repo, so the default view is scoped
    to the current repo's records (#137); --all-repos shows every repo,
    grouped, never merging ids across repos. FIRING COUNTS only — this cannot
    report whether the agent honored an injected scar, only that it was shown
    to it (unobservable from inside the hook)."""
    from .hooks import firing_log_path

    log_path = firing_log_path()
    records = _read_firing_log(log_path)

    # Window BEFORE any metric runs, so injection_rate and retrieval_misses are
    # computed by the same code that computes them unwindowed (#258). A bad
    # bound is an error, never a silently ignored filter — a typo'd date that
    # quietly widened the window would corrupt a pre-registered measurement.
    lo = hi = None
    since, until = getattr(args, "since", None), getattr(args, "until", None)
    for text, end_of_day, flag in ((since, False, "--since"), (until, True, "--until")):
        if text is None:
            continue
        parsed = _parse_window_bound(text, end_of_day=end_of_day)
        if parsed is None:
            print(f"{flag}: not an ISO date or datetime: {text!r}", file=sys.stderr)
            return 1
        if flag == "--since":
            lo = parsed
        else:
            hi = parsed
    if lo is not None and hi is not None and lo > hi:
        print(f"--since ({since}) is after --until ({until})", file=sys.stderr)
        return 1
    records, excluded_undated = _filter_window(records, lo, hi)
    window = ({"since": since, "until": until,
               "excluded_undated": excluded_undated}
              if (lo is not None or hi is not None) else None)

    if getattr(args, "all_repos", False):
        return _stats_all_repos(records, log_path, args, window)

    store = _require_store()
    if store is None:
        return 1

    # Scope to this repo: the hook stamps each record with str(store.root)
    # (resolved), so exact string match is the same resolution on both sides.
    # Records missing the field can't be attributed — excluded, not guessed.
    repo_key = str(store.root)
    agg = _aggregate_firings([r for r in records if r.get("repo") == repo_key])
    counts, per_scar, last_fired = agg["counts"], agg["per_scar"], agg["last_fired"]
    fired_scars = [e for e in per_scar if e["count"] > 0]
    most_fired = fired_scars[0]["id"] if fired_scars else None
    firing_ids = {s.id for _f, s in store.firing() if s.id is not None}
    never_fired = sorted(firing_ids - set(counts))

    ADVISORY_MIN_TOTAL = 20
    ADVISORY_SHARE = 0.5
    total = sum(counts.values())
    advisories = [
        {"id": e["id"], "share": round(e["count"] / total, 2),
         "note": ("likely over-broad — narrow the path anchor or rely on a "
                  "pattern/symbol anchor so it fires on relevant edits only")}
        for e in per_scar
        if total > ADVISORY_MIN_TOTAL and e["count"] / total > ADVISORY_SHARE
    ]

    data = {
        "repo": repo_key,
        "total_firings": total,
        "per_scar": per_scar,
        "most_fired": most_fired,
        "last_fired": last_fired,
        "never_fired": never_fired,
        **_stage_block(agg),
        **_health_block(agg),
        "advisories": advisories,
    }
    if window is not None:
        data["window"] = window

    def plain():
        if window is not None:
            print(f"scar stats: window {window['since'] or 'start'} .. "
                  f"{window['until'] or 'now'}"
                  + (f" ({window['excluded_undated']} record(s) excluded: "
                     "no usable timestamp)"
                     if window["excluded_undated"] else ""))
        print(f"scar stats: {data['total_firings']} firing(s) recorded "
              f"for {repo_key} ({log_path})")
        for e in per_scar:
            line = f"  #{e['id']}: {e['count']} fire(s)"
            if e.get("violations", 0) > 0:
                line += f", violations: x{e['violations']}"
            print(line)
        if most_fired is not None:
            print(f"  most-fired: #{most_fired}")
        if last_fired:
            print(f"  last fired: {last_fired}")
        if never_fired:
            print("  never fired: " + ", ".join(f"#{i}" for i in never_fired))
        for line in _health_lines(data):
            print(f"  {line}")
        for line in _stage_lines(data, per_scar, data["total_firings"]):
            print(f"  {line}")
        for adv in data["advisories"]:
            print(f"  advisory: #{adv['id']} = {int(adv['share'] * 100)}% of "
                  f"firings — {adv['note']}")
        print(f"  {RETRIEVAL_FLOOR_NOTE}")
        print("  note: firing + violation counts — whether the agent honored an "
              "injected scar is unobservable from inside the hook")

    output.render(data=data, json_flag=getattr(args, "json", False),
                  tty=lambda: _stats_rich(data), plain=plain)
    return 0


def _stats_all_repos(records: list[dict], log_path: Path, args,
                     window: dict | None = None) -> int:
    """Machine-global stats view (#137): one group per repo recorded in the
    log, each aggregated independently. Ids are NEVER merged across repos —
    a foreign repo's #1 is a different scar than this repo's #1. Repo-local
    surfaces (never-fired, advisories, most-fired) don't apply here; they
    need a .scars/ store to mean anything."""
    by_repo: dict[str, list[dict]] = {}
    for rec in records:
        repo = rec.get("repo")
        key = repo if isinstance(repo, str) and repo else "(unknown)"
        by_repo.setdefault(key, []).append(rec)

    groups = []
    for repo_key in sorted(by_repo):
        agg = _aggregate_firings(by_repo[repo_key])
        groups.append({"repo": repo_key, "total_firings": agg["total"],
                       "per_scar": agg["per_scar"],
                       "last_fired": agg["last_fired"],
                       **_stage_block(agg), **_health_block(agg)})
    groups.sort(key=lambda g: (-g["total_firings"], g["repo"]))
    data = {"all_repos": True, "repos": groups}
    if window is not None:
        data["window"] = window

    def plain():
        if window is not None:
            print(f"scar stats --all-repos: window {window['since'] or 'start'} "
                  f".. {window['until'] or 'now'}"
                  + (f" ({window['excluded_undated']} record(s) excluded: "
                     "no usable timestamp)" if window["excluded_undated"] else ""))
        print(f"scar stats --all-repos: {len(groups)} repo(s) in {log_path}")
        for g in groups:
            print(f"  {g['repo']}: {g['total_firings']} firing(s)")
            for e in g["per_scar"]:
                line = f"    #{e['id']}: {e['count']} fire(s)"
                if e.get("violations", 0) > 0:
                    line += f", violations: x{e['violations']}"
                print(line)
            for line in _health_lines(g):
                print(f"    {line}")
            for line in _stage_lines(g, g["per_scar"], g["total_firings"]):
                print(f"    {line}")
        print("  note: ids are per-repo — the same number in two repos is "
              "two different scars")
        print(f"  {RETRIEVAL_FLOOR_NOTE}")

    output.render(data=data, json_flag=getattr(args, "json", False),
                  tty=lambda: _stats_all_repos_rich(data, log_path), plain=plain)
    return 0


def _cmd_gc(args) -> int:
    """Clean machine state (~/.claude/scar-state/ or SCAR_STATE_DIR), report
    repo hygiene. Posture (#115): machine state is regenerable, so gc ACTS on
    it (deletes stale drafted-* markers, truncates firing-log.jsonl); .scars/
    is human-gated, so gc only ever REPORTS on it — candidate ages, fp-log.txt
    presence — never writes there (same ethos as promote/reanchor)."""
    from .hooks import _state_dir, firing_log_path
    from . import gc as gc_mod

    store = _require_store()
    if store is None:
        return 1

    dry_run = args.dry_run
    state_dir = _state_dir()
    removed_markers = gc_mod.prune_markers(state_dir, args.days, dry_run=dry_run)
    dropped_firings = gc_mod.truncate_firing_log(
        firing_log_path(), args.max_firings, dry_run=dry_run)
    candidates = gc_mod.candidate_ages(store)
    fp_log = gc_mod.fp_log_report(store)

    data = {
        "removed_markers": len(removed_markers),
        "dropped_firings": dropped_firings,
        "dry_run": dry_run,
        "candidates": candidates,
        "fp_log": fp_log,
    }

    def plain():
        verb = "would remove" if dry_run else "removed"
        fverb = "would drop" if dry_run else "dropped"
        print(f"scar gc: {verb} {data['removed_markers']} stale marker(s) "
              f"(older than {args.days}d) in {state_dir}")
        print(f"  firing log: {fverb} {dropped_firings} line(s) "
              f"(kept newest {args.max_firings})")
        print(f"  {len(candidates)} candidate(s) pending review")
        for c in candidates:
            print(f"    {c['name']} — {c['age_days']}d old")
        if candidates:
            print("    review with `scar promote <path>` or delete rejected files")
        if fp_log["present"]:
            print(f"  fp-log.txt: {fp_log['lines']} line(s), {fp_log['size']} byte(s) "
                  "— drafter-precision instrument data, not auto-cleaned")
        else:
            print("  fp-log.txt: not present")

    output.render(data=data, json_flag=getattr(args, "json", False),
                  tty=lambda: _gc_rich(data, days=args.days,
                                       max_firings=args.max_firings, state_dir=state_dir),
                  plain=plain)
    return 0


def _cmd_draft_check(args) -> int:
    """Universal authoring trigger (#117): transcript-free abandonment
    detection from git evidence, for any runtime (not just Claude Code's
    stop_drafter). Advisory only — ALWAYS returns 0, whatever happens; a
    hook can't gate a commit that already landed, and a direct human
    invocation should never fail a shell script that chains it."""
    from . import draftcheck
    from .hooks import _state_dir

    try:
        state_dir = _state_dir()
        repo = Path.cwd()

        if args.from_hook and draftcheck.is_throttled(state_dir, repo):
            return 0  # nudged within the last hour; stay silent, don't re-analyze

        result = draftcheck.analyze(state_dir, repo)
        if result is None:
            return 0  # not a git repo (or git unusable here) — silent, never a crash

        data = {"triggered": result.triggered, **result.signal_counts()}

        if not result.triggered:
            output.render(data=data, json_flag=getattr(args, "json", False),
                          tty=lambda: None, plain=lambda: None)
            return 0

        store = ScarStore.discover(repo)
        if store is None:
            return 0  # nowhere to write a candidate — same posture as the other hooks

        text = draftcheck.contract_text(store, result)
        data["message"] = text

        def plain():
            print(text)

        def tty():
            from rich.panel import Panel
            output.console.print(Panel(text, title="scar draft-check", border_style="yellow"))

        output.render(data=data, json_flag=getattr(args, "json", False), tty=tty, plain=plain)

        if args.from_hook:
            draftcheck.touch_throttle(state_dir, repo)
        return 0
    except Exception:
        # Contract (issue #117): advisory only, ALWAYS exit 0. A hook can't
        # gate a commit that already landed, and no internal failure here
        # (bad SCAR_STATE_DIR, git surprises, whatever) may propagate — same
        # fail-open posture as hooks.py's precheck (#91).
        return 0


def _cmd_inject(args) -> int:
    """Machine mode for hooks: JSON additionalContext or silence."""
    start = Path(args.path).resolve() if args.path else Path.cwd()
    store = ScarStore.discover(start)
    if store is None:
        return 0  # hooks must never fail the edit
    # Max 3 injected is a format-level guarantee (SPEC/ROADMAP), not a tuning
    # knob — clamp so no caller can widen the fatigue budget (#91).
    top_k = min(args.top_k, 3)
    if args.diff:
        try:
            diff_text = Path(args.diff).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            # ValueError covers NUL-byte paths; a hook must never crash on
            # whatever lands in --diff — fall back to treating it as text
            diff_text = args.diff
        matches = rank_matches_for_diff(store, diff_text, top_k=top_k)
    elif getattr(args, "shell_command", None):
        from .match import rank_matches_for_command
        matches = rank_matches_for_command(store, args.shell_command, top_k=top_k)
    elif args.path:
        matches = rank_matches_for_edit(store, Path(args.path).resolve(),
                                        args.content or "", top_k=top_k)
    else:
        matches = []
    full = [m.scar for m in matches if has_content_signal(m)]
    demoted = [(m.scar, "path-only match")
               for m in matches if not has_content_signal(m)]
    context = injection_context(full, store.broken(), store.scars_dir,
                                demoted=demoted)
    if context:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": args.hook_event, "additionalContext": context}}))
    return 0


# Per-section display: section key -> (heading, one-line formatter). The
# formatters omit the leading "- " and the [id score] prefix; _cmd_harvest
# adds those uniformly so both the sectioned and --top-k views match.
_HARVEST_SECTIONS = [
    ("reverts", "Revert-shaped commits (deadend candidates)",
     lambda c: f"`{c['commit']}` {c['date']} — {c['subject']}"),
    ("deleted_components", "Components tried then deleted (deadend candidates)",
     lambda c: f"**{c['component']}** died {c['died']} (`{c['death_commit']}` {c['death_subject']})"),
    ("flapping", "Flapping values A->B->A (fence candidates)",
     lambda c: f"`{c['file']}` **{c['key']}**: {c['sequence']}"),
    ("comments", "Comment archaeology (fence candidates)",
     lambda c: f"`{c['location']}` — {c['text']}"),
]
_HARVEST_FMT = {key: fmt for key, _title, fmt in _HARVEST_SECTIONS}


def _harvest_line(section_key: str, c: dict) -> str:
    """One rendered candidate line: id + score prefix the human-readable body.
    The id is what `scar harvest --label` consumes; score is the rank key."""
    return f"- [{c['id']} score {c['score']:.1f}] {_HARVEST_FMT[section_key](c)}"


# Labels JSONL lives under the harvested repo at .scars/harvest-labels.jsonl.
# It used to default to experiments/harvest/labels.jsonl, which leaked an
# untracked experiments/ directory into every adopter's working tree (#106) —
# .scars/ is already the repo-owned, human-gated home for scar data. Tests set
# LABELS_PATH_OVERRIDE to a tmp path so they never touch the real file.
LABELS_PATH_OVERRIDE: Path | None = None
_OLD_LABELS_RELPATH = Path("experiments") / "harvest" / "labels.jsonl"
_VALID_LABELS = ("keep", "discard")


def _labels_path(repo: Path) -> Path:
    """Resolve where NEW label judgements are appended. Override wins (tests);
    otherwise .scars/harvest-labels.jsonl under the harvested repo root. Never
    writes to the pre-#106 location — see _labels_read_path for the read-side
    fallback that keeps existing local label sets from being orphaned."""
    if LABELS_PATH_OVERRIDE is not None:
        return LABELS_PATH_OVERRIDE
    return repo / ".scars" / "harvest-labels.jsonl"


def _labels_read_path(repo: Path) -> Path:
    """Resolve where labels are READ from. Same as _labels_path, except: if
    the new path doesn't exist yet but the old (pre-#106) path does, read the
    old path — so a local label set recorded before this change still counts
    instead of silently reporting 'no labels'."""
    if LABELS_PATH_OVERRIDE is not None:
        return LABELS_PATH_OVERRIDE
    new_path = _labels_path(repo)
    old_path = repo / _OLD_LABELS_RELPATH
    if not new_path.exists() and old_path.exists():
        return old_path
    return new_path


def _harvest_candidate_ids(repo: Path) -> set[str]:
    """All candidate ids the current harvest of `repo` produces — the valid set
    --label may reference (mirrors orphan --apply validating --id)."""
    from .harvest import harvest
    result = harvest(repo)
    return {c["id"] for cands in result.values() for c in cands}


def _harvest_label(repo: Path, args) -> int:
    cid, label = args.label
    if label not in _VALID_LABELS:
        print(f"invalid label '{label}'; use one of: {', '.join(_VALID_LABELS)} "
              "(precision@N counts only keep/discard — a third value corrupts it)")
        return 1
    if cid not in _harvest_candidate_ids(repo):
        print(f"id '{cid}' is not a harvest candidate of {repo.name} — "
              "run `scar harvest` to list valid ids; nothing recorded")
        return 1
    record = {
        "id": cid,
        "label": label,
        "note": args.note,
        "date": time.strftime("%Y-%m-%d"),
        "repo": repo.name,
    }
    path = _labels_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    print(f"recorded {label} for {cid} -> {path}")
    return 0


# Below this many labels the heuristic weights are still intuition (see the
# tuning note in harvest.py) — precision@N is reported but flagged as unstable.
_CALIBRATION_THRESHOLD = 50
_DEFAULT_PRECISION_NS = [5, 10, 20]


def _load_labels(path: Path) -> dict[str, str]:
    """Read labels.jsonl into {id: label}; last write wins (a re-label supersedes).
    Missing file or malformed lines are skipped — reporting must never crash."""
    labels: dict[str, str] = {}
    if not path.exists():
        return labels
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        cid, lab = rec.get("id"), rec.get("label")
        if cid and lab:
            labels[cid] = lab
    return labels


def _parse_at(spec: str | None) -> list[int]:
    """Parse --at '5,10,25' → [5,10,25]; falls back to the default N set."""
    if not spec:
        return _DEFAULT_PRECISION_NS
    ns = [int(t) for t in spec.split(",") if t.strip().isdigit()]
    return ns or _DEFAULT_PRECISION_NS


def _harvest_precision(repo: Path, args) -> int:
    """Report precision@N of the harvest ranking against labels.jsonl, with the
    base rate (no-ranking baseline) and lift, so the scorer's value is measurable."""
    from .harvest import harvest, precision_report
    result = harvest(repo)
    flat = [c for cands in result.values() for c in cands]
    flat.sort(key=lambda c: c["score"], reverse=True)

    labels = _load_labels(_labels_read_path(repo))
    if not labels:
        print(f"no labels yet for {repo.name} — run "
              f"`scar harvest --label <id> keep|discard` to start "
              f"({_labels_path(repo)})")
        return 0

    rep = precision_report(flat, labels, _parse_at(args.at))
    print(f"# Harvest precision — {repo.name} "
          f"({rep['total']} candidates, {rep['labeled']} labeled)")
    print(f"base rate (all labeled): {rep['base_rate']:.2f}")
    for e in rep["at"]:
        sign = "+" if e["lift"] >= 0 else ""
        print(f"precision@{e['n']}: {e['precision']:.2f}  "
              f"(lift {sign}{e['lift']:.2f}, {e['labeled_in_top']}/{e['n']} labeled)")
    if rep["labeled"] < _CALIBRATION_THRESHOLD:
        print(f"note: {rep['labeled']} labels — weights uncalibrated until "
              f"~{_CALIBRATION_THRESHOLD}; precision unstable, treat lift as directional")
    return 0


# --- harvest --write (#136: cold-start bridge) -----------------------------
# Mined candidates are ~13% precision; the cap keeps a first run from burying
# the reviewer in dozens of mostly-noise drafts.
_WRITE_CAP = 20

# Section -> scar type, mirroring the section titles rendered above: reverts
# and deletions read as tried-and-failed (deadend); flapping values and
# keep-out comments read as looks-wrong-but-intentional (fence).
_WRITE_SCAR_TYPES = {"reverts": "deadend", "deleted_components": "deadend",
                     "flapping": "fence", "comments": "fence"}


def _sanitize_signal_text(s: str) -> str:
    """Mined git text (revert subjects, squash-merge subjects) can carry
    markdown links, emphasis markers, and bare URLs; leaked into a candidate
    they make the title unreadable and a URL eats the whole slug budget (#159)."""
    import re as _re
    s = _re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)  # [text](url) -> text
    s = _re.sub(r"https?://\S+", "", s)
    s = _re.sub(r"[*_`]+", "", s)
    return " ".join(s.split())


def _write_slug(title: str) -> str:
    import re as _re
    s = _re.sub(r"[^a-z0-9]+", "-", _sanitize_signal_text(title).lower()).strip("-")
    if len(s) > 60:
        cut = s[:60]
        if s[60] != "-" and "-" in cut:  # sliced mid-token: drop the fragment
            cut = cut.rsplit("-", 1)[0]
        s = cut.rstrip("-")
    return s or "signal"


def _write_anchors(repo: Path, section: str, c: dict, tracked: set[str]) -> list[str]:
    """Live tracked paths this candidate can anchor to — empty list means the
    candidate would be an orphan at birth (dead anchors) and must be skipped.
    Reverts carry no path themselves; pull the reverted commit's files."""
    from .harvest import _git as _hgit
    if section == "reverts":
        files = [ln.strip() for ln in
                 _hgit(repo, "show", "--name-only", "--format=", c["commit"]).splitlines()
                 if ln.strip()]
        return [f for f in files if f in tracked][:3]
    from .harvest import _SECTION_TYPES, _candidate_path
    path = _candidate_path(_SECTION_TYPES[section], c)
    return [path] if path in tracked else []


def _write_title(section: str, c: dict) -> str:
    if section == "reverts":
        raw = f"Reverted: {c['subject']}"
    elif section == "deleted_components":
        raw = f"Deleted component: {c['component']}"
    elif section == "flapping":
        raw = f"Flapping value {c['key']} in {c['file']}"
    else:
        raw = "Keep-out comment in " + c["location"].split(":", 1)[0]
    return _sanitize_signal_text(raw)


def _write_evidence(section: str, c: dict) -> str:
    if section == "reverts":
        return f"commit: {c['commit']}"
    if section == "deleted_components":
        return f"commit: {c['death_commit']}"
    if section == "flapping":
        return f"note: harvest flapping signal {c['sequence']} in {c['file']}"
    return f"note: harvest comment signal at {c['location']}"


def _harvest_write(repo: Path, args) -> int:
    """Render the top-N live-anchored harvest candidates as reviewable
    candidate files in .scars/candidates/ (#136). Drafts only: status
    candidate, low confidence, provenance in the body — the human promotes or
    deletes. Never writes into .scars/ root; never overwrites; skips (and
    reports) candidates whose paths are gone from the tree."""
    from .harvest import _git as _hgit, harvest
    n = args.write
    if n < 1 or n > _WRITE_CAP:
        print(f"harvest --write: N must be 1..{_WRITE_CAP} — mined candidates "
              "run ~13% precision, more drafts than that buries the reviewer")
        return 1
    cand_dir = repo / ".scars" / "candidates"
    if not cand_dir.is_dir():
        print(f"no .scars/candidates/ in {repo} — run `scar init` there first")
        return 1

    result = harvest(repo)
    tracked = set(_hgit(repo, "ls-files").splitlines())
    flat = [(key, c) for key, cands in result.items() for c in cands]
    flat.sort(key=lambda kc: kc[1]["score"], reverse=True)

    today = time.strftime("%Y-%m-%d")
    written: list[str] = []
    skipped_dead: list[str] = []
    skipped_existing: list[str] = []
    taken = 0  # written + already-present: an existing draft still occupies
    for section, c in flat:  # its top-N slot, else rerun would fall through
        if taken == n:       # to lower-ranked candidates (not idempotent)
            break
        anchors = _write_anchors(repo, section, c, tracked)
        if not anchors:
            skipped_dead.append(c["id"])
            continue
        title = _write_title(section, c)
        name = f"harvest-{_write_slug(title)}.md"
        if (cand_dir / name).exists():
            skipped_existing.append(name)
            taken += 1
            continue
        body = (
            f"Mined by `scar harvest` from git history — UNVERIFIED draft "
            f"(~13% precision expected).\n"
            f"Signal: {_HARVEST_FMT[section](c)}\n\n"
            f"A human must review this: confirm the constraint is real, rewrite "
            f"this body with\nthe actual why, then `scar promote {name}` — or "
            f"delete the file if the signal\nis noise.")
        scar = Scar(type=_WRITE_SCAR_TYPES[section], title=title, severity="low",
                    confidence=0.3, created=today, authors=["scar-harvest"],
                    path_anchors=anchors,
                    evidence=[_write_evidence(section, c)],
                    status="candidate", body=body)
        (cand_dir / name).write_text(scar.to_text(), encoding="utf-8")
        written.append(name)
        taken += 1

    print(f"harvest --write: {len(written)} candidate draft(s) -> "
          f"{cand_dir.relative_to(repo) if cand_dir.is_relative_to(repo) else cand_dir}")
    for name in written:
        print(f"  {name}")
    if skipped_dead:
        print(f"  skipped {len(skipped_dead)} candidate(s) with no live tracked "
              f"path (would be orphans at birth): {', '.join(skipped_dead)}")
    if skipped_existing:
        print(f"  skipped {len(skipped_existing)} — file(s) already exist: "
              f"{', '.join(sorted(set(skipped_existing)))}")
    if written:
        print("  next: review each draft, then `scar promote` it — or delete it")
    return 0


def _cmd_harvest(args) -> int:
    from .harvest import GitError, harvest  # subprocess-heavy; import only when used
    repo = Path(args.repo).resolve()

    try:
        if args.label is not None:
            return _harvest_label(repo, args)

        if args.precision:
            return _harvest_precision(repo, args)

        if args.write is not None:
            return _harvest_write(repo, args)

        result = harvest(repo)
    except GitError as exc:
        # Surface the git failure loudly instead of printing an empty harvest
        # that reads as "nothing to mine" (landmine #1).
        print(f"harvest: {exc}")
        return 1
    total = sum(len(v) for v in result.values())

    if args.top_k is not None:
        # Cross-section ranking by RAW score, no normalization. The per-type
        # base constants (comment < flapping < deleted < revert) are an
        # intentional precision prior: signal type predicts precision, so a
        # revert outranks a grep hit by design. Normalizing would erase that.
        flat = [(key, c) for key, cands in result.items() for c in cands]
        flat.sort(key=lambda kc: kc[1]["score"], reverse=True)
        top = flat[:args.top_k]

        def plain_top():
            print(f"# Harvest top {len(top)} — {repo.name} "
                  f"(of {total} raw, cross-section by raw score; "
                  "curation required, expect ~13% precision)\n")
            for key, c in top:
                print(_harvest_line(key, c))

        output.render(data={"top": [{"section": k, **c} for k, c in top]},
                      json_flag=False,
                      tty=lambda: _harvest_top_rich(repo.name, total, top),
                      plain=plain_top)
        return 0

    def plain_all():
        print(f"# Harvest candidates — {repo.name} "
              f"({total} raw; curation required, expect ~13% precision)\n")
        for key, title, _fmt in _HARVEST_SECTIONS:
            print(f"## {title} ({len(result[key])})")
            for c in result[key]:
                print(_harvest_line(key, c))
            print()

    output.render(data=result, json_flag=False,
                  tty=lambda: _harvest_rich(repo.name, total, result),
                  plain=plain_all)
    return 0


def _cmd_agent(args) -> int:
    from .agent import config, doctor, skill
    if args.agent_command == "doctor":
        lines = doctor(Path.cwd())

        def plain():
            for line in lines:
                print(line)

        output.render(data={"doctor": lines}, json_flag=False,
                      tty=lambda: _agent_doctor_rich(lines), plain=plain)
        return 0
    if args.agent_command == "skill":
        print(skill())
        return 0
    try:
        print(config(args.target))
    except ValueError as exc:
        print(str(exc))
        return 1
    return 0


def _cmd_mcp(args) -> int:
    from .mcp import serve
    return serve()


def _cmd_hook(args) -> int:
    if args.kind in ("install", "uninstall", "status"):
        return _cmd_hook_lifecycle(args)
    if args.kind.startswith("codex-"):
        from .codex import HANDLERS
    else:
        from .hooks import HANDLERS  # hot path: imports nothing beyond library
    return HANDLERS[args.kind]()


def _cmd_cascade_hook(args) -> int:
    """Windsurf/Cascade entrypoint (#197): Cascade JSON on stdin, exit code
    out. Returns cascade.BLOCK_EXIT to bounce the action once — never 2
    directly; see cascade.py for why the installed command does that mapping."""
    from .cascade import cascade_hook  # hot path: imports nothing beyond library
    return cascade_hook()


def _detect(kind: str):
    """Which hosts exist here, and which channel already serves each one.
    Pure read: hosts.py never writes and never prompts (#303)."""
    from . import hosts, installer
    home = installer.CLAUDE_DIR.parent
    repo = Path.cwd()
    found = hosts.detect_hosts(home, repo, kind=kind,
                               path_env=os.environ.get("PATH", ""),
                               codex_dir=installer.codex_home())
    resolved = hosts.resolve_channels(found, claude_dir=installer.CLAUDE_DIR,
                                      repo=repo, kind=kind)
    return resolved, repo


def _run_hook_installers(names: list[str], repo: Path, dry: bool) -> int:
    """Worst exit code wins: one host failing must not be reported as success."""
    from .installer import cascade_install, codex_install, install
    rc = 0
    for name in names:
        print(f"== {name}")
        if name == "claude":
            rc = max(rc, install(dry=dry))
        elif name == "codex":
            rc = max(rc, codex_install(dry=dry))
        elif name == "windsurf":
            rc = max(rc, cascade_install(repo, dry=dry))
    return rc


def _cmd_hook_lifecycle(args) -> int:
    from . import hosts
    from .installer import (
        CLAUDE_DIR,
        cascade_install,
        cascade_status,
        cascade_uninstall,
        codex_install,
        codex_status,
        codex_uninstall,
        git_hook_install,
        git_hook_status,
        git_hook_uninstall,
        install,
        status,
        uninstall,
    )
    dry = args.dry_run
    # --all sits in the target group, so argparse already rejects it next to
    # --runtime or --git. --force is not a target, so it is checked here, and
    # checked by returning: commands never raise to the shell.
    if getattr(args, "force", False) and not args.runtime:
        print("--force needs --runtime: it overrides the plugin check for one "
              "named runtime, and detection never needs overriding.")
        return 2
    if getattr(args, "git", False):
        repo = Path.cwd()
        return {"install": lambda: git_hook_install(repo, dry=dry),
                "uninstall": lambda: git_hook_uninstall(repo, dry=dry),
                "status": lambda: git_hook_status(repo)}[args.kind]()
    runtime = args.runtime
    # No --runtime: look at the machine first. Plain print, not Rich, because
    # the non-tty branch of a read command must stay unwrapped (scar 0008).
    if runtime is None and args.kind == "install":
        found, repo = _detect("hook")
        print(hosts.render_table(found))
        decision = hosts.decide(found, interactive=hosts.is_interactive(),
                                all_flag=getattr(args, "all", False),
                                ask=hosts.ask_yes_no, command="hook")
        for line in decision.lines:
            print(line)
        return _run_hook_installers(decision.install, repo, dry)
    if runtime is None and args.kind == "status":
        found, _ = _detect("hook")
        print(hosts.render_table(found))
        print()
        return status()
    # uninstall with no --runtime keeps targeting Claude: removal is out of
    # the detection spec's scope, and widening it silently would rip hooks
    # out of hosts the user never named.
    runtime = runtime or "claude"
    if (runtime == "claude" and args.kind == "install"
            and not getattr(args, "force", False)
            and hosts.claude_plugin_enabled(CLAUDE_DIR)):
        print("claude: hooks are provided by the scar plugin (scar@scar); "
              "nothing written. Pass --force to install into "
              "~/.claude/settings.json as well.")
        return 0
    if runtime == "codex":
        return {"install": lambda: codex_install(dry=dry),
                "uninstall": lambda: codex_uninstall(dry=dry),
                "status": codex_status}[args.kind]()
    if runtime == "windsurf":
        repo = Path.cwd()
        return {"install": lambda: cascade_install(repo, dry=dry),
                "uninstall": lambda: cascade_uninstall(repo, dry=dry),
                "status": lambda: cascade_status(repo)}[args.kind]()
    return {"install": lambda: install(dry=dry),
            "uninstall": lambda: uninstall(dry=dry),
            "status": status}[args.kind]()


def _cmd_skill_lifecycle(args) -> int:
    from . import hosts
    from .installer import CLAUDE_DIR, skill_install, skill_status, skill_uninstall
    dry = args.dry_run
    # --all sits in the target group, so argparse already rejects it next to
    # --runtime. --force is not a target, so it is checked here, and checked
    # by returning: commands never raise to the shell.
    if getattr(args, "force", False) and not args.runtime:
        print("--force needs --runtime: it overrides the plugin check for one "
              "named runtime, and detection never needs overriding.")
        return 2
    runtime = args.runtime
    # No --runtime: look at the machine first. Plain print, not Rich, because
    # the non-tty branch of a read command must stay unwrapped (scar 0008).
    if runtime is None and args.kind == "install":
        found, _ = _detect("skill")
        print(hosts.render_table(found))
        decision = hosts.decide(found, interactive=hosts.is_interactive(),
                                all_flag=getattr(args, "all", False),
                                ask=hosts.ask_yes_no, command="skill")
        for line in decision.lines:
            print(line)
        return skill_install(dry=dry) if "claude" in decision.install else 0
    if runtime is None and args.kind == "status":
        found, _ = _detect("skill")
        print(hosts.render_table(found))
        print()
        return skill_status()
    # uninstall with no --runtime keeps targeting Claude, the only wirable
    # host today: same rule as `hook`.
    runtime = runtime or "claude"
    if (runtime == "claude" and args.kind == "install"
            and not getattr(args, "force", False)
            and hosts.claude_plugin_enabled(CLAUDE_DIR)):
        print("claude: the scar-authoring skill is provided by the scar plugin "
              "(scar@scar); nothing written. Pass --force to install into "
              "~/.claude/skills as well.")
        return 0
    return {"install": lambda: skill_install(dry=dry),
            "uninstall": lambda: skill_uninstall(dry=dry),
            "status": skill_status}[args.kind]()


def _scar_version() -> str:
    """Installed package version (pyproject is the source of truth via
    release-please). 'unknown' when run from a tree that was never installed."""
    try:
        return _dist_version("scar-cli")
    except PackageNotFoundError:
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    """Construct the fully-wired argparse parser.

    Subparsers do NOT inherit ``formatter_class`` from their parent, so every
    ``add_parser`` call is routed through ``_add`` to wire RichHelpFormatter in
    one place instead of repeating it per command.
    """

    def _add(subparsers, name, func, **kw):
        # set_defaults(func=...) is the dispatch mechanism (#180): keying on
        # args.command let any flag whose dest resolved to "command" shadow
        # the subcommand name and KeyError the dispatch (scar 0014).
        p = subparsers.add_parser(name, formatter_class=RichHelpFormatter, **kw)
        p.set_defaults(func=func)
        return p

    parser = argparse.ArgumentParser(prog="scar",
                                     description="version control for negative knowledge",
                                     formatter_class=RichHelpFormatter)
    # argparse's version action prints and exits during optional parsing, before
    # the required-subcommand check below — so `scar --version` needs no command.
    parser.add_argument("--version", action="version", version=f"scar {_scar_version()}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = _add(sub, "init", _cmd_init, help="create .scars/ layout in the current repo")
    p.add_argument("--no-seed", action="store_true",
                   help="skip the worked-example candidate seeded on fresh init")
    p = _add(sub, "lint", _cmd_lint, help="validate every scar and candidate")
    p.add_argument("--fail-orphans", action="store_true",
                   help="exit non-zero when any scar is orphan-detected")
    p.add_argument("--fail-firing-review", action="store_true",
                   help="exit non-zero when any scar has crossed its "
                        "review_after_firings threshold")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p = _add(sub, "status", _cmd_status, help="counts, titles, broken-file warnings")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    p = _add(sub, "promote", _cmd_promote, help="review a candidate into an active scar")
    p.add_argument("candidate", help="candidate filename (or unique substring)")
    p.add_argument("--reviewer", default="",
                   help="the human vouching for this scar, recorded as promoted_by "
                        "(default: git config user.name)")

    p = _add(sub, "check", _cmd_check, help="scars anchored to a path (CI gate with --exit-code)")
    p.add_argument("path", nargs="*", default=[], help="path(s) to check")
    p.add_argument("--content", default="", help="new code to test pattern anchors against")
    p.add_argument("--diff", help="unified diff text, or path to a diff file — gates on "
                                  "the union of changed files, like `inject --diff`")
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.add_argument("--exit-code", action="store_true",
                   help="exit 1 if any scar fires on the checked path(s)/diff, or any "
                        "violation tripped (CI gate); default is always 0 (back-compat)")

    p = _add(sub, "why", _cmd_why, help="history of pain for a path (any status)")
    p.add_argument("path")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    p = _add(sub, "stats", _cmd_stats, help="firing counts from the precheck hook's firing log")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.add_argument("--all-repos", action="store_true",
                   help="show the whole machine-global log grouped per repo "
                        "(default: only the current repo's records)")
    p.add_argument("--since", metavar="WHEN",
                   help="only records at or after this ISO date/datetime "
                        "(a bare date means 00:00:00 that day)")
    p.add_argument("--until", metavar="WHEN",
                   help="only records at or before this ISO date/datetime "
                        "(a bare date means the END of that day, so the day is "
                        "included)")

    p = _add(sub, "gc", _cmd_gc, help="clean machine state (markers, firing log); "
                             "report .scars/ hygiene (never touches .scars/)")
    p.add_argument("--days", type=int, default=7,
                   help="delete drafted-* markers older than this many days (default 7)")
    p.add_argument("--max-firings", type=int, default=10000,
                   help="truncate firing-log.jsonl to the newest N entries (default 10000)")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would be removed/truncated; change nothing")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    p = _add(sub, "challenge", _cmd_challenge, help="dispute a scar (still fires, marked challenged)")
    p.add_argument("id", type=int)
    p.add_argument("--reason", required=True, help="why the scar may no longer hold")

    p = _add(sub, "archive", _cmd_archive, help="retire a scar (never fires; history kept)")
    p.add_argument("id", type=int)
    p.add_argument("--reason", required=True, help="why it is retired (e.g. expiry condition met)")

    p = _add(sub, "orphan", _cmd_orphan, help="list scars whose anchors all went dead")
    p.add_argument("--apply", action="store_true",
                   help="persist status: orphaned (human review only — never CI)")
    p.add_argument("--id", type=int, default=None,
                   help="with --apply: the detected scar id to persist")
    p.add_argument("--reason", default="anchors no longer resolve",
                   help="with --apply: why it is being orphaned (recorded in the note)")
    p.add_argument("--fix-renames", action="store_true",
                   help="rewrite dead path anchors to their unambiguous, currently-"
                        "tracked git rename target (surgical single-line edit; "
                        "explicit opt-in, never the default — #109)")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON (read mode only)")

    p = _add(sub, "reanchor", _cmd_reanchor, help="propose new anchors for orphaned/partial-rot scars (#111)")
    p.add_argument("--id", type=int, default=None,
                   help="with --apply: the scar id whose eligible anchors to rewrite")
    p.add_argument("--apply", action="store_true",
                   help="rewrite ONE scar's single-high-confidence dead anchors "
                        "(human review only — never CI; never flips status)")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON (propose mode only)")

    p = _add(sub, "harvest", _cmd_harvest, help="mine git history for candidate scars")
    p.add_argument("repo", nargs="?", default=".")
    p.add_argument("--top-k", type=int, default=None,
                   help="show the N highest-scoring candidates across all sections "
                        "(raw score, no cross-type normalization)")
    p.add_argument("--write", type=int, default=None, metavar="N",
                   help=f"write the top-N live-anchored candidates as reviewable "
                        f"draft files in .scars/candidates/ (max {_WRITE_CAP}; "
                        f"never overwrites, human promotes or deletes)")
    p.add_argument("--label", nargs=2, metavar=("ID", "LABEL"), default=None,
                   help="record a curation judgement: <id> keep|discard "
                        "(appends one line to .scars/harvest-labels.jsonl)")
    p.add_argument("--note", default="", help="with --label: free-text rationale")
    p.add_argument("--precision", action="store_true",
                   help="report precision@N of the ranking against labels.jsonl "
                        "(base rate + lift = does ranking beat no-ranking)")
    p.add_argument("--at", default=None,
                   help="with --precision: comma-separated N values (default 5,10,20)")

    p = _add(sub, "draft-check", _cmd_draft_check, help="git-native abandonment nudge — universal "
                                      "authoring trigger for any runtime (#117)")
    p.add_argument("--from-hook", action="store_true",
                   help="invoked by the post-commit git hook; adds a 1h throttle "
                        "per repo so commit-heavy sessions aren't nagged")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    p = _add(sub, "hook", _cmd_hook, help="install, remove, inspect, or run agent hooks "
                                  "(Claude Code by default; --runtime codex|windsurf, "
                                  "--git)")
    p.add_argument("kind", choices=["install", "uninstall", "status",
                                    "precheck", "precheck-command", "posttool",
                                    "session-notice", "stop-drafter",
                                    "codex-pretool", "codex-posttool",
                                    "codex-session-notice"])
    p.add_argument("--dry-run", action="store_true",
                   help="show lifecycle changes without writing settings")
    # One invocation, one target: --git writes .git/hooks/post-commit,
    # --runtime windsurf writes .windsurf/hooks.json, --all writes to every
    # unserved host detection finds, and no flag at all means detect and ask.
    # Passing two can only mean one is ignored.
    target = p.add_mutually_exclusive_group()
    target.add_argument("--git", action="store_true",
                        help="with install/uninstall/status: target this repo's "
                             ".git/hooks/post-commit trigger for `scar draft-check` "
                             "(#117) instead of Claude Code's settings.json")
    target.add_argument("--runtime", choices=["claude", "codex", "windsurf"],
                        default=None,
                        help="with install/uninstall/status: which runtime to wire. "
                             "Omitted: detect installed hosts and ask (install), or "
                             "show every host's channel (status); uninstall still "
                             "targets claude. "
                             "codex writes ~/.codex/hooks.json — shared with other "
                             "tools, so merged, and inactive until you trust the "
                             "entries in Codex's `/hooks` (#246); "
                             "windsurf writes this repo's committed "
                             ".windsurf/hooks.json (Cascade block-once, #197); "
                             "claude writes ~/.claude/settings.json")
    target.add_argument("--all", action="store_true",
                        help="with install: wire every detected, unserved host "
                             "without asking. A target like the other two, so "
                             "argparse rejects it next to --runtime or --git "
                             "rather than letting one be silently ignored")
    p.add_argument("--force", action="store_true",
                   help="with --runtime: install into the settings file even "
                        "though the scar plugin already serves that host")

    _add(sub, "cascade-hook", _cmd_cascade_hook,
         help="run the Windsurf/Cascade hook adapter (stdin JSON; installed by "
              "`scar hook install --runtime windsurf`, not run by hand)")

    p = _add(sub, "skill", _cmd_skill_lifecycle, help="install, remove, or inspect the scar-authoring skill")
    p.add_argument("kind", choices=["install", "uninstall", "status"])
    p.add_argument("--dry-run", action="store_true",
                   help="show changes without writing to ~/.claude/skills")
    # One invocation, one target: --runtime claude writes ~/.claude/skills,
    # --all writes to every unserved host detection finds (only claude is
    # wirable today), and no flag at all means detect and ask.
    target = p.add_mutually_exclusive_group()
    target.add_argument("--runtime", choices=["claude"], default=None,
                        help="with install/uninstall/status: which host to wire. "
                             "Omitted: detect installed hosts and ask (install), "
                             "or show every host's channel (status); uninstall "
                             "still targets claude")
    target.add_argument("--all", action="store_true",
                        help="with install: wire every detected, unserved host "
                             "without asking. A target like --runtime, so "
                             "argparse rejects it next to --runtime rather than "
                             "letting one be silently ignored")
    p.add_argument("--force", action="store_true",
                   help="with --runtime: install even though the scar plugin "
                        "already ships the skill")

    _add(sub, "mcp", _cmd_mcp, help="run the SCAR MCP stdio server")

    p = _add(sub, "agent", _cmd_agent, help="agent integration helpers")
    agent_sub = p.add_subparsers(dest="agent_command", required=True)
    _add(agent_sub, "doctor", _cmd_agent, help="show local agent integration readiness")
    cfg = _add(agent_sub, "config", _cmd_agent, help="print config for an agent runtime")
    cfg.add_argument("target", choices=["codex", "cursor", "opencode", "windsurf"])
    _add(agent_sub, "skill", _cmd_agent, help="print the scar-authoring skill body")

    p = _add(sub, "brief", _cmd_brief, help="paste-ready scar block for sub-agent launch "
                                "prompts (#176) — plain text, byte-capped")
    p.add_argument("--compact", action="store_true", default=True,
                   help="compact one-line-per-scar format (the only mode today)")
    p.add_argument("--paths", nargs="*", default=[],
                   help="filter to scars whose anchors overlap these paths; "
                        "command-anchored scars are always included")
    p.add_argument("--max-chars", type=int, default=2000,
                   help="byte budget for the block (default 2000); omissions "
                        "are reported, never silent")

    p = _add(sub, "inject", _cmd_inject, help="machine mode for hooks: JSON or silence")
    p.add_argument("--path")
    # dest kept off "command" for clarity; dispatch no longer keys on
    # args.command (set_defaults(func=...), #180 — archived scar 0014).
    p.add_argument("--command", dest="shell_command",
                   help="shell command about to execute — fires "
                        "command-anchored scars (#175)")
    p.add_argument("--content", default="")
    p.add_argument("--diff", help="unified diff text, or path to a diff file")
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--hook-event", default="PreToolUse")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Dispatch via the subparser's set_defaults(func=...) — never via
    # args.command, which any same-dest flag could shadow (#180, scar 0014).
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
