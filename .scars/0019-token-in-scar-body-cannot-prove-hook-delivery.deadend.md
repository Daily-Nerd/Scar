---
id: 19
type: deadend
title: A token in a scar body cannot prove hook delivery — the agent can just read the file
severity: high
confidence: 0.95
created: 2026-08-28
authors: ["claude-code", "Kibukx"]
anchors:
  - path: src/scar/codex.py
  - path: src/scar/hooks.py
evidence:
  - pr: 244
  - pr: 247
  - issue: 246
expires:
  condition: "a delivery check exists that observes hook execution directly (firing-log row or marker) rather than model output"
  review_after: 2027-02-28
status: active
---

Putting a private token in an active scar's body and looking for it in the
model's reply does NOT prove the host→adapter→model hook chain fired. The scar
lives in a readable file inside the repo, so the agent can simply find it.

Tried on 2026-08-28 to verify the Codex runtime after #247. The token came back
four times and the model refused the forbidden edit quoting the scar verbatim —
a convincing-looking pass. The transcript shows the agent ran
`rg --files -g '.scars/**'` and then `sed` on the scar file. It read the scar
itself. The firing log delta over the whole run was **zero**: the model refused
before issuing any `apply_patch`, so `codex-pretool` never fired at all. The
token proved only that a file existed.

This retroactively weakens the same claim made for #244, where a token in a scar
body was taken as proof of delivery.

Prove delivery by observing the hook's own execution, never the model's output:
a new firing-log row tagged with the runtime, or a marker file the hook writes.
Also force the tool call under test to actually happen — an agent that honors
the scar and declines to edit produces no PreToolUse event on that tool, so a
"good" outcome and a dead hook look identical.
