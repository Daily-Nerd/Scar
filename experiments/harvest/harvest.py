"""scar harvest — prototype (Gate 0.1).

Mines a git repo for negative-knowledge candidates and emits a
"history of pain" markdown report.

Heuristics:
  H1 revert-ish commit messages (revert/rollback/downgrade/back to/undo)
  H2 image/version downgrades in diffs (gitops-aware)
  H3 apps/components tried then deleted
  H4 value flapping: same key in same file toggling A -> B -> A
  H5 fix-chains: bursts of fix commits against one component
  H6 comment archaeology in the current tree (DO NOT / HACK / intentional...)

Usage: uv run harvest.py /path/to/repo > report.md
"""

import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REVERT_RE = re.compile(
    r"revert|rollback|roll back|downgrade|back to|undo|set back|restore(?!d from backup)|"
    r"retire|disable again|turn off|remove (?:broken|failing)",
    re.IGNORECASE,
)
TRACKED_KEYS = ("image", "replicas", "tag", "version", "cpu", "memory", "nodeSelector")
COMMENT_RE = re.compile(
    r"DO NOT|DON'T|do not (?:remove|change|touch)|HACK|XXX|load.?bearing|"
    r"intentional|must (?:stay|remain|be)|keep this|workaround|temporary(?! file)",
    re.IGNORECASE,
)
VERSION_RE = re.compile(r"[:@]v?(\d+(?:\.\d+){0,3})(?:[-@]|$)")


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True
    ).stdout


def commits(repo):
    out = git(repo, "log", "--format=%H\x01%ad\x01%s", "--date=short")
    for line in out.splitlines():
        h, date, subj = line.split("\x01", 2)
        yield h, date, subj


def h1_reverts(repo):
    return [
        {"commit": h[:8], "date": d, "subject": s}
        for h, d, s in commits(repo)
        if REVERT_RE.search(s)
    ]


def parse_version(line):
    m = VERSION_RE.search(line)
    if not m:
        return None
    return tuple(int(x) for x in m.group(1).split("."))


def h2_downgrades(repo):
    out = git(repo, "log", "-p", "--format=\x02%h\x01%ad\x01%s", "--date=short",
              "-G", "image:", "--", "apps/")
    found, commit, fname = [], None, None
    removed = {}
    for line in out.splitlines():
        if line.startswith("\x02"):
            commit = line[1:].split("\x01")
            removed = {}
        elif line.startswith("+++ b/"):
            fname = line[6:]
        elif line.startswith("-") and "image:" in line:
            v = parse_version(line)
            if v:
                removed[fname] = (v, line.strip("- ").strip())
        elif line.startswith("+") and "image:" in line and fname in removed:
            new = parse_version(line)
            old, old_line = removed[fname]
            if new and new < old:
                found.append({
                    "commit": commit[0], "date": commit[1], "subject": commit[2],
                    "file": fname,
                    "change": f"{old_line}  ->  {line.strip('+ ').strip()}",
                })
    return found


def h3_deleted_components(repo):
    out = git(repo, "log", "--diff-filter=D", "--name-only",
              "--format=\x02%h\x01%ad\x01%s", "--date=short")
    comp_del = defaultdict(list)
    commit = None
    for line in out.splitlines():
        if line.startswith("\x02"):
            commit = line[1:].split("\x01")
        elif line and "/" in line:
            comp = "/".join(line.split("/")[:2])
            comp_del[comp].append(commit)
    results = []
    for comp, dels in comp_del.items():
        if Path(repo, comp).exists():
            continue  # still exists elsewhere / re-added
        first = git(repo, "log", "--diff-filter=A", "--format=%ad", "--date=short",
                    "--reverse", "--", comp).splitlines()
        born = first[0] if first else "?"
        last = dels[0]
        results.append({
            "component": comp, "born": born, "died": last[1],
            "death_commit": last[0], "death_subject": last[2],
            "files_deleted": len(dels),
        })
    return sorted(results, key=lambda r: r["died"], reverse=True)


def h4_flapping(repo):
    key_re = re.compile(
        r"^[+-]\s*(" + "|".join(TRACKED_KEYS) + r"):\s*(.+)$"
    )
    out = git(repo, "log", "-p", "--reverse", "--format=\x02%h\x01%ad\x01%s",
              "--date=short", "--", "apps/")
    history = defaultdict(list)  # (file, key) -> [(value, commit, date)]
    commit, fname = None, None
    for line in out.splitlines():
        if line.startswith("\x02"):
            commit = line[1:].split("\x01")
        elif line.startswith("+++ b/"):
            fname = line[6:]
        elif line.startswith("+") and fname:
            m = key_re.match(line)
            if m:
                history[(fname, m.group(1))].append(
                    (m.group(2).strip(), commit[0], commit[1])
                )
    flaps = []
    for (fname, key), seq in history.items():
        values = [v for v, _, _ in seq]
        for i in range(len(values) - 2):
            if values[i] == values[i + 2] and values[i] != values[i + 1]:
                flaps.append({
                    "file": fname, "key": key,
                    "sequence": f"{values[i]} -> {values[i+1]} -> {values[i+2]}",
                    "commits": [seq[i][1], seq[i+1][1], seq[i+2][1]],
                    "dates": f"{seq[i][2]} .. {seq[i+2][2]}",
                })
                break  # one flap per (file,key) is enough signal
    return flaps


def h5_fix_chains(repo, window_days=14, threshold=3):
    from datetime import date as ddate
    per_comp = defaultdict(list)
    for h, d, s in commits(repo):
        if not re.match(r"fix\b|fix\(", s, re.IGNORECASE):
            continue
        files = git(repo, "show", "--name-only", "--format=", h).splitlines()
        comps = {"/".join(f.split("/")[:2]) for f in files if f.startswith("apps/")}
        for c in comps:
            per_comp[c].append((ddate.fromisoformat(d), h[:8], s))
    chains = []
    for comp, fixes in per_comp.items():
        fixes.sort()
        for i in range(len(fixes)):
            run = [f for f in fixes if 0 <= (f[0] - fixes[i][0]).days <= window_days]
            if len(run) >= threshold:
                chains.append({
                    "component": comp, "fixes": len(run),
                    "from": str(run[0][0]), "to": str(run[-1][0]),
                    "subjects": [f"{h} {s}" for _, h, s in run[:5]],
                })
                break
    return sorted(chains, key=lambda c: -c["fixes"])


def h6_comments(repo):
    out = subprocess.run(
        ["rg", "-n", "--no-heading", "-g", "!*.lock", COMMENT_RE.pattern, "."],
        capture_output=True, text=True, cwd=repo,
    ).stdout
    hits = []
    for line in out.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and ("#" in parts[2] or "//" in parts[2]):
            hits.append({"location": f"{parts[0]}:{parts[1]}", "text": parts[2].strip()[:120]})
    return hits


def emit(title, rows, fmt):
    print(f"\n## {title} ({len(rows)})\n")
    if not rows:
        print("_nothing found_")
        return
    for r in rows:
        print(fmt(r))


def main(repo):
    print(f"# History of Pain — `{Path(repo).name}`\n")
    print("Candidate scars mined from git history. Each needs human confirmation.\n")

    emit("H1 — Revert-shaped commits (deadend candidates)", h1_reverts(repo),
         lambda r: f"- `{r['commit']}` {r['date']} — {r['subject']}")
    emit("H2 — Version downgrades (deadend candidates)", h2_downgrades(repo),
         lambda r: f"- `{r['commit']}` {r['date']} `{r['file']}`\n  - {r['change']}\n  - _{r['subject']}_")
    emit("H3 — Components tried then deleted (deadend candidates)", h3_deleted_components(repo),
         lambda r: f"- **{r['component']}** lived {r['born']} → {r['died']} "
                   f"(`{r['death_commit']}` {r['death_subject']}, {r['files_deleted']} files)")
    emit("H4 — Flapping values A→B→A (fence candidates: the A is load-bearing)", h4_flapping(repo),
         lambda r: f"- `{r['file']}` **{r['key']}**: {r['sequence']} ({r['dates']}, {'/'.join(r['commits'])})")
    emit("H5 — Fix-chains (landmine candidates: something here bites repeatedly)", h5_fix_chains(repo),
         lambda r: f"- **{r['component']}** — {r['fixes']} fixes {r['from']}..{r['to']}\n"
                   + "".join(f"  - {s}\n" for s in r["subjects"]).rstrip())
    emit("H6 — Comment archaeology (fence candidates already in the code)", h6_comments(repo),
         lambda r: f"- `{r['location']}` — {r['text']}")


if __name__ == "__main__":
    main(sys.argv[1])
