"""Fetch the pinned external tools for the conformance round trip (#397).

    python tests/conformance_fetch.py DEST

DEST must not exist yet. Downloads the release files of one pinned tag, checks
the tar against the published SHA256SUMS and against the sha256 pinned in
conformance_support, takes the verifier out of the tar and checks it the same
two ways, fetches the checker source at the pinned commit and checks its content
digest. Any mismatch exits non-zero before anything is written to the
environment: fail closed. Nothing is built. Standard library only.

On success it prints the three variables the tests read, and appends them to
the file named by GITHUB_ENV when that is set (the CI job).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conformance_support as cs  # noqa: E402


def die(message: str) -> None:
    sys.exit(f"conformance fetch refused: {message}")


def download(name: str, dest: Path) -> Path:
    target = dest / name
    with urllib.request.urlopen(f"{cs.RELEASE_URL}/{name}", timeout=120) as resp, open(target, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    return target


def published(sums: Path) -> dict[str, str]:
    table = {}
    for line in sums.read_text().splitlines():
        digest, _, name = line.partition("  ")
        if digest and name:
            table[name] = digest
    return table


def check(label: str, got: str, *wanted: str) -> None:
    for want in wanted:
        if got != want:
            die(f"{label}: {got} is not {want}")


def verifier_from(tar_path: Path, dest: Path) -> Path:
    # Read the one member we need; never extract the archive.
    with tarfile.open(tar_path) as tar:
        member = tar.getmember(cs.VERIFIER_NAME)
        if not member.isfile():
            die(f"{cs.VERIFIER_NAME} in the tar is not a regular file")
        data = tar.extractfile(member).read()
    target = dest / cs.VERIFIER_NAME
    target.write_bytes(data)
    target.chmod(0o755)
    return target


def checker_source(dest: Path) -> Path:
    git = ["git", "-C", str(dest)]
    run = lambda *a: subprocess.run([*git, *a], check=True, capture_output=True, text=True).stdout.strip()  # noqa: E731
    dest.mkdir()
    run("init", "-q")
    run("fetch", "-q", "--depth", "1", f"https://github.com/{cs.REPO_SLUG}", cs.COMMIT)
    run("checkout", "-q", "--detach", "FETCH_HEAD")
    check("checker source commit", run("rev-parse", "HEAD"), cs.COMMIT)
    src = dest / cs.CHECKER_SRC_RELPATH
    check("checker source digest", cs.tree_digest(src / cs.CHECKER_PACKAGE), cs.CHECKER_TREE_SHA256)
    return src


def main(dest_arg: str) -> None:
    dest = Path(dest_arg)
    if dest.exists():
        die(f"{dest} exists; use a new, empty location")
    dest.mkdir(parents=True)
    dl = dest / "dl"
    dl.mkdir()
    sums = published(download(cs.SUMS_NAME, dl))
    tar_path = download(cs.TAR_NAME, dl)
    tar_sha = cs.sha256_file(tar_path)
    check("tar vs pin", tar_sha, cs.TAR_SHA256)
    check("tar vs " + cs.SUMS_NAME, tar_sha, sums.get(cs.TAR_NAME, "missing"))
    verifier = verifier_from(tar_path, dest)
    v_sha = cs.sha256_file(verifier)
    check("verifier vs pin", v_sha, cs.VERIFIER_SHA256_LINUX)
    check("verifier vs " + cs.SUMS_NAME, v_sha, sums.get(cs.VERIFIER_NAME, "missing"))
    src = checker_source(dest / "signer")
    env = {cs.ENV_VERIFIER: str(verifier), cs.ENV_CHECKER_SRC: str(src), cs.ENV_REQUIRED: "1"}
    lines = "".join(f"{k}={v}\n" for k, v in env.items())
    sys.stdout.write(lines)
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a") as fh:
            fh.write(lines)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
