# Experiment: Auto-Authorship Trial (Kill Gate 0.4)

**Question.** Do scars accumulate from normal agent-assisted work with near-zero human effort, at acceptable quality? This is the skeptic's #1 fatal risk: if agent-authored scars are noise, the "authorship cost goes to ~zero" differentiator collapses.

## Instrument

Two global hooks (installed alongside the existing precheck hook):

1. **SessionStart notice** (`scar-session-notice.py`) — when the session's repo has `.scars/`, injects a one-line convention reminder with live counts ("N active scars, M candidates pending review; author candidates when you abandon approaches"). Closes the discovery gap: agents learn the convention exists, even at zero scars.
2. **Stop-hook drafter** (`scar-stop-drafter.py`) — at session end, scans the transcript for abandonment signals:
   - revert/rollback language in assistant messages
   - repeated user corrections ("didn't work", "broke", "go back", "no funciona")
   - error-dense tool activity + file-edit thrash (same file edited ≥4×)
   When triggered (conservatively — see thresholds in script), it blocks the stop **once** with an instruction: review the session; if an approach was genuinely abandoned, write a ≤15-line candidate to `.scars/candidates/`; if this was a false trigger, append one line to `.scars/candidates/fp-log.txt` instead. Either way, finish.

Design properties: candidates never become active without human review; the drafter fires at most once per session (`stop_hook_active` + marker); false positives are *self-logging* — the fp-log is the measurement instrument.

## Pre-registered thresholds (2-week window of normal work)

- **PASS:** ≥5 candidates the repo owner marks "worth keeping" across instrumented repos, AND false-trigger rate <15% of drafter firings (fp-log entries / total firings), AND no session where the drafter blocked more than once.
- **KILL:** <2 keepable candidates (the engine doesn't compound) OR fp rate >40% (the hook is noise and would be uninstalled).
- Between: tune signal thresholds once, extend one week, re-judge.

## Measurement

- Candidates: `.scars/candidates/*.md` across instrumented repos (homelab-apps, daimon, fabcap, TripWire, Heimdall — any repo with `.scars/`).
- Firings: drafter appends one line per trigger to `~/.claude/scar-state/drafter-log.jsonl` (timestamp, repo, signals, session).
- Review session at trial end: owner marks each candidate keep/discard; fp-log counted.

## Limitations

1. Single user, single agent family (Claude) — same caveat as gate 0.3.
2. Signal heuristics are text-level v0; the experiment measures whether even crude detection clears the bar. Production can only improve on it.
3. Two weeks may undersample deadend-producing work (depends what the fortnight brings); the window extends rather than fails if <3 sessions trigger.
