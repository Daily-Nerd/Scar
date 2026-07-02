---
id: 9
type: deadend
title: replay_shipped.py measures SYMBOL-anchor survival only — there is no seam to wire PATH-anchor rename-following (#109) into it
severity: low
confidence: 0.85
created: 2026-07-01
authors: ["claude-code", "Kibukx"]
anchors:
  - path: experiments/anchor-survival/replay_shipped.py
  - path: experiments/anchor-survival/replay.py
  - path: src/scar/renames.py
evidence:
  - issue: 109
  - issue: 102
expires:
  condition: "the anchor-survival harness grows a path-anchor survival mode (a new replay script or a --anchor-kind flag), or scar.renames is imported anywhere under experiments/"
  review_after: 2027-01-01
status: active
---

Body: #109 asked to "wire rename-following into the shipped-mechanism replay
(there may be a natural seam where it checks anchor liveness)" and re-measure
survival before/after. I read replay_shipped.py, replay.py, PROTOCOL.md and
RESULTS.md end to end looking for that seam — there isn't one, and grafting
one in would silently change what the number means.

The harness (`replay_shipped.py`, `replay.py`, `long_replay.py`) measures
SYMBOL-anchor survival only: it plants a `path::qualified_name` anchor via
`scar.symbols.fingerprint`, and resolves it via `scar.symbols.resolve_symbol`
+ Jaccard similarity (issues #94/#98/#99/#100/#101/#102). It never imports
`scar.orphan` or `scar.renames`, never calls `_path_anchor_live`, and does not
model PATH anchors (the `- path:` anchor kind) at all — confirmed by grep,
zero hits for "path_anchor" or "orphan" anywhere in experiments/anchor-survival/.

The harness already has ITS OWN independent rename-following for the SYMBOL
case — step (b) in `resolve_one()`/`main_long()`, backed by `rename_events()`
in replay.py (`git log -M50 --diff-filter=R`, structurally close to
`scar.renames.build_rename_map` but a separate, older, experiment-local
implementation). That step has been active since the original prototype and
is already baked into the published 94.8%/94.6% and 88.0%/92.5% numbers — it
is not something #109 changed or could improve, because it resolves a
different anchor kind through a different code path.

What a future editor must do instead: #109's rename-following protects
PATH anchors (`scar orphan`/`scar lint`/`scar status`, dead-anchor detection
in src/scar/orphan.py). To re-measure ITS effect on survival, a NEW harness
(or a new mode of this one) would need to: (1) plant PATH anchors instead of
symbol anchors, (2) delete/rename the anchored file across real refactor
commits, (3) run scar.orphan.detect_orphans with repo= set (or without) and
compare orphan counts. That harness does not exist yet. Do not bend
replay_shipped.py's symbol-anchor step (b) into a #109 rename-following
call — it already does rename detection for its own (different) purpose, and
"wiring in" scar.renames there would double up two independent rename
walks measuring two different anchor kinds, muddying both numbers.
