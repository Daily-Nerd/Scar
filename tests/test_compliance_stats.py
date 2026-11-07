"""#373: the compliance result (F, V, rate, dominant-scar cut, verdict
completeness) is computed by `scar stats` itself, and a repository set is a
scope filter applied to RECORDS before any metric, exactly like --since."""
import json

import pytest

from scar.cli import main

KEYS = {"F", "V", "rate", "violations_unmatched", "verdicts_expected",
        "verdicts_observed", "verdicts_resolved", "verdict_completeness",
        "top_scar"}


def _fire(repo, ts, target, scar_ids, armed, edit_id=None):
    rec = {"ts": ts, "repo": str(repo), "target": target,
           "scar_ids": scar_ids, "count": len(scar_ids), "armed_ids": armed}
    if edit_id:
        rec["edit_id"] = edit_id
        rec["verdict_expected"] = True
    return rec


def _verdict(repo, ts, target, violation_ids, edit_id):
    return {"ts": ts, "repo": str(repo), "target": target,
            "violation_ids": violation_ids, "count": len(violation_ids),
            "verdict_observed": True, "edit_id": edit_id}


def _repo_rows(repo, day="2026-08-30"):
    """4 armed firings (scar 1 x3, scar 2 x1). One violation of scar 1, one
    clean verdict, one edit with no verdict yet."""
    return [
        _fire(repo, f"{day}T09:00:00", "a.py", [1], [1], "e1"),
        _fire(repo, f"{day}T09:01:00", "b.py", [1, 2], [1, 2], "e2"),
        _fire(repo, f"{day}T09:02:00", "c.py", [1], [1], "e3"),
        _verdict(repo, f"{day}T09:00:30", "a.py", [1], "e1"),
        _verdict(repo, f"{day}T09:01:30", "b.py", [], "e2"),
    ]


@pytest.fixture
def env(tmp_path, monkeypatch):
    for n in ("repo-a", "repo-b", "repo-c"):
        (tmp_path / n).mkdir()
    monkeypatch.setenv("SCAR_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path / "repo-a")
    return {n: (tmp_path / n).resolve() for n in ("repo-a", "repo-b", "repo-c")}


def _log(tmp_path, records):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    with (state / "firing-log.jsonl").open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def _run(capsys, *argv):
    capsys.readouterr()
    rc = main(["stats", "--json", *argv])
    return rc, capsys.readouterr()


def test_totals_equal_the_sums_of_the_existing_fields(env, tmp_path, capsys):
    a = env["repo-a"]
    (a / ".scars").mkdir()
    _log(tmp_path, _repo_rows(a))
    rc, out = _run(capsys)
    assert rc == 0
    data = json.loads(out.out)
    c = data["compliance"]
    assert KEYS <= c.keys()
    assert c["F"] == data["armed_firings"] == 4
    assert c["V"] == sum(e["violations"] for e in data["per_scar"]) == 1
    assert c["rate"] == 0.25
    assert c["verdicts_expected"] == data["verdicts_expected"] == 3
    assert c["verdicts_observed"] == data["verdicts_observed"] == 2
    assert c["verdicts_resolved"] == 2
    assert c["verdict_completeness"] == round(2 / 3, 3)


def test_rate_is_null_never_zero_when_there_are_no_armed_firings(env, tmp_path, capsys):
    a = env["repo-a"]
    (a / ".scars").mkdir()
    _log(tmp_path, [_fire(a, "2026-08-30T09:00:00", "a.py", [1], [], "e1")])
    c = json.loads(_run(capsys)[1].out)["compliance"]
    assert c["F"] == 0 and c["V"] == 0
    assert c["rate"] is None
    assert c["verdict_completeness"] is None
    assert c["top_scar"] == {"repo": None, "id": None, "share": None,
                             "F": None, "V": None, "rate": None}


def test_dominant_scar_cut_excludes_the_top_scar(env, tmp_path, capsys):
    a = env["repo-a"]
    (a / ".scars").mkdir()
    rows = [_fire(a, f"2026-08-30T09:{i:02d}:00", "wide.py", [7], [7])
            for i in range(6)]
    rows.append(_fire(a, "2026-08-30T10:00:00", "n.py", [3], [3]))
    rows.append(_fire(a, "2026-08-30T10:01:00", "n.py", [3], [3]))
    rows.append(_verdict(a, "2026-08-30T10:02:00", "wide.py", [7], "x"))
    rows.append(_verdict(a, "2026-08-30T10:03:00", "n.py", [3], "y"))
    _log(tmp_path, rows)
    c = json.loads(_run(capsys)[1].out)["compliance"]
    assert (c["F"], c["V"], c["rate"]) == (8, 2, 0.25)
    top = c["top_scar"]
    assert top["id"] == 7 and top["repo"] == str(a)
    assert top["share"] == 0.75 and top["share"] > 0.5
    assert (top["F"], top["V"], top["rate"]) == (2, 1, 0.5)


def test_top_scar_ties_break_to_the_lowest_id(env, tmp_path, capsys):
    a = env["repo-a"]
    (a / ".scars").mkdir()
    _log(tmp_path, [_fire(a, "2026-08-30T09:00:00", "a", [5, 2], [5, 2])])
    top = json.loads(_run(capsys)[1].out)["compliance"]["top_scar"]
    assert top["id"] == 2


def test_violation_without_an_earlier_armed_firing_is_not_counted(env, tmp_path, capsys):
    """Fired THEN violated. A violation with no earlier armed firing of that
    scar on that target is a retrieval miss, not a compliance failure, and it
    is reported apart instead of being folded into V."""
    a = env["repo-a"]
    (a / ".scars").mkdir()
    _log(tmp_path, [
        _fire(a, "2026-08-30T09:00:00", "a.py", [1], [1], "e1"),
        _verdict(a, "2026-08-30T09:00:30", "a.py", [1], "e1"),
        _verdict(a, "2026-08-30T09:05:00", "never-shown.py", [1], "e9"),
    ])
    data = json.loads(_run(capsys)[1].out)
    assert data["compliance"]["V"] == 1
    assert data["compliance"]["violations_unmatched"] == 1
    assert sum(e["violations"] for e in data["per_scar"]) == 2  # field unchanged


def _three_repo_rows(env):
    rows = []
    for n, day in (("repo-a", "2026-08-30"), ("repo-b", "2026-08-31"),
                   ("repo-c", "2026-09-01")):
        rows += _repo_rows(env[n], day)
    return rows


def test_repo_set_equals_a_log_containing_only_those_repos(env, tmp_path, capsys):
    rows = _three_repo_rows(env)
    _log(tmp_path, rows)
    _, out = _run(capsys, "--repo", str(env["repo-a"]), "--repo", str(env["repo-b"]))
    chosen = json.loads(out.out)
    only = [r for r in rows if r["repo"] != str(env["repo-c"])]
    _log(tmp_path, only)
    _, out2 = _run(capsys, "--repo", str(env["repo-a"]), "--repo", str(env["repo-b"]))
    assert json.dumps(chosen["compliance"], sort_keys=True) == json.dumps(
        json.loads(out2.out)["compliance"], sort_keys=True)
    assert chosen["compliance"]["F"] == 8 and chosen["compliance"]["V"] == 2
    assert chosen["repos"] == sorted([str(env["repo-a"]), str(env["repo-b"])])


def test_repo_set_pools_counts_and_never_averages_rates(env, tmp_path, capsys):
    a, b = env["repo-a"], env["repo-b"]
    rows = _repo_rows(a)  # F=4 V=1
    rows += [_fire(b, "2026-08-30T09:00:00", "z", [1], [1], "q1")]  # F=1 V=0
    _log(tmp_path, rows)
    c = json.loads(_run(capsys, "--repo", str(a), "--repo", str(b))[1].out)["compliance"]
    assert (c["F"], c["V"], c["rate"]) == (5, 1, 0.2)  # not mean(0.25, 0)


def test_repo_set_keys_scars_by_repo_not_bare_id(env, tmp_path, capsys):
    """Ids are per-repo. Two repos each own a scar #1, and the dominant-scar
    cut must not fuse them into one scar."""
    a, b = env["repo-a"], env["repo-b"]
    rows = [_fire(a, "2026-08-30T09:00:00", "x", [1], [1]) for _ in range(3)]
    rows += [_fire(b, "2026-08-30T09:00:00", "x", [1], [1]) for _ in range(3)]
    _log(tmp_path, rows)
    c = json.loads(_run(capsys, "--repo", str(a), "--repo", str(b))[1].out)["compliance"]
    assert c["F"] == 6
    assert c["top_scar"]["share"] == 0.5 and c["top_scar"]["F"] == 3


def test_repo_set_with_no_records_reports_zeros_honestly(env, tmp_path, capsys):
    _log(tmp_path, _repo_rows(env["repo-a"]))
    rc, out = _run(capsys, "--repo", str(env["repo-c"]))
    assert rc == 0
    data = json.loads(out.out)
    assert data["compliance"]["F"] == 0 and data["compliance"]["rate"] is None
    assert data["total_firings"] == 0


def test_repo_set_composes_with_the_window(env, tmp_path, capsys):
    a, b = env["repo-a"], env["repo-b"]
    rows = _repo_rows(a, "2026-08-30") + _repo_rows(a, "2026-06-01")
    rows += _repo_rows(b, "2026-08-31") + _repo_rows(env["repo-c"], "2026-08-31")
    _log(tmp_path, rows)
    _, out = _run(capsys, "--repo", str(a), "--repo", str(b),
                  "--since", "2026-08-01")
    windowed = json.loads(out.out)
    assert windowed["compliance"]["F"] == 8
    assert windowed["window"]["since"] == "2026-08-01"
    keep = [r for r in rows if r["ts"] >= "2026-08-01"
            and r["repo"] != str(env["repo-c"])]
    _log(tmp_path, keep)
    _, out2 = _run(capsys, "--repo", str(a), "--repo", str(b))
    assert windowed["compliance"] == json.loads(out2.out)["compliance"]


def test_repo_with_all_repos_is_an_argparse_error(env, capsys):
    with pytest.raises(SystemExit) as e:
        main(["stats", "--json", "--all-repos", "--repo", str(env["repo-a"])])
    assert e.value.code == 2


def test_all_repos_blocks_each_carry_their_own_compliance(env, tmp_path, capsys):
    _log(tmp_path, _three_repo_rows(env))
    data = json.loads(_run(capsys, "--all-repos")[1].out)
    assert len(data["repos"]) == 3
    for g in data["repos"]:
        assert KEYS <= g["compliance"].keys()
        assert g["compliance"]["F"] == 4


def test_plain_output_has_a_compliance_line_and_json_is_rich_free(env, tmp_path, capsys):
    a = env["repo-a"]
    (a / ".scars").mkdir()
    _log(tmp_path, _repo_rows(a))
    capsys.readouterr()
    assert main(["stats"]) == 0
    text = capsys.readouterr().out
    assert "compliance: 1 violation(s) / 4 armed firing(s)" in text
    out = _run(capsys)[1].out
    assert "\x1b[" not in out
    json.loads(out)


def test_repo_set_plain_output_names_the_set_window_and_compliance(env, tmp_path, capsys):
    a, b = env["repo-a"], env["repo-b"]
    rows = _repo_rows(a) + _repo_rows(b, "2026-08-31")
    rows.append({"repo": str(a), "target": "t", "scar_ids": [1]})  # no ts
    _log(tmp_path, rows)
    capsys.readouterr()
    assert main(["stats", "--repo", str(a), "--repo", str(b),
                 "--since", "2026-08-01"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == ("scar stats: window 2026-08-01 .. now "
                        "(1 record(s) excluded: no usable timestamp)")
    assert "recorded across 2 repo(s)" in lines[1]
    assert f"  {a}" in lines and f"  {b}" in lines
    assert any(ln.startswith("  compliance: 2 violation(s) / 8 armed firing(s)")
               for ln in lines)
    assert any("ids are not merged across repos" in ln for ln in lines)


def test_repo_set_plain_output_without_a_window_has_no_window_line(env, tmp_path, capsys):
    a = env["repo-a"]
    _log(tmp_path, _repo_rows(a))
    capsys.readouterr()
    assert main(["stats", "--repo", str(a)]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith("scar stats: 4 firing(s) recorded across 1 repo(s)")
