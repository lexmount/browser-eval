"""Content extraction scenarios: frozen inputs, native uploads and full denominators."""

import json

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
