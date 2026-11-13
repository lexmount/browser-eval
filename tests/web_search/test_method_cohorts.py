"""Adding a provider must preserve old calls and one shared task population."""

import json

import pytest
from bs4 import BeautifulSoup

from harness.record_io import bytes_sha256
from harness.web_search.core import write_json
from harness.web_search.example import example
from harness.web_search.profiles import current_selection, load_profile_bytes
from harness.web_search.report import build, check, load_report_runs


def split_runs(root):
    paths = []
    for index, name in enumerate(("old", "new")):
        location = root / name
        example(location)
        source = location / "fixture"
        profile = json.loads((source / "profile.json").read_bytes())
        profile["methods"] = [profile["methods"][index]]
        profile["protocol"].update(
            as_of=f"2026-11-{index + 1:02d}", order="complete provider batches"
        )
        write_json(source / "profile.json", profile)
        rows = [
            json.loads(line)
            for line in (source / "results.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        rows = [r for r in rows if r["method"] == profile["methods"][0]["id"]]
        (source / "results.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )
        receipt = json.loads((source / "receipt.json").read_bytes())
        receipt.update(
            call_count=len(rows),
            profile_sha256=bytes_sha256((source / "profile.json").read_bytes()),
            results_sha256=bytes_sha256((source / "results.jsonl").read_bytes()),
        )
        write_json(source / "receipt.json", receipt)
        paths.append(source)
    return paths


def test_add_provider_reuses_old_calls_and_keeps_exact_task_population(tmp_path):
    old, new = split_runs(tmp_path)
    frozen = (old / "results.jsonl").read_bytes()
    content = tmp_path / "content.json"
    write_json(content, {"collection_composition": "methods"})
    output = tmp_path / "combined"
    build(output, source=old, cohorts=[("yuanbao", new)], content=content, include_english=True)
    assert check(output)
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert len(manifest["profile"]["cases"]) == 2
    assert len(manifest["profile"]["methods"]) == 2
    assert len(manifest["rows"]) == 4
    assert len(manifest["receipt"]["separate_collections"]) == 2
    assert (old / "results.jsonl").read_bytes() == frozen
    for prefix in ("", "en/"):
        text = (output / (prefix + "research.html")).read_text(encoding="utf-8")
        assert "2026-11-01" in text and "2026-11-02" in text
        dataset = BeautifulSoup(
            (output / (prefix + "dataset.html")).read_text(encoding="utf-8"), "html.parser"
        )
        assert len(dataset.select("[data-row]")) == 2
    # Release checks bind the collection-window disclosure as well as the numbers.
    home_path = output / "index.html"
    home = home_path.read_text(encoding="utf-8")
    home_path.write_text(home.replace("data-method-collection-notice", "removed"), encoding="utf-8")
    with pytest.raises(ValueError, match="window disclosure"):
        check(output)
    home_path.write_text(home, encoding="utf-8")
    path = output / "research.html"
    path.write_text(
        path.read_text(encoding="utf-8").replace("2026-11-02", "2026-11-01"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Execution settings"):
        check(output)


def test_composed_matrix_is_not_an_executable_collection_profile(tmp_path):
    from harness.web_search.runner import run

    old,new=split_runs(tmp_path)
    methods={name:[json.loads((path/'profile.json').read_bytes())['methods'][0]['id']]
             for name,path in [('primary',old),('new',new)]}
    profile,_,_,_=load_report_runs(old,[('new',new)],{},composition='matrix',source_methods=methods)
    path=tmp_path/'view.json'
    write_json(path,profile)
    with pytest.raises(ValueError,match='cannot be used as a collection profile'):
        run(path,tmp_path/'forbidden-collection',certified=True)
    assert not (tmp_path/'forbidden-collection').exists()


def _fragment_run(source, target, case_ids):
    import shutil

    shutil.copytree(source,target)
    profile=json.loads((target/'profile.json').read_bytes())
    profile['cases']=[c for c in profile['cases'] if c['id'] in case_ids]
    profile['protocol']['client_timeout_s']=35
    write_json(target/'profile.json',profile)
    rows=[json.loads(line) for line in (target/'results.jsonl').read_text().splitlines()]
    rows=[r for r in rows if r['case_id'] in case_ids]
    (target/'results.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    receipt=json.loads((target/'receipt.json').read_bytes())
    receipt.update(call_count=len(rows),profile_sha256=bytes_sha256((target/'profile.json').read_bytes()),
                   results_sha256=bytes_sha256((target/'results.jsonl').read_bytes()))
    write_json(target/'receipt.json',receipt)
    return target


def test_matrix_preserves_concurrent_source_admission_without_reusing_it_for_other_methods(tmp_path):
    from harness.web_search.report import execution_settings

    first,second=split_runs(tmp_path)
    source=json.loads((first/'profile.json').read_bytes())
    mid=source['methods'][0]['id']
    source['protocol'].update(concurrency=2,interval_s=0,order='bounded calls')
    source['execution_policy']=dict(purpose='quality',max_in_flight=2,
                                    resources={mid:dict(capacity=2)})
    write_json(first/'profile.json',source)
    receipt=json.loads((first/'receipt.json').read_bytes())
    receipt['profile_sha256']=bytes_sha256((first/'profile.json').read_bytes())
    write_json(first/'receipt.json',receipt)
    methods={name:[json.loads((path/'profile.json').read_bytes())['methods'][0]['id']]
             for name,path in [('primary',first),('second',second)]}
    profile,rows,_,_=load_report_runs(first,[('second',second)],{},composition='matrix',source_methods=methods)
    assert len(rows) == 4
    assert profile['method_collections'][0]['protocol']['concurrency'] == 2
    page=BeautifulSoup(execution_settings(profile,False),'html.parser')
    capacities=[r.select('td')[1].get_text() for r in page.select('table')[0].select('tbody tr')]
    assert capacities == ['2','1']


def test_matrix_composition_joins_provider_fragments_and_preserves_actual_controls(tmp_path):
    old,new=split_runs(tmp_path)
    profiles=[json.loads((p/'profile.json').read_bytes()) for p in (old,new)]
    case_ids=[c['id'] for c in profiles[0]['cases']]
    first=_fragment_run(new,tmp_path/'first',[case_ids[0]])
    second=_fragment_run(new,tmp_path/'second',[case_ids[1]])
    methods={name:[profile['methods'][0]['id']] for name,profile in [('primary',profiles[0]),('first',profiles[1]),('second',profiles[1])]}
    frozen=[(p/'results.jsonl').read_bytes() for p in (old,first,second)]
    profile,rows,receipt,_=load_report_runs(old,[('first',first),('second',second)],{},
                                         composition='matrix',source_methods=methods)
    assert len(profile['cases']) == 2 and len(profile['methods']) == 2 and len(rows) == 4
    assert len(receipt['separate_collections']) == 3
    assert profile['method_collections'][0]['protocol']['client_timeout_s'] != 35
    assert profile['method_collections'][1]['protocol']['client_timeout_s'] == 35
    assert [(p/'results.jsonl').read_bytes() for p in (old,first,second)] == frozen
    with pytest.raises(ValueError,match='complete method-by-task'):
        load_report_runs(old,[('first',first)],{},composition='matrix',
                         source_methods={k:v for k,v in methods.items() if k != 'second'})
    with pytest.raises(ValueError,match='Overlapping.*cells'):
        load_report_runs(old,[('first',first),('second',first)],{},composition='matrix',source_methods=methods)
    content=tmp_path/'matrix-content.json'
    write_json(content,dict(collection_composition='matrix',collection_methods=methods))
    output=tmp_path/'matrix-report'
    build(output,source=old,cohorts=[('first',first),('second',second)],content=content)
    assert check(output)


def test_method_composition_rejects_duplicate_providers_or_changed_questions(tmp_path):
    old, new = split_runs(tmp_path)
    with pytest.raises(ValueError, match="Overlapping method"):
        load_report_runs(old, [("duplicate", old)], {}, composition="methods")
    profile = json.loads((new / "profile.json").read_bytes())
    profile["cases"][0]["query"] = "A different task"
    write_json(new / "profile.json", profile)
    receipt = json.loads((new / "receipt.json").read_bytes())
    receipt["profile_sha256"] = bytes_sha256((new / "profile.json").read_bytes())
    write_json(new / "receipt.json", receipt)
    with pytest.raises(ValueError, match="identical tasks"):
        load_report_runs(old, [("new", new)], {}, composition="methods")


def test_projection_revision_preserves_collection_protocol_after_serialization(tmp_path):
    from harness.web_search.core import normalize_response
    from harness.web_search.revisions import NATIVE_CONTROLS, apply_report_revision

    old, new = split_runs(tmp_path)
    profile, _, _, _ = load_report_runs(old, [("new", new)], {}, composition="methods")
    frozen_protocol = json.loads(json.dumps(profile["report_cohorts"][0]["protocol"]))
    revision = {
        "schema": "web-search-assessment-revision/1",
        "provider_calls": 0,
        "root_cause": "Native URL projection correction",
        "normalization": "request-url/1",
        "regression_ids": ["relative-serp-links", "unsafe-serp-links", "top-five-boundary"],
        "regressions_sha256": bytes_sha256(NATIVE_CONTROLS.read_bytes()),
    }
    before, _, _ = apply_report_revision(profile, [], revision, normalizer=normalize_response)
    restored, _, _ = apply_report_revision(
        json.loads(json.dumps(profile)), [], revision, normalizer=normalize_response
    )
    assert before == restored
    assert before["report_cohorts"][0]["protocol"] == frozen_protocol


def test_current_instance_can_collect_only_new_provider(tmp_path):
    old, new = split_runs(tmp_path)
    bindings = []
    for name, source in (("old", old), ("new", new)):
        path = tmp_path / (name + ".json")
        path.write_bytes((source / "profile.json").read_bytes())
        bindings.append({"path": path.name, "sha256": bytes_sha256(path.read_bytes())})
    selector = tmp_path / "current.json"
    write_json(
        selector,
        {
            "schema": "web-search-current/1",
            "title": "Added provider",
            "profile": bindings[0],
            "supplementary_profiles": [dict(bindings[1], id="new")],
            "methods": ["lexmount", "deepseek-content"],
            "publication_target": "target.json",
        },
    )
    current_selection(selector)
    selected = json.loads(load_profile_bytes(selector, cohort="new"))
    assert [m["id"] for m in selected["methods"]] == ["deepseek-content"]
    assert len(selected["cases"]) == 2


@pytest.mark.parametrize(
    "changed",
    [
        {"normalize_top_k": True},
        {"result_url_resolution": "request-url/1"},
        {"client_timeout_s": 99},
    ],
)
def test_added_provider_cannot_change_measurement_or_projection_controls(tmp_path, changed):
    old, new = split_runs(tmp_path)
    profile = json.loads((new / "profile.json").read_bytes())
    profile["protocol"].update(changed)
    write_json(new / "profile.json", profile)
    receipt = json.loads((new / "receipt.json").read_bytes())
    receipt["profile_sha256"] = bytes_sha256((new / "profile.json").read_bytes())
    write_json(new / "receipt.json", receipt)
    with pytest.raises(ValueError, match="measurement controls"):
        load_report_runs(old, [("new", new)], {}, composition="methods")


@pytest.mark.parametrize("composition", ["cases", "methods"])
@pytest.mark.parametrize("synthetic_first", [True, False])
def test_mixed_measured_and_synthetic_collections_are_rejected(
    tmp_path, monkeypatch, composition, synthetic_first
):
    from harness.web_search import report

    old, new = split_runs(tmp_path)
    original = report.load_run

    def load(source):
        profile, rows, receipt = original(source)
        return (
            profile,
            rows,
            dict(receipt, synthetic=synthetic_first if source == old else not synthetic_first),
        )

    monkeypatch.setattr(report, "load_run", load)
    with pytest.raises(ValueError, match="synthetic and measured"):
        load_report_runs(old, [("new", new)], {}, composition=composition)


def test_native_response_links_resolve_in_both_language_editions(tmp_path):
    location = tmp_path / "example"
    example(location)
    source = location / "fixture"
    profile = json.loads((source / "profile.json").read_bytes())
    content = tmp_path / "content.json"
    write_json(
        content,
        {
            "presentation": "comparison-report",
            "interface_example_case_id": profile["cases"][0]["id"],
        },
    )
    output = tmp_path / "report"
    build(output, source=source, content=content, include_english=True)
    assert check(output)
    for language in ("", "en/"):
        page = BeautifulSoup(
            (output / (language + "capabilities.html")).read_text(encoding="utf-8"), "html.parser"
        )
        links = page.select("[data-interface-case] [data-evidence]")
        assert len(links) == len(profile["methods"])
        assert all((output / link["data-evidence"]).is_file() for link in links)
