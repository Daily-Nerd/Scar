"""Everything the conformance round trip needs that is not a test (#397).

One place owns the pins, the way the tools are found and checked, the
deterministic repository the fixture act is bound to, and the test-only shim
that stands in for the checker path Scar calls. Production code is untouched:
tests point presence.CHECKER at the shim, exactly as the fake-binary tests do.

The two external programs are never built here. The verifier is a prebuilt
release file; the checker is the pinned source of the same release. Both are
checked against hashes pinned in this file before they are used, so an
environment variable that names a different file is refused, not trusted.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

# ---- pins: changing any of these is a deliberate upgrade, never automatic ----

TAG = "v0.4.0"
COMMIT = "53bbf6789c6b6141dc50b7bd6e53020ea0beb790"
REPO_SLUG = "Daily-Nerd/handsal"
RELEASE_URL = f"https://github.com/{REPO_SLUG}/releases/download/{TAG}"
SUMS_NAME = "SHA256SUMS"
TAR_NAME = f"handsal-{TAG}-linux-amd64.tar"
TAR_SHA256 = "fd21367bdc5c93e18de8ba6d59159f65f49a416bdb09c346698a20b6881eb65e"
VERIFIER_NAME = "vitni-verify"
# The static linux-x86_64 verifier inside the tar (what CI runs), and the
# darwin-arm64 asset of the same release (what a developer machine may have).
VERIFIER_SHA256_LINUX = "79f89c5948c359e6b13338bcfb4ceaef958223183ed0001ec3522d963d433b01"
VERIFIER_SHA256_DARWIN = "6ee527151227e544e32e3ed8900cbfb9b9cac46ee235c474ee25426f004538e1"
VERIFIER_PINS = frozenset({VERIFIER_SHA256_LINUX, VERIFIER_SHA256_DARWIN})
# Digest of the checker package files (see tree_digest), so the source is pinned
# by a sha256 here and not only by the commit it is fetched at.
CHECKER_PACKAGE = "handsal_check"
CHECKER_SRC_RELPATH = "checker/python/src"
CHECKER_TREE_SHA256 = "c4f6995c8f7c5d9d126ae3c0ac002ae6dac0597d5350daf7d148ee9be42aec86"

# ---- environment contract (names avoid the SCAR_ prefix the suite strips) ----

ENV_REQUIRED = "CONFORMANCE_REQUIRED"
ENV_VERIFIER = "CONFORMANCE_VERIFIER"
ENV_CHECKER_SRC = "CONFORMANCE_CHECKER_SRC"

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "conformance" / "promote.json"

# The scar the fixture act promotes. Its record bytes are what the act signs,
# so any change here (or to scar.record) is a change of what stored acts mean.
FIXTURE_SCAR = """---
id: 7
type: fence
title: Keep the odd retry loop
severity: high
confidence: 0.7
created: 2026-11-01
authors: ["claude-code"]
promoted_by: alice
anchors:
  - path: src/retry.py
status: active
---

The loop retries three times on purpose. Do not fold it into the helper.
"""

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": "2026-11-01T00:00:00+00:00",
    "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_DATE": "2026-11-01T00:00:00+00:00",
    # No ambient identity, signing or template may reach the root commit.
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def make_repo(path: Path, message: str = "root") -> Path:
    """A repository with exactly one commit whose id depends only on `message`:
    fixed identity and dates, an empty tree, no config read from the machine."""
    env = {**os.environ, **_GIT_ENV}
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True, env=env, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "--allow-empty", "-m", message],
                   check=True, env=env, capture_output=True)
    return path


# ---- tools ------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(package_dir: Path) -> str:
    """sha256 over `relative path NUL file sha256 LF` for every file under the
    package, sorted, skipping bytecode. Independent of mtimes, modes and of how
    the files were fetched."""
    h = hashlib.sha256()
    for p in sorted(package_dir.rglob("*")):
        rel = p.relative_to(package_dir).as_posix()
        if not p.is_file() or "__pycache__" in rel.split("/") or p.suffix == ".pyc":
            continue
        h.update(f"{rel}\0{sha256_file(p)}\n".encode())
    return h.hexdigest()


class ToolsUnusable(Exception):
    """A tool is absent or is not the pinned one. `absent` tells the two apart:
    absent may be a skip on a developer machine; wrong is always a failure."""

    def __init__(self, reason: str, absent: bool):
        super().__init__(reason)
        self.absent = absent


def resolve_tools(env) -> tuple[str, str]:
    """(verifier path, checker source dir) from the environment, each checked
    against its pin. Raises ToolsUnusable."""
    verifier, src = env.get(ENV_VERIFIER), env.get(ENV_CHECKER_SRC)
    if not verifier or not src:
        raise ToolsUnusable(f"{ENV_VERIFIER} and {ENV_CHECKER_SRC} are not both set", absent=True)
    vp, package = Path(verifier), Path(src) / CHECKER_PACKAGE
    if not vp.is_file() or not package.is_dir():
        raise ToolsUnusable("a named tool does not exist", absent=True)
    if sha256_file(vp) not in VERIFIER_PINS:
        raise ToolsUnusable("the verifier is not the pinned release file", absent=False)
    if tree_digest(package) != CHECKER_TREE_SHA256:
        raise ToolsUnusable("the checker source is not the pinned one", absent=False)
    return str(vp), str(src)


def required(env) -> bool:
    """CI sets this and only CI: with it, a missing tool is a failure."""
    return env.get(ENV_REQUIRED) == "1"


# ---- the shim -----------------------------------------------------------------

_SHIM = """#!{python}
import io, json, sys
sys.dont_write_bytecode = True
cfg = json.load(open({cfg!r}))
sys.path.insert(0, cfg["checker_src"])
from handsal_check import conformance
cmd = sys.argv[1]
req = json.loads(sys.stdin.read())
fill = cfg["fill"].get(cmd, {{}})
clash = sorted(set(req) & set(fill))
if clash:
    # The caller must not send what the shim supplies: keep the fixture honest.
    print(json.dumps({{"error": "shim_clash:" + ",".join(clash)}}))
    sys.exit(0)
req.update(fill)
out = io.BytesIO()
conformance.main(["--vitni", cfg["verifier"], cmd], io.BytesIO(json.dumps(req).encode()), out)
sys.stdout.write(out.getvalue().decode())
"""


class Shim:
    """A program at a tmp path that accepts what Scar sends to its checker path
    and runs the pinned conformance checker over it. It supplies only what the
    production checker keeps in its own store: the pin, the lineage and the
    clock. Nothing Scar sent is changed or dropped."""

    def __init__(self, directory: Path, verifier: str, checker_src: str, fixture: dict):
        self.path = directory / "check-shim"
        self._cfg = directory / "check-shim.cfg.json"
        self._fixture = fixture
        self._base = {"verifier": verifier, "checker_src": checker_src}
        self.path.write_text(_SHIM.format(python=sys.executable, cfg=str(self._cfg)))
        self.path.chmod(0o755)
        self.set_clock(fixture["now"])

    def set_clock(self, now: str) -> None:
        fx = self._fixture
        view = {"acts": [], "lineage": fx["lineage"], "pin": fx["pin"]}
        self._cfg.write_text(json.dumps({
            **self._base,
            "fill": {
                "check-act": {**view, "next": "no", "now": now, "synced_at": now, "tip_statement": None},
                "recheck-act": view,
            },
        }))
