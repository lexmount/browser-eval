"""Capture an independently bound optional UI-stripping comparison on one frozen page."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from scripts.run_webmainbench_benchmark import fixture_server, load_wcxb_source


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    records, source_hash = load_wcxb_source(args.dataset)
    record = next(row for row in records if row['track_id'] == '4017')
    if args.output.exists():
        parser.error('Output already exists; diagnostic evidence is immutable')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = {'schema_version': 1, 'case_id': '4017', 'source_sha256': source_hash,
              'html_sha256': hashlib.sha256(record['html'].encode()).hexdigest(),
              'command_options': ['fetch', '--dump', 'markdown', '--strip-mode', 'ui',
                                  '--disable-js', '--disable-css', '--wait-until', 'domcontentloaded'],
              'builds': {}}
    with tempfile.TemporaryDirectory(prefix=f'.{args.output.name}.', dir=args.output.parent) as staging:
        staged = Path(staging)
        with fixture_server([record]) as (base, ca_cert):
            for name, binary in (('release', args.release), ('candidate', args.candidate)):
                command = [str(binary), *result['command_options'], '--ca-cert', str(ca_cert),
                           f'{base}/case/4017']
                completed = subprocess.run(command, capture_output=True, timeout=60)
                (staged / f'{name}.md').write_bytes(completed.stdout)
                (staged / f'{name}.stderr').write_bytes(completed.stderr)
                result['builds'][name] = {'binary_sha256': sha256(binary),
                                          'output_sha256': sha256(staged / f'{name}.md'),
                                          'stderr_sha256': sha256(staged / f'{name}.stderr'),
                                          'returncode': completed.returncode,
                                          'description_present': b'Struggling with discomfort in your knees' in completed.stdout}
        if (result['builds']['release']['returncode'] or result['builds']['candidate']['returncode']
                or result['builds']['release']['description_present']
                or not result['builds']['candidate']['description_present']):
            raise ValueError('Optional UI-stripping comparison did not reproduce the claimed defect')
        (staged / 'receipt.json').write_text(json.dumps(result, indent=2) + '\n')
        if args.output.exists():
            parser.error('Output already exists; diagnostic evidence is immutable')
        staged.rename(args.output)
    print(args.output / 'receipt.json')


if __name__ == '__main__':
    main()
