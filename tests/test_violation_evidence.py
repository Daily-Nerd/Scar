"""Write-time violation evidence across every posttool adapter (#317)."""

import hashlib
import io
import json
import re

import pytest

from scar.cli import main
from scar.match import MAX_ANCHOR_SCAN, find_violations
from scar.store import ScarStore
from scar.violation_evidence import is_violation_summary, summarize_match


SCAR = """---
id: 1
type: landmine
title: Forbidden call
severity: high
confidence: 0.9
anchors:
  - path: src/
violation: 'forbidden_call'
evidence:
  - issue: 317
status: active
---

Do not call this function.
"""


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".scars").mkdir()
    (tmp_path / ".scars" / "0001-forbidden.landmine.md").write_text(SCAR)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("unrelated current content\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCAR_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


def run_posttool(repo, runtime, content, monkeypatch, capsys):
    target = str(repo / "src" / "a.py")
    if runtime == "claude-code":
        payload = {"tool_input": {"file_path": target, "new_string": content}}
        command = ["hook", "posttool"]
    elif runtime == "codex":
        added = "\n".join("+" + line for line in content.splitlines())
        patch = ("*** Begin Patch\n*** Update File: src/a.py\n@@\n"
                 + added + "\n*** End Patch")
        payload = {"cwd": str(repo), "tool_name": "apply_patch",
                   "tool_input": {"command": patch},
                   "tool_response": {"exit_code": 0}}
        command = ["hook", "codex-posttool"]
    else:
        payload = {"agent_action_name": "post_write_code",
                   "tool_info": {"file_path": target,
                                 "edits": [{"new_string": content}]}}
        command = ["cascade-hook"]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert main(command) == 0
    capsys.readouterr()
    return json.loads((repo / "state" / "firing-log.jsonl").read_text())


@pytest.mark.parametrize("runtime", ["claude-code", "codex", "windsurf"])
def test_posttool_persists_summary_not_edited_content(
        repo, runtime, monkeypatch, capsys):
    excerpt = '  forbidden_call("private-value-123")'
    record = run_posttool(repo, runtime, "clean\n" + excerpt, monkeypatch, capsys)
    scanned_excerpt = excerpt + (" " if runtime == "claude-code" else "")

    assert record["runtime"] == runtime
    assert record["violation_ids"] == [1]
    assert record["violation_evidence"] == {"1": {
        "format": "summary-v1",
        "pattern_sha256": digest("forbidden_call"),
        "match_sha256": digest("forbidden_call"),
        "excerpt_sha256": digest(scanned_excerpt),
        "line": 2,
        "column": 3,
        "match_chars": len("forbidden_call"),
        "excerpt_chars": len(scanned_excerpt),
        "excerpt_truncated": False,
    }}
    assert "private-value-123" not in json.dumps(record)
    assert "forbidden_call" not in json.dumps(record)


@pytest.mark.parametrize("runtime", ["claude-code", "codex", "windsurf"])
def test_clean_verdict_has_empty_evidence(repo, runtime, monkeypatch, capsys):
    record = run_posttool(repo, runtime, "clean", monkeypatch, capsys)

    assert record["verdict_observed"] is True
    assert record["violation_ids"] == []
    assert record["violation_evidence"] == {}


@pytest.mark.parametrize("pattern,content", [
    ("forbidden_call", "é" * 150 + "forbidden_call()"),
    ("forbidden_call", "safe\n  forbidden_call()\nforbidden_call()"),
    (r"first\nsecond", "prefix\nfirst\nsecond"),
    (r"(?=forbidden_call)", "forbidden_call()"),
    ("(?i)FORBIDDEN_CALL", "forbidden_call()"),
    ("$", ""),
    ("forbidden_call", "\ud800forbidden_call()"),
])
def test_summary_captures_the_actual_match_even_outside_the_excerpt(pattern, content):
    match = re.search(pattern, content)
    assert match is not None
    excerpt, summary = summarize_match(match)

    assert is_violation_summary(summary)
    assert len(excerpt) <= 120
    assert summary["match_sha256"] == hashlib.sha256(
        match.group().encode("utf-8", errors="surrogatepass")).hexdigest()
    assert summary["excerpt_sha256"] == hashlib.sha256(
        excerpt.encode("utf-8", errors="surrogatepass")).hexdigest()
    assert summary["line"] == content.count("\n", 0, match.start()) + 1
    assert summary["column"] == match.start() - content.rfind("\n", 0, match.start())
    assert summary["match_chars"] == len(match.group())
    assert len(json.dumps(summary)) < 512


def test_long_line_marks_excerpt_truncated():
    excerpt, summary = summarize_match(re.search("forbidden_call", "x" * 150 + "forbidden_call"))

    assert excerpt == "x" * 120
    assert summary["column"] == 151
    assert summary["excerpt_chars"] == 120
    assert summary["excerpt_truncated"] is True
    assert summary["match_sha256"] == digest("forbidden_call")


def test_evidence_obeys_match_scan_cap_and_invalid_regex_behavior(repo):
    store = ScarStore.discover(repo)
    assert find_violations(store, "src/a.py", "x" * MAX_ANCHOR_SCAN + "forbidden_call") == []
    (repo / ".scars" / "0001-forbidden.landmine.md").write_text(
        SCAR.replace("'forbidden_call'", "'['"))
    assert find_violations(store, "src/a.py", "forbidden_call") == []


def test_other_scar_files_are_still_checked(repo):
    (repo / ".scars" / "0001-forbidden.landmine.md").write_text(
        SCAR.replace("path: src/", "path: .scars/"))
    store = ScarStore.discover(repo)
    violations = find_violations(store, ".scars/candidates/another.md", "forbidden_call")

    assert len(violations) == 1
    assert is_violation_summary(violations[0].evidence)
    assert find_violations(store, ".scars/0001-forbidden.landmine.md", "forbidden_call") == []


def test_codex_scopes_evidence_to_each_edited_file(repo, monkeypatch, capsys):
    patch = """*** Begin Patch
*** Update File: src/a.py
@@
+forbidden_call('one')
*** Add File: src/b.py
+forbidden_call('two')
*** Add File: src/clean.py
+safe
*** End Patch"""
    payload = {"cwd": str(repo), "tool_name": "apply_patch",
               "tool_input": {"command": patch},
               "tool_response": {"exit_code": 0}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert main(["hook", "codex-posttool"]) == 0
    capsys.readouterr()
    records = [json.loads(line) for line in
               (repo / "state" / "firing-log.jsonl").read_text().splitlines()]
    by_target = {record["target"]: record["violation_evidence"] for record in records}

    assert len(by_target) == 3
    assert by_target[str(repo / "src" / "a.py")]["1"]["excerpt_sha256"] == digest("forbidden_call('one')")
    assert by_target[str(repo / "src" / "b.py")]["1"]["excerpt_sha256"] == digest("forbidden_call('two')")
    assert by_target[str(repo / "src" / "clean.py")] == {}


def summary_fixture():
    return summarize_match(re.search("forbidden_call", "forbidden_call()"))[1]


@pytest.mark.parametrize("field,value", [
    ("format", "summary-v2"), ("format", None),
    ("pattern_sha256", "raw pattern"), ("match_sha256", []),
    ("excerpt_sha256", "f" * 63), ("line", 0), ("line", True),
    ("column", -1), ("column", 1.5), ("match_chars", -1),
    ("match_chars", "14"), ("excerpt_chars", 121), ("excerpt_chars", None),
    ("excerpt_truncated", 1), ("excerpt_truncated", True),
])
def test_malformed_summary_is_unknown(field, value):
    from scar.cli import _aggregate_firings

    summary = summary_fixture()
    summary[field] = value
    aggregate = _aggregate_firings([
        {"violation_ids": [1], "violation_evidence": {"1": summary}}])

    assert not is_violation_summary(summary)
    assert aggregate["violation_evidence_summarized"] == 0
    assert aggregate["violation_evidence_unknown"] == 1


@pytest.mark.parametrize("evidence", [None, [], "bad", {}, {"2": summary_fixture()}])
def test_missing_or_misassociated_evidence_is_unknown(evidence):
    from scar.cli import _aggregate_firings

    aggregate = _aggregate_firings([{"violation_ids": [1], "violation_evidence": evidence}])
    assert aggregate["violation_evidence_summarized"] == 0
    assert aggregate["violation_evidence_unknown"] == 1


def test_unknown_additive_fields_do_not_invalidate_a_summary():
    summary = summary_fixture()
    summary["future_field"] = 3
    assert is_violation_summary(summary)


def test_evidence_is_counted_per_violation_not_per_row():
    from scar.cli import _aggregate_firings

    aggregate = _aggregate_firings([
        {"violation_ids": [1, 2, 3], "violation_evidence": {
            "1": summary_fixture(), "2": summary_fixture()}},
        {"violation_ids": [], "violation_evidence": {}},
        {"violation_ids": "123", "violation_evidence": {"1": summary_fixture()}},
    ])
    assert aggregate["violation_evidence_summarized"] == 2
    assert aggregate["violation_evidence_unknown"] == 1


def test_stats_preserves_legacy_rows_without_reconstructing_evidence(repo, monkeypatch, capsys):
    from scar.cli import _read_firing_log, _stats_rich

    record = run_posttool(repo, "codex", "forbidden_call()", monkeypatch, capsys)
    legacy = {key: value for key, value in record.items() if key != "violation_evidence"}
    legacy["ts"] = "2000-01-01T00:00:00"
    log = repo / "state" / "firing-log.jsonl"
    with log.open("a") as stream:
        stream.write(json.dumps(legacy) + "\n")
    original_log = log.read_bytes()
    (repo / "src" / "a.py").write_text("forbidden_call('present-day text')")
    (repo / ".scars" / "0001-forbidden.landmine.md").write_text(
        SCAR.replace("violation: 'forbidden_call'", "violation: 'another_pattern'"))

    records = _read_firing_log(log)
    assert len(records) == 2
    assert "violation_evidence" not in records[1]
    assert records[0]["violation_evidence"]["1"]["pattern_sha256"] == digest("forbidden_call")
    for options in ([], ["--all-repos"]):
        assert main(["stats", "--json", *options]) == 0
        data = json.loads(capsys.readouterr().out)
        block = data["repos"][0] if options else data
        assert block["violation_evidence_summarized"] == 1
        assert block["violation_evidence_unknown"] == 1
        assert main(["stats", *options]) == 0
        output = capsys.readouterr().out
        assert "1 summarized (no raw text), 1 unknown" in output
        assert "summaries alone cannot adjudicate violations" in output
        if not options:
            _stats_rich(data)
            assert "evidence:" in capsys.readouterr().out
    assert main(["stats", "--json", "--until", "2000-01-02"]) == 0
    historical = json.loads(capsys.readouterr().out)
    assert historical["violation_evidence_summarized"] == 0
    assert historical["violation_evidence_unknown"] == 1
    assert log.read_bytes() == original_log


def test_log_write_failure_does_not_block_the_hook(repo, monkeypatch, capsys):
    state = repo / "state"
    state.mkdir()
    (state / "firing-log.jsonl").mkdir()
    payload = {"tool_input": {"file_path": str(repo / "src" / "a.py"),
                              "content": "forbidden_call()"}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    assert main(["hook", "posttool"]) == 0
    assert "violation pattern" in capsys.readouterr().out
