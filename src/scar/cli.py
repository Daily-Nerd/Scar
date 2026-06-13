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
from pathlib import Path

from .lint import lint_text
from .match import rank_for_edit, rank_matches_for_diff, rank_matches_for_edit
from .model import parse_scar_text
from .orphan import (
    anchors_all_dead,
    build_repo_context,
    detect_orphans,
)
from .render import injection_context, label_line
from .store import ScarStore, init_scars


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


def _orphan_reason(finding) -> str:
    """Human description of why a finding is an orphan — distinguishes a scar
    with NO anchors (protects nothing) from one whose every anchor went dead."""
    if not finding.dead_path_anchors and not finding.dead_pattern_anchors:
        return "no anchors — scar protects nothing"
    dead = []
    if finding.dead_path_anchors:
        dead.append("paths: " + ", ".join(finding.dead_path_anchors))
    if finding.dead_pattern_anchors:
        dead.append("patterns: " + ", ".join(f"/{p}/" for p in finding.dead_pattern_anchors))
    return "all anchors dead (" + "; ".join(dead) + ")"


def _cmd_lint(args) -> int:
    store = _require_store()
    if store is None:
        return 1
    failed = 0
    files = store._scar_files() + store.candidates()
    for f in files:
        findings = lint_text(f.read_text(encoding="utf-8"))
        for finding in findings:
            print(f"{f.relative_to(store.root)}: {finding}")
        if any(fi.level == "error" for fi in findings):
            failed += 1

    ctx = build_repo_context(store.root)
    orphans = detect_orphans(store, ctx)
    for of in orphans:
        print(f"WARNING orphan-detected: scar #{of.scar_id} — {_orphan_reason(of)}")

    # reverse hint: persisted-orphaned scars whose anchors resolve again
    for _f, s in store.parsed():
        if s.status == "orphaned" and not anchors_all_dead(s, ctx):
            print(f"HINT: scar #{s.id} is marked orphaned but its anchors live "
                  "again — consider re-activating (scar challenge/archive note)")

    print(f"lint: {len(files)} file(s), {failed} with errors, {len(orphans)} orphan(s)")
    if failed:
        return 1
    if orphans and getattr(args, "fail_orphans", False):
        return 1
    return 0


def _cmd_status(_args) -> int:
    store = _require_store()
    if store is None:
        return 1
    active, broken, cands = store.active(), store.broken(), store.candidates()
    print(f"{store.scars_dir}: {len(active)} active, {len(cands)} candidate(s) pending review")
    for f, s in active:
        print(f"  [{s.type} #{s.id} | {s.severity}] {s.title}")
    for f, s in store.parsed():
        if s.status == "challenged":
            print(f"  [challenged {s.type} #{s.id}] {s.title}")
    for c in cands:
        print(f"  candidate: {c.name}")
    today = time.strftime("%Y-%m-%d")
    due = [s for _, s in store.firing() if s.review_after and s.review_after < today]
    for s in due:
        print(f"  REVIEW DUE [{s.type} #{s.id}] review_after {s.review_after} — "
              "re-verify, then update the date or archive")

    # Orphans: detected (firing scars whose anchors all died — not yet persisted)
    # and persisted (already flipped to status: orphaned, invisible until now).
    ctx = build_repo_context(store.root)
    detected = detect_orphans(store, ctx)
    persisted = [s for _, s in store.parsed() if s.status == "orphaned"]
    print(f"  {len(detected)} orphan-detected (firing, anchors gone), "
          f"{len(persisted)} orphaned (persisted)")
    for of in detected:
        print(f"    orphan-detected [#{of.scar_id}] {_orphan_reason(of)}")
    for s in persisted:
        print(f"    orphaned [{s.type} #{s.id}] {s.title}")

    if broken:
        print(f"  WARNING: {len(broken)} unparseable (can NEVER fire): "
              + ", ".join(b.name for b in broken))
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
    if anchors_all_dead(promoted, build_repo_context(store.root)):
        print("  advisory: this scar's anchors resolve to nothing in the current "
              "tree (born orphan-detected) — confirm the anchors are right")
    return 0


def _cmd_check(args) -> int:
    store = _require_store(Path(args.path).resolve())
    if store is None:
        return 1
    hits = rank_for_edit(store, Path(args.path).resolve(), args.content or "",
                         top_k=args.top_k)
    if not hits:
        print(f"no scars anchored to {args.path}")
        return 0
    for s in hits:
        print(label_line(s))
        print("  " + s.body[:200].replace("\n", "\n  "))
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
    ctx = build_repo_context(store.root)
    findings = detect_orphans(store, ctx)

    if not args.apply:
        if not findings:
            print("no orphan-detected scars")
            return 0
        for of in findings:
            print(f"orphan-detected [#{of.scar_id}] {_orphan_reason(of)}")
        print(f"{len(findings)} orphan(s) detected — review, then "
              "`scar orphan --apply --id N --reason ...` to persist")
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
    for f, s in records:
        print(f"[{s.status} {s.type} #{s.id}] {s.title}  ({f.name})")
        print("  " + s.body[:300].replace("\n", "\n  ") + "\n")
    if not records:
        print(f"no recorded pain for {rel}")
    return 0


def _cmd_inject(args) -> int:
    """Machine mode for hooks: JSON additionalContext or silence."""
    start = Path(args.path).resolve() if args.path else Path.cwd()
    store = ScarStore.discover(start)
    if store is None:
        return 0  # hooks must never fail the edit
    if args.diff:
        try:
            diff_text = Path(args.diff).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            # ValueError covers NUL-byte paths; a hook must never crash on
            # whatever lands in --diff — fall back to treating it as text
            diff_text = args.diff
        matches = rank_matches_for_diff(store, diff_text, top_k=args.top_k)
    elif args.path:
        matches = rank_matches_for_edit(store, Path(args.path).resolve(),
                                        args.content or "", top_k=args.top_k)
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


# Labels JSONL lives under the harvested repo at experiments/harvest/labels.jsonl
# (instrument/data, committed like the anchor-survival experiment). Tests set
# LABELS_PATH_OVERRIDE to a tmp path so they never touch the real file.
LABELS_PATH_OVERRIDE: Path | None = None
_VALID_LABELS = ("keep", "discard")


def _labels_path(repo: Path) -> Path:
    """Resolve where label judgements are appended. Override wins (tests);
    otherwise experiments/harvest/labels.jsonl under the harvested repo root."""
    if LABELS_PATH_OVERRIDE is not None:
        return LABELS_PATH_OVERRIDE
    return repo / "experiments" / "harvest" / "labels.jsonl"


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


def _cmd_harvest(args) -> int:
    from .harvest import harvest  # subprocess-heavy; import only when used
    repo = Path(args.repo).resolve()

    if args.label is not None:
        return _harvest_label(repo, args)

    result = harvest(repo)
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
    from .agent import config, doctor
    if args.agent_command == "doctor":
        for line in doctor(Path.cwd()):
            print(line)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scar",
                                     description="version control for negative knowledge")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create .scars/ layout in the current repo")
    p = sub.add_parser("lint", help="validate every scar and candidate")
    p.add_argument("--fail-orphans", action="store_true",
                   help="exit non-zero when any scar is orphan-detected")
    sub.add_parser("status", help="counts, titles, broken-file warnings")

    p = sub.add_parser("promote", help="review a candidate into an active scar")
    p.add_argument("candidate", help="candidate filename (or unique substring)")
    p.add_argument("--reviewer", default="", help="human reviewer to add to authors")

    p = sub.add_parser("check", help="scars anchored to a path")
    p.add_argument("path")
    p.add_argument("--content", default="", help="new code to test pattern anchors against")
    p.add_argument("--top-k", type=int, default=10)

    p = sub.add_parser("why", help="history of pain for a path (any status)")
    p.add_argument("path")

    p = sub.add_parser("challenge", help="dispute a scar (still fires, marked challenged)")
    p.add_argument("id", type=int)
    p.add_argument("--reason", required=True, help="why the scar may no longer hold")

    p = sub.add_parser("archive", help="retire a scar (never fires; history kept)")
    p.add_argument("id", type=int)
    p.add_argument("--reason", required=True, help="why it is retired (e.g. expiry condition met)")

    p = sub.add_parser("orphan", help="list scars whose anchors all went dead")
    p.add_argument("--apply", action="store_true",
                   help="persist status: orphaned (human review only — never CI)")
    p.add_argument("--id", type=int, default=None,
                   help="with --apply: the detected scar id to persist")
    p.add_argument("--reason", default="anchors no longer resolve",
                   help="with --apply: why it is being orphaned (recorded in the note)")

    p = sub.add_parser("harvest", help="mine git history for candidate scars")
    p.add_argument("repo", nargs="?", default=".")
    p.add_argument("--top-k", type=int, default=None,
                   help="show the N highest-scoring candidates across all sections "
                        "(raw score, no cross-type normalization)")
    p.add_argument("--label", nargs=2, metavar=("ID", "LABEL"), default=None,
                   help="record a curation judgement: <id> keep|discard "
                        "(appends one line to experiments/harvest/labels.jsonl)")
    p.add_argument("--note", default="", help="with --label: free-text rationale")

    p = sub.add_parser("hook", help="install, remove, inspect, or run Claude Code hooks")
    p.add_argument("kind", choices=["install", "uninstall", "status",
                                    "precheck", "session-notice", "stop-drafter"])
    p.add_argument("--dry-run", action="store_true",
                   help="show lifecycle changes without writing settings")

    sub.add_parser("mcp", help="run the SCAR MCP stdio server")

    p = sub.add_parser("agent", help="agent integration helpers")
    agent_sub = p.add_subparsers(dest="agent_command", required=True)
    agent_sub.add_parser("doctor", help="show local agent integration readiness")
    cfg = agent_sub.add_parser("config", help="print config for an agent runtime")
    cfg.add_argument("target", choices=["codex", "cursor", "opencode", "windsurf"])

    p = sub.add_parser("inject", help="machine mode for hooks: JSON or silence")
    p.add_argument("--path")
    p.add_argument("--content", default="")
    p.add_argument("--diff", help="unified diff text, or path to a diff file")
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--hook-event", default="PreToolUse")

    args = parser.parse_args(argv)
    if args.command == "mcp":
        from .mcp import serve
        return serve()
    if args.command == "hook":
        if args.kind in ("install", "uninstall", "status"):
            return _cmd_hook_lifecycle(args)
        from .hooks import HANDLERS  # hot path: imports nothing beyond library
        return HANDLERS[args.kind]()
    if args.command in ("challenge", "archive"):
        status = {"challenge": "challenged", "archive": "archived"}[args.command]
        return _cmd_transition(args, status)
    handler = {
        "init": _cmd_init, "lint": _cmd_lint, "status": _cmd_status,
        "promote": _cmd_promote, "check": _cmd_check, "why": _cmd_why,
        "inject": _cmd_inject, "harvest": _cmd_harvest, "orphan": _cmd_orphan,
        "agent": _cmd_agent,
    }[args.command]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
