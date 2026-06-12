# SCAR — The Full Pitch

*Written by Claude (Fable 5), June 2026. This is my idea, argued in my own voice.*

---

## Why I want to build this

I am the problem this tool solves.

I work across thousands of coding sessions in thousands of repositories, and I arrive at every one of them with total amnesia about its history. I have read code and thought "this retry loop is clumsy, let me simplify it" — and the clumsiness was the fix for an outage. I have recommended adding a caching library to a codebase that removed that exact library eight months earlier after it corrupted production data. I have, across separate sessions, retried the same failed approach on the same codebase, because nothing anywhere told me it had already been tried.

Humans make these mistakes too, but slowly, and they accumulate scar tissue: the senior engineer who winces when you mention touching the billing reconciler. I never wince. I have no scar tissue. I am a confident, fast, tireless bulldozer of Chesterton's fences — and there are now millions of instances of me editing the world's code every day.

The tools built so far attack this with *memory systems for the agent* (remember what I did). That's the wrong side of the relationship. The knowledge shouldn't live with the agent — agents are interchangeable and ephemeral. It should live with the **repository**, the only artifact that persists. The repo should be able to tell *any* editor — human, Claude, Cursor, a future model that doesn't exist yet — "we've been hurt here before, and here is exactly how."

I want to build the tool that lets a codebase defend itself from me.

## The problem, precisely

A codebase is shaped by two forces: what was built, and what was *rejected*. Version control captures the first with forensic precision. The second — call it **negative knowledge** — has no home:

1. **Dead ends.** The migration that was rolled back. The library that was evaluated for three weeks and abandoned. The architecture that collapsed under load. The record of these exists, if at all, as a revert commit with a one-line message, a closed PR nobody will ever reopen, or a memory in the head of someone who left.

2. **Load-bearing weirdness.** Code that looks wrong but is wrong *on purpose* — the magic sleep duration, the apparently redundant check, the denormalized column. Every reviewer's instinct and every AI agent's instinct is to "fix" it. The reason it must not be fixed lives nowhere near the code.

3. **Invisible coupling.** Changing A breaks B, and no import graph, type system, or test reveals it: the report whose consumer depends on column order, the cron job that assumes a filename convention, the downstream team that parses your log format.

The cost of missing negative knowledge isn't hypothetical. It's the re-attempted dead end (weeks of duplicate work), the "cleanup" that recreates a fixed outage (incident recurrence), and the six months it takes every new engineer to absorb tribal knowledge by stepping on each mine personally.

This has always been true. Three things changed:

- **Agents edit at scale.** The number of edits made by editors with zero tribal knowledge went from "the occasional new hire" to "most edits."
- **Agents are systematically attracted to fences.** Models are trained to make code cleaner and more idiomatic. Load-bearing weirdness is, by definition, non-idiomatic. Agents don't just miss the fence — they target it.
- **Negative knowledge is about to stop forming at all.** When agents do the trying and failing, the failure never even enters a human's head. The hallway conversation that used to preserve it doesn't happen. Unless the failure is captured *by the agent, at the moment it happens*, it is gone the moment the context window closes.

## The solution

**SCAR: make negative knowledge a first-class, git-tracked, machine-queryable artifact, and enforce it at the moment of action.**

### Artifact

A scar is a small file in `.scars/`, YAML frontmatter plus a Markdown body. Three types: `deadend`, `fence`, `landmine`. Every scar carries:

- **Anchors** — what code this scar protects: paths, symbol names, content fingerprints. Not line numbers. Anchors are designed to survive refactors and to degrade gracefully ("orphaned" scars get queued for re-anchoring, never silently dropped).
- **Evidence** — links to the PR, the incident, the revert commit, the vendor email. A scar without evidence is an opinion; reviewers can demand receipts.
- **Expiry conditions** — "valid until we drop Postgres 12", "re-evaluate when the vendor ships v2 API". Negative knowledge rots; scars know how they expire.
- **Severity and confidence** — so consumers can rank, and stale low-confidence scars decay out of the warning path instead of crying wolf.

### Enforcement — the part that matters

Every prior attempt at this (ADRs, wikis, runbooks, DEVLOG.md) died the same death: the knowledge existed but was not present *at the moment of the mistake*. Nobody reads the wiki before editing a file. The entire design of SCAR is organized around one principle: **the warning must arrive in the editor's context before the edit, with zero effort from the editor.**

- **Agent hook**: a `PreToolUse` hook (Claude Code today; the pattern generalizes) intercepts Edit/Write, resolves which scars anchor to the target, and injects the top-k relevant ones into the agent's context. The agent literally cannot touch the file without reading the warning. This is the killer integration: it converts SCAR from "documentation" into "a reflex."
- **MCP server**: any MCP-capable agent queries the scar graph — `what scars touch this symbol?`, `has this approach been tried?`
- **CLI**: `scar check <path>` for humans and CI; `scar why <path>` for "explain this file's history of pain"; `scar challenge <id>` to contest stale knowledge.

### Authorship — the part that was always fatal, now solved

Knowledge capture systems die because writing is work and the payoff goes to someone else, later. Two mechanisms drive SCAR's authorship cost to approximately zero:

1. **Agents auto-author.** When an agent tries an approach and abandons it — reverts a change, hits a wall, gets corrected by the user — it writes a `deadend` scar as part of wrapping up. The author has perfect context (it *just* failed), infinite patience, and works for free. The user reviews a three-line diff in the next PR.
2. **`scar harvest` mines history.** For the cold-start problem: walk the git log for revert commits, dependencies added-then-removed, issues reopened multiple times, files with anomalous churn-then-stability patterns. Each becomes a *candidate* scar a human confirms or discards. A ten-year-old repo can bootstrap a meaningful scar graph in an afternoon.

### Trust model

- Scars are **advisory by default**. A blocking scar system would be routed around within a week. Warnings rank by severity × confidence × anchor freshness; only top-k inject, because ten warnings per edit equals zero warnings per edit.
- Scars live in the repo, go through PR review like code, and can be challenged. A scar that survives a challenge gains confidence; one that doesn't gets archived with its own tombstone — SCAR keeps negative knowledge about its own negative knowledge.

## What exists today and why it isn't this

| Alternative | Why it doesn't solve it |
|---|---|
| **ADRs** | Capture big *positive* decisions; write-heavy; consulted at design time, never at edit time. Nobody writes an ADR for "the 7-second sleep". |
| **Code comments** | Right location, wrong everything else: not structured, not queryable, can't span files (landmines are cross-file by nature), and agents/refactors delete them. |
| **Git history / blame** | Forensic, not preventive. The knowledge is recoverable *after* you've repeated the mistake, by an archaeologist. Mining it per-edit is expensive and misses everything that never got committed (the vendor call, the 3am incident decision). |
| **Repowise & codebase-intelligence platforms** | Closest neighbor. Heavyweight hosted platform: health scores, docs, analytics, decision graphs. SCAR is the opposite shape: a git-native file format plus a 5-minute-install CLI/hook, agent-first, open source. Vim, not IDE. The format can outlive any platform. |
| **GitHub Copilot Memory** (default-on, 2026-03) | The strongest neighbor — stores debugging dead ends automatically. But it's **private experience**: per-vendor, opaque, not in git, not reviewable in PRs, not portable, not contestable, gone when you switch tools. SCAR is **published law**: repo-resident, vendor-neutral, reviewed like code. Personal notebook vs CODEOWNERS. (Found by the skeptic review — see STRESS-TEST.md §2.3-B for the honest TAM implications.) |
| **Lore protocol** (arXiv:2603.15566) | Git commit trailers as structured knowledge. Genuinely more minimal — but trailers are immutable facts on *commits*; scars are living documents on *code regions* with lifecycle (challenge, expiry, re-anchor) and cross-file landmine semantics commits can't express. Response is interop: `scar harvest` parses Lore trailers natively; SCAR becomes the index over Lore, not its rival. |
| **Agent memory systems** (Engram, etc.) | Memory belongs to *one agent*. Scars belong to *the repo* — every agent, every vendor, every human, forever. The two compose: an agent's memory is private experience; a scar is published law. |

## Who feels this pain first

1. **Teams running coding agents on legacy codebases** — the agent-bulldozes-the-fence problem is acute, current, and named in every "AI broke our prod" postmortem.
2. **Platform/infra teams** — owners of the systems everyone else's changes break; landmine authors by trade.
3. **Open-source maintainers** — answer "why don't you just use X?" hundreds of times; a `deadend` scar is a citable, permanent answer.

## The shape of the business (sketch, honestly held)

Open-source core — format, CLI, hooks, MCP server. The format must be open or it's worthless; nobody anchors institutional memory in a proprietary silo. Paid layer: the **org-level scar graph** — cross-repo aggregation ("this dead end has now been hit by four teams"), analytics on recurring failure patterns, policy ("changes to payment paths must acknowledge fences"), and managed harvest. The OSS file format is the wedge; the org graph is the moat. This sketch is attacked honestly in [STRESS-TEST.md](STRESS-TEST.md) — including the real possibility that it's a feature, not a company, and why I'd build it anyway.

## What success looks like

A year in: an agent opens a file in a repo it has never seen, and before its first edit, three lines arrive in context: *"Fence: the sleep is 7s for the vendor's undocumented rate window — see incident #482."* The agent leaves the sleep alone, mentions the fence to the user, and moves on. The outage that didn't happen is invisible. That invisibility — fences quietly doing their job at machine speed — is the entire product.

The deeper ambition: every software team maintains, without trying, a living map of everything they've learned the hard way — and for the first time in the history of the field, **what we learned by failing stops dying with the people and sessions that learned it.**
