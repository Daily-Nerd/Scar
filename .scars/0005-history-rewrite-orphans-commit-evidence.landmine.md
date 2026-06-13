---
id: 5
type: landmine
title: rewriting git history orphans commit-SHA evidence in scars — receipts break in fresh clones
severity: medium
confidence: 0.9
created: 2026-06-11
authors: ["claude-code", "Kibukx"]
anchors:
  - path: .scars/
evidence:
  - note: v0.1.0 public release (2026-06-11): fresh-start force-push orphaned 3 commit SHAs cited by scars 0001 and 0002; SHAs still resolve on GitHub by URL but fail in any fresh clone, and GitHub may GC them eventually
expires:
  condition: "evidence schema gains a resolvable form (full URL or archived diff) or lint warns on bare SHAs at promotion"
  review_after: 2027-06-11
status: active
---

Scars cite commit SHAs as evidence receipts. Those receipts implicitly assume
the SHA stays reachable in the repo's history forever. Any history rewrite —
fresh-start orphan branch, force-push, filter-repo scrub, or a routine squash-/rebase-merge — silently breaks
that assumption: the scar still lints clean and still fires (anchors are
paths/patterns, not commits), but `git show <sha>` fails in every fresh clone,
so the receipt is unverifiable exactly where strangers would check it.

Observed at the v0.1.0 public release: the fresh-start force-push orphaned
5c63b14, faad8f6, and bcd3864, cited by scars 0001 and 0002. Nobody noticed
until after the push because nothing in the toolchain connects "history
operation" to "evidence integrity."

The everyday trigger is the merge strategy itself: this repo squash-merges, so a
feature-branch commit — exactly the SHA you cite while drafting a scar mid-PR —
is orphaned the moment that PR lands. Rebase-merge does the same; only a true
merge-commit preserves branch SHAs. So PREFER `pr:`/`issue:` evidence (it
resolves on GitHub regardless of merge strategy) or a SHA already on the default
branch, and avoid citing transient feature-branch SHAs at all.

Before any deliberate history rewrite: grep `.scars/` for `commit:` evidence and
either (a) amend with a note explaining the rewrite, (b) replace bare SHAs with
full GitHub commit URLs (at GC's mercy), or (c) inline the fact so the scar is
self-contained. `scar lint` now warns when a cited SHA is unreachable from HEAD
(#43) — but it fires after the fact; the durable fix is not citing branch SHAs.
