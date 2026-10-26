"""Public onboarding keeps reachable navigation and rectangular comparison tables."""
import re
from pathlib import Path

import pytest


def check_navigation(text):
    headings = {
        re.sub(r'[^\w\s-]', '', heading.lower()).replace(' ', '-')
        for heading in re.findall(r'^#{1,6} (.+)$', text, re.M)
    }
    for anchor in re.findall(r'\]\(#([^)]*)\)', text):
        assert anchor in headings, f'Missing README section: {anchor}'
    tables = re.findall(r'(?:^\|.*\|\n)+', text, re.M)
    for table in tables:
        assert len({len(row.split('|')) for row in table.splitlines()}) == 1


@pytest.mark.parametrize('name', ['README.md', 'docs/README.zh-CN.md'])
def test_readme_navigation_and_tables(name):
    check_navigation((Path(__file__).resolve().parents[1] / name).read_text())


@pytest.mark.parametrize('text', [
    '[Quickstart](#quickstart)\n\n## Reports\n',
    '| Track | Benchmark |\n| --- | --- |\n| Fetch |\n',
])
def test_missing_sections_and_damaged_tables_are_rejected(text):
    with pytest.raises(AssertionError):
        check_navigation(text)
