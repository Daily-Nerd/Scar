---
id: 18
type: landmine
title: cli.py subparsers register via _add(), not add_parser — and a test's drift gate parses that exact call shape
severity: medium
confidence: 0.8
created: 2026-08-29
authors: ["claude-code", "Kibukx"]
anchors:
  - path: src/scar/cli.py
  - path: tests/test_json_contract.py
  - pattern: "_add\(\s*sub,"
evidence:
  - pr: 257
  - issue: 255
  - issue: 259
expires:
  condition: "subcommand registration stops going through a wrapper, or the drift gate stops parsing source text"
  review_after: 2027-08-29
status: active
---

Every subcommand is registered through the local `_add()` wrapper (which wires
RichHelpFormatter), so `add_parser` appears only twice in cli.py — inside that
helper's own definition. Grepping for `add_parser` to enumerate the CLI surface
returns nothing useful and reads as "no subcommands here". Search for
`_add(sub, "name"` instead.

The coupling: `tests/test_json_contract.py::test_spec_documents_every_json_subcommand`
parses cli.py as TEXT with that regex to prove SPEC §9.3 documents every
`--json` command. Renaming `_add`, changing its argument order, or building
parsers in a loop makes the regex match nothing — the test then compares an
empty set against a populated SPEC table and fails with a confusing message
that points at the spec rather than at the refactor. If you change the
registration shape, update that helper in the same commit (PR #257).
