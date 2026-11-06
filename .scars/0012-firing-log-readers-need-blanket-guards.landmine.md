---
id: 12
type: landmine
title: firing-log.jsonl has one reader: read through read_firing_log_records, never hand-roll, distrust slice math
severity: high
confidence: 0.85
created: 2026-07-02
authors: ["claude-code", "kibukx"]
anchors:
  - path: src/scar/firing_log.py
  - path: src/scar/gc.py
  - pattern: "firing_log_path\("
violation: "except \((KeyError|TypeError|ValueError|AttributeError|IndexError)[^)]*\)"
evidence:
  - pr: 122
  - issue: 124
  - issue: 312
expires:
  condition: "the shared reader validates field types (ts is a string, scar_ids is a list), not only row shape, so callers stop re-guarding fields"
  review_after: 2027-03-01
status: active
---

The firing log is append-only JSONL written best-effort from a hook that must
never fail, so ANY line shape can and will appear: `null`, `[]`, numbers,
truncated JSON, non-string `ts`, non-list `scar_ids`. Three bugs shipped from
that fact in two days (PR #122, issue #124):

1. Narrow per-line except tuples let `ts: null` (TypeError) and bare `null`
   lines (AttributeError at `rec.get`) escape a reader into precheck's outer
   fail-open, killing injection silently and PERMANENTLY.
2. `lines[-max_firings:]` with 0 is `[-0:]`, the whole list, while the report
   arithmetic claimed everything was dropped (issue #124).

Since #312 both rules live in ONE place: `read_firing_log_records` in
`src/scar/firing_log.py` (blanket `except Exception` per line, `tail == 0`
special-cased, negative tail refused). Rule now: every new reader calls it.
Do not parse the log by hand anywhere else. When editing the reader itself,
keep the blanket guard. It checks row SHAPE only (dicts kept), so callers
must still treat every field as untrusted. `gc.py` counts and truncates raw
lines on purpose (a malformed line still counts); keep its slice math
special-casing 0, and verify report numbers against the file, not arithmetic.
