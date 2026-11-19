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


def test_candidate_carrying_promoted_by_is_a_warning():
    """#287: only promote writes promoted_by, and a candidate has not been
    promoted, so the field on a candidate is a claim nobody made. Warn, not
    error: promote overwrites it anyway, and blocking would make the fix
    harder than the fault. An active scar with the field is the normal case
    and must stay quiet."""
    cand = GOOD.replace("status: active", "status: candidate") \
               .replace("authors: [mara]", "authors: [mara]\npromoted_by: mara")
    findings = lint_text(cand)
    assert any(f.level == "warning" and "promoted_by" in f.message for f in findings)
    active = GOOD.replace("authors: [mara]", "authors: [mara]\npromoted_by: kib")
    assert not any("promoted_by" in f.message for f in lint_text(active))


def test_promoted_by_source_git_config_is_a_warning():
    """#295: a git-config-sourced reviewer means nobody typed a name, so lint
    flags it for a second look. Explicit stays quiet."""
    git_config = GOOD.replace("authors: [mara]",
                               "authors: [mara]\npromoted_by: mara\npromoted_by_source: git-config")
    findings = lint_text(git_config)
    assert any(f.level == "warning" and "promoted_by_source" in f.message for f in findings)
    explicit = GOOD.replace("authors: [mara]",
                             "authors: [mara]\npromoted_by: mara\npromoted_by_source: explicit")
    assert not any("promoted_by_source" in f.message for f in lint_text(explicit))


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


def test_markdown_link_in_title_is_warning():
    # Candidates are committable, reviewer-facing files; markdown link syntax
    # in a title is harvester leakage, not prose (#159 — a downstream
    # harvester shipped `title: "**PR [#172](https://...)** ..."` verbatim).
    bad = GOOD.replace(
        "title: The 7s sleep is intentional",
        'title: "**PR [#172](https://github.com/o/r/pull/172)** broke boot"')
    findings = lint_text(bad)
    assert any(f.level == "warning" and "title" in f.message for f in findings)


def test_bare_url_in_title_is_warning():
    bad = GOOD.replace("title: The 7s sleep is intentional",
                       "title: see https://example.com/why before touching")
    findings = lint_text(bad)
    assert any(f.level == "warning" and "title" in f.message for f in findings)


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


def test_nested_quantifier_pattern_is_error():
    # A valid-but-pathological anchor: re.compile accepts (a+)+$ but search()
    # backtracks catastrophically on adversarial input. Lint must reject it at
    # the gate so it can never be promoted to an active (firing) scar.
    bad = GOOD.replace("  - path: payments/retry.py",
                       '  - pattern: "(a+)+$"')
    findings = lint_text(bad)
    assert any(f.level == "error" and "pattern" in f.message for f in findings)


def test_normal_pattern_anchors_not_flagged_as_redos():
    # Ordinary anchors must NOT false-positive: an escaped literal paren
    # (redis\.get\() is not a group, and an alternation has no nested quantifier.
    ok = GOOD.replace(
        "  - path: payments/retry.py",
        '  - pattern: "redis\\.get\\("\n  - pattern: "TODO|FIXME"')
    findings = lint_text(ok)
    assert not any(f.level == "error" for f in findings)


def test_malformed_url_evidence_is_warning():
    bad = GOOD.replace(
        "evidence:\n  - commit: abc1234\n",
        "evidence:\n  - url: not-a-link\n",
    )
    findings = lint_text(bad)
    assert any(f.level == "warning" and "url" in f.message and "not-a-link" in f.message
               for f in findings)
    assert not any(f.level == "error" for f in findings)


def test_valid_url_evidence_no_warning():
    ok = GOOD.replace(
        "evidence:\n  - commit: abc1234\n",
        "evidence:\n  - url: https://github.com/org/repo/commit/abc1234\n",
    )
    findings = lint_text(ok)
    assert not any("url" in f.message for f in findings)


def test_issue_evidence_skips_url_check():
    ok = GOOD.replace(
        "evidence:\n  - commit: abc1234\n",
        "evidence:\n  - issue: 50\n",
    )
    findings = lint_text(ok)
    assert findings == []


def test_symbol_anchor_warns_to_install_extra_when_absent(monkeypatch):
    from scar import lint, symbols
    monkeypatch.setattr(symbols, "symbols_available", lambda: False)
    findings = lint.lint_text(
        "---\ntype: deadend\ntitle: t\nseverity: medium\nconfidence: 0.5\n"
        "anchors:\n  - path: p.py\n  - symbol: Foo\nstatus: active\n---\nbody\n")
    assert any(f.level == "warning" and "symbols" in f.message and "extra" in f.message
               for f in findings)


def test_symbol_anchor_no_warning_when_extra_present(monkeypatch):
    from scar import lint, symbols
    monkeypatch.setattr(symbols, "symbols_available", lambda: True)
    findings = lint.lint_text(
        "---\ntype: deadend\ntitle: t\nseverity: medium\nconfidence: 0.5\n"
        "anchors:\n  - path: p.py\n  - symbol: Foo\nstatus: active\n---\nbody\n")
    assert not any("symbol" in f.message for f in findings)


def test_symbol_only_scar_is_not_anchorless():
    from scar.lint import lint_text
    findings = lint_text(
        "---\ntype: deadend\ntitle: t\nseverity: medium\nconfidence: 0.5\n"
        "anchors:\n  - symbol: Foo\nstatus: active\n---\nbody\n")
    assert not any("protects nothing" in f.message for f in findings)


def test_unsupported_touch_and_breaks_anchors_are_warnings():
    bad = GOOD.replace(
        "  - path: payments/retry.py",
        "  - path: payments/retry.py\n  - touch: a.py\n  - breaks: b.py")
    findings = lint_text(bad)
    assert any(f.level == "warning" and "touch" in f.message for f in findings)
    assert any(f.level == "warning" and "breaks" in f.message for f in findings)
    assert not any(f.level == "error" for f in findings)


def test_supported_anchors_yield_no_unsupported_warning():
    # A normal path/pattern scar must not trip the unsupported-anchor warning.
    ok = GOOD.replace(
        "  - path: payments/retry.py",
        '  - path: payments/retry.py\n  - pattern: "redis"')
    findings = lint_text(ok)
    assert not any(
        "unsupported" in f.message or "ignored" in f.message for f in findings)


def test_bad_violation_regex_is_error():
    bad = GOOD.replace("status: active", 'violation: "[unclosed"\nstatus: active')
    findings = lint_text(bad)
    assert any(f.level == "error" and "violation" in f.message for f in findings)


def test_nested_quantifier_violation_is_error():
    # A valid-but-pathological violation: re.compile accepts (a+)+$ but search()
    # backtracks catastrophically on adversarial input. Lint must reject it at
    # the gate so it can never cause ReDoS on a code read.
    bad = GOOD.replace("status: active", 'violation: "(a+)+$"\nstatus: active')
    findings = lint_text(bad)
    assert any(f.level == "error" and "violation" in f.message for f in findings)


def test_valid_violation_not_flagged():
    ok = GOOD.replace("status: active", 'violation: "foo"\nstatus: active')
    findings = lint_text(ok)
    assert not any("violation" in f.message for f in findings)


def test_absent_violation_silent():
    # When violation is not specified, no violation-related messages.
    findings = lint_text(GOOD)
    assert not any("violation" in f.message for f in findings)


# --- command anchors (#175) ---

def _command_scar(regex: str) -> str:
    return f"""---
type: deadend
title: command trap
severity: high
confidence: 0.9
created: 2026-07-30
authors: ["kib"]
anchors:
  - command: "{regex}"
evidence:
  - issue: 175
status: active
---

body
"""


def test_command_only_scar_counts_as_anchored():
    findings = lint_text(_command_scar("uv sync"))
    assert not any("no anchors" in f.message for f in findings)


def test_invalid_command_regex_is_an_error():
    findings = lint_text(_command_scar("uv sync ("))
    assert any(f.level == "error" and "command" in f.message for f in findings)


def test_empty_matching_command_regex_is_an_error():
    findings = lint_text(_command_scar(".*"))
    assert any(f.level == "error" and "empty" in f.message for f in findings)


def test_redos_command_regex_is_an_error():
    findings = lint_text(_command_scar("(a+)+b"))
    assert any(f.level == "error" and "command" in f.message
               and "backtracking" in f.message for f in findings)


# --- author identity drift (#182) ---

def _authors_scar(authors: str) -> str:
    return f"""---
type: deadend
title: authors drift probe
severity: medium
confidence: 0.7
created: 2026-07-31
authors: [{authors}]
anchors:
  - path: src/
evidence:
  - issue: 182
status: active
---

body
"""


def test_lint_warns_on_case_only_author_drift_within_one_scar():
    findings = lint_text(_authors_scar('"kibukx", "Kibukx"'))
    assert any(f.level == "warning" and "author" in f.message.lower()
               and "case" in f.message.lower() for f in findings)


def test_lint_silent_on_distinct_authors():
    findings = lint_text(_authors_scar('"claude-code", "kibukx"'))
    assert not any("author" in f.message.lower() and "case" in f.message.lower()
                   for f in findings)


# --- ReDoS gate: nested-group and alternation-overlap evasions (#184) ---

def test_redos_gate_catches_nested_group_at_depth():
    from scar.lint import _is_redos_prone
    assert _is_redos_prone("((a+))+$")
    assert _is_redos_prone("(x(y+)z)*")
    assert _is_redos_prone("(?:(a*))+")


def test_redos_gate_catches_overlapping_alternation():
    from scar.lint import _is_redos_prone
    assert _is_redos_prone("(a|a)*$")
    assert _is_redos_prone("(x|xy)*$")
    assert _is_redos_prone("(?:foo|foobar)+")


def test_redos_gate_still_accepts_ordinary_anchors():
    from scar.lint import _is_redos_prone
    for benign in (r"redis\.get\(", r"TODO|FIXME", r"(abc)+", r"(a|b)*",
                   r"uv sync(?!.* --all-extras)", r"except \((KeyError|TypeError)[^)]*\)",
                   r"[+*]+", r"\(a\+\)\+"):
        assert not _is_redos_prone(benign), benign


def test_redos_gate_still_catches_original_form():
    from scar.lint import _is_redos_prone
    assert _is_redos_prone("(a+)+$")
    assert _is_redos_prone("([a-z]+)*")


def test_missing_status_field_is_error():
    # #199: omitting status: entirely used to default to active AND lint
    # clean — an unreviewed scar armed by omission, bypassing the human
    # promotion gate. Omission must be an error, distinct from an invalid
    # status value.
    text = GOOD.replace("status: active\n", "")
    findings = lint_text(text)
    assert any(f.level == "error" and "missing status" in f.message
               for f in findings)


def test_explicit_status_still_clean():
    assert lint_text(GOOD) == []
    assert lint_text(GOOD.replace("status: active", "status: candidate")) == []


def test_invalid_revives_if_regex_is_an_error():
    """revives_if is compiled and matched like any other regex, so an invalid
    one is a dead watchdog that never fires (#205)."""
    findings = lint_text(GOOD.replace(
        "status: active", 'revives_if: "(unclosed"\nstatus: archived'))
    assert any(f.level == "error" and "revives_if" in f.message for f in findings)


def test_redos_prone_revives_if_is_an_error():
    """Same ReDoS gate as violation (#88/#184): the predicate runs against
    tracked content on every liveness pass."""
    findings = lint_text(GOOD.replace(
        "status: active", 'revives_if: "(a+)+$"\nstatus: archived'))
    assert any(f.level == "error" and "revives_if" in f.message for f in findings)


def test_promoted_by_source_git_config_interactive_is_quiet():
    """#308: a human at a terminal promoted it. Warning here was the false
    positive the issue is about."""
    interactive = GOOD.replace(
        "authors: [mara]",
        "authors: [mara]\npromoted_by: mara\n"
        "promoted_by_source: git-config-interactive")
    assert not any("promoted_by_source" in f.message
                   for f in lint_text(interactive))


def test_git_config_warning_does_not_claim_nobody_typed_a_name():
    """#308: 'nobody typed a reviewer name' asserts something the data cannot
    support for scars promoted before interactivity was recorded. The warning
    must report what is unknown, not assert what did not happen."""
    git_config = GOOD.replace(
        "authors: [mara]",
        "authors: [mara]\npromoted_by: mara\npromoted_by_source: git-config")
    messages = [f.message for f in lint_text(git_config)
                if "promoted_by_source" in f.message]

    assert messages, "the unattended case must still warn"
    assert "nobody typed a reviewer name" not in messages[0]
    assert "no interactive terminal recorded" in messages[0]


# --- flow-style anchors and unsupported kinds (#344) ---

_ANCHORS_BLOCK = "anchors:\n  - path: payments/retry.py\n"


def _flow(anchors_line: str) -> str:
    return GOOD.replace(_ANCHORS_BLOCK, anchors_line + "\n")


def test_flow_style_anchors_name_the_cause_not_generic_no_anchors():
    findings = lint_text(_flow("anchors: [{symbol: src/mod.py::detect}]"))
    errors = [f.message for f in findings if f.level == "error"]
    assert any("flow-style" in m for m in errors)
    assert not any("no anchors" in m for m in errors)


def test_flow_style_bare_kind_value_is_the_same_error():
    findings = lint_text(_flow("anchors: [path: src/mod.py]"))
    errors = [f.message for f in findings if f.level == "error"]
    assert any("flow-style" in m for m in errors)
    assert not any("no anchors" in m for m in errors)


def test_block_anchors_trigger_neither_error():
    findings = lint_text(GOOD)
    assert not any("flow-style" in f.message or "no anchors" in f.message
                   for f in findings)


def test_anchors_key_with_trailing_comment_is_not_flow_style():
    findings = lint_text(_flow("anchors: # none yet"))
    errors = [f.message for f in findings if f.level == "error"]
    assert not any("flow-style" in m for m in errors)
    assert any("no anchors" in m for m in errors)


def test_typo_anchor_kind_warns_and_names_the_four_kinds():
    text = GOOD.replace("  - path: payments/retry.py\n",
                        "  - path: payments/retry.py\n  - symbols: a.py::f\n")
    msgs = [f.message for f in lint_text(text) if f.level == "warning"]
    hit = [m for m in msgs if "symbols" in m]
    assert hit
    for kind in ("path", "pattern", "symbol", "command"):
        assert kind in hit[0]


def test_unsupported_kind_outside_anchors_block_is_not_flagged():
    text = GOOD.replace("  - commit: abc1234\n",
                        "  - commit: abc1234\n  - note: see the thread\n")
    assert not any("unsupported anchor" in f.message for f in lint_text(text))


def test_touch_still_warns_as_unsupported_kind():
    text = GOOD.replace("  - path: payments/retry.py\n",
                        "  - path: payments/retry.py\n  - touch: a.py\n")
    assert any(f.level == "warning" and "touch" in f.message
               for f in lint_text(text))


def test_candidate_carrying_promoted_from_is_a_warning():
    """Only promote writes promoted_from; on a candidate it is a claim."""
    cand = (GOOD.replace("status: active", "status: candidate")
               .replace("authors: [mara]", "authors: [mara]\npromoted_from: candidates/x.md"))
    assert any(f.level == "warning" and "promoted_from" in f.message for f in lint_text(cand))
    active = GOOD.replace("authors: [mara]", "authors: [mara]\npromoted_from: candidates/x.md")
    assert lint_text(active) == []


# --- authors names the drafting runtime, never a placeholder or a role ---

def _candidate_authors(authors: str) -> str:
    return _authors_scar(authors).replace("status: active", "status: candidate")


def test_candidate_with_template_placeholder_author_is_an_error():
    findings = lint_text(_candidate_authors('"<your-runtime>"'))
    assert any(f.level == "error" and "authors" in f.message
               and "placeholder" in f.message for f in findings)


def test_candidate_with_role_word_author_is_an_error():
    for word in ("agent", "maintainer", "Human", "reviewer"):
        findings = lint_text(_candidate_authors(f'"{word}"'))
        assert any(f.level == "error" and "authors" in f.message
                   for f in findings), word


def test_candidate_with_real_runtime_author_is_clean():
    findings = lint_text(_candidate_authors('"codex"'))
    assert not any(f.level == "error" and "authors" in f.message
                   for f in findings)


def test_unedited_template_copy_as_candidate_fails_lint():
    from scar.store import TEMPLATE
    text = TEMPLATE.replace("status: template", "status: candidate")
    findings = lint_text(text)
    assert any(f.level == "error" and "placeholder" in f.message
               for f in findings)


def test_tool_identities_in_seed_stay_clean():
    from scar.store import EXAMPLE_SEED
    assert not any(f.level == "error" for f in lint_text(EXAMPLE_SEED))


def test_malformed_authors_list_is_an_error():
    findings = lint_text(_authors_scar('"a" x'))
    assert any(f.level == "error" and "authors" in f.message
               and "flow list" in f.message for f in findings)


def test_authors_with_trailing_comment_is_clean():
    findings = lint_text(
        _authors_scar('"codex"').replace('["codex"]', '["codex"]  # who drafted it'))
    assert not any("authors" in f.message for f in findings)


# --- over-escaped regex fields (#404) ---
#
# The reader takes regex fields verbatim, so a doubled backslash before a
# metacharacter is a literal backslash followed by the metacharacter. That
# compiles, never matches real code, and used to pass lint.

OVER_PATTERN = r"func \\(\\w+ \\*?Snapshot\\) MarshalJSON"
OVER_VIOLATION = r"func \\(\\w+ Snapshot\\) MarshalJSON"


def _with(field, value):
    return GOOD.replace("status: active", f'{field}: "{value}"\nstatus: active')


def _anchored(pattern):
    return GOOD.replace(
        "  - path: payments/retry.py",
        f'  - path: payments/retry.py\n  - pattern: "{pattern}"')


def _overescaped(findings):
    return [f for f in findings if f.level == "error" and "over-escaped" in f.message]


def test_overescaped_pattern_anchor_is_error_naming_single_backslash_form():
    found = _overescaped(lint_text(_anchored(OVER_PATTERN)))
    assert len(found) == 1
    assert r"func \(\w+ \*?Snapshot\) MarshalJSON" in found[0].message


def test_overescaped_violation_is_error_naming_single_backslash_form():
    found = _overescaped(lint_text(_with("violation", OVER_VIOLATION)))
    assert len(found) == 1
    assert r"func \(\w+ Snapshot\) MarshalJSON" in found[0].message


def test_overescaped_revives_if_is_error():
    text = GOOD.replace(
        "status: active", f'revives_if: "{OVER_VIOLATION}"\nstatus: archived')
    found = _overescaped(lint_text(text))
    assert len(found) == 1 and "revives_if" in found[0].message


def test_overescaped_command_anchor_is_error():
    text = GOOD.replace(
        "  - path: payments/retry.py",
        '  - command: "git \\\\(status\\\\)"')
    assert len(_overescaped(lint_text(text))) == 1


def test_each_metachar_and_class_escape_is_flagged():
    for ch in "()[]{}.*+?|^$bBsSwWdD":
        found = _overescaped(lint_text(_with("violation", f"a\\\\{ch}")))
        assert found, ch


def test_literal_backslash_class_escape_hatch_is_clean():
    # [\\]b = a literal backslash, then the letter b. Recognised because the
    # doubled backslash sits inside a character class, not by matching text.
    assert _overescaped(lint_text(_with("violation", r"[\\]b"))) == []
    assert _overescaped(lint_text(_with("violation", r"x[^a\\]+\\"))) == []


def test_hatch_is_structural_not_a_string_match():
    # Doubled backslash before a bracket outside any class is still flagged.
    assert _overescaped(lint_text(_with("violation", r"a\\]b")))


def test_single_backslash_regexes_stay_clean():
    for ok in (r"func \(\w+ \*?Snapshot\) MarshalJSON", r"\bwiden\b",
               r"a\.b", r"\\n", r"a\\", r"\\\("):
        assert _overescaped(lint_text(_with("violation", ok))) == [], ok


def test_doubled_backslash_at_end_of_string_is_a_literal_backslash():
    assert _overescaped(lint_text(_with("violation", "path" + "\\\\"))) == []


def test_four_backslashes_before_metachar_is_flagged_and_collapses_pairs_once():
    # An even run >= 2 leaves the metachar unescaped. Pairs collapse once, so
    # the hint for four is two (a literal backslash then a group).
    found = _overescaped(lint_text(_with("violation", "a" + "\\" * 4 + "(b")))
    assert len(found) == 1
    assert "/a" + "\\" * 2 + "(b/" in found[0].message


def test_odd_run_before_metachar_is_not_flagged():
    # Three backslashes: a literal backslash then an escaped paren.
    assert _overescaped(lint_text(_with("violation", "a" + "\\" * 3 + "(b"))) == []


def test_uncompilable_overescaped_regex_reports_both_causes():
    # NewConformance\\( fails to compile ("missing )"), which hides the real
    # cause; both errors must surface.
    errors = [f.message for f in lint_text(_with("violation", "New\\\\("))
              if f.level == "error"]
    assert any("invalid violation" in m for m in errors)
    assert any("over-escaped" in m and "/New\\(/" in m for m in errors)
