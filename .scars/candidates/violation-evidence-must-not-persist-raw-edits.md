---
id: 0
type: fence
title: Violation evidence omits raw edits deliberately because excerpts can contain secrets
severity: high
confidence: 0.9
created: 2026-11-05
authors: ["codex"]
anchors:
  - path: src/scar/violation_evidence.py
  - path: src/scar/hooks.py
evidence:
  - issue: 317
expires:
  condition: "a reviewed capture design can retain original edits without exposing arbitrary secrets"
status: candidate
---

The display excerpt is arbitrary edited content, not safe log text (#317).
Truncating to 120 characters can retain an entire credential. The shared
verdict writer therefore persists a bounded summary, never the raw excerpt
or violation regex. Do not add either string to make the log more readable.
Capture positions and fingerprints from the original regex match; searching
the truncated excerpt again can miss the actual match on a long line.
Hashes still permit guessing low-entropy content, so the log remains sensitive.
A summary needs an independently retained edit for review; it is not a
confirmed true positive. Old rows without evidence must stay unknown, never
be backfilled from current files or current scar definitions.
