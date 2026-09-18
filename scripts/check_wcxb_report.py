"""Fast structural and evidence check for the current WCXB report."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from bs4 import BeautifulSoup
from html.parser import HTMLParser
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.report_presentation_contract import check_presentation
from harness.web_fetch.safety import contained_path
from harness.web_fetch.storage import data_home

ROOT = Path(__file__).resolve().parents[1]


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.lang = None
        self.ids = set()
        self.results = 0
        self.outputs = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'html': self.lang = values.get('lang')
        if values.get('id'): self.ids.add(values['id'])
        if tag == 'tr' and values.get('data-provider'): self.results += 1
        if tag == 'pre' and values.get('data-provider'): self.outputs += 1


class ReportShape(HTMLParser):
    """Compare authored bilingual structure while excluding verbatim evidence blocks."""
    def __init__(self):
        super().__init__()
        self.tags = Counter()
        self.ids = set()
        self.links = Counter()
        self.numbers = Counter()
        self.controls = Counter()
        self.untranslated = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.tags[tag] += 1
        if values.get('id'):
            self.ids.add(values['id'])
        if tag == 'a' and values.get('href'):
            self.links[re.sub(r'^en/', '', values['href'])] += 1
        for name in ('value', 'data-provider', 'data-case', 'data-result',
                     'data-diagnosis', 'data-page-type', 'data-sort', 'data-core-sort'):
            if name in values:
                self.controls[(tag, name, values[name])] += 1
        for name in ('aria-label', 'title', 'placeholder', 'alt'):
            value = values.get(name, '')
            if re.search(r'[\u4e00-\u9fff]', value) and value != '中文':
                self.untranslated.append(f'{name}: {value[:80]}')
        if tag in {'pre', 'code', 'script', 'style'}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {'pre', 'code', 'script', 'style'}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, value):
        if self.skip:
            return
        self.numbers.update(re.findall(r'\d+(?:[.,]\d+)*(?:%|\b)', value))
        if re.search(r'[\u4e00-\u9fff]', value) and value.strip() != '中文':
            self.untranslated.append(value.strip()[:80])


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def require_bilingual_shape(zh: ReportShape, en: ReportShape, route: str) -> None:
    require(zh.tags == en.tags and zh.ids == en.ids and zh.links == en.links
            and zh.numbers == en.numbers and zh.controls == en.controls,
            f'WCXB bilingual structure or value differs: {route}')


def require_analysis_controls(html: str | BeautifulSoup, result_count: int) -> BeautifulSoup:
    soup = BeautifulSoup(html, 'html.parser') if isinstance(html, str) else html
    if len(soup.select('tr[data-provider]')) != result_count:
        raise ValueError('Qualified result inventory is incomplete')
    if any(soup.select_one(selector) is None for selector in (
            '#compare-baseline', '[name="compare-provider"]', '#compare-relation',
            '#comparison-rows', '#diagnosis-dialog',
            '#diagnosis-dialog [data-call-log-close]',
            '#diagnosis-dialog [data-diagnosis-link]')):
        raise ValueError('Comparison or diagnosis control is missing')
    if len(soup.select('[data-diagnosis-open]')) != result_count:
        raise ValueError('Result row lacks a diagnosis control')
    return soup


def require_case_summary(html: str | BeautifulSoup, lang: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, 'html.parser') if isinstance(html, str) else html
    label = '同一网页的全部方法结果' if lang == 'zh' else 'All method results for this page'
    if sum(node.get_text(' ', strip=True) == label for node in soup.select('h2')) != 1:
        raise ValueError('Result detail has missing or duplicate summary')
    return soup


def check(root: Path) -> None:
    root = root.resolve()
    profile = json.loads((ROOT / 'evaluation/current-report.json').read_text())
    sample_file = root / 'sample-manifest.json'
    sample = json.loads(sample_file.read_text())
    require(sample.get('scope') == 'qualified_test', 'WCXB current selection is not source-qualified')
    certificate = json.loads((root / 'certification.json').read_text())
    summary = json.loads((root / 'wcxb-score-summary.json').read_text())
    scores = [json.loads(line) for line in (root / 'wcxb-scores.jsonl').read_text().splitlines() if line]
    sample_hash = digest(sample_file)
    require(sample_hash == profile['sample_manifest_sha256'] == summary['sample_sha256'], 'WCXB sample hash differs')
    require(sample_hash == certificate['sample_sha256_before'] == certificate['sample_sha256_after'], 'WCXB sample changed during calls')
    require(certificate['complete'] is True, 'WCXB certificate is incomplete')
    require(certificate['source_sha256_before'] == certificate['source_sha256_after'] == summary['source_sha256'] == profile['source_dataset_sha256'], 'WCXB source hash differs')
    require(summary.get('official_evaluator_sha256') == profile['official_evaluator_sha256'], 'WCXB official evaluator hash differs')
    providers = profile['providers']
    case_ids = {row['file_id'] for row in sample['cases']}
    excluded = {row['file_id'] for row in sample.get('exclusions', [])}
    qualified = case_ids - excluded
    require(len(case_ids) == profile['case_count'] == 504, 'WCXB case count differs')
    require(len(scores) == len(case_ids) * len(providers), 'WCXB score count differs')
    require(summary['quality_case_count'] == profile['primary_quality_case_count'] == sample['primary_quality_case_count'] == 504, 'WCXB quality denominator differs')
    require(summary['exclusions'] == sample.get('exclusions', []) == [], 'WCXB current cohort contains excluded cases')
    require(summary['benchmark'] == sample['dataset_id'] == certificate['benchmark'], 'WCXB certified dataset identity differs')
    require({(r['provider'], r['case_id']) for r in scores} == {(p, c) for p in providers for c in case_ids}, 'WCXB method-case matrix differs')
    require([r['provider'] for r in certificate['providers']] == providers, 'WCXB certificate participants differ')
    candidate = next(r for r in certificate['providers'] if r['provider'] == 'moli-pr643')
    require(candidate['identity'].get('binary_sha256') == profile['candidate_binary_sha256'],
            'WCXB candidate binary differs from the current report profile')
    released = next(r for r in certificate['providers'] if r['provider'] == 'moli')
    require(released['identity'].get('binary_sha256') == profile['release_binary_sha256'],
            'WCXB released Moli binary differs from the current report profile')
    from harness.web_fetch.wcxb_latency import load_paired_latency
    load_paired_latency(root / 'research/wcxb-paired-latency.json', profile, case_ids)
    evidence_hashes = json.loads((root / 'evidence-manifest.json').read_text())
    require(set(evidence_hashes) == case_ids, 'WCXB evidence inventory differs')
    by_case = {}
    for row in scores:
        by_case.setdefault(row['case_id'], {})[row['provider']] = row
        require(row['quality_eligible'] == (row['case_id'] not in excluded), 'WCXB quality eligibility differs')
        for retry in row.get('retry_failures', []):
            retry_path = contained_path(root, retry['artifact_path'])
            require(retry_path.is_file() and not retry_path.is_symlink() and digest(retry_path) == retry['output_sha256'], 'WCXB retry evidence differs')
        require(all(0 <= row[k] <= 1 for k in ('precision', 'recall', 'f1', 'with_rate', 'without_rate')), 'WCXB score is out of range')
    for case in case_ids:
        path = root / 'evidence' / f'{case}.json'
        require(path.is_file() and not path.is_symlink() and digest(path) == evidence_hashes[case], 'WCXB case evidence differs')
        evidence = json.loads(path.read_text())
        require(evidence['case_id'] == case and set(evidence['outputs']) == set(providers), 'WCXB case evidence participants differ')
        for provider in providers:
            require(hashlib.sha256(evidence['source_html'].encode()).hexdigest() == by_case[case][provider]['input_html_sha256'], 'WCXB source evidence hash differs')
            require(hashlib.sha256(evidence['reference'].encode()).hexdigest() == by_case[case][provider]['reference_sha256'], 'WCXB reference evidence hash differs')
            require(hashlib.sha256(evidence['outputs'][provider].encode()).hexdigest() == by_case[case][provider]['output_sha256'], 'WCXB output evidence hash differs')
            retries = evidence['retry_failures'][provider]
            require(len(retries) == len(by_case[case][provider].get('retry_failures', [])), 'WCXB retry count differs')
            for retry in retries:
                require(hashlib.sha256(retry['output'].encode()).hexdigest() == retry['output_sha256'], 'WCXB retry output hash differs')
    expected = {'index.html', 'datasets/index.html', 'analysis/index.html', 'methodology/index.html',
                'review/index.html', 'guide/index.html', 'adoption/index.html', 'reproduce/index.html',
                'changes/index.html', 'accounts/index.html',
                *(f'providers/{provider}.html' for provider in providers),
                *(f'cases/{case}.html' for case in qualified)}
    shapes = {'zh': {}, 'en': {}}
    for lang, prefix in (('zh', ''), ('en', 'en/')):
        routes = {str(path.relative_to(root / prefix)) for path in (root / prefix).rglob('*.html')}
        if not prefix:
            routes = {route for route in routes if not route.startswith('en/')}
        require(routes == expected, f'WCXB {lang} route inventory differs: {sorted(expected-routes)[:5]}')
        for route in expected:
            path = root / prefix / route
            html = path.read_text(encoding='utf-8')
            page = Page(); page.feed(html)
            shape = ReportShape(); shape.feed(html)
            shapes[lang][route] = shape
            require(page.lang == lang, f'WCXB language differs: {route}')
            if lang == 'en':
                require(not shape.untranslated, f'Untranslated WCXB interface: {route}: {shape.untranslated[:3]}')
            private_paths = (str(ROOT.resolve()), str(data_home()))
            require(all(path not in html for path in private_paths), f'WCXB private path leaked: {route}')
            if route == 'analysis/index.html':
                require(page.results == len(qualified) * len(providers), 'Qualified result inventory differs')
                require('id="scope"' in html and 'value="fail"' in html and 'value="pass"' in html and 'value="all"' in html, 'Result status filters missing')
                require('id="diagnosis"' in html and 'value="issues"' in html, 'Result diagnosis filter missing')
                require_analysis_controls(html, len(qualified) * len(providers))
                require(all(f'data-case="{case}"' not in html for case in excluded), 'Invalid references entered result inventory')
            if route == 'guide/index.html':
                guide = BeautifulSoup(html, 'html.parser')
                headings = {node.get_text(' ', strip=True) for node in guide.select('h2')}
                require(('Obscura 为什么分数更高' if lang == 'zh' else 'Why Obscura scores higher') in headings,
                        'Obscura content analysis is missing')
                require(('Moli 两版的耗时差异' if lang == 'zh' else 'Why the Moli builds have different latency') in headings,
                        'Paired Moli latency analysis is missing')
                require(guide.select_one('a[href="research/wcxb-paired-latency.json"]') is not None,
                        'Paired latency evidence link is missing')
                require(guide.select_one('a[href$="cases/4461.html#obscura"]') is not None,
                        'Obscura boundary counterexample is missing')
            if route in ('index.html', 'datasets/index.html'):
                check_presentation(html)
                if route == 'index.html':
                    require('id="home-type"' in html, 'Page-type filter missing')
                    require('官方全量 F1' not in html and 'Official full F1' not in html, 'Invalid full-reference score remains visible')
                    require(len(BeautifulSoup(html, 'html.parser').select('#wcxb-core .comparison-winner')) >= 4, 'Core comparison winners missing')
                    first_card = BeautifulSoup(html, 'html.parser').select_one('.lead-grid .panel')
                    require(first_card is not None and all(word not in first_card.get_text() for word in ('得分', 'scores')),
                            'Home dataset card claims ownership of measured scores')
                    if all(summary['providers'][provider]['success_latency_count'] == len(case_ids) for provider in providers):
                        require('Conditional latency for delivered content' not in html and '仅成功交付的条件耗时' not in html,
                                'Duplicate conditional latency table remains on the home page')
                else:
                    require('id="wcxb-dataset"' in html and 'id="dataset-type"' in html and 'id="dataset-query"' in html, 'Dataset inventory filters missing')
                    dataset = BeautifulSoup(html, 'html.parser')
                    require(len(dataset.select('#wcxb-dataset tbody tr')) == len(case_ids), 'Dataset case inventory differs')
                    require('F1' not in dataset.select_one('#wcxb-dataset thead').get_text(), 'Measured scores leaked into the dataset inventory')
                    require(len(dataset.select('#wcxb-dataset tbody a[href$="#reference"]')) == len(case_ids), 'Dataset references are not linked')
                    require(('搜索网页 ID 或地址' if lang == 'zh' else 'Search page ID or URL') in dataset.get_text(),
                            'Dataset search label does not describe its searchable fields')
            if route.startswith('cases/'):
                require_case_summary(html, lang)
                require(set(providers) <= page.ids and page.outputs == len(providers), f'Case participant evidence missing: {route}')
                require(f'evidence/{Path(route).stem}.json' in html, f'Case evidence link missing: {route}')
                require('id="preview"' in html and 'class="frozen-preview"' in html, f'Frozen-page preview missing: {route}')
                require('id="reference"' in html, f'Human reference anchor missing: {route}')
                case = Path(route).stem
                require(BeautifulSoup(html, 'html.parser').select_one(f'.hero .source a[href$="cases/{case}.html#preview"]') is not None,
                        f'Case source URL does not link to the frozen preview: {route}')
                for provider in providers:
                    row = by_case[case][provider]
                    if row['error_message']:
                        require(row['error_message'] in BeautifulSoup(html, 'html.parser').get_text(), f'Exact error missing: {route}')
    for route in expected:
        zh, en = shapes['zh'][route], shapes['en'][route]
        require_bilingual_shape(zh, en, route)
    untranslated = ReportShape(); untranslated.feed('<p>未翻译的界面文字</p>')
    require(bool(untranslated.untranslated), 'Untranslated-English negative control was accepted')
    broken = ReportShape()
    broken.feed((root / 'en/index.html').read_text().replace('href="en/analysis/index.html"',
                'href="en/broken.html"', 1))
    try:
        require_bilingual_shape(shapes['zh']['index.html'], broken, 'index.html')
    except ValueError:
        pass
    else:
        raise AssertionError('A broken bilingual navigation link was accepted')
    # Negative controls ensure the semantic contract rejects missing or wrong highlights.
    home = BeautifulSoup((root / 'index.html').read_text(), 'html.parser')
    winner = home.select_one('#wcxb-core .comparison-winner')
    require(winner is not None, 'No numeric winner for negative control')
    winner.unwrap()
    try:
        check_presentation(home)
    except ValueError:
        pass
    else:
        raise AssertionError('A missing comparison highlight was accepted')
    home = BeautifulSoup((root / 'index.html').read_text(), 'html.parser')
    first = home.select_one('#wcxb-core .comparison-winner').find_parent('td')
    metric = first['data-metric']
    other = next(cell for cell in home.select('#wcxb-core td[data-metric]')
                 if cell['data-metric'] == metric and cell is not first)
    other.append(BeautifulSoup('<strong class="comparison-winner">wrong</strong>', 'html.parser'))
    try:
        check_presentation(home)
    except ValueError:
        pass
    else:
        raise AssertionError('An incorrect comparison highlight was accepted')
    analysis = require_analysis_controls((root / 'analysis/index.html').read_text(), len(qualified) * len(providers))
    analysis.select_one('#diagnosis-dialog [data-diagnosis-link]').decompose()
    try:
        require_analysis_controls(analysis, len(qualified) * len(providers))
    except ValueError:
        pass
    else:
        raise AssertionError('A broken diagnosis link was accepted')
    case = next(iter(sorted(qualified)))
    detail = require_case_summary((root / 'cases' / f'{case}.html').read_text(), 'zh')
    label = '同一网页的全部方法结果'
    heading = next(node for node in detail.select('h2') if node.get_text(' ', strip=True) == label)
    heading.insert_after(BeautifulSoup(f'<h2>{label}</h2>', 'html.parser'))
    try:
        require_case_summary(detail, 'zh')
    except ValueError:
        pass
    else:
        raise AssertionError('A duplicate result summary was accepted')
    require((root / 'assets/style.css').is_file() and (root / 'assets/report.js').is_file(), 'WCXB report assets missing')
    require(len([path for path in root.rglob('*') if path.is_file()]) < 2000, 'WCXB report tree unexpectedly large')
    print(f'PASS: WCXB {len(case_ids)}-case, {len(providers)}-method, bilingual evidence and route inventory')


if __name__ == '__main__':
    import sys
    check(Path(sys.argv[1]))
