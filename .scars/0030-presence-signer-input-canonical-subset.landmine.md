---
id: 30
type: landmine
title: The presence signer reads a canonical subset of JSON, so a draft with a number, a boolean or default json.dumps whitespace is refused
severity: medium
confidence: 0.8
created: 2026-11-18
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: git-config-interactive
promoted_from: candidates/presence-signer-input-canonical-subset.md
anchors:
  - path: src/scar/presence.py
  - pattern: "expires_in_s"
evidence:
  - issue: 392
  - note: Profile ACT-04 and SG-04 (signer v0.4.0): no numbers, no true/false, no whitespace outside strings; expires_in_s is a decimal string.
expires:
  condition: "the signer accepts general JSON on stdin"
  review_after: 2027-05-18
status: active
---

The signer and the checker are fed through one canonical-subset recognizer.
A JSON number, a `true` or `false`, or the spaces `json.dumps` adds after
separators makes the input `schema_refused` before any prompt. That is why
`presence.EXPIRES_IN_S` is the string `"3600"` and not an int, and why
`presence._canonical` writes compact sorted ASCII.

Changing the window to a number, or building a request with plain
`json.dumps`, still looks fine in a unit test against a fake that parses
loosely. It fails only against the real signer, after the person has been
asked nothing, as `schema_refused`. Keep every value a string, an object or
an array, and keep the fake in tests/presence_fakes.py as strict as the
profile when you add a field.
