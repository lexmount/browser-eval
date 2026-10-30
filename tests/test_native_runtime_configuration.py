"""Native methods accept declared upgrades and reject mismatched installations."""
from harness.distribution import source_path

import shutil
import subprocess
from pathlib import Path

import pytest

from harness.web_fetch.providers.cowagent import CowAgentBuiltinProvider
from harness.web_fetch.providers.subprocess_jsonl import SubprocessJsonlProvider

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / 'upstream'
    repo.mkdir()
    paths = [
        'agent/tools/web_fetch/web_fetch.py', 'agent/tools/utils/url_safety.py',
        'src/agents/tools/web-fetch.ts', 'packages/web/web-fetch-http/src/provider.ts',
        'pnpm-lock.yaml',
    ]
    for name in paths:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic source\n')
    for name, version in [('node', 'v30.1.2'), ('pnpm', '15.2.3')]:
        path = repo / name
        path.write_text('#!/bin/sh\nprintf "' + version + '\\n"\n')
        path.chmod(0o755)
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture',
                    '-c', 'user.email=fixture@example.test', 'commit', '-qm', 'fixture'],
                   check=True)
    revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'],
                                       text=True).strip()
    return repo, revision


@pytest.mark.parametrize('method,change', [
    (method, change)
    for method in ['cowagent_builtin', 'openclaw_builtin', 'deepseek_harness_builtin']
    for change in ['none', 'wrong-revision', 'dirty', 'missing-source']
] + [('cowagent_builtin', 'wrong-python'),
     ('openclaw_builtin', 'wrong-node'), ('openclaw_builtin', 'wrong-manager'),
     ('deepseek_harness_builtin', 'wrong-node'), ('deepseek_harness_builtin', 'stale-worker')])
def test_declared_native_installation_is_authoritative(checkout, method, change):
    repo, revision = checkout
    if method == 'cowagent_builtin':
        required = ['agent/tools/web_fetch/web_fetch.py', 'agent/tools/utils/url_safety.py']
    elif method == 'openclaw_builtin':
        required = ['src/agents/tools/web-fetch.ts', 'pnpm-lock.yaml']
    else:
        required = ['packages/web/web-fetch-http/src/provider.ts', 'pnpm-lock.yaml',
                    'node_modules/.pnpm/.browser-harness-dsh-worker.mts']
        deployed = repo / required[-1]
        deployed.parent.mkdir(parents=True)
        shutil.copyfile(source_path('harness/web_fetch/workers/dsh_webfetch_worker.mts', root=ROOT), deployed)
    config = {'repo_path': str(repo), 'expected_repo_revision': revision,
              'required_paths': ['{repo_path}/' + name for name in required]}
    if method != 'cowagent_builtin':
        worker = ('openclaw_webfetch_worker.mts' if method == 'openclaw_builtin'
                  else 'dsh_webfetch_worker.mts')
        config.update(method_type=method, command=[str(repo / 'node')],
                      expected_node_version='v30.1.2')
        config['required_paths'].append('{harness_root}/harness/web_fetch/workers/' + worker)
    if method == 'deepseek_harness_builtin':
        config['required_paths'].append('{harness_root}/harness/web_fetch/workers/dsh_output.mjs')
    if method == 'openclaw_builtin':
        config.update(package_manager_command=str(repo / 'pnpm'),
                      expected_package_manager_version='15.2.3')
    provider = (CowAgentBuiltinProvider if method == 'cowagent_builtin'
                else SubprocessJsonlProvider)('custom-name', config)
    config = provider.config
    if change == 'wrong-revision':
        config['expected_repo_revision'] = '0' * 40
    elif change == 'dirty':
        (repo / required[0]).write_text('dirty\n')
    elif change == 'missing-source':
        (repo / required[0]).unlink()
    elif change == 'wrong-node':
        config['expected_node_version'] = 'v0.0.0'
    elif change == 'wrong-manager':
        config['expected_package_manager_version'] = '0.0.0'
    elif change == 'wrong-python':
        config['expected_python_version'] = 'Python 0.0.0'
    elif change == 'stale-worker':
        deployed.write_text('old worker\n')
    errors = provider.validate()
    if change == 'none':
        assert errors == []
        identity = provider._implementation_identity
        assert identity['repository']['head'] == revision
        assert str(repo) not in str(identity)
        config['expected_repo_revision'] = '0' * 40
        assert provider.validate()
        assert not hasattr(provider, '_implementation_identity')
    else:
        assert errors
        if change == 'stale-worker':
            assert any(error.startswith('runtime files differ:') for error in errors)
        assert not hasattr(provider, '_implementation_identity')


@pytest.mark.parametrize('method', ['cowagent_builtin', 'openclaw_builtin',
                                   'deepseek_harness_builtin'])
def test_invalid_revalidation_discards_prior_identity(checkout, method):
    repo, _ = checkout
    provider = (CowAgentBuiltinProvider if method == 'cowagent_builtin'
                else SubprocessJsonlProvider)('custom-name', {
                    'repo_path': str(repo), 'method_type': method,
                })
    provider._implementation_identity = {'repository': {'head': 'old'}}
    assert provider.validate()
    assert not hasattr(provider, '_implementation_identity')


@pytest.mark.parametrize('paths', [None, 'source.py', [None]])
def test_invalid_required_path_configuration_fails_preflight(checkout, paths):
    repo, revision = checkout
    provider = SubprocessJsonlProvider('custom-name', {
        'repo_path': str(repo), 'command': [str(repo / 'node')],
        'expected_repo_revision': revision, 'required_paths': paths,
    })
    assert provider.validate()
    assert not hasattr(provider, '_implementation_identity')
