"""Repository paths named by maintained docs and skills must resolve."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_ROOTS = (".agents/skills", "docs", "evaluation", "examples", "scripts", "src/harness")
# Dataset cards are published with Hugging Face trees; their links resolve there.
PUBLISHED_ELSEWHERE = ("src/harness/benchmarks/cards/",)
REPOSITORY_PREFIXES = {
    ".agents", "configs", "deploy", "docs", "evaluation", "examples", "harness",
    "research", "scripts", "src", "tests",
}
LINK_SCHEMES = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)
# Named in docs by owning projects or external bundles, not by this checkout.
NOT_IN_CHECKOUT = {
    # Historical logical input; `docs/reference-inputs.md` owns its resolution.
    "evaluation/dataset.json",
    # Inside the external WebMainBench `--authority-root` bundle.
    "evaluation/webmainbench-full/current-report.json",
    "evaluation/webmainbench-full/publication-target.json",
    # DeepSeek's native source tree, named as a provider's `required_paths` entry.
    "src/network.ts",
}
PLACEHOLDER = re.compile(r"[*{}<>$\[\]]|\.\.\.")
CODE_TOKEN = re.compile(r"`([^`\n]+)`")
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def documents(root: Path = ROOT) -> list[Path]:
    found = [path for path in root.glob("*.md")]
    for directory in DOCUMENT_ROOTS:
        found.extend((root / directory).rglob("*.md"))
    return sorted(
        path for path in found
        if not path.relative_to(root).as_posix().startswith(PUBLISHED_ELSEWHERE)
    )


def logical_definitions(root: Path = ROOT) -> set[str]:
    """Frozen definitions that the pinned benchmark binding resolves by logical path."""
    binding = json.loads((root / "evaluation/lexbench-webfetch.json").read_text(encoding="utf-8"))
    return set(binding["definition_files"])


def rooted_token(token: str) -> str | None:
    """Return a repository-rooted path from one code span, ignoring commands and placeholders."""
    if any(character.isspace() for character in token) or PLACEHOLDER.search(token):
        return None
    if LINK_SCHEMES.match(token):
        return None
    path = re.split(r"::|:(?=[A-Za-z_])|#", token, maxsplit=1)[0].rstrip("/.,;")
    parts = path.split("/")
    return path if len(parts) > 1 and parts[0] in REPOSITORY_PREFIXES else None


def missing_paths(document: Path, root: Path = ROOT, logical: set[str] = frozenset()) -> list[str]:
    text = document.read_text(encoding="utf-8")
    missing = []
    for match in CODE_TOKEN.finditer(text):
        path = rooted_token(match.group(1))
        if path and path not in logical and path not in NOT_IN_CHECKOUT and not (root / path).exists():
            missing.append(path)
    for match in MARKDOWN_LINK.finditer(CODE_TOKEN.sub("", text)):
        target = match.group(1).split("#", 1)[0]
        if not target or LINK_SCHEMES.match(target) or PLACEHOLDER.search(target):
            continue
        base = root if target.startswith("/") else document.parent
        if not (base / target.lstrip("/")).exists():
            missing.append(target)
    return missing


def test_document_inventory_covers_root_guides_and_skills():
    names = {path.relative_to(ROOT).as_posix() for path in documents()}
    assert {
        "AGENTS.md",
        "README.md",
        ".agents/skills/benchmark-report/SKILL.md",
        "docs/architecture.md",
        "scripts/README.md",
        "src/harness/web_fetch/README.md",
    } <= names
    assert not [name for name in names if name.startswith(PUBLISHED_ELSEWHERE)]


def test_maintained_documents_name_only_existing_repository_paths():
    logical = logical_definitions()
    stale = {
        path.relative_to(ROOT).as_posix(): missing
        for path in documents()
        if (missing := missing_paths(path, logical=logical))
    }
    assert stale == {}


@pytest.mark.parametrize("text,expected", [
    ("Read `scripts/gone.py` first.", ["scripts/gone.py"]),
    ("Keep `src/harness/gone.py::test_case` current.", ["src/harness/gone.py"]),
    ("Keep `tests/gone.py:helper` current.", ["tests/gone.py"]),
    ("See [guide](gone.md#section).", ["gone.md"]),
    ("Use `harness/web_fetch/gone.py`.", ["harness/web_fetch/gone.py"]),
    ("Read `scripts/present.py`, then [local](present.md#a).", []),
    ("Run `scripts/present.py --flag` and `evaluation/<track>/x.json`.", []),
    ("See `~/.browser-eval/evidence/x.json` and [site](https://example.test/x).", []),
    ("`evaluation/logical.json` resolves through the binding.", []),
])
def test_path_checker_accepts_and_rejects_scenarios(tmp_path, text, expected):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/present.py").write_text("")
    (tmp_path / "present.md").write_text("")
    document = tmp_path / "doc.md"
    document.write_text(text)
    assert missing_paths(document, tmp_path, {"evaluation/logical.json"}) == expected
