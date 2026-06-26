from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_agents_md_documents_authoring_skill_access():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "scar agent skill" in text
    assert "scar-authoring" in text


def test_readme_documents_plugin_and_cli_install():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "scar skill install" in text
