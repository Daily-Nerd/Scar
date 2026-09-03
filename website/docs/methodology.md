---
sidebar_position: 4
title: Measurement methodology
description: The fired→violated compliance instrument — pre-registered thresholds, the window result, and the four measurement errors caught on the way.
---

# Measurement methodology

Every AI memory tool claims agents "remember." SCAR ships an **instrument** for the harder question: when a recorded lesson fires into an agent's context, does the agent do the forbidden thing anyway? That event — fired, then violated — is logged and countable.

This page is the permanent record of how the number is produced, what the first pre-registered window measured, and every measurement error we caught before (and after) publishing.

## The instrument

- SCAR fires **pre-edit**: a `PreToolUse` hook injects negative knowledge anchored to the code being touched, before the agent edits it.
- A scar may declare an optional `violation: "<regex>"` — a **post-edit tripwire** (`PostToolUse` / `scar check --diff`) that runs against the diff the agent just produced.
- The firing log records both events, giving a **fired→violated rate per scar**.

Honesty is carved into the design:

- Only machine-checkable scars carry `violation:`; prose dead ends stay advisory-only.
- The hook cannot observe *obedience* — only disobedience. A violation count of zero is a floor on detected violations, not proof of compliance.
- Firing is advisory and never blocks the edit, so the measurement does not alter the behavior it measures beyond the injection itself.

## Pre-registered decision rule

Set on 2026-07-03, **before** any number existed. Let **F** = firings on violation-armed scars in the window, **V** = violation records.

| Condition | Outcome |
|---|---|
| F ≥ 20 and V/F ≤ 0.2 | Branch A — publish as headline |
| F ≥ 20 and V/F > 0.2 | Branch B — publish as honest postmortem with a fix plan |
| F < 20 | Branch C — publish the instrument only; refuse a compliance claim on thin data |

Committing to thresholds before seeing the number is part of the method. Changing them afterward would invalidate the result.

## Window result (2026-07-03 → 2026-07-16)

Two repositories dogfooded the instrument: SCAR's own repo and a sibling CLI project. Counting rule: a firing counts toward F only if the scar was violation-armed **at the moment it fired** — per-scar arming timestamps taken from git history, because one repo armed its scars mid-window. Two independent counts (a script and a separate shell pipeline) agreed exactly; the smaller repo's records were verified line-by-line by hand.

| Slice | F | V | V/F |
|---|---|---|---|
| Primary window (Jul 16 inclusive) | **737** | **0** | **0.00** |
| Strict window (Jul 16 exclusive) | 724 | 0 | 0.00 |
| Excluding the one over-broad scar (whole-repo anchors, 716 of 737 fires) | **21** | 0 | 0.00 |

**Outcome: Branch A.** The robustness cut is the load-bearing row: one scar with directory-wide anchors contributed 97% of raw F, and amputating it entirely still clears the pre-registered F ≥ 20 bar. The headline number survives the harshest cut, not the most flattering one.

### What is reproducible from the log, and what is not

The F above is correct, and it is **not reproducible from the firing log alone**. This is a permanent, known limitation, stated here because it has been stated publicly.

The window rows predate `armed_ids`. At the time, a firing row recorded which scars fired and how many, but not whether each one carried a `violation:` tripwire at that moment. The armed-only denominator therefore came from git: for each scar file, the date of the first commit that added a `violation:` line, then a gate on every row's timestamp against that date.

```bash
# per scar file: the oldest commit that changed the count of `violation:` lines
# is the arming commit. No --diff-filter=A: most scars are armed by a later
# edit, not at creation, and that filter returns nothing for them.
git log --format='%ad' --date=short -S'violation:' -- .scars/0001-*.md | tail -1
# then count firing rows for that scar with ts on or after that date
```

Two independent counts (a script and a separate shell pipeline) agreed exactly. An auditor who wants to check 737 has to redo that step; `scar stats` on the archived rows will report them as `armed_unknown`, which is the correct output.

Two things follow.

- **The gap is bounded.** Every row written by 0.21.0 or later carries `armed_ids`, recorded live by the hook. For those rows the armed denominator reads straight off the log, and `armed_ids` is more accurate than a date could be: a scar that was armed, disarmed and re-armed is placed correctly per firing, and wrongly by any single date. Recording dates would add nothing.
- **The gap is not repaired.** Rows from before the field are not backfilled from today's `.scars/`. A scar armed last week was not armed last month, and guessing would rewrite history in exactly the flattering direction this page exists to refuse.

One earlier public statement said the log records ids and counts but not armedness. That was true of the window rows and is no longer true of current rows.

Post-window disclosure: two violation records were logged **after** the window closed (Jul 27 and Jul 29), both from one scar tripping on files that *describe* the forbidden pattern — the same self-referential class as measurement error №4 below. They are outside the window and disclosed here regardless.

## Five times the numbers tried to lie

Every metric we published or nearly published contained a measurement error we caught ourselves. An instrument that has survived its own audits is more credible than a bigger number from a tool that never checked.

1. **The 94.8% that described unshipped code.** An anchor-survival figure came from a prototype; the shipped mechanism didn't exist yet. Flagged in a maturity audit, symbol anchors were shipped, and the number was re-measured on the real API: 94.6% / 92.5%, reconciled publicly.
2. **The stats double-count.** Scar IDs are per-repo sequential integers; `scar stats` summed them across repos, reporting 54 firings that were really ~31 + ~32 in two different repos. Fixed with repo scoping (#137).
3. **The test contamination.** 92 of 105 firing-log records were pytest tmpdir artifacts — the test suite was dogfooding into the production log. Isolated via a state-dir fixture; the log was garbage-collected.
4. **The self-match violations.** The first two violation records ever logged were the tool flagging its own documentation: a scar's body quotes the forbidden construct by design. Fixed same day (#148/#149), false records pruned, baseline zeroed 2026-07-03 — which is why the window starts there.
5. **The disconnected instrument.** `scar hook install` wrote the `precheck` hook and then deleted it — two hook kinds share one event, and ownership was matched by event rather than by kind, so the second install stripped the first. Pre-edit injection had been off for a month on every install since command anchors, while `posttool` kept recording violations and `hook status` reported everything installed. Every violation in that period therefore had no prior firing *by construction*, and was counted as a retrieval miss: a "retrieval floor" of 14 of 16 that measured the broken install, not retrieval. Found before publication, the finding was withdrawn, and the fix (#236) shipped with the detector that would have caught it in a day (#237). **The window below predates the break and is unaffected** — it ran entirely inside the period when `precheck` still worked.

## Caveats

- Obedience is unobservable from inside the hook; only violations are observable.
- The headline V/F is an **enforcement** number: of the scars that were surfaced, how many were ignored. It says nothing about scars that should have been surfaced and were not. `scar stats` reports that second failure mode separately as a retrieval floor — violations with no prior firing on that target — and it is a **lower bound, not a rate**: a retrieval miss on a scar carrying no `violation:` pattern leaves no trace in the log at all (#207).
- That floor becomes a **rate** only if `SCAR_LOG_ZERO_HITS=1` is set. The hook then records the edits it saw and matched nothing, which is the denominator "of edits the hook observed, how often was a scar injected". It is off by default: one log line per edit is real write volume on the hot path. Without it, `scar stats` reports `injection_rate: null` rather than a firings-over-firings number that would be 100% by construction (#217). "Edits the hook observed" counts only rows that record an edit anchor: command-anchor firings are counted separately and rows written before the anchor kind was recorded are reported as unknown, not folded in either way (#294).
- `violation:` coverage is machine-checkable scars only — a minority of active scars (4 of 10 and 5 of 25 in the two window repos).
- n = 1 developer dogfooding across two repos. No claim of generalization; the instrument is open source so you can run it on your own repositories.
- F concentration matters: report the robustness cut alongside the raw total, or the number is theater.

## Run it yourself

```bash
scar init
# arm a machine-checkable scar with a violation: regex, then:
scar stats          # enforcement, retrieval floor, and demotion counts for this repo
SCAR_LOG_ZERO_HITS=1        # opt in to recording no-match edits — earns a retrieval RATE
scar check --diff changes.patch --exit-code   # the same tripwire, CI-side
```
