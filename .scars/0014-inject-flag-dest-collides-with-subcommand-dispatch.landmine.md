---
id: 14
type: landmine
title: An argparse flag whose dest is "command" silently overwrites the subcommand dispatch key
severity: medium
confidence: 0.9
created: 2026-07-30
authors: ["claude-code", "Kibukx"]
anchors:
  - path: src/scar/cli.py
evidence:
  - issue: 175
  - note: adding `inject --command` with default dest crashed EVERY inject test with KeyError at the dispatch table — argparse lets a flag's dest shadow the subparser dest with no warning
  - note: archived 2026-07-30: dispatch moved to set_defaults(func=...) (#180) — the args.command collision class is structurally dead
expires:
  condition: "CLI dispatch stops keying on args.command (e.g. set_defaults(func=...) pattern)"
  review_after: 2027-01-30
status: archived
---

`main()` dispatches subcommands via a dict keyed on `args.command` (the
subparser `dest`). argparse happily lets any later flag reuse that dest —
`p.add_argument("--command")` on a subparser overwrites `args.command` with
the flag value (or `None` when absent), so EVERY invocation of that
subcommand then fails at dispatch with `KeyError`, including invocations
that never pass the flag.

When adding a flag conceptually named "command", "target", or similar to any
subparser, set an explicit non-colliding `dest` (e.g.
`dest="shell_command"`) and read it via that name.
