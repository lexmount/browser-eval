"""Full review counts only explicit, complete, hash-bound Codex opinions."""

import json
from unittest.mock import patch

import pytest

from harness.web_search import review


def example(tmp_path, model="gpt-6.1-sol"):
    report = tmp_path / "report"
    report.mkdir()
    profile = {
        "protocol": {"top_k": 5},
        "methods": [{"id": "a"}, {"id": "b"}],
        "cases": [
            {
                "id": "custom",
                "query": "Find penguin facts",
                "anchors": ["https://example.org/facts"],
            },
            {
                "id": "fact",
                "query": "Where?",
                "reference_answer": "Antarctica",
                "anchors": ["https://example.org/answer"],
            },
        ],
    }
    rows = []
    for case in profile["cases"]:
        for method in profile["methods"]:
            path = report / f"{case['id']}-{method['id']}.json"
            payload = (
                {
                    "results": [
                        {
                            "url": "https://example.org/facts",
                            "title": "Penguin",
                            "snippet": "Antarctica",
                            "summary": "S" * 3000,
                            "content": "C" * 5000,
                        }
                    ]
                }
                if method["id"] == "a"
                else {"results": []}
            )
            path.write_text(json.dumps(payload))
            rows.append(
                {
                    "id": path.stem,
                    "case_id": case["id"],
                    "method": method["id"],
                    "http_status": 200,
                    "error": "",
                    "metrics": {"score": 42},
                    "evidence": {"path": path.name, "sha256": review.sha(path.read_bytes())},
                }
            )
    (report / "manifest.json").write_text("{}")
    root = tmp_path / "review"
    with patch("harness.web_search.report.check", return_value=(profile, rows)):
        contract = review.freeze(report, root, budget=1000, model=model)
    return report, root, contract


def opinion(packet, packet_hash):
    return {
        "packet_sha256": packet_hash,
        "cases": [
            {
                "case_id": c["case"]["id"],
                "dataset_review": {
                    "status": "accept",
                    "reason": "Question and evidence agree.",
                    "source_checks": [
                        {
                            "url": "https://example.org/answer",
                            "excerpt": "Antarctica",
                            "assessment": "Supports the reference.",
                        }
                    ],
                },
                "candidate_grades": [
                    {
                        "candidate_id": item["candidate_id"],
                        "grade": 3,
                        "reason": "Direct answer.",
                        "evidence": "Antarctica",
                    }
                    for item in c["candidates"]
                ],
                "output_reviews": [
                    {
                        "output_id": item["output_id"],
                        "status": "confirmed",
                        "reason": "Inspected complete payload.",
                        "evidence": "Returned one item."
                        if item["items"]
                        else "The retained results array is empty.",
                    }
                    for item in c["outputs"]
                ],
                "findings": [],
            }
            for c in packet["cases"]
        ],
    }


def complete(root, contract):
    for entry in contract["packets"]:
        packet = json.loads((root / entry["path"]).read_bytes())
        result = opinion(packet, entry["sha256"])
        job = root / "jobs" / entry["id"]
        job.mkdir(parents=True)
        review.save(job / "result.json", result)
        review.save(
            job / "status.json",
            {
                "status": "completed",
                "returncode": 0,
                "packet_sha256": entry["sha256"],
                "contract_sha256": review.sha(review.encoded(contract)),
                "result_sha256": review.sha((job / "result.json").read_bytes()),
            },
        )


def test_complete_population_full_fields_and_blinding(tmp_path):
    _, root, contract = example(tmp_path)
    assert (contract["case_count"], contract["output_count"], contract["candidate_count"]) == (
        2,
        4,
        1,
    )
    assert len(contract["packets"]) == 2  # An oversized single case is retained intact.
    packets = [json.loads((root / e["path"]).read_bytes()) for e in contract["packets"]]
    text = json.dumps(packets)
    assert '"metrics"' not in text and '"method"' not in text
    assert (
        packets[0]["cases"][0]["outputs"][0]["items"]
        or packets[0]["cases"][0]["outputs"][1]["items"]
    )
    item = next(o["items"][0] for o in packets[0]["cases"][0]["outputs"] if o["items"])
    assert len(item["content"]) == 5000 and len(item["summary"]) == 3000
    aliases = json.loads((root / "alias-map.private.json").read_bytes())
    assert {v["method"] for v in aliases.values()} == {"a", "b"}
    complete(root, contract)
    data = review.collect(root, require_complete=True)
    assert (data["case_reviewed"], data["output_reviewed"], data["candidate_reviewed"]) == (2, 4, 1)
    assert data["accepted_unreviewed_passes"] == 0


@pytest.mark.parametrize(
    "mutation",
    ["missing-case", "duplicate-output", "missing-grade", "wrong-hash", "unverified-reference"],
)
def test_partial_or_invented_coverage_rejected(tmp_path, mutation):
    _, root, contract = example(tmp_path)
    entry = contract["packets"][0 if mutation != "unverified-reference" else 1]
    packet = json.loads((root / entry["path"]).read_bytes())
    data = opinion(packet, entry["sha256"])
    if mutation == "missing-case":
        data["cases"] = []
    elif mutation == "duplicate-output":
        data["cases"][0]["output_reviews"][1] = data["cases"][0]["output_reviews"][0]
    elif mutation == "missing-grade":
        data["cases"][0]["candidate_grades"] = []
    elif mutation == "wrong-hash":
        data["packet_sha256"] = "wrong"
    else:
        data["cases"][0]["dataset_review"]["source_checks"] = []
    with pytest.raises(ValueError):
        review.validate_result(packet, data, entry["sha256"])


@pytest.mark.parametrize("target", ["packet", "raw", "manifest", "schema", "alias"])
def test_frozen_input_tampering_rejected(tmp_path, target):
    report, root, contract = example(tmp_path)
    path = {
        "packet": root / contract["packets"][0]["path"],
        "raw": next((root / "raw").glob("*.json")),
        "manifest": root / "frozen-inputs" / "manifest.json",
        "schema": root / "schema.json",
        "alias": root / "alias-map.private.json",
    }[target]
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError):
        review.load_frozen(root)


def test_successful_jobs_resume_without_new_process_and_invalid_job_not_counted(tmp_path):
    _, root, contract = example(tmp_path)
    complete(root, contract)
    with patch(
        "harness.web_search.review.subprocess.Popen",
        side_effect=AssertionError("must not repeat completed review"),
    ):
        assert review.run(root, workers=2) == [("0001", "reused"), ("0002", "reused")]
    (root / "jobs/0001/result.json").write_text("{}")
    data = review.collect(root)
    assert data["case_reviewed"] == 1 and data["output_reviewed"] == 2
    with pytest.raises(ValueError, match="incomplete"):
        review.collect(root, require_complete=True)


def test_full_case_method_matrix_required(tmp_path):
    report, _, _ = example(tmp_path)
    profile = {
        "protocol": {"top_k": 5},
        "methods": [{"id": "a"}],
        "cases": [{"id": "missing", "query": "Test"}],
    }
    with pytest.raises(ValueError, match="Incomplete"):
        review.make_cases(profile, [], report)


def test_live_job_is_not_restarted(tmp_path):
    _, root, contract = example(tmp_path)
    job = root / "jobs" / contract["packets"][0]["id"]
    job.mkdir(parents=True)
    review.save(job / "status.json", {"status": "running", "pid": 12345})
    with (
        patch("harness.web_search.review.os.kill"),
        patch(
            "harness.web_search.review.subprocess.Popen",
            side_effect=AssertionError("live process must not be duplicated"),
        ),
        pytest.raises(ValueError, match="live review process"),
    ):
        review.run(root, limit=1)


def test_written_result_without_terminal_success_does_not_count(tmp_path):
    _, root, contract = example(tmp_path)
    complete(root, contract)
    job = root / "jobs" / contract["packets"][0]["id"]
    state = json.loads((job / "status.json").read_bytes())
    state["status"] = "running"
    review.save(job / "status.json", state)
    data = review.collect(root)
    assert data["case_reviewed"] == 1
    assert data["case_total"] == 2


def test_subagent_accept_keeps_actual_backend_and_exact_output_identity(tmp_path):
    report, root, contract = example(tmp_path, model="codex-app-inherited")
    entry = contract["packets"][0]
    packet = json.loads((root / entry["path"]).read_bytes())
    result = tmp_path / "actual-agent-result.json"
    review.save(result, opinion(packet, entry["sha256"]))
    assert review.accept(root, entry["id"], result, "/root/actual-reviewer") == "accepted"
    assert review.accept(root, entry["id"], result, "/root/actual-reviewer") == "reused"
    state = json.loads((root / "jobs" / entry["id"] / "status.json").read_bytes())
    assert state["backend"] == "codex-app-subagent"
    assert state["reviewer_id"] == "/root/actual-reviewer"
    assert "returncode" not in state
    # Rebuilding the original report cannot invalidate frozen review inputs.
    for path in report.iterdir():
        path.unlink()
    data = review.collect(root)
    assert data["case_reviewed"] == 1 and data["output_reviewed"] == 2
    assert data["review_runs"][0]["backend"] == "codex-app-subagent"
    for output in data["cases"][0]["output_reviews"]:
        assert {"method", "row_id", "raw_sha256"} <= set(output)
        assert "raw_path" not in output
    with pytest.raises(ValueError, match="app subagents"):
        review.run(root)


def test_subagent_accept_rejects_wrong_backend_contract_and_extra_schema_fields(tmp_path):
    _, root, contract = example(tmp_path)
    entry = contract["packets"][0]
    packet = json.loads((root / entry["path"]).read_bytes())
    result = tmp_path / "opinion.json"
    data = opinion(packet, entry["sha256"])
    review.save(result, data)
    with pytest.raises(ValueError, match="inherited-model"):
        review.accept(root, entry["id"], result, "/root/reviewer")
    data["fabricated"] = True
    with pytest.raises(ValueError, match="schema fields"):
        review.validate_result(packet, data, entry["sha256"])


def test_report_binding_ignores_derived_metrics_but_rejects_changed_answers():
    profile = {"cases": [{"id": "q", "query": "Question"}]}
    row = {
        "id": "q-a",
        "case_id": "q",
        "method": "a",
        "round": 0,
        "evidence": {"sha256": "raw"},
        "results": [],
    }
    binding = review.report_binding(profile, [row])
    projected = {
        **row,
        "normalized_results_sha256": review.sha(review.encoded([])),
        "metrics": {"new": 44},
    }
    del projected["results"]
    assert review.report_binding(profile, [projected]) == binding
    assert (
        review.report_binding(profile, [{**row, "results": [{"url": "https://example.org"}]}])
        != binding
    )
