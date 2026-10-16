import json

import pytest

from harness.web_fetch.cli import build_parser
from harness.web_fetch.models import EvalCase, ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.runner import run_evaluation


@pytest.mark.parametrize('name', ['moli', 'chromium', 'hosted'])
@pytest.mark.parametrize('failure', [
    ProviderResult(False, '', error_type='browser_error'),
    ProviderResult(False, '', error_type='dns'),
    ProviderResult(False, '', error_type='timeout'),
    ProviderResult(False, '', error_type='tls_certificate'),
    ProviderResult(False, '', status_code=403, error_type='http_error'),
    ProviderResult(True, 'incomplete answer'),
    ProviderResult(True, ''),
])
@pytest.mark.parametrize('native', [False, True])
def test_default_retry_treats_every_method_and_failure_equally(tmp_path, name, failure, native):
    class Recovers(Provider):
        calls = 0

        def fetch(self, case, timeout):
            self.calls += 1
            return failure if self.calls == 1 else ProviderResult(True, 'Expected')

    provider = Recovers(name, {'type': 'fixture', 'uniform_native_parameters': native})
    rows, _ = run_evaluation(
        output_dir=tmp_path, dataset_meta={'id': 'retry'}, dataset_path=tmp_path/'dataset.json',
        cases=[EvalCase('one', 'One', 'https://example.com', 'static', ['Expected'])],
        providers=[provider], provider_config_path=tmp_path/'providers.json',
        runs=1, timeout=2, delay=0,
    )
    assert provider.calls == 2
    assert len(rows) == 1
    assert rows[0]['metrics']['content_pass']
    assert (tmp_path/rows[0]['artifact_path']).read_text() == 'Expected'
    assert len((tmp_path/'results.jsonl').read_text().splitlines()) == 1


@pytest.mark.parametrize('retries', [0, 1, 3])
def test_configured_retry_bound_selects_last_answer_not_best(tmp_path, retries):
    class Fails(Provider):
        calls = 0

        def fetch(self, case, timeout):
            self.calls += 1
            return ProviderResult(True, f'wrong answer {self.calls}',
                                  metadata={'_answer_elapsed_ms': self.calls * 10})

    provider = Fails('fixture', {'type': 'fixture'})
    rows, _ = run_evaluation(
        output_dir=tmp_path, dataset_meta={'id': 'retry'}, dataset_path=tmp_path/'dataset.json',
        cases=[EvalCase('one', 'One', 'https://example.com', 'static', ['Expected'])],
        providers=[provider], provider_config_path=tmp_path/'providers.json',
        runs=1, timeout=2, delay=0, max_failure_retries=retries,
    )
    assert provider.calls == retries + 1
    assert len(rows) == 1
    assert not rows[0]['metrics']['content_pass']
    assert (tmp_path/rows[0]['artifact_path']).read_text() == f'wrong answer {retries + 1}'
    assert rows[0]['elapsed_ms'] == (retries + 1) * 10
    assert json.loads((tmp_path/'manifest.json').read_text())['attempt_count'] == 1


def test_cli_defaults_to_one_retry_and_accepts_larger_bounds():
    parser = build_parser()
    assert parser.parse_args(['run']).max_failure_retries == 1
    assert parser.parse_args(['run', '--max-failure-retries', '4']).max_failure_retries == 4


@pytest.mark.parametrize('value', ['-1', '1.5'])
def test_cli_rejects_invalid_retry_limits(value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(['run', '--max-failure-retries', value])


def test_success_stops_retries_before_exhausting_budget(tmp_path):
    class Recovers(Provider):
        calls = 0

        def fetch(self, case, timeout):
            self.calls += 1
            if self.calls == 1:
                return ProviderResult(False, 'old answer', status_code=503,
                                      title='old title', error_type='http_error')
            return ProviderResult(True, 'Expected', status_code=200, title='new title',
                                  metadata={'_answer_elapsed_ms': 7})

    provider = Recovers('fixture', {'type': 'fixture'})
    rows, _ = run_evaluation(
        output_dir=tmp_path, dataset_meta={'id': 'retry'}, dataset_path=tmp_path/'dataset.json',
        cases=[EvalCase('one', 'One', 'https://example.com', 'static', ['Expected'])],
        providers=[provider], provider_config_path=tmp_path/'providers.json',
        runs=1, timeout=2, delay=0, max_failure_retries=4,
    )
    assert provider.calls == 2
    assert len(rows) == 1
    assert rows[0]['status_code'] == 200
    assert rows[0]['title'] == 'new title'
    assert rows[0]['error_type'] is None
    assert rows[0]['elapsed_ms'] == 7


def test_hosted_credit_budget_accounts_for_all_configured_retries():
    from harness.web_fetch.providers.firecrawl import FirecrawlProvider

    provider = FirecrawlProvider('firecrawl', {'capacity_credits_per_call': 2})
    case = EvalCase('one', 'One', 'https://example.com', 'static', [])
    assert provider.capacity_credits_for_cases([case], runs=2, max_failure_retries=3) == 16
