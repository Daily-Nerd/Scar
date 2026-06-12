# AGENTS.md

## Project Overview

SCAR records negative knowledge for a repository: failed approaches
(`deadend`), intentional weirdness (`fence`), and non-obvious coupling
(`landmine`). Scars live in `.scars/` as Markdown files with mandatory YAML
frontmatter.

## Agent Contract

- Before editing anchored code, query SCAR with `scar inject --path <path>` or,
  when you have a diff, `scar inject --diff <unified-diff>`.
- Honor injected scars unless the user explicitly overrides them.
- New scars always start in `.scars/candidates/`; never write directly to
  active `.scars/*.md` files.
- A human promotes candidates with `scar promote`.
- Do not silently ignore broken scar files. Run `scar lint` when changing scar
  format, parsing, promotion, lifecycle, or candidate-writing behavior.

## Agent Integrations

- MCP-capable agents can launch the local server with `scar mcp`.
- Integration snippets are available with:
  - `scar agent config codex`
  - `scar agent config cursor`
  - `scar agent config opencode`
  - `scar agent config windsurf`
- Check local readiness with `scar agent doctor`.

## Development Commands

- Run tests: `uv run pytest`
- Run focused tests: `uv run pytest tests/test_cli.py tests/test_match.py`
- Lint scars: `uv run scar lint`

## Repository Rules

- Do not add AI attribution or `Co-Authored-By` lines to commits.
- Use conventional commit messages.
- Do not build after changes.
- Keep runtime dependencies at zero unless there is a strong architectural
  reason and tests/docs are updated with the tradeoff.
