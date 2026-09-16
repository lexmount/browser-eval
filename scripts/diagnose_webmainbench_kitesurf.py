"""Diagnose frozen-page Kitesurf failures without changing benchmark evidence."""
from __future__ import annotations

import argparse
import html as html_module
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.config import load_provider_config, load_runtime_env
from harness.web_fetch.models import ProviderResult
from harness.web_fetch.providers.cloudflare import CloudflareMarkdownProvider
from harness.web_fetch.runner import classify_exception
from scripts.run_webmainbench_benchmark import case_for, load_source, public_metadata

CASE_IDS = (
    '4c631939-b5d2-4750-a19e-687459751b91',
    '30b05f1d-56b3-48d3-b587-aea4e99bc003',
    '819d17d7-4825-492f-9546-ab61b14e9aab',
    'a5791c85-1701-4ffd-97e1-779aeefae3b8',
    'ac11d851-aed7-4b9e-89d1-39c142fa152d',
)
SCRIPT = re.compile(r'<script\b[^>]*>.*?</script\s*>', re.I | re.S)
SRC = re.compile(r'<script\b[^>]*\bsrc\s*=', re.I | re.S)
TARGETS = {
    '819d17d7-4825-492f-9546-ab61b14e9aab':
        'if (top.frames.length!=0){top.location=self.document.location;}',
    'ac11d851-aed7-4b9e-89d1-39c142fa152d':
        'window.location.search += "&nocdn=true";',
}
STRUCTURE_CASE_IDS = {
    '30b05f1d-56b3-48d3-b587-aea4e99bc003',
    'a5791c85-1701-4ffd-97e1-779aeefae3b8',
}


def remove_scripts(html: str, *, external: bool | None) -> str:
    def replace(match: re.Match[str]) -> str:
        is_external = bool(SRC.search(match.group()))
        return '' if external is None or is_external is external else match.group()
    return SCRIPT.sub(replace, html)


def keep_inline_scripts(html: str, start: int, stop: int) -> str:
    index = 0
    def replace(match: re.Match[str]) -> str:
        nonlocal index
        if SRC.search(match.group()):
            return match.group()
        keep = start <= index < stop
        index += 1
        return match.group() if keep else ''
    result = SCRIPT.sub(replace, html)
    if index != 63:
        raise ValueError(f'Expected 63 inline CNN scripts, found {index}')
    return result


def variants(case_id: str, html: str, profile: str = 'scripts') -> dict[str, str]:
    if profile == 'inline_root':
        if case_id != '4c631939-b5d2-4750-a19e-687459751b91':
            raise ValueError('Unexpected inline_root case: ' + case_id)
        handler = 'window.onerror=function(){clickURL=cleanClickURL(document.location.href);return true;}'
        call = 'if(!self.clickURL) clickURL=cleanClickURL(parent.location.href);'
        if html.count(handler) != 1 or html.count(call) != 1:
            raise ValueError('CNN root statements changed')
        stub = '<script>function cleanClickURL(value){return value;}</script>'
        with_stub, replacements = re.subn(r'(<head\b[^>]*>)', r'\1' + stub, html,
                                          count=1, flags=re.I)
        if replacements != 1:
            raise ValueError('CNN document has no unique head start tag')
        return {
            'without_recursive_error_handler': html.replace(handler, ''),
            'without_missing_function_call': html.replace(call, ''),
            'with_cleanClickURL_stub': with_stub,
        }
    if profile.startswith('inline'):
        if case_id != '4c631939-b5d2-4750-a19e-687459751b91':
            raise ValueError('Unexpected inline1 case: ' + case_id)
        ranges = {
            'inline1': ((0, 31), (31, 63)),
            'inline2': ((0, 16), (16, 31)),
            'inline3': ((0, 8), (8, 16)),
            'inline4': ((8, 12), (12, 16)),
            'inline5': ((12, 14), (14, 16)),
            'inline6': ((14, 15), (15, 16)),
        }.get(profile)
        if ranges is None:
            raise ValueError('Unknown inline diagnostic profile: ' + profile)
        return {f'inline_{start}_{stop - 1}': keep_inline_scripts(html, start, stop)
                for start, stop in ranges}
    if profile == 'structure2':
        if case_id == '30b05f1d-56b3-48d3-b587-aea4e99bc003':
            result = {}
            for limit in (1000, 2500, 4000):
                soup = BeautifulSoup(html, 'html.parser')
                for row in soup.find_all('tr')[limit:]:
                    row.decompose()
                result['first_' + str(limit) + '_table_rows'] = str(soup)
            return result
        if case_id == 'a5791c85-1701-4ffd-97e1-779aeefae3b8':
            result = {}
            for name, keep in (('first_12_tables', range(0, 12)),
                               ('last_13_tables', range(12, 25))):
                soup = BeautifulSoup(html, 'html.parser')
                keep_set = set(keep)
                for index, table in reversed(list(enumerate(soup.find_all('table')))):
                    if index not in keep_set:
                        table.decompose()
                result[name] = str(soup)
            return result
        raise ValueError('Unexpected structure2 case: ' + case_id)
    if profile == 'structure':
        soup = BeautifulSoup(html, 'html.parser')
        text = soup.get_text('\n', strip=True)
        without_tables = BeautifulSoup(html, 'html.parser')
        for table in without_tables.find_all('table'):
            table.decompose()
        first_rows = BeautifulSoup(html, 'html.parser')
        for row in first_rows.find_all('tr')[100:]:
            row.decompose()
        return {
            'text_only': '<html><body><pre>' + html_module.escape(text) + '</pre></body></html>',
            'without_tables': str(without_tables),
            'first_100_table_rows': str(first_rows),
        }
    if profile != 'scripts':
        raise ValueError('Unknown diagnostic profile: ' + profile)
    result = {
        'original': html,
        'without_external_scripts': remove_scripts(html, external=True),
        'without_inline_scripts': remove_scripts(html, external=False),
        'without_all_scripts': remove_scripts(html, external=None),
    }
    if target := TARGETS.get(case_id):
        if html.count(target) != 1:
            raise ValueError(case_id + ': expected one target statement')
        result['without_target_statement'] = html.replace(target, '')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/providers.headless.json')
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=45)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--profile', choices=('scripts', 'structure', 'structure2', 'inline1', 'inline2', 'inline3', 'inline4', 'inline5', 'inline6', 'inline_root'), default='scripts')
    args = parser.parse_args()
    if args.output.exists() and not args.resume:
        raise ValueError('Diagnostic output already exists')
    load_runtime_env(args.env_file)
    config = load_provider_config(args.config)['kitesurf']
    provider = CloudflareMarkdownProvider('kitesurf', {
        'account_id': config['account_id'], 'api_token': config['api_token'],
        'browser': 'kitesurf', 'max_bytes': config.get('max_bytes', 20_000_000),
    })
    if missing := provider.validate():
        raise ValueError(', '.join(missing))
    selected = (set(CASE_IDS) if args.profile == 'scripts' else
                {'4c631939-b5d2-4750-a19e-687459751b91'} if args.profile.startswith('inline') else
                STRUCTURE_CASE_IDS)
    source = load_source(args.dataset, selected)
    args.output.mkdir(parents=True, exist_ok=args.resume)
    artifact_root = args.output / 'artifacts'
    artifact_root.mkdir(exist_ok=args.resume)
    manifest = {
        'kind': 'diagnostic_only', 'provider': 'kitesurf', 'profile': args.profile,
        'dataset_sha256': hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        'cases': [],
    }
    controls_by_case = {}
    for record in source:
        controls = variants(record['track_id'], record['html'], args.profile)
        controls_by_case[record['track_id']] = controls
        manifest['cases'].append({
            'case_id': record['track_id'],
            'variants': {name: hashlib.sha256(html.encode()).hexdigest()
                         for name, html in controls.items()},
        })
    manifest_path = args.output / 'manifest.json'
    expected_manifest = json.dumps(manifest, ensure_ascii=False, indent=2)
    if manifest_path.exists() and manifest_path.read_text(encoding='utf-8') != expected_manifest:
        raise ValueError('Resume manifest does not match current diagnostic controls')
    manifest_path.write_text(expected_manifest, encoding='utf-8')
    result_path = args.output / 'results.jsonl'
    existing = [json.loads(line) for line in result_path.read_text(encoding='utf-8').splitlines() if line.strip()] if result_path.exists() else []
    completed = {(row['case_id'], row['variant']) for row in existing}
    if len(completed) != len(existing):
        raise ValueError('Duplicate diagnostic result identity')
    with result_path.open('a' if args.resume else 'x', encoding='utf-8') as sink:
        for record in source:
            controls = controls_by_case[record['track_id']]
            for name, html in controls.items():
                if (record['track_id'], name) in completed:
                    continue
                started = time.monotonic()
                try:
                    response = provider.fetch_html(case_for(record, record['url']), html, args.timeout)
                except Exception as error:
                    error_type, error_message = classify_exception(error)
                    response = ProviderResult(False, '', error_type=error_type,
                                              error_message=error_message,
                                              metadata={'exception_class': type(error).__name__})
                elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                artifact = artifact_root / (record['track_id'] + '__' + name + '.md')
                artifact.write_text(response.content, encoding='utf-8')
                row = {
                    'case_id': record['track_id'], 'variant': name,
                    'input_sha256': hashlib.sha256(html.encode()).hexdigest(),
                    'ok': response.ok, 'output_chars': len(response.content),
                    'output_sha256': hashlib.sha256(response.content.encode()).hexdigest(),
                    'elapsed_ms': elapsed_ms, 'status_code': response.status_code,
                    'error_type': response.error_type, 'error_message': response.error_message,
                    'metadata': public_metadata(response.metadata),
                    'artifact': artifact.relative_to(args.output).as_posix(),
                }
                sink.write(json.dumps(row, ensure_ascii=False) + '\n')
                sink.flush()
                print(record['track_id'], name, response.ok, len(response.content),
                      response.error_type, response.error_message, flush=True)


if __name__ == '__main__':
    main()
