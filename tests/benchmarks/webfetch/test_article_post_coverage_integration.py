"""Current collection floors work through scoring without live references."""
import json
import subprocess
import sys

import pytest

from lexbench_webfetch.article_dataset import DEFINITIONS
from lexbench_webfetch.evaluation import evaluate_response


def fixture(number):
    case = next(c for c in json.loads((DEFINITIONS / 'tasks.json').read_text())['cases']
                if c['id'] == f'article-{number:03}')
    stable = '\n\n'.join(a['alternatives'][0] for a in case['acceptance_contract']['stable_text_anchors'])
    count = case['source_benchmark']['post_contract']['minimum_posts']
    prefix = 'https://xueqiu.com/123/' if number == 101 else 'https://x.com/browser_use/status/'
    posts = [f'## [Current author {i}]({prefix}{90000+i})\n\nNew visible post text number {i}.' for i in range(count)]
    return case, stable, posts


@pytest.mark.parametrize('number,minimum', [(38,4),(39,8),(40,4),(41,4),(101,16)])
def test_current_collection_coverage_without_runtime_reference(number, minimum):
    case, stable, posts = fixture(number)
    assert case['source_benchmark']['post_contract']['minimum_posts'] == minimum
    full = evaluate_response(case, (stable+'\n\n'+'\n\n'.join(posts)).encode())
    assert full['passed'] and full['measured']
    assert full['metrics']['acceptance_checks']['article_posts'] is True
    assert 'article_reference_status' not in full['metrics']
    assert full['metrics']['content_decision_basis'] == 'base_acceptance_and_required_components'
    for text, posts_pass in ((stable, False),
                             (stable+'\n\n'+'\n\n'.join(posts[:-1]), False),
                             ('\n\n'.join(posts), True)):
        result = evaluate_response(case, text.encode())
        assert not result['passed']
        assert result['measured']
        assert result['metrics']['acceptance_checks']['article_posts'] is posts_pass


def test_cli_accepts_collection_without_reference_arguments(tmp_path):
    case, stable, posts = fixture(38)
    task, answer = tmp_path/'task.json', tmp_path/'answer.md'
    task.write_text(json.dumps(case))
    answer.write_text(stable+'\n\n'+'\n\n'.join(posts))
    result = subprocess.run([sys.executable, '-m', 'lexbench_webfetch.cli', 'score',
                             '--task', str(task), '--response', str(answer)],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['passed']


@pytest.mark.parametrize('number', [38, 41])
def test_required_profile_fields_cannot_be_replaced_by_posts(number):
    case, stable, posts = fixture(number)
    full = stable + '\n\n' + '\n\n'.join(posts)
    assert evaluate_response(case, full.encode())['passed']
    fields = ['Agents that use the Browser', 'Science & Technology',
              'San Francisco', 'browser-use.com', 'Joined November 2024']
    for field in fields:
        assert not evaluate_response(case, full.replace(field, '').encode())['passed']
