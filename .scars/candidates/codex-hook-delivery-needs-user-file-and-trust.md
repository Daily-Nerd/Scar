---
id: 0
type: landmine
title: Codex plugin hooks are not materialized, and untrusted Codex hooks are skipped silently
severity: high
confidence: 0.9
created: 2026-08-28
authors: ["claude-code"]
anchors:
  - path: plugin/hooks/hooks.json
  - path: src/scar/codex.py
  - pattern: "hooks/hooks\.json|CODEX_HOME|codex-(pretool|posttool|session-notice)"
evidence:
  - pr: 244
  - pr: 247
  - issue: 246
expires:
  condition: "Codex materializes a plugin's hooks/hooks.json into the installed cache AND surfaces an error for untrusted hooks"
  review_after: 2027-02-28
status: candidate
---

Shipping a Codex hook through a plugin's `hooks/hooks.json` does not deliver it,
and a delivered hook still does not run until a human trusts it.

Two independent failures, both silent, verified on a live codex-cli 0.147.0
install that had the Scar plugin present with `enabled = true` (#246):

1. The materialized plugin cache carried `hooks/run.sh` but **no** `hooks.json`,
   and the generated `.codex-plugin/plugin.json` had no `hooks` block. PR #244's
   adapter was therefore unreachable for a month — `codex-pretool`,
   `codex-posttool` and `codex-session-notice` could never be invoked. The
   supported delivery path is the user-level `~/.codex/hooks.json`
   (`$CODEX_HOME`-aware), which is what `scar hook install --runtime codex`
   writes (#247).
2. Codex runs a hook only once its definition hash is trusted in `/hooks`, and
   it skips an untrusted hook with **no error and no stderr**. Verified
   directly: a hook config parsed, printed its own `clamping SessionEnd hook
   timeout to 3s` warning, and the hook never executed. An untrusted install is
   indistinguishable from a working one.

Consequence for any future Codex work: never treat "the config is written" as
"the hook runs" — that is the #236 lesson in a second host. Assert delivery by
observing an effect (a marker file, a private token echoed back by the model),
and make every install path say out loud that trust is a separate, mandatory,
human step. Re-trust is required after any upgrade that changes a definition.

`~/.codex/hooks.json` is also shared with other tools, so it must be merged and
backed up, never rewritten.
