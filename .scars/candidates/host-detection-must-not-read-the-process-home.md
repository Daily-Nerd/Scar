---
id: 0
type: deadend
title: Host detection must take home from installer.CLAUDE_DIR, never Path.home() — a bare call passes locally and fails every other machine
severity: high
confidence: 0.9
created: 2026-09-07
authors: ["claude-code"]
anchors:
  - pattern: "detect_hosts\("
  - symbol: src/scar/cli.py::_detect
evidence:
  - issue: 318
  - note: "2026-09-07, during the multi-host skill install work: the skill-install refusal resolved home with Path.home(). Under HOME=empty the suite went 1031 passed / 2 FAILED, both PRE-EXISTING tests, printing 'claude not detected on this machine, nothing written'. Fixed by reusing _detect(). Branch SHAs deliberately omitted: scar #5 fired on the amend that wrote this, and squash-merge would orphan them."
expires:
  condition: "detect_hosts takes home from one injected seam no caller can bypass"
  review_after: 2027-03-08
status: candidate
---

`Path.home()` in a detection path looks obviously right and is a trap. Tests
patch `installer.CLAUDE_DIR`, not the environment, and `_detect()` derives
home from `installer.CLAUDE_DIR.parent`. A bare call reads the author's real
home, finds their real `~/.claude`, and passes. On any machine without one,
every CI runner included, detection returns nothing and the caller refuses.

The failure is invisible where it is introduced: the suite was fully green on
the authoring machine, and the tests that finally failed were pre-existing
ones nobody had touched. Use `_detect(kind)`, or `installer.CLAUDE_DIR.parent`
exactly as `_detect` does. Never add a second answer to where home is.

Two anchors on purpose. `cli.py` is 143 KiB and its first `detect_hosts(` sits
at byte ~123,000, past the 64 KiB `MAX_ANCHOR_SCAN` bound, so the pattern is
blind to the very file the defect landed in. The `symbol:` anchor covers it,
at the cost of needing `scar-cli[symbols]`. Lint reports neither gap: the
pattern matches `hosts.py`, so the scar reads live while blind where it counts.

Not armed. The obvious tripwire is `Path\.home\(\)`, but `cli.py` carries that
string in a warning comment and `installer.py` uses it correctly for module
constants, so it would fire on prose and on correct code.
