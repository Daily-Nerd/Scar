"""Lint rules: every rule exists because something already went wrong once."""

from scar.lint import lint_text

GOOD = """\
---
type: fence
title: The 7s sleep is intentional
severity: high
confidence: 0.9
created: 2026-06-09
authors: [mara]
anchors:
  - path: payments/retry.py
evidence:
  - commit: abc1234
status: active
---

Why it must stay.
"""


def test_clean_scar_yields_no_errors():
    assert lint_text(GOOD) == []


def test_missing_frontmatter_is_fatal():
    findings = lint_text("# daimon 0004 case\nplain markdown, never fires\n")
    assert any(f.level == "error" and "frontmatter" in f.message for f in findings)


def test_unknown_type_is_error():
    findings = lint_text(GOOD.replace("type: fence", "type: wart"))
    assert any(f.level == "error" and "type" in f.message for f in findings)


def test_missing_title_is_error():
    findings = lint_text(GOOD.replace("title: The 7s sleep is intentional\n", ""))
    assert any(f.level == "error" and "title" in f.message for f in findings)


def test_no_anchors_is_error():
    bad = GOOD.replace("anchors:\n  - path: payments/retry.py\n", "")
    findings = lint_text(bad)
    assert any(f.level == "error" and "anchor" in f.message for f in findings)


def test_no_evidence_is_warning_not_error():
    bad = GOOD.replace("evidence:\n  - commit: abc1234\n", "")
    findings = lint_text(bad)
    assert any(f.level == "warning" and "evidence" in f.message for f in findings)
    assert not any(f.level == "error" for f in findings)


def test_invalid_severity_is_error():
    findings = lint_text(GOOD.replace("severity: high", "severity: extreme"))
    assert any(f.level == "error" and "severity" in f.message for f in findings)


def test_invalid_status_is_error():
    findings = lint_text(GOOD.replace("status: active", "status: maybe"))
    assert any(f.level == "error" and "status" in f.message for f in findings)


def test_bad_pattern_regex_is_error():
    bad = GOOD.replace("  - path: payments/retry.py",
                       '  - pattern: "([unclosed"')
    findings = lint_text(bad)
    assert any(f.level == "error" and "pattern" in f.message for f in findings)
