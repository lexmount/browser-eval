"""Reviewed sample assembly uses the declared population and retains every physical call."""
import json

import pytest

from harness.web_fetch import webmainbench_assembly as assembly
from harness.web_fetch.webmainbench_diagnostics import sample_review_main
from harness.web_fetch.webmainbench_evidence import digest, read_jsonl, scored_shard_bindings
from tests.webmainbench_sample_fixture import sample_fixture, write_json, write_rows


def assemble(fixture):
    return assembly.assemble(fixture['root'], fixture['output'], fixture['source'], fixture['profile'],
                             sample_manifest=fixture['manifest'], review_path=fixture['review'])


@pytest.mark.parametrize('providers,size', [(('one',), 1), (('first', 'new-engine', 'third'), 2)])
def test_declared_sample_preserves_all_bound_results_calls_and_source(tmp_path, providers, size):
    fixture = sample_fixture(tmp_path, providers=providers, size=size)
    bindings = scored_shard_bindings(fixture['root'], list(providers))
    assemble(fixture)
    output = fixture['output']
    dataset = json.loads((output / 'dataset.json').read_bytes())
    assert dataset['coverage_plan']['static_html'] == list(providers)
    assert len(dataset['cases']) == size
    assert len(read_jsonl(output / 'results.jsonl')) == size * len(providers)
    assert len(read_jsonl(output / 'physical-attempts.jsonl')) == (size + 1) * len(providers)
    for provider in providers:
        shard = fixture['root'] / provider
        expected = read_jsonl(shard / 'scored-results.jsonl')
        actual = [row for row in read_jsonl(output / 'results.jsonl') if row['provider'] == provider]
        assert actual == [{**row, 'case_id': row['track_id']} for row in expected]
    for row in read_jsonl(output / 'results.jsonl') + read_jsonl(output / 'physical-attempts.jsonl'):
        relative = row.get('physical_artifact_path', row.get('artifact_path'))
        assert digest(output / relative) == row['output_sha256']
    assert not list(output.rglob('private-notes.txt'))
    assert scored_shard_bindings(fixture['root'], list(providers)) == bindings
    review = json.loads((output / 'result-review.json').read_bytes())
    assert review['bindings']['results_jsonl_sha256'] == digest(output / 'results.jsonl')


@pytest.mark.parametrize('damage', ['review-bindings', 'review-provider', 'review-check', 'profile', 'source',
                                    'logical', 'physical', 'artifact', 'symlink', 'dangling-output'])
def test_invalid_inputs_leave_no_partial_assembly(tmp_path, damage):
    fixture = sample_fixture(tmp_path)
    shard = fixture['root'] / 'engine-a'
    if damage.startswith('review-'):
        review = json.loads(fixture['review'].read_bytes())
        if damage == 'review-bindings':
            review.pop('bindings')
        elif damage == 'review-provider':
            review['providers'] = ['engine-a']
        else:
            review['checks'][0]['status'] = 'failed'
        write_json(fixture['review'], review)
    elif damage == 'profile':
        profile = json.loads(fixture['profile'].read_bytes())
        profile['timeout_seconds'] = 900
        write_json(fixture['profile'], profile)
    elif damage == 'source':
        fixture['source'].write_text('{}\n')
    elif damage in {'logical', 'physical'}:
        name = 'scored-results.jsonl' if damage == 'logical' else 'physical-attempts.jsonl'
        rows = read_jsonl(shard / name)
        write_rows(shard / name, rows[:-1])
    elif damage == 'artifact':
        (shard / 'artifacts/engine-a/case-0.md').write_text('corrupt')
    elif damage == 'symlink':
        path = shard / 'artifacts/engine-a/case-0.md'
        path.rename(path.with_suffix('.saved'))
        path.symlink_to(path.with_suffix('.saved'))
    else:
        fixture['output'].symlink_to(tmp_path / 'absent', target_is_directory=True)
    with pytest.raises((ValueError, KeyError)):
        assemble(fixture)
    assert not fixture['output'].exists()
    assert not list(tmp_path.glob('.webmainbench-assemble-*'))


def test_changed_inputs_during_materialization_do_not_commit(tmp_path, monkeypatch):
    fixture = sample_fixture(tmp_path)
    collect = assembly._assemble_cohort

    def changed_after_collect(*args, **kwargs):
        collect(*args, **kwargs)
        fixture['review'].write_text('{}')

    monkeypatch.setattr(assembly, '_assemble_cohort', changed_after_collect)
    with pytest.raises(ValueError, match='changed during aggregation'):
        assemble(fixture)
    assert not fixture['output'].exists()


def test_audit_binds_actual_evidence_and_unknown_provider_names(tmp_path):
    fixture = sample_fixture(tmp_path, providers=('custom-engine',))
    output = tmp_path / 'new-review.json'
    sample_review_main(['--source-root', str(fixture['root']), '--dataset', str(fixture['source']),
                        '--sample-manifest', str(fixture['manifest']), '--profile', str(fixture['profile']),
                        '--output', str(output)])
    review = json.loads(output.read_bytes())
    assert review['status'] == 'passed'
    assert review['bindings']['provider_files_sha256'] == scored_shard_bindings(fixture['root'], ['custom-engine'])
    fixture['review'] = output
    assemble(fixture)


@pytest.mark.parametrize('budget,calls,accepted', [
    (0, 1, True), (0, 2, False), (1, 2, True), (1, 3, False),
    (3, 3, True), (3, 4, True), (3, 5, False),
])
def test_audit_and_assembly_enforce_the_declared_retry_budget(tmp_path, budget, calls, accepted):
    fixture = sample_fixture(tmp_path, providers=('custom',), size=1,
                             max_failure_retries=budget, recovery_call=calls)
    fixture['review'] = tmp_path / 'actual-review.json'
    arguments = ['--source-root', str(fixture['root']), '--dataset', str(fixture['source']),
                 '--sample-manifest', str(fixture['manifest']), '--profile', str(fixture['profile']),
                 '--output', str(fixture['review'])]
    if accepted:
        sample_review_main(arguments)
        assemble(fixture)
        assert len(read_jsonl(fixture['output'] / 'results.jsonl')) == 1
        assert len(read_jsonl(fixture['output'] / 'physical-attempts.jsonl')) == calls
    else:
        with pytest.raises(SystemExit):
            sample_review_main(arguments)
        review = json.loads(fixture['review'].read_text())
        assert review['status'] == 'failed'
        assert 'custom/case-0: final retry selection' in review['failures']
        with pytest.raises(ValueError):
            assemble(fixture)
        assert not fixture['output'].exists()


def test_reference_inapplicability_is_excluded_but_applicable_metric_failure_is_zero(tmp_path):
    fixture = sample_fixture(tmp_path, providers=('custom',))
    shard = fixture['root'] / 'custom'
    rows = read_jsonl(shard / 'scored-results.jsonl')
    for row in rows:
        row['metrics']['code_edit']['details'] = {'availability': 'reference_not_applicable'}
    write_rows(shard / 'scored-results.jsonl', rows)
    from harness.web_fetch.webmainbench_scoring import aggregate
    summary = json.loads((shard / 'score-summary.json').read_bytes())
    summary['official_aggregate'] = aggregate(rows)
    write_json(shard / 'score-summary.json', summary)
    output = tmp_path / 'new-review.json'
    sample_review_main(['--source-root', str(fixture['root']), '--dataset', str(fixture['source']),
                        '--sample-manifest', str(fixture['manifest']), '--profile', str(fixture['profile']),
                        '--output', str(output)])
    assert json.loads(output.read_bytes())['status'] == 'passed'
    assert summary['official_aggregate']['denominators']['code_edit'] == 0
    assert summary['official_aggregate']['denominators']['table_TEDS'] == 2
    assert summary['official_aggregate']['scores']['table_TEDS'] == 0


def test_audit_does_not_write_decision_for_inputs_changed_mid_audit(tmp_path, monkeypatch):
    from harness.web_fetch import webmainbench_diagnostics as diagnostics
    fixture = sample_fixture(tmp_path, providers=('custom',))
    output = tmp_path / 'new-review.json'
    original = diagnostics.normalized_edit

    def changed_after_read(predicted, reference):
        (fixture['root'] / 'custom/artifacts/custom/case-0.md').write_text('changed during audit')
        return original(predicted, reference)

    monkeypatch.setattr(diagnostics, 'normalized_edit', changed_after_read)
    with pytest.raises(ValueError, match='inputs changed'):
        sample_review_main(['--source-root', str(fixture['root']), '--dataset', str(fixture['source']),
                            '--sample-manifest', str(fixture['manifest']), '--profile', str(fixture['profile']),
                            '--output', str(output)])
    assert not output.exists()


def test_single_shard_audit_preserves_arbitrary_input_directory_contract(tmp_path):
    fixture = sample_fixture(tmp_path, providers=('custom',))
    shard = fixture['root'] / 'custom'
    moved = tmp_path / 'worker-output'
    shard.rename(moved)
    output = tmp_path / 'single-review.json'
    sample_review_main(['--source-root', str(fixture['root']), '--single-shard', str(moved),
                        '--providers', 'custom', '--dataset', str(fixture['source']),
                        '--sample-manifest', str(fixture['manifest']), '--profile', str(fixture['profile']),
                        '--output', str(output)])
    review = json.loads(output.read_bytes())
    assert review['status'] == 'passed'
    assert all(path.startswith('custom/') for path in review['bindings']['provider_files_sha256'])
