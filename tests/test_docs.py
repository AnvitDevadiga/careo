"""Documentation integrity: relative links resolve, one AI disclosure, license header."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = [
    REPO / "README.md",
    *sorted((REPO / "docs").glob("*.md")),
    REPO / "benchmarks" / "README.md",
]


def test_relative_links_resolve():
    broken = []
    for doc in DOCS:
        text = doc.read_text()
        for target in re.findall(r"\]\(([^)]+)\)", text) + re.findall(
            r'(?:src|srcset)="([^"]+)"', text
        ):
            if re.match(r"^(https?:|mailto:|#)", target):
                continue
            path = (doc.parent / target.split("#")[0]).resolve()
            if not path.exists():
                broken.append(f"{doc.relative_to(REPO)} -> {target}")
    assert broken == []


def test_single_ai_disclosure():
    text = (REPO / "README.md").read_text()
    assert text.count("Claude") == 1
    assert "Development assistance: Claude Code was used during implementation." in text
    for p in [*DOCS[1:], REPO / "LICENSE", REPO / "THIRD_PARTY_NOTICES.md"]:
        assert "Claude" not in p.read_text(), p
    for p in (REPO / "site" / "src").rglob("*"):
        if p.is_file():
            assert "Claude" not in p.read_text(), p


def test_license_is_standard_mit():
    text = (REPO / "LICENSE").read_text()
    assert text.startswith("MIT License\n\nCopyright (c) 2026 Anvit Devadiga\n")
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text


def test_versions_agree():
    import careo

    toml = (REPO / "pyproject.toml").read_text()
    assert f'version = "{careo.__version__}"' in toml
