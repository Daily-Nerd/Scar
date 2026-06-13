---
id: 0
type: landmine
title: Pattern anchors over-escape through YAML double-quotes and silently only self-match
severity: medium
confidence: 0.9
created: 2026-06-13
authors: ["claude-code"]
anchors:
  - path: src/scar/orphan.py
  - path: src/scar/match.py
  - path: .scars/
evidence:
  - pr: 40
  - issue: 41
  - commit: f12354f
  - note: "scar 1 grep pattern matched only its own body, never experiments/anchor-survival/RESULTS.md"
expires:
  condition: "pattern anchors are authored through a validated path (e.g. scar draft) that escapes regex correctly, OR lint rejects a pattern whose only pre-exclusion match is the scar's own file"
  review_after: 2027-06-13
status: candidate
---

A regex written in a scar's `pattern:` field passes through YAML double-quoted
string parsing before it ever reaches the matcher. Backslashes collapse: what
you type as a four-backslash word boundary in the file becomes a regex needing
*literal* backslashes, not a word boundary. The intended code almost never
contains literal backslashes, so the pattern matches nothing real.

The trap is that it still reads as LIVE. Pattern anchors are matched against ALL
tracked content, including the scar's own `.scars/` body, and the body quotes
the pattern verbatim, so the scar keeps itself alive by self-reference. Orphan
detection sees a live anchor and stays quiet. The protection is dead; the gauge
says green. On this repo, scars 1 and 5 were pure ghosts (own-body only) and
scar 2 matched zero files at all, none visible until self-referential exclusion
was added in PR #40 (`_pattern_anchor_live(..., exclude_path=self_path)`).

What a future editor must do: when adding a `pattern:` anchor, verify it matches
the REAL code with `scar lint` (it must NOT appear under partial-rot), not just
that the scar parses. Prefer a `path:` anchor when the target is a file or dir;
path anchors do not go through regex escaping and cannot self-match. If you must
use a regex with escapes, test it against tracked content excluding the scar's
own file before trusting it.
