# SCAR — Version Control for Negative Knowledge

> Git records what your codebase **is**. Nothing records what it **refused to be**.

SCAR is a git-native system for capturing, anchoring, and enforcing the *negative knowledge* of a codebase — the dead ends, the load-bearing weirdness, the invisible tripwires — and surfacing it at the exact moment someone (human or AI agent) is about to step on it.

## The one-liner

**Every codebase is a battlefield where the bodies have been removed.** SCAR puts the markers back.

## The three primitives

| Type | Meaning | Example |
|------|---------|---------|
| `deadend` | We tried X. It failed because Y. Don't retry unless Z changes. | "We tried Redis for session storage in 2024-03. Eviction under memory pressure logged users out mid-checkout. Don't retry unless sessions become re-derivable." |
| `fence` | This code looks wrong. It is intentional. Here's why. | "Yes, this retry loop sleeps 7 seconds, not 5. The upstream vendor's rate limiter has a 6-second window they don't document." |
| `landmine` | Changing A breaks B in a way nothing in the code tells you. | "The CSV export in `reports/` depends on the column order of this SELECT. Reorder it and Finance's reconciliation pipeline silently corrupts." |

## Why now

AI agents write an increasing share of all code. Agents have **zero hallway memory**. They see a weird retry loop and "clean it up." They see a missing cache layer and re-add the library that was removed after a data-corruption incident. They retry, across thousands of sessions, the exact approaches that already failed — because the repository only records positive space.

Humans at least had tribal knowledge. Agents have none. And as agents author more of the code, the negative knowledge stops even entering human memory — it evaporates entirely.

The flip side: agents also solve the historically fatal flaw of every knowledge-capture system — **authorship cost**. Nobody writes documentation after a failure. But an agent that just tried an approach and abandoned it can write a `deadend` scar in milliseconds, for free, at the moment of maximum context.

**Agents created the urgency. Agents remove the adoption barrier. That's the wedge.**

## How it works

```
.scars/
├── 0001-redis-sessions.deadend.md
├── 0002-vendor-retry-window.fence.md
└── 0003-csv-column-order.landmine.md
```

- Scars are small structured Markdown files with YAML frontmatter, tracked in git, reviewed in PRs like code.
- Each scar is **anchored** to code via paths, symbol names, and content fingerprints — not line numbers — so anchors survive refactors.
- Enforcement happens **at the moment of action**:
  - `scar check <path>` — CLI gate for humans and CI
  - Agent hook (Claude Code `PreToolUse`, etc.) — injects relevant scars into the agent's context *before* it edits the file
  - `scar mcp` — local MCP server, so MCP-capable agents can query and draft scars
- `scar harvest` — mines git history (reverts, add-then-remove dependencies, reopened issues) to propose candidate scars for codebases starting from zero.
- Scars are **advisory, never blocking, by default** — and stale knowledge has a lifecycle: `scar challenge <id> --reason` disputes a scar (it still fires, marked as disputed), `scar archive <id> --reason` retires it (never fires again; `scar why` keeps the history), and `scar lint`/`scar status` surface any scar whose `review_after` date has passed. Nothing expires automatically — archiving is a human decision, same governance as promotion.

## Install

```bash
uv tool install scar-cli   # or: pipx install scar-cli
```

Zero runtime dependencies. Python ≥3.10.

## Quickstart

```bash
cd your-repo
scar init                  # creates .scars/ with template + README

# write your first scar
cp .scars/template.md .scars/candidates/redis-sessions.md
$EDITOR .scars/candidates/redis-sessions.md

scar lint                  # validate format
scar promote redis-sessions.md   # human review gate: candidate -> active

# from then on
scar check src/auth/       # what's anchored here?
scar why src/auth/         # full history of pain for this path
scar harvest               # mine git history for candidate scars
```

Wiring the Claude Code hook (auto-injects scars before any agent edit):

```bash
scar hook install
scar hook status
```

Hooks are advisory and are installed only by this explicit user command. To
stop all automatic injection and drafting while keeping the repository's
`.scars/` records:

```bash
scar hook uninstall
```

Wiring MCP-capable agents:

```bash
scar agent doctor
scar agent config opencode   # or: codex, cursor, windsurf
```

The MCP server runs as:

```bash
scar mcp
```

It exposes `scar_query`, `scar_why`, and `scar_draft`. Drafting writes only to
`.scars/candidates/`; active enforcement still requires human promotion.

## Quality discipline

- **Candidates vs active:** agents and `scar harvest` only ever write to `.scars/candidates/`. A human promotes (`scar promote`) — nothing enters active enforcement without review.
- **Expiry conditions:** every scar can declare when it stops being true ("valid until sessions are re-derivable"). Stale knowledge is a bug, not a feature.
- **Validated in use:** in a 14-day agent auto-authorship trial, agents drafted 13 keepable scars across 3 repos with 0% false positives — including one that caught a real parser bug in this repo and fired on the exact edit that fixed it.

## Read more

- [IDEA.md](IDEA.md) — the full pitch: problem, solution, why this, why now, why me
- [SPEC.md](SPEC.md) — scar format, anchoring model, CLI surface, agent integration
- [STRESS-TEST.md](STRESS-TEST.md) — adversarial analysis: failure modes, loopholes, objections, premortem
- [ROADMAP.md](ROADMAP.md) — phased plan from prototype to product

## Status & expectations

**Working software, shared as-is.** CLI v0 is shipped: 9 subcommands, 63 tests, zero dependencies, CI-enforced. It runs daily across the author's repos (where it has already caught real bugs — see `.scars/` in this very repo for live examples).

This is personal infrastructure published as a gift to the OSS community, not a product. Issues and PRs are welcome and read with interest, but there is no support SLA and no roadmap promise. If it's useful to you, that's the whole point.

## License

MIT
