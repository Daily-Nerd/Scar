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

## Documentation

**Docs site: <https://daily-nerd.github.io/Scar/>** — [quickstart](https://daily-nerd.github.io/Scar/quickstart), [concepts](https://daily-nerd.github.io/Scar/concepts), [measurement methodology](https://daily-nerd.github.io/Scar/methodology), [agent integration](https://daily-nerd.github.io/Scar/agents), [changelog](https://daily-nerd.github.io/Scar/changelog).

AI agents: a machine-readable index lives at [llms.txt](https://daily-nerd.github.io/Scar/llms.txt).

In one paragraph: scars are small YAML+Markdown files in `.scars/`, tracked in git and reviewed in PRs, **anchored** to code via paths, tree-sitter symbols, and diff-scoped patterns — not line numbers. A pre-edit hook injects relevant scars into an agent's context at the moment of action; an optional `violation:` regex is a post-edit tripwire that makes compliance measurable. Advisory, never blocking, by default; agents and `scar harvest` write only candidates, a human promotes.

## Install

```bash
uv tool install scar-cli   # or: pipx install scar-cli
```

Symbol anchors (function/class names that survive file moves) need the
tree-sitter extra; without it they silently don't resolve and only `scar lint`
warns:

```bash
uv tool install "scar-cli[symbols]"
```

The parser and agent hook hot-path are stdlib-only; the human-facing CLI adds
`rich` + `rich-argparse` for formatted output. Python ≥3.10.

## Quickstart

```bash
cd your-repo
scar init                        # creates .scars/ with template + seeded example
cp .scars/template.md .scars/candidates/redis-sessions.md
$EDITOR .scars/candidates/redis-sessions.md
scar lint                        # validate format
scar promote redis-sessions.md   # human review gate: candidate -> active
scar hook install                # Claude Code: inject scars before agent edits
scar skill install               # Claude Code: authoring skill into ~/.claude/skills/
scar hook install --runtime windsurf   # Windsurf/Cascade: block-once injection
```

Claude Code users: the marketplace **plugin** ships the hooks and the
scar-authoring skill together; the two commands above are the manual fallback.
Re-run `scar skill install` after upgrading — the installed skill is a static copy.

Full walkthrough, lifecycle commands, and agent wiring (Claude Code plugin,
Windsurf/Cascade hooks, MCP server, `scar draft-check` for every other runtime):
[quickstart](https://daily-nerd.github.io/Scar/quickstart) ·
[agent integration](https://daily-nerd.github.io/Scar/agents).

## CI / pre-commit

SCAR ships a `.pre-commit-hooks.yaml` (repo root) for one-line adoption via
[pre-commit](https://pre-commit.com/):

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/Daily-Nerd/Scar
    rev: v0.11.0  # pin a release-please tag; see the repo's releases
    hooks:
      - id: scar-lint    # validates every scar; fails on orphan-detected
      - id: scar-check   # blocks a commit that touches anchored code (#106 gate)
```

Both hooks assume `.scars/` already exists (`scar init`) and `language: python`
installs this package into pre-commit's own isolated venv — no separate
`scar` install required on the machine running the hook.

SCAR also ships a composite GitHub Action (`action.yml`, repo root) for
CI without pre-commit:

```yaml
# .github/workflows/scar.yml
- uses: Daily-Nerd/Scar@main   # or pin a tag, e.g. @v0.11.0 (release-please)
  with:
    fail-orphans: 'true'   # default; set 'false' to only fail on parse errors
    version: ''             # empty = latest scar-cli from PyPI; or pin e.g. '0.11.0'
    args: ''                # extra args appended to `scar lint`
```

`@main` always tracks the latest commit; releases are tagged `vX.Y.Z` via
release-please, so pin a tag for reproducible CI.

## Quality discipline

- **Candidates vs active:** agents and `scar harvest` only ever write to `.scars/candidates/`. A human promotes (`scar promote`) — nothing enters active enforcement without review.
- **Expiry conditions:** every scar can declare when it stops being true ("valid until sessions are re-derivable"). Stale knowledge is a bug, not a feature.
- **Validated in use, honestly:** a 14-day agent auto-authorship trial ([protocol + findings](experiments/auto-authorship/)) kept 3 of the drafter's candidates after human review — including one that caught a real parser bug in this repo and fired on the exact edit that fixed it. The trigger heuristic also fired falsely on 2 of its 4 firings, which is why it was retuned to revert-language-only. Candidates that reached review were kept at a high rate; the trigger needed work. Both numbers are in the findings file.

## Read more

- [Docs site](https://daily-nerd.github.io/Scar/) — quickstart, concepts, measurement methodology, agent integration
- [IDEA.md](IDEA.md) — the full pitch: problem, solution, why this, why now, why me
- [SPEC.md](SPEC.md) — scar format, anchoring model, CLI surface, agent integration
- [STRESS-TEST.md](STRESS-TEST.md) — adversarial analysis: failure modes, loopholes, objections, premortem
- [ROADMAP.md](ROADMAP.md) — phased plan from prototype to product

## Status & expectations

**Working software, shared as-is.** CLI v0 is shipped: 19 subcommands, 528 tests, stdlib-only parser/hot-path, CI-enforced. It runs daily across the author's repos (where it has already caught real bugs — see `.scars/` in this very repo for live examples).

This is personal infrastructure published as a gift to the OSS community, not a product. Issues and PRs are welcome and read with interest, but there is no support SLA and no roadmap promise. If it's useful to you, that's the whole point.

## License

MIT
