"""One reader for scar files (#439).

Every scar path in the store is either read or reported as unreadable, never
dropped without a word and never a crash. A file that is not valid UTF-8 is an
unparseable scar, the same as one with no frontmatter.
"""
import ast
import json
import subprocess
from pathlib import Path

import pytest

from scar import renames
from scar.cli import main
from scar.model import ParseError
from scar.store import ScarStore, init_scars, read_scar, read_scar_text, write_active_scar

SRC = Path(__file__).resolve().parent.parent / "src" / "scar"

GOOD = """\
---
id: 1
type: fence
title: Good
severity: low
confidence: 0.5
created: 2026-11-01
authors: ["claude-code"]
promoted_by: kibukx
anchors:
  - path: src/x.py
status: active
---

Body.
"""

BAD_BYTES = b"\xff\xfe---\ntitle: nope\n---\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    init_scars(tmp_path)
    return tmp_path


def _bad(repo, name="0002-bad.fence.md"):
    path = repo / ".scars" / name
    path.write_bytes(BAD_BYTES)
    return path


# ---------------------------------------------------------------------------
# The reader itself
# ---------------------------------------------------------------------------


def test_read_scar_text_turns_a_decode_failure_into_a_parse_error(tmp_path):
    path = tmp_path / "x.md"
    path.write_bytes(BAD_BYTES)
    with pytest.raises(ParseError, match="not valid UTF-8"):
        read_scar_text(path)


def test_read_scar_text_leaves_a_missing_file_an_oserror(tmp_path):
    with pytest.raises(OSError):
        read_scar_text(tmp_path / "absent.md")


def test_read_scar_text_translates_newlines_unless_asked_to_keep_them(tmp_path):
    path = tmp_path / "x.md"
    path.write_bytes(b"a\r\nb\r\n")
    assert read_scar_text(path) == "a\nb\n"
    assert read_scar_text(path, keep_newlines=True) == "a\r\nb\r\n"


def test_read_scar_parses_and_raises_parse_error_for_both_failures(tmp_path):
    good = tmp_path / "g.md"
    good.write_text(GOOD, encoding="utf-8")
    assert read_scar(good).title == "Good"
    for content in (b"no frontmatter\n", BAD_BYTES):
        bad = tmp_path / "b.md"
        bad.write_bytes(content)
        with pytest.raises(ParseError):
            read_scar(bad)


# ---------------------------------------------------------------------------
# An undecodable file shows up wherever an unparseable one does
# ---------------------------------------------------------------------------


def test_undecodable_scar_is_reported_as_broken_everywhere(repo, capsys):
    (repo / ".scars" / "0001-good.fence.md").write_text(GOOD, encoding="utf-8")
    bad = _bad(repo)
    store = ScarStore.discover(repo)

    assert store.broken() == [bad]
    firing, broken = store.scan()
    assert [s.id for _f, s in firing] == [1] and broken == [bad]
    assert [f for f, _s in store.parsed()] == [repo / ".scars" / "0001-good.fence.md"]

    assert main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["broken"] == [bad.name]

    assert main(["lint", "--json"]) == 1
    findings = json.loads(capsys.readouterr().out)["findings"]
    errors = [f for f in findings if bad.name in f["file"] and f["level"] == "error"]
    assert errors and "not valid UTF-8" in errors[0]["message"]

    assert main(["inject", "--path", "src/x.py", "--content", ""]) == 0
    assert bad.name in capsys.readouterr().out


def test_an_undecodable_candidate_is_pending_and_a_lint_error(repo, capsys):
    cand = repo / ".scars" / "candidates" / "x.md"
    cand.write_bytes(BAD_BYTES)
    store = ScarStore.discover(repo)
    assert store.pending_candidates() == [cand]
    assert main(["lint"]) == 1
    assert "not valid UTF-8" in capsys.readouterr().out


def test_an_undecodable_candidate_is_refused_by_promote_not_a_crash(repo):
    cand = repo / ".scars" / "candidates" / "x.md"
    cand.write_bytes(BAD_BYTES)
    store = ScarStore.discover(repo)
    with pytest.raises(ValueError, match="not valid UTF-8"):
        store.check_promotable(cand)


def test_next_id_ignores_an_undecodable_file(repo):
    _bad(repo, "0007-bad.fence.md")
    assert ScarStore.discover(repo).next_id() == 1


def test_write_gate_overwrites_an_undecodable_file_like_an_unparseable_one(repo):
    bad = _bad(repo, "0001-bad.fence.md")
    write_active_scar(bad, GOOD)
    assert bad.read_text(encoding="utf-8") == GOOD


def test_apply_anchor_rewrite_on_an_undecodable_file_raises_parse_error(repo):
    bad = _bad(repo, "0001-bad.fence.md")
    with pytest.raises(ParseError):
        renames.apply_anchor_rewrite(bad, "path", {"a": "b"})


# ---------------------------------------------------------------------------
# Structure: no second owner of the read
# ---------------------------------------------------------------------------

# (module, function) pairs that read a file which is NOT a scar file, grouped by
# what they read. A new entry needs a reason a reviewer accepts: a scar path
# read anywhere else reintroduces the crash.
NON_SCAR_READS = {
    # source files being matched, traced or fingerprinted
    ("match", "_read_source_cached"), ("orphan", "build_repo_context"),
    ("orphan", "detect_symbol_drift"), ("reanchor", "trace_dead_path"),
    ("reanchor", "dead_symbol_anchors"), ("reanchor", "trace_dead_symbol"),
    # a --diff file the user names, and the labels file
    ("cli", "_cmd_check"), ("cli", "_cmd_inject"), ("cli", "_load_labels"),
    # machine-local state: firing log, hook failures, fire state, config
    ("firing_log", "read_firing_log_records"), ("gc", "truncate_firing_log"),
    ("gc", "_count_lines"), ("hooks", "record_hook_failure"),
    ("hooks", "_read_hook_failures"), ("cascade", "_load_fire_state"),
    ("config", "load"),
    # host settings, git hooks and codex config the installer edits
    ("hosts", "claude_plugin_enabled"), ("installer", "load_settings"),
    ("installer", "git_hook_install"), ("installer", "git_hook_uninstall"),
    ("installer", "git_hook_status"), ("installer", "_load_cascade_config"),
    ("installer", "_codex_plugin_hooks_silenced"),
    ("installer", "codex_plugin_channels"), ("installer", "_load_codex_config"),
    # the packaged skill text
    ("agent", "skill"),
}
READ_ATTRS = {"read_text", "read_bytes"}


def _read_sites():
    sites = []
    for path in sorted(SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in READ_ATTRS):
                    sites.append((path.stem, fn.name, node.lineno))
    return sites


def test_every_scar_path_read_goes_through_the_one_reader():
    """No module calls read_text or read_bytes on a scar path: they call
    store.read_scar_text / read_scar. Every other read in src/scar is on the
    allowlist above, and the allowlist holds no stale entry."""
    sites = {(module, func) for module, func, _l in _read_sites()}
    offenders = sorted(sites - NON_SCAR_READS - {("store", "read_scar_text")})
    assert offenders == []
    assert sorted(NON_SCAR_READS - sites) == []


def test_the_reader_is_the_only_read_in_store():
    assert [fn for module, fn, _l in _read_sites() if module == "store"] == [
        "read_scar_text"]
