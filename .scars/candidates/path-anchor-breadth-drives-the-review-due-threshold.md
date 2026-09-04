---
id: 0
type: landmine
title: A whole-file or whole-directory path anchor makes the review-due threshold report anchor breadth, not code hazard
severity: medium
confidence: 0.8
created: 2026-09-04
authors: ["claude-code"]
anchors:
  - path: SCAR-FORMAT.md
  - pattern: "review_after_firings"
evidence:
  - issue: 312
  - note: "triage of all five over-threshold scars in this repo on 2026-09-04: four were over-anchored, one was a real code fix"
expires:
  condition: "lint reports what share of a scar's firings touched the hazard, so the review-due warning can distinguish breadth from recurrence"
  review_after: 2027-03-04
status: candidate
---

`scar lint` warns past `review_after_firings` that either the guarded code
merits the fix or the scar merits revision. Read literally that sends you to
the code first. It is usually the anchor.

A `path:` anchor is a repo-relative PREFIX, so it fires on every edit beneath
it. Scar #8 guards a 17-line tty branch inside a 52-line module but anchored
the whole 3,090-line `src/scar/cli.py`; roughly nine of every ten of its 47
firings touched nothing related. Scar #5 guards against history rewrites and
anchored all of `.scars/`, so it fired on 22 routine promotions and zero
force-pushes, which is zero of its actual trigger events.

A firing count therefore measures anchor breadth first and hazard recurrence
second, and the two are not separable from the log. Before reading a
review-due warning as evidence that code needs fixing, check what share of the
firings touched the hazard at all. Then narrow the anchor to the smallest file
holding the branching, or switch to a `command:` anchor when the trigger is an
action rather than a file (#312 is the one case here that really was the code).
