---
id: 32
type: landmine
title: Scar files are read in one place; a direct read_text on a scar path brings back the crash
severity: medium
confidence: 0.8
created: 2026-11-21
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: git-config-interactive
promoted_from: candidates/scar-files-have-one-reader.md
anchors:
  - path: src/scar/store.py
  - pattern: "parse_scar_text\(\w+\.read_text"
evidence:
  - issue: 439
  - url: https://github.com/Daily-Nerd/Scar/issues/439
expires:
  condition: "scar files stop being read as UTF-8 text, or the reader moves out of store.py"
  review_after: 2027-05-21
violation: "parse_scar_text\(\w+\.read_text"
status: active
---

store.read_scar_text and store.read_scar are the only code that opens a scar
file. They turn bytes that are not valid UTF-8 into a ParseError, so such a
file is listed as an unparseable scar (status, lint, the hook notice) like one
with no frontmatter. Calling Path.read_text on a scar path instead raises
UnicodeDecodeError, which no caller of the old per-site reads caught: lint and
status ended in a traceback, and the hooks, which catch everything, silently
stopped every scar from firing because of one file.

The same reader feeds the signing path: ScarStore.seen_receipts refuses when any
scar file cannot be read, because a file the store cannot parse has no receipt it
can list for the checker's `seen` argument.

Read a scar through read_scar (parse) or read_scar_text (text; pass
keep_newlines=True when the file will be rewritten and CRLF must survive).
tests/test_scar_reader.py fails if a new read_text or read_bytes appears in
src/scar outside its short allowlist of non-scar files. See
https://github.com/Daily-Nerd/Scar/issues/439.
