---
id: 17
type: landmine
title: Codex can replay an apply_patch after PreToolUse injects additionalContext
severity: high
confidence: 0.8
created: 2026-08-28
authors: ["codex", "Kibukx"]
anchors:
  - path: src/scar/codex.py
  - path: plugin/hooks/hooks.json
evidence:
  - issue: 243
  - note: live codex-cli 0.147.0 A/B emitted two tool_use_ids for one identical apply_patch after the first PreToolUse context injection
expires:
  condition: "Codex documents and demonstrates exactly-once tool execution when PreToolUse returns additionalContext"
  review_after: 2027-02-28
status: active
---

The issue #243 live smoke test applied a patch, delivered SCAR context, then
issued the identical patch again under a second `tool_use_id`; the retry failed
because the first call had already changed the expected line. The hook never
requested a block or rewrite.

Keep Codex hook handlers idempotent, side-effect-light, and correlated by
`tool_use_id`. Never assume one PreToolUse invocation per user-intended edit,
and never make correctness depend on the hook preventing the pending action.
