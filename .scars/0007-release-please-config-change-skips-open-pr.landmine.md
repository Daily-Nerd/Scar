---
id: 7
type: landmine
title: Changing release-please extra-files config does NOT retro-apply to an already-open release PR
severity: medium
confidence: 0.85
created: 2026-06-26
authors: ["claude-code", "Kibukx"]
anchors:
  - path: release-please-config.json
  - path: .github/workflows/release.yml
evidence:
  - pr: 64
  - pr: 66
  - note: v0.6.1 release cycle, 2026-06-26
expires:
  condition: "release-please gains retro-application of config changes to open release PRs, or the repo stops carrying version in extra files (plugin.json)"
  review_after: 2027-06-26
status: active
---

If a release PR is already open and you then merge a change to `release-please-config.json`
(e.g. adding/altering `extra-files`), release-please will NOT retro-apply the new config to
that open PR — especially when the commit that lands the config is a non-releasable type
(`ci:`, `chore:`), because release-please sees no new releasable commit and leaves the
existing PR untouched.

Observed live: PR #64 (release 0.6.1) was computed from the #63 `fix:` commit BEFORE #65 added
the `extra-files` updater for `plugin/plugin.json`. When #65 (a `ci:` commit) merged,
release-please re-ran but did not re-bump the open PR, so #64 bumped `pyproject.toml` only.
`plugin/plugin.json` stayed behind and the `test_plugin_version_matches_pyproject` drift guard
failed CI — correctly blocking a broken release.

Fix when this happens: close the stale release PR, delete its `release-please--branches--main`
branch, and re-dispatch the Release workflow (`gh workflow run release.yml`). The fresh PR is
built from scratch and applies the current `extra-files` config. Do NOT hand-patch the version
into the open release branch — it leaves the manifest/state half-migrated. The drift guard is
the safety net: it makes this failure loud instead of shipping a mismatched manifest.
