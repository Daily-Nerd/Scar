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


def _cmd_lint(_args) -> int:
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
    print(f"lint: {len(files)} file(s), {failed} with errors")
    return 1 if failed else 0


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


def _cmd_harvest(args) -> int:
    from .harvest import harvest  # subprocess-heavy; import only when used
    result = harvest(Path(args.repo).resolve())
    total = sum(len(v) for v in result.values())
    print(f"# Harvest candidates — {Path(args.repo).resolve().name} "
          f"({total} raw; curation required, expect ~13% precision)\n")
    sections = [("reverts", "Revert-shaped commits (deadend candidates)",
                 lambda c: f"- `{c['commit']}` {c['date']} — {c['subject']}"),
                ("deleted_components", "Components tried then deleted (deadend candidates)",
                 lambda c: f"- **{c['component']}** died {c['died']} (`{c['death_commit']}` {c['death_subject']})"),
                ("flapping", "Flapping values A->B->A (fence candidates)",
                 lambda c: f"- `{c['file']}` **{c['key']}**: {c['sequence']}"),
                ("comments", "Comment archaeology (fence candidates)",
                 lambda c: f"- `{c['location']}` — {c['text']}")]
    for key, title, fmt in sections:
        print(f"## {title} ({len(result[key])})")
        for c in result[key]:
            print(fmt(c))
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scar",
                                     description="version control for negative knowledge")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create .scars/ layout in the current repo")
    sub.add_parser("lint", help="validate every scar and candidate")
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

    p = sub.add_parser("harvest", help="mine git history for candidate scars")
    p.add_argument("repo", nargs="?", default=".")

    p = sub.add_parser("hook", help="Claude Code hook handlers (payload on stdin)")
    p.add_argument("kind", choices=["precheck", "session-notice", "stop-drafter"])

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
        from .hooks import HANDLERS  # hot path: imports nothing beyond library
        return HANDLERS[args.kind]()
    if args.command in ("challenge", "archive"):
        status = {"challenge": "challenged", "archive": "archived"}[args.command]
        return _cmd_transition(args, status)
    handler = {
        "init": _cmd_init, "lint": _cmd_lint, "status": _cmd_status,
        "promote": _cmd_promote, "check": _cmd_check, "why": _cmd_why,
        "inject": _cmd_inject, "harvest": _cmd_harvest,
        "agent": _cmd_agent,
    }[args.command]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
