"""The single injection formatter (render.py): label_line + injection_context.

render.py is the ONE place CLI, hooks and MCP go through to format a scar, so a
drift here silently corrupts every surface. These characterization tests pin the
fields each rendered block must carry (type, id, severity, confidence, title,
body) and the body-truncation guarantee.
"""

from pathlib import Path

from scar.model import Scar
from scar.render import MAX_BODY_CHARS, injection_context, label_line


def _scar(**kw) -> Scar:
    base = dict(
        type="deadend",
        title="Tried the shared cache",
        id=7,
        severity="high",
        confidence=0.8,
        status="active",
        body="Shared cache corrupts under concurrent writes.",
    )
    base.update(kw)
    return Scar(**base)


def test_label_line_carries_every_identifying_field():
    line = label_line(_scar())
    assert "deadend" in line          # type
    assert "#7" in line               # id
    assert "severity: high" in line   # severity
    assert "confidence: 0.8" in line  # confidence
    assert "Tried the shared cache" in line  # title


def test_label_line_marks_challenged_status():
    active = label_line(_scar(status="active"))
    challenged = label_line(_scar(status="challenged"))
    assert "challenged deadend" in challenged
    assert "challenged" not in active


def test_injection_context_renders_scar_body_and_header():
    text = injection_context([_scar()], broken=[], scars_dir=Path(".scars"))
    # the label line fields survive into the payload...
    assert "deadend" in text
    assert "#7" in text
    assert "Tried the shared cache" in text
    # ...and the body is included after the label.
    assert "Shared cache corrupts under concurrent writes." in text
    # match-count is reported so the agent knows how many scars fired.
    assert "1 match" in text


def test_injection_context_truncates_body_at_max():
    long_body = "x" * (MAX_BODY_CHARS + 500)
    text = injection_context([_scar(body=long_body)], broken=[],
                             scars_dir=Path(".scars"))
    # exactly MAX_BODY_CHARS of the body survive — no more.
    assert "x" * MAX_BODY_CHARS in text
    assert "x" * (MAX_BODY_CHARS + 1) not in text


def test_injection_context_warns_about_broken_files():
    text = injection_context([], broken=[Path(".scars/0001-bad.deadend.md")],
                             scars_dir=Path(".scars"))
    assert "0001-bad.deadend.md" in text
    assert "NEVER" in text


def test_injection_context_empty_when_nothing_to_report():
    assert injection_context([], broken=[], scars_dir=Path(".scars")) == ""


def test_demoted_scars_render_as_one_liners():
    scar = _scar()
    ctx = injection_context([], [], Path(".scars"),
                            demoted=[(scar, "path-only match")])
    assert scar.title in ctx            # label line present
    assert "path-only match" in ctx            # reason visible
    assert "scar why" in ctx                   # recovery hint
    assert scar.body[:80] not in ctx    # body NOT included


def test_demoted_counts_in_match_total():
    scar = _scar()
    ctx = injection_context([scar], [], Path(".scars"),
                            demoted=[(scar, "repeat")])
    assert "2 match(es)" in ctx


def test_no_demoted_is_backward_compatible():
    scar = _scar()
    old = injection_context([scar], [], Path(".scars"))
    new = injection_context([scar], [], Path(".scars"), demoted=None)
    assert old == new


# --- the cap is named in the header when it cut scars (#321) ---

def test_header_names_the_pre_cap_total_when_the_cap_cut(
):
    ctx = injection_context([_scar(), _scar(id=8)], [], Path(".scars"),
                            matched_total=5)
    assert "2 of 5 matched shown" in ctx
    assert "`scar why` on the path" in ctx


def test_header_counts_demoted_scars_as_shown_when_the_cap_cut():
    ctx = injection_context([_scar()], [], Path(".scars"),
                            demoted=[(_scar(id=8), "path-only match")],
                            matched_total=4)
    assert "2 of 4 matched shown" in ctx


def test_header_is_byte_identical_when_nothing_was_cut():
    scar = _scar()
    base = injection_context([scar], [], Path(".scars"))
    assert injection_context([scar], [], Path(".scars"), matched_total=1) == base
    assert injection_context([scar], [], Path(".scars"), matched_total=None) == base
    assert "of" not in base.split("\n")[0].split("(")[1]


def test_header_ignores_a_census_smaller_than_what_was_rendered():
    scar = _scar()
    base = injection_context([scar, _scar(id=8)], [], Path(".scars"))
    for bogus in (0, 1):
        assert injection_context([scar, _scar(id=8)], [], Path(".scars"),
                                 matched_total=bogus) == base
    assert "None" not in injection_context([scar], [], Path(".scars"),
                                           matched_total=None)
