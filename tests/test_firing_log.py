"""The one shared firing-log reader (landmine #12).

The log is appended best-effort from a fail-open hook, so ANY JSON shape can
land on a line. Every reader used to carry its own copy of the guarding; these
tests pin the single implementation both readers now call.
"""

import json

import pytest

from scar.firing_log import read_firing_log_records


def _write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_keeps_only_dict_records(tmp_path):
    """null, arrays and numbers all parse as valid JSON, then crash at
    rec.get(). They must be dropped, not merely survived by a try block."""
    log = tmp_path / "firing-log.jsonl"
    _write(log, [
        json.dumps({"repo": "a", "scar_ids": [1]}),
        "null",
        "[]",
        "42",
        '"a bare string"',
        json.dumps({"repo": "b", "scar_ids": [2]}),
    ])

    records = read_firing_log_records(log)

    assert records == [{"repo": "a", "scar_ids": [1]},
                       {"repo": "b", "scar_ids": [2]}]


def test_skips_malformed_json(tmp_path):
    log = tmp_path / "firing-log.jsonl"
    _write(log, [
        json.dumps({"repo": "a"}),
        '{"repo": "truncated"',
        "not json at all",
        json.dumps({"repo": "b"}),
    ])

    assert read_firing_log_records(log) == [{"repo": "a"}, {"repo": "b"}]


def test_skips_blank_lines(tmp_path):
    log = tmp_path / "firing-log.jsonl"
    _write(log, [json.dumps({"repo": "a"}), "", "   ", json.dumps({"repo": "b"})])

    assert read_firing_log_records(log) == [{"repo": "a"}, {"repo": "b"}]


def test_missing_file_returns_empty(tmp_path):
    assert read_firing_log_records(tmp_path / "absent.jsonl") == []


def test_tail_keeps_only_the_newest_lines(tmp_path):
    """The tail cap is line-count based and applied BEFORE parsing, so a
    malformed line still consumes one of the N slots (it counts toward the
    total, exactly as gc truncation counts it)."""
    log = tmp_path / "firing-log.jsonl"
    _write(log, [json.dumps({"n": i}) for i in range(10)])

    records = read_firing_log_records(log, tail=3)

    assert records == [{"n": 7}, {"n": 8}, {"n": 9}]


def test_tail_larger_than_the_log_returns_everything(tmp_path):
    log = tmp_path / "firing-log.jsonl"
    _write(log, [json.dumps({"n": 0}), json.dumps({"n": 1})])

    assert read_firing_log_records(log, tail=500) == [{"n": 0}, {"n": 1}]


def test_tail_of_zero_returns_nothing(tmp_path):
    """Guard the slice-math trap the scar names: lines[-0:] is the WHOLE list,
    so a zero tail must be special-cased rather than sliced."""
    log = tmp_path / "firing-log.jsonl"
    _write(log, [json.dumps({"n": 0}), json.dumps({"n": 1})])

    assert read_firing_log_records(log, tail=0) == []


def test_unreadable_log_returns_empty_and_never_raises(tmp_path):
    """The reader is called from a fail-open hook. An unreadable log must
    degrade to 'no records', never escape into the caller."""
    log = tmp_path / "firing-log.jsonl"
    log.mkdir()  # a directory where a file is expected: read_text raises

    assert read_firing_log_records(log) == []


def test_undecodable_bytes_return_empty(tmp_path):
    log = tmp_path / "firing-log.jsonl"
    log.write_bytes(b"\xff\xfe not utf-8\n")

    assert read_firing_log_records(log) == []


@pytest.mark.parametrize("tail", [-1, -50])
def test_negative_tail_is_refused(tmp_path, tail):
    """A negative tail would slice from the front and silently return the
    OLDEST rows, which is the opposite of what every caller wants."""
    log = tmp_path / "firing-log.jsonl"
    _write(log, [json.dumps({"n": 0}), json.dumps({"n": 1})])

    with pytest.raises(ValueError):
        read_firing_log_records(log, tail=tail)
