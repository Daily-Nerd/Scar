"""`scar sweep` (#326): the violation tripwire run over the tree as it stands."""

import json
import subprocess

import pytest

from scar.cli import main
from scar.hooks import firing_log_path
from scar.match import _violation_lines
from scar.store import init_scars


def _scar(id_, anchor, violation, status="active", title="No raw sleep",
          type_="fence"):
    viol = f'violation: "{violation}"\n' if violation is not None else ""
    return (
        f"---\nid: {id_}\ntype: {type_}\ntitle: {title}\nseverity: high\n"
        f"confidence: 0.9\ncreated: 2026-06-10\nauthors: [k]\nanchors:\n"
        f"  - path: {anchor}\nevidence:\n  - commit: abc1234\n{viol}"
        f"status: {status}\n---\n\nDo not call sleep.\n")


SLEEP = r"sleep\((?:[0-6])\)"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCAR_STATE_DIR", str(tmp_path / "state"))
    init_scars(tmp_path)
    return tmp_path


def _write(repo, rel, text):
    fp = repo / rel
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(text, encoding="utf-8")


def _track(repo):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)


def _sweep_json(capsys, *extra):
    capsys.readouterr()
    code = main(["sweep", "--json", *extra])
    return code, json.loads(capsys.readouterr().out)


# --- the line helper ---------------------------------------------------------

def test_violation_lines_reports_every_matching_line_once():
    text = "a\nsleep(1) sleep(2)\nb\nsleep(3)\n"
    assert _violation_lines(r"sleep\((?:[0-6])\)", text) == [
        (2, "sleep(1) sleep(2)"), (4, "sleep(3)")]


def test_violation_lines_trims_like_the_excerpt():
    line = "sleep(1)" + "x" * 300
    ((n, got),) = _violation_lines(r"sleep\(1\)", line)
    assert n == 1 and got == line[:120]


def test_violation_lines_invalid_regex_yields_nothing():
    assert _violation_lines("(", "anything\n") == []


def test_violation_lines_scans_past_the_hot_path_bound():
    text = ("x\n" * 40000) + "sleep(1)\n"
    assert len(text) > 64 * 1024
    assert _violation_lines(r"sleep\(1\)", text) == [(40001, "sleep(1)")]


# --- the verb ----------------------------------------------------------------

def test_sweep_reports_each_hit_with_path_line_and_excerpt(repo, capsys):
    (repo / ".scars" / "0042-nosleep.fence.md").write_text(_scar(42, "src/", SLEEP))
    _write(repo, "src/b.py", "import time\nx = 1\ntime.sleep(2)\ntime.sleep(3)\n")
    _write(repo, "src/a.py", "time.sleep(1)\n")
    _write(repo, "src/clean.py", "time.sleep(60)\n")
    _track(repo)
    code, data = _sweep_json(capsys)
    assert code == 0
    assert [(h["path"], h["line"]) for h in data["hits"]] == [
        ("src/a.py", 1), ("src/b.py", 3), ("src/b.py", 4)]
    hit = data["hits"][1]
    assert hit == {"id": 42, "type": "fence", "severity": "high",
                   "title": "No raw sleep",
                   "source": ".scars/0042-nosleep.fence.md",
                   "path": "src/b.py", "line": 3, "excerpt": "time.sleep(2)"}
    assert data["scars"] == 1


def test_sweep_does_not_report_where_the_scar_does_not_arm(repo, capsys):
    (repo / ".scars" / "0042-nosleep.fence.md").write_text(_scar(42, "src/", SLEEP))
    _write(repo, "other/c.py", "time.sleep(1)\n")
    _track(repo)
    code, data = _sweep_json(capsys)
    assert data["hits"] == []
    assert data["scars"] == 0


def test_sweep_excludes_the_scars_own_file_but_not_other_scar_files(repo, capsys):
    own = _scar(5, ".scars/", "SECRET_TOKEN", title="No tokens")
    (repo / ".scars" / "0005-tok.fence.md").write_text(own + "\nSECRET_TOKEN\n")
    (repo / ".scars" / "0006-other.fence.md").write_text(
        _scar(6, "nowhere/", None) + "\nSECRET_TOKEN\n")
    _track(repo)
    code, data = _sweep_json(capsys)
    assert [(h["id"], h["path"]) for h in data["hits"]] == [
        (5, ".scars/0006-other.fence.md")]


def test_sweep_ignores_archived_and_violationless_scars(repo, capsys):
    (repo / ".scars" / "0042-a.fence.md").write_text(
        _scar(42, "src/", SLEEP, status="archived"))
    (repo / ".scars" / "0043-b.fence.md").write_text(_scar(43, "src/", None))
    _write(repo, "src/a.py", "time.sleep(1)\n")
    _track(repo)
    code, data = _sweep_json(capsys)
    assert data["hits"] == []


def test_sweep_includes_challenged_scars(repo, capsys):
    (repo / ".scars" / "0042-a.fence.md").write_text(
        _scar(42, "src/", SLEEP, status="challenged"))
    _write(repo, "src/a.py", "time.sleep(1)\n")
    _track(repo)
    code, data = _sweep_json(capsys)
    assert len(data["hits"]) == 1


def test_sweep_skips_oversized_files_and_counts_them(repo, capsys):
    (repo / ".scars" / "0042-a.fence.md").write_text(_scar(42, "src/", SLEEP))
    _write(repo, "src/big.py", "time.sleep(1)\n" + "#" * (1024 * 1024 + 10))
    _write(repo, "src/a.py", "time.sleep(1)\n")
    _track(repo)
    code, data = _sweep_json(capsys)
    assert [h["path"] for h in data["hits"]] == ["src/a.py"]
    assert data["skipped"] == 1
    assert data["files"] == len(
        subprocess.run(["git", "ls-files"], capture_output=True, text=True
                       ).stdout.split()) - 1


def test_sweep_invalid_regex_gives_no_hits_and_no_crash(repo, capsys):
    (repo / ".scars" / "0042-a.fence.md").write_text(_scar(42, "src/", "(unclosed"))
    _write(repo, "src/a.py", "time.sleep(1)\n")
    _track(repo)
    code, data = _sweep_json(capsys)
    assert code == 0 and data["hits"] == []


def test_sweep_never_writes_the_firing_log(repo, capsys):
    (repo / ".scars" / "0042-a.fence.md").write_text(_scar(42, "src/", SLEEP))
    _write(repo, "src/a.py", "time.sleep(1)\n")
    _track(repo)
    log = firing_log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('{"ts": "2026-06-10T10:00:00"}\n', encoding="utf-8")
    before = log.read_bytes()
    code, data = _sweep_json(capsys)
    assert len(data["hits"]) == 1
    main(["sweep"])
    assert log.read_bytes() == before


def test_sweep_never_creates_a_firing_log(repo, capsys):
    (repo / ".scars" / "0042-a.fence.md").write_text(_scar(42, "src/", SLEEP))
    _write(repo, "src/a.py", "time.sleep(1)\n")
    _track(repo)
    _sweep_json(capsys)
    assert not firing_log_path().exists()


def test_sweep_exit_codes(repo, capsys):
    (repo / ".scars" / "0042-a.fence.md").write_text(_scar(42, "src/", SLEEP))
    _write(repo, "src/a.py", "time.sleep(1)\n")
    _track(repo)
    assert main(["sweep"]) == 0
    assert main(["sweep", "--exit-code"]) == 1
    _write(repo, "src/a.py", "time.sleep(60)\n")
    assert main(["sweep", "--exit-code"]) == 0


def test_sweep_json_shape_when_there_are_no_hits(repo, capsys):
    code, data = _sweep_json(capsys)
    assert code == 0
    assert data == {"files": data["files"], "skipped": 0, "scars": 0, "hits": []}
    assert isinstance(data["files"], int)


def test_sweep_without_git_exits_1_with_a_reason(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    init_scars(tmp_path)
    capsys.readouterr()
    assert main(["sweep"]) == 1
    out = capsys.readouterr().out
    assert "git" in out and len(out.strip().splitlines()) == 1


def test_sweep_without_a_store_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["sweep"]) == 1


def test_sweep_plain_output_is_stable(repo, capsys):
    (repo / ".scars" / "0042-nosleep.fence.md").write_text(_scar(42, "src/", SLEEP))
    _write(repo, "src/a.py", "x = 1\ntime.sleep(1)\n")
    _track(repo)
    capsys.readouterr()
    assert main(["sweep"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "src/a.py:2: scar #42 (fence): No raw sleep"
    assert lines[1] == "  time.sleep(1)"
    assert lines[-1] == "swept 4 files, skipped 0, 1 scars armed, 1 hits"


def test_sweep_plain_output_with_no_hits_is_only_the_summary(repo, capsys):
    capsys.readouterr()
    assert main(["sweep"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and lines[0].startswith("swept ")


def test_sweep_groups_by_scar_in_id_order(repo, capsys):
    (repo / ".scars" / "0002-b.fence.md").write_text(
        _scar(2, "src/", "bbb", title="B"))
    (repo / ".scars" / "0001-a.fence.md").write_text(
        _scar(1, "src/", "aaa", title="A"))
    _write(repo, "src/x.py", "bbb\naaa\n")
    _write(repo, "src/y.py", "aaa\n")
    _track(repo)
    code, data = _sweep_json(capsys)
    assert [(h["id"], h["path"], h["line"]) for h in data["hits"]] == [
        (1, "src/x.py", 2), (1, "src/y.py", 1), (2, "src/x.py", 1)]
    assert data["scars"] == 2


def test_sweep_counts_an_empty_file_as_swept_not_skipped(repo, capsys):
    (repo / ".scars" / "0042-a.fence.md").write_text(_scar(42, "src/", SLEEP))
    _write(repo, "src/empty.py", "")
    _track(repo)
    code, data = _sweep_json(capsys)
    assert data["skipped"] == 0
