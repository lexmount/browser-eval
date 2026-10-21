"""New methods and executable versions must not require renderer changes."""
import copy
import json

import pytest

from harness.web_fetch.methods import (
    browser_method_label, is_browser_comparison, method_version,
    report_method_catalog, with_measured_method_labels,
)
from harness.web_fetch.report_interfaces import method_access, method_spec, render_interfaces


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
    monkeypatch.setattr('harness.web_fetch.methods.report_method_catalog', lambda: catalog)
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
    assert saved['interface']['entry'] == '未登记 / Not registered'
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


def test_colocated_method_translations_remain_available_to_every_report():
    from harness.web_fetch.methods import report_method_translations
    from harness.web_fetch.report_i18n import translate_text
    for original, translated in report_method_translations().items():
        assert translate_text(original) == translated, original
