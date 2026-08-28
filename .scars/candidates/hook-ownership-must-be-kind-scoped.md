---
id: 0
type: landmine
title: Two hook specs share PreToolUse — ownership matching must be kind-exact, never event-wide or substring
severity: critical
confidence: 0.95
created: 2026-08-28
authors: ["claude-code"]
anchors:
  - path: src/scar/installer.py
  - pattern: "for spec in HOOKS"
violation: "if is_ours\(g\)\]"
evidence:
  - issue: 236
  - pr: 238
expires:
  condition: "no two entries in HOOKS share the same event value"
  review_after: 2027-08-28
status: candidate
---

`HOOKS` is not one spec per event. `precheck` (matcher `Edit|Write|MultiEdit|
NotebookEdit`) and `precheck-command` (matcher `Bash`) both live on
`PreToolUse`. Any per-spec loop that decides "is this entry mine?" by scanning
the whole event will have the second iteration delete what the first wrote.

That is exactly what shipped. `is_ours()` matched any scar command on an event,
so `scar hook install` wrote the `precheck` entry, then stripped it while
installing `precheck-command`, and printed a success line on the way out
(issue #236). No scar was injected before any edit for roughly a month, on
every install since command anchors. `posttool`, `session-notice` and
`stop-drafter` were never affected — each is alone on its event, which is why
the single-spec-per-event assumption looked correct for so long.

The obvious fix is also wrong: `precheck` is a string PREFIX of
`precheck-command`, so matching the kind by substring lets each claim the
other's entry and reproduces the bug in a form that reads as correct. Match
the kind exactly — `owns_kind()` uses a trailing `(?!\S)` so `hook precheck`
cannot match `hook precheck-command` while `hook precheck --flag` still does.

`uninstall()` is the one place event-wide `is_ours()` is right: it clears every
kind off an event deliberately. `install()` and `status()` must both be
kind-scoped — `status()` had the same blindness and reported both kinds
installed when only one was, which is why the one command a user would run to
check this could never have surfaced it (PR #238).
