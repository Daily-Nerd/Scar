---
sidebar_position: 2
title: Quickstart
description: Install to first firing scar in five minutes.
---

# Quickstart

## Install

```bash
uv tool install scar-cli   # or: pipx install scar-cli
```

Symbol anchors (function/class names that survive file moves) need the tree-sitter extra; without it they silently don't resolve and only `scar lint` warns:

```bash
uv tool install "scar-cli[symbols]"
```

The parser and agent-hook hot path are stdlib-only; the human-facing CLI adds `rich` + `rich-argparse` for formatted output. Python ≥ 3.10.

## First scar

```bash
cd your-repo
scar init                  # creates .scars/ with a template, README, and a seeded example candidate
```

Write your first scar by copying the template into `candidates/`:

```bash
cp .scars/template.md .scars/candidates/redis-sessions.md
$EDITOR .scars/candidates/redis-sessions.md
```

Validate and promote — promotion is the human review gate that turns a candidate into an active, firing scar:

```bash
scar lint                        # validate format
scar promote redis-sessions.md   # candidate -> active
```

## Daily commands

```bash
scar check src/auth/       # which scars are anchored here? (--exit-code for CI)
scar why src/auth/         # full history of pain for a path
scar status                # repo health: active / orphaned / challenged / expiring
scar harvest               # mine git history for candidate scars
scar harvest --write 5     # render the top 5 candidates as reviewable drafts
```

## Wire an agent

For Claude Code, the hook injects relevant scars into the agent's context *before* it edits an anchored file:

```bash
scar hook install
scar hook status
```

Hooks are advisory, never blocking, and installed only by this explicit command. `scar hook uninstall` stops all automatic injection while keeping `.scars/` intact.

For every other runtime — MCP hosts, Codex, Cursor, Windsurf, opencode, plain git — see [Agent integration](./agents.md).

## Knowledge lifecycle

Nothing expires automatically; retiring knowledge is a human decision with the same governance as promotion:

```bash
scar challenge 3 --reason "vendor fixed the rate limiter"   # dispute: still fires, marked disputed
scar archive 3 --reason "confirmed fixed upstream"          # retire: never fires again, history kept
```

`scar lint` and `scar status` surface any scar whose `review_after` date has passed.
