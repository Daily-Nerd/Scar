# .scars/ — Negative knowledge for this repo

This directory records what this codebase **refused to be**: approaches that
were tried and failed (`deadend`), configuration that looks wrong but is
intentional (`fence`), and changes that break non-obvious things elsewhere
(`landmine`).

Before "cleaning up" anything these files anchor to — read the scar first.
Every scar carries evidence (commit hashes in this repo's history). If a scar
is stale, challenge it: update or archive it with a note, don't ignore it.

Format: YAML frontmatter + Markdown body, one scar per file,
`{seq}-{slug}.{type}.md`. Spec: github.com/<pending> (SCAR project, concept
stage — this repo is the first production scar graph).

Provenance: harvested from 470 commits of git history on 2026-06-09
(`scar harvest` prototype), curated, and confirmed by the repo owner.
