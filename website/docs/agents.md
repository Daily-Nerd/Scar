---
sidebar_position: 5
title: Agent integration
description: Claude Code hook and plugin, MCP server, and the git-native path for every other runtime.
---

# Agent integration

SCAR treats AI agents as first-class users: they trigger scars, they read scars, and they author scars (into `candidates/`, behind the human promotion gate).

## Claude Code (reference integration)

**Recommended: install the plugin** from the marketplace — hooks and the scar-authoring skill arrive together.

Manual fallback:

```bash
scar hook install     # PreToolUse injection + PostToolUse violation tripwire
scar hook status
scar skill install    # authoring skill into ~/.claude/skills/
```

How the hook behaves:

- `PreToolUse` on `Edit|Write|MultiEdit|NotebookEdit`: resolves the target path, runs `scar inject --path <target> --top-k 3`, and emits ranked scars as `additionalContext`. Advisory — never blocks the tool call.
- `PreToolUse` on `Bash`: fires command-anchored scars against the shell command about to execute (`scar inject --command "<cmd>"` is the same surface for other runtimes) — the injection path for run-a-command mistakes that edit anchors cannot cover.
- `PostToolUse`: runs any armed `violation:` regex against the edit just made and logs fired→violated events.
- Everything is installed only by explicit command; `scar hook uninstall` removes it cleanly.

After upgrading scar-cli, re-run `scar skill install` — the installed skill is a static copy and does not update itself.

## MCP server (any MCP-capable agent)

```bash
scar mcp
```

A dependency-free stdio server speaking newline-delimited JSON per the MCP spec. It exposes:

| Tool | What it does |
|---|---|
| `scar_query` | Ranked scars for paths, content, or a unified diff |
| `scar_why` | Full scar history anchored to a path |
| `scar_draft` | Write a candidate scar — only ever to `.scars/candidates/` |

Config snippets for supported hosts:

```bash
scar agent doctor
scar agent config codex      # or: cursor, windsurf, opencode
```

## Every other runtime: git-native

No hook API? `scar draft-check` runs from a plain git hook and nudges *any* agent (or human) toward authoring, driven entirely by git evidence — revert language in commit messages, actual `git revert`/`reset --hard` history, churn:

```bash
scar hook install --git      # writes .git/hooks/post-commit
```

Throttled to one nudge per hour per repo, advisory only, always exits 0. And `scar agent skill` prints the full authoring contract as text for pasting into any runtime's instructions.

## Orchestrators and sub-agents

Sub-agents launch in fresh contexts: edit-anchored scars reach them (the hook fires inside their session), but process-level knowledge — command traps, workflow rules — only arrives if the launch prompt carries it. `scar brief --compact` makes that mechanical:

```bash
scar brief --compact --paths src/payments/ --max-chars 1500
```

One tight line per scar, severity-ordered, byte-capped, plain text with omissions reported (never silent). Command-anchored scars are always included — they are precisely the ones the edit hook cannot deliver. Orchestrator pattern: run it with the files the sub-agent will touch, prepend the block to the launch prompt.

## CI

```bash
scar check src/ --exit-code                   # gate on firing scars
scar check --diff changes.patch --exit-code   # gate on violation: tripwires against a diff
scar lint                                     # format, dead tripwires, overdue reviews, rot
```

Advisory by default everywhere; CI opts into strictness explicitly.

## Machine-readable docs

Agents reading these docs programmatically: fetch [`/llms.txt`](pathname:///Scar/llms.txt) for a plain-text index of every page with stable URLs, plus the changelog.
