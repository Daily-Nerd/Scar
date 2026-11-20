---
id: 31
type: landmine
title: Terminal output must pass values to rich as data through output.fmt, never inside a markup string
severity: high
confidence: 0.9
created: 2026-11-20
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: git-config-interactive
promoted_from: candidates/rich-values-never-in-markup.md
anchors:
  - path: src/scar/output.py
  - path: src/scar/cli.py
evidence:
  - issue: 414
status: active
---

Rich reads square brackets in any string as style tags. A value put into a
markup string with an f-string loses its brackets: the `[#5]` scar label
vanished from every orphan line, `app/[id]/page.tsx` printed as
`app//page.tsx`, and a title containing `[/]` raised MarkupError and crashed
the command. Only the terminal branch is affected, and it has few tests, so
this stayed hidden.

Escaping per call site does not hold: a trailing backslash or bracket in a
value combines with the literal text after it, so even `rich.markup.escape`
misrenders at the edges.

Build every formatted line with `output.fmt("literal {}", value)` and print it
with `output.say` / `output.show`. A bare string handed to those helpers is data
and prints literally. `tests/test_rich_markup_values.py` fails if `console.print`
is called outside output.py or a dynamic string is handed to a helper.
