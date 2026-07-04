"""scar — the CLI. Thin argparse layer; all logic lives in the library.

Adding a command = one _cmd_* function + one subparser block. Commands that
read scars resolve the store once via _require_store; commands return exit
codes (0 ok, 1 user-visible failure) and never raise to the shell.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
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
    detect_symbol_drift,
)
from .reanchor import (
    dead_symbol_anchors,
    propose_path_reanchors,
    propose_symbol_reanchors_for_scar,
)
from .render import injection_context, label_line
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
        t.add_column("type"); t.add_column("id", justify="right")
        t.add_column("severity"); t.add_column("title")
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
        t.add_column("file"); t.add_column("level"); t.add_column("message")
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
    if data["shallow_clone"]:
        console.print("[dim]note: shallow clone — evidence-reachability check skipped[/]")
    for ue in data["unreachable_evidence"]:
        console.print(f"[yellow]WARNING evidence-unreachable:[/] scar #{ue['scar_id']} — "
                      f"commit {ue['sha']} {ue['reason']}")
    style = "red" if data["failed"] else "green"
    console.print(f"[{style}]lint:[/] {data['files']} file(s), {data['failed']} with errors, "
                  f"{len(data['orphans'])} orphan(s), {len(data['partial_rot'])} partial-rot, "
                  f"{len(data['unreachable_evidence'])} unreachable-evidence")


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
        t.add_column("id", justify="right"); t.add_column("firings", justify="right")
        for e in data["per_scar"]:
            row = f"#{e['id']}", str(e["count"])
            if e.get("violations", 0) > 0:
                row = (f"#{e['id']}", f"{e['count']} (violations: x{e['violations']})")
            t.add_row(*row)
        console.print(t)
    if data["never_fired"]:
        console.print(f"[yellow]never fired:[/] "
                      + ", ".join(f"#{i}" for i in data["never_fired"]))
    for adv in data.get("advisories", []):
        console.print(f"[red]advisory:[/] scar #{adv['id']} accounts for "
                      f"{int(adv['share'] * 100)}% of firings — {adv['note']}")
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
        t.add_column("id", justify="right"); t.add_column("firings", justify="right")
        for e in g["per_scar"]:
            row = f"#{e['id']}", str(e["count"])
            if e.get("violations", 0) > 0:
                row = (f"#{e['id']}", f"{e['count']} (violations: x{e['violations']})")
            t.add_row(*row)
        console.print(t)
    console.print("[dim]note: ids are per-repo — the same number in two repos "
                  "is two different scars[/]")


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
        t.add_column("name"); t.add_column("age (days)", justify="right")
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

    ctx = _repo_context(store)
    if ctx is None:
        orphans, partial, reverse_hints, drift = [], [], [], []
    else:
        orphans = detect_orphans(store, ctx, repo=store.root)
        partial = detect_partial_rot(store, ctx, repo=store.root)
        reverse_hints = [s for _f, s in store.parsed()
                         if s.status == "orphaned" and not anchors_all_dead(s, ctx)]
        drift = detect_symbol_drift(store, store.root)

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
        "reverse_hints": [{"id": s.id} for s in reverse_hints],
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
        # symbol drift (#99): a symbol anchor that still resolves by name but
        # whose body shape changed since the evidence commit. Advisory only.
        for d in drift:
            print(f"HINT symbol-drift: scar #{d.scar_id} — {_symbol_drift_reason(d)} "
                  "— re-verify the scar still describes this symbol")
        # reverse hint: persisted-orphaned scars whose anchors resolve again
        for s in reverse_hints:
            print(f"HINT: scar #{s.id} is marked orphaned but its anchors live "
                  "again — consider re-activating (scar challenge/archive note)")
        if shallow:
            print("note: shallow clone — evidence-reachability check skipped "
                  "(actions/checkout defaults to depth 1; use fetch-depth: 0)")
        for ue in unreachable:
            print(f"WARNING evidence-unreachable: scar #{ue.scar_id} — commit "
                  f"{ue.sha} {ue.reason}, not reachable from HEAD")
        print(f"lint: {len(files)} file(s), {failed} with errors, "
              f"{len(orphans)} orphan(s), {len(partial)} partial-rot, "
              f"{len(unreachable)} unreachable-evidence")

    output.render(data=data, json_flag=getattr(args, "json", False),
                  tty=lambda: _lint_rich(data), plain=plain)

    if failed:
        return 1
    if orphans and getattr(args, "fail_orphans", False):
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

    data = {
        "scars_dir": str(store.scars_dir),
        "active": [{"type": s.type, "id": s.id, "severity": s.severity, "title": s.title}
                   for _f, s in active],
        "challenged": [{"type": s.type, "id": s.id, "title": s.title} for _f, s in challenged],
        "candidates": [c.name for c in cands],
        "review_due": [{"type": s.type, "id": s.id, "review_after": s.review_after} for s in due],
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
    try:
        new_path = store.promote(matches[0], reviewer=reviewer)
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
    data = {"promoted": str(rel), "reviewer": reviewer or None,
            "reviewer_from_git": from_git, "born_orphan": born_orphan}

    def plain():
        print(f"promoted -> {rel}")
        if from_git:
            print(f"  reviewer: {reviewer} (from git config)")
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

    for kind, renamed in renamed_by_kind.items():
        if renamed:
            apply_anchor_rewrite(scar_file, kind, renamed)

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


def _read_firing_log(log_path: Path) -> list[dict]:
    """Read the machine-global firing log, keeping only dict records. The log
    is written best-effort from a fail-open hook, so ANY JSON shape can appear
    on a line (landmine #12) — `null`, `[]`, numbers all parse fine and then
    crash at rec.get(); skip everything that isn't a dict."""
    records = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                records.append(rec)
    return records


def _aggregate_firings(records: list[dict]) -> dict:
    """Aggregate firing-log records into per-scar counts. Callers must pass
    records from ONE repo only — scar ids are per-repo sequential ints, so
    aggregating across repos sums different scars under one id (#137)."""
    counts: dict[int, int] = {}
    violations: dict[int, int] = {}
    last_fired = None
    for rec in records:
        sids = rec.get("scar_ids", [])
        if isinstance(sids, list):
            for sid in sids:
                if isinstance(sid, int):
                    counts[sid] = counts.get(sid, 0) + 1
        vids = rec.get("violation_ids", [])
        if isinstance(vids, list):
            # Guard: violation_ids might be garbage in corrupted records —
            # skip the whole record unless it's a list, and skip any
            # non-int element within it, rather than coercing or crashing.
            for vid in vids:
                if isinstance(vid, int):
                    violations[vid] = violations.get(vid, 0) + 1
        ts = rec.get("ts")
        if isinstance(ts, str) and (last_fired is None or ts > last_fired):
            last_fired = ts
    # Merge counts and violations: include all scars that fired OR violated
    all_scar_ids = set(counts.keys()) | set(violations.keys())
    per_scar = sorted(({"id": sid, "count": counts.get(sid, 0), "violations": violations.get(sid, 0)}
                       for sid in all_scar_ids),
                      key=lambda e: (-e["count"], e["id"]))
    return {"counts": counts, "per_scar": per_scar, "last_fired": last_fired,
            "total": sum(counts.values())}


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

    if getattr(args, "all_repos", False):
        return _stats_all_repos(records, log_path, args)

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
        "advisories": advisories,
    }

    def plain():
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
        for adv in data["advisories"]:
            print(f"  advisory: #{adv['id']} = {int(adv['share'] * 100)}% of "
                  f"firings — {adv['note']}")
        print("  note: firing + violation counts — whether the agent honored an "
              "injected scar is unobservable from inside the hook")

    output.render(data=data, json_flag=getattr(args, "json", False),
                  tty=lambda: _stats_rich(data), plain=plain)
    return 0


def _stats_all_repos(records: list[dict], log_path: Path, args) -> int:
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
                       "last_fired": agg["last_fired"]})
    groups.sort(key=lambda g: (-g["total_firings"], g["repo"]))
    data = {"all_repos": True, "repos": groups}

    def plain():
        print(f"scar stats --all-repos: {len(groups)} repo(s) in {log_path}")
        for g in groups:
            print(f"  {g['repo']}: {g['total_firings']} firing(s)")
            for e in g["per_scar"]:
                line = f"    #{e['id']}: {e['count']} fire(s)"
                if e.get("violations", 0) > 0:
                    line += f", violations: x{e['violations']}"
                print(line)
        print("  note: ids are per-repo — the same number in two repos is "
              "two different scars")

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


def _write_slug(title: str) -> str:
    import re as _re
    s = _re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:60].rstrip("-") or "signal"


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
        return f"Reverted: {c['subject']}"
    if section == "deleted_components":
        return f"Deleted component: {c['component']}"
    if section == "flapping":
        return f"Flapping value {c['key']} in {c['file']}"
    return "Keep-out comment in " + c["location"].split(":", 1)[0]


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


def _cmd_hook_lifecycle(args) -> int:
    if getattr(args, "git", False):
        from .installer import git_hook_install, git_hook_status, git_hook_uninstall
        repo = Path.cwd()
        if args.kind == "install":
            return git_hook_install(repo, dry=args.dry_run)
        if args.kind == "uninstall":
            return git_hook_uninstall(repo, dry=args.dry_run)
        return git_hook_status(repo)
    from .installer import install, status, uninstall
    if args.kind == "install":
        return install(dry=args.dry_run)
    if args.kind == "uninstall":
        return uninstall(dry=args.dry_run)
    return status()


def _cmd_skill_lifecycle(args) -> int:
    from .installer import skill_install, skill_status, skill_uninstall
    if args.kind == "install":
        return skill_install(dry=args.dry_run)
    if args.kind == "uninstall":
        return skill_uninstall(dry=args.dry_run)
    return skill_status()


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

    def _add(subparsers, name, **kw):
        return subparsers.add_parser(name, formatter_class=RichHelpFormatter, **kw)

    parser = argparse.ArgumentParser(prog="scar",
                                     description="version control for negative knowledge",
                                     formatter_class=RichHelpFormatter)
    # argparse's version action prints and exits during optional parsing, before
    # the required-subcommand check below — so `scar --version` needs no command.
    parser.add_argument("--version", action="version", version=f"scar {_scar_version()}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = _add(sub, "init", help="create .scars/ layout in the current repo")
    p.add_argument("--no-seed", action="store_true",
                   help="skip the worked-example candidate seeded on fresh init")
    p = _add(sub, "lint", help="validate every scar and candidate")
    p.add_argument("--fail-orphans", action="store_true",
                   help="exit non-zero when any scar is orphan-detected")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p = _add(sub, "status", help="counts, titles, broken-file warnings")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    p = _add(sub, "promote", help="review a candidate into an active scar")
    p.add_argument("candidate", help="candidate filename (or unique substring)")
    p.add_argument("--reviewer", default="",
                   help="human reviewer to add to authors "
                        "(default: git config user.name)")

    p = _add(sub, "check", help="scars anchored to a path (CI gate with --exit-code)")
    p.add_argument("path", nargs="*", default=[], help="path(s) to check")
    p.add_argument("--content", default="", help="new code to test pattern anchors against")
    p.add_argument("--diff", help="unified diff text, or path to a diff file — gates on "
                                  "the union of changed files, like `inject --diff`")
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.add_argument("--exit-code", action="store_true",
                   help="exit 1 if any scar fires on the checked path(s)/diff, or any "
                        "violation tripped (CI gate); default is always 0 (back-compat)")

    p = _add(sub, "why", help="history of pain for a path (any status)")
    p.add_argument("path")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    p = _add(sub, "stats", help="firing counts from the precheck hook's firing log")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.add_argument("--all-repos", action="store_true",
                   help="show the whole machine-global log grouped per repo "
                        "(default: only the current repo's records)")

    p = _add(sub, "gc", help="clean machine state (markers, firing log); "
                             "report .scars/ hygiene (never touches .scars/)")
    p.add_argument("--days", type=int, default=7,
                   help="delete drafted-* markers older than this many days (default 7)")
    p.add_argument("--max-firings", type=int, default=10000,
                   help="truncate firing-log.jsonl to the newest N entries (default 10000)")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would be removed/truncated; change nothing")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    p = _add(sub, "challenge", help="dispute a scar (still fires, marked challenged)")
    p.add_argument("id", type=int)
    p.add_argument("--reason", required=True, help="why the scar may no longer hold")

    p = _add(sub, "archive", help="retire a scar (never fires; history kept)")
    p.add_argument("id", type=int)
    p.add_argument("--reason", required=True, help="why it is retired (e.g. expiry condition met)")

    p = _add(sub, "orphan", help="list scars whose anchors all went dead")
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

    p = _add(sub, "reanchor", help="propose new anchors for orphaned/partial-rot scars (#111)")
    p.add_argument("--id", type=int, default=None,
                   help="with --apply: the scar id whose eligible anchors to rewrite")
    p.add_argument("--apply", action="store_true",
                   help="rewrite ONE scar's single-high-confidence dead anchors "
                        "(human review only — never CI; never flips status)")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON (propose mode only)")

    p = _add(sub, "harvest", help="mine git history for candidate scars")
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

    p = _add(sub, "draft-check", help="git-native abandonment nudge — universal "
                                      "authoring trigger for any runtime (#117)")
    p.add_argument("--from-hook", action="store_true",
                   help="invoked by the post-commit git hook; adds a 1h throttle "
                        "per repo so commit-heavy sessions aren't nagged")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    p = _add(sub, "hook", help="install, remove, inspect, or run Claude Code hooks")
    p.add_argument("kind", choices=["install", "uninstall", "status",
                                    "precheck", "posttool", "session-notice",
                                    "stop-drafter"])
    p.add_argument("--dry-run", action="store_true",
                   help="show lifecycle changes without writing settings")
    p.add_argument("--git", action="store_true",
                   help="with install/uninstall/status: target this repo's "
                        ".git/hooks/post-commit trigger for `scar draft-check` "
                        "(#117) instead of Claude Code's settings.json")

    p = _add(sub, "skill", help="install, remove, or inspect the scar-authoring skill")
    p.add_argument("kind", choices=["install", "uninstall", "status"])
    p.add_argument("--dry-run", action="store_true",
                   help="show changes without writing to ~/.claude/skills")

    _add(sub, "mcp", help="run the SCAR MCP stdio server")

    p = _add(sub, "agent", help="agent integration helpers")
    agent_sub = p.add_subparsers(dest="agent_command", required=True)
    _add(agent_sub, "doctor", help="show local agent integration readiness")
    cfg = _add(agent_sub, "config", help="print config for an agent runtime")
    cfg.add_argument("target", choices=["codex", "cursor", "opencode", "windsurf"])
    _add(agent_sub, "skill", help="print the scar-authoring skill body")

    p = _add(sub, "inject", help="machine mode for hooks: JSON or silence")
    p.add_argument("--path")
    p.add_argument("--content", default="")
    p.add_argument("--diff", help="unified diff text, or path to a diff file")
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--hook-event", default="PreToolUse")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "mcp":
        from .mcp import serve
        return serve()
    if args.command == "hook":
        if args.kind in ("install", "uninstall", "status"):
            return _cmd_hook_lifecycle(args)
        from .hooks import HANDLERS  # hot path: imports nothing beyond library
        return HANDLERS[args.kind]()
    if args.command == "skill":
        return _cmd_skill_lifecycle(args)
    if args.command in ("challenge", "archive"):
        status = {"challenge": "challenged", "archive": "archived"}[args.command]
        return _cmd_transition(args, status)
    handler = {
        "init": _cmd_init, "lint": _cmd_lint, "status": _cmd_status,
        "promote": _cmd_promote, "check": _cmd_check, "why": _cmd_why,
        "inject": _cmd_inject, "harvest": _cmd_harvest, "orphan": _cmd_orphan,
        "reanchor": _cmd_reanchor,
        "agent": _cmd_agent, "stats": _cmd_stats, "gc": _cmd_gc,
        "draft-check": _cmd_draft_check,
    }[args.command]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
