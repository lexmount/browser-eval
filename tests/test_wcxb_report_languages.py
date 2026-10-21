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

    monkeypatch.delenv('BROWSER_EVAL_REPORT_LANGUAGE', raising=False)
    from harness import report_languages
    monkeypatch.setattr(report_languages, 'REPORT_LANGUAGE_ENV_FILE', tmp_path / 'no-local-env')
    captured = []
    monkeypatch.setattr(report_build, 'build_wcxb_report',
                        lambda *args, **kwargs: captured.append(kwargs) or tmp_path / 'index.html')
    root = Path(__file__).resolve().parents[1]
    profile = tmp_path / 'instance.json'
    profile.write_text(json.dumps({'comparison_kind': 'wcxb_frozen_html_to_markdown', 'sample_manifest': 'sample.json'}))
    binding = {'kind': 'test-content'}
    (tmp_path / 'report-content-binding.json').write_text(json.dumps(binding))
    argv = [str(root / 'scripts/build_report.py'), '--profile', str(profile), '--wcxb', '--evidence', str(tmp_path),
            '--output', str(tmp_path / 'new-report')]
    if include_english:
        argv.append('--include-english')
    monkeypatch.setattr('sys.argv', argv)
    with pytest.raises(SystemExit) as result:
        runpy.run_path(argv[0], run_name='__main__')
    assert result.value.code == 0
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


@pytest.mark.parametrize('providers', [
    ('moli', 'moli-pr643'),
    ('moli', 'moli-pr643', 'chromium', 'obscura'),
])
def test_wcxb_instance_counts_versions_and_interfaces_follow_selected_evidence(tmp_path, providers):
    root, rows, summary, truth, identities, profile, content = create_report_inputs(tmp_path)
    rows = [row for row in rows if row['provider'] in providers]
    summary['providers'] = {p: summary['providers'][p] for p in providers}
    profile['providers'] = list(providers)
    identities['moli']['reported_version'] = 'moli 2.3.4'
    profile['candidate_source_commit'] = 'abcdef12' + '0' * 32
    profile['max_failure_retries'] = 3
    generate_wcxb(root, rows, summary, truth, identities, profile, content, include_english=True)
    for prefix, count_text in [('', f'{len(providers)} 种配置'), ('en/', f'{len(providers)} configurations')]:
        home = BeautifulSoup((root / prefix / 'index.html').read_text(), 'html.parser')
        assert count_text in home.select_one('h1').get_text()
        assert 'Moli 2.3.4' in home.get_text()
        assert 'Moli moli' not in home.get_text()
        assert 'abcdef12' in home.get_text()
        for route in ('index.html', 'methodology/index.html', 'datasets/index.html', 'changes/index.html'):
            text = (root / prefix / route).read_text()
            assert 'six configurations' not in text
            assert '六种配置' not in text
            assert 'five browsers' not in text
            assert '五款浏览器' not in text
            if not prefix:
                from harness.web_fetch.wcxb_report_check import ReportShape, require_bilingual_shape
                zh, en = ReportShape(), ReportShape()
                zh.feed(text)
                en.feed((root / 'en' / route).read_text())
                require_bilingual_shape(zh, en, route)
        method = (root / prefix / 'methodology/index.html').read_text()
        assert 'Kitesurf receives' not in method
        interfaces = BeautifulSoup((root / prefix / 'adoption/index.html').read_text(), 'html.parser').select_one('pre').get_text()
        assert 'kitesurf:' not in interfaces
        assert ('"engine": "chromium"' in interfaces) == ('chromium' in providers)
        from harness.web_fetch.wcxb_latency import load_full_paired_latency, summarize_full_paired_latency
        from harness.web_fetch.wcxb_report_check import require_home_interpretation
        _, calls = load_full_paired_latency(root / 'research/wcxb-full-paired-latency', profile, set(truth))
        paired = summarize_full_paired_latency(calls, set(truth))
        lang = 'zh' if not prefix else 'en'
        html = (root / prefix / 'index.html').read_text()
        checked = require_home_interpretation(html, summary, paired, lang, scores=rows)
        checked.select_one('[data-f1-relation="higher"]')['data-count'] = '99'
        with pytest.raises(ValueError, match='counts differ'):
            require_home_interpretation(checked, summary, paired, lang, scores=rows)
        assert ('最多重试 3 次' if lang == 'zh' else 'up to 3 retries') in method
        assert 'cases/5458.html' not in html


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


def test_new_wcxb_population_does_not_inherit_historical_findings(tmp_path):
    from harness.web_fetch.wcxb_report_check import require_home_interpretation
    from harness.web_fetch.wcxb_latency import load_full_paired_latency, summarize_full_paired_latency

    inputs = create_report_inputs(tmp_path, english_source=True)
    root, rows, summary, references, identities, profile, content = inputs
    before = json.dumps([rows, summary, references, identities, profile, content], sort_keys=True)
    _, calls = load_full_paired_latency(root / 'research/wcxb-full-paired-latency', profile,
                                        set(references))
    latency = summarize_full_paired_latency(calls, set(references))
    generate_wcxb(*inputs, include_english=True)
    assert json.dumps([rows, summary, references, identities, profile, content], sort_keys=True) == before
    for lang, prefix in (('zh', ''), ('en', 'en/')):
        raw = (root / prefix / 'index.html').read_text()
        soup = require_home_interpretation(raw, summary, latency, lang, {"moli": "release", "moli-pr643": "pr"})
        assert soup.select_one('#moli-change') is None
        assert '5412' not in raw and '5458' not in raw and 'PR #643' not in raw
        guide = BeautifulSoup((root / prefix / 'guide/index.html').read_text(), 'html.parser')
        assert guide.select_one('main').decode_contents() == content['guide'][lang]
        wrong = BeautifulSoup(raw, 'html.parser')
        context = wrong.select_one('#paired-latency-context')
        count = f'{summary["case_count"]} ' + ('页' if lang == 'zh' else 'pages')
        context.string = context.get_text(' ', strip=True).replace(count, '504 pages')
        with pytest.raises(ValueError, match='corrected population'):
            require_home_interpretation(wrong, summary, latency, lang, {"moli": "release", "moli-pr643": "pr"})
        wrong = BeautifulSoup(raw, 'html.parser')
        wrong.select_one('#paired-latency-context').decompose()
        with pytest.raises(ValueError, match='missing'):
            require_home_interpretation(wrong, summary, latency, lang, {"moli": "release", "moli-pr643": "pr"})


@pytest.mark.parametrize('participants', [
    ('moli', 'moli-pr643'),
    ('moli-release', 'moli-1.1.7', 'moli-candidate'),
])
def test_wcxb_public_build_uses_bound_optional_case_and_profile_participants(tmp_path, participants):
    import hashlib
    from harness.web_fetch.report_build import build_wcxb_report
    from harness.web_fetch.wcxb_scoring import score
    from harness.web_fetch.wcxb_report_check import check_bound_interpretation
    from tests.wcxb_fixture import create_cohort, write_json

    cohort = create_cohort(tmp_path / 'cohort', paired=True, providers=participants)
    score(**cohort)
    source = cohort['evidence_root']
    profile = json.loads(cohort['profile_path'].read_text())
    profile.update(source_repository='https://example.invalid/source', source_commit='a' * 40)
    optional_root = source / 'optional-new-case'
    builds = {}
    for name, binary in (('release', profile['release_binary_sha256']),
                         ('candidate', profile['candidate_binary_sha256'])):
        builds[name] = {'binary_sha256': binary, 'returncode': 0,
                        'description_present': name == 'release'}
        for extension, field in (('md', 'output_sha256'), ('stderr', 'stderr_sha256')):
            path = optional_root / f'{name}.{extension}'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('Synthetic optional evidence')
            builds[name][field] = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = optional_root / 'receipt.json'
    write_json(receipt, {'case_id': 'case-001', 'source_sha256': profile['source_dataset_sha256'],
                        'builds': builds})
    profile['optional_ui_receipt_sha256'] = hashlib.sha256(receipt.read_bytes()).hexdigest()
    review_name = 'research/wcxb-representative-review.json'
    review = source / 'report-assets' / review_name
    write_json(review, {'scope': 'targeted_representative_anomaly_review', 'case_count': 0,
                        'result_count': 0, 'total_results': 2 * len(participants), 'unreviewed_results': 2 * len(participants), 'cases': []})
    profile['report_assets'] = {review_name: hashlib.sha256(review.read_bytes()).hexdigest()}
    guide = '<section><h1>Only the supplied synthetic finding</h1></section>'
    content = source / 'report-assets/report-content.json'
    write_json(content, {'schema_version': 1, 'guide': {'zh': guide, 'en': guide}})
    authority = tmp_path / 'authority'
    authority.mkdir()
    (authority / 'contract.json').write_text('{}')
    binding = {'schema_version': 1, 'relative_path': 'report-content.json',
               'sha256': hashlib.sha256(content.read_bytes()).hexdigest(),
               'evidence': {receipt.relative_to(source).as_posix(): profile['optional_ui_receipt_sha256']},
               'authority': {'contract.json': hashlib.sha256(b'{}').hexdigest()},
               'rendered_sha256': {'guide': {lang: hashlib.sha256(guide.encode()).hexdigest()
                                            for lang in ('zh', 'en')}}}
    before = {p.relative_to(source): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in source.rglob('*') if p.is_file()}
    output = tmp_path / 'report'
    build_wcxb_report(source, cohort['source'], cohort['sample_manifest'], profile, output,
                      content_binding=binding, authority_root=authority, include_english=True)
    check_bound_interpretation(output, binding)
    assert (output / 'research/wcxb-optional-new-case/receipt.json').read_bytes() == receipt.read_bytes()
    assert 'Only the supplied synthetic finding' in (output / 'guide/index.html').read_text()
    assert '5412' not in (output / 'index.html').read_text()
    assert set(json.loads((output / 'evidence/case-001.json').read_text())['outputs']) == set(profile['providers'])
    assert before == {p.relative_to(source): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in source.rglob('*') if p.is_file()}


@pytest.mark.parametrize('tied', [False, True])
def test_wcxb_leaders_and_interfaces_follow_participants(tmp_path, tied):
    inputs = list(create_report_inputs(tmp_path))
    root, rows, summary, _, identities, profile, _ = inputs
    participants = ('moli', 'moli-pr643')
    inputs[1] = [row for row in rows if row['provider'] in participants]
    summary['providers'] = {p: summary['providers'][p] for p in participants}
    inputs[4] = {p: identities[p] for p in participants}
    profile['providers'] = list(participants)
    for index, provider in enumerate(participants):
        inputs[4][provider].update(engine='moli', reported_version=f'7.0.{index}')
        summary['providers'][provider]['quality_f1'] = 1.0 if tied else 0.8 + index * 0.1
        summary['providers'][provider]['mean_latency_ms'] = 11 if tied else 12 - index
    generate_wcxb(*inputs, include_english=True)
    for prefix in ('', 'en/'):
        home = BeautifulSoup((root / prefix / 'index.html').read_text(), 'html.parser')
        lead = home.select_one('main section p').get_text()
        assert 'Moli 7.0.1' in lead
        assert ('Moli 7.0.0' in lead) == tied
        for term in (('并列最高', '并列最短') if not prefix else ('tie for the highest', 'tie for the shortest')):
            assert (term in lead) == tied
        for metric in ('f1', 'mean'):
            marked = home.select(f'#wcxb-core td[data-metric="{metric}"] .comparison-winner')
            assert len(marked) == (0 if tied else 1)
        method = BeautifulSoup((root / prefix / 'methodology/index.html').read_text(), 'html.parser')
        adoption = BeautifulSoup((root / prefix / 'adoption/index.html').read_text(), 'html.parser')
        interface = adoption.select_one('main pre').get_text()
        for name in ('Kitesurf', 'Lightpanda', 'Obscura', 'Chromium'):
            assert name not in method.select_one('main').get_text()
            assert name not in interface
        for name in ('Moli 7.0.0', 'Moli 7.0.1'):
            assert name in method.get_text()
            assert name in interface


@pytest.mark.parametrize('tied', [False, True])
def test_wcxb_release_highlights_accept_actual_ties_and_reject_corruption(tmp_path, tied):
    from harness.web_fetch.wcxb_report_check import require_comparison_highlights

    inputs = create_report_inputs(tmp_path)
    for index, stats in enumerate(inputs[2]['providers'].values()):
        for key in ('quality_precision', 'quality_recall', 'quality_f1'):
            stats[key] = 1.0 if tied else 1.0 - index * 0.1
        for key in ('mean_latency_ms', 'p50_latency_ms', 'p95_latency_ms'):
            stats[key] = 11.0 if tied else 11.0 + index
    generate_wcxb(*inputs, include_english=True)
    for prefix in ('', 'en/'):
        source = (inputs[0] / prefix / 'index.html').read_text()
        require_comparison_highlights(source)
        page = BeautifulSoup(source, 'html.parser')
        marked = page.select('#wcxb-core .comparison-winner')
        assert bool(marked) is not tied
        unmarked = next(cell for cell in page.select('#wcxb-core td[data-metric]')
                        if not cell.select_one('.comparison-winner'))
        unmarked.append(BeautifulSoup('<strong class="comparison-winner">wrong</strong>', 'html.parser'))
        with pytest.raises(ValueError, match='highlight'):
            require_comparison_highlights(page)
        if not tied:
            page = BeautifulSoup(source, 'html.parser')
            page.select_one('#wcxb-core .comparison-winner').unwrap()
            with pytest.raises(ValueError, match='highlight'):
                require_comparison_highlights(page)


@pytest.mark.parametrize('rename', [False, True])
def test_wcxb_registered_aliases_use_shared_interfaces_and_bound_timing_roles(tmp_path, rename):
    from copy import deepcopy
    from harness.web_fetch.wcxb_report_check import require_home_interpretation
    from harness.web_fetch.wcxb_latency import load_full_paired_latency, summarize_full_paired_latency
    from harness.web_fetch.wcxb_scoring import paired_latency_methods

    inputs = list(create_report_inputs(tmp_path))
    root, rows, summary, references, identities, profile, _ = inputs
    new = 'moli-release' if rename else 'moli-1.1.7'
    summary['providers'][new] = deepcopy(summary['providers']['moli'])
    identities[new] = {**identities['moli'], 'engine': 'moli', 'reported_version': '1.1.7'}
    if rename:
        del summary['providers']['moli']
        del identities['moli']
        if profile.get('paired_comparison'):
            profile['paired_comparison'] = {key: new if value == 'moli' else value
                                            for key,value in profile['paired_comparison'].items()}
        for row in rows:
            if row['provider'] == 'moli':
                row['provider'] = new
    else:
        identities[new]['binary_sha256'] = 'c' * 64
        rows.extend({**deepcopy(row), 'provider': new} for row in list(rows) if row['provider'] == 'moli')
    import shutil
    shutil.copytree(root / 'artifacts/moli', root / 'artifacts' / new)
    profile['providers'] = list(summary['providers'])
    before = json.dumps(inputs[1:], sort_keys=True)
    generate_wcxb(*inputs, include_english=True)
    assert json.dumps(inputs[1:], sort_keys=True) == before
    pair = paired_latency_methods(profile, {'providers': [
        {'provider': p, 'identity': identity} for p, identity in identities.items()]})
    _, calls = load_full_paired_latency(root / 'research/wcxb-full-paired-latency', profile, set(references))
    timing = summarize_full_paired_latency(calls, set(references))
    for lang, prefix in (('zh', ''), ('en', 'en/')):
        require_home_interpretation((root / prefix / 'index.html').read_text(), summary, timing, lang, pair)
        provider = BeautifulSoup((root / prefix / f'providers/{new}.html').read_text(), 'html.parser')
        assert ('未提供可核对的接口路线' if lang == 'zh' else 'do not provide a verifiable route') in provider.get_text()
        adoption = BeautifulSoup((root / prefix / 'adoption/index.html').read_text(), 'html.parser')
        example = adoption.select_one('main pre').get_text()
        assert 'Moli 1.1.7' in example
        assert 'fetch' in example

