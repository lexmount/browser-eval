"""Public onboarding keeps reachable navigation and rectangular comparison tables."""
import json
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


@pytest.mark.parametrize('name,heading', [
    ('README.md', 'Evaluation tracks'),
    ('docs/README.zh-CN.md', '评测流程'),
])
def test_every_selected_dataset_has_an_evaluation_workflow(name, heading):
    root = Path(__file__).resolve().parents[1]
    text = (root / name).read_text()
    section = text.split('## ' + heading + '\n', 1)[1].split('\n## ', 1)[0]
    rows = re.findall(r'^\|.*\|$', section, re.M)
    linked = set(re.findall(r'https://huggingface.co/datasets/(Lexmount/LexBench-[\w-]+)', '\n'.join(rows)))
    lock = json.loads((root / 'src/harness/benchmarks/data-lock.json').read_text())
    assert linked == {dataset['repository'] for dataset in lock['datasets'].values()}


@pytest.mark.parametrize('text', [
    '[Quickstart](#quickstart)\n\n## Reports\n',
    '| Track | Benchmark |\n| --- | --- |\n| Fetch |\n',
])
def test_missing_sections_and_damaged_tables_are_rejected(text):
    with pytest.raises(AssertionError):
        check_navigation(text)
