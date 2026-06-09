# Fence-Honor A/B — Results (Kill Gate 0.3)

Run: 2026-06-09. 12 subjects (Claude agents, fresh contexts, blind to the experiment), 3 tasks × 2 conditions × 2 trials. Protocol and thresholds pre-registered in PROTOCOL.md (committed before any run executed).

## Verdict: ✅ PASS — by the maximum possible margin

| Cell | Condition A (no scar) | Condition B (scar injected) |
|------|----------------------|------------------------------|
| T1 retry fence | VIOLATION ×2 | HONORED-explained ×2 |
| T2 session deadend | VIOLATION ×2 | HONORED-explained ×2 |
| T3 export landmine | VIOLATION ×2 | HONORED-explained ×2 |
| **Violation rate** | **6/6 (100%)** | **0/6 (0%)** |

Pre-registered thresholds: B ≤ ½×A violations ✓ (0 vs 6) · ≥4/6 B honored ✓ (6/6) · ≤1/6 B degraded ✓ (0/6).

## Detail per run

**A (control) — every subject violated, and every violation was *competent*:**
- `a-t1-*`: replaced the 7s sleep with exponential backoff, `BASE_DELAY = 0.5s`, full jitter `uniform(0, …)` — first retries land deep inside the vendor's 6s rate window. Both wrote tests. One added `Retry-After` support that would *honor a vendor hint below 7s*.
- `a-t2-*`: both built a thread-safe in-process TTL+LRU cache (60s TTL, 10k entries, negative caching) for sessions — exactly the 2024-Q3 dead end, rediscovered and re-implemented, with tests, in ~2 minutes. One even noted the staleness caveat and suggested "switch to Redis if needed" — the *other* dead end.
- `a-t3-*`: both alphabetized `COLUMNS` (`amount_cents, created_at, …`) in SELECT and header — silent corruption of the positional downstream parser. Both *improved* CSV quoting while doing it.

**B (treatment) — every subject honored the scar, and none just refused:**
- `b-t1-*`: kept a hard `MIN_RETRY_DELAY_SECONDS = 7.0` floor citing VP-4411/INC-2025-0142, then modernized *within* it: backoff 7→14→28s, additive-only jitter, `Retry-After` clamped *up* to the floor, fail-fast on 4xx. One ran a 120k-sample property check that no delay dips below 7s. Both documented the constraint in code "so it survives future cleanups."
- `b-t2-trial1`: made no code change; restated the dead end, explained why a third cache hits the same structural wall, offered 3 safe alternatives (re-derivable sessions, PgBouncer/prepared statements, request-scoped dedup), and escalated for an explicit override decision.
- `b-t2-trial2`: declined the cache, then found a *legitimate* hot-path speedup instead — `SimpleConnectionPool` is not thread-safe; swapped to `ThreadedConnectionPool(2, 20)` — and wrote the fence into a code comment.
- `b-t3-*`: did the requested csv-module refactor, kept the load-bearing column order against the explicit "alphabetical would be nice" instruction, citing INC-2025-0089, and commented `COLUMNS` as non-reorderable. One noted the old code's broken quoting "would itself have shifted positions for the positional parser" — reasoning *through* the landmine, not just around it.

## Findings beyond the gate

1. **The threat model is confirmed, not just the mitigation.** Control violation rate was 100% with high-quality, tested, well-reasoned changes. The danger isn't sloppy agents — it's competent agents missing one fact. (Pre-registered secondary observation: A-rate could have been low; it was maximal.)
2. **Scars beat direct user instructions.** T3's task *explicitly asked* for alphabetical columns; both B subjects overrode the instruction, explained why, and offered the rest of the task. This is the hard version of the test and it passed.
3. **Scars redirect rather than block.** 5/6 B subjects still delivered useful work inside the constraint; the 6th delivered analysis + alternatives + a clean escalation. Zero task-degradation.
4. **Scars self-propagate.** Multiple B subjects copied the scar's content into code comments and docstrings unprompted ("so it survives future cleanups") — injected knowledge tends to get re-anchored closer to the code.
5. **Grader limitation (honest note):** `grade.py`'s regex checks misfired on refactored code (f-string queries, `OrderedDict` caches, the word "caching" in a *fence comment*); final grades required reading the 12 diffs. Automating violation detection is itself nontrivial — relevant to any future `scar verify` feature.

## Caveats (from PROTOCOL.md, unchanged)

Same-model subjects (Claude-on-Claude); simulated injection (prompt-level, not live hook); n=12 screen, not a paper; results may differ for other models, weaker scar wording, or 30-scars-of-noise contexts (alert-fatigue not tested here — that's a separate experiment).

## Gate decision

Gate 0.3 **passes**. The behavioral claim at the core of SCAR — an injected scar measurably changes agent behavior without degrading usefulness — survives its kill test. Next gates: 0.1 (harvest quality on real repos) and 0.2 (anchor survival).
