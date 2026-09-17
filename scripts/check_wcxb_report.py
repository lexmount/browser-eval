"""Fast structural and evidence check for the current WCXB report."""
from __future__ import annotations

import hashlib
import json
import sys
from bs4 import BeautifulSoup
from html.parser import HTMLParser
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.report_presentation_contract import check_presentation

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


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def require_analysis_controls(html: str | BeautifulSoup) -> BeautifulSoup:
    soup = BeautifulSoup(html, 'html.parser') if isinstance(html, str) else html
    if len(soup.select('tr[data-provider]')) != 2520:
        raise ValueError('Qualified result inventory is incomplete')
    if any(soup.select_one(selector) is None for selector in (
            '#compare-baseline', '[name="compare-provider"]', '#compare-relation',
            '#comparison-rows', '#diagnosis-dialog',
            '#diagnosis-dialog [data-call-log-close]',
            '#diagnosis-dialog [data-diagnosis-link]')):
        raise ValueError('Comparison or diagnosis control is missing')
    if len(soup.select('[data-diagnosis-open]')) != 2520:
        raise ValueError('Result row lacks a diagnosis control')
    return soup


def require_case_summary(html: str | BeautifulSoup, lang: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, 'html.parser') if isinstance(html, str) else html
    label = '同一网页的五种结果' if lang == 'zh' else 'Five results for this page'
    if sum(node.get_text(' ', strip=True) == label for node in soup.select('h2')) != 1:
        raise ValueError('Result detail has missing or duplicate summary')
    return soup


def check(root: Path) -> None:
    root = root.resolve()
    profile = json.loads((ROOT / 'evaluation/current-report.json').read_text())
    sample_file = root / 'sample-manifest.json'
    sample = json.loads(sample_file.read_text())
    certificate = json.loads((root / 'certification.json').read_text())
    summary = json.loads((root / 'wcxb-score-summary.json').read_text())
    scores = [json.loads(line) for line in (root / 'wcxb-scores.jsonl').read_text().splitlines() if line]
    sample_hash = digest(sample_file)
    require(sample_hash == profile['sample_manifest_sha256'] == summary['sample_sha256'], 'WCXB sample hash differs')
    require(sample_hash == certificate['sample_sha256_before'] == certificate['sample_sha256_after'], 'WCXB sample changed during calls')
    require(certificate['complete'] is True, 'WCXB certificate is incomplete')
    require(certificate['source_sha256_before'] == certificate['source_sha256_after'] == summary['source_sha256'] == profile['source_dataset_sha256'], 'WCXB source hash differs')
    providers = profile['providers']
    case_ids = {row['file_id'] for row in sample['cases']}
    excluded = {row['file_id'] for row in sample['exclusions']}
    qualified = case_ids - excluded
    require(len(case_ids) == profile['case_count'] == 511, 'WCXB case count differs')
    require(len(scores) == len(case_ids) * len(providers), 'WCXB score count differs')
    require(summary['quality_case_count'] == profile['primary_quality_case_count'] == sample['primary_quality_case_count'] == 504, 'WCXB quality denominator differs')
    require(summary['exclusions'] == sample['exclusions'], 'WCXB exclusions differ')
    require({(r['provider'], r['case_id']) for r in scores} == {(p, c) for p in providers for c in case_ids}, 'WCXB method-case matrix differs')
    require([r['provider'] for r in certificate['providers']] == providers, 'WCXB certificate participants differ')
    evidence_hashes = json.loads((root / 'evidence-manifest.json').read_text())
    require(set(evidence_hashes) == case_ids, 'WCXB evidence inventory differs')
    by_case = {}
    for row in scores:
        by_case.setdefault(row['case_id'], {})[row['provider']] = row
        require(row['quality_eligible'] == (row['case_id'] not in excluded), 'WCXB quality eligibility differs')
        for retry in row.get('retry_failures', []):
            retry_path = root / retry['artifact_path']
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
    for lang, prefix in (('zh', ''), ('en', 'en/')):
        routes = {str(path.relative_to(root / prefix)) for path in (root / prefix).rglob('*.html')}
        if not prefix:
            routes = {route for route in routes if not route.startswith('en/')}
        require(routes == expected, f'WCXB {lang} route inventory differs: {sorted(expected-routes)[:5]}')
        for route in expected:
            path = root / prefix / route
            html = path.read_text(encoding='utf-8')
            page = Page(); page.feed(html)
            require(page.lang == lang, f'WCXB language differs: {route}')
            require('/Users/lianglinkai/' not in html and '/.browser-eval/' not in html, f'WCXB private path leaked: {route}')
            if route == 'analysis/index.html':
                require(page.results == len(qualified) * len(providers), 'Qualified result inventory differs')
                require('id="scope"' in html and 'value="fail"' in html and 'value="pass"' in html and 'value="all"' in html, 'Result status filters missing')
                require('id="diagnosis"' in html and 'value="issues"' in html, 'Result diagnosis filter missing')
                require_analysis_controls(html)
                require(all(f'data-case="{case}"' not in html for case in excluded), 'Invalid references entered result inventory')
            if route in ('index.html', 'datasets/index.html'):
                check_presentation(html)
                require('id="home-type"' in html, 'Page-type filter missing')
                if route == 'index.html':
                    require('官方全量 F1' not in html and 'Official full F1' not in html, 'Invalid full-reference score remains visible')
                    require(len(BeautifulSoup(html, 'html.parser').select('#wcxb-core .comparison-winner')) >= 4, 'Core comparison winners missing')
            if route.startswith('cases/'):
                require_case_summary(html, lang)
                require(set(providers) <= page.ids and page.outputs == 5, f'Case participant evidence missing: {route}')
                require(f'evidence/{Path(route).stem}.json' in html, f'Case evidence link missing: {route}')
                case = Path(route).stem
                for provider in providers:
                    row = by_case[case][provider]
                    if row['error_message']:
                        require(row['error_message'] in BeautifulSoup(html, 'html.parser').get_text(), f'Exact error missing: {route}')
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
    analysis = require_analysis_controls((root / 'analysis/index.html').read_text())
    analysis.select_one('#diagnosis-dialog [data-diagnosis-link]').decompose()
    try:
        require_analysis_controls(analysis)
    except ValueError:
        pass
    else:
        raise AssertionError('A broken diagnosis link was accepted')
    case = next(iter(sorted(qualified)))
    detail = require_case_summary((root / 'cases' / f'{case}.html').read_text(), 'zh')
    heading = next(node for node in detail.select('h2') if node.get_text(' ', strip=True) == '同一网页的五种结果')
    heading.insert_after(BeautifulSoup('<h2>同一网页的五种结果</h2>', 'html.parser'))
    try:
        require_case_summary(detail, 'zh')
    except ValueError:
        pass
    else:
        raise AssertionError('A duplicate result summary was accepted')
    require((root / 'assets/style.css').is_file() and (root / 'assets/report.js').is_file(), 'WCXB report assets missing')
    require(len([path for path in root.rglob('*') if path.is_file()]) < 2000, 'WCXB report tree unexpectedly large')
    print(f'PASS: WCXB {len(case_ids)}-case, five-browser, bilingual evidence and route inventory')


if __name__ == '__main__':
    import sys
    check(Path(sys.argv[1]))
