---
id: 0
type: landmine
title: A health check that watches one direction of pipeline failure leaves the flattering direction unwatched
severity: high
confidence: 0.9
created: 2026-08-30
authors: ["claude-code"]
anchors:
  - path: src/scar/cli.py
  - path: src/scar/hooks.py
  - pattern: "instrument_disconnected|posttool_silent|verdict_observed"
evidence:
  - issue: 277
  - issue: 237
expires:
  condition: "the pipeline stops having two independently-installable halves"
status: candidate
---

The precheck and posttool hooks install separately and fail separately. #237
added a guard for one of those failures: violations recorded with zero precheck
rows means the precheck half is dead. That guard was correct and it shipped
after a real four-week outage.

Nobody wrote the mirror, and the mirror was the dangerous one. Posttool used to
`return 0` before logging anything when it found no violation, so a clean edit
left no trace. That made a dead posttool hook produce zero violations against a
full firing count, which is byte-identical to perfect compliance. The guarded
direction was the one that made the numbers look bad. The unguarded direction
was the one that made them look good, and it stayed unguarded for months.

Two rules follow, and both are load-bearing. First, when a pipeline has halves
that fail independently, a health check for one half is not a health check.
Ask which failure the existing guard CANNOT see, and assume it is the flattering
one until proven otherwise. Second, absence of a record is only evidence when
the writer emits a record in the negative case too. Posttool now logs on the
clean path whenever a violation was possible, and that is the only reason
`verdicts_unresolved` can mean anything.

Do not "simplify" posttool by restoring the early return on no-violation. It
looks like dead work and it is the entire mechanism.
