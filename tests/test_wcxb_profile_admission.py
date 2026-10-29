"""Profile-bound WCXB selections and metric results stay authoritative at admission."""
import json
from pathlib import Path

import pytest

from harness.benchmarks import data
from harness.web_fetch import report_build, wcxb_scoring, webmainbench_runner as runner, webmainbench_scoring as wmb
from tests.test_wcxb_selected_edition import corpus
from tests.wcxb_fixture import create_cohort


class AdmissionPassed(RuntimeError):
    pass


@pytest.mark.parametrize('mismatch', ['summary', 'certificate', 'missing-new-summary'])
def test_report_builder_rejects_metric_binding_before_creating_output(tmp_path, monkeypatch, mismatch):
    options = create_cohort(tmp_path / 'cohort')
    wcxb_scoring.score(**options)
    profile = json.loads(options['profile_path'].read_bytes())
    name = 'certification.json' if mismatch == 'certificate' else 'wcxb-score-summary.json'
    path = options['evidence_root'] / name
    value = json.loads(path.read_bytes())
    if mismatch == 'missing-new-summary':
        profile['metric_implementation_sha256'] = value.pop('metric_implementation_sha256')
    else:
        value['metric_implementation_sha256'] = '0' * 64
    path.write_text(json.dumps(value))
    monkeypatch.setattr(report_build, 'load_bound_content', lambda *args, **kwargs: {})
    monkeypatch.setattr(report_build, 'load_jsonl', lambda *args, **kwargs: (_ for _ in ()).throw(AdmissionPassed()))
    output = tmp_path / 'report'
    with pytest.raises(ValueError, match='metric implementation'):
        report_build.build_wcxb_report(options['evidence_root'], options['source'], options['sample_manifest'],
                                       profile, output, content_binding={}, authority_root=tmp_path)
    assert not output.exists()


def test_report_builder_keeps_known_legacy_metric_admission(tmp_path, monkeypatch):
    options = create_cohort(tmp_path / 'cohort')
    wcxb_scoring.score(**options)
    profile = json.loads(options['profile_path'].read_bytes())
    summary = options['evidence_root'] / 'wcxb-score-summary.json'
    value = json.loads(summary.read_bytes())
    value.pop('metric_implementation_sha256')
    summary.write_text(json.dumps(value))
    monkeypatch.setattr(report_build, 'load_bound_content', lambda *args, **kwargs: {})
    monkeypatch.setattr(report_build, 'load_jsonl', lambda *args, **kwargs: (_ for _ in ()).throw(AdmissionPassed()))
    with pytest.raises(AdmissionPassed):
        report_build.build_wcxb_report(options['evidence_root'], options['source'], options['sample_manifest'],
                                       profile, tmp_path / 'report', content_binding={}, authority_root=tmp_path)


def profile_selection(tmp_path, monkeypatch, *, legacy=False):
    locked = tmp_path / 'locked/tasks.json'
    locked.parent.mkdir()
    locked.write_text(json.dumps({'source_sha256': 'a' * 64}))
    selected = tmp_path / 'selected.json'
    selected.write_text(json.dumps({'source_sha256': ('b' if legacy else 'a') * 64}))
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'sample_manifest': str(selected)}))
    monkeypatch.setattr(data, 'definition_path', lambda track, relative: locked.parent / relative)
    return locked.parent / 'source', selected, profile


def test_profile_subset_overrides_default_manifest_even_without_source(tmp_path, monkeypatch):
    source, selected, profile = profile_selection(tmp_path, monkeypatch)
    assert runner.selected_wcxb_input(profile=profile) == (source, selected)


def test_other_profile_source_edition_requires_explicit_source(tmp_path, monkeypatch):
    _, selected, profile = profile_selection(tmp_path, monkeypatch, legacy=True)
    with pytest.raises(ValueError, match='source'):
        runner.selected_wcxb_input(profile=profile)
    explicit = tmp_path / 'historical/source'
    assert runner.selected_wcxb_input(explicit, profile=profile) == (explicit, selected)


@pytest.mark.parametrize('defect', [None, 'missing', 'extra', 'reference', 'hash'])
def test_qualified_v2_selection_from_full_511_source_is_source_verified(tmp_path, defect):
    source, path, manifest = corpus(tmp_path, 511, historical=True)
    manifest['schema_version'] = 2
    manifest['cases'] = [{**case, 'url': f"https://example.test/{case['file_id']}",
                          'reference': f"Independent reference {case['file_id']}", 'with': [], 'without': []}
                         for case in manifest['cases']]
    if defect == 'missing':
        manifest['cases'].pop()
    elif defect == 'extra':
        manifest['cases'].append({'file_id': '0504', 'page_type': 'article', 'url': 'https://example.test/0504',
                                  'reference': '', 'with': [], 'without': []})
    elif defect == 'reference':
        manifest['cases'][0]['reference'] = 'Wrong reference'
    elif defect == 'hash':
        manifest['source_sha256'] = '0' * 64
    path.write_text(json.dumps(manifest))
    if defect:
        with pytest.raises(ValueError):
            runner.load_wcxb_source(source, manifest=path)
    else:
        records, digest = runner.load_wcxb_source(source, manifest=path)
        assert len(records) == 511 and digest == manifest['source_sha256']


def test_invalid_managed_scorer_cache_gives_precise_recovery_without_deleting(tmp_path, monkeypatch):
    revision = 'a' * 40
    target = tmp_path / 'cache/benchmark-code/webmainbench' / revision
    target.mkdir(parents=True)
    marker = target / 'local-change.txt'
    marker.write_text('retain this change')
    monkeypatch.setenv('BROWSER_EVAL_DATASET_CACHE', str(tmp_path / 'cache'))
    monkeypatch.setattr(data, 'binding', lambda track: {'scorer': {'revision': revision}})
    monkeypatch.setattr(wmb, 'verify_scorer_checkout', lambda *args: (_ for _ in ()).throw(ValueError('dirty checkout')))
    with pytest.raises(ValueError) as error:
        wmb.prepare_scorer()
    assert str(target) in str(error.value)
    assert 'rerun' in str(error.value) and ('move' in str(error.value).lower() or 'remove' in str(error.value).lower())
    assert marker.read_text() == 'retain this change'


@pytest.mark.parametrize('entrypoint', ['runner', 'scoring', 'certification', 'latency', 'latency-certify', 'report'])
def test_all_entrypoints_honor_profile_selection_with_default_source(tmp_path, monkeypatch, entrypoint):
    import runpy
    import sys
    from harness.web_fetch import certification, wcxb_latency

    source, selected, profile_path = profile_selection(tmp_path, monkeypatch)
    profile = json.loads(profile_path.read_bytes())
    profile.update(comparison_kind='wcxb_frozen_html_to_markdown')
    profile_path.write_text(json.dumps(profile))
    captured = {}
    if entrypoint == 'runner':
        monkeypatch.setattr(runner, 'run', lambda args: captured.update(source=args.dataset, manifest=args.case_ids_file))
        monkeypatch.setattr(sys, 'argv', ['run.py', '--benchmark', 'wcxb', '--provider', runner.WCXB_PROVIDERS[0],
                                        '--profile', str(profile_path), '--output', str(tmp_path / 'output')])
        runner.main()
    elif entrypoint == 'scoring':
        monkeypatch.setattr(wcxb_scoring, 'score', lambda **args: captured.update(source=args['source'], manifest=args['sample_manifest']))
        monkeypatch.setattr(sys, 'argv', ['score.py', '--profile', str(profile_path), '--evidence-root', str(tmp_path / 'evidence')])
        wcxb_scoring.main()
    elif entrypoint in {'certification', 'latency-certify'}:
        def stop(dataset, **args):
            captured.update(source=dataset, manifest=args['manifest'])
            raise AdmissionPassed()
        if entrypoint == 'certification':
            monkeypatch.setattr(certification, 'load_wcxb_source', stop)
            with pytest.raises(AdmissionPassed):
                certification.run_wcxb(['--profile', str(profile_path), '--output-root', str(tmp_path / 'out')])
        else:
            (source / 'test').mkdir(parents=True)
            evidence = tmp_path / 'evidence'
            evidence.mkdir()
            for name in ('receipt.json', 'calls.jsonl'):
                (evidence / name).write_text('{}')
            monkeypatch.setattr(wcxb_scoring, 'participant_names', lambda value: [])
            monkeypatch.setattr(runner, 'load_wcxb_source', stop)
            with pytest.raises(AdmissionPassed):
                wcxb_latency.certify(['--profile', str(profile_path), '--evidence-root', str(evidence)])
    elif entrypoint == 'latency':
        monkeypatch.setattr(wcxb_latency, 'build_provider', lambda *args: object())
        monkeypatch.setattr(wcxb_latency, 'collect', lambda **args: captured.update(source=args['dataset'], manifest=args['manifest']) or {})
        wcxb_latency.main(['--profile', str(profile_path), '--release', str(tmp_path / 'release'),
                           '--candidate', str(tmp_path / 'candidate'), '--output', str(tmp_path / 'out')])
    else:
        (tmp_path / 'report-content-binding.json').write_text('{}')
        monkeypatch.setattr(report_build, 'build_wcxb_report', lambda evidence, corpus, sample, *args, **kwargs:
                            captured.update(source=corpus, manifest=sample) or tmp_path / 'index.html')
        script = Path(__file__).resolve().parents[1] / 'scripts/build_report.py'
        monkeypatch.setattr(sys, 'argv', [str(script), '--profile', str(profile_path), '--wcxb',
                                        '--evidence', str(tmp_path / 'evidence'), '--output', str(tmp_path / 'out')])
        with pytest.raises(SystemExit) as result:
            runpy.run_path(str(script), run_name='__main__')
        assert result.value.code == 0
    assert captured == {'source': source, 'manifest': selected}
