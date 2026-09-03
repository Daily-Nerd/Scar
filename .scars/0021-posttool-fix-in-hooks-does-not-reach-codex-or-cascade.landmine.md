---
id: 21
type: landmine
title: A posttool change in hooks.py does not reach codex.py or cascade.py, which carry their own copy of the same logic
severity: high
confidence: 0.8
created: 2026-09-02
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: git-config
anchors:
  - path: src/scar/hooks.py
  - path: src/scar/codex.py
  - path: src/scar/cascade.py
evidence:
  - issue: 277
  - issue: 293
  - pr: 297
expires:
  condition: "the three posttool paths share one implementation, or the adapters delegate to hooks.posttool"
  review_after: 2026-12-01
violation: "if not violations:"
status: active
---

The posttool verdict logic exists three times: `hooks.posttool` for Claude
Code, `codex.posttool` for Codex, and `cascade.post_write_code` for Windsurf.
They are copies, not callers. #277 fixed the flattering-direction blind spot
(a clean edit must still write a verdict row, or a dead hook reads as perfect
compliance) in hooks.py only. The adapters kept the pre-#277 early return, so
on Codex one clean armed edit trips the "broken install" warning on a healthy
install (#293). The #277 test suite asserted the Codex row SHAPE and never
exercised the Codex clean path, so green CI proved nothing about the copies.

When you change what a posttool writes, reads, or skips in hooks.py, open
codex.py and cascade.py in the same PR and make the same change, or make them
delegate. A bare `if not violations: return` in an adapter is the exact shape
that reintroduces the blind spot, which is why it is the tripwire here.
