"""Fast structural and evidence check for the current WCXB report."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from bs4 import BeautifulSoup

from harness.record_io import load_jsonl
from harness.report_languages import localized_report_routes, visible_numbers
from harness.reporting.presentation import check_presentation
from harness.web_fetch.safety import contained_path
from harness.web_fetch.storage import data_home

ROOT = Path(__file__).resolve().parents[2]


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
        self.numbers.update(visible_numbers(value))
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


def require_home_interpretation(html: str | BeautifulSoup, summary: dict,
                                paired_latency: dict, lang: str) -> BeautifulSoup:
    """Keep the version comparison readable and its timing caveat beside the table."""
    soup = BeautifulSoup(html, 'html.parser') if isinstance(html, str) else html
    core = soup.select_one('#wcxb-core')
    contexts = soup.select('#moli-latency-context')
    changes = soup.select('#moli-change')
    require(core is not None and len(contexts) == len(changes) == 1,
            'Home Moli comparison or timing context is missing or duplicated')
    context = contexts[0]
    require(core.find_parent('section') == context.find_parent('section'),
            'Moli timing context must be beside the core comparison')
    require(core.find_next(id='moli-latency-context') == context,
            'Moli timing context must follow the core comparison')
    release = summary['providers']['moli']['mean_latency_ms']
    candidate = summary['providers']['moli-pr643']['mean_latency_ms']
    required = (f'{release:,.0f} ms', f'{candidate:,.0f} ms',
                f'{paired_latency["paired_delta_mean_ms"]:+.1f} ms')
    context_text = context.get_text(' ', strip=True)
    require(all(value in context_text for value in required),
            'Home timing context disagrees with measured evidence')
    require(('交叉顺序' if lang == 'zh' else 'balanced same-window') in context_text
            and ('504 页' if lang == 'zh' else '504 pages') in context_text,
            'Home timing context does not identify the corrected population')
    require(context.select_one('a[href$="guide/index.html"]') is not None,
            'Home timing context does not link to the analysis')
    change = changes[0]
    change_text = change.get_text(' ', strip=True)
    require(len(change_text) <= (330 if lang == 'zh' else 700),
            'Home Moli change summary is too long; move detail to the interpretation page')
    require(len(change.select('p')) == 2 and change.select_one('a[href$="cases/5458.html"]')
            and change.select_one('a[href$="guide/index.html"]'),
            'Home Moli change summary lacks concise evidence links')
    required_change = (('5412、5413', '5411、5458', '逐页 F1 的平均', '内联 HTML 标签名')
                       if lang == 'zh' else
                       ('5412 and 5413', '5411 and 5458', 'page-level F1 values',
                        'inline HTML tag names'))
    require(all(marker in change_text for marker in required_change),
            'Home Moli change summary does not explain recall/F1 divergence')
    precision_delta = ((summary['providers']['moli-pr643']['quality_precision']
                        - summary['providers']['moli']['quality_precision']) * 100)
    recall_delta = ((summary['providers']['moli-pr643']['quality_recall']
                     - summary['providers']['moli']['quality_recall']) * 100)
    f1_delta = ((summary['providers']['moli-pr643']['quality_f1']
                 - summary['providers']['moli']['quality_f1']) * 100)
    require(all(f'{value:+.4f}' in change_text
                for value in (precision_delta, recall_delta, f1_delta)),
            'Home Moli change summary disagrees with aggregate deltas')
    require(not any(f'{summary["providers"][provider]["quality_f1"] * 100:.4f}%'
                    in change_text for provider in ('moli', 'moli-pr643')),
            'Home Moli change summary repeats exact table values')
    return soup


def require_guide_interpretation(html: str | BeautifulSoup, summary: dict,
                                 providers: list[str], lang: str) -> BeautifulSoup:
    """A guide must work as a standalone explanation, with traceable examples."""
    soup = BeautifulSoup(html, 'html.parser') if isinstance(html, str) else html
    anchors = ('guide-results', 'guide-score', 'guide-cases', 'guide-repair', 'guide-methods',
               'guide-timing', 'guide-conclusion')
    toc = soup.select_one('nav.essay-toc')
    require(toc is not None and all(soup.select_one(f'#{anchor}') is not None
                                     and toc.select_one(f'a[href="#{anchor}"]') is not None
                                     for anchor in anchors),
            'Guide lacks a working reading path or major interpretation')
    results = soup.select_one('#guide-results')
    check_presentation(results)
    rows = results.select('tbody tr')
    require(len(rows) == len(providers), 'Guide overview omits a measured method')
    for provider in providers:
        target = f'providers/{provider}.html'
        row = next((r for r in rows if r.select_one(f'a[href$="{target}"]')), None)
        require(row is not None, f'Guide overview omits {provider}')
        score = summary['providers'][provider]
        values = row.get_text(' ', strip=True)
        require(f'{score["quality_f1"] * 100:.2f}%' in values
                and f'{score["mean_latency_ms"]:,.0f} ms' in values,
                f'Guide overview disagrees with {provider} evidence')
    specimen = soup.select_one('#guide-cases')
    require(specimen is not None, 'Guide lacks a direct source-example entry')
    repair = soup.select_one('#guide-repair')
    images = ('code-block.png', 'description.png', 'adjacent-code.png')
    require(repair is not None and len(repair.select('.repair-case')) == len(images),
            'Guide must explain each Moli repair in its own figure')
    for case, image in zip(repair.select('.repair-case'), images):
        graphic = case.select_one('figure img')
        require(graphic is not None and graphic.get('src') == f'assets/wcxb-repair/{image}'
                and len(graphic.get('alt', '')) >= 30
                and len(case.select('figcaption span')) == 3
                and len(case.select('.repair-samples > div')) == 3
                and case.select_one('.repair-mechanism a[href*="github.com/lexmount/moli/blob/384d1d2a/"]'),
                f'Guide repair figure {image} lacks its image, code samples or current source')
        captions = [node.get_text(' ', strip=True) for node in case.select('figcaption span')]
        scope = case.select_one('.repair-scope')
        require('1.1.8' in captions[1] and '384d1d2a' in captions[2] and scope is not None,
                'Guide figure does not compare the tagged release with current PR')
        if image == 'description.png':
            mode = '可选 UI 清理' if lang == 'zh' else 'optional UI stripping'
            require(all(mode in caption for caption in captions[1:]),
                    'Optional stripping captions omit the measured mode')
    require('tsfunction identity(arg: number)' in repair.select('.repair-case')[0].get_text(' ', strip=True)
            and '<details' in repair.select('.repair-case')[1].select_one('.repair-samples').get_text(' ', strip=True)
            and 'namestring' in repair.select('.repair-case')[2].get_text(' ', strip=True)
            and 'gap-2' in repair.select('.repair-case')[2].get_text(' ', strip=True),
            'Guide figures lack source-backed examples')
    guide_text = soup.get_text(' ', strip=True)
    require(('98.45%' in guide_text and '100%' in guide_text
             and ('CSS' in guide_text or 'css' in guide_text)),
            'Guide omits the measured adjacent-code score change or CSS-spacing limit')
    require(all(repair.select_one(f'a[href$="cases/{case}.html#{provider}"]')
                for case, provider in (('5412', 'moli'), ('5412', 'moli-pr643'),
                                       ('5458', 'moli'), ('5458', 'moli-pr643'), ('4017', 'moli'))),
            'Guide repair explanation lacks measured case evidence')
    require(all(repair.select_one(f'a[href="research/wcxb-optional-ui-4017/{name}"]')
                for name in ('release.md', 'candidate.md', 'receipt.json')),
            'Guide optional-feature claim lacks the actual paired outputs')
    require(soup.select_one('#guide-methods table') is not None
            and len(soup.select('#guide-methods tbody tr')) == len(providers),
            'Guide does not diagnose every measured configuration')
    require(soup.select_one('#guide-timing a[href="research/wcxb-full-paired-latency/calls.jsonl"]') is not None
            and soup.select_one('#guide-timing a[href="research/wcxb-full-paired-latency/certification.json"]') is not None,
            'Guide timing is missing full-scope certified paired evidence')
    require(len(soup.select_one('#guide-conclusion').get_text(' ', strip=True)) > 200,
            'Guide conclusion lacks a substantive finding')
    return soup


def require_case_summary(html: str | BeautifulSoup, lang: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, 'html.parser') if isinstance(html, str) else html
    label = '同一网页的全部方法结果' if lang == 'zh' else 'All method results for this page'
    if sum(node.get_text(' ', strip=True) == label for node in soup.select('h2')) != 1:
        raise ValueError('Result detail has missing or duplicate summary')
    return soup


def check(root: Path, *, authority_root: Path | None = None) -> None:
    root = root.resolve()
    repository = (authority_root or ROOT).resolve()
    profile_path = repository / "evaluation/wcxb/current-report.json"
    if not profile_path.is_file():
        raise ValueError("WCXB release authority is missing; pass --authority-root pointing to the repository")
    profile = json.loads((repository / 'evaluation/wcxb/current-report.json').read_text())
    sample_file = root / 'sample-manifest.json'
    sample = json.loads(sample_file.read_text())
    require(sample.get('scope') == 'qualified_test', 'WCXB current selection is not source-qualified')
    certificate = json.loads((root / 'certification.json').read_text())
    summary = json.loads((root / 'wcxb-score-summary.json').read_text())
    scores = load_jsonl(root / 'wcxb-scores.jsonl', skip_blank=True)
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
    optional_root = root / 'research/wcxb-optional-ui-4017'
    optional_receipt = optional_root / 'receipt.json'
    require(optional_receipt.is_file() and not optional_receipt.is_symlink()
            and digest(optional_receipt) == profile['optional_ui_receipt_sha256'],
            'WCXB optional UI-stripping evidence differs from current profile')
    optional = json.loads(optional_receipt.read_text())
    for name, binary_hash in (('release', profile['release_binary_sha256']),
                              ('candidate', profile['candidate_binary_sha256'])):
        build = optional['builds'][name]
        artifact = optional_root / f'{name}.md'
        require(build['binary_sha256'] == binary_hash and build['returncode'] == 0
                and artifact.is_file() and not artifact.is_symlink()
                and digest(artifact) == build['output_sha256'],
                'WCXB optional UI-stripping output is not bound to its binary')
    require(not optional['builds']['release']['description_present']
            and optional['builds']['candidate']['description_present'],
            'WCXB optional UI-stripping finding is not supported')
    from harness.web_fetch.wcxb_latency import (
        load_full_paired_latency,
        summarize_full_paired_latency,
    )
    _, selected_latency = load_full_paired_latency(root / 'research/wcxb-full-paired-latency', profile, case_ids)
    paired_latency = summarize_full_paired_latency(selected_latency, case_ids)
    for provider, method in (('moli', 'release'), ('moli-pr643', 'pr')):
        values = sorted(selected_latency[case_id, method]['elapsed_ms'] for case_id in case_ids)
        position = (len(values) - 1) * .95
        low = int(position)
        p95 = values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (position - low)
        measured = summary['providers'][provider]
        require(abs(measured['mean_latency_ms'] - paired_latency[method]['mean_ms']) < .01
                and abs(measured['p50_latency_ms'] - paired_latency[method]['p50_ms']) < .01
                and abs(measured['p95_latency_ms'] - p95) < .01,
                'WCXB Moli timing summary differs from certified full paired evidence')
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
    localized_routes = localized_report_routes(root, expected)
    language_pairs = tuple((language, '' if language == 'zh' else 'en/')
                           for language in localized_routes)
    shapes = {language: {} for language in localized_routes}
    for lang, prefix in language_pairs:
        for route in expected:
            path = root / prefix / route
            html = path.read_text(encoding='utf-8')
            page = Page(); page.feed(html)
            shape = ReportShape(); shape.feed(html)
            shapes[lang][route] = shape
            require(page.lang == lang, f'WCXB language differs: {route}')
            if lang == 'en':
                require(not shape.untranslated, f'Untranslated WCXB interface: {route}: {shape.untranslated[:3]}')
            private_paths = (str(repository), str(data_home()))
            require(all(path not in html for path in private_paths), f'WCXB private path leaked: {route}')
            if route == 'analysis/index.html':
                require(page.results == len(qualified) * len(providers), 'Qualified result inventory differs')
                require('id="scope"' in html and 'value="fail"' in html and 'value="pass"' in html and 'value="all"' in html, 'Result status filters missing')
                require('id="diagnosis"' in html and 'value="issues"' in html, 'Result diagnosis filter missing')
                require_analysis_controls(html, len(qualified) * len(providers))
                require(all(f'data-case="{case}"' not in html for case in excluded), 'Invalid references entered result inventory')
            if route == 'guide/index.html':
                guide = BeautifulSoup(html, 'html.parser')
                require_guide_interpretation(guide, summary, providers, lang)
                for image in ('code-block.png', 'description.png', 'adjacent-code.png'):
                    asset = root / 'assets/wcxb-repair' / image
                    require(asset.is_file() and asset.read_bytes()[:8] == b'\x89PNG\r\n\x1a\n',
                            f'Repair graphic missing or invalid: {image}')
                headings = {node.get_text(' ', strip=True) for node in guide.select('h2')}
                require(('Obscura 为什么分数更高' if lang == 'zh' else 'Why Obscura scores higher') in headings,
                        'Obscura content analysis is missing')
                require(('Moli 两版耗时' if lang == 'zh' else 'Moli latency') in headings,
                        'Paired Moli latency analysis is missing')
                require(guide.select_one('a[href="research/wcxb-full-paired-latency/calls.jsonl"]') is not None,
                        'Paired latency evidence link is missing')
                require(guide.select_one('a[href$="cases/4461.html#obscura"]') is not None,
                        'Obscura boundary counterexample is missing')
            if route in ('index.html', 'datasets/index.html'):
                check_presentation(html)
                if route == 'index.html':
                    require_home_interpretation(html, summary, paired_latency, lang)
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
                preview_section = BeautifulSoup(html, 'html.parser').select_one('#preview')
                expected_title = '冻结 HTML 阅读视图' if lang == 'zh' else 'Frozen HTML reading view'
                require(preview_section.select_one('h2').get_text(strip=True) == expected_title,
                        f'Frozen HTML view is mislabeled as a webpage preview: {route}')
                require(preview_section.select_one(f'a[href="evidence/{Path(route).stem}.json"]') is not None,
                        f'Complete frozen HTML input link missing: {route}')
                require(('不是原网页截图' if lang == 'zh' else 'not a screenshot') in preview_section.get_text(),
                        f'Frozen HTML view lacks provenance warning: {route}')
                require('id="reference"' in html, f'Human reference anchor missing: {route}')
                case = Path(route).stem
                require(BeautifulSoup(html, 'html.parser').select_one(f'.hero .source a[href$="cases/{case}.html#preview"]') is not None,
                        f'Case source URL does not link to the frozen preview: {route}')
                for provider in providers:
                    row = by_case[case][provider]
                    if row['error_message']:
                        require(row['error_message'] in BeautifulSoup(html, 'html.parser').get_text(), f'Exact error missing: {route}')
    if 'en' in localized_routes:
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
    for lang, prefix in language_pairs:
        guide = BeautifulSoup((root / prefix / 'guide/index.html').read_text(), 'html.parser')
        captions = guide.select('#guide-repair .repair-case')[1].select('figcaption span')
        captions[1].string = 'Moli 1.1.8'
        try:
            require_guide_interpretation(guide, summary, providers, lang)
        except ValueError:
            pass
        else:
            raise AssertionError(f'A {lang} optional-feature caption without its mode was accepted')
    guide = BeautifulSoup((root / 'guide/index.html').read_text(), 'html.parser')
    guide.select_one('#guide-results tbody tr').decompose()
    try:
        require_guide_interpretation(guide, summary, providers, 'zh')
    except ValueError:
        pass
    else:
        raise AssertionError('A guide with a missing measured method was accepted')
    guide = BeautifulSoup((root / 'guide/index.html').read_text(), 'html.parser')
    guide.select_one('#guide-repair a[href$="cases/5458.html#moli-pr643"]').decompose()
    try:
        require_guide_interpretation(guide, summary, providers, 'zh')
    except ValueError:
        pass
    else:
        raise AssertionError('A guide with broken worked evidence was accepted')
    for lang, prefix in language_pairs:
        guide = BeautifulSoup((root / prefix / 'guide/index.html').read_text(), 'html.parser')
        guide.select_one('#guide-repair .repair-case').decompose()
        try:
            require_guide_interpretation(guide, summary, providers, lang)
        except ValueError:
            pass
        else:
            raise AssertionError(f'A {lang} guide missing a repair diagram was accepted')
        guide = BeautifulSoup((root / prefix / 'guide/index.html').read_text(), 'html.parser')
        guide.select_one('#guide-repair figure img').decompose()
        try:
            require_guide_interpretation(guide, summary, providers, lang)
        except ValueError:
            pass
        else:
            raise AssertionError(f'A {lang} guide missing a repair image was accepted')
        guide = BeautifulSoup((root / prefix / 'guide/index.html').read_text(), 'html.parser')
        guide.select('#guide-repair .repair-case')[2].select('figcaption span')[1].string = (
            '正式版 1.1.7' if lang == 'zh' else 'Release 1.1.7')
        try:
            require_guide_interpretation(guide, summary, providers, lang)
        except ValueError:
            pass
        else:
            raise AssertionError(f'A {lang} guide mislabeled the tagged release')
        guide = BeautifulSoup((root / prefix / 'guide/index.html').read_text(), 'html.parser')
        for node in guide.find_all(string=lambda value: value and 'gap-2' in value):
            node.replace_with(node.replace('gap-2', ''))
        try:
            require_guide_interpretation(guide, summary, providers, lang)
        except ValueError:
            pass
        else:
            raise AssertionError(f'A {lang} guide missing the CSS-spacing limit was accepted')
    guide = BeautifulSoup((root / 'guide/index.html').read_text(), 'html.parser')
    guide.select_one('#guide-results .comparison-winner').unwrap()
    try:
        require_guide_interpretation(guide, summary, providers, 'zh')
    except ValueError:
        pass
    else:
        raise AssertionError('A guide without a winning-column highlight was accepted')
    home = BeautifulSoup((root / 'index.html').read_text(), 'html.parser')
    home.select_one('#moli-latency-context').decompose()
    try:
        require_home_interpretation(home, summary, paired_latency, 'zh')
    except ValueError:
        pass
    else:
        raise AssertionError('A home page without adjacent timing context was accepted')
    with tempfile.TemporaryDirectory(prefix='wcxb-latency-negative-') as directory:
        invalid = Path(directory)
        evidence = root / 'research/wcxb-full-paired-latency'
        shutil.copy2(evidence / 'receipt.json', invalid / 'receipt.json')
        shutil.copy2(evidence / 'calls.jsonl', invalid / 'calls.jsonl')
        calls = (invalid / 'calls.jsonl').read_bytes()
        (invalid / 'calls.jsonl').write_bytes(calls.rsplit(b'\n', 2)[0] + b'\n')
        try:
            load_full_paired_latency(invalid, profile, case_ids)
        except ValueError:
            pass
        else:
            raise AssertionError('An incomplete paired latency cohort was accepted')
        (invalid / 'calls.jsonl').write_bytes(calls)
        receipt = json.loads((invalid / 'receipt.json').read_text())
        receipt['binary_sha256']['pr'] = '0' * 64
        (invalid / 'receipt.json').write_text(json.dumps(receipt))
        try:
            load_full_paired_latency(invalid, profile, case_ids)
        except ValueError:
            pass
        else:
            raise AssertionError('A wrong paired binary identity was accepted')
    require((root / 'assets/style.css').is_file() and (root / 'assets/report.js').is_file(), 'WCXB report assets missing')
    require(len([path for path in root.rglob('*') if path.is_file()]) < 2000, 'WCXB report tree unexpectedly large')
    print(f'PASS: WCXB {len(case_ids)}-case, {len(providers)}-method, declared-language evidence and route inventory')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--authority-root', type=Path)
    args = parser.parse_args()
    check(args.report, authority_root=args.authority_root)
