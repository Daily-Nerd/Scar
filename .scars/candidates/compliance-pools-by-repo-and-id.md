---
id: 0
type: landmine
title: Pooled stats must key a scar by (repo, id); a bare id fuses different scars and a pooled rate must be recomputed, never averaged
severity: high
confidence: 0.8
created: 2026-11-07
authors: ["claude-code"]
anchors:
  - path: src/scar/cli.py
  - pattern: "def _compliance_block"
evidence:
  - issue: 373
  - issue: 137
expires:
  condition: "compliance pooling stops spanning more than one repository"
  review_after: 2027-05-07
status: candidate
---

Scar ids are per-repo sequential integers, so #1 in one repo is a different
scar from #1 in another. `--repo` pools several repos into one compliance block,
and the dominant-scar cut picks the single largest scar. Keyed by bare id, two
unrelated scars merge into one and the "top scar" share is invented. It is the
same defect as the original --all-repos double count (#137), reached through a
new door.

Two rules hold the block together. Identify a scar by (repo, id) everywhere in
`_compliance_block`. And pool by summing F, V and the verdict counts, then
computing the rate once; averaging per-repo rates weights a one-firing repo the
same as a thousand-firing one. Scope filters (window, repo set) run on records
before any metric, exactly like `_filter_window`, so a set of two repos equals a
log that contains only those two.

V is "fired then violated": a violation counts only if that scar has an armed
firing on the same target at or before it. The rest are retrieval misses and
are reported as `violations_unmatched`, not folded into V (#373).
