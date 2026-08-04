---
id: 0
type: fence
title: Cascade's block code is 2, and so is argparse's error code — never exit 2 directly from cascade-hook
severity: high
confidence: 0.9
created: 2026-08-04
authors: ["claude-code"]
anchors:
  - path: src/scar/cascade.py
  - path: src/scar/installer.py
  - pattern: "cascade-hook"
evidence:
  - issue: 197
  - note: ".windsurf/hooks.json is committed and workspace-level, so a teammate on an older scar-cli runs whatever command string we wrote"
expires:
  condition: "Cascade gains a non-blocking context channel (an additionalContext equivalent), so blocking is no longer the injection path at all"
  review_after: 2027-02-04
status: candidate
---

`scar cascade-hook` signals "block this action" with sentinel exit code 20,
and the command wired into `.windsurf/hooks.json` maps 20 to 2. Exiting 2
straight from the handler looks obviously correct against Cascade's docs —
and is a trap.

argparse also exits 2, on any parse error, including an unknown subcommand.
`.windsurf/hooks.json` is workspace-level and committed, so it reaches
teammates whose installed scar-cli may predate this subcommand. Those
installs would exit 2 with a usage message on every single `pre_write_code`,
which Cascade reads as a block: every write in the workspace bounces, with
argparse's usage text as the explanation. Cascade treats any code that is
neither 0 nor 2 as "action proceeds normally", so the sentinel turns that
version skew back into a silent no-op.

Same class as the `2>/dev/null` on the git post-commit hook, and for the same
reason: an installed hook line outlives the binary version that wrote it.
Keep the handler's block code distinct from 2, keep the mapping in
`installer.cascade_command()` (which imports the constant, so the two can
never drift), and do not "simplify" either half.
