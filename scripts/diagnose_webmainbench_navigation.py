"""Run isolated navigation controls; never replace benchmark inputs or results."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.webmainbench_runner import fixture_server, load_source, fetch_record
from harness.web_fetch.providers import build_provider

CASE_ID = '819d17d7-4825-492f-9546-ab61b14e9aab'
TRIGGER = 'if (top.frames.length!=0){top.location=self.document.location;}'


def controls(html: str) -> dict[str, str]:
    if html.count(TRIGGER) != 1:
        raise ValueError('Expected exactly one source-bound navigation guard')
    minimal = ('<html><body><p>Navigation control body.</p><iframe></iframe>'
               '<script>' + TRIGGER + '</script></body></html>')
    return {
        'original': html,
        'original_without_frame_guard': html.replace(TRIGGER, ''),
        'minimal_with_child_frame': minimal,
        'minimal_without_child_frame': minimal.replace('<iframe></iframe>', ''),
        'minimal_correct_frame_guard': minimal.replace('top.frames.length!=0', 'top !== self'),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Diagnostic output already exists')
    original = load_source(args.dataset, {CASE_ID})[0]
    variants = controls(original['html'])
    records = [{**original, 'track_id': name, 'html': html} for name, html in variants.items()]
    provider = build_provider('obscura', {
        'type': 'headless_markdown', 'engine': 'obscura', 'binary': str(args.binary.resolve()),
        'network_policy': 'loopback_only', 'allow_private_network': True,
    })
    if missing := provider.validate():
        raise ValueError(', '.join(missing))
    args.output.mkdir(parents=True)
    (args.output / 'controls.json').write_text(json.dumps({
        'kind': 'diagnostic_only', 'source_id': CASE_ID,
        'source_html_sha256': hashlib.sha256(original['html'].encode()).hexdigest(),
        'identity': provider.implementation_identity(), 'inputs': variants,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    with fixture_server(records) as (base, ca), (args.output / 'results.jsonl').open('x', encoding='utf-8') as sink:
        provider.config['ca_cert'] = str(ca)
        for record in records:
            response = fetch_record(provider, None, record, base + '/case/' + record['track_id'], 45)
            row = {
                'control': record['track_id'],
                'input_sha256': hashlib.sha256(record['html'].encode()).hexdigest(),
                'ok': response.ok, 'content': response.content,
                'error_type': response.error_type, 'error_message': response.error_message,
                'metadata': response.metadata,
            }
            sink.write(json.dumps(row, ensure_ascii=False) + '\n')
            sink.flush()
            terminal = (response.error_message or '').splitlines()[-1:]
            print(record['track_id'], response.ok, len(response.content), terminal, flush=True)


if __name__ == '__main__':
    main()
