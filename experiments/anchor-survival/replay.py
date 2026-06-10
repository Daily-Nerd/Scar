"""Anchor survival replay (Gate 0.2).

Replays real rename/refactor commits: plants symbol+fingerprint anchors on the
pre-image of every renamed code file, then resolves them post-commit with the
SCAR resolver v0 and a naive path+line baseline.

Usage: uv run replay.py /path/to/repo [/path/to/repo2 ...]
"""

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

CODE_EXT = (".py", ".tsx", ".ts", ".go", ".js")
PY_DEF = re.compile(r"^(\s*)(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)")
TS_DEF = re.compile(r"^(\s*)(?:export\s+)?(?:async\s+)?(function|const|class)\s+([A-Za-z_$][\w$]*)")


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return r.stdout


def show(repo, commit, path):
    return git(repo, "show", f"{commit}:{path}")


def normalize(block_lines):
    out = []
    for ln in block_lines:
        ln = re.sub(r"#.*$|//.*$", "", ln)
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln:
            out.append(ln)
    return "\x00".join(out)


def extract_symbols(text, path):
    """Yield (qualified_name, line_no, fingerprint). Indent-tracked for py."""
    rx = PY_DEF if path.endswith(".py") else TS_DEF
    lines = text.splitlines()
    symbols = []
    class_stack = []  # (indent, name)
    for i, line in enumerate(lines):
        m = rx.match(line)
        if not m:
            continue
        indent, kind, name = len(m.group(1)), m.group(2), m.group(3)
        while class_stack and indent <= class_stack[-1][0]:
            class_stack.pop()
        qual = ".".join([c[1] for c in class_stack] + [name])
        # def block = lines until next line with indent <= this def's indent
        block = [line]
        for j in range(i + 1, min(i + 40, len(lines))):
            nxt = lines[j]
            if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent and rx.match(nxt) or \
               (nxt.strip() and (len(nxt) - len(nxt.lstrip())) < indent):
                break
            block.append(nxt)
        symbols.append((qual, i + 1, normalize(block)))
        if kind == "class":
            class_stack.append((indent, name))
    return symbols


def find_defs(repo, commit, qual):
    """Repo-wide qualified-symbol search at a commit. Returns [(path, line)]."""
    parts = qual.split(".")
    name = parts[-1]
    # NB: \b is not POSIX ERE — git grep -E treats it literally. Explicit boundary.
    pat = (rf"^[ \t]*((async[ \t]+)?def|class|(export[ \t]+)?((async[ \t]+)?function|const))"
           rf"[ \t]+{re.escape(name)}([^A-Za-z0-9_]|$)")
    out = git(repo, "grep", "-nE", pat, commit)
    hits = []
    for ln in out.splitlines():
        try:
            path, lineno = ln.split(":")[1], int(ln.split(":")[2])
        except (IndexError, ValueError):
            continue
        if path.endswith(CODE_EXT):
            hits.append((path, lineno))
    if len(parts) == 1:
        return hits
    # qualified: keep hits whose enclosing class chain matches
    kept = []
    for path, lineno in hits:
        text = show(repo, commit, path)
        syms = extract_symbols(text, path)
        if any(q == qual and l == lineno for q, l, _ in syms):
            kept.append((path, lineno))
    return kept


def name_exists(repo, commit, qual):
    name = qual.split(".")[-1]
    out = git(repo, "grep", "-lwF", name, commit)  # -w: word boundary, -F: literal
    return any(ln.split(":", 1)[-1].endswith(CODE_EXT) for ln in out.splitlines())


def rename_events(repo):
    out = git(repo, "log", "-M50", "--diff-filter=R", "--name-status",
              "--format=\x02%H")
    events, commit = [], None
    for line in out.splitlines():
        if line.startswith("\x02"):
            commit = line[1:]
        elif line.startswith("R"):
            _, old, new = line.split("\t")
            if old.endswith(CODE_EXT):
                events.append((commit, old, new))
    return events


def main(repos):
    tally = Counter()
    failures = []
    for repo in repos:
        rname = Path(repo).name
        for commit, old, new in rename_events(repo):
            parent = f"{commit}~1"
            pre = show(repo, parent, old)
            if not pre:
                continue
            renames = {old: new}
            for qual, lineno, fp in extract_symbols(pre, old):
                tally["planted"] += 1
                # --- baseline: path+line ---
                post_same = show(repo, commit, old)
                base_ok = False
                if post_same:
                    plines = post_same.splitlines()
                    base_ok = lineno <= len(plines) and qual.split(".")[-1] in plines[lineno - 1]
                tally["baseline_survived" if base_ok else "baseline_dead"] += 1
                # --- SCAR resolver ---
                outcome = None
                # (a) old path
                if post_same and any(q == qual for q, _, _ in extract_symbols(post_same, old)):
                    outcome = "survived(old-path)"
                # (b) git rename record
                if not outcome:
                    tgt = show(repo, commit, renames.get(old, ""))
                    if tgt and any(q == qual for q, _, _ in extract_symbols(tgt, renames[old])):
                        outcome = "survived(rename-map)"
                # (c) repo-wide search
                if not outcome:
                    hits = find_defs(repo, commit, qual)
                    if len(hits) == 1:
                        outcome = "survived(search)"
                    elif len(hits) > 1:
                        matches = []
                        for path, hl in hits:
                            text = show(repo, commit, path)
                            for q, l, f in extract_symbols(text, path):
                                if q == qual and l == hl and f == fp:
                                    matches.append((path, hl))
                        if len(matches) == 1:
                            outcome = "survived(fingerprint)"
                        else:
                            outcome = "AMBIGUOUS"
                    else:
                        outcome = ("orphan-correct"
                                   if not name_exists(repo, commit, qual)
                                   else "FALSE-ORPHAN")
                tally[outcome] += 1
                if outcome in ("AMBIGUOUS", "FALSE-ORPHAN"):
                    failures.append(f"{rname} {commit[:7]} {old} :: {qual} -> {outcome}")
                # --- fingerprint drift (among survived) ---
                if outcome.startswith("survived"):
                    loc = renames[old] if "rename" in outcome else old
                    text = show(repo, commit, loc) if "search" not in outcome and "fingerprint" not in outcome else None
                    if text:
                        same = any(q == qual and f == fp for q, _, f in extract_symbols(text, loc))
                        tally["fp_intact" if same else "fp_drifted"] += 1

    survived = sum(v for k, v in tally.items() if k.startswith("survived"))
    failed = tally["AMBIGUOUS"] + tally["FALSE-ORPHAN"]
    denom = survived + failed
    print("== Anchor survival replay ==")
    for k in sorted(tally):
        print(f"{k:24} {tally[k]}")
    print(f"\nsurvival rate (gated): {survived}/{denom} = {survived/denom:.1%}" if denom else "no events")
    bdenom = tally["baseline_survived"] + tally["baseline_dead"]
    if bdenom:
        print(f"baseline path+line:    {tally['baseline_survived']}/{bdenom} = {tally['baseline_survived']/bdenom:.1%}")
    fpd = tally["fp_intact"] + tally["fp_drifted"]
    if fpd:
        print(f"fingerprint intact:    {tally['fp_intact']}/{fpd} = {tally['fp_intact']/fpd:.1%}")
    if failures:
        print("\n-- failures --")
        print("\n".join(failures))


if __name__ == "__main__":
    main(sys.argv[1:])
