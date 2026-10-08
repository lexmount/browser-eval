"""Exercise an installed wheel outside its checkout; run with the base dependencies."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


def verify_package_tree(source_root: Path, package_root: Path) -> None:
    """Require the installed library's exact current source and declared assets."""
    source = source_root / 'harness'
    if source.is_symlink() or package_root.is_symlink():
        raise ValueError('Package roots must not be symlinked')
    expected = {path.relative_to(source).as_posix(): path for path in source.rglob('*.py')}
    project = tomllib.loads((source_root / 'pyproject.toml').read_text())
    for package, patterns in project['tool']['setuptools'].get('package-data', {}).items():
        for directory in source_root.glob(package.replace('.', '/')):
            for pattern in patterns:
                for path in directory.glob(pattern):
                    if path.is_file():
                        expected[path.relative_to(source).as_posix()] = path
    observed = {}
    for path in package_root.rglob('*'):
        if path.is_symlink():
            raise ValueError('Installed package contains a symlink')
        relative = path.relative_to(package_root)
        if path.is_file() and '__pycache__' not in relative.parts:
            observed[relative.as_posix()] = path
    if set(expected) != set(observed):
        raise ValueError('Installed package inventory differs from current source: '
                         f'missing={sorted(set(expected) - set(observed))}; '
                         f'extra={sorted(set(observed) - set(expected))}')
    for name, path in expected.items():
        if path.is_symlink():
            raise ValueError('Package source contains a symlink: ' + name)
        source_hash = hashlib.sha256(path.read_bytes()).digest()
        installed_hash = hashlib.sha256(observed[name].read_bytes()).digest()
        if source_hash != installed_hash:
            raise ValueError('Installed package differs from current source bytes: ' + name)


def verify_lexbench_package(root: Path, renderer: Path) -> None:
    from harness.lexbench.pack import package

    source = root / 'lexbench-source'
    source.mkdir()
    for name in ('index.html', 'en.html', 'en/index.html'):
        path = source / name
        path.parent.mkdir(exist_ok=True)
        prefix = '../' if name.startswith('en/') else ''
        path.write_text(f'<script defer src="{prefix}mermaid.min.js"></script>')
    for name in ('app.js', 'style.css', 'mermaid.min.js', 'mermaid-LICENSE.txt'):
        (source / name).write_text(name)
    (source / 'app.js').write_bytes(renderer.read_bytes())
    (source / 'evidence.bin').write_bytes(bytes(range(256)))
    destination = root / 'lexbench-package'
    summary = package(source, destination, pack_limit=80)
    assert summary['bundled_files'] == 1
    catalog = json.loads((destination / 'bundle-manifest.json').read_text())['files']
    entry = catalog['evidence.bin']
    payload = gzip.decompress((destination / entry['pack']).read_bytes())
    data = payload[entry['offset']:entry['offset'] + entry['size']]
    assert data == bytes(range(256))
    assert hashlib.sha256(data).hexdigest() == entry['sha256']
    assert (destination / 'bundle-loader.js').is_file()
    for name in ('index.html', 'en.html', 'en/index.html'):
        assert 'bundle-loader.js' in (destination / name).read_text()
    from harness.report_languages import check_language_contract, write_language_contract

    (source / 'en/index.html').unlink()
    write_language_contract(source, include_english=False)
    notice = (source / 'en/index.html').read_text()
    (source / 'en.html').write_text(
        notice.replace('href="../index.html"', 'href="index.html"')
    )
    chinese_package = root / 'lexbench-chinese-package'
    package(source, chinese_package)
    check_language_contract(chinese_package)
    assert (chinese_package / 'report-languages.json').is_file()
    for name in ('en.html', 'en/index.html'):
        assert 'data-english-not-generated' in (chinese_package / name).read_text()
        assert 'bundle-loader.js' not in (chinese_package / name).read_text()
    from harness.lexbench import reproduction, resource_profiles, verification
    subprocess.run([sys.executable, "-m", "harness.lexbench.reproduction", "--help"],
                   check=True, cwd=root, capture_output=True, text=True)
    subprocess.run([sys.executable, "-m", "harness.lexbench.resource_profiles", "--help"],
                   check=True, cwd=root, capture_output=True, text=True)
    public_collector = root / "collector.py"
    public_collector.write_bytes(Path(resource_profiles.__file__).read_bytes())
    subprocess.run([sys.executable, str(public_collector), "--help"],
                   check=True, cwd=root, capture_output=True, text=True)
    public_reproduction = root / "reproduce.py"
    public_reproduction.write_bytes(Path(reproduction.__file__).read_bytes())
    subprocess.run([sys.executable, str(public_reproduction), "--help"],
                   check=True, cwd=root, capture_output=True, text=True)
    contract = Path(verification.__file__).with_name('assets') / 'render_contract.cjs'
    subprocess.run(['node', str(contract), '--app', str(source / 'app.js')],
                   check=True, cwd=root, capture_output=True, text=True)
    subprocess.run([sys.executable, '-m', 'harness.lexbench.verification', '--help'],
                   check=True, cwd=root, capture_output=True, text=True)
    subprocess.run([sys.executable, '-m', 'harness.lexbench.pack', '--help'],
                   check=True, cwd=root, capture_output=True, text=True)


def verify_dynamic_cohort(root: Path, source_root: Path) -> None:
    from harness.web_fetch.dynamic_cohort import merge
    from harness.web_fetch.risk_control_dataset import load_cohort, parse_sections

    for script in ('append_dynamic_content.py', 'merge_dynamic_supplement.py',
                   'build_risk_control_dataset.py'):
        subprocess.run([sys.executable, str(source_root / 'scripts' / script), '--help'],
                       cwd=root, check=True, capture_output=True, text=True)
    definitions = source_root / 'evaluation/risk-control'
    sections = parse_sections(tuple(definitions / name for name in
                                   ('first-30-rule-review.md', 'remaining-70-rule-review.md')))
    _, cases = load_cohort(definitions / 'qualification/cohort/cohort-manifest.json')
    assert [section['id'] for section in sections] == [case['id'] for case in cases]
    reference, measured, output = (root / name for name in ('dynamic-reference', 'dynamic-run',
                                                           'dynamic-assembled'))
    reference.mkdir()
    measured.mkdir()
    dataset = {'cases': [{'id': 'one', 'name': 'One', 'url': 'https://example.invalid/',
                          'category': 'dynamic', 'track': 'dynamic'}]}
    for directory in (reference, measured):
        (directory / 'dataset.json').write_text(json.dumps(dataset))
    metadata = {}
    for name, raw in [('result', b'answer'), ('stdout', b'process output')]:
        relative = f'artifacts/{name}.txt'
        path = measured / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(raw)
        if name == 'stdout':
            metadata = {'raw_stdout_path': relative,
                        'raw_stdout_sha256': hashlib.sha256(raw).hexdigest()}
    record = {'attempt_id': 'fixture__one__r001', 'provider': 'fixture', 'case_id': 'one',
              'artifact_path': 'artifacts/result.txt', 'provider_metadata': metadata,
              'metrics': {'content_sha256': hashlib.sha256(b'answer').hexdigest()}}
    records = (json.dumps(record) + '\r\n').encode()
    (measured / 'results.jsonl').write_bytes(records)
    (measured / 'execution.json').write_text(json.dumps({'batches': [{
        'dataset': {'case_count': 1}, 'providers': [{'name': 'fixture', 'type': 'fixture'}],
        'attempt_count': 1, 'attempt_ids': [record['attempt_id']],
    }]}))
    merge(reference, [measured], output)
    assert (output / 'results.jsonl').read_bytes() == records
    assert (output / 'artifacts/stdout.txt').read_bytes() == b'process output'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node-html-markdown-path', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    args = parser.parse_args()
    from harness.web_fetch import webmainbench_latency_correction

    legacy_registry = json.loads(
        (Path(webmainbench_latency_correction.__file__).parent
         / 'data/webmainbench-latency-source.json').read_bytes()
    )
    for source in legacy_registry['sources']:
        webmainbench_latency_correction.verify_certifier_source(
            Path.cwd(), {'certifying_runner_sha256': source['sha256']}
        )
    import harness.web_fetch
    installed = Path(harness.web_fetch.__file__).resolve().parents[1]
    if installed.is_relative_to(args.source_root.resolve()):
        raise ValueError('Package smoke must use an installed distribution outside the checkout')
    verify_package_tree(args.source_root, installed)
    from harness.web_fetch.markdown import (
        CONVERTER,
        converter_command,
        require_converter,
    )

    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        verify_lexbench_package(root, args.source_root / 'scripts/moli_report/app.js')
        verify_dynamic_cohort(root, args.source_root)
        subprocess.run([str(Path(sys.executable).parent / 'browser-harness-eval'), 'validate'],
                       check=True, cwd=root)
        binary = root / 'browser'
        binary.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; '
                          'then printf "fixture 7.4.0\\n"; '
                          'else printf "Independent fixture output\\n"; fi\n')
        binary.chmod(0o700)
        dataset = root / 'dataset.json'
        dataset.write_text(json.dumps({'tool': 'web_fetch', 'id': 'fixture',
            'comparison': {},
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
            '--task-kind', 'body', '--case-ids', 'one',
            '--env-file', str(env), '--output', str(root / 'run'), '--delay', '0',
            '--fail-on-failure',
        ], check=True, cwd=root)
        records = [json.loads(line)
                   for line in (root / 'run/results.jsonl').read_text().splitlines()]
        assert len(records) == 1 and records[0]['metrics']['content_pass']
        assert records[0]['provider_metadata']['worker_sha256']
        scoped = json.loads((root / 'run/dataset.json').read_text())
        assert scoped['coverage_plan'] == {'web': ['custom']}
        assert scoped['comparison']['publication_state'] == 'diagnostic_not_for_release'
        from harness.web_fetch.benchmark_runner import main as run_cohort
        run_cohort(['--dataset', str(dataset), '--provider-config', str(config),
                    '--providers', 'custom', '--env-file', str(env),
                    '--output', str(root / 'cohort'), '--workers', '1', '--measure-only'])
        cohort = [json.loads(line) for line in
                  (root / 'cohort/results.jsonl').read_text().splitlines()]
        assert len(cohort) == 1 and cohort[0]['metrics']['content_pass']
        assert (root / 'cohort' / cohort[0]['artifact_path']).is_file()
        from harness.web_fetch.artifacts import artifact_bindings
        from harness.web_fetch.assembly import assemble
        for directory in ('sources', 'list-references'):
            (root / 'cohort' / directory).mkdir(exist_ok=True)
        assemble(root / 'cohort', root / 'certificates', [], root / 'assembled')
        assembled = [json.loads(line) for line in
                     (root / 'assembled/results.jsonl').read_text().splitlines()]
        assert assembled == cohort
        for relative, _ in artifact_bindings(cohort[0]):
            assert ((root / 'assembled' / relative).read_bytes()
                    == (root / 'cohort' / relative).read_bytes())
        subprocess.run([sys.executable, '-c',
                        'from harness.web_fetch.resource_cohort import main; main(["--help"])'],
                       check=True, cwd=root, capture_output=True, text=True)
        from harness.reporting.links import check
        check(root / 'run/report')
        reference = (root / 'run/report/references/one.txt').read_text()
        assert 'Independent fixture output' in reference
        detail = (root / 'run/report/attempts/custom__one__r001.html').read_text()
        assert '验收规则（无全文参考）' in detail
        assert '检查下列目标资源' not in detail
        package = require_converter(str(args.node_html_markdown_path))
        converted = subprocess.run(converter_command(package),
                                   input='<h1>中文标题</h1><p>独立内容</p>',
                                   text=True, capture_output=True, check=True, cwd=root)
        assert '# 中文标题' in converted.stdout and '独立内容' in converted.stdout
        assert not CONVERTER.resolve().is_relative_to(Path(__file__).resolve().parents[1])
    print('Installed wheel: exact source inventory, native worker, scoring, '
          'evidence assembly, Lexbench packaging and HTML conversion passed')


if __name__ == '__main__':
    main()
