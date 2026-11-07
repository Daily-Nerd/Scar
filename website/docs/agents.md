---
sidebar_position: 5
title: Agent integration
description: Native Claude Code and Codex plugin hooks, Windsurf/Cascade hooks, MCP, and the git-native fallback.
---

# Agent integration

SCAR treats AI agents as first-class users: they trigger scars, they read scars, and they author scars (into `candidates/`, behind the human promotion gate).

## Where `scar skill install` writes

`scar skill install` puts the scar-authoring skill at a native path per host. Every
path below comes from that host's own documentation for where it looks for skills,
except Claude Code, which SCAR has actually watched load the file. Nobody has yet
confirmed the other four hosts read the file SCAR writes there. Run
`scar skill status` to see the resolved path and installed/not-installed state for
every host on this machine.

| Host | Destination | Confirmed loading it? |
|---|---|---|
| Claude Code | `~/.claude/skills/scar-authoring/` | Yes, observed in daily use |
| Codex | `$CODEX_HOME/skills/scar-authoring/`, and `~/.codex/skills/scar-authoring/` when that variable is unset | No. File is written and present on disk; nobody has watched Codex load it |
| Cursor | `~/.cursor/skills/scar-authoring/` | No. Path is per vendor documentation only |
| opencode | `~/.config/opencode/skills/scar-authoring/` | No. Path is per vendor documentation only |
| Windsurf | `~/.codeium/windsurf/skills/scar-authoring/` | No. Path is per vendor documentation only |

Windsurf was renamed Devin Desktop in mid-2026, but its on-disk paths still use the
old `windsurf`/`codeium` names, which is why the host key here is still `windsurf`.

`scar skill install --all` writes to every detected host the plugin does not
already serve, including hosts an earlier `scar skill install` already wrote to:
re-running install is the documented step after an upgrade, because the installed
skill is a static copy. `scar
skill install --runtime <host>` targets one. Install refuses to write to a host it
did not detect on the machine, rather than create a skill directory nothing reads.

## Claude Code (reference integration)

**Recommended: install the plugin** from the marketplace — hooks and the scar-authoring skill arrive together.

Manual fallback:

```bash
scar hook install                # detects installed hosts, asks which to wire (--all, --runtime X)
scar skill install               # same detection for the scar-authoring skill
scar hook status                 # every host with the channel that serves it (settings, plugin, none)
```

With no `--runtime`, install detects the hosts on this machine: on a terminal it asks per host, refreshing one already wired through its settings file and skipping one served by the plugin unless `--force`; without a terminal it installs or refreshes only when exactly one such host is detected, otherwise it prints the host table and the exact commands and writes nothing.

How the hook behaves:

- `PreToolUse` on `Edit|Write|MultiEdit|NotebookEdit`: resolves the target path, runs `scar inject --path <target> --top-k 3`, and emits ranked scars as `additionalContext`. Advisory — never blocks the tool call.
- `PreToolUse` on `Bash`: fires command-anchored scars against the shell command about to execute (`scar inject --command "<cmd>"` is the same surface for other runtimes) — the injection path for run-a-command mistakes that edit anchors cannot cover.
- `PostToolUse`: runs any armed `violation:` regex against the edit just made and logs fired→violated events.
- Everything is installed only by explicit command; `scar hook uninstall` removes it cleanly.

After upgrading scar-cli, re-run `scar skill install` — the installed skill is a static copy and does not update itself.

## Codex (native hooks)

```bash
scar hook install --runtime codex
```

Writes `~/.codex/hooks.json` (honours `$CODEX_HOME`) — Codex's user-level hooks
file and the direct analogue of Claude Code's `settings.json`. Other tools
register there too, so Scar **merges** into it and never rewrites it wholesale;
a file that no longer parses is refused rather than clobbered.

Then open `/hooks` in Codex, review the three Scar entries, and trust them.
**This step is not optional.** Codex skips an untrusted hook *silently* — no
error, no stderr — so an untrusted install is indistinguishable from a working
one until you notice nothing ever fires. An upgrade that changes a hook
definition requires review again.

Once trusted:

- `PreToolUse` on `Bash`: fires command-anchored scars before the command runs.
- `PreToolUse` on `apply_patch`: parses every touched path and added line, ranks
  across the whole patch, deduplicates matches, and emits at most three scars
  as advisory `additionalContext`.
- `PostToolUse` on successful `apply_patch`: reports armed `violation:` matches.

Codex exposes edits as one `apply_patch` program, not as Claude Code's
structured tool calls, which is why the matchers are `Bash|apply_patch` rather
than `Edit|Write|MultiEdit`.

The adapter never denies or rewrites a tool call. Malformed payloads, missing
binaries, and internal errors fail open. A session-start notice is the single
visible warning when the hooks are wired but `scar-cli` cannot be resolved.

```bash
scar hook status --runtime codex
```

reports what is in the file. It cannot see Codex's trust record, so an entry
shown as `installed` may still be untrusted — trust state belongs to the host,
and Scar does not pretend it can infer it. `scar agent doctor` verifies the
binary path the hooks route through.

The Scar plugin ships the authoring skill only on the Codex side. `scar hook install --runtime codex` is the supported way to get push delivery.

MCP and direct `scar inject` remain useful pull companions and the fallback for
users who install only `scar-cli`.

## Windsurf / Cascade (block-once injection)

Cascade runs shell commands on lifecycle events, configured per workspace in a committable `.windsurf/hooks.json`:

```bash
scar hook install --runtime windsurf     # run inside the repo
scar hook status --runtime windsurf
```

This wires `pre_write_code` and `pre_run_command` (injection) plus `post_write_code` (the `violation:` tripwire) to `scar cascade-hook`. An existing `hooks.json` is **merged**, never overwritten — your team's own hooks keep their place and run first.

**Why blocking.** Cascade has no `additionalContext` equivalent: on exit 0 stdout reaches the Cascade UI only, never the model. The one channel into the agent's context is a blocking exit, which surfaces stderr and cancels the pending action. So a firing scar bounces the action **once**:

1. The agent is about to write code (or run a command) that an armed scar anchors. The action is cancelled and the scar arrives on stderr, compact — label line plus the one rule that matters.
2. The agent retries the identical action. This time it goes straight through, informed.

Exactly one bounced action per firing, scoped to the Cascade conversation. Retries are never blocked twice; the state expires on its own, since Cascade gives no session-end signal to clean up on.

**Precision bar is deliberately higher here** than in Claude Code. A false firing there spends tokens; here it cancels something the user can see. So only content-signal matches block — the pattern hit the pending edit, the symbol resolved, or the command *is* the recorded mistake. Path-proximity matches print a one-liner to the UI for the human and never block anything.

Firings and violations land in the same log `scar stats` reads, tagged with the runtime that produced them.

**Known no-op:** Cascade does not load hooks while a workspace is open in Restricted Mode. Install still succeeds; the hooks simply never run there.

For pull access as well, Windsurf speaks MCP — see below.

## MCP server (any MCP-capable agent)

```bash
scar mcp
```

A dependency-free stdio server speaking newline-delimited JSON per the MCP spec. It exposes:

| Tool | What it does |
|---|---|
| `scar_query` | Ranked scars for paths, content, or a unified diff |
| `scar_why` | Full scar history anchored to a path |
| `scar_draft` | Write a candidate scar — only ever to `.scars/candidates/` |

Config snippets for supported hosts:

```bash
scar agent doctor
scar agent config codex      # or: cursor, windsurf, opencode
```

## Every other runtime: git-native

No hook API? `scar draft-check` runs from a plain git hook and nudges *any* agent (or human) toward authoring, driven entirely by git evidence — revert language in commit messages, actual `git revert`/`reset --hard` history, churn:

```bash
scar hook install --git      # writes .git/hooks/post-commit
```

Throttled to one nudge per hour per repo, advisory only, always exits 0. And `scar agent skill` prints the full authoring contract as text for pasting into any runtime's instructions.

## Orchestrators and sub-agents

Sub-agents launch in fresh contexts: edit-anchored scars reach them (the hook fires inside their session), but process-level knowledge — command traps, workflow rules — only arrives if the launch prompt carries it. `scar brief --compact` makes that mechanical:

```bash
scar brief --compact --paths src/payments/ --max-chars 1500
```

One tight line per scar, severity-ordered, byte-capped, plain text with omissions reported (never silent). Command-anchored scars are always included — they are precisely the ones the edit hook cannot deliver. Orchestrator pattern: run it with the files the sub-agent will touch, prepend the block to the launch prompt.

## CI

```bash
scar check src/ --exit-code                   # gate on firing scars
scar check --diff changes.patch --exit-code   # gate on violation: tripwires against a diff
scar sweep --exit-code                        # same tripwires over the whole tree, read-only; hits fail the build
scar lint                                     # format, dead tripwires, overdue reviews, rot
```

Advisory by default everywhere; CI opts into strictness explicitly.

### Review queue

Orphans, partial rot, symbol drift, unreachable evidence, revivals and passed `review_after` dates all end in a red job or a terminal warning that nobody owns. `scar lint --review-queue` turns them into one markdown checklist, one row per scar, and a scheduled workflow keeps a single rolling issue in sync with it:

```bash
scar lint --review-queue
# - [ ] scar 7 `One anchor alive, one rotted` (`src/live/`): partial rot (dead anchor among live ones). Suggested: `scar reanchor`
```

The output is plain text with no scar bodies, evidence or violation patterns: only id, title, path, state and a suggested command. Every value taken from a scar file (title, path, file name) is wrapped in a code span, so a crafted title cannot ping a user, link an issue or inject HTML when the queue is posted to GitHub; the row prefix is `scar 7`, not `#7`, so it never autolinks. When nothing needs review it prints nothing and exits 0. Scar files with lint errors are listed too, by path, with no lint message text. Exit codes are unchanged: lint still exits 1 when any file has an error, and `--fail-orphans` and friends still apply. The output is capped below GitHub's issue body limit, with a final line saying how many rows were omitted. The firing-log signals (firing review, never fired) are left out on purpose: that log lives on each developer's machine and is empty on a CI checkout.

```yaml
# .github/workflows/scar-review-queue.yml
name: Scar review queue
on:
  schedule:
    - cron: '0 7 * * 1'
  workflow_dispatch:

permissions:
  contents: read
  issues: write

jobs:
  queue:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # evidence-reachability checks need full history
      - uses: astral-sh/setup-uv@v5
      - name: Build the queue
        # Keep lint's exit code, but let the next step run first: each run
        # step is bash -e, so a bare failure would skip the issue sync.
        run: |
          set +e
          uvx --from scar-cli scar lint --review-queue > queue.md
          echo "status=$?" >> "$GITHUB_ENV"
          exit 0
      - name: Sync the rolling issue
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          label=scar-review-queue
          gh label create "$label" --color BFD4F2 --description "Rolling Scar review queue" --force
          issue=$(gh issue list --label "$label" --state all --limit 1 --json number,state)
          number=$(jq -r '.[0].number // empty' <<<"$issue")
          state=$(jq -r '.[0].state // empty' <<<"$issue")
          if [ -s queue.md ]; then
            if [ -z "$number" ]; then
              gh issue create --title "Scar review queue" --label "$label" --body-file queue.md
            else
              gh issue edit "$number" --body-file queue.md
              if [ "$state" = "CLOSED" ]; then gh issue reopen "$number"; fi
            fi
          elif [ -n "$number" ] && [ "$state" = "OPEN" ]; then
            gh issue close "$number" --comment "Review queue is empty."
          fi
      - name: Fail the job if lint failed
        if: env.status != '0'
        run: exit 1
```

The queue step records lint's exit code instead of failing, the issue is synced, and the last step fails the job when lint did, so CI stays red and the issue stays current. The issue is rewritten from scar's output on every run, never duplicated, closed when the queue is empty and reopened when it is not. Its visibility follows the repository's: on a public repo the titles and file paths in the checklist are public too.

## Machine-readable docs

Agents reading these docs programmatically: fetch [`/llms.txt`](pathname:///Scar/llms.txt) for a plain-text index of every page with stable URLs, plus the changelog.
