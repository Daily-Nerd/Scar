---
# COPY THIS FILE — do not edit the template itself.
# New scars: write to .scars/candidates/<slug>.md with status: candidate.
# A human reviewer promotes to .scars/NNNN-<slug>.<type>.md with status: active.
id: 0                      # assigned at promotion (next free NNNN)
type: landmine             # deadend = tried+failed | fence = looks wrong, intentional | landmine = touching A breaks B
title: Read commands' non-tty branch must bypass Rich — Rich wraps to 80 cols and breaks path-substring tests
severity: high
confidence: 0.9
created: 2026-06-30
authors: ["claude-code"]
anchors:
  - path: src/scar/cli.py
  - path: src/scar/output.py
  - pattern: "output\\.render\\("
evidence:
  - issue: 78
  - note: "187-test suite asserts plain substrings like '0001-bad.deadend.md' and long anchor paths on main([...]) under capsys"
expires:
  condition: "the test suite stops asserting on plain stdout substrings (e.g. moves to asserting structured --json only)"
  review_after: 2027-06-30
status: candidate
---

The five read commands (status, lint, check, why, orphan) route output 3 ways:
--json, Rich (tty), and plain print() (non-tty). The non-tty branch MUST keep
emitting the legacy plain lines byte-for-byte. It looks redundant — "why not
just always render with Rich?" — but it is load-bearing.

Rich's Console wraps and truncates to ~80 columns when it does not detect a wide
terminal. Many existing tests call main([...]) under pytest's capsys (stdout is
NOT a tty) and assert on plain SUBSTRINGS, including long anchor paths like
`src/long_gone/` and filenames like `0001-bad.deadend.md`. If you route the
non-tty branch through Rich, those strings get wrapped/elided and the assertions
fail in subtle, hard-to-trace ways (the value is "there" visually but split
across a wrapped line).

What a future editor must do: keep `output.is_tty()` gating Rich. Only branch 2
(real tty) may call a `_*_rich` renderer. The `plain()` closure in each handler
is the contract for non-tty + CI consumers — change its text only if you also
update the corresponding test substrings. Do NOT collapse the plain branch into
the Rich branch. See issue #78 and src/scar/output.py's module docstring.
