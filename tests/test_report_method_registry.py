"""New methods and executable versions must not require renderer changes."""
import copy
import json

import pytest

from harness.web_fetch.methods import (
    browser_method_label, is_browser_comparison, method_version,
    report_method_catalog, with_measured_method_labels,
)
from harness.web_fetch.methods import method_access
from harness.web_fetch.report_interfaces import method_spec, render_interfaces


@pytest.fixture
def custom_catalog(monkeypatch):
    catalog = copy.deepcopy(report_method_catalog())
    catalog['methods']['acme-fetch'] = {
        'label': {'zh': 'Acme Fetch', 'en': 'Acme Fetch'},
        'kind': 'browser', 'label_template': '{label} {version}',
        'source_url': 'https://example.com/acme', 'adapter_types': ['acme'],
        'profile': {'kind': 'Local CLI', 'description': 'A registered synthetic browser.'},
        'access': {'account': 'No account', 'configuration': 'Install the bound binary.',
                   'env_vars': ['ACME_BINARY'], 'links': [['Source', 'https://example.com/acme']]},
        'interface': {'entry': 'acme fetch URL', 'auth': 'Local binary',
                      'params': [['url', 'string', 'Target URL']],
                      'fields': [['text', 'string', 'Returned text']],
                      'request': {'url': 'https://example.com/'},
                      'source': 'https://example.com/acme', 'notes': 'Synthetic registration.'},
    }
    monkeypatch.setattr('harness.web_fetch.methods.report_method_catalog', lambda language="zh": catalog)
    monkeypatch.setattr('harness.web_fetch.report_interfaces.report_method_catalog', lambda language="zh": catalog)
    return catalog


def test_new_method_uses_one_registration_in_all_method_views(tmp_path, custom_catalog):
    from harness.web_fetch.report_methods import render_methods
    from harness.web_fetch.report import hero, section, table, cell, link, esc
    (tmp_path / 'answer.txt').write_text('Synthetic answer')
    (tmp_path / 'dataset.json').write_text('{}')
    records = [{'provider': 'acme-fetch', 'provider_type': 'acme', 'case_id': 'new-task',
                'artifact_path': 'answer.txt', 'attempt_id': 'acme-attempt',
                'provider_metadata': {'reported_version': '7.8.9'}}]

    @with_measured_method_labels
    def render(out, dataset, rows):
        assert browser_method_label('acme-fetch') == 'Acme Fetch 7.8.9'
        assert is_browser_comparison(rows)
        assert method_access('acme-fetch', rows)['env_vars'] == ['ACME_BINARY']
        return render_methods(out, rows, hero=hero, section=section, table=table,
                              cell=cell, link=link, esc=esc)
    body = render(tmp_path, {}, records)
    assert 'A registered synthetic browser.' in body
    assert 'acme fetch URL' in body
    assert 'Acme Fetch 7.8.9' in body
    assert 'ACME_BINARY' in body
    saved = json.loads((tmp_path / 'adoption/interfaces/acme-fetch.json').read_text())
    assert saved['interface']['entry'] == 'acme fetch URL'


def test_unknown_method_retains_output_and_discloses_missing_registration(tmp_path):
    (tmp_path / 'adoption').mkdir()
    (tmp_path / 'answer.txt').write_text('Retained unknown output')
    row = {'provider': 'unknown-vendor', 'provider_type': 'unknown', 'case_id': 'unknown-task',
           'artifact_path': 'answer.txt', 'attempt_id': 'unknown-attempt'}
    body = render_interfaces(tmp_path, [row])
    assert 'Not registered' in body
    assert 'Retained unknown output' in body
    saved = json.loads((tmp_path / 'adoption/interfaces/unknown-vendor.json').read_text())
    assert saved['interface']['entry'] == '未登记'
    assert method_spec('unknown-vendor', row, language='en')['entry'] == 'Not registered'
    assert method_spec('unknown-vendor', row)['request'] == {}


@pytest.mark.parametrize('name,engine,version,expected', [
    ('new-moli', 'moli', 'moli 9.9.9', 'Moli 9.9.9'),
    ('new-browser', 'acme-fetch', '7.8.9', 'Acme Fetch 7.8.9'),
])
def test_recorded_versions_win_over_provenance_and_never_leak_between_reports(
        tmp_path, custom_catalog, name, engine, version, expected):
    row = {'provider': name, 'provider_metadata': {
        'engine': engine, 'binary_sha256': 'a' * 64, 'reported_version': version}}
    provenance = {'a' * 64: {'version': 'older'}}
    (tmp_path / 'implementation-provenance.json').write_text(json.dumps({
        'implementations': [{'binary_sha256': 'a' * 64, 'version': 'older'}]}))
    assert method_version(row, provenance) == version

    @with_measured_method_labels
    def render(out, dataset, rows):
        return browser_method_label(name)
    before = browser_method_label(name)
    assert render(tmp_path, {}, [row]) == expected
    assert browser_method_label(name) == before
    row['provider_metadata'].pop('reported_version')
    assert render(tmp_path, {}, [row]).endswith(' older')


def test_native_compatibility_version_is_not_published_for_a_renamed_engine():
    record = {'provider': 'new-native-run', 'provider_metadata': {
        'engine': 'moli', 'browser_version': '150.0.0.0'}}
    assert method_version(record) is None


def test_diagnostic_method_view_uses_executable_version_before_cdp_version(tmp_path):
    from harness.web_fetch.report_methods import render_methods
    from harness.web_fetch.report import hero, section, table, cell, link, esc
    row = {'provider': 'new-moli', 'provider_type': 'headless_markdown',
           'provider_metadata': {'engine': 'moli', 'reported_version': 'moli 9.9.9',
                                 'browser_version': '150.0.0.0'}}
    body = render_methods(tmp_path, [row], hero=hero, section=section, table=table,
                          cell=cell, link=link, esc=esc, authored=False)
    assert 'moli 9.9.9' in body
    assert '150.0.0.0' not in body


def test_method_catalog_rejects_unsupported_language():
    with pytest.raises(ValueError, match='Unsupported method catalog language'):
        report_method_catalog('fr')


@pytest.mark.parametrize('version,release', [
    ('moli 9.9.9', True), ('MOLI 9.9.9', True), ('9.9.9', True),
    ('moli 9.9.9-build.17', False), ('moli 9.9.9 (abcdef)', False),
])
def test_release_links_and_labels_share_normalization(version, release):
    from harness.web_fetch.methods import report_method, versioned_method_label
    row = {'provider': 'moli', 'provider_metadata': {'reported_version': version}}
    access = method_access('moli', [row])
    label = versioned_method_label(report_method('moli'), 'Moli', version)
    assert label.startswith('Moli 9.9.9')
    assert ('/releases/tag/v9.9.9' in access['links'][0][1]) is release
    assert method_version(row) == version  # Raw evidence is never rewritten.


def test_ambiguous_transport_requires_recorded_engine(custom_catalog):
    from harness.web_fetch.methods import report_method
    custom_catalog['methods']['other-acme'] = {
        'label': {'zh': 'Other', 'en': 'Other'}, 'adapter_types': ['acme']}
    row = {'provider_type': 'acme'}
    with pytest.raises(ValueError, match='Ambiguous report method'):
        report_method('unregistered-run', row)
    row['provider_metadata'] = {'engine': 'other-acme'}
    assert report_method('unregistered-run', row)['label']['en'] == 'Other'


@pytest.mark.parametrize('label,channel,expected_candidate', [
    ('Acme candidate build', None, False),
    ('Acme preview', 'candidate', True),
    ('Acme candidate build', 'stable', False),
])
def test_candidate_status_is_explicit_not_guessed_from_label(
        tmp_path, custom_catalog, monkeypatch, label, channel, expected_candidate):
    monkeypatch.setattr('harness.web_fetch.methods.browser_method_registry', lambda: {
        'acme-run': {'engine': 'acme-fetch', 'label': label, 'release_channel': channel}})
    rows = [{'provider': 'acme-run', 'provider_metadata': {
        'reported_version': version}} for version in ['9.9.9-build.a', '9.9.9-build.b']]

    @with_measured_method_labels
    def render(out, dataset, records):
        return browser_method_label('acme-run')
    assert ('候选版' in render(tmp_path, {}, rows)) is expected_candidate



def test_json_registered_route_and_interface_variant_need_no_engine_branch(custom_catalog):
    from harness.web_fetch.report_interfaces import _observed_route
    method = custom_catalog['methods']['acme-fetch']
    method['observed_routes'] = [{
        'metadata_equals': {'extractor': 'acme-native'},
        'text': 'Acme native Markdown{format}',
        'fragments': {'format': [{'metadata_equals': {'native_metadata.format': 'acme-json'},
                                  'text': ' via JSON'}]}}]
    method['interface_variants'] = [{
        'metadata_equals': {'native_metadata.format': 'acme-json'},
        'command': 'acme fetch --json URL', 'notes': 'Native JSON output.'}]
    metadata = {'engine': 'acme-fetch', 'extractor': 'acme-native',
                'native_metadata': {'format': 'acme-json', 'api_token': 'secret-canary'}}
    assert _observed_route('acme-fetch', metadata) == 'Acme native Markdown via JSON'
    spec = method_spec('acme-run', {'provider_metadata': metadata})
    assert spec['request']['command'] == 'acme fetch --json URL'
    assert 'secret-canary' not in json.dumps(spec)
    metadata['extractor'] = 'unknown'
    assert 'Acme native Markdown' not in _observed_route('acme-fetch', metadata)
    metadata['native_metadata']['format'] = 'unsupported'
    assert 'command' not in method_spec('acme-run', {'provider_metadata': metadata})['request']


def test_new_engine_matrix_uses_registered_plan_and_recorded_wait(custom_catalog):
    from harness.web_fetch.report_interfaces import browser_task_interface_matrix
    method = custom_catalog['methods']['acme-fetch']
    method['routes'] = {'capabilities': 'Native text', 'markdown': 'Acme text'}
    method['task_interfaces'] = [{'case_equals': {}, 'description': '{markdown}', 'wait': 'ready'}]
    cases = {'body': {'id': 'body', 'url': 'https://example.test/', 'track': 'web'}}
    planned = browser_task_interface_matrix(cases, ['acme-fetch'])
    assert planned['rows'][0]['cells']['acme-fetch'] == [
        {'count': 1, 'mode': 'markdown', 'description': 'Acme text', 'wait': 'ready'}]
    records = [{'provider': 'renamed-run', 'case_id': 'body',
                'provider_metadata': {'engine': 'acme-fetch', 'wait_until': 'observed-ready'}}]
    measured = browser_task_interface_matrix(cases, ['renamed-run'], records)
    part = measured['rows'][0]['cells']['renamed-run'][0]
    assert part['wait'] == '输出完成条件 observed-ready'
    assert 'Acme text' not in part['description']  # No observed extractor was recorded.
    records[0]['provider_metadata'].pop('wait_until')
    assert '未提供' in browser_task_interface_matrix(
        cases, ['renamed-run'], records)['rows'][0]['cells']['renamed-run'][0]['wait']


def test_registered_task_modes_match_public_execution_selection():
    from types import SimpleNamespace
    from harness.web_fetch.providers.headless import output_mode_for_case
    from harness.web_fetch.report_interfaces import browser_task_interface_matrix
    cases = {f'{track}-{kind}': {'id': f'{track}-{kind}', 'track': track,
        'task_kind': kind, 'url': 'https://example.test/document.pdf'}
        for track in ['web', 'dynamic', 'document', 'image', 'video', 'challenge', 'risk_control']
        for kind in ['body', 'resource', 'file']}
    from harness.web_fetch.methods import report_method_catalog
    providers = [name for name, method in report_method_catalog()['methods'].items()
                 if method.get('kind') == 'browser']
    # Single-case matrices keep the assertion independent of presentation grouping.
    for case_id, case in cases.items():
        matrix = browser_task_interface_matrix({case_id: case}, providers)
        for provider in providers:
            assert matrix['rows'][0]['cells'][provider][0]['mode'] == output_mode_for_case(
                provider, 'headless_markdown', SimpleNamespace(**case))



def test_interface_converter_version_comes_from_call_not_catalog():
    row = {'provider': 'chromium', 'provider_metadata': {
        'engine': 'chromium', 'extractor': 'node-html-markdown', 'extractor_version': '9.9.9'}}
    spec = method_spec('chromium', row)
    assert spec['request']['converter'] == 'node-html-markdown 9.9.9'
    assert next(param[2] for param in spec['params'] if param[0] == 'converter') == 'node-html-markdown 9.9.9'
    row['provider_metadata'].pop('extractor_version')
    assert method_spec('chromium', row)['request']['converter'] == '<recorded_converter>'


def test_catalog_examples_do_not_assert_prior_run_settings():
    import ast
    import re
    from pathlib import Path
    from fnmatch import fnmatchcase
    pattern = re.compile(r'\d[\d,.]*\s*-?\s*(?:秒|毫秒|ms\b|seconds?\b)|\b\d+\.\d+\.\d+\b')
    # Versioned evidence identities and documented product limits, not run settings.
    allowed = ['sources.*.checked_version', 'sources.*.url', 'sources.*.label',
               'sources.*.label.zh', 'sources.*.label.en',
               'methods.moli.capabilities.sources.0',
               'adapters.cloudflare_markdown.freshness.*',
               'adapters.cloudflare_content.freshness.*',
               'methods.obscura.routes.waits.*']
    def strings(value, path=()):
        if isinstance(value, dict):
            for key, item in value.items():
                yield from strings(item, (*path, str(key)))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from strings(item, (*path, str(index)))
        elif isinstance(value, str):
            yield '.'.join(path), value
    root = Path(__file__).resolve().parents[1] / 'harness/web_fetch'
    catalog = json.loads((root / 'data/report-methods.json').read_text())
    violations = [(path, text) for path, text in strings(catalog)
                  if pattern.search(text) and not any(fnmatchcase(path, rule) for rule in allowed)]
    for name in ['report_interfaces', 'report_capabilities', 'methods', 'report_methods', 'report_explainer']:
        for node in ast.walk(ast.parse((root / (name+'.py')).read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and pattern.search(node.value):
                violations.append((f'{name}:{node.lineno}', node.value))
    assert not violations
    for prohibited in ['30 秒', '40,500 毫秒', '6.1-second', '2500 ms', 'version 9.9.9']:
        assert pattern.search(prohibited)


def test_mixed_build_labels_have_explicit_english_and_scoped_lifetime(tmp_path):
    rows = [{'provider': 'moli-candidate', 'provider_metadata': {'reported_version': version}}
            for version in ['moli 9.9.8', 'moli 9.9.9', 'moli 9.9.9']]
    @with_measured_method_labels
    def render(out, dataset, records):
        return browser_method_label('moli-candidate'), browser_method_label('moli-candidate', language='en')
    zh, en = render(tmp_path, {}, rows)
    assert zh == 'Moli 多构建结果（1 例 9.9.8；2 例 9.9.9）'
    assert en == 'Moli mixed builds (1 case: 9.9.8; 2 cases: 9.9.9)'
    assert browser_method_label('moli-candidate', language='en') == 'Moli candidate build'
    for row, version in zip(rows, ['9.9.9-a', '9.9.9-b', '9.9.9-b']):
        row['provider_metadata']['reported_version'] = version
    assert render(tmp_path, {}, rows) == ('Moli 9.9.9（候选版）', 'Moli 9.9.9 (candidate build)')



@pytest.mark.parametrize('value,expected', [(2500, '2,500'), (None, None),
    (True, None), (-1, None), ('2500', None), (float('inf'), None)])
def test_registered_runtime_parameter_uses_only_valid_numeric_evidence(custom_catalog, value, expected):
    method = custom_catalog['methods']['acme-fetch']
    method['interface']['params'] = [['timeout', 'number', 'Unknown']]
    method['runtime_parameters'] = [{'parameter': 'timeout', 'rules': [{
        'metadata_equals': {}, 'description': {'text': '{budget}{value}',
            'fields': {'budget': 'report_outer_budget'},
            'fragments': {'value': [{'text': '; inner {ms:,} ms',
                'fields': {'ms': 'inner_ms'}, 'positive_numeric_fields': ['ms']}]}},
        'note': 'Read saved parameters.'}]}]
    row = {'provider': 'acme-run', 'provider_metadata': {'engine': 'acme-fetch', 'inner_ms': value}}
    result = method_spec('acme-run', row, timeout_seconds=30, language='en')
    description = result['params'][0][2]
    assert '30-second outer deadline' in description
    if expected:
        assert '; inner '+expected+' ms' in description
    else:
        assert 'inner' not in description



def test_observed_route_matrix_localizes_without_reverse_lookup(monkeypatch):
    from harness.web_fetch.report_interfaces import browser_task_interface_matrix
    cases = {'body': {'id': 'body', 'url': 'https://example.test/', 'track': 'web'}}
    records = [{'provider': 'moli', 'case_id': 'body', 'provider_metadata': {
        'extractor': 'moli_native_markdown', 'dump': 'markdown',
        'wait_until': 'domstable', 'navigation_wait_until': 'load'}}]
    import re
    for compact in (False, True):
        matrix = browser_task_interface_matrix(cases, ['moli'], records, language='en', compact=compact)
        assert not re.search(r'[一-鿿]', json.dumps(matrix, ensure_ascii=False))
        assert 'Markdown' in matrix['rows'][0]['cells']['moli'][0]['description']
    detailed = browser_task_interface_matrix(cases, ['moli'], records, language='en')
    assert detailed['rows'][0]['cells']['moli'][0]['wait'] == 'Navigation wait load; output completion domstable'
    missing = browser_task_interface_matrix(cases, ['unknown'], records=[], language='en')
    assert missing['rows'][0]['cells']['unknown'][0]['description'] == 'Not registered'


def test_english_filters_and_hosted_labels_use_registered_language(tmp_path):
    from pathlib import PurePosixPath
    from bs4 import BeautifulSoup
    from harness.web_fetch.report import filter_controls, render_execution_contract
    from harness.web_fetch.report_i18n import localize_html
    records = [{'provider': 'moli-candidate', 'provider_metadata': {'reported_version': version}}
               for version in ['9.9.8', '9.9.9']]
    records.append({'provider': 'kitesurf', 'provider_metadata': {'engine': 'kitesurf'}})

    @with_measured_method_labels
    def render(out, dataset, rows):
        names = ['moli-candidate', 'kitesurf']
        controls = filter_controls([('provider', '方法', [
            (name, browser_method_label(name)) for name in names])])
        body = controls + render_execution_contract({}, rows, [])
        return localize_html(body, PurePosixPath('analysis/index.html'))

    document = BeautifulSoup(render(tmp_path, {}, records), 'html.parser')
    options = document.select('select[data-filter="provider"] option')
    assert [option['value'] for option in options] == ['', 'moli-candidate', 'kitesurf']
    assert options[1].get_text() == 'Moli mixed builds (1 case: 9.9.8; 1 case: 9.9.9)'
    assert 'hosted backend version not disclosed' in options[2].get_text()
    assert not any(option.find() for option in options)
    import re
    assert not re.search(r'[\u4e00-\u9fff]', document.get_text())


@pytest.mark.parametrize('contract', ['native_markdown_resource_urls', 'native_html_resource_urls', 'native_json_resource_urls'])
@pytest.mark.parametrize('language', ['zh', 'en'])
def test_output_contract_labels_use_selected_method(contract, language):
    label = report_method_catalog(language)['output_contract_labels'][contract].format(method='Acme Fetch')
    assert 'Acme Fetch' in label
    assert 'Moli' not in label


@pytest.mark.parametrize('value', [True, False, None, '30', 0, -1, float('inf'), float('-inf'), float('nan')])
def test_positive_number_rejects_invalid_budgets(value):
    from harness.numbers import positive_number
    assert not positive_number(value)


@pytest.mark.parametrize('value', [0.001, 1, 30.5])
def test_positive_number_accepts_finite_budgets(value):
    from harness.numbers import positive_number
    assert positive_number(value)


def test_catalog_templates_merge_overrides_without_mutating_defaults():
    from harness.web_fetch.methods import _resolve_catalog_templates
    source = {'templates': {'base': {'interface': {'notes': 'shared', 'params': ['base']}}},
              'methods': {'one': {'extends': 'base', 'interface': {'params': ['one']}},
                          'two': {'extends': 'base'}}}
    result = _resolve_catalog_templates(source)
    assert result['methods']['one']['interface'] == {'notes': 'shared', 'params': ['one']}
    result['methods']['one']['interface']['notes'] = 'changed'
    assert result['methods']['two']['interface']['notes'] == 'shared'
    assert source['templates']['base']['interface']['params'] == ['base']


@pytest.mark.parametrize('templates,parent', [({}, 'missing'), ({'loop': {'extends': 'loop'}}, 'loop')])
def test_catalog_templates_reject_missing_or_cyclic_parent(templates, parent):
    from harness.web_fetch.methods import _resolve_catalog_templates
    with pytest.raises(ValueError, match='Unknown|Cyclic'):
        _resolve_catalog_templates({'templates': templates, 'methods': {'test': {'extends': parent}}})


def test_matrix_uses_recorded_provider_type_in_execution_selector(custom_catalog):
    from harness.web_fetch.report_interfaces import browser_task_interface_matrix
    cases = {'body': {'id': 'body', 'url': 'https://example.test/', 'track': 'web'}}
    records = [{'provider': 'acme-fetch', 'provider_type': 'headless_html', 'case_id': 'body',
                'provider_metadata': {'engine': 'acme-fetch'}}]
    part = browser_task_interface_matrix(cases, ['acme-fetch'], records)['rows'][0]['cells']['acme-fetch'][0]
    assert part['mode'] == 'html'
