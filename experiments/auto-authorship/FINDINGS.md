# Gate 0.4 — Running Findings

Live observations during the trial window (opened 2026-06-09). Tally lives here; verdict at window close.

## Tally (updated 2026-06-11)

| Metric | Count | Bar |
|--------|-------|-----|
| Drafter firings (real) | 4 | — |
| Drafter-produced candidates kept after review | 3 (daimon 0002, daimon 0003, fabcap 0002) | ≥5 |
| Self-logged false triggers | 2 (fp-log: error-counting noise) | <15% of firings — currently 2/4, watch |
| Organic scars (authored outside the drafter flow) | 2 (daimon 0001 user-written, daimon 0004 agent-written) | not gate currency, but the adoption signal |

## Finding 1 — Format drift at birth (2026-06-11, important)

daimon 0004 was authored by an agent **directly into `.scars/`** in plain-markdown
style (headers, no YAML frontmatter). Two failures in one event:

1. **Bypassed the candidate flow** — went straight to active without review.
2. **Unparseable** — the precheck hook requires frontmatter; the scar would
   *never have fired*. Good knowledge, silently dead on arrival.

Repaired by hand (reformatted, verified firing via pipe-test). Product
consequences for v0:

- `scar lint` is not optional — every scar write needs validation.
- The precheck hook should report unparseable `.scars/*.md` files as a
  warning instead of skipping silently (rot must be loud — principle 3).
- The session-notice text needs to state the format contract more forcefully,
  or point at a template file the agent copies.

## Finding 2 — FP triggers are concentrated in error-counting (2026-06-09/10)

Both false triggers came from `tool_errors` counting things that aren't
failures: permission-classifier denials, intentional non-zero exits, shell
quoting noise. The eventual one-shot threshold tune (allowed by protocol)
should exclude permission denials and require error *streaks* on the same
command family, not raw counts. Deferred until ~10 firings.

## Finding 3 — Organic adoption outpaces the instrument (2026-06-10)

Two scars appeared without the drafter prompting anyone (user hand-wrote
daimon 0001; an agent wrote 0004 unprompted after the SessionStart notice).
The convention propagates faster than the enforcement loop — good problem,
but it's exactly how format drift (Finding 1) sneaks in.
