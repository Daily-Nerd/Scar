"""reanchor --apply and orphan --fix-renames sign in place (#434).

Everything runs against the strict fake signer and checker from test_resign.
The real programs are never run.
"""
import base64
import json

import pytest

from scar import cli, output, presence, renames, signedpromote, store as store_module
from scar.cli import main
from scar.model import parse_scar_text
from scar.record import promoted_record, record_hash

from test_resign import ORG, RECEIPT
from test_resign import fakes as resign_fakes  # noqa: F401  (fixture)
from test_write_gate import make_act
from test_write_gate_cli import _moved_module_repo, extra_renamed_scar, sign
from test_cli import _git_repo_with_renamed_anchor

NEW_MODULE = "src/new_module.py"


@pytest.fixture
def fakes(request):
    """The strict fake signer and checker of test_resign, pointed at by presence."""
    return request.getfixturevalue("resign_fakes")


def configure(repo, text=None):
    (repo / ".scars" / "config.json").write_text(
        text if text is not None else json.dumps({"org": ORG, "signed_from": 1}))


@pytest.fixture
def moved(tmp_path, monkeypatch, fakes):
    """A signed scar 24 whose anchored module moved, in a repo that signs."""
    f = _moved_module_repo(tmp_path, 24, signed=True)
    configure(tmp_path)
    monkeypatch.chdir(tmp_path)
    return f


@pytest.fixture
def renamed(tmp_path, monkeypatch, fakes):
    """Scar 9 unsigned, 10 and 11 signed, all anchored to the renamed path."""
    _git_repo_with_renamed_anchor(tmp_path)
    s10 = extra_renamed_scar(tmp_path, 10, signed=True)
    s11 = extra_renamed_scar(tmp_path, 11, signed=True)
    configure(tmp_path)
    monkeypatch.chdir(tmp_path)
    return {9: tmp_path / ".scars" / "0009-renamed.deadend.md", 10: s10, 11: s11}


def act_matches(path):
    scar = parse_scar_text(path.read_text())
    return presence.act_inputs_hash(scar.promotion_act) == record_hash(scar)


def spy_order(monkeypatch):
    """Every presence call and every scar-file write, in order."""
    order = []
    for name in ("enrolled", "render", "head", "sign", "ingest"):
        real = getattr(presence, name)

        def spy(*args, _real=real, _name=name):
            order.append(_name)
            return _real(*args)

        monkeypatch.setattr(presence, name, spy)
    real_write = store_module.write_active_scar

    def write_spy(path, text):
        order.append(("write", path.name))
        real_write(path, text)

    for module in (signedpromote, renames):
        if hasattr(module, "write_active_scar"):
            monkeypatch.setattr(module, "write_active_scar", write_spy)
    monkeypatch.setattr(store_module, "write_active_scar", write_spy)
    return order


# ---------------------------------------------------------------------------
# reanchor --apply on a signed scar
# ---------------------------------------------------------------------------


def test_reanchor_signs_in_place_one_touch(moved, fakes, capsys):
    signer, _ = fakes
    assert main(["reanchor", "--apply", "--id", "24"]) == 0
    text = moved.read_text()
    assert NEW_MODULE in text and "note:" in text
    assert act_matches(moved)
    assert parse_scar_text(text).promotion_receipt == RECEIPT
    assert len(signer.calls("sign")) == 1
    assert "signed" in capsys.readouterr().out
    assert main(["lint"]) == 0, capsys.readouterr().out


def test_reanchor_call_order_and_one_write(moved, fakes, monkeypatch):
    order = spy_order(monkeypatch)
    assert main(["reanchor", "--apply", "--id", "24"]) == 0
    assert order == ["head", "enrolled", "render", "head", "sign", "ingest",
                     ("write", moved.name)]


def test_reanchor_signer_is_shown_the_record_as_written(moved, fakes):
    signer, _ = fakes
    assert main(["reanchor", "--apply", "--id", "24"]) == 0
    (call,) = signer.calls("sign")
    shown = base64.b64decode(call["stdin_json"]["attachments"]["decision_b64"])
    assert shown == promoted_record(parse_scar_text(moved.read_text()))
    assert NEW_MODULE.encode() in shown


def test_reanchor_cancelled_touch_leaves_the_file_alone(moved, fakes, capsys):
    signer, checker = fakes
    signer.reply("sign", ({"error": "user_cancel"}, 1))
    before = moved.read_bytes()
    assert main(["reanchor", "--apply", "--id", "24"]) == 1
    assert "cancelled" in capsys.readouterr().out
    assert checker.calls("check-act") == []
    assert moved.read_bytes() == before


def test_reanchor_no_terminal_leaves_the_file_alone(moved, fakes, capsys):
    signer, checker = fakes
    signer.reply("sign", ({"error": "no_display"}, 4))
    before = moved.read_bytes()
    assert main(["reanchor", "--apply", "--id", "24"]) == 1
    assert "no_display" in capsys.readouterr().out
    assert checker.calls("check-act") == []
    assert moved.read_bytes() == before


def test_reanchor_missing_signer_asks_for_no_touch(moved, fakes, monkeypatch, tmp_path,
                                                   capsys):
    signer, _ = fakes
    monkeypatch.setattr(presence, "SIGNER", str(tmp_path / "nope-sign"))
    before = moved.read_bytes()
    assert main(["reanchor", "--apply", "--id", "24"]) == 1
    out = capsys.readouterr().out
    assert "not installed at its fixed path" in out and "no touch was requested" in out
    assert signer.calls("sign") == []
    assert moved.read_bytes() == before


def test_reanchor_render_refused_asks_for_no_touch(moved, fakes, capsys):
    signer, _ = fakes
    signer.reply("render", ({"error": "render_refused"}, 2))
    before = moved.read_bytes()
    assert main(["reanchor", "--apply", "--id", "24"]) == 1
    out = capsys.readouterr().out
    assert "the signer cannot show this record" in out and "no touch was requested" in out
    assert signer.calls("sign") == []
    assert moved.read_bytes() == before


def test_reanchor_checker_refusal_after_the_touch_says_so(moved, fakes, capsys):
    _, checker = fakes
    checker.reply("check-act", ({"class": None, "reason": "validity_too_long",
                                 "receipt_id": "uEiX", "valid": False}, 0))
    before = moved.read_bytes()
    assert main(["reanchor", "--apply", "--id", "24"]) == 1
    out = capsys.readouterr().out
    assert "touch was spent" in out and "validity_too_long" in out
    assert moved.read_bytes() == before


def test_reanchor_file_changed_during_the_touch_is_not_overwritten(moved, fakes, monkeypatch,
                                                                   capsys):
    real_sign = presence.sign

    def sign_then_edit(request):
        result = real_sign(request)
        moved.write_text(moved.read_text().replace("Body text.", "Edited meanwhile."))
        return result

    monkeypatch.setattr(presence, "sign", sign_then_edit)
    assert main(["reanchor", "--apply", "--id", "24"]) == 1
    out = capsys.readouterr().out
    assert "touch was spent" in out and "changed while it was being signed" in out
    assert "Edited meanwhile." in moved.read_text() and RECEIPT not in moved.read_text()


@pytest.mark.parametrize("cfg,expect", [
    (None, "no org in .scars/config.json"),
    ("{bad", "config.json"),
    (json.dumps({"org": ORG}), "signed_from"),
])
def test_reanchor_config_problems_refuse_with_the_reason_and_no_touch(moved, fakes, capsys,
                                                                      cfg, expect):
    signer, _ = fakes
    if cfg is None:
        (moved.parent / "config.json").unlink()
    else:
        configure(moved.parent.parent, cfg)
    before = moved.read_bytes()
    assert main(["reanchor", "--apply", "--id", "24"]) == 1
    out = capsys.readouterr().out
    assert "refused [#24]" in out and "signed" in out and expect in out
    assert signer.calls() == []
    assert moved.read_bytes() == before


def test_reanchor_challenged_signed_scar_is_refused_naming_the_status(moved, fakes, capsys):
    signer, _ = fakes
    moved.write_text(moved.read_text().replace("status: active", "status: challenged"))
    before = moved.read_bytes()
    assert main(["reanchor", "--apply", "--id", "24"]) == 1
    out = capsys.readouterr().out
    assert "challenged" in out and "only an active scar is signed" in out
    assert signer.calls() == []
    assert moved.read_bytes() == before


def test_reanchor_unsigned_scar_in_a_signing_repo_is_written_with_no_touch(
        tmp_path, monkeypatch, fakes, capsys):
    signer, _ = fakes
    f = _moved_module_repo(tmp_path, 24, signed=False)
    configure(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["reanchor", "--apply", "--id", "24", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["fixed"][0]["signed"] is False
    assert NEW_MODULE in f.read_text() and "note:" in f.read_text()
    assert signer.calls() == []


def test_reanchor_already_stale_act_is_still_written_and_lint_reports_it(
        tmp_path, monkeypatch, fakes, capsys):
    signer, _ = fakes
    f = _moved_module_repo(tmp_path, 24, signed=False)
    f.write_text(f.read_text().replace(
        "\nstatus: active\n", f"\npromotion_act: {make_act('uOTHER')}\nstatus: active\n"))
    configure(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["reanchor", "--apply", "--id", "24", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["fixed"][0]["signed"] is False
    assert NEW_MODULE in f.read_text()
    assert signer.calls() == []
    assert main(["lint"]) == 1
    assert "scar resign --id 24" in capsys.readouterr().out


def test_reanchor_json_has_signed_and_touch_spent(moved, fakes, capsys):
    assert main(["reanchor", "--apply", "--id", "24", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) == {"fixed", "skipped", "refused"}
    assert data["fixed"][0]["signed"] is True and data["refused"] == []


def test_reanchor_json_touch_spent_is_true_when_the_checker_refuses(moved, fakes, capsys):
    _, checker = fakes
    checker.reply("check-act", ({"class": None, "reason": "fork", "receipt_id": "uEiX",
                                 "valid": False}, 0))
    assert main(["reanchor", "--apply", "--id", "24", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["fixed"] == []
    assert data["refused"][0]["touch_spent"] is True


def test_reanchor_json_refusal_without_touch_has_touch_spent_false(moved, fakes, capsys):
    signer, _ = fakes
    signer.reply("render", ({"error": "render_refused"}, 2))
    assert main(["reanchor", "--apply", "--id", "24", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["refused"][0]["touch_spent"] is False
    assert data["refused"][0]["scar_id"] == 24


def test_reanchor_a_scar_that_turned_signed_after_it_was_sorted_is_refused(
        moved, fakes, monkeypatch, capsys):
    before = moved.read_bytes()
    # sorted as unsigned; by the write the file is signed, and the gate refuses
    monkeypatch.setattr(signedpromote, "is_signed", lambda scar: False)
    assert main(["reanchor", "--apply", "--id", "24", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["fixed"] == []
    assert data["refused"][0]["touch_spent"] is False
    assert moved.read_bytes() == before


def test_reanchor_terminal_output_shows_signed(moved, fakes, capsys, monkeypatch):
    monkeypatch.setattr(output, "is_tty", lambda: True)
    assert main(["reanchor", "--apply", "--id", "24"]) == 0
    assert "fixed (signed) [#24]" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# orphan --fix-renames
# ---------------------------------------------------------------------------


def test_fix_renames_two_signed_one_unsigned(renamed, fakes, capsys):
    signer, _ = fakes
    assert main(["orphan", "--fix-renames", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {f["scar_id"]: f["signed"] for f in data["fixed"]} == {9: False, 10: True, 11: True}
    assert data["refused"] == []
    assert len(signer.calls("sign")) == 2
    for sid in (10, 11):
        assert "src/new_home.py" in renamed[sid].read_text() and act_matches(renamed[sid])
    assert "src/new_home.py" in renamed[9].read_text()


def test_fix_renames_plain_output_says_signed(renamed, fakes, capsys):
    assert main(["orphan", "--fix-renames"]) == 0
    out = capsys.readouterr().out
    assert "fixed [#9] renamed anchor src/old_home.py -> src/new_home.py\n" in out
    assert "fixed (signed) [#10]" in out and "fixed (signed) [#11]" in out
    assert "3 scar(s) fixed, 0 left unfixed" in out


def test_fix_renames_terminal_output_says_signed(renamed, fakes, capsys, monkeypatch):
    monkeypatch.setattr(output, "is_tty", lambda: True)
    assert main(["orphan", "--fix-renames"]) == 0
    assert "fixed (signed) [#10]" in capsys.readouterr().out


def test_fix_renames_preflight_precedes_the_first_touch(renamed, fakes, monkeypatch):
    order = spy_order(monkeypatch)
    assert main(["orphan", "--fix-renames"]) == 0
    first_sign = order.index("sign")
    assert order[:first_sign].count("render") == 2
    assert order[:first_sign].count("enrolled") == 1
    assert order.count("sign") == 2 and order.count("ingest") == 2


def test_fix_renames_unsigned_scars_are_fixed_first(tmp_path, monkeypatch, fakes):
    _git_repo_with_renamed_anchor(tmp_path)  # unsigned 9
    s5 = extra_renamed_scar(tmp_path, 5, signed=True)
    configure(tmp_path)
    monkeypatch.chdir(tmp_path)
    order = spy_order(monkeypatch)
    assert main(["orphan", "--fix-renames"]) == 0
    writes = [o for o in order if isinstance(o, tuple)]
    assert writes == [("write", "0009-renamed.deadend.md"), ("write", s5.name)]


def test_fix_renames_cancel_on_the_first_signed_scar_stops_asking(renamed, fakes, capsys):
    signer, _ = fakes
    signer.reply("sign", ({"error": "user_cancel"}, 1))
    before = {sid: renamed[sid].read_bytes() for sid in (10, 11)}
    assert main(["orphan", "--fix-renames", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert [f["scar_id"] for f in data["fixed"]] == [9]
    assert [r["scar_id"] for r in data["refused"]] == [10, 11]
    assert "cancelled" in data["refused"][0]["reason"]
    assert "not attempted" in data["refused"][1]["reason"]
    assert [r["touch_spent"] for r in data["refused"]] == [False, False]
    assert len(signer.calls("sign")) == 1
    for sid, raw in before.items():
        assert renamed[sid].read_bytes() == raw
    assert "src/new_home.py" in renamed[9].read_text()


def test_fix_renames_cancel_on_the_second_keeps_the_first_signed(renamed, fakes, capsys):
    signer, _ = fakes
    signer.reply("sign", {"out": '{"receipt_id": "%s"}' % RECEIPT, "code": 0,
                          "act_from_decision": True}, ({"error": "user_cancel"}, 1))
    before11 = renamed[11].read_bytes()
    assert main(["orphan", "--fix-renames", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert {f["scar_id"] for f in data["fixed"]} == {9, 10}
    assert [r["scar_id"] for r in data["refused"]] == [11]
    assert act_matches(renamed[10]) and "src/new_home.py" in renamed[10].read_text()
    assert renamed[11].read_bytes() == before11


def test_fix_renames_no_signer_refuses_signed_with_the_cause_and_fixes_unsigned(
        renamed, fakes, monkeypatch, tmp_path, capsys):
    signer, _ = fakes
    monkeypatch.setattr(presence, "SIGNER", str(tmp_path / "nope-sign"))
    before = {sid: renamed[sid].read_bytes() for sid in (10, 11)}
    assert main(["orphan", "--fix-renames", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert [f["scar_id"] for f in data["fixed"]] == [9]
    assert [r["scar_id"] for r in data["refused"]] == [10, 11]
    assert all("not installed at its fixed path" in r["reason"] for r in data["refused"])
    assert signer.calls("sign") == []
    for sid, raw in before.items():
        assert renamed[sid].read_bytes() == raw


def test_fix_renames_no_terminal_refuses_each_signed_scar(renamed, fakes, capsys):
    signer, _ = fakes
    signer.reply("sign", ({"error": "no_display"}, 4))
    before = {sid: renamed[sid].read_bytes() for sid in (10, 11)}
    assert main(["orphan", "--fix-renames", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert [f["scar_id"] for f in data["fixed"]] == [9]
    assert all("no_display" in r["reason"] for r in data["refused"])
    for sid, raw in before.items():
        assert renamed[sid].read_bytes() == raw


def test_fix_renames_render_refusal_on_one_scar_leaves_only_that_scar_refused(
        renamed, fakes, capsys):
    signer, _ = fakes
    signer.reply("render", ({"first_line": "l", "text": "t"}, 0),
                 ({"error": "render_refused"}, 2))
    before11 = renamed[11].read_bytes()
    assert main(["orphan", "--fix-renames", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert [r["scar_id"] for r in data["refused"]] == [11]
    assert "the signer cannot show this record" in data["refused"][0]["reason"]
    assert {f["scar_id"] for f in data["fixed"]} == {9, 10}
    assert len(signer.calls("sign")) == 1
    assert renamed[11].read_bytes() == before11


def test_fix_renames_checker_refusal_on_one_scar_continues_with_the_next(renamed, fakes,
                                                                          capsys):
    _, checker = fakes
    checker.reply("check-act",
                  ({"class": None, "reason": "fork", "receipt_id": "uEiX", "valid": False}, 0),
                  ({"class": "apple-se-bio", "reason": "ok", "receipt_id": RECEIPT,
                    "valid": True}, 0))
    before10 = renamed[10].read_bytes()
    assert main(["orphan", "--fix-renames", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert [r["scar_id"] for r in data["refused"]] == [10]
    assert data["refused"][0]["touch_spent"] is True
    assert {f["scar_id"] for f in data["fixed"]} == {9, 11}
    assert renamed[10].read_bytes() == before10
    assert act_matches(renamed[11])


def test_fix_renames_challenged_signed_scar_is_refused_naming_the_status(renamed, fakes,
                                                                          capsys):
    signer, _ = fakes
    renamed[10].write_text(renamed[10].read_text().replace("status: active",
                                                           "status: challenged"))
    before = renamed[10].read_bytes()
    assert main(["orphan", "--fix-renames", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["refused"][0]["scar_id"] == 10
    assert "challenged" in data["refused"][0]["reason"]
    assert {f["scar_id"] for f in data["fixed"]} == {9, 11}
    assert len(signer.calls("sign")) == 1
    assert renamed[10].read_bytes() == before


def test_fix_renames_config_problem_refuses_signed_and_fixes_unsigned(renamed, fakes, capsys):
    signer, _ = fakes
    configure(renamed[9].parent.parent, "{bad")
    assert main(["orphan", "--fix-renames", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert [f["scar_id"] for f in data["fixed"]] == [9]
    assert [r["scar_id"] for r in data["refused"]] == [10, 11]
    assert all("config.json" in r["reason"] for r in data["refused"])
    assert signer.calls() == []


def test_fix_renames_a_signed_scar_whose_anchor_line_does_not_change_is_left_unfixed(
        renamed, fakes, monkeypatch, capsys):
    signer, _ = fakes
    monkeypatch.setattr(cli, "anchor_rewrite_text", lambda text, kind, renamed: text)
    before = {sid: renamed[sid].read_bytes() for sid in (10, 11)}
    assert main(["orphan", "--fix-renames", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [s["scar_id"] for s in data["skipped"]] == [10, 11]
    assert signer.calls() == []
    for sid, raw in before.items():
        assert renamed[sid].read_bytes() == raw


def test_fix_renames_a_crlf_signed_scar_keeps_its_line_endings(tmp_path, monkeypatch, fakes):
    _git_repo_with_renamed_anchor(tmp_path)
    s10 = extra_renamed_scar(tmp_path, 10, signed=True)
    s10.write_bytes(s10.read_bytes().replace(b"\n", b"\r\n"))
    configure(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["orphan", "--fix-renames"]) == 0
    raw = s10.read_bytes().decode()
    assert "\r\n" in raw and "\n" not in raw.replace("\r\n", "")
    assert "src/new_home.py" in raw and act_matches(s10)


def test_fix_renames_a_scar_that_turned_signed_after_it_was_sorted_is_refused(
        tmp_path, monkeypatch, fakes, capsys):
    _git_repo_with_renamed_anchor(tmp_path)
    p = tmp_path / ".scars" / "0009-renamed.deadend.md"
    sign(p)
    signed_bytes = p.read_bytes()
    monkeypatch.chdir(tmp_path)
    # the plan sorted it as unsigned; by the time of the write it is signed
    monkeypatch.setattr(signedpromote, "is_signed", lambda scar: False)
    assert main(["orphan", "--fix-renames", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert [r["scar_id"] for r in data["refused"]] == [9]
    assert data["refused"][0]["touch_spent"] is False
    assert p.read_bytes() == signed_bytes


# ---------------------------------------------------------------------------
# The pieces the tools are built from
# ---------------------------------------------------------------------------


def test_anchor_rewrite_text_builds_the_text_and_writes_nothing(tmp_path):
    f = tmp_path / "s.md"
    text = "---\nid: 1\nanchors:\n  - path: src/a.py  # keep\n---\n\nBody.\n"
    f.write_text(text)
    out = renames.anchor_rewrite_text(text, "path", {"src/a.py": "src/b.py"})
    assert out == text.replace("src/a.py", "src/b.py")
    assert f.read_text() == text


@pytest.mark.parametrize("kind,renamed", [("path", {}), ("pattern", {"src/a.py": "x"}),
                                          ("path", {"src/zzz.py": "src/b.py"})])
def test_anchor_rewrite_text_returns_the_text_when_nothing_changes(kind, renamed):
    text = "---\nid: 1\nanchors:\n  - path: src/a.py\n---\n\nBody.\n"
    assert renames.anchor_rewrite_text(text, kind, renamed) == text


def test_with_evidence_note_matches_what_append_evidence_note_writes(tmp_path):
    from scar.store import ScarStore, init_scars, with_evidence_note
    init_scars(tmp_path)
    f = tmp_path / ".scars" / "0001-s.fence.md"
    f.write_text("---\nid: 1\ntype: fence\ntitle: T\nseverity: low\nconfidence: 0.5\n"
                 "created: 2026-11-01\nauthors: [\"claude-code\"]\nanchors:\n  - path: src/x.py\n"
                 "evidence:\n  - issue: 5\nstatus: active\n---\n\nBody.\n")
    before = f.read_text()
    built = with_evidence_note(before, "note: pinned")
    assert f.read_text() == before
    ScarStore(tmp_path, tmp_path / ".scars").append_evidence_note(1, "note: pinned")
    assert f.read_text() == built
    assert "note: pinned" in built and "issue: 5" in built


def test_is_signed_is_true_only_for_an_act_that_matches(tmp_path):
    from test_write_gate import write_scar
    from scar.store import init_scars
    init_scars(tmp_path)
    expect = {"matching": True, "stale": False, "not-a-jws": False, None: False}
    for i, (act, want) in enumerate(expect.items(), start=1):
        p = write_scar(tmp_path, i, act=act)
        assert signedpromote.is_signed(parse_scar_text(p.read_text())) is want


def test_new_record_checks_refuses_text_that_does_not_parse(tmp_path, fakes):
    from scar.store import ScarStore, init_scars
    init_scars(tmp_path)
    store = ScarStore(tmp_path, tmp_path / ".scars")
    with pytest.raises(signedpromote.SignedPromoteError) as exc:
        signedpromote.new_record_checks(store, ORG, tmp_path / "s.md", "not a scar", {})
    assert "does not parse" in str(exc.value)
