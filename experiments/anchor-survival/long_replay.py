"""Long-horizon anchor survival: plant in the distant past, resolve at HEAD.

Stress case: a scar written ~200 commits ago (pre envsync->tripwire rename),
never re-anchored, resolved against today's tree. Exercises path-chain
following (git log --follow), repo-wide search, and fingerprint
disambiguation — the paths the single-commit replay never hit.

Usage: uv run long_replay.py /path/to/repo <old_commit> <path_prefix>
e.g.   uv run long_replay.py ../TripWire 04d1660~1 src/envsync/
"""

import sys
from collections import Counter

from replay import extract_symbols, find_defs, git, name_exists, show


def follow_path(repo, old_path):
    """Walk rename chain for old_path to its current name (or None)."""
    out = git(repo, "log", "--follow", "-M50", "--name-status",
              "--format=\x02", "--", old_path)
    current = None
    for line in out.splitlines():  # newest first; first R line has newest name
        if line.startswith("R"):
            _, _old, new = line.split("\t")
            current = new
            break
        if line.startswith(("A", "M")) and "\t" in line:
            current = line.split("\t")[1]
            break
    if current and show(repo, "HEAD", current):
        return current
    return None


def main(repo, old_commit, prefix):
    files = [f for f in git(repo, "ls-tree", "-r", "--name-only", old_commit).splitlines()
             if f.startswith(prefix) and f.endswith(".py")]
    tally = Counter()
    failures = []
    for path in files:
        pre = show(repo, old_commit, path)
        followed = follow_path(repo, path)
        for qual, _lineno, fp in extract_symbols(pre, path):
            tally["planted"] += 1
            outcome = None
            # (a) old path at HEAD
            cur = show(repo, "HEAD", path)
            if cur and any(q == qual for q, _, _ in extract_symbols(cur, path)):
                outcome = "survived(old-path)"
            # (b) rename-chain follow
            if not outcome and followed:
                txt = show(repo, "HEAD", followed)
                if any(q == qual for q, _, _ in extract_symbols(txt, followed)):
                    outcome = "survived(follow)"
            # (c) repo-wide search + fingerprint disambiguation
            if not outcome:
                hits = find_defs(repo, "HEAD", qual)
                if len(hits) == 1:
                    outcome = "survived(search)"
                elif len(hits) > 1:
                    matches = []
                    for hpath, hl in hits:
                        text = show(repo, "HEAD", hpath)
                        for q, l, f in extract_symbols(text, hpath):
                            if q == qual and l == hl and f == fp:
                                matches.append((hpath, hl))
                    outcome = ("survived(fingerprint)" if len(matches) == 1
                               else "AMBIGUOUS")
                else:
                    outcome = ("orphan-correct"
                               if not name_exists(repo, "HEAD", qual)
                               else "FALSE-ORPHAN")
            tally[outcome] += 1
            if outcome in ("AMBIGUOUS", "FALSE-ORPHAN"):
                failures.append(f"{path} :: {qual} -> {outcome}")

    survived = sum(v for k, v in tally.items() if k.startswith("survived"))
    failed = tally["AMBIGUOUS"] + tally["FALSE-ORPHAN"]
    denom = survived + failed
    print(f"== Long-horizon replay: {old_commit} -> HEAD ({prefix}) ==")
    for k in sorted(tally):
        print(f"{k:24} {tally[k]}")
    print(f"\nsurvival rate (gated): {survived}/{denom} = {survived/denom:.1%}" if denom else "no anchors")
    if failures:
        print("\n-- failures --")
        print("\n".join(failures))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
