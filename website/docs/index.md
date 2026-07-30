---
sidebar_position: 1
title: SCAR
description: Version control for negative knowledge — dead ends, load-bearing weirdness, and invisible tripwires, surfaced at the moment of action.
slug: /
---

# SCAR — version control for negative knowledge

> Git records what your codebase **is**. Nothing records what it **refused to be**.

Every codebase is a battlefield where the bodies have been removed. SCAR puts the markers back: the approaches that failed (`deadend`), the code that looks wrong on purpose (`fence`), and the couplings nothing in the code reveals (`landmine`) — as small Markdown files in `.scars/`, tracked in git, reviewed in PRs, and injected into an agent's context at the exact moment it is about to step on one.

## Why now

AI agents write an increasing share of all code, and agents have zero hallway memory. They see a weird retry loop and "clean it up." They re-add the library that was removed after a data-corruption incident. They retry, across thousands of sessions, the exact approaches that already failed — because the repository only records positive space.

The flip side: agents also remove the historically fatal adoption barrier — authorship cost. An agent that just abandoned an approach can write a `deadend` candidate in milliseconds, at the moment of maximum context. Agents created the urgency; agents remove the barrier.

## What makes SCAR different

- **Enforcement at the moment of action** — a pre-edit hook injects anchored scars before the edit; an optional post-edit tripwire catches the forbidden thing actually being done. Advisory, never blocking, by default.
- **Anchors that survive refactors** — paths, tree-sitter symbols, and diff-scoped patterns, with loud (never silent) degradation when they rot.
- **A human promotion gate** — agents and harvest write only candidates; a human promotes.
- **Measured, not asserted** — compliance is instrumented with pre-registered thresholds and published methodology, including every measurement error caught along the way. See [Measurement methodology](./methodology.md).

## Start here

- [Quickstart](./quickstart.md) — install to first firing scar in five minutes.
- [Concepts](./concepts.md) — primitives, anchors, lifecycle, fatigue budget.
- [Measurement methodology](./methodology.md) — the fired→violated instrument and its numbers.
- [Agent integration](./agents.md) — Claude Code, MCP, and the git-native path for everything else.

AI agents: a machine-readable index of these docs lives at [`/llms.txt`](pathname:///Scar/llms.txt).
