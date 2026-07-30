# SCAR — Technical Specification (Draft 0.1)

Status: largely shipped as of v0.9. Most of this spec describes working software
(see the reference implementation in `src/scar/`). Treat this document as a
description of what the tool does, not a concept draft. One scope note:
**§5 confidence** is a static, human-authored ranking weight — the decay,
uphold, and `scar confirm` dynamics once sketched here were cut (issue #95)
and deferred until scar volume and confirmation data can calibrate a decay
policy without inventing unvalidated constants.

## 1. The scar file

Scars live in `.scars/` at the repo root, one file per scar:

```
.scars/
├── 0001-redis-sessions.deadend.md
├── 0002-vendor-retry-window.fence.md
├── 0003-csv-column-order.landmine.md
└── archive/
    └── 0000-old-resolved-scar.deadend.md
```

Naming: `{seq}-{slug}.{type}.md`. Sequence gives stable short IDs (`scar challenge 1`); slug gives humans a clue; type is in the name so `rg --files` alone tells you the shape of a repo's pain.

### Format

YAML frontmatter + Markdown body. Chosen over pure YAML/JSON because the body is prose for humans *and* injectable context for agents — it must read well.

```markdown
---
id: 1
type: deadend            # deadend | fence | landmine
title: Redis for session storage
severity: high           # low | medium | high | critical
confidence: 0.9          # 0..1, static human-authored ranking weight
created: 2024-03-12
authors: [mara@example.com, "claude-code"]
anchors:
  - path: services/auth/sessions.py
  - symbol: SessionStore        # language-aware symbol anchor
  - pattern: "redis|aioredis"   # approach anchor: fires on matching *new* code anywhere
evidence:
  - pr: 1482
  - incident: INC-2024-0231
  - commit: a3f9c21
expires:
  condition: "sessions become re-derivable from auth tokens"
  review_after: 2027-03-12      # forces a freshness check even without the condition
status: active           # active | challenged | archived | orphaned
---

We ran Redis-backed sessions for 6 weeks in 2024-Q1. Under memory pressure
Redis evicted live sessions, logging users out mid-checkout (INC-2024-0231,
~$40k attributed loss). Root cause: sessions are not re-derivable, so eviction
is data loss, not cache miss.

Do not reintroduce Redis (or any evicting store) for sessions unless sessions
become stateless/re-derivable. Postgres-backed sessions are intentional.
```

### Type semantics

- **`deadend`** — protects against *re-attempting an approach*. Primary anchor is often a `pattern` (the approach reappearing anywhere), not a location.
- **`fence`** — protects *existing code from being changed*. Primary anchors are path + symbol + fingerprint of the protected region.
- **`landmine`** — encodes *non-obvious coupling*: anchors on the trigger site, body names the blast radius (as prose and, ideally, a second `- path:` anchor on the coupled site).

## 2. Anchoring model

The hard technical problem. Line numbers are dead on arrival; file paths die on renames; the system must degrade loudly, never silently.

Four anchor classes, used in combination:

1. **Path anchors** — file or directory globs. Cheap, survive content change, die on rename (mitigated by git rename tracking during re-anchor).
2. **Symbol anchors** — function/class/method names resolved via tree-sitter. Survive moves within and across files in the same repo. Primary anchor class for fences.
3. **Pattern anchors** — regex/AST patterns over *new* code (diff-scoped, not whole-repo). The only anchor class that can catch a dead end being re-attempted in a brand-new file. Powers `deadend` enforcement.
4. **Command anchors** (#175) — regexes over a *shell command about to execute* (`PreToolUse:Bash` and `scar inject --command`). The only anchor class with a firing surface for run-a-command mistakes — knowledge like "bare `uv sync` strips extras" has no edit to anchor to. Never matched against paths or content (structurally immune to self-match/partial-rot) and exempt from content liveness; `review_after` is the freshness mechanism.

Plus a **content fingerprint** (normalized-token hash of the protected region) used not for matching but for *drift detection*: fingerprint drift is an advisory warning surfaced by `scar orphan` and `scar lint`. The `orphaned` transition itself is driven by the location anchors — a scar orphans when all of its path/pattern anchors go dead — and surfaces in `scar status` and CI as "this knowledge has come loose — re-anchor or archive." Orphaned ≠ deleted, ever.

## 3. CLI surface (v0)

```
scar init                 # create .scars/ with a seeded example candidate; installs nothing (hooks are explicit: scar hook install)
scar check <path|diff>    # scars relevant to a path or staged diff; exit code for CI
scar why <path>           # human-readable history of pain for a file/dir
scar challenge <id>       # open a challenge: contest staleness with evidence
scar harvest              # mine git history, emit candidate scars to .scars/candidates/
scar status               # active/orphaned/challenged/expiring counts; repo health
scar inject --path <p>    # machine mode: top-k scars for one edit as hook JSON
scar inject --diff <d>    # machine mode: top-k scars for a unified diff as hook JSON
scar mcp                  # stdio MCP server for MCP-capable agents
scar agent config <name>  # print setup snippets for supported agent runtimes
```

An interactive `scar add` authoring command was specced but intentionally never
built: copying `.scars/template.md` into `candidates/` plus `scar promote`
covers authoring without a bespoke command (see ROADMAP Phase 1). Shipped
extras not shown above: `lint`, `promote`, `orphan`, `hook`, `skill`.

## 4. Agent integration

### 4.1 Claude Code hook (reference implementation)

`PreToolUse` on `Edit|Write|MultiEdit|NotebookEdit`:

1. Resolve target path(s) from tool input.
2. `scar inject --path <target> --top-k 3` → ranked scars (severity × confidence × anchor match strength).
3. Emit as `additionalContext`. Advisory: never blocks the tool call.

Also `PostToolUse`/stop-hook prompt: *"You appear to have abandoned approach X after error Y — author a `deadend` candidate?"* — the auto-authorship loop.

### 4.2 MCP server

`scar mcp` exposes: `scar_query(paths|content|diff)`, `scar_why(path)`, `scar_draft(type, title, body, anchors, evidence)` (writes to `candidates/`, never directly to active). Intended for any MCP-capable agent — Codex, Cursor, Windsurf, opencode, custom. Stdio transport is newline-delimited JSON per the MCP spec ([#162](https://github.com/Daily-Nerd/Scar/issues/162), fixed).

### 4.3 Ranking and the fatigue budget

Hard rule: **max 3 scars injected per edit, max ~120 words each.** A scar system that warns constantly is a scar system that gets uninstalled. Ranking: `severity_weight × confidence × anchor_specificity`, sorted descending (`confidence` is the static authored weight — see §5). Everything else is reachable via `scar why` but not pushed.

## 5. Lifecycle

```
candidate ──promote──▶ active ──challenge──▶ challenged
    │                    │                        └────────▶ archived (+tombstone note)
 discard              anchors drift
                         ▼
                      orphaned ──re-anchor──▶ active
                         └──expire/review──▶ archived
```

- Confidence is a static, human-authored ranking weight (0..1); the tool does not currently mutate it. Dynamic confidence — decay toward a floor without confirmations, an uphold bump on a survived challenge, and an explicit `scar confirm <id>` — is deferred until scar volume and confirmation data can calibrate a decay policy without unvalidated constants (issue #95).
- `review_after` forces periodic human contact with old scars; CI can warn on overdue reviews.
- Archive keeps everything: SCAR's own history is negative knowledge.

## 6. Harvest heuristics (v0)

| Signal | Candidate type |
|---|---|
| `revert` commits / `git revert` parents | deadend |
| Dependency added then removed within N months (lockfile diff walk) | deadend |
| Issue reopened ≥2 times | landmine or deadend |
| File with high churn followed by long stability + "fix"-dense messages | fence |
| Comment archaeology: `DO NOT`, `HACK`, `don't remove`, `load-bearing` | fence |

All harvest output is `candidates/`, never active. Precision over recall: a harvest that produces 50 junk candidates kills trust on day one.

**Ranking is calibrated for code-heavy repos (out-of-regime on GitOps).** The signal type-prior — `revert > deleted > flapping > comment` — assumes reverts and component removals mark genuine deadends. That holds on application/library repos, where a revert is a real retreat and a deletion is a real retirement. It **inverts** on flap-heavy GitOps/config repos, where reverts and removals are routine deployment churn (an `A → B → A` config flap returns to origin, so it was never load-bearing; an app "death" is often a temporary teardown). Labeled measurement bears this out: positive lift across `precision@{5,10,20,30}` on a code-heavy repo, negative lift through `precision@20` on a GitOps repo. Treat harvest *ranking* on GitOps repos as unreliable — candidates are still surfaced, but the order carries little signal. A single prior cannot fit both regimes; repo-class-aware scoring is deferred until enough labeled repos exist per class to tune it without overfitting.

## 7. Non-goals (v0)

- No hosted service, no accounts, no telemetry. A file format and a binary.
- No blocking enforcement by default (CI may opt in to `--strict` for `critical` scars).
- No cross-repo graph (that's the eventual paid layer, and it's premature).
- No attempt to auto-detect fences from code alone — humans and agents author; harvest only *proposes*.

## 8. Open questions

1. Anchor resolution quality across languages — tree-sitter coverage is good, but symbol semantics differ; how bad is the long tail?
2. **RESOLVED.** Pattern anchors ship as regex, diff-scoped (tested against edited paths and *new* content). AST patterns are deferred; regex-first was accepted and the false-positive rate is managed by the ReDoS/lint gate.
3. **RESOLVED.** Monorepos use a single root `.scars/` with path scoping (nearest-ancestor discovery), not `.scars/` per package.
4. **RESOLVED.** No pre-ranked binary index is needed: a live stdlib parse of `.scars/` ranks under the hook latency budget. Revisit only if a repo's scar count ever pushes past it.
5. **OPEN** (tracked). Authorship trust: agent-authored scars marked `authors: ["claude-code"]` — should they start at a lower *static* confidence than human-authored ones? This no longer depends on the (now-cut) §5 dynamics; it could ship as a simple authored-default policy. Deferred with the rest of confidence tuning (issue #95).
