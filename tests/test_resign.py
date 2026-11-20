"""scar resign (#425): sign an active scar's record as it stands now.

Everything runs against the strict fake signer and checker at tmp paths. The
real programs are never run.
"""
import base64
import json
import subprocess

import pytest
from presence_fakes import FakeBinary

from scar import output, presence, signedpromote, store as store_module
from scar.cli import main
from scar.model import parse_scar_text
from scar.record import promoted_record, promotion_subject, record_hash
from scar.signedpromote import with_act
from scar.store import SignedRecordWouldChange, init_scars

ORG = "uEiARDVf_JDy-0rOjSM1lEH6YMSEdD2RQiuviL-AsSVLq4A"
HEAD = {"id": "uEiBheadheadheadheadheadheadheadheadheadheadhead", "kind": "chain"}
RECEIPT = "uEiReceipt0000000000000000000000000000000000"

BODY = """\
type: deadend
title: Tried X, failed
severity: medium
confidence: 0.7
created: 2026-06-10
authors: ["claude-code"]
{act}anchors:
  - path: src/
evidence:
  - commit: abc1234
status: {status}
---

Why X failed.
"""


def make_act(inputs_hash):
    payload = base64.urlsafe_b64encode(
        json.dumps({"inputs_hash": inputs_hash}).encode()).rstrip(b"=").decode()
    return f"h.{payload}.s"


def scar_text(*, act="", status="active", sid=1, eol="\n"):
    text = f"---\nid: {sid}\n" + BODY.format(act=act, status=status)
    return text.replace("\n", eol)


STALE = f"promotion_act: {make_act('uOTHER')}\npromotion_receipt: uEiOld\n"
UNREADABLE = "promotion_act: not-a-jws\npromotion_receipt: uEiOld\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "root"], check=True)
    init_scars(tmp_path)
    (tmp_path / ".scars" / "config.json").write_text(
        json.dumps({"org": ORG, "signed_from": 1}))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def fakes(tmp_path_factory, monkeypatch):
    d = tmp_path_factory.mktemp("bin")
    signer = FakeBinary(d, "handsal-sign")
    checker = FakeBinary(d, "handsal-check")
    monkeypatch.setattr(presence, "SIGNER", str(signer.path))
    monkeypatch.setattr(presence, "CHECKER", str(checker.path))
    checker.reply("head", ({"head": HEAD, "reason": "ok"}, 0))
    checker.reply("enrolled", ({"enrolled": True, "reason": "ok"}, 0))
    checker.reply("check-act", ({"class": "apple-se-bio", "reason": "ok",
                                 "receipt_id": RECEIPT, "valid": True}, 0))
    signer.reply("pubkey", ({"kid": "KID43", "class": "apple-se-bio",
                             "agree": {"x": "a", "y": "b"}, "sign": {"x": "c", "y": "d"}}, 0))
    signer.reply("render", ({"first_line": "l", "text": "t"}, 0))
    signer.reply("sign", {"out": '{"receipt_id": "%s"}' % RECEIPT, "code": 0,
                          "act_from_decision": True})
    return signer, checker


def put(repo, text, name="0001-tried-x.deadend.md"):
    p = repo / ".scars" / name
    p.write_text(text, newline="")
    return p


def snapshot(repo):
    return {p: p.read_bytes() for p in (repo / ".scars").rglob("*") if p.is_file()}


def lint(capsys):
    capsys.readouterr()
    rc = main(["lint"])
    return rc, capsys.readouterr().out


def other_lines(text):
    return [line for line in text.splitlines() if not line.startswith("promotion_")]


@pytest.mark.parametrize("act", [STALE, UNREADABLE, ""], ids=["stale", "unreadable", "never"])
def test_resign_signs_the_record_as_it_stands(repo, fakes, capsys, act):
    p = put(repo, scar_text(act=act))
    before = p.read_text()
    assert main(["resign", "--id", "1"]) == 0
    after = p.read_text()
    scar = parse_scar_text(after)
    assert presence.act_inputs_hash(scar.promotion_act) == record_hash(scar)
    assert scar.promotion_receipt == RECEIPT
    assert other_lines(after) == other_lines(before)
    assert after.count("promotion_act:") == 1 and after.count("promotion_receipt:") == 1
    rc, out = lint(capsys)
    assert rc == 0, out


def test_the_signer_is_shown_the_record_on_disk(repo, fakes):
    signer, _ = fakes
    p = put(repo, scar_text(act=STALE))
    record = promoted_record(parse_scar_text(p.read_text()))
    assert main(["resign", "--id", "1"]) == 0
    (sign_call,) = signer.calls("sign")
    sent = sign_call["stdin_json"]
    assert sent["draft"]["body"]["subject"] == promotion_subject(repo, 1)
    assert base64.b64decode(sent["attachments"]["decision_b64"]) == record
    (render_call,) = signer.calls("render")
    assert render_call["stdin_json"] == sent


def test_call_order_is_preflight_head_sign_check_write(repo, fakes, monkeypatch):
    put(repo, scar_text(act=STALE))
    order = []
    for name in ("enrolled", "render", "head", "sign", "ingest"):
        real = getattr(presence, name)

        def spy(*args, _real=real, _name=name):
            order.append(_name)
            return _real(*args)

        monkeypatch.setattr(presence, name, spy)
    real_write = store_module.write_active_scar

    def write_spy(path, text):
        order.append("write")
        real_write(path, text)

    monkeypatch.setattr(signedpromote, "write_active_scar", write_spy)
    assert main(["resign", "--id", "1"]) == 0
    assert order == ["head", "enrolled", "render", "head", "sign", "ingest", "write"]


def test_crlf_files_keep_their_line_endings(repo, fakes):
    p = put(repo, scar_text(act="", eol="\r\n"))
    assert main(["resign", "--id", "1"]) == 0
    raw = p.read_bytes().decode()
    assert "\r\n" in raw and "\n" not in raw.replace("\r\n", "")
    scar = parse_scar_text(raw)
    assert presence.act_inputs_hash(scar.promotion_act) == record_hash(scar)


def test_an_act_that_already_matches_is_refused_before_any_touch(repo, fakes, capsys):
    signer, _ = fakes
    put(repo, scar_text(act=""))
    assert main(["resign", "--id", "1"]) == 0
    before = snapshot(repo)
    signs = len(signer.calls("sign"))
    capsys.readouterr()
    assert main(["resign", "--id", "1"]) == 1
    assert "nothing to sign" in capsys.readouterr().out
    assert len(signer.calls("sign")) == signs
    assert snapshot(repo) == before


def test_an_archived_scar_is_refused(repo, fakes, capsys):
    signer, _ = fakes
    put(repo, scar_text(act=STALE, status="archived"))
    before = snapshot(repo)
    assert main(["resign", "--id", "1"]) == 1
    out = capsys.readouterr().out
    assert "scar #1 is archived" in out and "only an active scar" in out
    assert signer.calls() == []
    assert snapshot(repo) == before


def test_an_unknown_id_is_refused(repo, fakes, capsys):
    signer, _ = fakes
    put(repo, scar_text())
    assert main(["resign", "--id", "7"]) == 1
    assert "no scar with id 7" in capsys.readouterr().out
    assert signer.calls() == []


def test_a_candidate_is_refused(repo, fakes, capsys):
    signer, _ = fakes
    cand = repo / ".scars" / "candidates" / "tried-x.md"
    cand.write_text(scar_text(status="candidate"))
    before = snapshot(repo)
    assert main(["resign", "--id", "1"]) == 1
    assert "no scar with id 1" in capsys.readouterr().out
    assert signer.calls() == []
    assert snapshot(repo) == before


def test_no_org_is_refused(repo, fakes, capsys):
    signer, _ = fakes
    (repo / ".scars" / "config.json").unlink()
    put(repo, scar_text(act=STALE))
    before = snapshot(repo)
    assert main(["resign", "--id", "1"]) == 1
    assert "refusing to resign: no org in .scars/config.json" in capsys.readouterr().out
    assert signer.calls() == []
    assert snapshot(repo) == before


def test_an_unreadable_config_is_refused_never_read_as_no_org(repo, fakes, capsys):
    (repo / ".scars" / "config.json").write_text("{bad")
    put(repo, scar_text(act=STALE))
    assert main(["resign", "--id", "1"]) == 1
    out = capsys.readouterr().out
    assert "refusing to resign:" in out and "config.json" in out


def test_a_config_lint_would_refuse_is_refused(repo, fakes, capsys):
    (repo / ".scars" / "config.json").write_text(json.dumps({"org": ORG}))
    put(repo, scar_text(act=STALE))
    assert main(["resign", "--id", "1"]) == 1
    assert "refusing to resign:" in capsys.readouterr().out


def test_a_cancelled_touch_leaves_the_file_alone(repo, fakes, capsys):
    signer, checker = fakes
    signer.reply("sign", ({"error": "user_cancel"}, 1))
    put(repo, scar_text(act=STALE))
    before = snapshot(repo)
    assert main(["resign", "--id", "1"]) == 1
    assert "cancelled at 0001-tried-x.deadend.md" in capsys.readouterr().out
    assert checker.calls("check-act") == []
    assert snapshot(repo) == before


def test_no_terminal_leaves_the_file_alone(repo, fakes, capsys):
    signer, checker = fakes
    signer.reply("sign", ({"error": "no_display"}, 4))
    put(repo, scar_text(act=STALE))
    before = snapshot(repo)
    assert main(["resign", "--id", "1"]) == 1
    assert "no_display" in capsys.readouterr().out
    assert checker.calls("check-act") == []
    assert snapshot(repo) == before


def test_a_missing_signer_asks_for_no_touch(repo, fakes, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(presence, "SIGNER", str(tmp_path / "nope-sign"))
    put(repo, scar_text(act=STALE))
    before = snapshot(repo)
    assert main(["resign", "--id", "1"]) == 1
    out = capsys.readouterr().out
    assert "cannot sign: not installed at its fixed path" in out
    assert "no touch was requested" in out
    assert snapshot(repo) == before


def test_a_record_the_signer_cannot_show_asks_for_no_touch(repo, fakes, capsys):
    signer, _ = fakes
    signer.reply("render", ({"error": "render_refused"}, 2))
    put(repo, scar_text(act=STALE))
    before = snapshot(repo)
    assert main(["resign", "--id", "1"]) == 1
    out = capsys.readouterr().out
    assert "0001-tried-x.deadend.md: the signer cannot show this record" in out
    assert "no touch was requested" in out
    assert signer.calls("sign") == []
    assert snapshot(repo) == before


def test_a_checker_refusal_after_the_touch_says_so_and_writes_nothing(repo, fakes, capsys):
    signer, checker = fakes
    checker.reply("check-act", ({"class": None, "reason": "validity_too_long",
                                 "receipt_id": "uEiX", "valid": False}, 0))
    put(repo, scar_text(act=STALE))
    before = snapshot(repo)
    assert main(["resign", "--id", "1"]) == 1
    out = capsys.readouterr().out
    assert "touch was spent" in out and "validity_too_long" in out
    assert len(signer.calls("sign")) == 1
    assert snapshot(repo) == before


def test_a_file_that_changed_during_the_touch_is_not_overwritten(repo, fakes, monkeypatch, capsys):
    p = put(repo, scar_text(act=STALE))
    real_sign = presence.sign

    def sign_then_edit(request):
        result = real_sign(request)
        p.write_text(p.read_text().replace("Why X failed.", "Edited meanwhile."))
        return result

    monkeypatch.setattr(presence, "sign", sign_then_edit)
    assert main(["resign", "--id", "1"]) == 1
    out = capsys.readouterr().out
    assert "touch was spent" in out and "changed while it was being signed" in out
    assert "Edited meanwhile." in p.read_text()
    assert RECEIPT not in p.read_text()


def test_a_refusal_from_the_write_gate_after_the_touch_says_so(repo, fakes, monkeypatch, capsys):
    p = put(repo, scar_text(act=STALE))
    before = p.read_bytes()

    def refuse(path, text):
        raise SignedRecordWouldChange(1)

    monkeypatch.setattr(signedpromote, "write_active_scar", refuse)
    assert main(["resign", "--id", "1"]) == 1
    out = capsys.readouterr().out
    assert "touch was spent" in out and "nothing was written" in out
    assert p.read_bytes() == before


def test_signer_input_is_strict_and_in_the_canonical_subset(repo, fakes):
    # presence_fakes stays strict: the signer's stdin must be JSON with string
    # values only (scar 0030). The fake would answer unsupported otherwise.
    signer, _ = fakes
    put(repo, scar_text(act=STALE))
    assert main(["resign", "--id", "1"]) == 0
    draft = signer.calls("sign")[0]["stdin_json"]["draft"]
    assert draft["expires_in_s"] == "3600"
    assert all(isinstance(v, (str, dict)) for v in draft.values())


def test_plain_output_names_the_file_and_the_outcome(repo, fakes, capsys):
    put(repo, scar_text(act=STALE))
    assert main(["resign", "--id", "1"]) == 0
    out = capsys.readouterr().out
    assert "signed -> .scars/0001-tried-x.deadend.md" in out
    assert "a person's presence was proven" in out


def test_json_output_is_one_object(repo, fakes, capsys):
    put(repo, scar_text(act=STALE))
    assert main(["resign", "--id", "1", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {"signed": True, "id": 1, "path": ".scars/0001-tried-x.deadend.md"}


def test_json_refusal_is_one_object_and_exit_1(repo, fakes, capsys):
    put(repo, scar_text(act=STALE, status="archived"))
    assert main(["resign", "--id", "1", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["signed"] is False and data["id"] == 1
    assert "archived" in data["reason"] and data["touch_spent"] is False


def test_json_touch_spent_is_reported(repo, fakes, capsys):
    _, checker = fakes
    checker.reply("check-act", ({"class": None, "reason": "fork",
                                 "receipt_id": "uEiX", "valid": False}, 0))
    put(repo, scar_text(act=STALE))
    assert main(["resign", "--id", "1", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["touch_spent"] is True


def test_terminal_output_for_success_and_refusal(repo, fakes, capsys, monkeypatch):
    monkeypatch.setattr(output, "is_tty", lambda: True)
    put(repo, scar_text(act=STALE))
    assert main(["resign", "--id", "1"]) == 0
    assert "signed" in capsys.readouterr().out
    assert main(["resign", "--id", "1"]) == 1
    assert "matches its record" in capsys.readouterr().out


def test_terminal_output_for_a_preflight_failure(repo, fakes, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(output, "is_tty", lambda: True)
    monkeypatch.setattr(presence, "SIGNER", str(tmp_path / "nope-sign"))
    put(repo, scar_text(act=STALE))
    assert main(["resign", "--id", "1"]) == 1
    out = capsys.readouterr().out
    assert "cannot sign" in out and "no touch was requested" in out


# with_act: the surgical rewrite of the two lines.

def test_with_act_replaces_both_lines_in_place():
    text = scar_text(act=STALE)
    out = with_act(text, "new.act.jws", "uEiNew")
    assert out.splitlines().index("promotion_act: new.act.jws") == \
        text.splitlines().index(STALE.splitlines()[0])
    assert other_lines(out) == other_lines(text)


def test_with_act_inserts_before_anchors():
    out = with_act(scar_text(), "a.b.c", "uEiR").splitlines()
    assert out[out.index("anchors:") - 2:out.index("anchors:")] == [
        "promotion_act: a.b.c", "promotion_receipt: uEiR"]


def test_with_act_appends_when_there_are_no_anchors():
    text = "---\nid: 1\ntype: fence\ntitle: T\nstatus: active\n---\n\nBody.\n"
    out = with_act(text, "a.b.c", "uEiR")
    assert out == ("---\nid: 1\ntype: fence\ntitle: T\nstatus: active\n"
                   "promotion_act: a.b.c\npromotion_receipt: uEiR\n---\n\nBody.\n")


def test_with_act_never_touches_the_body():
    text = scar_text(act="").replace("Why X failed.", "promotion_act: in the body\n")
    out = with_act(text, "a.b.c", "uEiR")
    assert out.endswith("promotion_act: in the body\n\n")


def test_with_act_refuses_text_with_no_frontmatter():
    with pytest.raises(ValueError):
        with_act("no frontmatter", "a.b.c", "uEiR")


# The lint remedy.

def test_lint_names_the_remedy_for_a_stale_act(repo, capsys):
    put(repo, scar_text(act=STALE))
    rc, out = lint(capsys)
    assert rc == 1 and "run scar resign --id 1" in out


def test_lint_names_the_remedy_for_an_unreadable_act(repo, capsys):
    put(repo, scar_text(act=UNREADABLE))
    rc, out = lint(capsys)
    assert rc == 1 and "cannot be read" in out and "run scar resign --id 1" in out


def test_lint_does_not_offer_resign_for_an_archived_scar(repo, capsys):
    put(repo, scar_text(act=STALE, status="archived"))
    rc, out = lint(capsys)
    assert "new signature" in out and "scar resign" not in out


def test_resign_is_registered_through_add():
    from scar import cli
    src = (__import__("pathlib").Path(cli.__file__)).read_text()
    assert '_add(sub, "resign", _cmd_resign' in src


def test_a_write_that_would_change_the_record_asks_for_no_touch(repo, fakes, capsys):
    # An empty `severity:` reads the next line as its value (the parser's
    # field pattern spans the newline), so taking the act lines out would
    # change the record the person is about to approve.
    signer, _ = fakes
    put(repo, "---\nid: 1\ntype: deadend\ntitle: T\nseverity:\n" + STALE
        + "anchors:\n  - path: src/\nstatus: active\n---\n\nBody.\n")
    before = snapshot(repo)
    assert main(["resign", "--id", "1"]) == 1
    out = capsys.readouterr().out
    assert "would change its record" in out
    assert signer.calls("sign") == []
    assert snapshot(repo) == before


def test_outside_a_scars_repo_resign_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["resign", "--id", "1"]) == 1
    assert "no .scars/ directory" in capsys.readouterr().out


def test_lint_names_the_remedy_for_a_never_signed_active_scar(repo, capsys):
    put(repo, scar_text(act=""))
    rc, out = lint(capsys)
    assert rc == 1
    assert "carries no promotion_act; run scar resign --id 1" in out


def test_lint_does_not_offer_resign_for_an_archived_scar_without_an_act(repo, capsys):
    put(repo, scar_text(act="", status="archived"))
    rc, out = lint(capsys)
    assert "carries no promotion_act" in out and "scar resign" not in out
