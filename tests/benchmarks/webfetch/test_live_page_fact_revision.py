"""Registered live-page rules reject missing prose, not changing release labels."""
from copy import deepcopy
import re

import pytest

from lexbench_webfetch import benchmark
from lexbench_webfetch.annotation_revisions import apply_annotation_change
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def revised_case(prefix):
    root = benchmark.suite('webfetch')[1]
    case = deepcopy(next(c for c in benchmark.read(root / 'tasks.json')['cases']
                         if c['id'].startswith(prefix)))
    revision = benchmark.read(benchmark.SUITES / 'annotation-revisions/review-550-20261023.json')
    change = next(c for c in revision['changes']
                  if c['suite'] == 'webfetch' and c['case_id'] == case['id'])
    apply_annotation_change(case, change, None)
    return case


def answer_with(case, *, omit=None):
    facts = [f['alternatives'][0] for f in case['required_facts'] if f['id'] != omit]
    anchors = [a['alternatives'][0] for a in case['acceptance_contract']['stable_text_anchors']]
    return '\n'.join(facts + anchors) + '\n' + 'Ordinary supplementary prose. ' * 40


@pytest.mark.parametrize('omitted', [None, 'escape', 'unescape'])
def test_function_labels_do_not_replace_explanations(omitted):
    case = revised_case('live-007-')
    text = answer_with(case)
    text += '\nhtml.escape(s, quote=True)\nhtml.unescape(s)'
    fact_id = {'escape': 'body-semantic-unit-1', 'unescape': 'anchor-3'}.get(omitted)
    if fact_id:
        phrase = next(f['alternatives'][0] for f in case['required_facts'] if f['id'] == fact_id)
        text = text.replace(phrase, '')
    metrics = score_attempt(case, text.encode(), ok=True)
    assert metrics['length_pass']
    assert task_passed(True, metrics) is (omitted is None)


@pytest.mark.parametrize('version', ['3.10', '3.11', '3.99'])
@pytest.mark.parametrize('missing_description', [False, True])
def test_scrapy_version_is_not_a_frozen_requirement(version, missing_description):
    case = revised_case('live-010-')
    text = answer_with(case) + f'\nIt requires Python {version}+.'
    if missing_description:
        text = text.replace('is a web scraping framework to extract structured data from', '')
    assert task_passed(True, score_attempt(case, text.encode(), ok=True)) is not missing_description


@pytest.mark.parametrize('missing_directory', [False, True])
def test_postgresql_needs_its_directory_without_a_release_banner(missing_directory):
    case = revised_case('live-035-')
    text = answer_with(case)
    group = case['composite_acceptance']['groups'][0]
    paths = group['entry_path'][3:-1].split('|')
    if missing_directory:
        paths = paths[:group['minimum_records'] - 1]
    for index, path in enumerate(paths):
        path = re.sub(r'\\(.)', r'\1', path)
        text += f'\n[Tutorial section {index}](https://www.postgresql.org{path})'
    assert task_passed(True, score_attempt(case, text.encode(), ok=True)) is not missing_directory
