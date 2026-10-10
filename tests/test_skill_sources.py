"""Vendored writing instructions retain their recorded upstream bytes."""
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / ".agents/skills"


@pytest.mark.parametrize("name", ["humanizer", "humanizer-chinese"])
def test_vendored_skill_matches_its_pinned_source(name):
    directory = ROOT / name
    source = json.loads((directory / "SOURCE.json").read_text())
    assert len(source["commit"]) == 40
    assert source["repository"].startswith("https://github.com/")
    assert {"SKILL.md", "LICENSE"} <= source["unmodified_upstream_files"].keys()
    for filename, digest in source["unmodified_upstream_files"].items():
        assert hashlib.sha256((directory / filename).read_bytes()).hexdigest() == digest
