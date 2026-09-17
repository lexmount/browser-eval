"""Fast structural and evidence check for the current WCXB report."""
from __future__ import annotations

import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path

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


def check(root: Path) -> None:
    root = root.resolve()
    profile = json.loads((ROOT / 'evaluation/current-report.json').read_text())
    sample_file = root / 'sample-manifest.json'
    sample = json.loads(sample_file.read_text())
    certificate = json.loads((root / 'certification.json').read_text())
    summary = json.loads((root / 'wcxb-score-summary.json').read_text())
    scores = [json.loads(line) for line in (root / 'wcxb-scores.jsonl').read_text().splitlines() if line]
    sample_hash = digest(sample_file)
    assert sample_hash == profile['sample_manifest_sha256'] == summary['sample_sha256']
    assert sample_hash == certificate['sample_sha256_before'] == certificate['sample_sha256_after']
    assert certificate['complete'] is True
    assert certificate['source_sha256_before'] == certificate['source_sha256_after'] == summary['source_sha256'] == profile['source_dataset_sha256']
    providers = profile['providers']
    case_ids = {row['file_id'] for row in sample['cases']}
    assert len(case_ids) == profile['case_count'] == 511
    assert len(scores) == len(case_ids) * len(providers)
    assert summary['quality_case_count'] == profile['primary_quality_case_count'] == sample['primary_quality_case_count'] == 504
    assert summary['exclusions'] == sample['exclusions']
    assert {(r['provider'], r['case_id']) for r in scores} == {(p, c) for p in providers for c in case_ids}
    assert [r['provider'] for r in certificate['providers']] == providers
    evidence_hashes = json.loads((root / 'evidence-manifest.json').read_text())
    assert set(evidence_hashes) == case_ids
    by_case = {}
    for row in scores:
        by_case.setdefault(row['case_id'], {})[row['provider']] = row
        assert row['quality_eligible'] == (row['case_id'] not in {e['file_id'] for e in sample['exclusions']})
        for retry in row.get('retry_failures', []):
            retry_path = root / retry['artifact_path']
            assert retry_path.is_file() and not retry_path.is_symlink() and digest(retry_path) == retry['output_sha256']
        assert all(0 <= row[k] <= 1 for k in ('precision', 'recall', 'f1', 'with_rate', 'without_rate'))
    for case in case_ids:
        path = root / 'evidence' / f'{case}.json'
        assert path.is_file() and not path.is_symlink() and digest(path) == evidence_hashes[case]
        evidence = json.loads(path.read_text())
        assert evidence['case_id'] == case and set(evidence['outputs']) == set(providers)
        for provider in providers:
            assert hashlib.sha256(evidence['source_html'].encode()).hexdigest() == by_case[case][provider]['input_html_sha256']
            assert hashlib.sha256(evidence['reference'].encode()).hexdigest() == by_case[case][provider]['reference_sha256']
            assert hashlib.sha256(evidence['outputs'][provider].encode()).hexdigest() == by_case[case][provider]['output_sha256']
            retries = evidence['retry_failures'][provider]
            assert len(retries) == len(by_case[case][provider].get('retry_failures', []))
            for retry in retries:
                assert hashlib.sha256(retry['output'].encode()).hexdigest() == retry['output_sha256']
    expected = {'index.html', 'datasets/index.html', 'analysis/index.html', 'methodology/index.html',
                'review/index.html', 'guide/index.html', 'adoption/index.html', 'reproduce/index.html',
                'changes/index.html', 'accounts/index.html',
                *(f'providers/{provider}.html' for provider in providers),
                *(f'cases/{case}.html' for case in case_ids)}
    for lang, prefix in (('zh', ''), ('en', 'en/')):
        routes = {str(path.relative_to(root / prefix)) for path in (root / prefix).rglob('*.html')}
        if not prefix:
            routes = {route for route in routes if not route.startswith('en/')}
        assert routes == expected, (lang, len(routes), len(expected), sorted(expected-routes)[:5])
        for route in expected:
            path = root / prefix / route
            html = path.read_text(encoding='utf-8')
            page = Page(); page.feed(html)
            assert page.lang == lang
            assert '/Users/lianglinkai/' not in html and '/.browser-eval/' not in html
            if route == 'analysis/index.html':
                assert page.results == len(scores)
                assert 'id="scope"' in html and 'value="fail"' in html and 'value="pass"' in html and 'value="all"' in html
                assert 'id="diagnosis"' in html and 'value="issues"' in html
            if route.startswith('cases/'):
                assert set(providers) <= page.ids and page.outputs == 5
                assert f'evidence/{Path(route).stem}.json' in html
    assert (root / 'assets/style.css').is_file() and (root / 'assets/report.js').is_file()
    assert len([path for path in root.rglob('*') if path.is_file()]) < 2000
    print(f'PASS: WCXB {len(case_ids)}-case, five-browser, bilingual evidence and route inventory')


if __name__ == '__main__':
    import sys
    check(Path(sys.argv[1]))
