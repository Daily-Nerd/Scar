---
id: 0
type: landmine
title: sanitize_signal_text is display cleaning, not a filename rule; slugify must not call it
severity: medium
confidence: 0.8
created: 2026-11-06
authors: ["claude-code"]
anchors:
  - symbol: src/scar/store.py::slugify
  - path: src/scar/store.py
violation: "(?<!def )sanitize_signal_text\("
evidence:
  - issue: 365
  - issue: 159
  - issue: 352
expires:
  condition: "slugify and sanitize_signal_text stop sharing any code, or harvested titles stop being cleaned"
  review_after: 2027-11-06
status: candidate
---

`sanitize_signal_text` (#159) cleans mined git text for a human to read, so it
deletes `_`, `*` and backticks (markdown emphasis markers). #356 made `slugify`
call it, and every filename writer inherited that: `my_file_name` became
`myfilename`, and accented letters were dropped by the `[a-z0-9]` filter
instead of transliterated (#365).

A filename needs the opposite rule: those characters separate words. Only the
link-target and bare-URL removal is shared, via `_strip_link_targets`. Do not
route `slugify` back through `sanitize_signal_text` to "reuse the cleaner".

Known sibling left alone on purpose: harvested titles still lose `_` inside
words (`retry_count` becomes `retrycount`). That is display text, not a path.
