import json
from dataclasses import asdict

import pytest

from harness.web_fetch.cli import build_parser
from lexbench_webfetch.models import EvalCase
from harness.web_fetch.models import ProviderResult
from harness.web_fetch.providers.fixture import FixtureProvider
from harness.web_fetch.runner import run_evaluation


class Responses(FixtureProvider):
    def __init__(self, answers, *, native=False):
        super().__init__('custom-method', {'type': 'fixture', 'uniform_native_parameters': native})
        self.answers = iter(answers)
        self.calls = 0

    def fetch(self, case, timeout, *, native_route_ledger=None):
        self.calls += 1
        result = next(self.answers)
        if self.config['uniform_native_parameters']:
            import base64
            import hashlib
            raw = json.dumps({'content': result.content}).encode()
            result.metadata.update(_raw_response_b64=base64.b64encode(raw).decode(),
                raw_response_sha256=hashlib.sha256(raw).hexdigest(), raw_response_bytes=len(raw),
                native_answer_projection={'schema_version': 2, 'content_pointer': ['content'], 'content_format': 'text'})
        return result


@pytest.fixture
def run_task(tmp_path):
    def run(provider, **options):
        case = EvalCase('one', 'One', 'https://example.com', 'static', ['Expected'], source_benchmark={'input_media_kind': 'html_page'})
        rows, _ = run_evaluation(
            output_dir=tmp_path, dataset_meta={'id': 'retry', 'cases': [asdict(case)]}, dataset_path=tmp_path/'dataset.json',
            cases=[case],
            providers=[provider], provider_config_path=tmp_path/'providers.json',
            runs=1, timeout=2, delay=0, **options,
        )
        assert len(rows) == len((tmp_path/'results.jsonl').read_text(encoding='utf-8').splitlines()) == 1
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
    assert (tmp_path/row['artifact_path']).read_text(encoding='utf-8') == 'Expected'


@pytest.mark.parametrize('retries', [0, 1])
def test_configured_retry_bound_selects_last_answer_not_best(tmp_path, run_task, retries):
    provider = Responses([ProviderResult(True, f'wrong answer {number}',
                         metadata={'_answer_elapsed_ms': number * 10})
                         for number in range(1, retries + 2)])
    row = run_task(provider, max_failure_retries=retries)
    assert provider.calls == retries + 1
    assert not row['metrics']['content_pass']
    assert (tmp_path/row['artifact_path']).read_text(encoding='utf-8') == f'wrong answer {retries + 1}'
    assert row['elapsed_ms'] == (retries + 1) * 10
    assert json.loads((tmp_path/'manifest.json').read_text(encoding='utf-8'))['attempt_count'] == 1


def test_success_stops_retries_before_exhausting_budget(run_task):
    provider = Responses([
        ProviderResult(False, 'old answer', status_code=503, title='old title', error_type='http_error'),
        ProviderResult(True, 'Expected', status_code=200, title='new title',
                       metadata={'_answer_elapsed_ms': 7}),
    ])
    row = run_task(provider, max_failure_retries=1)
    assert provider.calls == 2
    assert (row['status_code'], row['title'], row['error_type'], row['elapsed_ms']) == (200, 'new title', None, 7)


def test_cli_defaults_to_one_retry_and_rejects_larger_bounds():
    parser = build_parser()
    assert parser.parse_args(['run']).max_failure_retries == 1
    with pytest.raises(SystemExit):
        parser.parse_args(['run', '--max-failure-retries', '4'])


@pytest.mark.parametrize('value', ['-1', '1.5'])
def test_cli_rejects_invalid_retry_limits(value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(['run', '--max-failure-retries', value])


def test_hosted_credit_budget_accounts_for_all_configured_retries():
    from harness.web_fetch.providers.firecrawl import FirecrawlProvider

    provider = FirecrawlProvider('firecrawl', {'capacity_credits_per_call': 2})
    case = EvalCase('one', 'One', 'https://example.com', 'static', [])
    assert provider.capacity_credits_for_cases([case], runs=2, max_failure_retries=3) == 16


def test_runner_rejects_extra_retry_before_any_call(run_task):
    provider = Responses([ProviderResult(False, '')])
    with pytest.raises(ValueError, match='At most one'):
        run_task(provider, max_failure_retries=2)
    assert provider.calls == 0


def test_initial_success_never_retries(run_task):
    provider = Responses([ProviderResult(True, 'Expected')])
    assert run_task(provider)['metrics']['content_pass']
    assert provider.calls == 1


def test_late_first_call_is_discarded_then_retried_without_overlap(run_task):
    import threading
    import time

    class SlowFirst(Responses):
        def fetch(self, case, timeout, **kwargs):
            self.calls += 1
            if self.calls == 1:
                time.sleep(timeout + .05)
                self.finished.set()
                return ProviderResult(True, 'Expected')
            assert self.finished.is_set()
            return ProviderResult(False, '', error_type='second_failure')

    provider = SlowFirst([])
    provider.finished = threading.Event()
    row = run_task(provider)
    assert provider.calls == 2
    assert not row['ok'] and row['error_type'] == 'second_failure'
    attempts = row['provider_metadata']['retry']['attempts']
    assert len(attempts) == 2
    assert attempts[0]['error_type'] == 'timeout' and not attempts[0]['task_pass']
    assert not attempts[1]['task_pass']


def test_cleanup_events_and_native_logs_are_retained_before_retry(run_task, tmp_path):
    import hashlib
    import time
    from datetime import datetime, timezone
    from pathlib import Path

    class CleanupLogging(Responses):
        def fetch(self, case, timeout, **kwargs):
            self.calls += 1
            if self.calls == 1:
                time.sleep(timeout + .1)
                sink = self._call_diagnostics
                sink.emit('native_library', 'cleanup_observation')
                Path(str(sink.path)+'.moli.stderr').write_text('native cleanup bytes', encoding='utf-8')
                self.finished = datetime.now(timezone.utc)
                return ProviderResult(True, 'Late answer must be discarded')
            assert self.finished is not None
            return ProviderResult(True, 'Expected')

    provider = CleanupLogging([])
    provider.config['log_level'] = 'debug'
    provider.finished = None
    row = run_task(provider)
    first, second = row['provider_metadata']['retry']['attempts']
    assert first['error_type'] == 'timeout' and not first['task_pass']
    assert second['task_pass'] and row['elapsed_ms'] == second['elapsed_ms']
    assert (tmp_path/first['artifact_path']).read_bytes() == b''
    assert len(first['private_artifacts']) == 1
    diagnostic = first['private_artifacts'][0]
    raw = (tmp_path/diagnostic['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == diagnostic['sha256']
    assert b'cleanup_observation' in raw and b'native cleanup bytes' in raw
    assert raw.index(b'cleanup_observation') < raw.index(b'collection_completed')
    assert datetime.fromisoformat(first['completed_at']) >= provider.finished
    assert first['elapsed_ms'] < (provider.finished - datetime.fromisoformat(first['started_at'])).total_seconds() * 1000
    call = json.loads((tmp_path/first['call_log']['path']).read_bytes())
    assert call['timing']['completed_at'] == first['completed_at']
