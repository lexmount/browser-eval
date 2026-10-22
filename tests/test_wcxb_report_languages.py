"""Exercise generated WCXB editions and navigation with synthetic scored evidence."""

import json
import runpy
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from harness.report_languages import check_language_contract, localized_report_routes
from harness.web_fetch.report import generate_wcxb
from tests.wcxb_fixture import create_report_inputs


@pytest.mark.parametrize('include_english', [False, True])
def test_wcxb_language_output_and_every_language_link(tmp_path, include_english):
    inputs = create_report_inputs(tmp_path, english_source=include_english)
    root = inputs[0]
    if include_english:
        generate_wcxb(*inputs, include_english=True)
    else:
        generate_wcxb(*inputs)
    check_language_contract(root)
    routes = {path.relative_to(root).as_posix() for path in root.rglob('*.html')
              if path.relative_to(root).parts[0] != 'en'}
    editions = localized_report_routes(root, routes)
    assert set(editions) == ({'zh', 'en'} if include_english else {'zh'})
    assert 'cases/4017.html' in routes and 'analysis/index.html' in routes
    for route in routes:
        soup = BeautifulSoup((root / route).read_text(), 'html.parser')
        english_link = next(link for link in soup.select('header a') if link.get_text() == 'English')
        assert english_link['href'] == ('en/' + route if include_english else 'en/index.html')
        assert (root / english_link['href']).is_file()
    if not include_english:
        assert {path.relative_to(root / 'en').as_posix()
                for path in (root / 'en').rglob('*') if path.is_file()} == {'index.html'}


@pytest.mark.parametrize('include_english', [False, True])
def test_wcxb_command_forwards_requested_language(tmp_path, monkeypatch, include_english):
    from harness.web_fetch import report_build

    captured = []
    monkeypatch.setattr(report_build, 'build_wcxb_report',
                        lambda *args, **kwargs: captured.append(kwargs) or tmp_path / 'index.html')
    root = Path(__file__).resolve().parents[1]
    argv = [str(root / 'scripts/build_report.py'), '--wcxb', '--evidence', str(tmp_path),
            '--output', str(tmp_path / 'new-report')]
    if include_english:
        argv.append('--include-english')
    monkeypatch.setattr('sys.argv', argv)
    with pytest.raises(SystemExit) as result:
        runpy.run_path(argv[0], run_name='__main__')
    assert result.value.code == 0
    binding = json.loads((root / 'evaluation/wcxb/report-content-binding.json').read_text())
    assert captured == [{'include_english': include_english, 'language': 'en', 'authority_root': root,
                         'content_binding': binding}]


def test_wcxb_renders_supplied_interpretation_without_regenerating_prose(tmp_path):
    inputs = create_report_inputs(tmp_path, english_source=False)
    inputs[-1]['guide']['zh'] = '<section id="bound-finding">独立绑定的结论</section>'
    generate_wcxb(*inputs)
    soup = BeautifulSoup((inputs[0] / 'guide/index.html').read_text(), 'html.parser')
    assert soup.select_one('main').decode_contents() == inputs[-1]['guide']['zh']


def test_wcxb_does_not_fall_back_when_requested_authored_language_is_missing(tmp_path):
    inputs = create_report_inputs(tmp_path, english_source=False)
    with pytest.raises(ValueError, match='lacks requested languages'):
        generate_wcxb(*inputs, include_english=True)
    assert not (inputs[0] / 'index.html').exists()


@pytest.mark.parametrize('include_english', [False, True])
@pytest.mark.parametrize('tamper', ['none', 'body', 'binding', 'missing-binding'])
def test_wcxb_release_checks_bound_article_body(tmp_path, include_english, tamper):
    import hashlib

    from harness.web_fetch.wcxb_report_check import check_bound_interpretation

    inputs = create_report_inputs(tmp_path, english_source=include_english)
    generate_wcxb(*inputs, include_english=include_english)
    root = inputs[0]
    binding = {'rendered_sha256': {'guide': {
        lang: hashlib.sha256(body.encode()).hexdigest()
        for lang, body in inputs[-1]['guide'].items()}}}
    language = 'en' if include_english else 'zh'
    if tamper == 'body':
        page = root / ('en/guide/index.html' if include_english else 'guide/index.html')
        page.write_text(page.read_text().replace('Synthetic interpretation', 'Changed finding')
                        if include_english else page.read_text().replace('合成报告解读', '篡改结论'))
    elif tamper == 'binding':
        binding['rendered_sha256']['guide'][language] = '0' * 64
    elif tamper == 'missing-binding':
        binding.pop('rendered_sha256')
    if tamper == 'none':
        check_bound_interpretation(root, binding)
    else:
        with pytest.raises(ValueError, match='differs from bound content'):
            check_bound_interpretation(root, binding)


def test_wcxb_uses_measured_versions_and_actual_configuration_count(tmp_path):
    inputs = list(create_report_inputs(tmp_path, english_source=True))
    root, rows, summary, ground_truth, identities, profile, content = inputs
    # A smaller cohort and a different release must not inherit the old report.
    inputs[1] = [row for row in rows if row['provider'] != 'obscura']
    del summary['providers']['obscura']
    del identities['obscura']
    identities['moli'].update(engine='moli', reported_version='7.8.9')
    identities['moli-pr643'].update(engine='moli', reported_version='7.8.10-dev.42')
    generate_wcxb(*inputs, include_english=True)
    for prefix in ('', 'en/'):
        home = BeautifulSoup((root / prefix / 'index.html').read_text(), 'html.parser')
        assert str(len(summary['providers'])) in home.h1.get_text()
        assert 'Moli 7.8.9' in home.get_text()
        assert 'Moli 7.8.10-dev.42' in home.get_text()
        accounts = BeautifulSoup((root / prefix / 'accounts/index.html').read_text(), 'html.parser')
        assert accounts.select_one('a[href="https://github.com/lexmount/moli/releases/tag/v7.8.9"]')
        assert 'MOLI_BINARY' not in accounts.get_text()
        for route in ('index.html', 'accounts/index.html', 'adoption/index.html',
                      'methodology/index.html', 'datasets/index.html', 'review/index.html',
                      'changes/index.html', 'reproduce/index.html'):
            text = (root / prefix / route).read_text()
            assert '1.1.8' not in text
            assert 'six configurations' not in text and '六种配置' not in text
