---
id: 0
type: landmine
title: A plugin hook manifest is read by every host that installs the plugin, and hosts expand different root variables
severity: high
confidence: 0.9
created: 2026-09-03
authors: ["claude-code"]
anchors:
  - path: plugin/
  - path: src/scar/installer.py
  - pattern: "PLUGIN_ROOT"
evidence:
  - issue: 303
  - issue: 244
expires:
  condition: "the plugin format gains per-host scoping for hook files"
  review_after: 2026-12-01
status: candidate
---

Claude Code merges plugin.json hooks with hooks/hooks.json and defines only
CLAUDE_PLUGIN_ROOT. Codex reads the same file and defines both PLUGIN_ROOT
and CLAUDE_PLUGIN_ROOT. A hooks/hooks.json written for Codex with
${PLUGIN_ROOT} therefore ran on every Claude session start and Bash call as
"/hooks/run.sh": No such file or directory, on every machine with the plugin
(#244 introduced it, #303 removed it). There is no way to mark a plugin
hook file as one host's only. Ship one manifest per plugin, on
${CLAUDE_PLUGIN_ROOT}, and deliver host-specific hooks through
`scar hook install --runtime <host>`, whose files live outside the plugin.
