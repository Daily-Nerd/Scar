"""--verify-promotion: each active scar's promotion act, checked by the fixed
external verifier (#386). The verifier is a fake script at a test-only path."""
import base64
import json
import subprocess
import sys

import pytest

from scar import presence, verify
from scar.cli import main
from scar.model import parse_scar_text
from scar.record import promoted_record, promotion_subject
from scar.store import ScarStore, init_scars

SCAR = """---
id: 1
type: fence
title: Keep the odd retry loop
severity: high
confidence: 0.7
created: 2026-11-01
authors: ["kibukx"]
promoted_by: alice
{act}anchors:
  - path: src/x.py
status: active
---

Body text.
"""


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit",
        "-q", "--allow-empty", "-m", "root")
    init_scars(tmp_path)
    (tmp_path / ".scars" / "config.json").write_text(json.dumps({"org": "org-1", "signed_from": 1}))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write_scar(repo, act="JWS.A.B", body_extra=""):
    line = f"promotion_act: {act}\n" if act else ""
    p = repo / ".scars" / "0001-keep.fence.md"
    p.write_text(SCAR.format(act=line).rstrip("\n") + body_extra + "\n")
    return p


@pytest.fixture
def fake(tmp_path_factory, monkeypatch):
    d = tmp_path_factory.mktemp("fake")
    out = d / "stdin.json"
    path = d / "handsal-check"

    def install(reply='{"class":"x","not_proven":["expiry","freshness","order_of_arrival","replay","revocation_after_head"],"reason":"","receipt_id":"r","rechecked":true}',
                code=0, sleep=0):
        path.write_text(
            f"#!{sys.executable}\nimport sys, time\n"
            f"open({str(out)!r}, 'w').write(sys.stdin.read())\n"
            f"open({str(out)!r} + '.argv', 'w').write(' '.join(sys.argv[1:]))\n"
            f"time.sleep({sleep})\nprint({reply!r})\nsys.exit({code})\n")
        path.chmod(0o755)
        monkeypatch.setattr(presence, "CHECKER", str(path))
        return out
    return install


def states(repo):
    store = ScarStore.discover(repo)
    return [s for _f, _sc, s in verify.verify_store(store)]


def test_no_act_is_unverified_even_with_promoted_by(repo, fake):
    fake()
    write_scar(repo, act="")
    assert states(repo) == ["unverified"]


def test_stdin_has_exactly_the_expected_keys_and_values(repo, fake):
    out = fake()
    p = write_scar(repo)
    states(repo)
    sent = json.loads(out.read_text())
    scar = parse_scar_text(p.read_text())
    assert sorted(sent) == ["action", "consumer", "decision_b64", "expect_method",
                            "jws", "org", "subject"]
    assert sent["action"] == "promote" and sent["consumer"] == "scar"
    assert sent["expect_method"] == "local:handsal.decision"
    assert sent["jws"] == "JWS.A.B"
    assert sent["org"] == "org-1"
    assert base64.b64decode(sent["decision_b64"]) == promoted_record(scar)
    assert sent["subject"] == promotion_subject(repo, 1)
    assert (out.parent / "stdin.json.argv").read_text() == "recheck-act"


def test_rechecked_true_is_verified(repo, fake):
    fake()
    write_scar(repo)
    assert states(repo) == ["verified"]


def test_rechecked_false_shows_reason_verbatim(repo, fake):
    fake('{"class":"c","not_proven":["expiry","freshness","order_of_arrival","replay","revocation_after_head"],"reason":"weird_reason_code","receipt_id":"","rechecked":false}')
    write_scar(repo)
    assert states(repo) == ["not proven: weird_reason_code"]


@pytest.mark.parametrize("reply,code", [
    ("not json", 0),
    ('{"error":"boom"}', 0),
    ('{"rechecked":true}', 3),
    ("[1]", 0),
    ('{"class":"x"}', 0),
])
def test_failure_modes_never_verified(repo, fake, reply, code):
    fake(reply, code=code)
    write_scar(repo)
    assert states(repo) == ["not verified: verifier failed"]


def test_timeout_is_verifier_failed(repo, fake, monkeypatch):
    fake(sleep=3)
    monkeypatch.setattr(verify, "TIMEOUT_SECONDS", 0.5)
    write_scar(repo)
    assert states(repo) == ["not verified: verifier failed"]


def test_missing_verifier(repo, monkeypatch, tmp_path):
    monkeypatch.setattr(presence, "CHECKER", str(tmp_path / "nope"))
    write_scar(repo)
    assert states(repo) == ["not verified: verifier not installed"]


def test_verifier_default_is_the_fixed_path():
    assert not hasattr(verify, "VERIFIER")  # one owner: presence.CHECKER
    assert presence.CHECKER == "/Library/Application Support/handsal/bin/handsal-check"


@pytest.mark.parametrize("content", [None, "{bad", "[]", "{}", '{"org": ""}', '{"org": 5}'])
def test_config_problems(repo, fake, content):
    fake()
    cfg = repo / ".scars" / "config.json"
    if content is None:
        cfg.unlink()
    else:
        cfg.write_text(content)
    write_scar(repo)
    assert states(repo) == ["not verified: no org in .scars/config.json"]


def test_no_root_commit(tmp_path, monkeypatch, fake):
    fake()
    git(tmp_path, "init", "-q")  # no commits
    init_scars(tmp_path)
    (tmp_path / ".scars" / "config.json").write_text('{"org": "o"}')
    write_scar(tmp_path)
    assert states(tmp_path) == ["not verified: repository root commit unavailable"]


def test_body_edit_changes_bytes_sent(repo, fake):
    out = fake()
    write_scar(repo)
    states(repo)
    first = json.loads(out.read_text())["decision_b64"]
    write_scar(repo, body_extra="\nEdited.")
    states(repo)
    assert json.loads(out.read_text())["decision_b64"] != first


@pytest.mark.parametrize("status", ["orphaned", "archived"])
def test_non_injecting_scars_are_not_listed(repo, fake, status):
    fake()
    p = write_scar(repo)
    p.write_text(p.read_text().replace("status: active", f"status: {status}"))
    assert states(repo) == []


def test_challenged_scar_with_act_is_checked(repo, fake):
    out = fake()
    p = write_scar(repo)
    p.write_text(p.read_text().replace("status: active", "status: challenged"))
    assert states(repo) == ["verified"]
    assert json.loads(out.read_text())["jws"] == "JWS.A.B"


def test_challenged_scar_without_act_is_unverified(repo, fake):
    fake()
    p = write_scar(repo, act="")
    p.write_text(p.read_text().replace("status: active", "status: challenged"))
    assert states(repo) == ["unverified"]


def test_promotion_subject_stable_and_tracks_root(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit",
        "-q", "--allow-empty", "-m", "a")
    a = promotion_subject(tmp_path, 7)
    assert a == promotion_subject(tmp_path, 7)
    root = subprocess.run(["git", "-C", str(tmp_path), "rev-list", "--first-parent",
                           "--max-parents=0", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert a == f"7-{root[:16]}"
    other = tmp_path / "o"
    other.mkdir()
    git(other, "init", "-q")
    git(other, "-c", "user.name=t", "-c", "user.email=t@t", "commit",
        "-q", "--allow-empty", "-m", "b", "--date=2001-01-01T00:00:00")
    assert promotion_subject(other, 7) != a


def test_promotion_subject_none_without_repo(tmp_path):
    assert promotion_subject(tmp_path, 1) is None


def test_promotion_act_parsed_and_excluded_from_record():
    with_act = parse_scar_text(SCAR.format(act="promotion_act: AAA.BBB.CCC\n"))
    without = parse_scar_text(SCAR.format(act=""))
    assert with_act.promotion_act == "AAA.BBB.CCC" and without.promotion_act == ""
    assert promoted_record(with_act) == promoted_record(without)
    assert b"AAA" not in promoted_record(with_act)


def test_promotion_act_survives_lifecycle_transition(repo):
    p = write_scar(repo)
    ScarStore.discover(repo).transition(1, "challenged", "why", "2026-11-15")
    assert parse_scar_text(p.read_text()).promotion_act == "JWS.A.B"


def test_default_lint_unchanged(repo, fake, capsys):
    fake()
    write_scar(repo)
    assert main(["lint"]) == 0
    out = capsys.readouterr().out
    assert "promotion" not in out and "verified" not in out


def test_flag_reports_state_and_never_changes_exit(repo, fake, capsys):
    fake('{"class":"c","not_proven":["expiry","freshness","order_of_arrival","replay","revocation_after_head"],"reason":"nope","receipt_id":"","rechecked":false}')
    write_scar(repo)
    assert main(["lint"]) == 0
    capsys.readouterr()
    assert main(["lint", "--verify-promotion"]) == 0
    out = capsys.readouterr().out
    assert "scar #1: not proven: nope" in out


def test_flag_json_carries_promotion(repo, fake, capsys):
    fake()
    write_scar(repo)
    assert main(["lint", "--verify-promotion", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["promotion"] == [{"scar_id": 1, "file": ".scars/0001-keep.fence.md",
                                  "state": "verified"}]


def test_json_without_flag_has_no_promotion_key(repo, capsys):
    assert main(["lint", "--json"]) == 0
    assert "promotion" not in json.loads(capsys.readouterr().out)
