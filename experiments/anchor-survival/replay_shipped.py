"""Shipped-API anchor survival replay (Gate 0.2, #101 Phase 4 remeasure).

replay.py's headline 94.8%/88.0% numbers were measured against a REGEX
PROTOTYPE resolver that never imports src/scar. This script replays the same
real refactor commits with the same git scaffolding and the same survival
accounting/denominator, but swaps the anchor MECHANISM for the SHIPPED
tree-sitter API in src/scar/symbols.py: `resolve_symbol`, `fingerprint`,
`jaccard`.

Reused UNCHANGED from replay.py, for git scaffolding + symbol ENUMERATION
only (never for the resolve/fingerprint decision itself):
  - git(), show()            -- thin subprocess wrappers
  - rename_events()          -- git log -M50 --diff-filter=R scaffolding
  - extract_symbols()        -- regex enumeration of which symbols to PLANT,
                                 and the naive path+line baseline column
  - find_defs()               -- repo-wide `git grep` candidate-file search
                                 (including its \\b-is-not-POSIX-ERE workaround)
  - name_exists()             -- ground truth for "genuinely deleted" orphans

The actual anchor resolution, fingerprinting, and disambiguation are 100%
shipped-API calls (scar.symbols.resolve_symbol / fingerprint / jaccard).

Anchor form: `f"{rel_path}::{qual}"` where `qual` is the dotted qualified
name (`Class.method` for methods, bare name for top-level def/class) --
matching `_resolve_node`'s `qpath == rel_path` self-check and its dotted
member-lookup. Because the anchor is always re-minted with the CURRENT
rel_path being probed, the `::` component is a self-consistency check, not
a cross-file identifier -- see NOTES in the report for why this is fine.

Decision rule (LOCKED, issue #54): `pick_survivor()` below is pure argmax
Jaccard with tie-only ambiguity. NO similarity floor -- a floor is a tuned
threshold, forbidden.

Usage:
  Single-commit:  uv run --extra symbols experiments/anchor-survival/replay_shipped.py /path/to/repo [/path/to/repo2 ...]
  Long-horizon:   uv run --extra symbols experiments/anchor-survival/replay_shipped.py --long /path/to/repo <old_commit> <path_prefix>
                  e.g. --long ../TripWire 04d1660~1 src/envsync/
"""

import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replay import CODE_EXT, extract_symbols, find_defs, git, name_exists, rename_events, show  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from scar import symbols as scar_symbols  # noqa: E402


def pick_survivor(candidates):
    """candidates: list of (path, jaccard_score) for files where the symbol
    resolves (resolve_symbol confirmed it -- not just a name match).
    Returns 'survived' | 'ambiguous' | 'orphan'.

    Rule (LOCKED -- no tuned threshold, per issue #54):
      - no candidates                              -> 'orphan'
      - unique argmax jaccard                       -> 'survived'
      - >=2 candidates tie at the exact max jaccard -> 'ambiguous'

    NO minimum-similarity floor. Report the jaccard distribution separately.
    """
    if not candidates:
        return "orphan"
    best = max(score for _, score in candidates)
    winners = [p for p, score in candidates if score == best]
    return "survived" if len(winners) == 1 else "ambiguous"


def _anchor(rel_path, qual):
    return f"{rel_path}::{qual}"


def resolve_via_search(repo, commit, qual, fp0):
    """Repo-wide search + shipped-fingerprint disambiguation (waterfall step c).

    Returns (outcome_str, winning_jaccard_or_None) where outcome_str is one
    of 'survived(search+jaccard)', 'AMBIGUOUS', or the raw pick_survivor
    'orphan' verdict (caller resolves 'orphan' vs 'FALSE-ORPHAN' via the
    ground-truth name_exists() check, matching replay.py's accounting).
    """
    hits = find_defs(repo, commit, qual)  # regex-based candidate enumeration
    cand_paths = sorted({p for p, _ in hits})
    candidates = []
    for cp in cand_paths:
        ctext = show(repo, commit, cp)
        if not ctext:
            continue
        anchor = _anchor(cp, qual)
        if scar_symbols.resolve_symbol(anchor, cp, ctext) is None:
            continue  # name matched by regex grep but shipped resolver disagrees
        fp1 = scar_symbols.fingerprint(anchor, cp, ctext)
        if fp1 is None:
            continue
        candidates.append((cp, scar_symbols.jaccard(fp0, fp1)))
    verdict = pick_survivor(candidates)
    if verdict == "survived":
        return "survived(search+jaccard)", max(s for _, s in candidates)
    if verdict == "ambiguous":
        return "AMBIGUOUS", None
    return "orphan", None


def resolve_one(repo, commit, old, new, qual, fp0, post_same):
    """Single-commit waterfall for one planted anchor. Returns (outcome, jaccard_or_None)."""
    # (a) old path still holds it
    if post_same:
        if scar_symbols.resolve_symbol(_anchor(old, qual), old, post_same) is not None:
            return "survived(old-path)", None
    # (b) git's own rename record
    tgt_text = show(repo, commit, new) if new else ""
    if tgt_text:
        if scar_symbols.resolve_symbol(_anchor(new, qual), new, tgt_text) is not None:
            return "survived(rename-map)", None
    # (c) repo-wide search + jaccard disambiguation
    outcome, jscore = resolve_via_search(repo, commit, qual, fp0)
    if outcome == "orphan":
        # ground truth: genuinely deleted vs resolver failure (mirrors replay.py exactly)
        outcome = "orphan-correct" if not name_exists(repo, commit, qual) else "FALSE-ORPHAN"
    return outcome, jscore


def histogram(values, bins=10):
    if not values:
        return ""
    counts = [0] * bins
    for v in values:
        idx = min(int(v * bins), bins - 1)
        counts[idx] += 1
    width = max(counts) or 1
    lines = []
    for i, c in enumerate(counts):
        lo, hi = i / bins, (i + 1) / bins
        bar = "#" * int(20 * c / width)
        lines.append(f"  [{lo:.1f}-{hi:.1f}) {bar:20} {c}")
    return "\n".join(lines)


def main(repos):
    if not scar_symbols.symbols_available():
        print("FATAL: tree-sitter symbols extra not installed. Run: uv sync --extra symbols", file=sys.stderr)
        sys.exit(1)

    tally = Counter()
    failures = []
    jaccard_scores = []
    commits_used = {}

    for repo in repos:
        rname = Path(repo).name
        commits_used[rname] = set()
        for commit, old, new in rename_events(repo):
            commits_used[rname].add(commit)
            parent = f"{commit}~1"
            pre = show(repo, parent, old)
            if not pre:
                continue
            for qual, lineno, _regex_fp in extract_symbols(pre, old):
                tally["planted"] += 1

                # --- baseline: naive path+line (unchanged from replay.py) ---
                post_same = show(repo, commit, old)
                base_ok = False
                if post_same:
                    plines = post_same.splitlines()
                    base_ok = lineno <= len(plines) and qual.split(".")[-1] in plines[lineno - 1]
                tally["baseline_survived" if base_ok else "baseline_dead"] += 1

                # --- shipped mechanism: plant fp0 at C~1 via scar.symbols ---
                fp0 = scar_symbols.fingerprint(_anchor(old, qual), old, pre)
                if fp0 is None:
                    tally["shipped_unsupported"] += 1  # tree-sitter disagreed w/ regex enumeration, or unsupported ext
                    continue

                outcome, jscore = resolve_one(repo, commit, old, new, qual, fp0, post_same)
                tally[outcome] += 1
                if jscore is not None:
                    jaccard_scores.append(jscore)
                if outcome in ("AMBIGUOUS", "FALSE-ORPHAN"):
                    failures.append(f"{rname} {commit[:7]} {old} :: {qual} -> {outcome}")

    survived = sum(v for k, v in tally.items() if k.startswith("survived"))
    failed = tally["AMBIGUOUS"] + tally["FALSE-ORPHAN"]
    denom = survived + failed

    print("== Shipped-API anchor survival replay (scar.symbols) ==")
    for k in sorted(tally):
        print(f"{k:24} {tally[k]}")
    print(f"\nsurvival rate (gated): {survived}/{denom} = {survived / denom:.1%}" if denom else "no events")
    bdenom = tally["baseline_survived"] + tally["baseline_dead"]
    if bdenom:
        print(f"baseline path+line:    {tally['baseline_survived']}/{bdenom} = {tally['baseline_survived'] / bdenom:.1%}")

    print("\n-- winning Jaccard distribution (step-c disambiguation only) --")
    if jaccard_scores:
        print(f"  n={len(jaccard_scores)} min={min(jaccard_scores):.3f} "
              f"median={statistics.median(jaccard_scores):.3f} max={max(jaccard_scores):.3f}")
        print(histogram(jaccard_scores))
    else:
        print("  no step-c disambiguation was triggered (all resolutions settled at step a/b)")

    print("\n-- pinned rename-commit SHAs per repo --")
    for rname, shas in commits_used.items():
        print(f"  {rname}: {', '.join(sorted(shas))}")

    if failures:
        print("\n-- failures --")
        print("\n".join(failures))


def main_long(repo, old_commit, prefix):
    """Long-horizon shipped-API replay: plant at old_commit, resolve at HEAD.

    Mirrors long_replay.py's structure (git log --follow rename-chain
    following instead of the single-commit rename map) with the shipped
    mechanism swapped in exactly as main() does.
    """
    if not scar_symbols.symbols_available():
        print("FATAL: tree-sitter symbols extra not installed. Run: uv sync --extra symbols", file=sys.stderr)
        sys.exit(1)

    from long_replay import follow_path

    files = [f for f in git(repo, "ls-tree", "-r", "--name-only", old_commit).splitlines()
             if f.startswith(prefix) and f.endswith(CODE_EXT)]
    tally = Counter()
    failures = []
    jaccard_scores = []

    for path in files:
        pre = show(repo, old_commit, path)
        followed = follow_path(repo, path)
        for qual, _lineno, _regex_fp in extract_symbols(pre, path):
            tally["planted"] += 1
            fp0 = scar_symbols.fingerprint(_anchor(path, qual), path, pre)
            if fp0 is None:
                tally["shipped_unsupported"] += 1
                continue

            outcome = None
            jscore = None
            # (a) old path at HEAD
            cur = show(repo, "HEAD", path)
            if cur and scar_symbols.resolve_symbol(_anchor(path, qual), path, cur) is not None:
                outcome = "survived(old-path)"
            # (b) rename-chain follow
            if outcome is None and followed:
                txt = show(repo, "HEAD", followed)
                if txt and scar_symbols.resolve_symbol(_anchor(followed, qual), followed, txt) is not None:
                    outcome = "survived(follow)"
            # (c) repo-wide search + jaccard disambiguation
            if outcome is None:
                outcome, jscore = resolve_via_search(repo, "HEAD", qual, fp0)
                if outcome == "orphan":
                    outcome = "orphan-correct" if not name_exists(repo, "HEAD", qual) else "FALSE-ORPHAN"

            tally[outcome] += 1
            if jscore is not None:
                jaccard_scores.append(jscore)
            if outcome in ("AMBIGUOUS", "FALSE-ORPHAN"):
                failures.append(f"{path} :: {qual} -> {outcome}")

    survived = sum(v for k, v in tally.items() if k.startswith("survived"))
    failed = tally["AMBIGUOUS"] + tally["FALSE-ORPHAN"]
    denom = survived + failed
    print(f"== Shipped-API long-horizon replay: {old_commit} -> HEAD ({prefix}) ==")
    for k in sorted(tally):
        print(f"{k:24} {tally[k]}")
    print(f"\nsurvival rate (gated): {survived}/{denom} = {survived / denom:.1%}" if denom else "no anchors")
    print("\n-- winning Jaccard distribution (step-c disambiguation only) --")
    if jaccard_scores:
        print(f"  n={len(jaccard_scores)} min={min(jaccard_scores):.3f} "
              f"median={statistics.median(jaccard_scores):.3f} max={max(jaccard_scores):.3f}")
        print(histogram(jaccard_scores))
    else:
        print("  no step-c disambiguation was triggered (all resolutions settled at step a/b)")
    if failures:
        print("\n-- failures --")
        print("\n".join(failures))


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--long":
        if len(sys.argv) != 5:
            print("Usage: replay_shipped.py --long <repo> <old_commit> <path_prefix>", file=sys.stderr)
            sys.exit(2)
        main_long(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        main(sys.argv[1:])
