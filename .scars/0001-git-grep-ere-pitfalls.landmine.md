---
id: 1
type: landmine
title: git grep -E has no \b, and zero-match pathspecs silently empty results
severity: high
confidence: 0.9
created: 2026-06-09
authors: ["claude-code", kibukx]
anchors:
  - path: experiments/anchor-survival/
  - path: src/scar/harvest.py
  - pattern: "git.{0,20}grep"
evidence:
  - note: "orphaned receipt — pre-v0.1.0 commit 5c63b14 produced a fake 0% anchor-survival run before diagnosis (gate 0.2); resolves at github.com/Daily-Nerd/Scar/commit/5c63b14 until GC, not in fresh clones (rewritten at the v0.1.0 release)"
expires:
  condition: "resolver layer gains integration tests over its git invocations"
  review_after: 2027-06-09
---

Two git quirks that combined into a silent total failure during gate 0.2:

1. `git grep -E` is POSIX ERE — `\b` is NOT a word boundary, it matches a
   literal sequence, so patterns quietly match nothing. Use `-w` (word mode),
   `-F` for literals, or an explicit `([^A-Za-z0-9_]|$)` boundary class.
2. A pathspec glob that matches zero files (e.g. `-- '*.tsx'` in a pure
   Python repo) doesn't error — it silently empties the ENTIRE result set,
   including hits from globs that do match.

Together they made every anchor look orphaned (fake "0% survival"). Any code
in this project that shells out to `git grep` must avoid `\b`, avoid
speculative extension globs (filter extensions in code instead), and have an
integration test asserting at least one known-positive match.
