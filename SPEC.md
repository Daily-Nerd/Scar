# SCAR — Technical Specification (Draft 0.1)

Status: concept draft. Everything here is falsifiable and expected to change under contact with a prototype.

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
confidence: 0.9          # 0..1, decays per policy, raised by surviving challenges
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
- **`landmine`** — encodes *non-obvious coupling*: anchors on the trigger site, body names the blast radius. May have two anchor sets (`touch:` / `breaks:`).

## 2. Anchoring model

The hard technical problem. Line numbers are dead on arrival; file paths die on renames; the system must degrade loudly, never silently.

Three anchor classes, used in combination:

1. **Path anchors** — file or directory globs. Cheap, survive content change, die on rename (mitigated by git rename tracking during re-anchor).
2. **Symbol anchors** — function/class/method names resolved via tree-sitter. Survive moves within and across files in the same repo. Primary anchor class for fences.
3. **Pattern anchors** — regex/AST patterns over *new* code (diff-scoped, not whole-repo). The only anchor class that can catch a dead end being re-attempted in a brand-new file. Powers `deadend` enforcement.

Plus a **content fingerprint** (normalized-token hash of the protected region) used not for matching but for *drift detection*: when the fingerprint no longer matches anything near the anchors, the scar transitions to `orphaned`, which surfaces in `scar status` and CI as "this knowledge has come loose — re-anchor or archive." Orphaned ≠ deleted, ever.

## 3. CLI surface (v0)

```
scar init                 # create .scars/, install git hooks, detect agent runtimes
scar add                  # interactive authoring; --type, --from-commit, --from-pr
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

## 4. Agent integration

### 4.1 Claude Code hook (reference implementation)

`PreToolUse` on `Edit|Write|MultiEdit|NotebookEdit`:

1. Resolve target path(s) from tool input.
2. `scar inject --paths <targets> --topk 3` → ranked scars (severity × confidence × anchor match strength).
3. Emit as `additionalContext`. Advisory: never blocks the tool call.

Also `PostToolUse`/stop-hook prompt: *"You appear to have abandoned approach X after error Y — author a `deadend` candidate?"* — the auto-authorship loop.

### 4.2 MCP server

`scar mcp` exposes: `scar_query(paths|content|diff)`, `scar_why(path)`, `scar_draft(type, title, body, anchors, evidence)` (writes to `candidates/`, never directly to active). Works for any MCP-capable agent — Codex, Cursor, Windsurf, opencode, custom.

### 4.3 Ranking and the fatigue budget

Hard rule: **max 3 scars injected per edit, max ~120 words each.** A scar system that warns constantly is a scar system that gets uninstalled. Ranking: `severity_weight × confidence × anchor_specificity`, ties broken by recency of last confirmation. Everything else is reachable via `scar why` but not pushed.

## 5. Lifecycle

```
candidate ──confirm──▶ active ──challenge──▶ challenged ──▶ upheld (confidence↑)
    │                    │                        └────────▶ archived (+tombstone note)
 discard              anchors drift
                         ▼
                      orphaned ──re-anchor──▶ active
                         └──expire/review──▶ archived
```

- Confidence decays toward a floor over time without confirmations; surviving a challenge or being explicitly confirmed (`scar confirm <id>`) raises it.
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

## 7. Non-goals (v0)

- No hosted service, no accounts, no telemetry. A file format and a binary.
- No blocking enforcement by default (CI may opt in to `--strict` for `critical` scars).
- No cross-repo graph (that's the eventual paid layer, and it's premature).
- No attempt to auto-detect fences from code alone — humans and agents author; harvest only *proposes*.

## 8. Open questions

1. Anchor resolution quality across languages — tree-sitter coverage is good, but symbol semantics differ; how bad is the long tail?
2. Pattern anchors: regex is brittle, AST patterns are per-language work. Ship regex first and eat the false positives?
3. Monorepos: `.scars/` per package vs root with path scoping?
4. Should `scar inject` ship pre-ranked digests to stay under hook latency budgets (<150ms)? Likely: maintain a binary index under `.git/scar-index`, rebuilt incrementally.
5. Authorship trust: agent-authored scars marked `authors: ["claude-code"]` — should they start at lower confidence until human-confirmed? (Probably yes.)
