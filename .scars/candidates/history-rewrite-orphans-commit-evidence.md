---
type: landmine
title: rewriting git history orphans commit-SHA evidence in scars — receipts break in fresh clones
severity: medium
confidence: 0.9
created: 2026-06-11
authors: ["claude-code"]
anchors:
  - path: .scars/
  - pattern: "push.{0,30}(--force|\\+[a-zA-Z]).{0,40}main|filter-repo|checkout --orphan"
evidence:
  - note: "v0.1.0 public release (2026-06-11): fresh-start force-push orphaned 3 commit SHAs cited by scars 0001 and 0002; SHAs still resolve on GitHub by URL but fail in any fresh clone, and GitHub may GC them eventually"
expires:
  condition: "evidence schema gains a resolvable form (full URL or archived diff) or lint warns on bare SHAs at promotion"
  review_after: 2027-06-11
status: candidate
---

Scars cite commit SHAs as evidence receipts. Those receipts implicitly assume
the SHA stays reachable in the repo's history forever. Any history rewrite —
fresh-start orphan branch, force-push, filter-repo scrub — silently breaks
that assumption: the scar still lints clean and still fires (anchors are
paths/patterns, not commits), but `git show <sha>` fails in every fresh clone,
so the receipt is unverifiable exactly where strangers would check it.

Observed at the v0.1.0 public release: the fresh-start force-push orphaned
5c63b14, faad8f6, and bcd3864, cited by scars 0001 and 0002. Nobody noticed
until after the push because nothing in the toolchain connects "history
operation" to "evidence integrity."

Before any history rewrite in a repo with scars: grep `.scars/` for
`commit:` evidence and either (a) amend those scars with a note explaining the
rewrite, (b) replace bare SHAs with full GitHub commit URLs (survive as
unreachable objects, at GC's mercy), or (c) inline the relevant diff/fact into
a note so the scar is self-contained. Longer term: `scar lint` could warn
when a cited SHA is unreachable from HEAD.
