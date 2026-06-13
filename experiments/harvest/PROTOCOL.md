# Experiment: Harvest Ranking + Label Instrument (Issue #38)

**Question.** Can a cheap, explainable heuristic score RANK harvested candidates so the human curator reads the real scars first — without normalizing away the precision signal carried by candidate type?

**Why it matters.** Raw harvest precision sits at ~13% on real history. If a curator must read every candidate in arbitrary order, the tool costs more attention than it saves. Ranking earns its place only if the top-N is denser in real scars than the tail. This experiment builds the *instrument* (labels + precision@N) so that claim becomes measurable instead of asserted.

## What the ranker does

Each candidate gets a deterministic `score` (see `src/scar/harvest.py`). The score is a sum of calibration priors — base weight per signal type plus small bonuses (PR/issue ref on reverts, files-deleted threshold, oscillation count, comment specificity, recency). All weights are **priors, unvalidated until labels exist**; this experiment is how they get validated.

**Cross-section ranking uses RAW score, no normalization** (`scar harvest --top-k N`). The per-type base constants order `comment < flapping < deleted_component < revert`. That ordering is an intentional precision prior: signal *type* predicts precision, so a revert outranks a grep hit by design. Normalizing scores across types would erase exactly the signal we want to exploit. If the labels later show the ordering is wrong, fix the base constants — do not add normalization.

## Label JSONL format

Path: `experiments/harvest/labels.jsonl` (committed — instrument/data, like the anchor-survival replay). Written one line at a time by:

```
scar harvest <repo> --label <id> keep|discard [--note "..."]
```

Each line is one JSON object:

| Field   | Type   | Meaning |
|---------|--------|---------|
| `id`    | string | the candidate's stable id (see below) |
| `label` | string | **exactly** `"keep"` or `"discard"` — nothing else is accepted |
| `note`  | string | free-text rationale (may be empty) |
| `date`  | string | `YYYY-MM-DD`, from `time.strftime` (monkeypatchable in tests) |
| `repo`  | string | the harvested repo's name (provenance) |

**Only `keep`/`discard` are valid.** The CLI rejects any other label value with a non-zero exit and writes nothing. This is load-bearing: `precision_at_n` reads `label == "keep"` and counts everything labeled as the denominator — a third value (`"maybe"`, `"skip"`) would silently corrupt precision by inflating the denominator without ever counting toward the numerator.

**Id validation.** `--label` runs `harvest(repo)` for the target repo, collects every candidate id, and **rejects an id not in that set** (mirrors `scar orphan --apply` rejecting an unknown `--id`). You cannot label a candidate that the current harvest does not produce.

## Candidate-id stability rule

`harvest.candidate_id(signal_type, candidate)` = first 10 hex of `sha1(signal_type + identifying-fields)`. The id is a hash of the **identifying fields only — NOT the score, NOT the id itself**, so the same candidate gets the same id across runs and a re-scored candidate keeps its label.

Identifying fields per type:

| Type                | Hashed fields |
|---------------------|---------------|
| `revert`            | `commit` |
| `deleted_component` | `component` |
| `flapping`          | `file` + `key` |
| `comment`           | `location` + `text[:40]` |

**Comment ids use `text[:40]`** — the first 40 characters of the comment text. Keep those 40 chars stable: editing the tail of a long comment preserves the id; editing the start changes it (and orphans any prior label). This deliberately tolerates the 120-char display truncation in `_comment_archaeology` without making the id depend on it.

## Precision@N

`harvest.precision_at_n(ranked, labels, n)`:
- `ranked` — candidates pre-sorted by score descending (caller's responsibility; `scar harvest --top-k` produces this order).
- `labels` — a `{id: "keep"|"discard"}` dict built from the JSONL (group by id; last write wins if a candidate was labeled twice).
- Take the first `n`. Among them, consider **only** candidates whose id is in `labels`. Return the fraction of that labeled subset where `label == "keep"`.

**Contract: unlabeled candidates in the top-N are excluded from BOTH numerator and denominator.** They neither help nor hurt the score — precision@N measures "of the ones we judged in the top-N, how many were real". If no candidate in the top-N is labeled, the result is `0.0` (not NaN, not an error).

## Method (to run once labels accrue)

1. Harvest a real repo; curate the top-N by hand, recording `keep`/`discard` via `--label`.
2. Build `{id: label}` from `labels.jsonl`.
3. Compute `precision_at_n` at several N (e.g. 5, 10, 20) and compare against the ~13% raw base rate.
4. Compare per-type precision to validate (or refute) the base-constant ordering.

## Pre-registered claim

- **Ranking earns its place** if precision@N for small N is materially above the ~13% raw base rate — i.e. the top of the ranked list is denser in real scars than the unranked pool.
- If precision@N ≈ base rate at every N, the score adds no signal and the constants need rework (or the heuristic is the wrong instrument).

## Limitations (declared)

1. Weights are hand-set priors, not fit to data — this instrument exists to replace the guess with a measurement, but until ~50 labels accrue the ranking is an assertion.
2. Single-curator labels carry that curator's bias; `keep`/`discard` is a coarse binary over what is really a confidence gradient.
3. `precision_at_n` ignores recall — a candidate the harvester never surfaced cannot be labeled, so a missed real scar is invisible here.
4. Recency scoring reads the wall clock at harvest time; the same candidate scored months apart can shift rank (id stays stable, so labels still attach correctly).
