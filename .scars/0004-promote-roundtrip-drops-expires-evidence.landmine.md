---
id: 4
type: landmine
title: scar promote silently drops expires (always) and evidence (when notes contain quotes) — parser cannot read what to_text writes
severity: high
confidence: 0.95
created: 2026-06-11
authors: ["claude-code", "Kibukx"]
anchors:
  - path: src/scar/model.py
  - pattern: "_field\(front"
evidence:
  - note: Observed 2026-06-11 promoting 5 candidates in context-as-program (PR 3 there restores the data by hand). All 5 lost their expires block; one also lost evidence because its note contained escaped quotes.
status: active
---

promote() does parse -> mutate -> to_text. Two asymmetries make that lossy.

First: _field() matches keys at line start only (regex anchored ^condition:,
^review_after: with re.MULTILINE), but those keys are NESTED under expires: and
indented two spaces — including in to_text()'s own output. The parser can never
read what the serializer writes, so expires_condition and review_after parse as
empty and to_text omits the whole expires block. Every promotion loses it; so
does any future parse-rewrite cycle.

Second: the evidence regex captures ([^\"\n]+?) with an optional closing quote
at end of line. A note whose text contains an inner double quote terminates the
capture early, fails the tail match, and the entire evidence entry vanishes —
turning a receipted scar into one that lint flags as challengeable on sight.

Fix direction: allow leading whitespace in _field for nested keys (or parse the
expires block explicitly), make the evidence capture quote-tolerant, and add a
round-trip property test so parse(to_text(scar)) == scar gates future changes.
Until then, diff frontmatter after every promote.
