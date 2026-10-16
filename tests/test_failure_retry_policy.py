import json

import pytest

from harness.web_fetch.cli import build_parser
from harness.web_fetch.models import EvalCase, ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.runner import run_evaluation


class Responses(Provider):
    def __init__(self, answers, *, native=False):
        super().__init__('custom-method', {'type': 'fixture', 'uniform_native_parameters': native})
        self.answers = iter(answers)
        self.calls = 0

    def fetch(self, case, timeout):
        self.calls += 1
        return next(self.answers)


@pytest.fixture
def run_task(tmp_path):
    def run(provider, **options):
        rows, _ = run_evaluation(
            output_dir=tmp_path, dataset_meta={'id': 'retry'}, dataset_path=tmp_path/'dataset.json',
            cases=[EvalCase('one', 'One', 'https://example.com', 'static', ['Expected'])],
            providers=[provider], provider_config_path=tmp_path/'providers.json',
            runs=1, timeout=2, delay=0, **options,
        )
        assert len(rows) == len((tmp_path/'results.jsonl').read_text().splitlines()) == 1
        return rows[0]
    return run


@pytest.mark.parametrize('failure', [
    {'error_type': 'browser_error'}, {'error_type': 'dns'}, {'error_type': 'timeout'},
    {'error_type': 'tls_certificate'}, {'status_code': 403, 'error_type': 'http_error'},
    {'ok': True, 'content': 'incomplete answer'}, {'ok': True, 'content': ''},
])
@pytest.mark.parametrize('native', [False, True])
def test_default_retry_treats_all_task_failures_equally(tmp_path, run_task, failure, native):
    first = ProviderResult(**{'ok': False, 'content': '', **failure})
    provider = Responses([first, ProviderResult(True, 'Expected')], native=native)
    row = run_task(provider)
    assert provider.calls == 2
    assert row['metrics']['content_pass']
    assert (tmp_path/row['artifact_path']).read_text() == 'Expected'


@pytest.mark.parametrize('retries', [0, 1, 3])
def test_configured_retry_bound_selects_last_answer_not_best(tmp_path, run_task, retries):
    provider = Responses([ProviderResult(True, f'wrong answer {number}',
                         metadata={'_answer_elapsed_ms': number * 10})
                         for number in range(1, retries + 2)])
    row = run_task(provider, max_failure_retries=retries)
    assert provider.calls == retries + 1
    assert not row['metrics']['content_pass']
    assert (tmp_path/row['artifact_path']).read_text() == f'wrong answer {retries + 1}'
    assert row['elapsed_ms'] == (retries + 1) * 10
    assert json.loads((tmp_path/'manifest.json').read_text())['attempt_count'] == 1


def test_success_stops_retries_before_exhausting_budget(run_task):
    provider = Responses([
        ProviderResult(False, 'old answer', status_code=503, title='old title', error_type='http_error'),
        ProviderResult(True, 'Expected', status_code=200, title='new title',
                       metadata={'_answer_elapsed_ms': 7}),
    ])
    row = run_task(provider, max_failure_retries=4)
    assert provider.calls == 2
    assert (row['status_code'], row['title'], row['error_type'], row['elapsed_ms']) == (200, 'new title', None, 7)


def test_cli_defaults_to_one_retry_and_accepts_larger_bounds():
    parser = build_parser()
    assert parser.parse_args(['run']).max_failure_retries == 1
    assert parser.parse_args(['run', '--max-failure-retries', '4']).max_failure_retries == 4


@pytest.mark.parametrize('value', ['-1', '1.5'])
def test_cli_rejects_invalid_retry_limits(value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(['run', '--max-failure-retries', value])


def test_hosted_credit_budget_accounts_for_all_configured_retries():
    from harness.web_fetch.providers.firecrawl import FirecrawlProvider

    provider = FirecrawlProvider('firecrawl', {'capacity_credits_per_call': 2})
    case = EvalCase('one', 'One', 'https://example.com', 'static', [])
    assert provider.capacity_credits_for_cases([case], runs=2, max_failure_retries=3) == 16
