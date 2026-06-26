"""The packaged scar-authoring skill: canonical content + drift guards."""

from pathlib import Path

ROOT = Path(__file__).parent.parent
SKILL = ROOT / "src" / "scar" / "skills" / "scar-authoring"


def _frontmatter(text: str) -> dict:
    assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
    end = text.index("\n---", 4)
    block = text[4:end]
    fields = {}
    for line in block.splitlines():
        if line and not line[0].isspace() and ":" in line:
            key = line.split(":", 1)[0].strip()
            fields[key] = True
    return fields


def test_skill_frontmatter_has_name_and_description():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    fields = _frontmatter(text)
    assert "name" in fields and "description" in fields


def test_skill_covers_all_three_types():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for kind in ("deadend", "fence", "landmine"):
        assert kind in text


def test_bundled_template_is_byte_identical_to_dot_scars():
    canonical = (ROOT / ".scars" / "template.md").read_bytes()
    bundled = (SKILL / "assets" / "template.md").read_bytes()
    assert bundled == canonical
