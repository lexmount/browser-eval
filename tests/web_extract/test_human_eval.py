"""Content extraction scenarios: frozen inputs, native uploads and full denominators."""

import json
import math

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from harness.web_extract import human_eval as he
from harness.web_extract import pipeline as p
from harness.web_extract.certification import create_plan
from harness.web_extract.dataset import BENCHMARKS


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    rows = [
        {"schema": json.dumps({"type": "object", "properties": {"name": {"type": "string"}}}),
         "content": "# A & B\n<script>alert(1)</script>\n[x](/link) &amp;\n",
         "response": '{"name":"A & B"}'},
        {"schema": '{"type":"object","properties":{}}', "content": "Empty", "response": "{}"},
    ]
    pq.write_table(pa.Table.from_pylist(rows), source / "human_eval.parquet")
    # A deliberately simple synthetic evaluator tests orchestration, without
    # pretending to reproduce the official BLEU implementation in a unit test.
    (source / "metrics.py").write_text(
        'import json\n'
        'def _flatten_dict(value):\n'
        ' result={}\n'
        ' for key,item in value.items():\n'
        '  if isinstance(item,list) and item and isinstance(item[0],dict):\n'
        '   for field in item[0]: result[key+"[*]."+field]=[r[field] for r in item]\n'
        '  else: result[key]=item\n'
        ' return result\n'
        'def _compare_values(pred,gold):\n'
        ' if isinstance(gold,list): return float(sorted(pred,key=str)==sorted(gold,key=str))\n'
        ' if isinstance(gold,str) and gold: return len(set(str(pred).split()) & '
        'set(gold.split()))/len(set(gold.split()))\n'
        ' return float(pred==gold)\n'
        'def magic_metric_failed(): return {"key_f1":0.,"value_score":0.}\n'
        'def run_all(pred,schema,gold):\n'
        ' try: value=json.loads(pred)\n'
        ' except ValueError: return {"is_valid_json":0.,"key_f1":1.,"value_score":1.}\n'
        ' return {"is_valid_json":1.,"key_f1":float(value==gold),'
        '"value_score":float(value==gold)}\n'
    )
    card = tmp_path / "card.json"
    p.save_json(card, {"dataset": {"commit": "synthetic", "cases": 2, "files": {
        "source/human_eval.parquet": p.digest((source / "human_eval.parquet").read_bytes()),
        "evaluate.py": p.digest((source / "metrics.py").read_bytes()),
    }}, "source": {"dataset_url": "https://example.org/pinned"}})
    monkeypatch.setattr(he, "CARD", card)
    monkeypatch.setitem(BENCHMARKS, "human-eval", {
        "profile": str(card), "module": "harness.web_extract.human_eval",
    })
    home = tmp_path / "home"
    p.prepare(home, benchmark="human-eval", source=source)
    return home, source, rows


def test_frozen_content_and_schema_roundtrip(dataset):
    home, source, rows = dataset
    truth = p.read_json(home / "dataset/dataset/ground-truth.json")
    for index, case in enumerate(truth.values()):
        cid = case["id"]
        document = (home / "dataset/html" / (cid + ".html")).read_text()
        assert he.unwrap_markdown(document) == rows[index]["content"]
        assert "<script>" not in document
        assert he.task_schema(home, case) == json.loads(rows[index]["schema"])
        assert case["response_text"] == rows[index]["response"]
    (home / "dataset/html/human-eval-000.html").write_text("changed")
    with pytest.raises(ValueError, match="Dataset bytes changed"):
        p.validate_dataset(home)


def test_full_plan_uses_original_schema_and_declared_native_media(dataset, tmp_path):
    home, _, _ = dataset
    plan = create_plan(home, list(he.PARTICIPANT_OPTIONS), tmp_path / "plan.json")
    assert plan["case_count"] == 2 and plan["matrix_size"] == 6
    assert plan["participants"]["scrapegraph-extract"]["input"] == "markdown"
    assert plan["participants"]["cloudflare-json"]["input"] == "html"
    assert plan["input_contract"] == he.INPUT_CONTRACT
    with pytest.raises(ValueError, match="frozen input contract"):
        create_plan(home, ["zyte-product"], tmp_path / "bad.json")


def test_native_markdown_requests_retain_text_and_original_schema(monkeypatch):
    monkeypatch.setenv("SGAI_API_KEY", "test")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test")
    content = "# A & B\n[](/local) <script>unsafe()</script>\n"
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    received = []

    class Response:
        status = 200
        headers = {}

        def text(self):
            return '{"json":{"name":"A & B"}}'

    def request(url, **kwargs):
        received.append(kwargs)
        return Response()

    monkeypatch.setattr(p, "request", request)
    for method in ("scrapegraph-extract", "firecrawl-parse-json"):
        p.provider_call(method, he.wrap_markdown(content), "https://example.org/",
                        schema=schema, native_options=he.PARTICIPANT_OPTIONS[method])
    assert received[0]["json_body"]["markdown"] == content
    assert received[0]["json_body"]["schema"] == schema
    assert b'text/html' in received[1]["body"]
    assert he.wrap_markdown(content).encode() in received[1]["body"]
    assert b'snapshot.html' in received[1]["body"]
    with pytest.raises(ValueError, match="Unsupported native input route"):
        p.validate_native_options("cloudflare-json", {"input_route": "markdown"})


def test_missing_output_stays_zero_even_for_empty_gold(dataset):
    home, _, _ = dataset
    truth = p.read_json(home / "dataset/dataset/ground-truth.json")
    scores = he.score(truth, {"human-eval-000": {"name": "A & B"}}, home=home)
    assert scores["population"] == 2
    assert scores["delivery_count"] == 1
    assert scores["mean"]["value_score"] == .5
    assert scores["per_case"]["human-eval-001"]["value_score"] == 0
    rows = [{"method": "scrapegraph-extract", "case_id": cid,
             "http_status": 200, "normalized_fields": None} for cid in truth]
    result = p.score_rows(home, truth, rows, ["scrapegraph-extract"])
    assert result["scrapegraph-extract"]["delivery_count"] == 0


def test_record_identity_preserves_pairs_duplicates_and_types():
    gold = [{"name": "A", "price": 1}, {"name": "B", "price": 2}]
    assert he.record_identity(gold) == he.record_identity(list(reversed(gold)))
    assert he.record_identity(gold) != he.record_identity(
        [{"name": "A", "price": 2}, {"name": "B", "price": 1}])
    assert he.record_identity(["A", "A"]) != he.record_identity(["A"])
    assert he.record_identity(True) != he.record_identity(1)
    assert he.record_identity(None) != he.record_identity("")


def test_value_score_is_independent_of_case_and_field_order(dataset):
    home, _, _ = dataset
    truth, predictions = {}, {}
    for size in range(2, 80):
        cid = str(size)
        gold = {str(key): key for key in range(size)}
        truth[cid] = {"response": gold, "schema_text": '{"type":"object"}'}
        predictions[cid] = {**gold, "0": -1}
    rows = [{"method": "cloudflare-json", "case_id": cid,
             "http_status": 200, "normalized_fields": predictions[cid]} for cid in truth]
    first = p.score_rows(home, truth, rows, ["cloudflare-json"])
    second = p.score_rows(home, dict(reversed(list(truth.items()))), rows, ["cloudflare-json"])
    assert first == second
    expected = math.fsum((size - 1) / size for size in range(2, 80)) / 78
    assert first["cloudflare-json"]["mean"]["value_score"] == expected


def test_registered_controls_execute_through_public_scoring(dataset):
    home, _, _ = dataset
    # The scenario data stay in the shared registry. This fixture substitutes
    # only the external evaluator dependency; requests and scoring use public APIs.
    assert he.check_scoring_controls(home) == he.SCORING_REVISION["regression_ids"]


def test_source_changes_fail_before_materialization(dataset, tmp_path):
    _, source, _ = dataset
    (source / "metrics.py").write_text("changed")
    with pytest.raises(ValueError, match="Pinned human_eval source differs"):
        he.prepare(tmp_path / "other", source)
    assert not (tmp_path / "other/dataset").exists()


def test_certified_cohort_keeps_original_task_and_complete_population(
    dataset, tmp_path, monkeypatch
):
    from harness.web_extract.certification import run

    home, _, _ = dataset
    profile = tmp_path / "plan.json"
    create_plan(home, ["scrapegraph-extract"], profile)
    monkeypatch.setattr(p, "load_runtime_env", lambda: None)
    monkeypatch.setenv("SGAI_API_KEY", "synthetic")
    dispatched = []

    def call(method, document, url, fixture=None, **kwargs):
        dispatched.append((document, kwargs))
        assert kwargs["prompt"] == ""
        assert kwargs["native_options"] == {"input_route": "markdown"}
        value = {"name": "A & B"} if len(dispatched) == 1 else {}
        return {"http_status": 200, "elapsed_seconds": .01, "response": {"json": value}}

    monkeypatch.setattr(p, "provider_call", call)
    result = run(home, profile, "scrapegraph-extract", tmp_path / "run")
    assert result["cases"] == 2
    assert result["scores"]["scrapegraph-extract"]["delivery_count"] == 2
    assert (tmp_path / "run/certificate.json").is_file()
    contract = p.read_json(tmp_path / "run/contract.json")
    assert contract["extraction_instructions"] == ""
    assert contract["extraction_instruction_delivery"] == "original-schema/1"
    run(home, profile, "scrapegraph-extract", tmp_path / "run")
    assert len(dispatched) == 2


def test_source_reference_revision_preserves_original_and_rejects_unbound_edits(
    dataset, monkeypatch,
):
    home, _, _ = dataset
    monkeypatch.setattr(he, 'check_scoring_controls', lambda home: ['paired-control'])
    original = home / 'dataset/dataset/ground-truth.json'
    source = home / 'dataset/markdown/human-eval-000.md'
    revision = home / 'reference-revision.json'
    value = {'schema': 'source-json-reference-revision/1',
             'original_reference_sha256': p.digest(original.read_bytes()),
             'root_cause': 'The original answer omitted the source heading punctuation.',
             'regression_ids': ['paired-control'], 'changes': [{
                 'case_id': 'human-eval-000', 'path': '/name', 'before': 'A & B',
                 'after': '# A & B', 'source_sha256': p.digest(source.read_bytes()),
                 'source_quote': '# A & B', 'reason': 'Retain the exact source heading.'}]}
    p.save_json(revision, value)
    revised, policy = he.load_reference_revision(home, revision)
    assert revised['human-eval-000']['response']['name'] == '# A & B'
    assert p.read_json(original)['human-eval-000']['response']['name'] == 'A & B'
    assert policy == {}
    for key, bad in [('before', 'wrong predecessor'), ('source_quote', 'invented words'),
                     ('source_sha256', '0' * 64)]:
        candidate = json.loads(json.dumps(value))
        candidate['changes'][0][key] = bad
        p.save_json(revision, candidate)
        with pytest.raises(ValueError):
            he.load_reference_revision(home, revision)


@pytest.mark.parametrize('defect', [
    'missing_case', 'empty_cases', 'empty_mean', 'missing_leaf_metric',
    'missing_mean_metric', 'population', 'delivery', 'extra_case',
])
def test_predecessor_scores_reject_incomplete_or_changed_evidence(dataset, defect):
    import copy

    home, _, _ = dataset
    truth = p.read_json(home / 'dataset/dataset/ground-truth.json')
    rows = [{'method': 'cloudflare-json', 'case_id': cid, 'http_status': 200,
             'normalized_fields': case['response']} for cid, case in truth.items()]
    current = p.score_rows(home, truth, rows, ['cloudflare-json'])
    saved = copy.deepcopy(current)
    for group in saved.values():
        for scores in [group['mean'], *group['per_case'].values()]:
            for key in ('item_precision', 'item_recall', 'item_f1'):
                scores.pop(key)
    assert he.verify_predecessor_scores(saved, current, list(truth))
    group = saved['cloudflare-json']
    if defect == 'missing_case':
        group['per_case'].pop(next(iter(truth)))
    elif defect == 'empty_cases':
        group['per_case'] = {}
    elif defect == 'empty_mean':
        group['mean'] = {}
    elif defect == 'missing_leaf_metric':
        group['per_case'][next(iter(truth))].pop('value_score')
    elif defect == 'missing_mean_metric':
        group['mean'].pop('value_score')
    elif defect == 'population':
        group['population'] += 1
    elif defect == 'delivery':
        group['delivery_count'] -= 1
    else:
        group['per_case']['not-declared'] = copy.deepcopy(next(iter(group['per_case'].values())))
    assert not he.verify_predecessor_scores(saved, current, list(truth))


def test_nested_array_numeric_serializers_preserve_order_and_multiplicity():
    gold = {'items': [{'values': [100000000000000000000, 11]}]}
    assert he.item_scores(gold, {'items': [{'values': [11, 1e20]}]})['item_f1'] == 1
    assert he.item_scores({'items': [{'values': [1, 1, 2]}]},
                          {'items': [{'values': [1, 2, 2]}]})['item_f1'] == 0


def test_reference_rescoring_rejects_a_corrupt_predecessor_envelope(dataset, monkeypatch, tmp_path):
    home, _, _ = dataset
    truth = p.read_json(home / 'dataset/dataset/ground-truth.json')
    rows = [{'method': 'cloudflare-json', 'case_id': cid, 'http_status': 200,
             'normalized_fields': case['response']} for cid, case in truth.items()]
    contract = {'kind': 'full_archived_extraction', 'case_ids': list(truth),
                'methods': ['cloudflare-json'],
                'dataset_manifest_sha256': p.digest((home / 'dataset/manifest.json').read_bytes())}
    run = tmp_path / 'run'
    p.save_json(run / 'manifest.json', {'synthetic_calls': list(truth)})
    monkeypatch.setattr(p, 'load_run', lambda path: (contract, rows))
    p.score_run(home, run)
    original = home / 'dataset/dataset/ground-truth.json'
    source = home / 'dataset/markdown/human-eval-000.md'
    authority = tmp_path / 'revision.json'
    p.save_json(authority, {'schema': 'source-json-reference-revision/1',
        'original_reference_sha256': p.digest(original.read_bytes()),
        'root_cause': 'Keep literal heading punctuation.',
        'regression_ids': he.check_scoring_controls(home), 'changes': [{
            'case_id': 'human-eval-000', 'path': '/name', 'before': 'A & B', 'after': '# A & B',
            'source_sha256': p.digest(source.read_bytes()), 'source_quote': '# A & B',
            'reason': 'The literal source heading includes the marker.'}]})
    p.score_run(home, run, reference_revision=authority, output=run / 'revised.json')
    predecessor = p.read_json(run / 'scores.json')
    predecessor['cases'] = 0
    p.save_json(run / 'scores.json', predecessor)
    with pytest.raises(ValueError, match='Original scores do not reproduce'):
        p.score_run(home, run, reference_revision=authority, output=run / 'invalid.json')
    assert not (run / 'invalid.json').exists()
