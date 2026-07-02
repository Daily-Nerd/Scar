---
id: 12
type: landmine
title: firing-log.jsonl handlers are a bug cluster — guard every line blanket, distrust slice math
severity: high
confidence: 0.85
created: 2026-07-02
authors: ["claude-code", "kibukx"]
anchors:
  - path: src/scar/gc.py
  - pattern: "firing[-_]?log"
evidence:
  - pr: 122
  - issue: 124
expires:
  condition: "firing log gains a schema-validated reader shared by hooks/stats/gc"
  review_after: 2026-10-01
status: active
---

Three firing-log bugs shipped or were caught in two days (PR #122, issue #124).
The log is append-only JSONL written best-effort from a hook that must never
fail — so ANY line shape can and will appear: `null`, `[]`, numbers, non-string
`ts`, non-list `scar_ids`.

1. Narrow per-line except tuples let `ts: null` (TypeError) then bare `null`
   lines (AttributeError at `rec.get`) escape a reader into precheck's outer
   fail-open — injection silently and PERMANENTLY killed (log never grows past
   the bad line). Fixed twice in PR #122; final form is a blanket
   `except Exception: continue` per line.
2. `lines[-max_firings:]` with 0 is `[-0:]` == keep everything, while the
   report arithmetic claims everything was dropped (issue #124).

Rule: any new reader of this log wraps the ENTIRE per-line body in
`except Exception: continue`, and any truncation special-cases 0. Verify
report numbers against the file, not against arithmetic.
