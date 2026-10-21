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
