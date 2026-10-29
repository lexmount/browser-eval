"""Migration defaults preserve explicit editions, selection identities and cache roots."""
import json

import pytest

from harness.benchmarks import data
from harness.web_fetch import webmainbench_runner as runner, webmainbench_scoring as wmb


def test_explicit_source_without_authority_never_fetches_default_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(data, 'definition_path', lambda *args: pytest.fail('Explicit sources must not fetch defaults'))
    with pytest.raises(ValueError, match='manifest'):
        runner.selected_wcxb_input(tmp_path / 'historical/source')


def test_explicit_source_uses_profile_bound_manifest_before_conflicting_neighbor(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    (tmp_path / 'tasks.json').write_text('conflicting edition')
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'sample_manifest': 'evaluation/historical.json'}))
    monkeypatch.setattr(runner, 'ROOT', tmp_path / 'repo')
    monkeypatch.setattr(data, 'definition_path', lambda *args: pytest.fail('Explicit sources must not fetch defaults'))
    assert runner.selected_wcxb_input(source, profile=profile) == (source, tmp_path / 'repo/evaluation/historical.json')


def test_explicit_source_retains_adjacent_authority_without_profile(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    manifest = tmp_path / 'tasks.json'
    manifest.write_text('{}')
    monkeypatch.setattr(data, 'definition_path', lambda *args: pytest.fail('Explicit sources must not fetch defaults'))
    assert runner.selected_wcxb_input(source) == (source, manifest)


def test_absolute_profile_manifest_and_explicit_override_are_preserved(tmp_path):
    profile = tmp_path / 'profile.json'
    selected = tmp_path / 'external/old.json'
    profile.write_text(json.dumps({'sample_manifest': str(selected)}))
    source = tmp_path / 'source'
    assert runner.selected_wcxb_input(source, profile=profile) == (source, selected)
    override = tmp_path / 'other.json'
    assert runner.selected_wcxb_input(source, override, profile=profile) == (source, override)


def test_official_scorer_stays_inside_custom_dataset_cache(tmp_path, monkeypatch):
    cache = tmp_path / 'controlled/cache'
    revision = 'a' * 40
    expected = cache / 'benchmark-code/webmainbench' / revision
    expected.mkdir(parents=True)
    monkeypatch.setenv('BROWSER_EVAL_DATASET_CACHE', str(cache))
    monkeypatch.setattr(data, 'binding', lambda track: {'scorer': {'revision': revision}})
    checked = []
    monkeypatch.setattr(wmb, 'verify_scorer_checkout', lambda path, commit: checked.append((path, commit)))
    monkeypatch.setattr(wmb.subprocess, 'run', lambda *args, **kwargs: pytest.fail('Existing cache must not clone'))
    assert wmb.prepare_scorer() == expected
    assert checked == [(expected, revision)]
    assert not (cache.parent / 'benchmark-code').exists()


def test_source_only_scoring_cli_resolves_its_historical_profile(tmp_path, monkeypatch):
    import sys
    from harness.web_fetch import wcxb_scoring
    source, manifest = tmp_path / 'source', tmp_path / 'old.json'
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'sample_manifest': str(manifest)}))
    captured = {}
    monkeypatch.setattr(data, 'definition_path', lambda *args: pytest.fail('Historical CLI must not select HF defaults'))
    monkeypatch.setattr(wcxb_scoring, 'score', lambda **options: captured.update(options))
    monkeypatch.setattr(sys, 'argv', ['score_wcxb_results.py', '--source', str(source), '--profile', str(profile),
                                    '--evidence-root', str(tmp_path / 'evidence')])
    wcxb_scoring.main()
    assert captured['source'] == source and captured['sample_manifest'] == manifest


def test_source_only_certified_cli_resolves_profile_before_any_provider_work(tmp_path, monkeypatch):
    from harness.web_fetch import certification
    source, manifest = tmp_path / 'source', tmp_path / 'old.json'
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'sample_manifest': str(manifest)}))
    def stop_before_execution(dataset, **options):
        assert dataset == source and options['manifest'] == manifest
        raise RuntimeError('historical selection reached')
    monkeypatch.setattr(data, 'definition_path', lambda *args: pytest.fail('Historical CLI must not select HF defaults'))
    monkeypatch.setattr(certification, 'load_wcxb_source', stop_before_execution)
    with pytest.raises(RuntimeError, match='historical selection reached'):
        certification.run_wcxb(['--dataset', str(source), '--profile', str(profile),
                                '--output-root', str(tmp_path / 'new-output')])


def test_source_only_latency_cli_resolves_profile_manifest(tmp_path, monkeypatch):
    from harness.web_fetch import wcxb_latency
    source, manifest = tmp_path / 'source', tmp_path / 'old.json'
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'sample_manifest': str(manifest)}))
    captured = {}
    monkeypatch.setattr(data, 'definition_path', lambda *args: pytest.fail('Historical CLI must not select HF defaults'))
    monkeypatch.setattr(wcxb_latency, 'build_provider', lambda *args: object())
    monkeypatch.setattr(wcxb_latency, 'collect', lambda **options: captured.update(options) or {})
    wcxb_latency.main(['--dataset', str(source), '--profile', str(profile), '--output', str(tmp_path / 'timing'),
                       '--release', str(tmp_path / 'release'), '--candidate', str(tmp_path / 'candidate')])
    assert captured['dataset'] == source and captured['manifest'] == manifest
