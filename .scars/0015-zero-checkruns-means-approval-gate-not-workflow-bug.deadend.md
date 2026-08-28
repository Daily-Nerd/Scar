---
id: 15
type: deadend
title: Zero check-runs on a bot/fork PR means the Actions run-approval gate, not a workflow trigger bug
severity: medium
confidence: 0.95
created: 2026-08-23
authors: ["claude-code", "Kibukx"]
anchors:
  - path: .github/workflows/
  - pattern: "pull_request"
evidence:
  - pr: 172
  - pr: 201
  - pr: 209
  - note: runs 30890868402/30890868324/30890868284 sat completed/action_required 2026-08-04-15; approving them unblocked 0.17.0 after 2 weeks. Recurred on #209 (runs 32802363141/164/162, sha acd12eb).
expires:
  condition: "repo Actions setting changed to require approval for first-time contributors only, or approval gate removed"
  review_after: 2027-02-23
status: active
---

A PR showing `mergeStateStatus: BLOCKED` with ZERO check-runs on its head SHA is
NOT a workflow trigger bug. Editing workflow `on:` config to "restore" the
missing checks is the dead end — the workflows were correct; #172 lost two weeks
to it. The runs EXIST, parked at `conclusion: action_required`: GitHub's
"approve workflow runs" gate, which fires for fork PRs from first-time
contributors (#201) AND bot-created PRs (release-please, #172, #209).

`gh pr view --json statusCheckRollup` renders such runs as an empty list, so the
gate is invisible from the PR view. Diagnose with
`gh api "repos/{o}/{r}/actions/runs?branch={b}"` and filter
`conclusion=="action_required"`.

Fix is one click (Approve and run) or `POST /actions/runs/{id}/approve` — never a
workflow edit. Agent-side approval is classifier-denied; hand the click to the
human.
