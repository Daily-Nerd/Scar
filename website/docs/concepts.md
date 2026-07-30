---
sidebar_position: 3
title: Concepts
description: The three primitives, the anchoring model, violation tripwires, and the knowledge lifecycle.
---

# Concepts

Git records what your codebase **is**. Nothing records what it **refused to be**. SCAR is version control for that negative space: the dead ends, the load-bearing weirdness, the invisible tripwires — captured as files, anchored to code, and surfaced at the exact moment someone (human or AI agent) is about to step on them.

## The three primitives

| Type | Meaning | Example |
|------|---------|---------|
| `deadend` | We tried X. It failed because Y. Don't retry unless Z changes. | "We tried Redis for session storage. Eviction under memory pressure logged users out mid-checkout. Don't retry unless sessions become re-derivable." |
| `fence` | This code looks wrong. It is intentional. Here's why. | "Yes, this retry loop sleeps 7 seconds, not 5. The upstream vendor's rate limiter has a 6-second window they don't document." |
| `landmine` | Changing A breaks B in a way nothing in the code tells you. | "The CSV export depends on the column order of this SELECT. Reorder it and the reconciliation pipeline silently corrupts." |

Type semantics drive anchoring: a `deadend` protects against *re-attempting an approach* (primary anchor is usually a pattern), a `fence` protects *existing code from change* (path + symbol), a `landmine` encodes *non-obvious coupling* (anchor on the trigger site, body names the blast radius).

## The scar file

Scars are small structured Markdown files with YAML frontmatter, tracked in git, reviewed in PRs like code:

```
.scars/
├── 0001-redis-sessions.deadend.md
├── 0002-vendor-retry-window.fence.md
├── 0003-csv-column-order.landmine.md
├── candidates/          # drafts awaiting human promotion
└── archive/             # retired knowledge — kept forever
```

Naming is `{seq}-{slug}.{type}.md`, so a plain file listing already tells you the shape of a repo's pain.

## Anchors

Line numbers are dead on arrival; file paths die on renames. SCAR uses three anchor classes in combination, designed to degrade loudly, never silently:

1. **Path anchors** — file or directory globs. Cheap, survive content change, die on rename (mitigated by git rename tracking during re-anchor).
2. **Symbol anchors** — function/class names resolved via tree-sitter. Survive moves within and across files. Primary class for fences. Measured survival across refactors: 94.6% / 92.5% on the shipped API.
3. **Pattern anchors** — regexes over *new* code (diff-scoped, not whole-repo). The only class that catches a dead end being re-attempted in a brand-new file.

A **content fingerprint** of the protected region powers drift detection: fingerprint drift is an advisory warning, and a scar whose location anchors all go dead becomes `orphaned` — loud in `scar status` and CI, never silently dropped.

Anchor breadth is a measurement concern, not just noise: a scar anchored on whole directories fires on nearly every edit and inflates any firing-derived metric. See [Measurement methodology](./methodology.md).

## Violation tripwires

A scar may declare a machine-checkable tripwire:

```yaml
violation: "redis|aioredis"
```

The pre-edit hook injects the scar; the post-edit check runs the regex against the diff the agent just produced. Fired-then-violated is a logged, countable event — the basis of the [compliance instrument](./methodology.md). Two authoring rules matter:

- **Double-quote the regex.** Single-quoted `violation:` values keep the quotes as literal regex characters and never match — a silently dead tripwire.
- Only machine-checkable scars get `violation:`; prose dead ends stay advisory.

## Injection and the fatigue budget

Hard rule: **max 3 scars injected per edit, max ~120 words each**, ranked by `severity × confidence × anchor specificity`. Path-only matches render as one-line hints; the full body is injected only when content or a symbol actually trips the pattern, and a body already shown for the same file within 4 hours collapses to a one-liner. A scar system that warns constantly is a scar system that gets uninstalled.

## Lifecycle

```
candidate ──promote──▶ active ──challenge──▶ challenged
    │                    │                        └────────▶ archived (+tombstone note)
 discard              anchors drift
                         ▼
                      orphaned ──re-anchor──▶ active
                         └──expire/review──▶ archived
```

- **Promotion is a human gate.** Agents and `scar harvest` write only to `candidates/`; a human promotes.
- **Nothing expires automatically.** `review_after` forces periodic freshness checks; `challenge` disputes with evidence; `archive` retires with a tombstone. The archive keeps everything — SCAR's own history is negative knowledge.
- `confidence` is a static, human-authored ranking weight. Automatic decay was deliberately cut rather than shipping uncalibrated constants.

## Harvest

`scar harvest` mines git history for candidate scars: revert commits, dependencies added-then-removed, issues reopened repeatedly, churn-then-stability files, and comment archaeology (`DO NOT`, `load-bearing`). All output lands in `candidates/` — precision over recall, because 50 junk candidates kill trust on day one. Field data so far: hand-authoring during sessions produces most active scars; harvest ranking is calibrated for code-heavy repos and is explicitly unreliable on flap-heavy GitOps repos.
