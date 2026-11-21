"""The commands that rewrite active scar files report a refused rewrite (#412).

These repos have no org in .scars/config.json, so there is nothing to sign a
changed record for (#434): a signed scar is refused with that reason, the
file is byte-identical and no touch is requested. test_sign_in_place covers
the repos that can sign."""
import json

from scar.cli import main
from scar.model import parse_scar_text
from scar.record import record_hash
from scar.store import init_scars

from test_cli import (RENAMED_ORPHAN_SCAR, _git, _git_repo_with_renamed_anchor,
                      _init_bare_git, _pad, _reanchor_scar)
from test_write_gate import make_act


def sign(path):
    """Put an act on the scar file that matches its record as it is now."""
    text = path.read_text()
    act = make_act(record_hash(parse_scar_text(text)))
    path.write_text(text.replace("\nstatus: active\n", f"\npromotion_act: {act}\nstatus: active\n"))
    return path


def extra_renamed_scar(repo, n, signed):
    text = (RENAMED_ORPHAN_SCAR.replace("id: 9", f"id: {n}")
            .replace("Anchored to a path", f"Scar {n} anchored to a path"))
    p = repo / ".scars" / f"{n:04d}-renamed.deadend.md"
    p.write_text(text)
    return sign(p) if signed else p


def test_fix_renames_fixes_the_unsigned_and_lists_the_signed_as_refused(
        tmp_path, monkeypatch, capsys):
    _git_repo_with_renamed_anchor(tmp_path)  # scar 9, unsigned
    s10 = extra_renamed_scar(tmp_path, 10, signed=True)
    s11 = extra_renamed_scar(tmp_path, 11, signed=True)
    before = {p: p.read_bytes() for p in (s10, s11)}
    monkeypatch.chdir(tmp_path)

    rc = main(["orphan", "--fix-renames"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "fixed [#9]" in out
    assert "refused [#10]" in out and "refused [#11]" in out
    assert "scar #10 is signed" in out and "scar #11 is signed" in out
    assert out.count("no org in .scars/config.json") == 2
    assert "no touch was requested" in out
    assert "1 scar(s) fixed, 0 left unfixed, 2 refused" in out
    assert "src/new_home.py" in (tmp_path / ".scars" / "0009-renamed.deadend.md").read_text()
    for p, data in before.items():
        assert p.read_bytes() == data


def test_fix_renames_json_lists_refused_scars_with_the_reason(tmp_path, monkeypatch, capsys):
    _git_repo_with_renamed_anchor(tmp_path)
    extra_renamed_scar(tmp_path, 10, signed=True)
    extra_renamed_scar(tmp_path, 11, signed=True)
    monkeypatch.chdir(tmp_path)

    rc = main(["orphan", "--fix-renames", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert [f["scar_id"] for f in data["fixed"]] == [9]
    assert [r["scar_id"] for r in data["refused"]] == [10, 11]
    assert all("no org in .scars/config.json" in r["reason"] for r in data["refused"])
    assert [r["touch_spent"] for r in data["refused"]] == [False, False]
    assert all(f["signed"] is False for f in data["fixed"])


def test_fix_renames_with_nothing_refused_still_exits_zero(tmp_path, monkeypatch, capsys):
    _git_repo_with_renamed_anchor(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["orphan", "--fix-renames", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["refused"] == []


def _moved_module_repo(tmp_path, scar_id, signed):
    _init_bare_git(tmp_path)
    init_scars(tmp_path)
    content = ("def hello():\n    return 'world from old module'\n\n"
               "def helper():\n    return 42\n")
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "old_module.py").write_text(content)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add old_module")
    (tmp_path / "src" / "old_module.py").unlink()
    (tmp_path / "src" / "new_module.py").write_text(content + _pad("b"))
    f = tmp_path / ".scars" / f"{scar_id:04d}-moved.deadend.md"
    f.write_text(_reanchor_scar(id=scar_id, path_anchors=["src/old_module.py"]))
    if signed:
        sign(f)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "move old_module to new_module + add scar")
    return f


def test_reanchor_apply_on_a_signed_scar_is_refused_with_no_note(tmp_path, monkeypatch, capsys):
    f = _moved_module_repo(tmp_path, 24, signed=True)
    monkeypatch.chdir(tmp_path)
    before = f.read_bytes()

    rc = main(["reanchor", "--apply", "--id", "24"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "refused [#24]" in out and "scar #24 is signed" in out
    assert "no org in .scars/config.json" in out
    assert f.read_bytes() == before  # neither the anchor nor an evidence note


def test_reanchor_apply_json_reports_the_refusal(tmp_path, monkeypatch, capsys):
    f = _moved_module_repo(tmp_path, 24, signed=True)
    monkeypatch.chdir(tmp_path)
    before = f.read_bytes()

    rc = main(["reanchor", "--apply", "--id", "24", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["fixed"] == []
    assert data["refused"][0]["scar_id"] == 24
    assert "no org in .scars/config.json" in data["refused"][0]["reason"]
    assert data["refused"][0]["touch_spent"] is False
    assert f.read_bytes() == before


def test_reanchor_apply_on_an_unsigned_scar_still_writes_anchor_and_note(
        tmp_path, monkeypatch, capsys):
    f = _moved_module_repo(tmp_path, 24, signed=False)
    monkeypatch.chdir(tmp_path)
    assert main(["reanchor", "--apply", "--id", "24", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["refused"] == []
    assert "src/new_module.py" in f.read_text() and "note:" in f.read_text()


def test_transition_refusal_is_a_clear_error_not_a_traceback(tmp_path, monkeypatch, capsys):
    """If a reserialization ever changed signed bytes the command must say so.
    Simulated by a transition whose rewritten text changes the record."""
    init_scars(tmp_path)
    f = tmp_path / ".scars" / "0001-a.deadend.md"
    f.write_text(_reanchor_scar(id=1, path_anchors=["src/a.py"]))
    sign(f)
    monkeypatch.chdir(tmp_path)
    from scar import model

    real = model.Scar.to_text

    def drifting(self):
        return real(self).replace("reanchor test scar 1", "drifted title")

    monkeypatch.setattr(model.Scar, "to_text", drifting)
    before = f.read_bytes()
    assert main(["archive", "1", "--reason", "x"]) == 1
    out = capsys.readouterr().out
    assert "scar 1 is signed" in out
    assert f.read_bytes() == before
