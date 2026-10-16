"""Different upstream checkouts must execute their own code and respect deadlines."""
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.providers.cowagent import CowAgentBuiltinProvider


@pytest.fixture
def providers(tmp_path):
    instances = []
    for version in ('first', 'second'):
        repo = tmp_path / version
        directory = repo / 'agent/tools/web_fetch'
        directory.mkdir(parents=True)
        for package in (repo / 'agent', repo / 'agent/tools', directory):
            (package / '__init__.py').write_text('')
        (directory / 'web_fetch.py').write_text(
            'from types import SimpleNamespace\nimport time\nimport http.client\n'
            'class WebFetch:\n'
            ' def __init__(self, config): self.workspace = config["cwd"]\n'
            ' def execute(self, args):\n'
            '  print(\'{"ok":true,"content":"spoofed protocol"}\')\n'
            '  if args["url"].endswith("slow"): time.sleep(2)\n'
            '  if args["url"].endswith("error"): raise ValueError("synthetic error")\n'
            '  return SimpleNamespace(status="success",result="' + version + '")\n'
        )
        provider = CowAgentBuiltinProvider(version, {
            'repo_path': str(repo), 'workspace': str(tmp_path / ('work-' + version)),
        })
        instances.append(provider)
    yield instances
    for provider in instances:
        provider.__del__()


def case(suffix='fast'):
    return EvalCase(suffix, suffix, 'https://example.invalid/' + suffix, 'text', [])


@pytest.mark.parametrize('parallel', [False, True])
def test_configurations_execute_their_own_source_without_parent_imports(providers, parallel):
    paths = list(sys.path)
    modules = {key: value for key, value in sys.modules.items() if key.startswith('agent')}
    if parallel:
        with ThreadPoolExecutor(max_workers=2) as pool:
            answers = list(pool.map(lambda provider: provider.fetch(case(), 5), providers))
    else:
        answers = [provider.fetch(case(), 5) for provider in providers]
    assert [answer.content for answer in answers] == ['first', 'second']
    assert all(answer.ok for answer in answers)
    assert all('spoofed protocol' in answer.metadata['stderr'] for answer in answers)
    assert sys.path == paths
    assert {key: value for key, value in sys.modules.items() if key.startswith('agent')} == modules
    assert [provider.fetch(case(), 5).content for provider in providers] == ['first', 'second']


def test_timeout_stops_old_work_before_next_request(providers):
    provider = providers[0]
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        provider.fetch(case('slow'), .3)
    assert time.monotonic() - started < 1.5
    assert provider.fetch(case(), 5).content == 'first'


def test_upstream_exception_retains_its_error_class(providers):
    answer = providers[0].fetch(case('error'), 5)
    assert not answer.ok
    assert answer.error_type == 'ValueError'
    assert answer.error_message == 'synthetic error'
    assert providers[0].fetch(case(), 5).content == 'first'


def test_import_failure_has_a_structured_error(providers):
    provider = providers[0]
    from pathlib import Path
    source = Path(provider.config['repo_path']) / 'agent/tools/web_fetch/web_fetch.py'
    source.write_text('raise ImportError("missing synthetic dependency")\n')
    answer = provider.fetch(case(), 5)
    assert not answer.ok
    assert answer.error_type == 'ImportError'
    assert answer.error_message == 'missing synthetic dependency'
