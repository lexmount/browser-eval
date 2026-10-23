"""Full review counts only explicit, complete, hash-bound Codex opinions."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.web_search import review
from harness.web_search.core import normalize_response


def example(tmp_path, model="gpt-6.1-sol", scope="all", extra_cases=0, source_mode="live"):
    report = tmp_path / "report"
    report.mkdir()
    profile = {
        "protocol": {"top_k": 5},
        "methods": [{"id": "a", "adapter": "lexmount"}, {"id": "b", "adapter": "browserbase"}],
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
                "source_observations": [{"url": "https://example.org/answer", "excerpt": "Antarctica", "observation_kind": "independent fixture source"}],
                "anchors": ["https://example.org/answer"],
            },
        ],
    }
    profile["cases"].extend({**profile["cases"][0], "id": f"extra-{i}"} for i in range(extra_cases))
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
                    "results": normalize_response(payload, method, profile["protocol"]),
                    "evidence": {"path": path.name, "sha256": review.sha(path.read_bytes())},
                }
            )
    (report / "manifest.json").write_text("{}")
    root = tmp_path / "review"
    with patch("harness.web_search.report.check", return_value=(profile, rows)):
        contract = review.freeze(report, root, budget=1000, model=model, scope=scope, authorization="Explicit full-review test scenario" if scope == "all" else None, source_mode=source_mode)
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


@pytest.mark.parametrize("source_mode", ["frozen", "live"])
def test_source_quotes_follow_the_declared_review_mode(tmp_path, source_mode):
    _, root, contract = example(tmp_path, source_mode=source_mode)
    entry = next(e for e in contract["packets"] if "fact" in e["case_ids"])
    packet = json.loads((root / entry["path"]).read_bytes())
    data = opinion(packet, entry["sha256"])
    review.validate_result(packet, data, entry["sha256"], source_mode=source_mode)
    fact = next(c for c in data["cases"] if c["case_id"] == "fact")
    fact["dataset_review"]["source_checks"][0]["url"] = "https://independent.example/new-observation"
    if source_mode == "live":
        review.validate_result(packet, data, entry["sha256"], source_mode=source_mode)
    else:
        with pytest.raises(ValueError, match="frozen source"):
            review.validate_result(packet, data, entry["sha256"], source_mode=source_mode)
    fact["dataset_review"]["source_checks"] = []
    with pytest.raises(ValueError, match="independent source checks"):
        review.validate_result(packet, data, entry["sha256"], source_mode=source_mode)


def complete(root, contract):
    for entry in contract["packets"]:
        packet = json.loads((root / entry["path"]).read_bytes())
        result = opinion(packet, entry["sha256"])
        job = root / "jobs" / entry["id"]
        job.mkdir(parents=True)
        review.save(job / "result.json", result)
        attempt = job / "attempt-001"
        attempt.mkdir()
        (attempt / "result.json").write_bytes((job / "result.json").read_bytes())
        (attempt / "input.txt").write_text(review.LIVE_PROMPT + json.dumps({"packet_sha256": entry["sha256"], **packet}, ensure_ascii=False))
        review.save(
            job / "status.json",
            {
                "status": "completed",
                "returncode": 0,
                "packet_sha256": entry["sha256"],
                "contract_sha256": review.sha(review.encoded(contract)),
                "result_sha256": review.sha((job / "result.json").read_bytes()),
                "attempt": attempt.name,
                "command": review._review_command("codex", contract["model"], attempt, (root / "schema.json").resolve(), (attempt / "result.json").resolve()),
                "input_sha256": review.sha((review.LIVE_PROMPT + json.dumps({"packet_sha256": entry["sha256"], **packet}, ensure_ascii=False)).encode()),
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
    import fcntl
    _, root, contract = example(tmp_path)
    job = root / "jobs" / contract["packets"][0]["id"]
    job.mkdir(parents=True)
    with (job / "process.lock").open("a") as active:
        fcntl.flock(active, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with patch("harness.web_search.review.subprocess.Popen", side_effect=AssertionError("duplicate reviewer")), pytest.raises(ValueError, match="active reviewer"):
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


def test_manual_backend_and_inherited_placeholder_are_not_accepted(tmp_path):
    with pytest.raises(ValueError, match="explicit reviewer model"):
        example(tmp_path, model="codex-app-inherited")
    with pytest.raises(SystemExit):
        review.main(["accept", "--review", str(tmp_path), "--packet", "1", "--result", "fake", "--reviewer-id", "/root/invented"])


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


def test_failure_only_review_counts_only_selected_outputs_and_can_resume(tmp_path):
    _, root, contract = example(tmp_path, scope="failures")
    frozen = review.load_frozen(root)
    aliases = json.loads((root / "alias-map.private.json").read_text())
    assert frozen == contract
    assert contract["output_count"] == len(aliases)
    assert 0 < contract["output_count"] < 4
    assert contract["scope"] == "failures"


def native_case(tmp_path, adapter, payload, *, error="", results=None):
    protocol = {"top_k": 5, "normalize_top_k": True, "result_url_resolution": "request-url/1"}
    method = {"id": "anonymous", "adapter": adapter}
    request = {"url": "https://www.google.com/search?q=penguin"}
    profile = {"protocol": protocol, "methods": [method],
               "cases": [{"id": "q", "query": "Penguin", "family": "web"}]}
    path = tmp_path / "response.json"
    path.write_text(json.dumps(payload))
    projected = normalize_response(payload, method, protocol, request) if not error else []
    row = {"id": "q-result", "case_id": "q", "method": method["id"],
           "http_status": 200, "error": error, "request": request,
           "results": projected if results is None else results,
           "evidence": {"path": path.name, "sha256": review.sha(path.read_bytes())}}
    return profile, row


@pytest.mark.parametrize("adapter,wrapped", [("serper", None), ("brightdata", None),
                                            ("brightdata", "json"), ("brightdata", "object")])
def test_native_review_preserves_scored_first_five_and_candidate_pool_without_calls(tmp_path, adapter, wrapped):
    organic = [{"link": f"https://example.org/{i}", "title": f"Result {i}",
                "snippet": f"Penguin detail {i}"} for i in range(5)]
    # Neither ads/answers nor malformed entries beyond the frozen cutoff are candidates.
    payload = {"organic": organic + [None], "ads": [{"link": "https://ad.invalid"}],
               "answerBox": {"answer": "unscored answer"}}
    if wrapped:
        payload = {"status_code": 200, "body": json.dumps(payload) if wrapped == "json" else payload}
    profile, row = native_case(tmp_path, adapter, payload)
    with (patch("urllib.request.urlopen", side_effect=AssertionError("No provider calls")),
          patch("subprocess.Popen", side_effect=AssertionError("No subprocess calls"))):
        cases, aliases = review.make_cases(profile, [row], tmp_path)
    assert cases[0]["outputs"][0]["items"] == row["results"]
    assert len(cases[0]["candidates"]) == 5
    assert {c["url"] for c in cases[0]["candidates"]} == {p["link"] for p in organic}
    assert aliases[cases[0]["outputs"][0]["output_id"]]["row_id"] == row["id"]


def test_native_review_title_only_and_request_relative_url(tmp_path):
    profile, row = native_case(tmp_path, "browserbase", {"results": [
        {"url": "/search?q=antarctica", "title": "Antarctica"}]})
    cases, _ = review.make_cases(profile, [row], tmp_path)
    item = cases[0]["outputs"][0]["items"][0]
    assert item["url"] == "https://www.google.com/search?q=antarctica"
    assert item["title"] == "Antarctica" and item["snippet"] == ""
    assert cases[0]["candidates"][0]["previews"] == [{"title": "Antarctica", "snippet": ""}]


def test_native_review_retains_nested_failed_response_evidence(tmp_path):
    payload = {"status_code": 407, "body": "", "headers": {
        "x-brd-error": "Search engine captcha challenge", "x-brd-error-code": "captcha",
        "authorization": "must not be included"}}
    profile, row = native_case(tmp_path, "brightdata", payload, error="upstream failure")
    cases, _ = review.make_cases(profile, [row], tmp_path)
    output = cases[0]["outputs"][0]
    assert output["items"] == [] and cases[0]["candidates"] == []
    assert output["transport"]["error"] == (
        "upstream failure\nstatus_code: 407\nx-brd-error: Search engine captcha challenge\n"
        "x-brd-error-code: captcha")
    assert "must not be included" not in json.dumps(cases)


@pytest.mark.parametrize("correction", ["different-items", "different-receipt", "invalid-scored-slot"])
def test_native_review_rejects_disagreement_instead_of_fabricating_empty_output(tmp_path, correction):
    profile, row = native_case(tmp_path, "serper", {"organic": [{"link": "https://example.org"}]})
    if correction == "different-items":
        row["results"] = []
    elif correction == "different-receipt":
        del row["results"]
        row["normalized_results_sha256"] = review.sha(review.encoded([]))
    else:
        path = tmp_path / row["evidence"]["path"]
        path.write_text(json.dumps({"organic": [None]}))
        row["evidence"]["sha256"] = review.sha(path.read_bytes())
    with pytest.raises(ValueError, match="projection disagrees|organic result"):
        review.make_cases(profile, [row], tmp_path)


def test_review_command_allows_live_source_checks_but_disables_mutating_tools(tmp_path):
    _, root, contract = example(tmp_path)
    commands = []

    class Process:
        pid = 123456
        returncode = 0

        def __init__(self, command, **kwargs):
            commands.append(command)
            packet_entry = contract["packets"][0]
            packet = json.loads((root / packet_entry["path"]).read_bytes())
            result_path = command[command.index("--output-last-message") + 1]
            review.save(Path(result_path), opinion(packet, packet_entry["sha256"]))

        def communicate(self, text, timeout):
            pass

    with patch("harness.web_search.review.subprocess.Popen", Process):
        assert review.run(root, limit=1) == [("0001", "completed")]
    command = commands[0]
    state = json.loads((root / "jobs/0001/status.json").read_bytes())
    assert state["schema_protocol"] == "packet-identities/1"
    schema_path = Path(command[command.index("--output-schema") + 1])
    assert schema_path == (root / "jobs/0001/attempt-001/schema.json").resolve()
    assert state["schema_sha256"] == review.sha(schema_path.read_bytes())
    assert review._successful(root, contract["packets"][0], contract)
    assert 'web_search="live"' in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    disabled = {command[i + 1] for i, part in enumerate(command) if part == "--disable"}
    assert {"shell_tool", "unified_exec", "apps", "plugins", "browser_use", "computer_use", "multi_agent"} <= disabled


@pytest.mark.parametrize("failure_log", ["HTTP 429 Too Many Requests", "rate_limit_exceeded"])
def test_account_limit_stops_new_packets_keeps_running_success_and_resumes(tmp_path, failure_log):
    from threading import Event

    _, root, contract = example(tmp_path, extra_cases=2)
    second_started, limit_recorded = Event(), Event()
    started = []
    original_save = review.save

    def save_and_signal(path, value):
        original_save(path, value)
        if path.name == "status.json" and value.get("failure_kind") == "rate_limit":
            limit_recorded.set()

    class Process:
        pid = 123456

        def __init__(self, command, **kwargs):
            self.result_path = Path(command[command.index("--output-last-message") + 1])
            self.packet_id = self.result_path.parent.parent.name
            started.append(self.packet_id)
            self.returncode = 1 if self.packet_id == "0001" else 0
            if self.returncode:
                kwargs["stderr"].write(failure_log.encode())
            else:
                second_started.set()
                entry = next(p for p in contract["packets"] if p["id"] == self.packet_id)
                packet = json.loads((root / entry["path"]).read_bytes())
                original_save(self.result_path, opinion(packet, entry["sha256"]))

        def communicate(self, text, timeout):
            assert (second_started if self.returncode else limit_recorded).wait(5)

        def poll(self):
            return self.returncode

    with (patch("harness.web_search.review.subprocess.Popen", Process),
          patch("harness.web_search.review.save", save_and_signal)):
        assert review.run(root, workers=2) == [("0001", "failed"), ("0002", "completed")]
    assert set(started) == {"0001", "0002"}
    assert not (root / "jobs/0003").exists()
    assert not (root / "jobs/0004").exists()
    success_bytes = (root / "jobs/0002/result.json").read_bytes()
    failed_log = root / "jobs/0001/attempt-001/stderr.log"
    assert failed_log.read_text() == failure_log

    class SuccessfulProcess(Process):
        def __init__(self, command, **kwargs):
            self.result_path = Path(command[command.index("--output-last-message") + 1])
            self.packet_id = self.result_path.parent.parent.name
            started.append(self.packet_id)
            self.returncode = 0
            entry = next(p for p in contract["packets"] if p["id"] == self.packet_id)
            packet = json.loads((root / entry["path"]).read_bytes())
            original_save(self.result_path, opinion(packet, entry["sha256"]))

        def communicate(self, text, timeout):
            pass

    started.clear()
    with patch("harness.web_search.review.subprocess.Popen", SuccessfulProcess):
        assert review.run(root, workers=2) == [("0001", "completed"), ("0002", "reused"),
                                                ("0003", "completed"), ("0004", "completed")]
    assert set(started) == {"0001", "0003", "0004"}
    assert (root / "jobs/0002/result.json").read_bytes() == success_bytes
    assert failed_log.read_text() == failure_log
    assert (root / "jobs/0001/attempt-002/result.json").exists()
    assert review.collect(root, require_complete=True)["case_reviewed"] == 4


def test_collect_projects_existing_absolute_private_contract_without_mutating_it(tmp_path):
    _, root, contract = example(tmp_path)
    # Existing in-flight freezes keep their absolute bindings and original hash.
    for binding in contract["source_bindings"]:
        assert not Path(binding["path"]).is_absolute()
        binding["path"] = str((root / binding["path"]).resolve())
    review.save(root / "contract.json", contract)
    complete(root, contract)
    original_contract = (root / "contract.json").read_bytes()
    original_status = (root / "jobs/0001/status.json").read_bytes()
    evidence = review.collect(root, require_complete=True)
    proof = evidence["provenance"]
    public_text = json.dumps(evidence)
    assert str(tmp_path) not in public_text
    assert '"command"' not in public_text and '"output_bytes_hex"' not in public_text
    assert proof["private_contract_sha256"] == review.sha(original_contract)
    assert proof["public_contract_sha256"] == review.sha(review.encoded(proof["contract"]))
    assert proof["private_contract_sha256"] != proof["public_contract_sha256"]
    assert all(not Path(b["path"]).is_absolute() for b in proof["contract"]["source_bindings"])
    assert (root / "contract.json").read_bytes() == original_contract
    assert (root / "jobs/0001/status.json").read_bytes() == original_status
    execution = proof["executions"][0]
    assert execution["private_execution_sha256"] == review.sha(original_status)
    assert execution["output_utf8"].encode() == (root / "jobs/0001/result.json").read_bytes()
    assert review.load_frozen(root) == contract


@pytest.mark.parametrize("defect", ["model", "sandbox", "web-search", "extra-option", "input", "input-file", "output", "attempt-output", "packet", "raw"])
def test_collect_requires_unchanged_private_physical_execution(tmp_path, defect):
    _, root, contract = example(tmp_path)
    complete(root, contract)
    state_path = root / "jobs/0001/status.json"
    state = json.loads(state_path.read_bytes())
    if defect in ("model", "sandbox", "web-search"):
        option, value = {"model": ("--model", "other-model"),
                         "sandbox": ("--sandbox", "danger-full-access"),
                         "web-search": ("-c", 'web_search="disabled"')}[defect]
        state["command"][state["command"].index(option) + 1] = value
    elif defect == "extra-option":
        state["command"][1:1] = ["--dangerously-bypass-approvals-and-sandbox"]
    elif defect == "input":
        state["input_sha256"] = "0" * 64
    elif defect == "input-file":
        (root / "jobs/0001/attempt-001/input.txt").write_text("Different review instructions")
    elif defect == "attempt-output":
        (root / "jobs/0001/attempt-001/result.json").write_text("{}")
    elif defect == "output":
        with (root / "jobs/0001/result.json").open("ab") as f:
            f.write(b"\n")
    elif defect == "packet":
        (root / contract["packets"][0]["path"]).write_text("{}")
    elif defect == "raw":
        raw = next((root / "raw").glob("*.json"))
        raw.write_text("{}")
    review.save(state_path, state)
    with pytest.raises(ValueError, match="incomplete|changed"):
        review.collect(root, require_complete=True)


@pytest.mark.parametrize("identity", ["case_id", "output_id", "candidate_id", "affected_output_ids", "packet_sha256"])
def test_packet_schema_rejects_fabricated_or_extended_identities(tmp_path, identity):
    _, root, contract = example(tmp_path)
    entry = contract["packets"][0]
    packet = json.loads((root / entry["path"]).read_bytes())
    result = opinion(packet, entry["sha256"])
    schema_bytes = (root / "schema.json").read_bytes()
    schema = review._packet_schema(json.loads(schema_bytes), packet, entry["sha256"])
    review._validate_schema(result, schema)
    case = result["cases"][0]
    if identity == "case_id":
        case["case_id"] += "90"
    elif identity == "output_id":
        case["output_reviews"][0]["output_id"] += "90"
    elif identity == "candidate_id":
        assert len(case["candidate_grades"][0]["candidate_id"]) == 12
        case["candidate_grades"][0]["candidate_id"] += "90"
    elif identity == "affected_output_ids":
        case["findings"] = [{"kind": "test", "reason": "test", "evidence": "test",
                             "affected_output_ids": ["made-up-output"]}]
    else:
        result["packet_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="enum"):
        review._validate_schema(result, schema)
    assert (root / "schema.json").read_bytes() == schema_bytes


def test_packet_schema_accepts_empty_candidate_pool_without_empty_enums(tmp_path):
    _, root, contract = example(tmp_path)
    entry = contract["packets"][1]
    packet = json.loads((root / entry["path"]).read_bytes())
    assert packet["cases"][0]["candidates"] == []
    schema = review._packet_schema(review.SCHEMA, packet, entry["sha256"])
    assert '"enum": []' not in json.dumps(schema)
    result = opinion(packet, entry["sha256"])
    review._validate_schema(result, schema)
    result["cases"][0]["candidate_grades"] = [{"candidate_id": "made-up", "grade": 0,
                                                "reason": "test", "evidence": "test"}]
    with pytest.raises(ValueError, match="maximum"):
        review._validate_schema(result, schema)


@pytest.mark.parametrize("defect", ["bytes", "receipt", "command", "protocol", "extended-output-id"])
def test_packet_schema_physical_receipt_cannot_drift(tmp_path, defect):
    _, root, contract = example(tmp_path)
    complete(root, contract)
    entry = contract["packets"][0]
    job = root / "jobs" / entry["id"]
    state = json.loads((job / "status.json").read_bytes())
    packet = json.loads((root / entry["path"]).read_bytes())
    schema_path = job / state["attempt"] / "schema.json"
    review.save(schema_path, review._packet_schema(review.SCHEMA, packet, entry["sha256"]))
    state.update(schema_protocol="packet-identities/1", schema_sha256=review.sha(schema_path.read_bytes()))
    state["command"][state["command"].index("--output-schema") + 1] = str(schema_path.resolve())
    review.save(job / "status.json", state)
    assert review._successful(root, entry, contract)
    if defect == "bytes":
        schema_path.write_text("{}")
    elif defect == "receipt":
        state["schema_sha256"] = "0" * 64
    elif defect == "command":
        state["command"][state["command"].index("--output-schema") + 1] = str((root / "schema.json").resolve())
    elif defect == "protocol":
        state["schema_protocol"] = "unknown/1"
    else:
        result = json.loads((job / "result.json").read_bytes())
        result["cases"][0]["candidate_grades"][0]["candidate_id"] += "90"
        review.save(job / "result.json", result)
        review.save(job / state["attempt"] / "result.json", result)
        state["result_sha256"] = review.sha((job / "result.json").read_bytes())
    review.save(job / "status.json", state)
    assert review._successful(root, entry, contract) is None
    with pytest.raises(ValueError, match="incomplete"):
        review.collect(root, require_complete=True)
