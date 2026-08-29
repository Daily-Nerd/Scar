Closes #

<!--
CI gates this PR (.github/workflows/pr-validation.yml). All of these are checked
BEFORE review, so getting them right up front saves a round-trip:

1. The body links an issue: `Closes #N` / `Fixes #N` / `Resolves #N`.
   "Refs #N" does NOT satisfy this check — the regex only accepts those three
   verbs. If a PR is one stage of a multi-PR issue and you don't want merging it
   to close the issue, say so in the body and reopen the issue after merge.

2. That linked issue carries the `status:approved` label. A maintainer adds it.
   If it isn't there yet, the check fails — wait for approval rather than
   opening the PR early.

3. Exactly ONE `type:*` label on the PR. Note the names, they are not the same
   as the conventional-commit types:
       type:feature   type:fix   type:docs   type:refactor   type:chore
   ("type:feat" does not exist and will not apply.)

4. Conventional title: `type(scope): description`. Squash-merge makes this the
   commit subject on main, so release-please reads THIS, not your local commit
   messages. A breaking change needs the `!` in the title (`feat!:`,
   `fix(scope)!:`) — a local commit footer does not survive the squash.

5. Branch name matches `type/description`, lowercase, `[a-z0-9._-]` only:
       feat fix chore docs style refactor perf test build ci revert
   Pick it from the KIND of change, not the content area — `research/foo` fails.
   A PR's head branch cannot be retargeted, so a wrong name means closing and
   reopening the PR. Worse, renaming a branch mid-flight can resurrect the old
   ref. Get the name right before the first push.

Two exemptions, both automatic:
  - Scars-only PRs (every changed file under `.scars/`) skip gates 1 and 2 —
    human sign-off already happened at `scar promote`. Gates 3-5 still apply.
  - release-please branches skip the whole job.

Seeing NO checks at all, rather than failing ones? That is not a broken
workflow. Pull requests from forks and from bots wait on a maintainer to
approve the Actions run, and until someone clicks, zero check-runs is the
expected state. Nothing on your side to fix — say so in a comment if it sits.
-->

## What

## Why

## Tests

<!--
What you ran and what it proves. New behavior needs a test that failed before
the change.

Run before pushing — CI runs all three:

    uv run pytest
    uv run scar lint          # a gate pytest does NOT cover: dead anchors, rot, ReDoS
    uv run ruff check src/scar tests

If you touched a `--json` payload, re-read `SPEC.md` §9 first. Those keys are a
published contract: adding is fine, removing or retyping a documented key is
breaking, and `tests/test_json_contract.py` will tell you which.
-->

## Checklist

- [ ] Linked issue above has `status:approved`
- [ ] Exactly one `type:*` label applied
- [ ] `uv run pytest`, `uv run scar lint` and `ruff` pass locally
- [ ] If an approach was tried and abandoned here, a candidate scar was written to `.scars/candidates/`
