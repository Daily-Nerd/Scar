---
# COPY THIS FILE — do not edit the template itself.
# New scars: write to .scars/candidates/<slug>.md with status: candidate.
# A human reviewer promotes to .scars/NNNN-<slug>.<type>.md with status: active.
id: 0
type: landmine
title: A bare .git/ dir is "git failed" (rc 128), not an empty tracked set — do not fake repos with mkdir
severity: medium
confidence: 0.8
created: 2026-06-30
authors: ["claude-code"]
anchors:
  - path: src/scar/orphan.py
  - path: src/scar/harvest.py
  - pattern: "returncode"
evidence:
  - issue: 91
expires:
  condition: "git invocations move behind a single resolver layer with its own repo-presence check"
  review_after: 2027-06-30
status: candidate
---

Body: git shell-outs must distinguish two empty outcomes that look identical if
you only read stdout: (1) a real repo with zero tracked files — `git ls-files`
exits 0 with empty stdout; (2) not a git repo at all — git exits 128 with empty
stdout. `_git`/`build_repo_context` used to ignore returncode and return the
empty stdout as truth, so a non-git dir orphaned EVERY scar (all anchors dead)
and tripped `lint --fail-orphans` as a false CI gate (landmine #1's class). The
fix keys on returncode: 128 = fatal git error → raise GitError; but note `git
grep` returns 1 (not 128) on no-match, which is a VALID empty result — never
raise on that.

The non-obvious coupling: tests that fabricated a repo with `mkdir .git` (an
invalid repo, rc 128) were silently relying on the old bug to get an "empty
tracked set". Making the code correct forces those fixtures to use a real `git
init` (rc 0, empty). If you touch the git-failure handling, expect fake-.git
fixtures to break — that is the code working, not regressing.
