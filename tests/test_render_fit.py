"""render_fit (#419): will a promoted record fit the signer's render limits.

The signer refuses a record over 2048 bytes or over 32 block lines. A line is
escaped (a printable ASCII byte is itself, every other byte is 4 characters),
and an escaped line over 96 characters wraps; nothing is truncated.
"""
import json
import subprocess
from pathlib import Path

import pytest

from scar.cli import main
from scar.model import parse_scar_text
from scar.record import (
    MAX_RECORD_BYTES, MAX_RECORD_LINES, promoted_record, render_fit,
)
from scar.store import init_scars

ROOT = Path(__file__).resolve().parent.parent

HEAD = """---
id: 7
type: fence
title: T
severity: low
confidence: 0.5
created: 2026-11-01
authors: ["claude-code"]
anchors:
  - path: src/x.py
status: {status}
---

{body}
"""


def scar_with_body(body, status="active"):
    return parse_scar_text(HEAD.format(status=status, body=body))


def fixed_bytes(body="x"):
    return len(promoted_record(scar_with_body(body)))


def test_limits_are_the_signers_numbers():
    assert MAX_RECORD_BYTES == 2048
    assert MAX_RECORD_LINES == 32


def test_2048_bytes_fits_and_2049_does_not():
    base = fixed_bytes("")
    fit = render_fit(scar_with_body("a" * (2048 - base)))
    assert (fit.bytes, fit.bytes_over) == (2048, 0)
    over = render_fit(scar_with_body("a" * (2049 - base)))
    assert (over.bytes, over.bytes_over) == (2049, 1)
    assert not over.fits


def test_32_lines_fits_and_33_does_not():
    base = render_fit(scar_with_body("")).lines
    fit = render_fit(scar_with_body("\n".join(["l"] * (32 - base + 1))))
    assert (fit.lines, fit.lines_over, fit.fits) == (32, 0, True)
    over = render_fit(scar_with_body("\n".join(["l"] * (33 - base + 1))))
    assert (over.lines, over.lines_over, over.fits) == (33, 1, False)


def one_line_base():
    """Block lines of a record whose body is a single short line."""
    return render_fit(scar_with_body("a")).lines


def test_a_line_of_96_characters_is_one_line_and_97_is_two():
    base = one_line_base()
    assert render_fit(scar_with_body("a" * 96)).lines == base
    assert render_fit(scar_with_body("a" * 97)).lines == base + 1


def test_a_non_ascii_byte_counts_as_four_characters():
    base = one_line_base()
    # Each of these is two bytes, so 8 escaped characters. 12 make exactly 96
    # characters (one line); 13 make 104 and wrap.
    assert render_fit(scar_with_body("\u00e9" * 12)).lines == base
    assert render_fit(scar_with_body("\u00e9" * 13)).lines == base + 1
    assert render_fit(scar_with_body("\u00e9" * 24)).lines == base + 1
    assert render_fit(scar_with_body("\u00e9" * 25)).lines == base + 2


def test_an_escape_is_never_split_across_lines():
    base = one_line_base()
    # 94 plain characters, then two 4-character escapes: the first would reach
    # 98, so both move to a continuation line instead of splitting.
    assert render_fit(scar_with_body("a" * 94 + "\u00e9")).lines == base + 1


def test_a_tab_is_escaped_and_a_backslash_is_itself():
    base = one_line_base()
    assert render_fit(scar_with_body("a" * 92 + "\tb")).lines == base + 1
    assert render_fit(scar_with_body("a" * 91 + "\tb")).lines == base
    assert render_fit(scar_with_body("a" * 95 + "\\")).lines == base
    assert render_fit(scar_with_body("a" * 96 + "\\")).lines == base + 1


@pytest.mark.parametrize("number, size, lines", [
    (5, 1922, 38), (6, 2116, 37), (9, 2600, 44), (11, 1890, 36),
    (16, 1776, 33), (20, 1715, 34), (22, 1871, 35),
])
def test_real_scars_reproduce_the_reference_measurement(number, size, lines):
    path = next((ROOT / ".scars").glob(f"{number:04d}-*.md"))
    fit = render_fit(parse_scar_text(path.read_text(encoding="utf-8")))
    assert (fit.bytes, fit.lines) == (size, lines)


def test_scar_19_fits():
    path = next((ROOT / ".scars").glob("0019-*.md"))
    fit = render_fit(parse_scar_text(path.read_text(encoding="utf-8")))
    assert fit.lines == 30
    assert fit.fits


# ---- lint ----------------------------------------------------------------

@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    init_scars(tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write(repo, rel, status, body, with_id):
    ident = "id: 3\n" if with_id else ""
    p = repo / ".scars" / rel
    p.parent.mkdir(exist_ok=True)
    p.write_text(HEAD.replace("id: 7\n", ident).format(status=status, body=body))
    return p


def fit_messages(capsys):
    rc = main(["lint", "--json"])
    data = json.loads(capsys.readouterr().out)
    return rc, [f["message"] for f in data["findings"] if "signer" in f["message"]]


LONG = "\n".join(["l"] * 40)


def test_a_candidate_over_the_limit_is_warned_with_the_excess(repo, capsys):
    write(repo, "candidates/big.md", "candidate", LONG, with_id=False)
    rc, msgs = fit_messages(capsys)
    assert rc == 0
    assert len(msgs) == 1
    assert msgs[0].startswith("candidate big.md: record is ")
    assert "lines over the signer's limit of 32" in msgs[0]
    assert "cannot be signed at promotion until it is shortened" in msgs[0]


def test_bytes_and_lines_are_combined_when_both_are_over(repo, capsys):
    write(repo, "candidates/huge.md", "candidate", "x" * 3000 + "\n" + LONG, with_id=False)
    _, msgs = fit_messages(capsys)
    assert "bytes" in msgs[0] and "lines" in msgs[0]


def test_an_active_scar_over_the_limit_gets_no_warning(repo, capsys):
    write(repo, "0003-big.fence.md", "active", LONG, with_id=True)
    _, msgs = fit_messages(capsys)
    assert msgs == []


def test_a_fitting_candidate_gets_no_warning(repo, capsys):
    write(repo, "candidates/ok.md", "candidate", "short", with_id=False)
    _, msgs = fit_messages(capsys)
    assert msgs == []


def test_the_candidate_is_measured_as_it_will_be_promoted(repo, capsys):
    # The signed record carries the id promote will assign and the
    # promoted_from line, so a candidate that fits bare can still be over.
    base = render_fit(scar_with_body("")).lines
    write(repo, "candidates/edge.md", "candidate", "\n".join(["l"] * (32 - base + 1)),
          with_id=False)
    _, msgs = fit_messages(capsys)
    assert len(msgs) == 1  # promoted_from adds a line to the bare 32


def test_plain_output_shows_the_warning(repo, capsys):
    write(repo, "candidates/big.md", "candidate", LONG, with_id=False)
    main(["lint"])
    assert "signer's limit" in capsys.readouterr().out
