---
id: 0
type: landmine
title: lint only re.compiles pattern anchors — pathological regex passes the gate then hangs search()
severity: high
confidence: 0.9
created: 2026-06-30
authors: ["claude-code"]
anchors:
  - path: src/scar/lint.py
  - path: src/scar/match.py
  - pattern: "rx\.search\("
  - pattern: "MAX_ANCHOR_SCAN"
evidence:
  - issue: 88
expires:
  condition: "lint gains full regex-safety analysis OR the matcher runs on a hardened regex engine with a hard timeout"
  review_after: 2027-06-30
status: candidate
---

A pattern anchor's regex is validated in `lint.py` by `re.compile()` ONLY — a
syntax check. A valid-but-pathological anchor such as `(a+)+$` compiles clean,
so it passes lint and can be promoted to an active scar. It then reaches
`_pattern_anchor_matches()` in `match.py`, which does
`re.compile(pattern).search(text)` with no timeout — and on the read hot path
`text` is unbounded `new_content` (and whole file bodies in `orphan.py`). On
adversarial input it backtracks catastrophically and never returns: just 40
`a`s followed by a non-`a` hangs for >6s. Compiling ≠ safe to run.

The real defense is the GATE, not the cap — do not confuse the two.
`lint._is_redos_prone()` errors on the classic nested-quantifier forms, and the
hot path only matches active (merged, CI-linted) scars, so a pathological anchor
cannot reach `search()` through the normal workflow. That is the ReDoS
protection.

The `MAX_ANCHOR_SCAN` (64 KiB) input cap does NOT stop catastrophic
backtracking: `(a+)+$` hangs on ~30 chars, far under the cap. The cap only
bounds the HUGE-input case. Do not read the cap as a runtime ReDoS guard, and do
not delete the lint heuristic thinking the cap covers it — it does not.

Residual risk (accepted): a pattern hand-authored into a LOCAL `.scars/` that
never passed CI can still backtrack in `_pattern_anchor_matches()`. Bounding
arbitrary regex at runtime needs a killable subprocess or a timeout-capable
engine; both were rejected to keep this hot path stdlib-only, cross-platform, and
under the hook latency budget (no `regex` module, no SIGALRM — SIGALRM cannot
interrupt a C-level `re.search` anyway). See issue #88.
