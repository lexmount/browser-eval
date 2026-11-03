"""One registry selects every Web Fetch provider adapter."""

import json
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.web_fetch.providers import Provider, build_provider
from harness.web_fetch.providers.base import PROVIDER_TYPES
from harness.web_fetch.providers.fixture import FixtureProvider
from harness.web_fetch.providers.headless import ENGINES, HeadlessProvider

CONFIGS = Path(__file__).resolve().parents[1] / 'configs'


@pytest.mark.parametrize(('provider_type', 'entry'), sorted(PROVIDER_TYPES.items()))
def test_every_registered_type_names_a_provider_class(provider_type, entry):
    module, class_name = entry
    assert issubclass(getattr(import_module(f'harness.web_fetch.providers.{module}'), class_name),
                      Provider), provider_type


def test_a_new_provider_needs_only_one_registry_entry():
    class Future(FixtureProvider):
        pass

    with patch.dict(PROVIDER_TYPES, {'future': ('fixture', 'Future')}), \
         patch('harness.web_fetch.providers.fixture.Future', Future, create=True):
        provider = build_provider('future-run', {'type': 'future'})
    assert type(provider) is Future
    assert provider.name == 'future-run'


def test_build_provider_passes_name_and_configuration():
    provider = build_provider('run', {'type': 'headless_markdown', 'engine': 'moli'})
    assert type(provider) is HeadlessProvider
    assert (provider.name, provider.type) == ('run', 'headless_markdown')


@pytest.mark.parametrize('config', [{}, {'type': None}, {'type': 'retired'}])
def test_unknown_provider_types_are_rejected(config):
    with pytest.raises(ValueError, match='unknown provider type'):
        build_provider('run', config)


def test_shipped_configurations_use_registered_types_and_engines():
    seen = 0
    for path in CONFIGS.glob('providers*.json'):
        for name, config in json.loads(path.read_text(encoding='utf-8'))['providers'].items():
            seen += 1
            assert config['type'] in PROVIDER_TYPES, f'{path.name}: {name}'
            if config['type'].startswith('headless_'):
                assert config.get('engine') in ENGINES, f'{path.name}: {name}'
    assert seen


def test_unvalidated_fetch_rejects_an_unregistered_engine():
    from lexbench_webfetch.models import EvalCase

    provider = build_provider('run', {'type': 'headless_markdown', 'engine': 'retired'})
    with pytest.raises(ValueError, match='unsupported headless engine: retired'):
        provider.fetch(EvalCase('case', 'case', 'https://example.com/', 'public', []), 1)
    assert provider.validate()[0] == 'engine'
