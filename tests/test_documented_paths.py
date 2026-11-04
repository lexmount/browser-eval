"""Repository paths named by maintained docs and skills must resolve."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from harness.reporting.source_policy import REPORT_INSTANCE_NAMES

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_ROOTS = (
    ".agents/skills", ".github", "deploy", "docs", "evaluation", "examples", "scripts", "src/harness",
)
# Dataset cards are published with Hugging Face trees; their links resolve there.
PUBLISHED_ELSEWHERE = ("src/harness/benchmarks/cards/",)
REPOSITORY_PREFIXES = {
    ".agents", ".github", "configs", "deploy", "docs", "evaluation", "examples", "harness",
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
FENCED_BLOCK = re.compile(r"^[ \t>]*(`{3,}|~{3,})[^\n]*\n(.*?)^[ \t>]*\1", re.M | re.S)
# A repository-prefixed token anywhere in code; a longer path or URL ending in a prefix is not one.
PATH_TOKEN = re.compile(
    r"(?<![\w./~$@:%+-])(?:\./)?((?:"
    + "|".join(re.escape(prefix) for prefix in sorted(REPOSITORY_PREFIXES))
    + r")/[^\s`'\"(),;|&\\]*)"
)
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


def rooted_paths(code: str) -> list[str]:
    """Return repository-rooted paths from code text, ignoring placeholders and other locations."""
    paths = []
    for token in PATH_TOKEN.findall(code):
        if PLACEHOLDER.search(token):
            continue
        path = re.split(r"::|:(?=\w)|#", token, maxsplit=1)[0].rstrip("/.,;:")
        if "/" in path:
            paths.append(path)
    return paths


def missing_paths(document: Path, root: Path = ROOT, logical: set[str] = frozenset()) -> list[str]:
    text = document.read_text(encoding="utf-8")
    fenced = [match.group(2) for match in FENCED_BLOCK.finditer(text)]
    prose = FENCED_BLOCK.sub("", text)
    # A skill names its own bundled files relative to the directory holding its `SKILL.md`.
    skill_root = next((parent for parent in document.parents if (parent / "SKILL.md").is_file()), root)
    missing = []
    for code in [*CODE_TOKEN.findall(prose), *fenced]:
        for path in rooted_paths(code):
            if path in logical or path in NOT_IN_CHECKOUT:
                continue
            if not ((root / path).exists() or (skill_root / path).exists()):
                missing.append(path)
    for match in MARKDOWN_LINK.finditer(CODE_TOKEN.sub("", prose)):
        target = match.group(1).split("#", 1)[0]
        if not target or LINK_SCHEMES.match(target) or PLACEHOLDER.search(target):
            continue
        base = root if target.startswith("/") else document.parent
        if not (base / target.lstrip("/")).exists():
            missing.append(target)
    return missing


def named_report_instances(text: str) -> list[str]:
    return sorted(name for name in REPORT_INSTANCE_NAMES if name in text)


def test_evaluation_documents_do_not_present_report_instances_as_checkout_files():
    named = {
        path.relative_to(ROOT).as_posix(): instances
        for path in (ROOT / "evaluation").rglob("*.md")
        if (instances := named_report_instances(path.read_text(encoding="utf-8")))
    }
    assert named == {}


@pytest.mark.parametrize("text,expected", [
    ("`current-run.json.dataset.path` selects the input.", ["current-run.json"]),
    ("See `../sibling/current-report.json`.", ["current-report.json"]),
    ("`current-comparison.json` binds the results.", ["current-comparison.json"]),
    ("An external run profile selected with `--profile`.", []),
])
def test_report_instance_check_accepts_and_rejects_scenarios(text, expected):
    assert named_report_instances(text) == expected


def test_document_inventory_covers_root_guides_and_skills():
    names = {path.relative_to(ROOT).as_posix() for path in documents()}
    assert {
        "AGENTS.md",
        "README.md",
        ".agents/skills/benchmark-report/SKILL.md",
        ".github/workflows/README.md",
        "deploy/webarena/README.md",
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


def test_skill_documents_may_name_bundled_files_relative_to_the_skill(tmp_path):
    skill = tmp_path / ".agents/skills/example"
    (skill / "scripts").mkdir(parents=True)
    (skill / "references").mkdir()
    (skill / "SKILL.md").write_text("", encoding="utf-8")
    (skill / "scripts/bundled.py").write_text("", encoding="utf-8")
    document = skill / "references/guide.md"
    document.write_text("Run `scripts/bundled.py TIMELINE`, not `scripts/gone.py TIMELINE`.", encoding="utf-8")
    assert missing_paths(document, tmp_path) == ["scripts/gone.py"]


@pytest.mark.parametrize("text,expected", [
    ("Read `scripts/gone.py` first.", ["scripts/gone.py"]),
    ("Keep `src/harness/gone.py::test_case` current.", ["src/harness/gone.py"]),
    ("Keep `tests/gone.py:helper` current.", ["tests/gone.py"]),
    ("See [guide](gone.md#section).", ["gone.md"]),
    ("Use `harness/web_fetch/gone.py`.", ["harness/web_fetch/gone.py"]),
    ("Read `scripts/present.py`, then [local](present.md#a).", []),
    ("Run `scripts/present.py --flag` and `evaluation/<track>/x.json`.", []),
    ("Run `uv run python scripts/gone.py`.", ["scripts/gone.py"]),
    ("Run `scripts/gone.py --flag`.", ["scripts/gone.py"]),
    ("Run `./scripts/gone.py`.", ["scripts/gone.py"]),
    ("Run `--input=docs/gone.md`.", ["docs/gone.md"]),
    ("```sh\nuv run python scripts/gone.py \\\n  --out /tmp/x\n```\n", ["scripts/gone.py"]),
    ("  ~~~\n  scripts/gone.py\n  ~~~\n", ["scripts/gone.py"]),
    ("```sh\nuv run python scripts/present.py --in evaluation/<track>/x.json\n```\n", []),
    ("Run `python /tmp/scripts/gone.py ~/docs/gone.md $HOME/tests/gone.py ../src/gone.py`.", []),
    ("```\ncurl https://example.test/docs/gone.md\n```\n", []),
    ("```text\n[not a link](gone.md)\n```\n", []),
    ("See `~/.browser-eval/evidence/x.json` and [site](https://example.test/x).", []),
    ("`evaluation/logical.json` resolves through the binding.", []),
])
def test_path_checker_accepts_and_rejects_scenarios(tmp_path, text, expected):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/present.py").write_text("", encoding="utf-8")
    (tmp_path / "present.md").write_text("", encoding="utf-8")
    document = tmp_path / "doc.md"
    document.write_text(text, encoding="utf-8")
    assert missing_paths(document, tmp_path, {"evaluation/logical.json"}) == expected
