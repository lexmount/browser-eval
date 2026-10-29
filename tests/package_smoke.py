"""Exercise an installed wheel outside its checkout; run with the base dependencies."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path


def verify_dependency_metadata(source_root: Path) -> None:
    from importlib.metadata import requires

    project = tomllib.loads((source_root / 'pyproject.toml').read_text())
    expected_webarena = next(value for value in project['project']['optional-dependencies']['browser-use-eval']
                             if value.startswith('webarena-verified '))
    if not any(value.split(';', 1)[0].strip() == expected_webarena
               for value in requires('browser-harness-eval') or []):
        raise ValueError('Installed WebArena dependency differs from declared fork')


def verify_base_dataset_selection() -> None:
    """Dataset selection must not require the optional publication extra."""
    from harness.benchmarks.pinning import validate_selected_binding

    assert validate_selected_binding({'revision': 'a' * 40}) == 'a' * 40


def verify_lexbench_input_ownership(source_root: Path) -> None:
    from harness.web_fetch.native_projection import native_projection_regressions
    from harness.web_fetch.local_revision import _require_revision_regressions
    source_controls = native_projection_regressions()
    assert source_controls and _require_revision_regressions(list(source_controls)) == list(source_controls)
    from harness.web_fetch.reference_inputs import benchmark_input_path, require_mutable_inputs
    from lexbench_webfetch.inputs import definition_path
    logical = source_root / 'evaluation/dataset.json'
    assert benchmark_input_path(logical).parent == definition_path('evaluation/source-inputs.json').parent
    try:
        require_mutable_inputs(logical)
    except ValueError:
        pass
    else:
        raise AssertionError('Installed harness allowed writes to frozen benchmark inputs')


def verify_package_tree(source_root: Path, package_root: Path, *, package_name='harness') -> None:
    """Require the installed library's exact current source and declared assets."""
    from harness.distribution import installed_data_path

    project = tomllib.loads((source_root / 'pyproject.toml').read_text())
    directories = project['tool']['setuptools'].get('package-dir', {})

    def source_directory(package):
        owners = [name for name in directories if package == name or package.startswith(name + '.')]
        if owners:
            owner = max(owners, key=len)
            return source_root / directories[owner] / package[len(owner):].lstrip('.').replace('.', '/')
        return source_root / package.replace('.', '/')

    source = source_directory(package_name)
    if source.is_symlink() or package_root.is_symlink():
        raise ValueError('Package roots must not be symlinked')
    expected = {path.relative_to(source).as_posix(): path for path in source.rglob('*.py')}
    for package, patterns in project['tool']['setuptools'].get('package-data', {}).items():
        if package != package_name and not package.startswith(package_name + '.'):
            continue
        directory = source_directory(package)
        for pattern in patterns:
            for path in directory.glob(pattern):
                if path.is_file():
                    expected[path.relative_to(source).as_posix()] = path
    for destination, patterns in project['tool']['setuptools'].get('data-files', {}).items():
        for pattern in patterns:
            for path in source_root.glob(pattern):
                installed_data = installed_data_path((Path(destination) / path.name).as_posix())
                if path.is_symlink() or installed_data.is_symlink():
                    raise ValueError('Distribution data must not be symlinked')
                if installed_data.read_bytes() != path.read_bytes():
                    raise ValueError('Distribution data differs from source: ' + str(path))
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


def verify_review_revisions(root: Path, source_root: Path) -> None:
    from copy import deepcopy

    from harness.web_fetch.result_review import (
        merge_refresh_reviews,
        rebase_review,
        require_result_review,
    )

    dataset = {'comparison': {'require_independent_result_review': True},
               'cases': [{'id': name, 'task_kind': 'body'} for name in ('a', 'b')]}
    records = [{'attempt_id': f'custom__{name}__r001', 'provider': 'custom',
                'case_id': name, 'artifact_path': f'{name}.txt', 'ok': True,
                'metrics': {'content_pass': True, 'content_sha256': hashlib.sha256(b'').hexdigest()}}
               for name in ('a', 'b')]

    def bundle(name, document, rows, *, reviewed):
        path = root / name
        path.mkdir()
        for row in rows:
            (path / row['artifact_path']).write_bytes(b'')
        (path / 'dataset.json').write_text(json.dumps(document))
        (path / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
        if reviewed:
            review = {'schema_version': 1, 'reviewer': {'agent': 'synthetic control'},
                      'bindings': {
                          'dataset_sha256': hashlib.sha256((path / 'dataset.json').read_bytes()).hexdigest(),
                          'results_sha256': hashlib.sha256((path / 'results.jsonl').read_bytes()).hexdigest()},
                      'review_protocol': {'scope': 'all', 'machine_judgments_withheld': True},
                      'attempts': []}
            for row in rows:
                verdict = 'pass' if row['metrics']['content_pass'] else 'fail'
                review['attempts'].append({
                    'attempt_id': row['attempt_id'], 'machine_verdict': verdict,
                    'agent_verdict': verdict, 'final_verdict': verdict,
                    'agent_evidence': 'Independent synthetic answer control'})
            (path / 'result-review.json').write_text(json.dumps(review))
        return path

    prior = bundle('review-prior', dataset, records, reviewed=True)
    corrected = deepcopy(records)
    corrected[0]['metrics']['content_pass'] = False
    current = bundle('review-current', dataset, corrected, reviewed=False)
    review = json.loads((prior / 'result-review.json').read_text())
    review['attempts'][0].update(agent_verdict='fail', final_verdict=None)
    (prior / 'result-review.json').write_text(json.dumps(review))
    before = (current / 'results.jsonl').read_bytes()
    rebased = rebase_review(current, prior, root / 'rebased-review.json', None, install=True)
    assert rebased['attempts'][0]['agent_verdict'] == 'fail'
    assert (current / 'results.jsonl').read_bytes() == before
    require_result_review(current, dataset, corrected)

    revised_dataset = deepcopy(dataset)
    revised_dataset['cases'][0]['expected_any'] = ['new independent requirement']
    refreshed = bundle('review-refreshed', revised_dataset, corrected, reviewed=False)
    subset = {**revised_dataset, 'cases': [revised_dataset['cases'][0]]}
    supplemental = bundle('review-supplemental', subset, corrected[:1], reviewed=True)
    merge_refresh_reviews(refreshed, current, supplemental)
    merged = require_result_review(refreshed, revised_dataset, corrected)
    assert merged['review_protocol']['matrix_refresh_review']['retained_attempts'] == 1
    assert merged['review_protocol']['matrix_refresh_review']['reviewed_attempts'] == 1
    assert (refreshed / 'results.jsonl').read_bytes() == before
    for script in ('review_headless_results.py', 'review_result_anomalies.py'):
        work = root / (script.removesuffix('.py') + '-work')
        command = [sys.executable, str(source_root / 'scripts' / script),
                   '--evidence', str(current), '--work', str(work), '--model', 'synthetic',
                   '--reviewer', 'internal', '--prepare-only']
        for _ in range(2):
            subprocess.run(command, check=True, cwd=root, capture_output=True, text=True)
        session = json.loads((work / 'review-session.json').read_text())
        assert session['bindings']['results_sha256'] == hashlib.sha256(before).hexdigest()
        assert set(session['artifacts_sha256']) == {'a.txt', 'b.txt'}
        assert not (work / 'result-review.json').exists()


    from harness.web_fetch.result_review import merge_reviews, prepare_review_subset
    selected = root / 'installed-review-subset'
    prepare_review_subset(current, selected, case_ids=['b'])
    assert json.loads((selected / 'dataset.json').read_text())['cases'] == [{'id': 'b', 'task_kind': 'body'}]
    assert (selected / 'b.txt').read_bytes() == b''
    assert not (selected / 'a.txt').exists()
    changed = root / 'installed-refresh-subset'
    prepare_review_subset(refreshed, changed, prior_evidence=current)
    assert json.loads((changed / 'results.jsonl').read_text()) == corrected[0]

    sharded = bundle('installed-shard-evidence', dataset, corrected, reviewed=True)
    prior_path = sharded / 'result-review.json'
    failure_review = json.loads(prior_path.read_text())
    failure_review['review_protocol'].update(
        scope='machine-failures', production_scorer_forbidden=True,
        direct_artifact_evidence_required=True)
    failure_review['attempts'] = failure_review['attempts'][:1]
    failure_review['reviewer']['model'] = 'synthetic'
    prior_path.write_text(json.dumps(failure_review))
    shard = root / 'installed-pass-shard.json'
    shard.write_text(json.dumps({
        'schema_version': 1, 'bindings': failure_review['bindings'],
        'reviewer': {'model': 'synthetic'},
        'review_protocol': {'scope': 'machine-passes', 'machine_judgments_withheld': True,
                           'production_scorer_forbidden': True,
                           'direct_artifact_evidence_required': True},
        'attempts': [{'attempt_id': corrected[1]['attempt_id'], 'agent_verdict': 'pass',
                      'agent_evidence': 'Synthetic installation acceptance control'}],
    }))
    merged = merge_reviews(sharded, [shard], prior_path, prior_review=prior_path, install=True)
    assert merged['reviewed_attempts'] == 2
    require_result_review(sharded, dataset, corrected)
    from harness.web_fetch.reference_lifecycle import valid_scoring_revision
    from harness.web_fetch.result_review import rebind_metadata_review
    prior_dataset = root / 'installed-metadata-prior.json'
    prior_dataset.write_bytes((sharded / 'dataset.json').read_bytes())
    metadata_dataset = json.loads(prior_dataset.read_bytes())
    metadata_dataset['name'] = 'Synthetic public title'
    (sharded / 'dataset.json').write_text(json.dumps(metadata_dataset))
    (sharded / 'scoring-revisions.json').write_text(json.dumps([{
        'kind': 'audited_scoring_rule_revision', 'provider_calls': 0,
        'input_dataset_sha256': 'a' * 64, 'input_results_sha256': 'b' * 64,
        'output_dataset_sha256': hashlib.sha256(prior_dataset.read_bytes()).hexdigest(),
        'output_results_sha256': hashlib.sha256((sharded / 'results.jsonl').read_bytes()).hexdigest(),
        'changed_cases': [], 'task_decision_changes': [],
    }]))
    before_metadata_results = (sharded / 'results.jsonl').read_bytes()
    rebind_metadata_review(sharded, prior_dataset)
    assert valid_scoring_revision(sharded)
    assert (sharded / 'results.jsonl').read_bytes() == before_metadata_results
    assert require_result_review(sharded, metadata_dataset, corrected)['reviewed_attempts'] == 2
    for script in ('prepare_result_review_subset.py', 'merge_internal_review_shards.py',
                   'rebind_result_review_metadata.py'):
        subprocess.run([sys.executable, str(source_root / 'scripts' / script), '--help'],
                       check=True, cwd=root, capture_output=True, text=True)


def verify_sample_assembly(root: Path, source_root: Path) -> None:
    fixture_module = runpy.run_path(str(source_root / 'tests/webmainbench_sample_fixture.py'))
    fixture = fixture_module['sample_fixture'](root / 'sample-installation', providers=('native', 'new-engine'))
    command = [sys.executable, '-c',
               'from harness.web_fetch.webmainbench_assembly import sample_main; sample_main()',
               '--source-root', str(fixture['root']), '--dataset', str(fixture['source']),
               '--sample-manifest', str(fixture['manifest']), '--profile', str(fixture['profile']),
               '--review', str(fixture['review']), '--output', str(fixture['output'])]
    subprocess.run(command, check=True, cwd=root, capture_output=True, text=True)
    rows = [json.loads(line) for line in (fixture['output'] / 'results.jsonl').read_text().splitlines()]
    calls = [json.loads(line) for line in (fixture['output'] / 'physical-attempts.jsonl').read_text().splitlines()]
    assert len(rows) == 4 and len(calls) == 6
    assert {row['provider'] for row in rows} == {'native', 'new-engine'}
    for row in rows + calls:
        relative = row.get('physical_artifact_path', row.get('artifact_path'))
        assert hashlib.sha256((fixture['output'] / relative).read_bytes()).hexdigest() == row['output_sha256']
    assert not list(fixture['output'].rglob('private-notes.txt'))
    installed_review = json.loads((fixture['output'] / 'result-review.json').read_bytes())
    assert installed_review['bindings']['results_jsonl_sha256'] == hashlib.sha256((fixture['output'] / 'results.jsonl').read_bytes()).hexdigest()
    before = (fixture['output'] / 'results.jsonl').read_bytes()
    refused = subprocess.run(command, cwd=root, capture_output=True, text=True)
    assert refused.returncode != 0 and (fixture['output'] / 'results.jsonl').read_bytes() == before


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
    (source / 'messages.json').write_bytes(renderer.with_name('messages.json').read_bytes())
    (source / 'evidence.bin').write_bytes(bytes(range(256)))
    destination = root / 'lexbench-package'
    summary = package(source, destination, pack_limit=80)
    assert summary['bundled_files'] == 2
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
    from lexbench_webfetch.risk_control_dataset import load_cohort, parse_sections

    for script in ('append_dynamic_content.py', 'merge_dynamic_supplement.py'):
        subprocess.run([sys.executable, str(source_root / 'scripts' / script), '--help'],
                       cwd=root, check=True, capture_output=True, text=True)
    from lexbench_webfetch.inputs import definition_path
    definitions = definition_path('evaluation/risk-control')
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
    config_hash = hashlib.sha256(b'synthetic custom configuration').hexdigest()
    (measured / 'execution.json').write_text(json.dumps({'batches': [{
        'dataset': {'case_count': 1}, 'providers': [{'name': 'fixture', 'type': 'fixture'}],
        'attempt_count': 1, 'attempt_ids': [record['attempt_id']],
        'provider_config_source': '/home/private/custom.json',
        'provider_config_sha256': config_hash,
        'runtime_identity_record': {'provider_config_sha256': config_hash},
    }]}))
    (measured / 'runtime-identity.json').write_text('{"provider_config_sha256": "unrelated"}')
    merge(reference, [measured], output)
    assert (output / 'results.jsonl').read_bytes() == records
    assert (output / 'artifacts/stdout.txt').read_bytes() == b'process output'
    batch = json.loads((output / 'execution.json').read_text())['batches'][0]
    assert batch['provider_config_source'] == 'custom.json'
    assert batch['provider_config_path_omitted'] is True
    assert batch['provider_config_sha256'] == config_hash
    assert batch['runtime_identity_record'] == {'provider_config_sha256': config_hash}
    assert 'runtime_identity_sha256' not in batch


def verify_teds_revision(root: Path, source_root: Path, *, with_writer: bool = False) -> None:
    from harness.web_fetch import webmainbench_diagnostics as diagnostics
    from lexbench_webfetch.scoring_regressions import build_correction_provenance
    from harness.web_fetch.webmainbench_scoring import aggregate

    source, corrected = root / 'teds-source', root / 'teds-corrected'
    source.mkdir()
    corrected.mkdir()
    metrics = {name: {'score': 0.5, 'success': True, 'details': {}} for name in (
        'text_edit', 'code_edit', 'formula_edit', 'table_edit', 'table_TEDS', 'overall')}
    metrics['overall']['details']['individual_scores'] = {
        name: 0.5 for name in metrics if name != 'overall'}
    row = {'attempt_id': 'custom__one', 'provider': 'custom', 'case_id': 'one',
           'ok': True, 'artifact_path': 'one.md', 'metrics': metrics,
           'reference_sha256': hashlib.sha256(b'reference').hexdigest(),
           'output_sha256': hashlib.sha256(b'synthetic response').hexdigest()}
    original = json.dumps(row) + '\n'
    for directory in (source, corrected):
        (directory / 'one.md').write_bytes(b'synthetic response')
        (directory / 'references').mkdir()
        (directory / 'references/one.md').write_bytes(b'reference')
        (directory / 'dataset.json').write_text(json.dumps({
            'cases': [{'id': 'one', 'meta': {}}], 'coverage_plan': {'static_html': ['custom']}}))
    (source / 'results.jsonl').write_text(original)
    (corrected / 'original-results.jsonl').write_text(original)
    (corrected / 'results.jsonl').write_text(json.dumps({**row, 'official_metrics': metrics}) + '\n')
    summary = aggregate([row])
    for directory in (source, corrected):
        initial = {'official_aggregate': summary, 'dimensions': {},
                   'scorer': {'metric_source_sha256': {}}}
        if directory == corrected:
            initial['original_official_aggregate'] = summary
        (directory / 'score-summaries.json').write_text(json.dumps({
            'custom': initial}))
    receipt = {'schema_version': 2, 'provider_calls': 0, 'source_code_sha256': {},
               'input_results_sha256': diagnostics.digest(source / 'results.jsonl'),
               'output_results_sha256': diagnostics.digest(corrected / 'results.jsonl'),
               'dataset_sha256': diagnostics.digest(corrected / 'dataset.json'),
               'scope': {'logical_results': 1, 'providers': ['custom']}, 'changed_scores': [],
               'correction_provenance': build_correction_provenance(
                   'structured-extraction', diagnostics.ROOT_CAUSE, diagnostics.CONTROLS)}
    (corrected / 'teds-correction.json').write_text(json.dumps(receipt))
    subprocess.run([sys.executable, str(source_root / 'scripts/review_webmainbench_teds_correction.py'),
                    '--source', str(source), '--corrected', str(corrected)], check=True)
    _, audit = diagnostics.validate_teds_revision(corrected)
    assert audit['status'] == 'passed' and audit['provider_calls'] == 0
    if not with_writer:
        return
    repo = root / 'synthetic-scorer'
    package = repo / 'webmainbench/metrics'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    (package / 'base.py').write_text(
        'from dataclasses import dataclass\n'
        '@dataclass\nclass MetricResult:\n'
        '    name: str\n    score: float\n    details: dict\n'
        '    success: bool\n    error_message: str | None = None\n')
    (package / 'calculator.py').write_text(
        'from .base import MetricResult\n'
        'class TableMetric:\n'
        '    def calculate(self, *args, **kwargs):\n'
        "        return MetricResult('table_TEDS', 0.4, {}, True)\n"
        'class MetricCalculator:\n'
        '    def __init__(self, config):\n'
        "        self.metrics = {'table_TEDS': TableMetric()}\n")
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture',
                    '-c', 'user.email=fixture@example.test', 'commit', '-qm', 'Synthetic scorer'], check=True)
    head = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    written = root / 'teds-written'
    subprocess.run([sys.executable, str(source_root / 'scripts/correct_webmainbench_teds.py'),
                    '--source', str(source), '--output', str(written), '--scorer-repo', str(repo),
                    '--scorer-commit', head], check=True)
    subprocess.run([sys.executable, str(source_root / 'scripts/review_webmainbench_teds_correction.py'),
                    '--source', str(source), '--corrected', str(written)], check=True)
    receipt, audit = diagnostics.validate_teds_revision(written)
    assert receipt['scorer_commit'] == head and audit['provider_calls'] == 0
    assert json.loads((written / 'score-summaries.json').read_text())[
        'custom']['original_official_aggregate'] == summary


def verify_release_check_helpers(root: Path, source_root: Path) -> None:
    from harness.report_languages import localized_report_routes, write_language_contract
    from harness.web_fetch.wcxb_report_check import require_analysis_controls

    pages = root / 'release-check'
    (pages / 'analysis').mkdir(parents=True)
    (pages / 'index.html').write_text('<html lang="zh"><p>合成报告</p></html>')
    (pages / 'analysis/index.html').write_text('<html lang="zh"><p>结果</p></html>')
    write_language_contract(pages, include_english=False)
    assert set(localized_report_routes(pages, {'index.html', 'analysis/index.html'})) == {'zh'}
    require_analysis_controls('''<select id="compare-baseline"></select>
        <input name="compare-provider"><select id="compare-relation"></select>
        <tbody id="comparison-rows"></tbody><dialog id="diagnosis-dialog">
        <button data-call-log-close>Close</button><a data-diagnosis-link>Evidence</a></dialog>
        <table><tr data-provider="custom"><td><button data-diagnosis-open>Diagnose</button>
        </td></tr></table>''', 1)
    command = [sys.executable, str(source_root / 'scripts/check_wcxb_report.py')]
    subprocess.run([*command, '--help'], check=True, stdout=subprocess.DEVNULL)
    from harness.web_fetch.wcxb_publication import write_inventory
    profile = root / "selected-profile.json"
    profile.write_text(json.dumps({"comparison_kind": "wcxb_frozen_html_to_markdown"}))
    (root / "report-content-binding.json").write_text("{}")
    write_inventory(pages, json.loads(profile.read_bytes()))
    rejected = subprocess.run([*command, str(pages), '--profile', str(profile)],
                              capture_output=True, text=True)
    assert rejected.returncode != 0 and 'guide/index.html' in rejected.stderr, rejected.stderr
    for script in ('check_webmainbench_report.py', 'check_webmainbench_full_report.py'):
        command = [sys.executable, str(source_root / 'scripts' / script)]
        subprocess.run([*command, '--help'], check=True, stdout=subprocess.DEVNULL)
        unbound = subprocess.run([*command, str(pages)], capture_output=True, text=True)
        assert unbound.returncode != 0 and 'authority' in unbound.stderr, unbound.stderr


def verify_environment_preparation(root: Path, source_root: Path) -> None:
    from unittest.mock import patch
    from harness.web_fetch import accounts
    from harness.environments.webarena import config

    for owner in (accounts, config):
        assert not Path(owner.__file__).resolve().is_relative_to(source_root.resolve())
    source, target = root / 'synthetic-source.env', root / 'synthetic-target.env'
    source.write_text(''.join(f'{key}=synthetic-value\n' for key in accounts.HOSTED_KEYS)
                      + 'UNRELATED_SECRET=synthetic-unrelated\n')
    target.write_text(f'{accounts.HOSTED_KEYS[0]}=synthetic-existing\n')
    command = [sys.executable, str(source_root / 'scripts/import_benchmark_env.py'),
               '--source', str(source), '--target', str(target)]
    result = subprocess.run(command, check=True, cwd=root, capture_output=True, text=True)
    assert 'synthetic-value' not in result.stdout + result.stderr
    assert 'UNRELATED_SECRET' not in target.read_text()
    assert 'synthetic-existing' in target.read_text()
    assert target.stat().st_mode & 0o777 == 0o600
    def registry(url, *, headers=None):
        if 'auth.docker.io' in url:
            return {'token': 'synthetic-token'}, {}
        if '/manifests/' in url:
            return {'config': {'digest': 'sha256:' + 'b' * 64}}, {'Docker-Content-Digest': 'sha256:' + 'a' * 64}
        return {'architecture': 'arm64', 'os': 'linux'}, {}
    output = root / 'synthetic-images.json'
    with patch.object(config, '_registry_json', registry):
        config.main_image_lock(['--output', str(output)])
    before = output.read_bytes()
    assert set(json.loads(before)['images']) == set(config.SITES)
    with patch.object(config, '_registry_json', side_effect=AssertionError('output preflight must precede registry')):
        try:
            config.main_image_lock(['--output', str(output)])
        except ValueError as exc:
            assert 'output already exists' in str(exc)
        else:
            raise AssertionError('existing image lock was replaced')
    assert output.read_bytes() == before


def verify_replay_diagnostics(root: Path, source_root: Path) -> None:
    """Basic installed audits need neither WebArena nor Playwright imports."""
    from harness.trajectory_replay import diagnostics

    assert not Path(diagnostics.__file__).resolve().is_relative_to(source_root.resolve())
    subprocess.run([sys.executable, '-c',
                    'import sys; import harness.trajectory_replay.diagnostics; '
                    'import harness.trajectory_replay.public_webarena.diagnostics; '
                    'assert "webarena_verified" not in sys.modules; '
                    'assert "playwright" not in sys.modules'], check=True, cwd=root)
    for command in ('audit_replay_readiness.py', 'audit_replay_cart_assertion.py',
                    'audit_replay_comparison_inputs.py', 'audit_saved_replay_correction.py',
                    'audit_webarena_recorded_outcomes.py', 'verify_webarena_evaluator_exclusions.py',
                    'rescore_saved_retrieval_attempt.py', 'supersede_webarena_task_browser_results.py',
                    'build_revised_webarena_dataset.py'):
        subprocess.run([sys.executable, str(source_root / 'scripts' / command), '--help'],
                       check=True, cwd=root, capture_output=True)
    stage = root / 'replay-diagnostics'
    stage.mkdir()
    trace = stage / 'trace.json'
    trace.write_text(json.dumps([
        {'index': 1, 'result': 'button disabled: Submit'},
        {'index': 2, 'arguments': {'action': 'click', 'selector': 'button:has-text("Submit")'}},
    ]))
    results = stage / 'results.json'
    results.write_text(json.dumps({'attempts': [{
        'participant_id': 'custom', 'task_id': 'one', 'status': 'pass',
        'evidence': {'trace': {'path': str(trace),
                             'sha256': hashlib.sha256(trace.read_bytes()).hexdigest()}},
    }]}))
    output = stage / 'diagnostic.json'
    args = [sys.executable, str(source_root / 'scripts/audit_replay_readiness.py'),
            '--results', str(results), '--output', str(output)]
    subprocess.run(args, check=True, cwd=stage, capture_output=True)
    report = json.loads(output.read_bytes())
    assert report['score_effect'] == 'none' and report['findings'][0]['historical_status'] == 'pass'
    before = output.read_bytes()
    assert subprocess.run(args, cwd=stage, capture_output=True).returncode != 0
    assert output.read_bytes() == before
    task = stage / 'task-1'
    task.mkdir()
    for name in ('network.har', 'trace.json'):
        (task / name).write_text('{}')
    sites, before_catalog, after_catalog = [stage / name for name in (
        'sites.json', 'before.json', 'after.json')]
    for path in (sites, before_catalog, after_catalog):
        path.write_text('{}')
    digest = hashlib.sha256(b'{}').hexdigest()
    (stage / 'receipt.json').write_text(json.dumps({
        'method_id': 'custom', 'site_map_sha256': digest, 'results': [{
            'task_id': 1, 'status': 'fail', 'score': None, 'completed_actions': 0,
            'action_count': 1, 'evidence_sha256': {'trace': digest, 'network_har': digest}}],
    }))
    audit = diagnostics.audit_saved_correction(
        task_dir=task, task_id=1, site_map=sites,
        before_catalog=before_catalog, after_catalog=after_catalog)
    assert audit['browser_calls'] == 0 and audit['evaluation_reached'] is False


def verify_replay_supersession(root: Path, source_root: Path) -> None:
    from harness.trajectory_replay.public_webarena import manifest_repair

    assert not Path(manifest_repair.__file__).resolve().is_relative_to(source_root.resolve())
    fixture = runpy.run_path(str(source_root / 'tests/public_webarena_fixture.py'))
    base, correction = fixture['write_supersession_fixture'](root / 'supersession-sources')
    before = {path.resolve(): path.read_bytes() for source in (base, correction)
              for path in source.rglob('*') if path.is_file()}
    output = root / 'supersession'
    args = [sys.executable, str(source_root / 'scripts/supersede_webarena_task_browser_results.py'),
            '--base-run', str(base), '--correction-run', str(correction), '--output', str(output),
            '--replace', 'case-a/moli', '--reason', 'Synthetic installed command correction']
    subprocess.run(args, check=True, cwd=root, capture_output=True)
    assert all(path.read_bytes() == raw for path, raw in before.items())
    run = output / 'run'
    manifest = json.loads((run / 'manifest.json').read_bytes())
    source_bytes = (run / 'source-inputs.json').read_bytes()
    assert manifest['artifact_provenance']['source_inputs_sha256'] == hashlib.sha256(source_bytes).hexdigest()
    for row in json.loads(source_bytes)['files']:
        assert (run / row['retained_path']).read_bytes() == before[Path(row['path'])]
    rows = [json.loads(line) for line in (run / 'attempts.jsonl').read_text().splitlines()]
    assert [(row['engine'], row['attempt'], row['status']) for row in rows] == [
        ('chromium', 1, 'pass'), ('moli', 1, 'fail'), ('moli', 2, 'pass')]
    shutil.rmtree(base)
    shutil.rmtree(correction)
    for row in rows:
        path = Path(row['evidence_directory']) / 'attempt.json'
        assert json.loads(path.read_bytes()) == row
        assert hashlib.sha256((run / row['source_attempt_path']).read_bytes()).hexdigest() == row['source_attempt_sha256']
    before_output = (run / 'manifest.json').read_bytes()
    assert subprocess.run(args, cwd=root, capture_output=True).returncode != 0
    assert (run / 'manifest.json').read_bytes() == before_output


def verify_qualification_commands(root: Path, source_root: Path) -> None:
    from harness.trajectory_replay.public_webarena import qualification

    assert not Path(qualification.__file__).resolve().is_relative_to(source_root.resolve())
    stage = root / 'qualification'
    stage.mkdir()
    native_suite, native_images, native_exclusions = [stage / name for name in (
        'native-suite.json', 'native-images.json', 'native-exclusions.json')]
    native_suite.write_text(json.dumps({'tasks': [{
        'id': 'custom-native', 'environment': {'sites': ['custom']}}]}))
    native_images.write_text(json.dumps({'images': {'custom': {'architecture': 'arm64'}}}))
    native_exclusions.write_text(json.dumps({
        'schema': 'lex_webarena_evaluator_exclusions/1', 'excluded_tasks': []}))
    native_output = stage / 'native-scope.json'
    native_args = [sys.executable, str(source_root / 'scripts/build_webarena_native_scope.py'),
                   '--suite', str(native_suite), '--image-lock', str(native_images),
                   '--evaluator-exclusions', str(native_exclusions), '--architecture', 'arm64',
                   '--max-tasks-per-shard', '1', '--output', str(native_output)]
    subprocess.run(native_args, check=True, cwd=stage, capture_output=True)
    assert json.loads(native_output.read_bytes())['included_task_ids'] == ['custom-native']
    native_before = native_output.read_bytes()
    assert subprocess.run(native_args, cwd=stage, capture_output=True).returncode != 0
    assert native_output.read_bytes() == native_before
    repair_source = stage / 'repair-source.json'
    original_action = {'action': 'fill', 'selector': '#title', 'text': 'Frozen title'}
    repair_source.write_text(json.dumps({'tasks': [{
        'id': 'task-1', 'trajectory': {'actions': [original_action]},
        'ground_truth': {'response_builder': {
            'kind': 'null_after_verified_path', 'task_type': 'mutate'}},
    }]}))
    repair_config = stage / 'repair-config.json'
    repair_config.write_text(json.dumps({
        'schema': 'lex_browser_use_action_repair/1', 'task_id': 'task-1',
        'reason': 'Correct exact frozen input', 'edits': [{
            'start_index': 1, 'expected_actions': [original_action],
            'replacement_actions': [{**original_action, 'text': 'Corrected title'}],
        }],
    }))
    repair_output = stage / 'repair-proposal.json'
    arguments = ['--suite', str(repair_source), '--config', str(repair_config),
                 '--output', str(repair_output)]
    command = [sys.executable, str(source_root / 'scripts/propose_webarena_action_repair.py')]
    generated = subprocess.run([*command, *arguments], cwd=stage, capture_output=True)
    assert generated.returncode == 0, generated.stderr.decode()
    proposal = json.loads(repair_output.read_bytes())
    assert proposal['input_sha256']['suite'] == hashlib.sha256(
        repair_source.read_bytes()).hexdigest()
    probe = qualification.build_probe(json.loads(repair_source.read_bytes()), proposal)
    assert probe['tasks'][0]['ground_truth']['qualification']['status'] == 'pending'
    assert probe['benchmark']['repair_probe']['non_canonical'] is True
    original_proposal = repair_output.read_bytes()
    repeated = subprocess.run([*command, *arguments], cwd=stage, capture_output=True)
    assert repeated.returncode != 0 and repair_output.read_bytes() == original_proposal
    for kind in ('action_repair', 'observed_repair', 'capture_target', 'replace_target',
                 'autowait_repairs', 'evaluator_fill_repair'):
        subprocess.run([sys.executable,
                        str(source_root / f'scripts/propose_webarena_{kind}.py'), '--help'],
                       check=True, cwd=stage, capture_output=True)
    fixture = runpy.run_path(str(source_root / 'tests/public_webarena_fixture.py'))
    candidate, decisions = fixture['authoring_suite_fixture']()
    candidate_path, decisions_path = stage / 'candidate.json', stage / 'decisions.json'
    candidate_path.write_text(json.dumps(candidate))
    decisions_path.write_text(json.dumps(decisions))
    original = candidate_path.read_bytes()
    for name, entry, arguments in (
        ('observation', qualification.main_observation_suite, ['--task-ids', 'webarena-1']),
        ('authoring', qualification.main_authoring_suite,
         ['--qualification', str(decisions_path)]),
    ):
        output = stage / (name + '.json')
        args = ['--suite', str(candidate_path), '--output', str(output), *arguments]
        entry(args)
        derived = output.read_bytes()
        entry(args)
        assert output.read_bytes() == derived and candidate_path.read_bytes() == original
        assert qualification.load_suite(output).tasks[0].task_id == 'webarena-1'
        output.write_text('retained independent input')
        try:
            entry(args)
        except ValueError:
            pass
        else:
            raise AssertionError('Installed derivation replaced an existing input')
        assert output.read_text() == 'retained independent input'
    suite = stage / 'suite.json'
    suite.write_text(json.dumps({'schema': 'lex_browser_use_tasks/4', 'tasks': [{
        'id': 'a', 'title': 'Candidate', 'difficulty': 'simple', 'tags': ['mutate'],
        'trajectory': {'schema': 'lex_browser_trajectory/1', 'actions': []},
        'ground_truth': {'expected_actions': [], 'qualification': {'status': 'pending'}},
        'source': {'url': 'https://example.invalid/task'},
        'evaluator': {'kind': 'webarena_verified', 'task_id': 1, 'revision': 1},
        'environment': {'kind': 'webarena_verified', 'sites': ['shopping']},
    }]}))
    scope = stage / 'scope.json'
    scope.write_text(json.dumps({
        'schema': 'lex_browser_use_native_scope/1',
        'suite_sha256': hashlib.sha256(suite.read_bytes()).hexdigest(),
        'included_task_ids': ['a'], 'excluded_tasks': [],
        'qualification_batches': [{'id': 'shopping', 'sites': ['shopping'],
                                   'shard_count': 1, 'task_ids': ['a']}],
    }))
    config = stage / 'config.json'
    config.write_text('{}')
    evidence = stage / 'evidence'
    partial = evidence / 'pass-1/shopping-0-of-1/partial.txt'
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b'preserve partial evidence')
    scripts = source_root / 'scripts'
    subprocess.run([
        sys.executable, str(scripts / 'run_webarena_qualification.py'), '--suite', str(suite),
        '--native-scope', str(scope), '--webarena-config', str(config),
        '--evidence-root', str(evidence), '--dry-run',
    ], check=True, cwd=stage, capture_output=True)
    assert partial.read_bytes() == b'preserve partial evidence'
    assert not (evidence / '_failed').exists() and not (evidence / 'pass-2').exists()
    queue = stage / 'queue.json'
    queue.write_text(json.dumps({'tasks': [{'task_id': 'a', 'disposition': 'eligible'}]}))
    for command, arguments in (
        ('build_webarena_qualification_subset.py', ['--qualification', str(queue),
                                                  '--dispositions', 'eligible',
                                                  '--task-types', 'mutate']),
        ('build_webarena_requalification_suite.py', ['--native-scope', str(scope)]),
    ):
        output = stage / (command + '.json')
        subprocess.run([sys.executable, str(scripts / command), '--suite', str(suite),
                        *arguments, '--output', str(output)], check=True, cwd=stage)
        assert qualification.load_suite(output).tasks[0].task_id == 'a'


def verify_repair_commands(root: Path, source_root: Path) -> None:
    from harness.trajectory_replay.public_webarena import diagnostics, qualification

    assert not Path(diagnostics.__file__).resolve().is_relative_to(source_root.resolve())
    stage = root / 'repairs'
    stage.mkdir()
    suite = json.loads((root / 'qualification/suite.json').read_text())
    task = suite['tasks'][0]
    task['source']['verified_evaluators'] = [{
        'evaluator': 'AgentResponseEvaluator', 'expected': {
            'task_type': 'retrieve', 'retrieved_data': ['PRIVATE REFERENCE'],
        },
    }]
    suite_path = stage / 'suite.json'
    suite_path.write_text(json.dumps(suite))
    screenshot, page, har = stage / 'final.png', stage / 'page.json', stage / 'network.har'
    screenshot.write_bytes(b'fixture screenshot')
    page.write_text(json.dumps({'visible_text': 'Observed heading'}))
    har.write_text(json.dumps({'log': {'entries': []}}))
    attempt = stage / 'attempt.json'
    attempt.write_text(json.dumps({
        'task_id': 'a', 'evidence_directory': str(stage),
        'error': 'PRIVATE REFERENCE',
        'trace': [{'action': 'evaluate_result', 'expected': 'PRIVATE REFERENCE'}],
        'final_page_evidence': {'status': 'complete', **{
            key: {'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            for key, path in (('screenshot', screenshot), ('page', page))
        }},
    }))
    proposal = {
        'schema': 'lex_browser_use_repair_proposal/1', 'task_id': 'a',
        'reason': 'Observe the visible heading',
        'append_actions': [{'action': 'get_text', 'selector': 'h1', 'save_as': 'heading'}],
        'response_builder': {'kind': 'observed_shape', 'task_type': 'retrieve',
                             'shape': [{'$output': 'heading'}]},
    }
    proposal_path = stage / 'proposal.json'
    proposal_path.write_text(json.dumps(proposal))
    scripts = source_root / 'scripts'
    packet = stage / 'packet.json'
    subprocess.run([sys.executable, str(scripts / 'build_webarena_repair_packet.py'),
                    '--suite', str(suite_path), '--attempt', str(attempt),
                    '--output', str(packet)], check=True, cwd=stage)
    assert json.loads(packet.read_text())['schema'] == diagnostics.PACKET_SCHEMA
    assert 'PRIVATE REFERENCE' not in packet.read_text()
    for entry in ('build_webarena_repair_probe.py', 'build_webarena_repair_batch.py',
                  'build_webarena_codex_repair_suite.py'):
        output = stage / (entry + '.json')
        subprocess.run([sys.executable, str(scripts / entry), '--suite', str(suite_path),
                        '--proposal', str(proposal_path), '--output', str(output)],
                       check=True, cwd=stage)
        assert qualification.load_suite(output).tasks[0].task_id == 'a'
    fake = stage / 'fake-codex'
    fake.write_text('#!' + sys.executable + '\n' +
        'import json, pathlib, sys\n'
        'args = sys.argv\n'
        'packet = pathlib.Path(args[args.index("-C") + 1]) / "packet.json"\n'
        'value = json.loads(packet.read_text())\n'
        'assert "provenance_references" not in value\n'
        'assert "attempt" not in value["evidence_references"]\n'
        'assert "PRIVATE REFERENCE" not in packet.read_text()\n' +
        'pathlib.Path(args[args.index("-o") + 1]).write_text(' +
        repr(json.dumps(proposal)) + ')\n')
    fake.chmod(0o700)
    completed = subprocess.run([
        sys.executable, str(scripts / 'run_webarena_codex_repairs.py'),
        '--suite', str(suite_path), '--packet', str(packet),
        '--output-root', str(stage / 'jobs'), '--codex-binary', str(fake),
        '--model', 'offline-fixture', '--max-workers', '1',
    ], check=True, cwd=stage, capture_output=True, text=True)
    summary = json.loads(completed.stdout)
    assert summary['status'] == 'complete' and summary['qualification_effect'] == 'none'
    assert summary['model'] == 'offline-fixture'


def verify_adjudication_commands(root: Path, source_root: Path) -> None:
    from harness.trajectory_replay.public_webarena import qualification as q
    from harness.trajectory_replay.public_webarena.har import HAR_CREATOR_NAME, HAR_CREATOR_VERSION

    stage = root / 'adjudication'
    stage.mkdir()
    suite_path = stage / 'suite.json'
    suite_path.write_bytes((root / 'repairs/build_webarena_repair_probe.py.json').read_bytes())
    suite = json.loads(suite_path.read_text())
    task = suite['tasks'][0]
    rows = []
    for index in (1, 2):
        directory = stage / f'pass-{index}'
        directory.mkdir()
        (directory / 'final.png').write_bytes(b'synthetic screenshot')
        (directory / 'final-page.json').write_text('{"visible_text":"Observed heading"}')
        (directory / 'network.har').write_text(json.dumps({'log': {'creator': {
            'name': HAR_CREATOR_NAME, 'version': HAR_CREATOR_VERSION}, 'entries': []}}))
        row = {'task_id': task['id'], 'engine': 'chromium', 'attempt': 1, 'is_retry': False,
               'qualification_run': True, 'qualification_pass_index': index,
               'qualification_required_passes': 2, 'run_id': f'run-{index}',
               'task_contract_sha256': q.task_contract_sha256(task),
               'evidence_directory': str(directory),
               **{key: 'pass' for key in ('status', 'process_status', 'result_status',
                                         'evidence_status', 'combined_status')},
               'final_page_evidence': {'status': 'complete', **{
                   key: {'path': name, 'sha256': hashlib.sha256(
                       (directory / name).read_bytes()).hexdigest()}
                   for key, name in (('screenshot', 'final.png'), ('page', 'final-page.json'))}}}
        (directory / 'attempt.json').write_text(json.dumps(row))
        rows.append(row)
    projection = stage / 'attempts.jsonl'
    projection.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    scripts = source_root / 'scripts'
    queue, final = stage / 'queue.json', stage / 'final.json'
    for command, arguments in (
        ('build_webarena_repair_queue.py', ['--attempts', str(projection), '--output', str(queue)]),
        ('finalize_webarena_adjudication.py', ['--qualification', str(queue), '--validated-run',
                                             f'{suite_path}::{projection}', '--output', str(final)]),
        ('apply_webarena_qualification.py', ['--qualification', str(final),
                                           '--output', str(stage / 'applied.json')]),
    ):
        subprocess.run([sys.executable, str(scripts / command), '--suite', str(suite_path),
                        *arguments], check=True, cwd=stage)
    applied = q.load_suite(stage / 'applied.json').tasks[0]
    assert applied.ground_truth['qualification']['status'] == 'eligible'
    assert len(applied.ground_truth['qualification']['validated_run_sha256s']) == 2
    decisions = json.loads(final.read_text())
    decision = decisions['tasks'][0]
    decision['disposition'] = 'needs_trace_repair'
    decision['adjudication']['reason'] = 'Use the current heading locator'
    corrected = json.loads(json.dumps(task['trajectory']))
    corrected['actions'][-1]['selector'] = 'main h1'
    decision['adjudication']['corrected_trajectory'] = corrected
    proposed = stage / 'proposed.json'
    proposed.write_text(json.dumps(decisions))
    for command in ('build_webarena_repair_validation_suite.py',
                    'build_webarena_evidence_revalidation_suite.py'):
        output = stage / (command + '.json')
        subprocess.run([sys.executable, str(scripts / command), '--suite', str(suite_path),
                        '--qualification', str(proposed), '--output', str(output)],
                       check=True, cwd=stage)
        candidate = q.load_suite(output).tasks[0]
        assert candidate.ground_truth['qualification']['status'] == 'pending'
        assert candidate.tool_calls[-1]['selector'] == 'main h1'



def verify_browser_report_checks(root: Path, source_root: Path) -> None:
    """Invoke installed checkers through their public commands outside the checkout."""
    report = root / 'linked-report-source'
    report.mkdir()
    linked = root / 'linked-report'
    linked.symlink_to(report, target_is_directory=True)
    commands = [
        ('check_webarena_replay_report.py', [str(linked)]),
        ('check_browser_use_report.py', [str(linked), '--evidence', str(root)]),
        ('check_browser_use_visual.py', [str(linked)]),
        ('check_webarena_replay_visual.py', [str(linked)]),
    ]
    for command, arguments in commands:
        entry = str(source_root / 'scripts' / command)
        subprocess.run([sys.executable, entry, '--help'], check=True,
                       cwd=root, stdout=subprocess.DEVNULL)
        rejected = subprocess.run([sys.executable, entry, *arguments], cwd=root,
                                  capture_output=True, text=True)
        assert rejected.returncode != 0 and 'symlink' in rejected.stderr.lower(), rejected.stderr
    for command in ('package_browser_use_artifact_hub.py',
                    'package_webarena_replay_artifact_site.py',
                    'publish_browser_use_artifact_hub.py',
                    'publish_webarena_replay_artifact_site.py'):
        subprocess.run([sys.executable, str(source_root / 'scripts' / command), '--help'],
                       check=True, cwd=root, stdout=subprocess.DEVNULL)
    from harness.reporting.publication import (
        PublicationOperation,
        bind_publication_files,
        json_bytes,
        package_archive,
        recover_binding,
    )
    frozen = root / 'package-source'
    frozen.mkdir()
    (frozen / 'index.html').write_text('Installed publication control')
    output, proof = root / 'package.zip', root / 'package.json'
    package_archive(
        frozen, output, proof, inputs=[], validate=lambda: None,
        attest=lambda archive: {'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()},
    )
    with zipfile.ZipFile(output) as archive:
        assert archive.read('index.html') == b'Installed publication control'
    assert json.loads(proof.read_text())['archive_sha256'] == hashlib.sha256(
        output.read_bytes()
    ).hexdigest()
    from harness.web_fetch.report_package import build as package_webfetch
    webfetch = root / 'webfetch-report'
    (webfetch / 'assets').mkdir(parents=True)
    (webfetch / 'assets/style.css').write_text('body {color:black}')
    (webfetch / 'assets/report.js').write_text("window.addEventListener('report-ready',initReport);")
    (webfetch / 'index.html').write_text('<html><body>Installed Web Fetch report</body></html>')
    (webfetch / 'dataset.json').write_text('{}')
    (webfetch / 'results.jsonl').write_text('')
    (webfetch / 'reliability.json').write_text('{"complete":true}')
    webfetch_archive = root / 'webfetch.zip'
    package_webfetch(webfetch, webfetch_archive)
    from harness.web_fetch.publication_check import check as check_webfetch_package
    check_webfetch_package(webfetch_archive, webfetch)
    for command in ('publish_artifact_hub.py', 'verify_artifact_hub.py',
                    'check_publication_bundle.py'):
        subprocess.run([sys.executable, str(source_root / 'scripts' / command), '--help'],
                       check=True, cwd=root, stdout=subprocess.DEVNULL)
    from harness.web_fetch.report_publish import PublishConfig
    from harness.web_fetch.report_publish import publish as submit_webfetch
    target = root / 'webfetch-profile.json'
    target.write_text(json.dumps({'artifact_hub_base_url': 'https://reports.invalid',
                                 'artifact_hub_site': 'installed-site'}))
    receipt = root / 'webfetch-receipt.json'
    receipt.write_text(json.dumps({'slug': 'installed-site', 'versionId': 'ver_installed',
                                  'share_url': 'https://reports.invalid/v/installed'}))
    summary = submit_webfetch(PublishConfig(target, webfetch_archive, webfetch, receipt,
                                           root / 'webfetch-operation.json'))
    assert summary['versionId'] == 'ver_installed'
    with zipfile.ZipFile(webfetch_archive) as archive:
        assert b'function render()' in archive.read('loader.js')
        assert json.loads(archive.read('manifest.json'))['architecture']['logical_routes'] == 1
    binding_profile = root / 'binding-profile.json'
    binding_profile.write_bytes(json_bytes({'publication': {'expected_version': 'old'}}))
    binding_proof = root / 'binding-proof.json'
    binding_proof.write_bytes(json_bytes({'archive_sha256': 'frozen'}))
    binding_journal = root / 'binding-pending.json'

    def interrupted_write(path, payload):
        if path.resolve() == binding_proof.resolve():
            raise OSError('interrupted proof installation')
        path.write_bytes(payload)

    try:
        bind_publication_files(
            binding_profile, binding_proof, binding_journal,
            publication={'expected_version': 'new'}, schema='installed-control/1',
            writer=interrupted_write,
        )
    except OSError:
        pass
    else:
        raise AssertionError('binding failure was not exercised')
    recover_binding(binding_profile, binding_proof, binding_journal, schema='installed-control/1')
    assert not binding_journal.exists()
    assert json.loads(binding_profile.read_bytes())['publication']['expected_version'] == 'new'
    assert json.loads(binding_proof.read_bytes())['profile_sha256'] == hashlib.sha256(
        binding_profile.read_bytes()
    ).hexdigest()
    operations = PublicationOperation(root / 'operation-pending.json', 'installed-control')
    operation, resumed = operations.pending({'archive_sha256': 'frozen'}, allow_create=True)
    assert not resumed
    assert operations.pending({'archive_sha256': 'frozen'}, allow_create=False) == (operation, True)
    operations.clear({'archive_sha256': 'frozen'})
    entry = str(source_root / 'scripts/check_browser_use_report.py')
    missing = subprocess.run([
        sys.executable, entry, str(report), '--evidence', str(report),
        '--authority-root', str(root),
    ], cwd=root, capture_output=True, text=True)
    assert missing.returncode != 0 and '--authority-root' in missing.stderr, missing.stderr
    authority = root / 'authority'
    profile = authority / 'evaluation/browser-use/current-report.json'
    profile.parent.mkdir(parents=True)
    profile.write_text(json.dumps({
        'schema': 'lex_browser_use_current_report/2',
        'engines': ['fixture'], 'engine_labels': {'fixture': 'Fixture'},
        'models': ['fixture'], 'model_labels': {'fixture': 'Fixture'},
        'baseline_engine': 'fixture',
    }))
    implicit = subprocess.run([
        sys.executable, entry, str(report), '--evidence', str(report),
    ], cwd=authority, capture_output=True, text=True)
    assert implicit.returncode != 0 and '--authority-root' in implicit.stderr, implicit.stderr
    (report / 'manifest.json').write_text(json.dumps({'protocol': {'model': 'fixture'}}))
    (report / 'attempts.jsonl').write_text('')
    configured = subprocess.run([
        sys.executable, entry, str(report), '--evidence', str(report),
        '--authority-root', str(authority),
    ], cwd=root, capture_output=True, text=True)
    expected_selection = authority / 'evaluation/browser-use/webarena-verified-pilot.json'
    assert configured.returncode != 0 and str(expected_selection) in configured.stderr, configured.stderr


def verify_browser_observation_capture(root, source_root):
    from harness.web_fetch import browser_observations as observations

    subprocess.run([
        sys.executable, str(source_root / 'scripts/capture_headless_browser_observations.py'),
        '--help',
    ], check=True, cwd=root, capture_output=True, text=True)
    registry = json.loads(observations.CONTRACTS.read_bytes())
    assert registry['schema_version'] == 1
    assert registry['contracts']['javascript-spa']['capture']['minimum'] == 10
    task = {'id': 'installed-observation', 'url': 'https://example.invalid/',
            'source_verification': {'browser_observation': {
                'selector': '.card', 'minimum': 3, 'content_selector': 'main',
                'text_mode': 'visible',
            }}}
    assert observations.capture_policy(task).minimum == 3
    assert observations.extract_pruned_selector_text(
        '<main>Installed reference<nav>Navigation</nav></main>', task['url']
    ) == 'Installed reference'
    binary = root / 'observation-browser'
    binary.write_bytes(b'synthetic installed browser identity')
    calls = []
    original = observations._capture_in_process

    def observe(case, *args, **kwargs):
        calls.append(case['id'])
        return {'case_id': case['id'], 'status': 'success', 'content_text': 'Installed capture'}

    observations._capture_in_process = observe
    try:
        options = dict(timeout_ms=1000, attempts=1, browser_binary=str(binary),
                       checkpoints=root / 'observation-checkpoints')
        first = observations.capture_batch([task], **options)
        assert observations.capture_batch([task], **options) == first
        assert calls == ['installed-observation']
    finally:
        observations._capture_in_process = original


def verify_reference_promotion(root, source_root):
    fixture = runpy.run_path(str(source_root / 'tests/reference_freeze_fixture.py'))
    stage = root / 'reference-promotion'
    stage.mkdir()
    inputs = fixture['create_promotion_inputs'](stage)
    receipt, candidate = stage / 'receipt.json', stage / 'candidate.json'
    prepare = [sys.executable, '-m', 'harness.web_fetch.reference_freeze', 'prepare-promotion',
               '--dataset', str(inputs['dataset_path']), '--captures', str(inputs['captures']),
               '--output', str(receipt), '--candidate-output', str(candidate),
               '--browser-observations', str(inputs['observations_path']),
               '--approved-by', 'synthetic-independent-review', '--approved-at', inputs['checked_at'],
               '--root-cause-category', 'reference-evidence', '--root-cause', fixture['ROOT_CAUSE']]
    for identifier in fixture['REGRESSIONS']:
        prepare.extend(['--regression-id', identifier])
    subprocess.run(prepare, cwd=root, check=True, capture_output=True, text=True)
    before = (receipt.read_bytes(), candidate.read_bytes())
    repeated = subprocess.run(prepare, cwd=root, capture_output=True, text=True)
    assert repeated.returncode != 0 and 'immutable' in repeated.stderr
    assert before == (receipt.read_bytes(), candidate.read_bytes())
    research = stage / 'research'
    subprocess.run([sys.executable, '-m', 'harness.web_fetch.reference_freeze', 'promote',
                    '--canonical', str(inputs['dataset_path']), '--candidate', str(candidate),
                    '--captures', str(inputs['captures']), '--receipt', str(receipt),
                    '--receipt-target', str(research / 'promotion.json'),
                    '--research-root', str(research),
                    '--browser-observations', str(inputs['observations_path'])],
                   cwd=root, check=True, capture_output=True, text=True)
    dataset = json.loads(inputs['dataset_path'].read_bytes())
    assert dataset['cases'][0]['ground_truth_main_content'] == inputs['new_reference']
    observed = stage / 'sources/javascript-spa/current-browser-observation.json'
    assert json.loads(observed.read_bytes()) == inputs['observation']
    assert (research / 'promotion.json').read_bytes() == receipt.read_bytes()


def verify_wcxb_scoring(root, source_root):
    from harness.record_io import load_jsonl
    from harness.web_fetch.wcxb_scoring import score

    fixture = runpy.run_path(str(source_root / 'tests/wcxb_fixture.py'))
    options = fixture['create_cohort'](root / 'wcxb-scoring', paired=True)
    summary = score(**options)
    assert summary['case_count'] == 2
    assert summary['providers']['alpha']['mean_latency_ms'] == 11
    assert summary['providers']['gamma']['mean_latency_ms'] == 13
    assert summary['providers']['beta']['physical_call_count'] == 3
    rows = load_jsonl(options['evidence_root'] / 'wcxb-scores.jsonl')
    assert len(rows) == 6
    assert sum(row['delivered'] for row in rows) == 5
    subprocess.run([
        sys.executable, str(source_root / 'scripts/score_wcxb_results.py'), '--help',
    ], check=True, cwd=root, capture_output=True, text=True)



def verify_wcxb_rendering(root, source_root):
    """The installed WCXB renderer reads explicit staged inputs, outside the checkout."""
    from harness.report_languages import check_language_contract, generated_languages
    from harness.web_fetch.report import generate_wcxb
    from harness.web_fetch.wcxb_report_check import check_bound_interpretation

    fixture = runpy.run_path(str(source_root / 'tests/wcxb_fixture.py'))
    for english in (False, True):
        inputs = fixture['create_report_inputs'](root / f'wcxb-render-{english}',
                                                 english_source=english)
        generate_wcxb(*inputs, include_english=english)
        report = inputs[0]
        import re

        guide = (report / 'guide/index.html').read_text()
        assert re.findall(r'<main\b[^>]*>(.*?)</main>', guide, re.S) == [
            inputs[-1]['guide']['zh']]
        check_bound_interpretation(report, {'rendered_sha256': {'guide': {
            lang: hashlib.sha256(body.encode()).hexdigest()
            for lang, body in inputs[-1]['guide'].items()}}})
        check_language_contract(report)
        assert generated_languages(report) == (('zh', 'en') if english else ('zh',))
        case = (report / 'cases/4017.html').read_text()
        assert ('href="en/cases/4017.html"' if english else 'href="en/index.html"') in case
        assert (report / ('en/cases/4017.html' if english else 'en/index.html')).is_file()


def verify_wcxb_boundaries(root, source_root):
    """Installed collection, certification and packaging use caller-owned inputs."""
    import ssl
    import urllib.request
    from unittest.mock import patch
    from harness.web_fetch import wcxb_latency, wcxb_publication
    from harness.web_fetch.models import ProviderResult
    from harness.record_io import load_jsonl

    fixture = runpy.run_path(str(source_root / 'tests/wcxb_fixture.py'))
    options = fixture['create_cohort'](root / 'wcxb-boundaries')
    binaries = {name: root / ('wcxb-' + name) for name in ('release', 'pr')}
    for name, binary in binaries.items():
        binary.write_text(name)
    profile = json.loads(options['profile_path'].read_bytes())
    profile.update(release_binary_sha256=fixture['sha'](binaries['release'].read_bytes()),
                   candidate_binary_sha256=fixture['sha'](binaries['pr'].read_bytes()), concurrency=1)
    fixture['write_json'](options['profile_path'], profile)

    class LocalReader:
        def __init__(self, binary):
            self.config = {}
            self.binary = binary

        def validate(self):
            return []

        def implementation_identity(self):
            return {'binary_sha256': fixture['sha'](self.binary.read_bytes())}

        def fetch(self, case, timeout):
            context = ssl.create_default_context(cafile=self.config['ca_cert'])
            with urllib.request.urlopen(case.url, timeout=timeout, context=context) as response:
                return ProviderResult(True, response.read().decode())

    output = root / 'wcxb-collected'
    with patch.object(wcxb_latency, 'require_quiet_host', return_value=1.0), \
            patch.object(wcxb_latency.time, 'sleep'):
        result = wcxb_latency.collect(
            dataset=options['source'], manifest=options['sample_manifest'],
            profile=options['profile_path'], output=output, binaries=binaries,
            providers={name: LocalReader(binary) for name, binary in binaries.items()},
        )
    assert result['call_count'] == len(load_jsonl(output / 'calls.jsonl')) == 8
    profile['latency_correction_receipt_sha256'] = fixture['sha']((output / 'receipt.json').read_bytes())
    fixture['write_json'](options['profile_path'], profile)
    subprocess.run([sys.executable, '-m', 'harness.web_fetch.wcxb_latency', 'certify',
                    '--profile', str(options['profile_path']), '--dataset', str(options['source']),
                    '--sample-manifest', str(options['sample_manifest']), '--evidence-root', str(output)],
                   check=True, cwd=root)
    assert json.loads((output / 'certification.json').read_bytes())['physical_call_count'] == 8
    report = root / 'wcxb-report'
    report.mkdir();(report / 'index.html').write_text('<h1>Synthetic package</h1>')
    wcxb_publication.write_inventory(report, profile)
    assert set(wcxb_publication.inventory(report, profile)) == {'index.html', 'public-files.json'}
    (report / 'private-note.txt').write_text('not declared')
    try:
        wcxb_publication.inventory(report, profile)
    except ValueError:
        pass
    else:
        raise AssertionError('Installed WCXB boundary accepted an undeclared file')
    for module in ('wcxb_latency', 'wcxb_publication'):
        subprocess.run([sys.executable, '-m', 'harness.web_fetch.' + module, '--help'],
                       check=True, cwd=root, stdout=subprocess.DEVNULL)

def verify_report_acceptance(root, source_root):
    from harness.web_fetch.report_check import check, check_revision

    fixture = runpy.run_path(str(source_root / 'tests/report_check_fixture.py'))
    report, authority = fixture['create_report'](root / 'report-acceptance', english=True)
    assert check(report, authority_root=authority).startswith('PASS:')
    command = [sys.executable, '-O', str(source_root / 'scripts/check_report.py'),
               str(report), '--authority-root', str(authority)]
    subprocess.run(command, check=True, cwd=root, capture_output=True, text=True)
    (report / 'artifacts/one.txt').write_text('changed output')
    result = subprocess.run(command, cwd=root, capture_output=True, text=True)
    assert result.returncode != 0 and 'PASS:' not in result.stdout
    presentation, authority = fixture['create_revision_report'](root / 'report-presentation')
    assert check_revision(presentation, authority_root=authority).startswith('PASS:')
    subprocess.run([
        sys.executable, '-O', str(source_root / 'scripts/check_report_revision.py'),
        str(presentation), '--authority-root', str(authority),
    ], check=True, cwd=root, capture_output=True, text=True)


def verify_cowagent_isolation(root):
    from lexbench_webfetch.models import EvalCase
    from harness.web_fetch.providers.cowagent import CowAgentBuiltinProvider

    providers = []
    try:
        for version in ('first', 'second'):
            repo = root / ('cowagent-' + version)
            source = repo / 'agent/tools/web_fetch/web_fetch.py'
            source.parent.mkdir(parents=True)
            source.write_text(
                'from types import SimpleNamespace\nclass WebFetch:\n'
                ' def __init__(self, config): pass\n'
                ' def execute(self, args):\n'
                '  return SimpleNamespace(status="success",result="' + version + '")\n')
            safety = repo / 'agent/tools/utils/url_safety.py'
            safety.parent.mkdir()
            safety.write_text('# independent fixture\n')
            for package in (repo / 'agent', repo / 'agent/tools', source.parent):
                (package / '__init__.py').write_text('')
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture',
                            '-c', 'user.email=fixture@example.test', 'commit', '-qm', 'fixture'],
                           check=True)
            revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'],
                                               text=True).strip()
            provider = CowAgentBuiltinProvider(version, {
                'repo_path': str(repo), 'expected_repo_revision': revision,
                'workspace': str(root / ('workspace-' + version)),
                'required_paths': ['{repo_path}/agent/tools/web_fetch/web_fetch.py',
                                   '{repo_path}/agent/tools/utils/url_safety.py'],
            })
            providers.append(provider)
            assert provider.validate() == []
            assert provider._implementation_identity['python']['executable_sha256']
            assert any(name.endswith('cowagent_worker.py') for name in
                       provider._implementation_identity['files_sha256'])
        case = EvalCase('one', 'One', 'https://example.invalid/', 'text', [])
        assert [provider.fetch(case, 5).content for provider in providers] == ['first', 'second']
    finally:
        for provider in providers:
            provider.__del__()


def verify_public_trajectory_publication(root, source_root):
    from harness.trajectory_replay.public_webarena.publication import (
        build, check_report_bundle, stage_report,
    )

    write_fixture = runpy.run_path(str(source_root / 'tests/public_webarena_fixture.py'))[
        'write_report_fixture']
    source, authority = write_fixture(root / 'public-trajectory')
    (source / 'run/private-worker-notes.txt').write_text('private diagnostics')
    staged = root / 'trajectory-staged'
    stage_report(source, staged)
    assert {path.name for path in (staged / 'run').iterdir()} == {'manifest.json', 'summary.json'}
    assert check_report_bundle(staged)['pages'] == 6
    private_name = 'webarena-trajectory-replay-latest-private-notes.txt'
    (staged / 'reports' / private_name).write_text('private report notes')
    archive, attestation = root / 'trajectory.zip', root / 'trajectory-attestation.json'
    result = build(staged, archive, attestation, authority=authority)
    assert result['archive_sha256'] == hashlib.sha256(archive.read_bytes()).hexdigest()
    with zipfile.ZipFile(archive) as bundle:
        assert private_name not in bundle.namelist()
        assert bundle.read('index.html') == bundle.read('webarena-trajectory-replay-latest.html')
    profile = json.loads(authority.read_text())
    profile['suite_sha256'] = 'wrong'
    authority.write_text(json.dumps(profile))
    try:
        build(staged, root / 'rejected.zip', attestation, authority=authority)
    except ValueError as error:
        assert 'suite hash' in str(error)
    else:
        raise AssertionError('Installed publication accepted a different suite')


def verify_oss_workers(root, source_root, *, with_libraries):
    from lexbench_webfetch.models import EvalCase
    from harness.web_fetch.providers.subprocess_jsonl import SubprocessJsonlProvider

    resolver = SubprocessJsonlProvider('resolver', {'repo_path': str(root)})
    workers = {}
    for name in ('openclaw_webfetch_worker.mts', 'dsh_webfetch_worker.mts',
                 'langchain_webbase_worker.py', 'crawl4ai_worker.py'):
        path = Path(resolver._resolve_token('{harness_root}/harness/web_fetch/workers/' + name))
        assert path.is_file() and not path.resolve().is_relative_to(source_root.resolve())
        workers[name] = path
    node = __import__('shutil').which('node')
    assert node is not None
    subprocess.run([node, '--experimental-vm-modules',
                    str(source_root / 'tests/test_dsh_worker.mjs'),
                    str(workers['dsh_webfetch_worker.mts'])], check=True,
                   capture_output=True, text=True, timeout=30)
    upstream = root / 'openclaw-fixture'
    source = upstream / 'src/agents/tools/web-fetch.ts'
    source.parent.mkdir(parents=True)
    source.write_text(
        'export function createWebFetchTool(options) {\n'
        ' if (options.config.tools.web.fetch.cacheTtlMinutes !== 0) throw Error("cache enabled");\n'
        ' return {async execute(id, args) { return {details: {text: "Local tool fixture",\n'
        ' status: 200, finalUrl: args.url}}; }};\n}\n')
    output = subprocess.run([node, '--experimental-strip-types',
                             str(workers['openclaw_webfetch_worker.mts'])],
                            input=json.dumps({'url': 'https://example.invalid/',
                                              'timeout_seconds': 1}) + '\n',
                            env={**os.environ, 'OPENCLAW_REPO': str(upstream)},
                            capture_output=True, text=True, check=True, timeout=15)
    answer = json.loads(output.stdout)
    assert answer['ok'] and answer['content'] == 'Local tool fixture'
    if not with_libraries:
        return

    import http.server
    import threading

    payload = b'name,value\nfixture,42\n'
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path == '/missing':
                self.send_error(404)
                return
            self.send_response(200)
            if self.path == '/download.csv':
                body = payload
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Disposition', 'attachment; filename="fixture.csv"')
            else:
                body = b'<html><head><title>Local fixture</title></head><body>Local worker fixture</body></html>'
                self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    providers = []
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        for worker, library, version, route, kind in (
            ('langchain_webbase_worker.py', 'langchain-community', '0.3.30', '/article', 'body'),
            ('crawl4ai_worker.py', 'crawl4ai', '0.9.3', '/download.csv', 'file'),
        ):
            provider = SubprocessJsonlProvider(library, {
                'repo_path': str(root), 'command': [sys.executable, str(workers[worker])],
                'expected_python_version': 'Python ' + sys.version.split()[0],
                'expected_library_versions': {library: version},
                'required_paths': ['{harness_root}/harness/web_fetch/workers/' + worker],
                'env': {'LANGCHAIN_TRACING_V2': 'false', 'LANGSMITH_TRACING': 'false',
                        'HF_HUB_OFFLINE': '1', 'NO_PROXY': '127.0.0.1,localhost',
                        'CRAWL4_AI_BASE_DIRECTORY': str(root / 'crawler-cache')},
            })
            providers.append(provider)
            assert provider.validate() == []
            try:
                answer = provider.fetch(EvalCase('one', 'One', base + route, kind, [],
                                                 task_kind=kind), 20)
            except (RuntimeError, TimeoutError) as error:
                raise AssertionError({
                    'worker': worker, 'diagnostics': getattr(error, 'public_metadata', {}),
                }) from error
            assert answer.ok, answer
            if kind == 'file':
                assert [item['data'] for item in answer.files] == [payload]
            else:
                assert 'Local worker fixture' in answer.content
            failure = provider.fetch(EvalCase('missing', 'Missing', base + '/missing',
                                              kind, [], task_kind=kind), 20)
            assert not failure.ok and failure.error_type
        assert requests == ['/article', '/missing', '/download.csv', '/missing']
    finally:
        for provider in providers:
            provider.__del__()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def verify_aggregate_finalization(root, source_root):
    from harness.web_fetch.evidence import finalize_aggregate

    fixture = runpy.run_path(str(source_root / 'tests/test_finalize_benchmark_run.py'))
    valid = root / 'finalize-valid'
    worker, observations, _, relative = fixture['retained_run'](valid)
    (worker / 'reference-checks/private-notes.txt').write_text('private diagnostic')
    finalize_aggregate(valid, observations)
    finalize_aggregate(valid, observations)
    assert (valid / relative).read_bytes() == observations.read_bytes()
    assert not (valid / 'reference-checks/private-notes.txt').exists()
    invalid = root / 'finalize-corrupt'
    worker, observations, _, relative = fixture['retained_run'](invalid)
    (worker / relative).write_text('corrupted independent observation')
    before = (invalid / 'execution.json').read_bytes()
    try:
        finalize_aggregate(invalid, observations)
    except ValueError:
        pass
    else:
        raise AssertionError('Installed finalizer accepted corrupt reference evidence')
    assert (invalid / 'execution.json').read_bytes() == before


def verify_sample_case_correction(root, source_root):
    from harness.web_fetch import webmainbench_case_correction as correction

    fixture = runpy.run_path(str(source_root / 'tests/webmainbench_case_fixture.py'))
    retained = fixture['correction_fixture'](root / 'case-revision', providers=('first', 'new-engine'))
    source, bindings, output, case, _, dataset, manifest, profile = retained
    # Only the synthetic source hash is substituted; all production validation runs.
    command = [sys.executable, '-c',
               'import sys; from pathlib import Path; '
               'from harness.web_fetch import webmainbench_case_correction as c; '
               'c.DATASET_SHA256=c.digest(Path(sys.argv.pop(1))); c.main()', str(dataset),
               '--source-root', str(source), '--output', str(output), '--case-id', case,
               '--dataset', str(dataset), '--sample-manifest', str(manifest), '--profile', str(profile)]
    for provider, path in bindings.items():
        command += ['--correction', f'{provider}={path}']
    subprocess.run(command, check=True, capture_output=True, text=True, cwd=root)
    for provider in ('first', 'new-engine'):
        shard = output / provider
        for call in correction.read_jsonl(shard / 'physical-attempts.jsonl'):
            assert correction.digest(shard / call['physical_artifact_path']) == call['output_sha256']
    assert not list(output.rglob('private-worker.txt'))
    assert not list(output.rglob('unbound.txt'))
    marker = (output / 'fixture-correction.json').read_bytes()
    failed = subprocess.run(command, capture_output=True, text=True, cwd=root)
    assert failed.returncode != 0
    assert (output / 'fixture-correction.json').read_bytes() == marker


def verify_full_cohort_commands(root, source_root):
    from unittest.mock import patch

    from harness.web_fetch import webmainbench_certification as certification
    from harness.web_fetch.webmainbench_scoring import CORE_METRICS, aggregate

    fixture = runpy.run_path(str(source_root / 'tests/webmainbench_case_fixture.py'))
    work, profile, records = fixture['certification_fixture'](root / 'full-certification')
    original = subprocess.run
    calls = []

    def synthetic_child(command, **kwargs):
        if '--provider' in command:
            assert command[1:3] == ['-m', 'harness.web_fetch.webmainbench_runner']
            provider = command[command.index('--provider') + 1]
            shard = Path(command[command.index('--output') + 1])
            fixture['measured_shard'](shard, provider, records, profile)
        elif '--evidence' in command:
            assert command[1:3] == ['-m', 'harness.web_fetch.webmainbench_scoring']
            shard = Path(command[command.index('--evidence') + 1])
            measured = certification.read_jsonl(shard / 'results.jsonl')
            scored = [{**row, 'metrics': {name: {'score': 0.5, 'success': True} for name in CORE_METRICS}}
                      for row in measured]
            fixture['write_rows'](shard / 'scored-results.jsonl', scored)
            fixture['write_json'](shard / 'score-summary.json', {
                'provider': shard.name, 'case_count': len(scored), 'official_aggregate': aggregate(scored),
                'scorer': {'commit': profile['official_scorer_commit'], 'metric_config': profile['metric_config'],
                           'metric_source_sha256': certification.scorer_source_binding(work / 'scorer')}})
        else:
            return original(command, **kwargs)
        calls.append(command)
        assert kwargs.get('cwd', work) == work
        return subprocess.CompletedProcess(command, 0)

    previous = Path.cwd()
    try:
        os.chdir(work)
        with patch.object(subprocess, 'run', synthetic_child):
            certification.main(['--source', 'source.jsonl', '--scorer', 'scorer', '--profile', 'profile.json', '--output-root', 'run'])
            certification.main_score(['--source', 'source.jsonl', '--scorer', 'scorer', '--profile', 'profile.json', '--cohort', 'run'])
        assert len(calls) == 4
        assert (work / 'run/scoring-certificate.json').is_file()
        for module in ('webmainbench_runner', 'webmainbench_scoring', 'webmainbench_certification'):
            original([sys.executable, '-m', 'harness.web_fetch.' + module, '--help'], check=True,
                     capture_output=True, text=True, cwd=work)
        original([sys.executable, '-m', 'harness.web_fetch.webmainbench_certification', 'score', '--help'],
                 check=True, capture_output=True, text=True, cwd=work)
    finally:
        os.chdir(previous)


def verify_certified_cohort(root):
    """Exercise all installed certification stages with independent local HTTP evidence."""
    import http.server
    import threading

    from harness.web_fetch.reference_lifecycle import require_verified_reference_checks
    from harness.web_fetch.source_capture import extract_html

    text = " ".join(f"Independent fact {index} describes a stable public observation."
                    for index in range(40))
    body = ("<html><head><title>Certification fixture</title></head><body><main>"
            + text + "</main></body></html>").encode()
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        stage = root / 'certified-cohort'
        stage.mkdir()
        url = f'http://127.0.0.1:{server.server_port}/source'
        reference = extract_html(body, url)[1]
        case = {'id': 'local-page', 'name': 'Independent local page', 'url': url,
                'category': 'web', 'track': 'web', 'task_kind': 'body',
                'expected_any': ['Independent fact 0'],
                'ground_truth_main_content': reference,
                'source_verification': {'reference_mode': 'live_exact', 'method': 'independent HTTP capture'}}
        (stage / 'tasks.json').write_text(json.dumps({'schema_version': 1,
            'id': 'certification-control', 'name': 'Certification control', 'tool': 'web_fetch',
            'cases': [case], 'coverage_plan': {'web': ['fixture']}}))
        (stage / 'methods.json').write_text(json.dumps({'providers': {
            'fixture': {'type': 'fixture'}}}))
        (stage / 'empty.env').write_text('')
        subprocess.run([sys.executable, '-c',
            'from harness.web_fetch.certification import main; main()',
            '--dataset', 'tasks.json', '--provider-config', 'methods.json',
            '--env-file', 'empty.env', '--output', 'run', '--work-root', 'captures',
            '--providers', 'fixture', '--case-ids', 'local-page', '--workers', '1',
            '--capture-workers', '1'], cwd=stage, check=True, capture_output=True, text=True)
        output = stage / 'run'
        records = [json.loads(line) for line in (output / 'results.jsonl').read_text().splitlines()]
        dataset = json.loads((output / 'dataset.json').read_text())
        batches = json.loads((output / 'execution.json').read_text())['batches']
        assert len(records) == 1 and records[0]['provider'] == 'fixture'
        require_verified_reference_checks(batches, cases=dataset, records=records, source=output)
        assert requests == ['/source', '/source']
        assert (stage / 'captures/pre-captures/audit.json').is_file()
        assert (stage / 'captures/post-captures/audit.json').is_file()
        assert dataset['cases'][0]['ground_truth_main_content'] == reference
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node-html-markdown-path', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--webmainbench', action='store_true',
                        help='Also exercise TEDS writing with the webmainbench-eval extra installed')
    parser.add_argument('--oss', action='store_true',
                        help='Exercise installed OSS libraries against local HTTP fixtures')
    parser.add_argument('--reference-audit', action='store_true',
                        help='Exercise installed certification against independent local captures')
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
    from importlib import import_module
    for package_name in ('lexbench_webfetch', 'lexbench_websearch'):
        package_root = Path(import_module(package_name).__file__).resolve().parent
        if package_root.is_relative_to(args.source_root.resolve()):
            raise ValueError('Package smoke imported source instead of installed ' + package_name)
        verify_package_tree(args.source_root, package_root, package_name=package_name)
    verify_dependency_metadata(args.source_root)
    verify_base_dataset_selection()
    verify_lexbench_input_ownership(args.source_root)
    from harness.web_fetch.webmainbench_correction import CONTROL_IDS, regression_registry_path

    REGISTRY = regression_registry_path()
    assert not REGISTRY.resolve().is_relative_to(args.source_root.resolve())
    assert {row['id'] for row in json.loads(REGISTRY.read_bytes())['cases']} == set(CONTROL_IDS)
    for command in ('correct_webmainbench_scoring.py', 'rescore_webmainbench_case_correction.py'):
        subprocess.run([sys.executable, str(args.source_root / 'scripts' / command), '--help'],
                       check=True, stdout=subprocess.DEVNULL)
    from harness.web_fetch.markdown import (
        CONVERTER,
        converter_command,
        require_converter,
    )

    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        if args.reference_audit:
            verify_certified_cohort(root)
        verify_aggregate_finalization(root, args.source_root)
        verify_sample_case_correction(root, args.source_root)
        verify_full_cohort_commands(root, args.source_root)
        verify_cowagent_isolation(root)
        verify_public_trajectory_publication(root, args.source_root)
        verify_oss_workers(root, args.source_root, with_libraries=args.oss)
        verify_browser_report_checks(root, args.source_root)
        verify_release_check_helpers(root, args.source_root)
        verify_teds_revision(root, args.source_root, with_writer=args.webmainbench)
        verify_lexbench_package(root, args.source_root / 'scripts/moli_report/app.js')
        verify_dynamic_cohort(root, args.source_root)
        verify_review_revisions(root, args.source_root)
        verify_sample_assembly(root, args.source_root)
        verify_qualification_commands(root, args.source_root)
        verify_replay_diagnostics(root, args.source_root)
        verify_environment_preparation(root, args.source_root)
        verify_replay_supersession(root, args.source_root)
        verify_repair_commands(root, args.source_root)
        verify_adjudication_commands(root, args.source_root)
        verify_browser_observation_capture(root, args.source_root)
        verify_reference_promotion(root, args.source_root)
        verify_wcxb_scoring(root, args.source_root)
        verify_wcxb_rendering(root, args.source_root)
        verify_wcxb_boundaries(root, args.source_root)
        verify_report_acceptance(root, args.source_root)
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
            'id': 'one', 'name': 'One', 'url': 'https://1.1.1.1/',
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
        from harness.web_fetch.rescoring import rescore
        from lexbench_webfetch.scoring_regressions import default_registry_path, load_scoring_regressions
        from harness.web_fetch.scoring_revision import revise
        assert not default_registry_path().resolve().is_relative_to(args.source_root.resolve())
        assert load_scoring_regressions()
        for module in ('rescoring', 'scoring_revision', 'reference_revision'):
            subprocess.run([sys.executable, '-m', 'harness.web_fetch.' + module, '--help'],
                           check=True, cwd=root, capture_output=True, text=True)
        for script in ('rescore_content.py', 'revise_body_scoring.py', 'revise_reference_evidence.py',
                       'merge_result_review_revision.py', 'rebase_blind_result_review.py'):
            subprocess.run([sys.executable, str(args.source_root / 'scripts' / script), '--help'],
                           check=True, cwd=root, capture_output=True, text=True)
        rescore(root / 'cohort')
        receipt = revise(
            root / 'cohort', root / 'cohort/dataset.json',
            root_cause_category='answer-surface',
            root_cause='Synthetic installation control preserves the complete answer scoring surface.',
            regression_ids=['body-only-word-boundary-answer-title-supplies-required-fact'],
        )
        assert receipt['provider_calls'] == 0 and receipt['task_decision_changes'] == []
        revised = json.loads((root / 'cohort/results.jsonl').read_text())
        assert revised['metrics'] == cohort[0]['metrics']
        assert revised['attempt_id'] == cohort[0]['attempt_id']
        cohort = [revised]
        from harness.web_fetch.reference_revision import revise as revise_reference
        canonical_reference = root / 'canonical-reference.json'
        document = json.loads((root / 'cohort/dataset.json').read_bytes())
        document['cases'][0]['ground_truth_evidence'] = {'kind': 'synthetic_installation_control'}
        canonical_reference.write_text(json.dumps(document))
        before_reference = (root / 'cohort/results.jsonl').read_bytes()
        reference_receipt = revise_reference(
            root / 'cohort', canonical_reference,
            root_cause_category='reference-evidence',
            root_cause='Independent installation control records corrected reference attribution.',
            regression_ids=['reference-scope-retains-main-content'],
        )
        assert reference_receipt['provider_calls'] == 0
        assert reference_receipt['task_decision_changes'] == []
        assert (root / 'cohort/results.jsonl').read_bytes() == before_reference
        predecessor, = root.glob('.cohort-reference-history-*/original')
        assert (predecessor / 'results.jsonl').read_bytes() == before_reference
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
    print('Installed wheel: exact source inventory, native worker, scoring, review revisions, '
          'evidence assembly, Lexbench packaging and HTML conversion passed')


if __name__ == '__main__':
    main()
