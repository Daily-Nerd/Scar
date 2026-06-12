# Contributing to SCAR

Thanks for your interest. This project is personal infrastructure shared as-is — contributions are welcome, but please read this first so the conventions don't surprise you.

## Issue-first workflow (CI-enforced)

1. **Open an issue before any PR.** Blank PRs without a linked issue are blocked by GitHub Actions.
2. Wait for the issue to get the `status:approved` label.
3. Branch from `main` using `type/description` naming (`fix/parser-quotes`, `feat/mcp-server`).
4. Open a PR whose body contains `Closes #<issue>` and carries exactly one `type:*` label (`type:fix`, `type:feature`, `type:refactor`, `type:chore`).

## Commits

Conventional commits, enforced by CI:

```
type(scope): description
```

Types: `build`, `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`, `style`, `test`. No AI attribution trailers.

## Code expectations

- **Zero runtime dependencies.** This is a hard constraint — hook startup must stay ~20ms. If your change needs a dependency, open an issue to discuss first.
- **Tests required.** `uv run pytest` must pass; new behavior needs new tests.
- **One parser.** All scar reading/writing goes through `src/scar/model.py`. Never parse frontmatter anywhere else — parser drift is how knowledge systems rot.
- `scar lint` must stay clean (CI runs it).

## Writing scars for this repo

Dogfooding is encouraged. If you hit a dead end working on SCAR itself, record it: copy `.scars/template.md` to `.scars/candidates/<slug>.md`. Only maintainers promote candidates to active.
