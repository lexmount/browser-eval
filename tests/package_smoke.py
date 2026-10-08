"""Exercise an installed wheel outside its checkout; run with the base dependencies."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node-html-markdown-path', type=Path, required=True)
    args = parser.parse_args()
    from harness.web_fetch.markdown import (
        CONVERTER,
        converter_command,
        require_converter,
    )

    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        binary = root / 'browser'
        binary.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then printf "fixture 7.4.0\\n"; else printf "Independent fixture output\\n"; fi\n')
        binary.chmod(0o700)
        dataset = root / 'dataset.json'
        dataset.write_text(json.dumps({'tool': 'web_fetch', 'id': 'fixture',
            'coverage_plan': {'web': ['custom']}, 'cases': [{
            'id': 'one', 'name': 'One', 'url': 'https://example.invalid/',
            'category': 'web', 'expected_any': ['Independent fixture output'],
        }]}))
        config = root / 'providers.json'
        config.write_text(json.dumps({'providers': {'custom': {
            'type': 'headless_markdown', 'engine': 'moli', 'binary': str(binary),
            'expected_version': '7.4.0',
        }}}))
        env = root / 'empty.env'
        env.write_text('')
        subprocess.run([
            sys.executable, '-m', 'harness.web_fetch', 'run', '--dataset', str(dataset),
            '--provider-config', str(config), '--providers', 'custom',
            '--env-file', str(env), '--output', str(root / 'run'), '--delay', '0',
            '--fail-on-failure',
        ], check=True, cwd=root)
        records = [json.loads(line) for line in (root / 'run/results.jsonl').read_text().splitlines()]
        assert len(records) == 1 and records[0]['metrics']['content_pass']
        assert records[0]['provider_metadata']['worker_sha256']
        from harness.reporting.links import check
        check(root / 'run/report')
        reference = (root / 'run/report/references/one.txt').read_text()
        assert 'Independent fixture output' in reference
        detail = (root / 'run/report/attempts/custom__one__r001.html').read_text()
        assert '验收规则（无全文参考）' in detail
        assert '检查下列目标资源' not in detail
        package = require_converter(str(args.node_html_markdown_path))
        converted = subprocess.run(converter_command(package), input='<h1>中文标题</h1><p>独立内容</p>',
                                   text=True, capture_output=True, check=True, cwd=root)
        assert '# 中文标题' in converted.stdout and '独立内容' in converted.stdout
        assert not CONVERTER.resolve().is_relative_to(Path(__file__).resolve().parents[1])
    print('Installed wheel: native worker, scoring, evidence and HTML conversion passed')


if __name__ == '__main__':
    main()
