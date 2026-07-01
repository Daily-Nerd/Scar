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

from .lint import lint_text
from .match import (
    rank_matches_for_diff,
    rank_matches_for_edit,
    rank_matches_for_paths,
)
from .model import parse_scar_text
from .evidence import unreachable_evidence
from .orphan import (
    GitError,
    anchors_all_dead,
    build_repo_context,
    detect_orphans,
    detect_partial_rot,
    detect_symbol_drift,
)
from .render import injection_context, label_line
from .store import ScarStore, init_scars
from . import output


def _require_store(start: Path | None = None) -> ScarStore | None:
    store = ScarStore.discover(start or Path.cwd())
    if store is None:
        print("no .scars/ directory found (walked up to repo root). Run: scar init")
    return store


def _cmd_init(_args) -> int:
    scars = init_scars(Path.cwd())
    print(f"initialized {scars} (README.md, template.md, candidates/)")
    print("convention: new scars -> candidates/, humans promote via `scar promote`")
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


def _orphan_reason(finding) -> str:
    """Human description of why a finding is an orphan — distinguishes a scar
    with NO anchors (protects nothing) from one whose every anchor went dead."""
    if not finding.dead_path_anchors and not finding.dead_pattern_anchors:
        return "no anchors — scar protects nothing"
    return "all anchors dead (" + _dead_anchor_summary(finding) + ")"


def _partial_rot_reason(finding) -> str:
    """Human description of partial rot — which specific anchors went dead while
    the scar keeps firing on its survivors (#35)."""
    return "partial rot — dead anchor(s) (" + _dead_anchor_summary(finding) + ")"


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


def _check_rich(label: str, hits) -> None:
    from rich.panel import Panel

    console = output.console
    if not hits:
        console.print(f"[green]no scars anchored to[/] {label}")
        return
    for s in hits:
        title = (f"{_type_label(s.type)} #{s.id} · severity: {s.severity} · "
                 f"confidence: {s.confidence}")
        console.print(Panel(s.body[:200].strip(), title=title,
                            subtitle=f"[bold]{s.title}[/]", title_align="left"))


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
        f"{data['total_firings']} firing(s) recorded\n"
        f"most-fired: {'#' + str(data['most_fired']) if data['most_fired'] is not None else '(none yet)'} · "
        f"last fired: {data['last_fired'] or '(never)'}",
        title="scar stats"))
    if data["per_scar"]:
        t = Table(title="Per-scar firing counts", show_edge=False, expand=False)
        t.add_column("id", justify="right"); t.add_column("firings", justify="right")
        for e in data["per_scar"]:
            t.add_row(f"#{e['id']}", str(e["count"]))
        console.print(t)
    if data["never_fired"]:
        console.print(f"[yellow]never fired:[/] "
                      + ", ".join(f"#{i}" for i in data["never_fired"]))
    console.print("[dim]note: firing counts only — whether the agent honored an "
                  "injected scar is not tracked (unobservable from inside the hook)[/]")


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
    try:
        new_path = store.promote(matches[0], reviewer=args.reviewer)
    except ValueError as exc:
        print(str(exc))
        return 1
    print(f"promoted -> {new_path.relative_to(store.root)}")

    # Non-blocking advisory: a freshly promoted scar whose anchors already
    # resolve to nothing is born orphan-detected. Promote still succeeds — the
    # reviewer may anchor to code that does not exist yet on purpose.
    promoted = parse_scar_text(new_path.read_text(encoding="utf-8"))
    ctx = _repo_context(store)
    if ctx is not None and anchors_all_dead(promoted, ctx):
        print("  advisory: this scar's anchors resolve to nothing in the current "
              "tree (born orphan-detected) — confirm the anchors are right")
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
    else:
        matches = rank_matches_for_paths(store, args.path, args.content or "",
                                         top_k=args.top_k)
    hits = [m.scar for m in matches]

    data = {
        "paths": [] if diff_text is not None else list(args.path),
        "scars": [{"type": s.type, "id": s.id, "severity": s.severity,
                   "confidence": s.confidence, "status": s.status, "title": s.title,
                   "body": s.body[:200]} for s in hits],
    }
    if diff_text is None and len(args.path) == 1:
        data["path"] = args.path[0]  # back-compat: single-path shape unchanged

    def plain():
        if not hits:
            print(f"no scars anchored to {label}")
            return
        for s in hits:
            print(label_line(s))
            print("  " + s.body[:200].replace("\n", "\n  "))

    output.render(data=data, json_flag=getattr(args, "json", False),
                  tty=lambda: _check_rich(label, hits), plain=plain)

    if getattr(args, "exit_code", False) and hits:
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


def _cmd_orphan(args) -> int:
    """List firing scars whose every anchor is dead. Read-only by default;
    --apply persists status: orphaned via store.transition() (human-only)."""
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


def _cmd_stats(args) -> int:
    """Aggregate the firing log the precheck hook writes (#106): total
    firings, per-scar counts, the most-fired scar, the last-fired timestamp,
    and which currently-firing scars have never fired. FIRING COUNTS only —
    this cannot report whether the agent honored an injected scar, only that
    it was shown to it (unobservable from inside the hook)."""
    from .hooks import firing_log_path
    store = _require_store()
    if store is None:
        return 1

    log_path = firing_log_path()
    records = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    counts: dict[int, int] = {}
    last_fired = None
    for rec in records:
        for sid in rec.get("scar_ids", []):
            counts[sid] = counts.get(sid, 0) + 1
        ts = rec.get("ts")
        if ts and (last_fired is None or ts > last_fired):
            last_fired = ts
    per_scar = sorted(({"id": sid, "count": c} for sid, c in counts.items()),
                      key=lambda e: (-e["count"], e["id"]))
    most_fired = per_scar[0]["id"] if per_scar else None
    firing_ids = {s.id for _f, s in store.firing() if s.id is not None}
    never_fired = sorted(firing_ids - set(counts))

    data = {
        "total_firings": sum(counts.values()),
        "per_scar": per_scar,
        "most_fired": most_fired,
        "last_fired": last_fired,
        "never_fired": never_fired,
    }

    def plain():
        print(f"scar stats: {data['total_firings']} firing(s) recorded ({log_path})")
        for e in per_scar:
            print(f"  #{e['id']}: {e['count']} fire(s)")
        if most_fired is not None:
            print(f"  most-fired: #{most_fired}")
        if last_fired:
            print(f"  last fired: {last_fired}")
        if never_fired:
            print("  never fired: " + ", ".join(f"#{i}" for i in never_fired))
        print("  note: firing counts only — whether the agent honored an "
              "injected scar is not tracked (unobservable from inside the hook)")

    output.render(data=data, json_flag=getattr(args, "json", False),
                  tty=lambda: _stats_rich(data), plain=plain)
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
    context = injection_context([m.scar for m in matches], store.broken(),
                                store.scars_dir)
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


def _cmd_harvest(args) -> int:
    from .harvest import GitError, harvest  # subprocess-heavy; import only when used
    repo = Path(args.repo).resolve()

    try:
        if args.label is not None:
            return _harvest_label(repo, args)

        if args.precision:
            return _harvest_precision(repo, args)

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
        print(f"# Harvest top {len(top)} — {repo.name} "
              f"(of {total} raw, cross-section by raw score; "
              "curation required, expect ~13% precision)\n")
        for key, c in top:
            print(_harvest_line(key, c))
        return 0

    print(f"# Harvest candidates — {repo.name} "
          f"({total} raw; curation required, expect ~13% precision)\n")
    for key, title, _fmt in _HARVEST_SECTIONS:
        print(f"## {title} ({len(result[key])})")
        for c in result[key]:
            print(_harvest_line(key, c))
        print()
    return 0


def _cmd_agent(args) -> int:
    from .agent import config, doctor, skill
    if args.agent_command == "doctor":
        for line in doctor(Path.cwd()):
            print(line)
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

    _add(sub, "init", help="create .scars/ layout in the current repo")
    p = _add(sub, "lint", help="validate every scar and candidate")
    p.add_argument("--fail-orphans", action="store_true",
                   help="exit non-zero when any scar is orphan-detected")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p = _add(sub, "status", help="counts, titles, broken-file warnings")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    p = _add(sub, "promote", help="review a candidate into an active scar")
    p.add_argument("candidate", help="candidate filename (or unique substring)")
    p.add_argument("--reviewer", default="", help="human reviewer to add to authors")

    p = _add(sub, "check", help="scars anchored to a path (CI gate with --exit-code)")
    p.add_argument("path", nargs="*", default=[], help="path(s) to check")
    p.add_argument("--content", default="", help="new code to test pattern anchors against")
    p.add_argument("--diff", help="unified diff text, or path to a diff file — gates on "
                                  "the union of changed files, like `inject --diff`")
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.add_argument("--exit-code", action="store_true",
                   help="exit 1 if any scar fires on the checked path(s)/diff (CI gate); "
                        "default is always 0 (back-compat)")

    p = _add(sub, "why", help="history of pain for a path (any status)")
    p.add_argument("path")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    p = _add(sub, "stats", help="firing counts from the precheck hook's firing log")
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
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON (read mode only)")

    p = _add(sub, "harvest", help="mine git history for candidate scars")
    p.add_argument("repo", nargs="?", default=".")
    p.add_argument("--top-k", type=int, default=None,
                   help="show the N highest-scoring candidates across all sections "
                        "(raw score, no cross-type normalization)")
    p.add_argument("--label", nargs=2, metavar=("ID", "LABEL"), default=None,
                   help="record a curation judgement: <id> keep|discard "
                        "(appends one line to .scars/harvest-labels.jsonl)")
    p.add_argument("--note", default="", help="with --label: free-text rationale")
    p.add_argument("--precision", action="store_true",
                   help="report precision@N of the ranking against labels.jsonl "
                        "(base rate + lift = does ranking beat no-ranking)")
    p.add_argument("--at", default=None,
                   help="with --precision: comma-separated N values (default 5,10,20)")

    p = _add(sub, "hook", help="install, remove, inspect, or run Claude Code hooks")
    p.add_argument("kind", choices=["install", "uninstall", "status",
                                    "precheck", "session-notice", "stop-drafter"])
    p.add_argument("--dry-run", action="store_true",
                   help="show lifecycle changes without writing settings")

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
        "agent": _cmd_agent, "stats": _cmd_stats,
    }[args.command]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
