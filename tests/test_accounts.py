"""Account setup and report access share the method catalog."""
import copy
from pathlib import PurePosixPath

from harness.web_fetch.accounts import account_checklist, env_template
from harness.web_fetch.methods import method_access, report_method_catalog
from harness.web_fetch.report import render_account_requirements
from harness.web_fetch.report_i18n import localize_html


def test_checklist_has_unique_ids_and_official_links():
    entries = account_checklist()
    assert len({item['id'] for item in entries}) == len(entries)
    for item in entries:
        assert item['type'] == 'hosted'
        assert item['env_vars']
        assert all(url.startswith('https://') for _, url in item['links'])


def test_env_template_contains_core_keys_without_values():
    template = env_template()
    assert 'LEXMOUNT_API_KEY=\n' in template
    assert 'FIRECRAWL_API_KEY=\n' in template
    assert 'secret' not in template.casefold()


def test_every_method_uses_one_access_schema():
    for name, method in report_method_catalog()['methods'].items():
        assert 'account_id' not in method
        assert 'account' not in method
        access = method_access(name, [])
        assert access['type'] in {'hosted', 'local'}
        assert access['account'] and access['configuration'] and access['links']
        assert isinstance(access['env_vars'], list)


def test_report_uses_only_measured_methods_and_includes_all_hosted_accounts():
    names = {'firecrawl', 'tavily', 'exa', 'cloudflare-markdown', 'moli', 'crawl4ai'}
    page = render_account_requirements([{'provider': name} for name in names])
    intro = page.split('需要供应商账号的方法：', 1)[1].split('</p>', 1)[0]
    for name in ('firecrawl', 'tavily', 'exa', 'cloudflare'):
        assert name in intro.casefold()
    assert 'Moli' not in intro
    assert 'Crawl4AI' not in intro
    assert 'Browserbase' not in page
    for variable in ('FIRECRAWL_API_KEY', 'TAVILY_API_KEY', 'EXA_API_KEY'):
        assert variable in page


def test_unknown_method_has_explicit_missing_access():
    access = method_access('unknown-method', [])
    assert access['type'] == 'unknown'
    assert 'Not registered' in access['account']
    assert access['env_vars'] == []
    assert 'unknown-method' in render_account_requirements([{'provider': 'unknown-method'}])


def test_local_tools_retain_runtime_commands():
    assert method_access('openclaw-built-in', [])['env_vars'] == [
        'OPENCLAW_REPO', 'OPENCLAW_NODE', 'OPENCLAW_PNPM']
    assert method_access('deepseek-harness-built-in', [])['env_vars'] == [
        'DEEPSEEK_HARNESS_REPO', 'OPENCLAW_NODE', 'DEEPSEEK_PNPM']


def test_cloudflare_retains_both_product_permissions():
    access = method_access('cloudflare-markdown', [])
    assert access['env_vars'] == ['CLOUDFLARE_API_TOKEN', 'CLOUDFLARE_ACCOUNT_ID']
    assert 'Browser Rendering → Edit' in access['configuration']
    assert 'Workers AI → Read' in access['configuration']
    assert 'Workers AI Read 或 Edit' in access['account']


def test_unimplemented_candidates_remain_marked_planned():
    entries = account_checklist()
    planned = {item['id'] for item in entries if not item['implemented']}
    assert 'browserbase' in planned
    assert len(planned) == 12
    catalog = report_method_catalog()['methods']
    assert all(catalog[name]['status'] == 'planned' for name in planned)


def test_json_registered_hosted_method_appears_in_both_languages(monkeypatch):
    catalog = copy.deepcopy(report_method_catalog())
    catalog['methods']['acme-api'] = {
        'label': {'zh': 'Acme API', 'en': 'Acme API'},
        'access': {'type': 'hosted', 'env_vars': ['ACME_API_KEY'],
                   'links': [['Dashboard', 'https://example.com/acme']],
                   'account': 'Requires an account.', 'configuration': 'Create an API key.'}}
    monkeypatch.setattr('harness.web_fetch.methods.report_method_catalog', lambda language='zh': catalog)
    page = render_account_requirements([{'provider': 'acme-api',
        'provider_metadata': {'api_key': 'secret-canary'}}])
    assert '需要供应商账号的方法：' in page
    assert 'Acme API' in page.split('需要供应商账号的方法：', 1)[1].split('</p>', 1)[0]
    english = localize_html(page, PurePosixPath('accounts/index.html'))
    assert 'Methods requiring a provider account: ' in english
    assert 'ACME_API_KEY' in english
    assert 'secret-canary' not in page + english



def test_access_localization_uses_field_identity_when_chinese_text_collides(monkeypatch):
    from harness.web_fetch.methods import _raw_report_method_catalog, _catalog_value
    from harness.web_fetch.report import render_account_requirements
    from harness.web_fetch.report_i18n import localize_html
    from pathlib import PurePosixPath
    catalog = copy.deepcopy(_raw_report_method_catalog())
    for name, meaning in [('alpha-api', 'Create an Alpha account'),
                          ('beta-api', 'Use your existing Beta account')]:
        catalog['methods'][name] = {
            'label': {'zh': name, 'en': name},
            'access': {'type': 'hosted', 'env_vars': [], 'links': [],
                       'account': {'zh': '账号要求', 'en': meaning},
                       'configuration': {'zh': '配置要求', 'en': name+' configuration'}}}
    monkeypatch.setattr('harness.web_fetch.methods.report_method_catalog',
                        lambda language='zh': _catalog_value(catalog, language=language))
    html = render_account_requirements([{'provider': name} for name in ['alpha-api', 'beta-api']])
    english = localize_html(html, PurePosixPath('accounts/index.html'))
    assert 'Create an Alpha account' in english
    assert 'Use your existing Beta account' in english
    assert 'alpha-api configuration' in english
    assert 'beta-api configuration' in english



def test_official_links_render_text_instead_of_escaped_bilingual_markup():
    from bs4 import BeautifulSoup
    from pathlib import PurePosixPath
    from harness.web_fetch.report_i18n import localize_html
    body = render_account_requirements([{'provider': 'moli'}])
    for page in (body, localize_html(body, PurePosixPath('accounts/index.html'))):
        soup = BeautifulSoup(page, 'html.parser')
        links = soup.select('a[href^="https://"]')
        assert links
        assert all('<span' not in link.get_text() for link in links)
        assert all('data-report-en' not in link.get_text() for link in links)


def test_version_aliases_use_their_execution_variables():
    import re
    from harness.web_fetch.methods import browser_method_registry

    for name, config in browser_method_registry().items():
        if config.get('engine') != 'moli':
            continue
        variable = re.fullmatch(r'\$\{([^:}]+)(?::-[^}]*)?}', config['binary']).group(1)
        for language in ('zh', 'en'):
            access = method_access(name, [], language=language)
            assert access['env_vars'] == [variable]
            assert variable in access['configuration']


def test_measured_installation_uses_verified_configuration(tmp_path):
    import hashlib
    import json
    from harness.web_fetch.methods import with_measured_method_labels

    config = tmp_path / 'providers.json'
    providers = {
        'moli-1.1.7': {'engine': 'moli', 'binary': '${FROZEN_OLD_BINARY:-/private/program}'},
        'moli-candidate': {'engine': 'moli', 'binary': '${FROZEN_NEW_BINARY}'},
    }
    config.write_text(json.dumps({'providers': providers}))
    manifests = [{'provider_config_source': str(config),
                  'provider_config_sha256': hashlib.sha256(config.read_bytes()).hexdigest(),
                  'providers': [{'name': name} for name in providers]}]
    records = [{'provider': name, 'provider_metadata': {'engine': 'moli',
                'reported_version': '1.1.7'}} for name in providers]

    @with_measured_method_labels
    def render(out, dataset, records, manifests):
        return render_account_requirements(records)

    page = render(tmp_path, {}, records, manifests)
    assert 'FROZEN_OLD_BINARY' in page and 'FROZEN_NEW_BINARY' in page
    assert 'MOLI_BINARY' not in page and 'MOLI_CANDIDATE_BINARY' not in page
    assert '/private/program' not in page and str(tmp_path) not in page
    assert '/releases/tag/v1.1.7' in page

    # A changed file must never silently become the historical authority.
    config.write_text(json.dumps({'providers': {
        name: {**value, 'binary': '${TODAYS_BINARY}'} for name, value in providers.items()}}))
    page = render(tmp_path, {}, records, manifests)
    assert 'TODAYS_BINARY' not in page and 'FROZEN_OLD_BINARY' not in page
    assert 'installation variables were not recorded' in page
    config.unlink()
    assert 'installation variables were not recorded' in render(tmp_path, {}, records, manifests)
    # The scoped configuration must not leak into another report or a catalog query.
    assert method_access('moli-candidate', [])['env_vars'] == ['MOLI_CANDIDATE_BINARY']


def test_measured_installation_does_not_guess_from_current_config():
    records = [{'provider': 'moli', 'provider_metadata': {'engine': 'moli'}}]
    access = method_access('moli', records, language='en')
    assert access['env_vars'] == []
    assert 'not recorded' in access['configuration']


def test_frozen_installation_rejects_ambiguous_cohort_and_batch_bindings(tmp_path):
    import hashlib
    import json
    from harness.web_fetch.methods import frozen_execution_configs

    def manifest(filename, binary, **extra):
        path = tmp_path / filename
        path.write_text(json.dumps({'providers': {'moli': {
            'engine': 'moli', 'binary': binary,
            'resource_binary': '${CONTAINER_BROWSER}'}}, **extra}))
        return {'provider_config_source': str(path),
                'provider_config_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'providers': [{'name': 'moli'}]}

    first = manifest('first.json', '${FIRST_BROWSER}')
    second = manifest('second.json', '${SECOND_BROWSER}')
    assert frozen_execution_configs([first, second, first])['moli'] is None
    assert frozen_execution_configs([first, first])['moli']['binary'] == '${FIRST_BROWSER}'
    resource = {**first, 'parameters': {'resource_sample_interval_ms': 100}}
    assert frozen_execution_configs([resource])['moli']['binary'] == '${CONTAINER_BROWSER}'
    ambiguous = manifest('ambiguous.json', '${DEFAULT_BROWSER}', cohorts={
        'alternate': {'provider_overrides': {'moli': {'binary': '${ALTERNATE_BROWSER}'}}}})
    assert frozen_execution_configs([ambiguous])['moli'] is None
    link = tmp_path / 'linked.json'
    link.symlink_to(tmp_path / 'first.json')
    assert frozen_execution_configs([{**first, 'provider_config_source': str(link)}])['moli'] is None
