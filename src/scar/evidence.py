"""Evidence reachability — READ-ONLY git check (#43, scar #5's expiry condition).

A scar's commit-SHA evidence is a receipt: it must resolve from HEAD or it can't
be verified in a fresh clone, which is exactly where a stranger would check it.
History rewrites (force-push, fresh-start, filter-repo) silently strand these
SHAs — the scar still lints clean and still fires, but `git show <sha>` fails.

This module flags commit evidence not reachable from HEAD. Advisory only: it
never fails a build and never mutates a scar. Non-commit evidence (pr, note,
incident) is ignored. On a SHALLOW clone reachability cannot be determined, so
the check is skipped entirely (returns None) rather than false-warning on every
historical SHA — actions/checkout defaults to depth 1.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .store import ScarStore

# A bare git object name: 7–40 lowercase hex chars. Full SHAs and the common
# abbreviated form both pass; anything with other characters is not a SHA.
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


@dataclass
class UnreachableEvidence:
    """One commit-SHA receipt that does not resolve from HEAD."""
    scar_id: int | None
    sha: str
    reason: str   # "missing" (object gone) | "off-history" (present, not an ancestor)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


def _is_shallow(repo: Path) -> bool:
    return _git(repo, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"


def _commit_shas(scar) -> list[str]:
    """Bare commit SHAs cited in a scar's evidence. Evidence entries are strings
    like 'commit: <sha>' / 'pr: 40' / 'note: ...'; only commit: is a receipt."""
    shas = []
    for entry in scar.evidence:
        key, _, value = entry.partition(":")
        if key.strip() == "commit":
            sha = value.strip().strip('"').strip("'")
            if _SHA_RE.match(sha):
                shas.append(sha)
    return shas


def _reachable(repo: Path, sha: str) -> bool:
    """True iff *sha* is an ancestor of HEAD. Non-zero exit covers BOTH a missing
    object and a present-but-off-history object — the two failure modes we warn on."""
    return _git(repo, "merge-base", "--is-ancestor", sha, "HEAD").returncode == 0


def _exists(repo: Path, sha: str) -> bool:
    return _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def unreachable_evidence(store: ScarStore, repo: Path) -> list[UnreachableEvidence] | None:
    """Commit-evidence SHAs across all parsed scars that don't resolve from HEAD.

    Returns None when the repo is shallow — reachability is indeterminate there,
    so we skip rather than flood with false warnings. Read-only.
    """
    repo = Path(repo)
    if _is_shallow(repo):
        return None

    findings: list[UnreachableEvidence] = []
    for _source, scar in store.parsed():
        for sha in _commit_shas(scar):
            if _reachable(repo, sha):
                continue
            reason = "off-history" if _exists(repo, sha) else "missing"
            findings.append(UnreachableEvidence(scar_id=scar.id, sha=sha, reason=reason))
    return findings
