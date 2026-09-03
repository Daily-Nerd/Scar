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
scar brief --compact      # paste-ready scar block for sub-agent launch prompts
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
- `review_after_firings` is the count-based sibling of `review_after`: once a scar has fired N times *since the last commit that touched its file*, `scar status` and `scar lint` escalate it from an ambient warning to an explicit review flag. Revising the scar re-affirms it and restarts the count; archiving it silences the flag because archived scars do not fire. Absent ⇒ per-type default (`landmine` 10, `fence` 15, `deadend` disabled); `0` ⇒ never escalate. Advisory by default — `scar lint --fail-firing-review` opts into a non-zero exit. Escalation reports only; it never archives or challenges a scar (ADR-4 keeps lifecycle transitions human).
- When the reset point cannot be determined (untracked scar file, or no git history), the count reported is a **lifetime** count and says so. A since-revision count and a lifetime count are different quantities and must never share one label.
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

## 9. Machine-readable output contract

Nine subcommands accept `--json`: `lint`, `status`, `check`, `why`, `stats`,
`gc`, `orphan`, `reanchor`, `draft-check`. Two more are machine-mode by
design: `scar inject` (hook JSON or silence) and `scar brief --compact`.

Everything in this section is a **promise to external consumers**. It exists
because a caller that parses this output — an agent runtime, another tool, a
sibling project probing `scar` as an optional external binary — needs to know
what it may rely on and what may move under it.

### 9.1 Compatibility policy

- **Guaranteed keys** (§9.3) are present on every successful `--json` run of
  that subcommand, with the stated type. Removing one, renaming one, or
  changing its type is a **breaking change**.
- **New keys may appear in any release.** Consumers MUST ignore unknown keys.
  Adding a key is *not* breaking, so do not assert on an exact key set.
- **List order is not guaranteed** unless stated. `stats.per_scar` is the one
  exception: it is sorted by descending `count`, then ascending `id`.
- **`--json` implies machine mode.** It is never Rich-rendered and never
  TTY-gated. Human-facing stdout is a separate, unpromised surface — parsing
  it is unsupported and it *will* change.
- **Unsupported surfaces.** The `.scars/*.md` files, the raw
  `firing-log.jsonl`, and the marker files under machine state are internal.
  They have changed shape and will again. Consume the CLI, not the store.

### 9.2 `null` means refused, not zero

Two `stats` fields are nullable, and the distinction is load-bearing:

| Field | `null` when | Why |
|---|---|---|
| `injection_rate` | zero-hit passes are not being logged | Without `SCAR_LOG_ZERO_HITS=1` the log contains only edits that *matched*, so firings/edits is 100% by construction — a flattering number measuring nothing (#217). |
| `retrieval_misses` | `instrument_disconnected` is `true` | Every violation in such a window has no prior firing *by construction*, so the count measures a broken install, not retrieval (#235, #237). |

In both cases `null` is a deliberate refusal to report an undefined quantity.

> A consumer that coerces `null` to `0` publishes a confident false number.
> This is not hypothetical: it is the exact failure that produced a retrieval
> figure this project had to publicly retract. **Propagate the refusal.**

Related: `retrieval_misses` is a **floor, not a rate** even when non-null —
misses on scars carrying no `violation:` pattern are not observable from the
firing log at all. Never present it as a percentage.

`instrument_disconnected: true` reports an *observed impossibility* (violations
recorded with zero edits observed). `false` is not a clean bill of health — an
empty log is silent, not healthy.

### 9.3 Guaranteed keys

Top-level keys, and the keys of each object inside the listed arrays.

| Command | Guaranteed top-level | Array item keys |
|---|---|---|
| `lint` | `files` int, `findings` array, `failed` int, `orphans`, `partial_rot`, `symbol_drift`, `revivals`, `reverse_hints`, `unreachable_evidence` arrays, `shallow_clone` bool | `findings[]`: `file`, `level`, `message` |
| `status` | `scars_dir` str, `active`, `challenged`, `candidates`, `review_due`, `orphan_detected`, `orphaned`, `partial_rot`, `broken` arrays, `counts` object | `active[]`: `id`, `type`, `severity`, `title`. `candidates[]` are strings. `counts`: `active`, `candidates`, `orphan_detected`, `orphaned`, `partial_rot`, `broken` |
| `check` | `paths` array of str, `scars` array | `scars[]`: `id`, `type`, `severity`, `confidence`, `status`, `title`, `body` |
| `why` | `path` str, `records` array | `records[]`: `id`, `type`, `status`, `title`, `file`, `body` |
| `stats` | `repo` str, `total_firings` int, `per_scar` array, `most_fired` int, `last_fired` str, `never_fired` array of int, `demotions` int, `demotions_path_only` int, `demotions_cooldown` int, `demotions_reason_unknown` int, `census_known` int, `census_unknown` int, `cofires_per_edit` float **or null**, `edits_multi_fire` int, `path_only_ratio` float **or null**, `edits_observed` int, `injection_rate` float **or null**, `retrieval_misses` int **or null**, `instrument_disconnected` bool, `posttool_silent` bool, `last_fired_age_days` int, `armed_firings` int, `armed_unknown` int, `verdicts_expected` int, `verdicts_observed` int, `verdicts_unresolved` int, `verdicts_unplaceable` int, `firings_block_capable` int, `firings_advisory` int, `firings_block_unknown` int, `all_firings_advisory` bool, `firings_context_known` int, `firings_context_unknown` int, `advisories` array. `window` object **only when `--since`/`--until` is given** | `per_scar[]`: `id`, `count`, `violations`. `window`: `since`, `until`, `excluded_undated` |
| `gc` | `removed_markers` int, `dropped_firings` int, `dry_run` bool, `candidates` array, `fp_log` object | `candidates[]`: `name`, `age_days`. `fp_log`: `present`, `size`, `lines` |
| `orphan` | `orphan_detected`, `partial_rot` arrays | — |
| `reanchor` | `proposals`, `pattern_diagnostics` arrays | — |
| `draft-check` | `triggered` bool, `revert_language`, `revert_commits`, `reset_hard`, `churn` ints. `message` str **only when `triggered` is true** | — |

`gc` mutates state. A consumer that only wants to observe MUST pass
`--dry-run`; `dry_run` echoes back which mode ran.

`stats.armed_firings` counts firings on scars that carried a `violation:`
tripwire at the moment they fired; `armed_unknown` counts firings on log rows
written before that was recorded. A scar with no tripwire can never be recorded
as violated, so `armed_firings` is the honest denominator for a compliance
ratio — `total_firings` includes events incapable of failing.

`armed_unknown` is never folded into either side. Armedness is a property of
the past, and today's `.scars/` cannot answer it: a scar armed last week was
not armed last month, so inferring it would rewrite history in the flattering
direction. Consumers must treat it as unplaceable, not as zero.

**Arming dates are not on the row, and will not be.** For every row written
by 0.21.0 or later, `armed_ids` records armedness at the moment of firing,
which is strictly more accurate than gating on an arming date: a scar that was
armed, disarmed and re-armed is placed correctly by `armed_ids` and wrongly by
any single date. A date field would add nothing for those rows. For rows
written before 0.21.0 it would add nothing either, because they are never
backfilled. That makes the gap permanent and bounded: any armed-window number
computed over rows that predate `armed_ids` is correct only if the arming
dates were reconstructed from git history, and it is not reproducible from
the log alone. The procedure is documented beside the one published number
that depends on it (the methodology page), and `armed_unknown` names exactly
the rows it applies to.

### 9.4 Both halves of the pipeline are watched

`instrument_disconnected` reports the **precheck** half dying: violations
recorded with no precheck rows, so no scar could have fired.

`posttool_silent` reports the **posttool** half dying: armed scars fired, and
no posttool verdict was ever recorded. This is the more dangerous direction,
because it produces zero violations against a full firing count, which reads
as perfect compliance. Before it existed the two were byte-identical.

The posttool hook records a verdict on a **clean** edit as well as a violating
one, whenever a violation was possible on that path. That is what makes silence
meaningful: absence of a verdict is `verdicts_unresolved`, never a pass.

Every runtime does this: Claude Code, Codex and Windsurf alike. A host that
recorded only violations would report its own healthy install as a dead hook,
because its precheck rows raise an expectation nothing there ever resolves. A
host that edits several files in one tool call owes one verdict row per path,
not per call.

Both flags are one-directional, exactly like the rest of this section. They can
show an instrument is broken. Neither certifies one healthy: observing a verdict
withdraws the alarm and claims nothing further.

`verdicts_unplaceable` counts armed firings that can be neither resolved nor
called unresolved, because the row predates the mechanism or carries no
`edit_id` to join on. Same rule as `armed_unknown`: never folded into either
side.

Being armed is not the same as being resolvable. `armed_ids` says a verdict was
*owed*; a separate marker on the precheck row says the writer could also
*produce* one. Every row written before the mechanism existed is armed and
permanently unresolvable, so gating on armedness alone would make a working
install report a broken one the moment it upgraded.

> An armed firing with no verdict means **UNKNOWN**. A consumer that reads it
> as compliance republishes the exact failure this section exists to prevent.

### 9.5 Capability travels with the claim

Zero violations on a host that could refuse an edit, and zero violations where
refusing was never possible, are the same number and different evidence.
`block_capable` is recorded on each firing row so the two can be told apart.

It is **written at firing time, never inferred from `runtime` at read time.**
Inference bakes today's host behavior into the reader, so a host that gains or
loses a blocking hook would silently rewrite the meaning of every historical
row. Capability is also per-firing rather than per-runtime: a host may refuse
on a content-signal match and merely surface a path-proximity match.

- `firings_block_capable` — the firing could have refused the action.
- `firings_advisory` — it could not; injection was the only channel.
- `firings_block_unknown` — the row predates the field. Not advisory. Reading
  it as "could not block" understates a host that could.
- `all_firings_advisory` — true only when every firing in the window is known
  advisory. One unknown row makes the universal claim unsayable, and the field
  is then `false`, meaning *cannot say* rather than *some firing could block*.

These three count **scar-firings**, the same unit as `total_firings`, and sum
to it. They are printed beside it, and counting log rows instead would put two
units in one sentence and read as though the remainder were known.

The writer's default is advisory. An adapter that forgets to declare capability
therefore understates the claim, never inflates it.

> `0 violations` in an all-advisory window measures what the agent did. It says
> nothing about what it was prevented from doing, because nothing could prevent
> anything.

### 9.6 Context size is recorded, not yet interpreted

`context_bytes` on a firing row is the size of the host's transcript file at
the moment of the firing. The unit is **bytes, not tokens**: it is a proxy for
how much conversation was already in play, and it is the only such signal a
supported host hands us.

The key is **omitted** when the host supplies nothing. Not zero, which would
claim the context was empty, and not `null`, which a careless consumer coerces
to zero anyway.

`firings_context_known` and `firings_context_unknown` report the population
rate, in scar-firings, summing to `total_firings`. They exist because whether a
given host supplies a transcript path on the injection hook is not knowable in
advance, and a field that silently never populates is a blind column rather
than a measurement. Check the population rate before drawing any conclusion
from the values.

Nothing reads `context_bytes` to make a decision, and no claim is derived from
it here. The analysis that will read it is pre-registered separately and is
gated on a real distribution existing first: buckets chosen after seeing a
result are how a result gets laundered into a finding.

`stats --since` / `--until` filter records **before** any metric is computed,
so every number is window-scoped by the same code that computes it unwindowed.
Timestamps in the firing log are **naive local time** (`time.strftime`), and the
bounds are interpreted in that same frame — a window is therefore local-time,
never UTC. A bare date means `00:00:00` as `--since` and the END of that day as
`--until`, so `--since D --until D` is the whole of day D. Records whose `ts` is
missing, non-string, unparseable, or not comparable to the bounds are excluded
and reported in `window.excluded_undated` — a windowed total that silently
dropped rows would look cleaner than the data is.

`draft-check` emits **no output at all** — not even `{}` — when the working
directory is not a usable git repo. A consumer must treat empty stdout as "no
verdict", never as a parse error. When it does emit, `message` is present only
on `triggered: true`; there is no message to carry otherwise.

`reanchor --json` is **propose-only**. `--apply` is human-review-only, never
CI, and never emits JSON.

### 9.7 A demotion says why

A scar rendered as a one-liner was demoted for one of two reasons, and they are
opposite evidence. A **path-only** demotion means the anchor proved the file
was in scope and nothing in the edit matched: a near miss. A **cooldown**
demotion means the edit content matched (a pattern hit, a symbol resolved, a
command matched) and the full body was withheld only because it had been shown
in the last four hours: a hit we chose not to repeat.

`demoted_ids` lists both in one field. `demotion_reasons` records which is
which, keyed by `str(scar_id)` because JSON keys are strings, with the values
`path-only` and `cooldown`. Nothing injected changes; this is recording only.

The key is always written. `{}` means nothing on the row was demoted. A
**missing** key means the row predates the field, and every demotion on such a
row counts as `demotions_reason_unknown`. It is never read as path-only, which
is the flattering reading for a precision measure.

- `demotions_path_only`, `demotions_cooldown`, `demotions_reason_unknown` sum
  to `demotions`.

> A repo whose demotions are mostly cooldown suppressions of strong matches
> and one whose demotions are mostly path-only noise have different precision
> stories. Before this field they reported identically.

### 9.8 The census is taken before the cut

`count` on a firing row is the injected list, and the injected list is capped
at `top_k` (default 3). It has been `min(matched, top_k)` for as long as the
cap has existed, so the number of scars that actually applied to one edit was
never recoverable from the log.

`matched` records what `_match_target` returned before `_select_top` truncated
it: `{"total": n, "content": c, "path_only": p}` with `content + path_only ==
total`. `content` matched the edit itself (a pattern hit, a symbol resolved, a
command matched). `path_only` matched nothing but the file's location. The
split is the same one the fatigue budget uses to tier, counted before it cuts.

The key is written by every shipped writer (edit, command, Codex, Cascade). It
is **omitted** by a writer that did not count, never zeroed: zeros would claim
the edit was observed and matched nothing.

- `census_known` / `census_unknown` count **firing rows** (one edit on one
  file), not scar-firings. A row without `matched` is unknown, never a 1.
- `cofires_per_edit` is the mean of `matched.total` over known rows. It is the
  leading indicator for the compliance cliff: two anchored lessons applying to
  one edit and pulling in different directions. If it sits near 1 almost
  always, a clean violation rate is partly structural rather than earned.
- `edits_multi_fire` counts known rows with `matched.total >= 2`.
- `path_only_ratio` is `sum(path_only) / sum(total)` over known rows. It is a
  **proxy** for the false-anchor rate and is labelled as one wherever it
  renders. The true rate needs ground truth about whether a scar applied,
  which nothing here produces.

Both ratios are `null`, not `0`, when no row carries a census. A zero would
read as "no co-fires" and "no path-only noise", both flattering.

### 9.4 Enforcement

`tests/test_json_contract.py` asserts every guaranteed key above against live
command output, and asserts the nullable semantics of §9.2. A guarantee no
test enforces is an untestable assertion, and untestable assertions are the
ones that reach `main` wrong.
