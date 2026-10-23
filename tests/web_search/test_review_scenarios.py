"""Collection, release and participant substitution regression scenarios."""

import json
import os
from unittest.mock import patch

import pytest

from harness.web_search.core import digest
from harness.web_search.profiles import load_profile_bytes
from harness.web_search.runner import run
from harness.web_search import report
from lexbench_websearch.inputs import definition_path
from tests.web_search.test_serp import synthetic_serp_profile


def response(url=None, body=None, header=None, timeout=20):
    return (
        {"http_status": 200, "elapsed_ms": 1, "transport_exit": 0, "transport_error": ""},
        b"reference " * 20 if body is None else b'{"results":[{"url":"https://example.org/"}]}',
    )


def test_suffix_sharing_methods_have_independent_quotas(tmp_path):
    profile = synthetic_serp_profile()
    profile["cases"] = profile["cases"][:1]
    base = profile["methods"][0]
    profile["methods"] = [{**base, "id": mid} for mid in ("variant-a", "a")]
    profile["protocol"]["max_calls_per_method"] = 1
    source = tmp_path / "profile.json"
    source.write_text(json.dumps(profile))
    output = tmp_path / "run"
    with (
        patch("harness.web_search.runner.load_runtime_env"),
        patch.dict(os.environ, {base["key_env"]: "synthetic-key"}),
        patch("harness.web_search.runner.http_call", side_effect=response),
    ):
        run(source, output, certified=True)
    from harness.web_search.runner import physical_call_counts

    assert physical_call_counts(profile, output) == {"variant-a": 1, "a": 1}
    archive = output / "resume-1/calls"
    archive.mkdir(parents=True)
    name = "fixture-0-variant-a-r0-attempt-1.json"
    (output / "calls" / name).rename(archive / name)
    reservations = archive.parent / "reservations"
    reservations.mkdir()
    (output / "reservations" / name).rename(reservations / name)
    assert physical_call_counts(profile, output) == {"variant-a": 1, "a": 1}
    (output / "calls" / name).write_text("{}")
    (output / "reservations" / name).write_bytes((reservations / name).read_bytes())
    assert physical_call_counts(profile, output) == {"variant-a": 2, "a": 1}


@pytest.mark.parametrize("tail", [b"", b'{"id":', b'{"id":"\xe4', b'{"id":\n'])
@pytest.mark.parametrize("selection", [None, "primary", "supplement"])
def test_execution_revision_resolves_composed_profiles_and_current_cohorts(tmp_path, selection, tail):
    profile = synthetic_serp_profile()
    profile["methods"] = profile["methods"][:1]
    dataset = definition_path("datasets/scenarios-v1.json")
    profile["cases"] = {
        "field": "cases",
        "benchmark_datasets": [{"id": "scenarios-v1", "sha256": digest(dataset.read_bytes())}],
    }
    compact = tmp_path / "compact.json"
    source = compact if selection is None else tmp_path / "current.json"

    def save():
        compact.write_text(json.dumps(profile))
        if selection:
            binding = {"path": compact.name, "sha256": digest(compact.read_bytes())}
            source.write_text(
                json.dumps(
                    {
                        "schema": "web-search-current/1",
                        "title": profile["title"],
                        "profile": binding,
                        "methods": [profile["methods"][0]["id"]],
                        "supplementary_profiles": [{**binding, "id": "supplement"}],
                        "publication_target": "target.json",
                    }
                )
            )

    save()
    cohort = selection or "primary"
    old_bytes = load_profile_bytes(source, cohort=cohort)
    output = tmp_path / "run"
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        if len(args) < 2 or args[1] is None:
            return response(*args, **kwargs)
        if calls:
            raise RuntimeError("Interrupted")
        calls += 1
        return response(*args, **kwargs)

    key = profile["methods"][0]["key_env"]
    with (
        patch("harness.web_search.runner.load_runtime_env"),
        patch.dict(os.environ, {key: "synthetic-key"}),
    ):
        with patch("harness.web_search.runner.http_call", side_effect=interrupted):
            with pytest.raises(RuntimeError, match="Interrupted"):
                run(source, output, certified=True, cohort=cohort)
        records = output / "results.jsonl"
        interrupted_bytes = records.read_bytes() + tail
        records.write_bytes(interrupted_bytes)
        profile["methods"][0]["endpoint"] += "/new"
        save()
        new_bytes = load_profile_bytes(source, cohort=cohort)
        proof = tmp_path / "proof.json"
        proof.write_text('{"accepted":true}')
        revision = tmp_path / "revision.json"
        revision.write_text(
            json.dumps(
                {
                    "schema": "web-search-execution-revision/1",
                    "root_cause": "Changed native endpoint",
                    "superseded_methods": [profile["methods"][0]["id"]],
                    "previous_profile_sha256": digest(old_bytes),
                    "profile_sha256": digest(new_bytes),
                    "evidence": [{"path": proof.name, "sha256": digest(proof.read_bytes())}],
                }
            )
        )
        if tail.endswith(b"\n"):
            with patch("harness.web_search.runner.http_call", side_effect=response):
                with pytest.raises(json.JSONDecodeError):
                    run(source, output, resume=True, execution_revision=revision, certified=True, cohort=cohort)
            assert records.read_bytes() == interrupted_bytes
            assert not (output / "resume-1").exists()
            return
        with patch("harness.web_search.runner.http_call", side_effect=response):
            run(
                source,
                output,
                resume=True,
                execution_revision=revision,
                certified=True,
                cohort=cohort,
            )
    assert (output / "profile.json").read_bytes() == new_bytes
    assert (output / "resume-1/previous-profile.json").read_bytes() == old_bytes
    assert (output / "resume-1/previous-results.jsonl").read_bytes() == interrupted_bytes
    frozen = json.loads(new_bytes)
    assert len((output / "results.jsonl").read_text().splitlines()) == len(frozen["cases"])
    if selection:
        assert frozen["current_selection"]["cohort"] == cohort


@pytest.mark.parametrize("zh", [False, True])
def test_catalog_prose_follows_selected_participants(zh):
    profile = {"methods": [{"id": "alpha", "label": "Alpha"}]}
    content = {
        "catalog_pricing": {
            "alpha": {
                "amount": 2,
                "currency": "USD",
                "unit": "1000-successful-searches",
                "source": "https://alpha.test/pricing",
                "checked_at": "2026-10-23",
                "terms": {"en": "Alpha terms", "zh": "Alpha terms"},
            }
        }
    }
    html = report.catalog_price_panel(profile, content, zh)
    assert "Alpha terms" in html and "Alpha" in html
    assert all(name not in html for name in ("Browserbase", "Bright Data", "Serper"))


def test_combined_release_export_keeps_the_explicit_publication_plan(tmp_path, monkeypatch):
    output, visual, plan, delivery = [
        tmp_path / name for name in ("report", "visual.json", "publication-plan.json", "delivery")
    ]
    output.mkdir()
    manifest = output / "manifest.json"
    manifest.write_text(json.dumps({"receipt": {"certified": True}}))
    visual.write_text(json.dumps({"manifest_sha256": digest(manifest.read_bytes())}))
    monkeypatch.setattr(report, "check", lambda _: None)
    with (
        patch("harness.web_search.publication.verify_plan") as verify_plan,
        patch("harness.web_search.visual.verify"),
        patch("harness.web_search.delivery.export") as export,
        patch("harness.web_search.delivery.verify") as verify,
    ):
        with pytest.raises(ValueError, match="explicit publication plan"):
            report.main(
                [
                    "--output",
                    str(output),
                    "--release-check",
                    str(visual),
                    "--export-delivery",
                    str(delivery),
                ]
            )
        export.assert_not_called()
        report.main(
            [
                "--output",
                str(output),
                "--release-check",
                str(visual),
                "--publication-plan",
                str(plan),
                "--export-delivery",
                str(delivery),
            ]
        )
    verify_plan.assert_called_once_with(output, plan.parent)
    export.assert_called_once_with(output, delivery)
    verify.assert_called_once_with(output, delivery)


@pytest.mark.parametrize("boundary", ["send", "response", "save"])
@pytest.mark.parametrize("stop_at", [1, 2])
def test_interrupted_paid_attempt_still_exhausts_budget(tmp_path, boundary, stop_at):
    from pathlib import Path
    from harness.web_search.runner import physical_call_counts

    profile = synthetic_serp_profile()
    profile['methods'] = profile['methods'][:1]
    profile['protocol']['max_calls_per_method'] = 2
    source = tmp_path / 'profile.json'
    source.write_text(json.dumps(profile))
    output = tmp_path / 'run'
    sent = 0
    original_write = Path.write_bytes

    def transport(*args, **kwargs):
        nonlocal sent
        if len(args) > 1 and args[1] is not None:
            sent += 1
            if sent == stop_at and boundary == 'send':
                raise KeyboardInterrupt(boundary)
        return response(*args, **kwargs)

    from harness.web_search.runner import normalize_response

    def normalize(*args, **kwargs):
        if boundary == 'response' and sent == stop_at:
            raise KeyboardInterrupt('response saved; logical record pending')
        return normalize_response(*args, **kwargs)

    def save(path, data):
        if boundary == 'save' and sent == stop_at and path.parent.name == 'calls':
            raise KeyboardInterrupt('save')
        return original_write(path, data)

    with (
        patch('harness.web_search.runner.load_runtime_env'),
        patch.dict(os.environ, {profile['methods'][0]['key_env']: 'synthetic-key'}),
        patch('harness.web_search.runner.http_call', side_effect=transport),
        patch('harness.web_search.runner.normalize_response', side_effect=normalize),
    ):
        with patch.object(Path, 'write_bytes', save), pytest.raises(KeyboardInterrupt):
            run(source, output, certified=True)
        with pytest.raises(ValueError, match='budget exhausted'):
            run(source, output, certified=True, resume=True)
    assert sent == 2
    assert physical_call_counts(profile, output) == {profile['methods'][0]['id']: 2}
    assert list(output.glob('resume-*/reservations/*.json'))
    assert not (output / 'receipt.json').exists()


@pytest.mark.parametrize('cutoff', [5, 10])
@pytest.mark.parametrize('position', ['first', 'last', 'excluded'])
def test_reference_panels_respect_declared_cutoff(cutoff, position):
    from bs4 import BeautifulSoup
    from lexbench_websearch.scoring import evaluate

    rank = {'first': 1, 'last': cutoff, 'excluded': cutoff + 1}[position]
    case = {'id': 'query', 'anchors': ['https://reference.test/']}
    results = [{'url': f'https://other.test/{i}'} for i in range(cutoff + 1)]
    results[rank - 1] = {'url': case['anchors'][0]}
    profile = {'cases': [case], 'methods': [{'id': 'a', 'label': 'A', 'currency': 'USD'}], 'protocol': {'top_k': cutoff}}
    row = {'case_id': 'query', 'method': 'a', 'results': results, 'metrics': evaluate(case, results),
           'http_status': 200, 'error': '', 'elapsed_ms': 1, 'cost': 0}
    original = dict(row['metrics'])
    doc = BeautifulSoup(report.reference_retrieval_panel(profile, [row], ['query'], False), 'html.parser')
    expected = 0 if rank > cutoff else 1 / rank
    assert float(doc.select_one('[data-metric="reference-mrr"]')['data-value']) == pytest.approx(expected)
    content = {'metric_populations': [
        {'id': 'overall', 'label': {'en': 'All', 'zh': 'All'}, 'case_ids': ['query']},
        {'id': 'scenario', 'label': {'en': 'Scenarios', 'zh': 'Scenarios'}, 'case_ids': ['query']},
    ]}
    summary = BeautifulSoup(report.population_summary(profile, [row], content, False), 'html.parser')
    cells = summary.select('[data-population="scenario"] tbody tr td')
    assert float(cells[2]['data-value']) == pytest.approx(expected)
    assert row['metrics'] == original


def test_supplementary_quarantines_remain_in_composed_quality_population(tmp_path):
    from harness.web_search.example import synthetic_profile

    profiles = []
    sources = []
    for index in range(2):
        profile = synthetic_profile()
        case = {**profile['cases'][0], 'id': f'q{index}', 'reference_answer': 'answer'}
        profile.update(cases=[case], factual_quality_exclusions=[case['id']],
                       reference_adjudications=[{'case_id': case['id'], 'reason': 'qualified defect'}])
        profiles.append(profile)
        source = tmp_path / str(index)
        source.mkdir()
        for name in ('profile.json', 'results.jsonl', 'receipt.json'):
            (source / name).write_text('{}')
        sources.append(source)
    with patch.object(report, 'load_run', side_effect=[(p, [], {'certified': True}) for p in profiles]):
        combined, _, _, _ = report.load_report_runs(sources[0], [('extra', sources[1])], {})
    assert set(combined['factual_quality_exclusions']) == {'q0', 'q1'}
    assert combined['reference_adjudications'] == [p['reference_adjudications'][0] for p in profiles]


def test_repeated_factual_collection_builds_and_checks_selected_score_round(tmp_path):
    from harness.web_search.example import example
    from lexbench_websearch.scoring import evaluate

    example(tmp_path)
    source = tmp_path / 'fixture'
    profile = json.loads((source / 'profile.json').read_bytes())
    profile['protocol']['rounds'] = 2
    for case in profile['cases']:
        case['reference_answer'] = 'Fixture'
    (source / 'profile.json').write_text(json.dumps(profile))
    rows = [json.loads(line) for line in (source / 'results.jsonl').read_text().splitlines()]
    for row in rows:
        case = next(c for c in profile['cases'] if c['id'] == row['case_id'])
        row['metrics'] = evaluate(case, row['results'])
    rows += [dict(row, id=row['id'].removesuffix('r0') + 'r1', round=1) for row in rows]
    (source / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    receipt = json.loads((source / 'receipt.json').read_bytes())
    receipt.update(call_count=len(rows), profile_sha256=digest((source / 'profile.json').read_bytes()),
                   results_sha256=digest((source / 'results.jsonl').read_bytes()))
    (source / 'receipt.json').write_text(json.dumps(receipt))
    report.build(tmp_path / 'repeated-report', source=source)
    assert report.check(tmp_path / 'repeated-report')


def test_interrupted_archive_never_counts_one_send_twice(tmp_path):
    from pathlib import Path
    from harness.web_search.runner import physical_call_counts

    profile = synthetic_serp_profile()
    profile['cases'] = profile['cases'][:1]
    profile['methods'] = profile['methods'][:1]
    profile['protocol']['max_calls_per_method'] = 2
    source = tmp_path / 'profile.json'
    source.write_text(json.dumps(profile))
    output = tmp_path / 'run'
    sent = 0
    normalize = report.normalize_response

    def transport(*args, **kwargs):
        nonlocal sent
        if len(args) > 1 and args[1] is not None:
            sent += 1
        return response(*args, **kwargs)

    def stop_before_logical_record(*args, **kwargs):
        raise KeyboardInterrupt('saved response')

    real_move = __import__('shutil').move
    real_rename = Path.rename

    def interrupt_move(src, dst, *args, **kwargs):
        result = real_move(src, dst, *args, **kwargs)
        if Path(src).parent.name == 'reservations':
            raise KeyboardInterrupt('reservation archived')
        return result

    def interrupt_rename(src, dst):
        result = real_rename(src, dst)
        if src.parent.name == 'reservations':
            raise KeyboardInterrupt('reservation archived')
        return result

    with (
        patch('harness.web_search.runner.load_runtime_env'),
        patch.dict(os.environ, {profile['methods'][0]['key_env']: 'synthetic-key'}),
        patch('harness.web_search.runner.http_call', side_effect=transport),
    ):
        with patch('harness.web_search.runner.normalize_response', side_effect=stop_before_logical_record):
            with pytest.raises(KeyboardInterrupt, match='saved response'):
                run(source, output, certified=True)
        with patch('harness.web_search.runner.shutil.move', side_effect=interrupt_move), patch.object(Path, 'rename', interrupt_rename):
            with pytest.raises(KeyboardInterrupt, match='reservation archived'):
                run(source, output, certified=True, resume=True)
        assert sent == 1
        assert physical_call_counts(profile, output) == {profile['methods'][0]['id']: 1}
        with patch('harness.web_search.runner.normalize_response', side_effect=normalize):
            run(source, output, certified=True, resume=True)
    assert sent == 2
    assert physical_call_counts(profile, output) == {profile['methods'][0]['id']: 2}
    receipt = json.loads((output / 'receipt.json').read_text())
    assert receipt['total_retained_physical_call_count'] == 2
    assert receipt['certified']
