import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from webarena_verified.core.utils.checksum import compute_data_file_checksum

from harness.browser_use.runner import _configure_benchmark
from harness.webarena_state import (
    PROTOCOL,
    capture_form,
    digest,
    evaluate_task,
    load_contracts,
    require_supersession,
    rule_identity,
    verify_attempt,
)


def materialize_attempt(s):
    payloads = {
        "final_state": s.observation,
        "evaluation": score(s),
        "actual_response": {"task_type": "MUTATE", "status": "SUCCESS", "retrieved_data": None},
    }
    evidence = {}
    for name, payload in payloads.items():
        path = s.root / f"{name}.json"
        path.write_text(json.dumps(payload))
        evidence[name] = {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    evidence["network_har"] = {
        "path": str(s.har),
        "sha256": hashlib.sha256(s.har.read_bytes()).hexdigest(),
    }
    return {
        "task_id": 528,
        "engine": "chromium",
        "run_id": "run",
        "attempt_id": "attempt",
        "state_contract_sha256": s.contract.definition_sha256,
        "status": "pass",
        "process_status": "pass",
        "evaluator_status": "pass",
        "evidence": evidence,
    }


def test_current_state_evidence_can_be_reverified_and_old_run_cannot(setup):
    attempt = materialize_attempt(setup)
    verify_attempt(attempt, setup.contract)
    attempt["run_id"] = "old"
    with pytest.raises(ValueError, match="cannot be reproduced"):
        verify_attempt(attempt, setup.contract)


@pytest.mark.parametrize(
    "field,value",
    [
        ("process_status", "fail"),
        ("evaluator_status", "fail"),
        ("contract_correction_id", "legacy"),
        ("state_contract_sha256", "old"),
    ],
)
def test_reverification_rejects_inconsistent_attempts(setup, field, value):
    attempt = materialize_attempt(setup)
    attempt[field] = value
    with pytest.raises(ValueError):
        verify_attempt(attempt, setup.contract)


def test_missing_or_rehashed_modified_evaluation_cannot_promote(setup):
    attempt = materialize_attempt(setup)
    path = setup.root / "evaluation.json"
    payload = json.loads(path.read_text())
    payload["score"] = 0
    path.write_text(json.dumps(payload))
    attempt["evidence"]["evaluation"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="cannot be reproduced"):
        verify_attempt(attempt, setup.contract)
    del attempt["evidence"]["final_state"]
    with pytest.raises(ValueError, match="missing or unsafe"):
        verify_attempt(attempt, setup.contract)


def test_legacy_correction_requires_explicit_exact_supersession(setup):
    with pytest.raises(ValueError, match="explicitly supersede"):
        require_supersession(setup.contract, "legacy", "abc")
    setup.contract.definition["supersedes_replay_correction"] = {
        "correction_id": "legacy",
        "record_sha256": "abc",
    }
    require_supersession(setup.contract, "legacy", "abc")
    with pytest.raises(ValueError):
        require_supersession(setup.contract, "legacy", "changed")


def test_session_identity_binds_single_request_auth():
    from harness.browser_use.runner import _browser_session_contract

    assert _browser_session_contract()["postmill_auth"] == "single_request_bootstrap"


def test_capture_dispatch_failure_is_unavailable_without_repeating_actions(setup):
    from harness.trajectory_replay.webarena_replay import _attempt_needs_retry
    from harness.webarena_state import capture_service_form

    calls = []

    def submit(_):
        calls.append("capture")
        raise RuntimeError("service unavailable")

    observation = capture_service_form(
        SimpleNamespace(_submit=submit),
        setup.contract,
        run_id="run",
        attempt_id="attempt",
        engine="chromium",
    )
    assert calls == ["capture"]
    assert observation["capture_status"] == "error"
    assert score(setup, observation)["status"] == "unsupported"
    assert not _attempt_needs_retry({"status": score(setup, observation)["status"]})


def test_one_browser_observation_cannot_be_borrowed_by_another(setup):
    from harness.webarena_state import validate_attempt_identities

    chromium = materialize_attempt(setup)
    verify_attempt(chromium, setup.contract)
    moli = {**chromium, "engine": "moli"}
    with pytest.raises(ValueError, match="cannot be reproduced"):
        verify_attempt(moli, setup.contract)
    with pytest.raises(ValueError, match="identity reused"):
        validate_attempt_identities([chromium, moli], {528})


@pytest.mark.parametrize("tamper", [None, "missing", "rehash"])
def test_qualification_validates_actual_state_not_just_score(setup, tamper):
    from harness.trajectory_replay.webarena_dataset import _validate_passing_attempt_evidence
    from harness.trajectory_replay.webarena_plan import replay_calls_binding_sha256

    attempt = materialize_attempt(setup)
    calls = [{"action": "wait", "seconds": 1}]
    candidate = {
        "tool_calls": calls,
        "action_count": 1,
        "recorded_response": {"task_type": "MUTATE", "status": "SUCCESS", "retrieved_data": None},
    }
    trace = setup.root / "trace.json"
    trace.write_text(json.dumps([{"arguments": calls[0], "status": "success", "result": "ok"}]))
    attempt["evidence"]["trace"] = {
        "path": str(trace),
        "sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
    }
    attempt.update(
        source_tool_calls_sha256=digest(calls),
        executed_tool_calls_sha256=digest(calls),
        source_site_hosts=[],
        runtime_site_host="host.docker.internal",
        replay_calls_binding_sha256=replay_calls_binding_sha256(calls, allowed_hosts=set()),
        evaluation_input_sha256=digest(candidate["recorded_response"]),
    )
    if tamper == "missing":
        del attempt["evidence"]["final_state"]
    elif tamper == "rehash":
        path = setup.root / "final_state.json"
        value = json.loads(path.read_text())
        value["state"]["fields"]["email"]["value"] = "wrong@example.test"
        path.write_text(json.dumps(value))
        attempt["evidence"]["final_state"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    arguments = (
        attempt,
        candidate,
        "host.docker.internal",
        setup.contract.official_data_checksum,
        set(),
    )
    if tamper:
        with pytest.raises(ValueError, match="missing or unsafe|cannot be reproduced"):
            _validate_passing_attempt_evidence(*arguments, state_contract=setup.contract)
    else:
        _validate_passing_attempt_evidence(*arguments, state_contract=setup.contract)


@pytest.fixture
def setup(tmp_path):
    benchmark = _configure_benchmark()
    task = benchmark.get_task(528)
    definition = {
        "task_id": task.task_id,
        "official_task_sha256": digest(task.model_dump(mode="json")),
        "replace_evaluator_index": 1,
        "replace_evaluator_sha256": digest(task.eval[1].model_dump(mode="json")),
        "forbidden_submit_evaluator_index": 2,
        "page_url": "__SHOPPING__/contact",
        "form_selector": "#contact-form",
        "field_selectors": {
            "name": "input[name='name']",
            "email": "input[name='email']",
            "comment": "textarea[name='comment']",
        },
        "allow_trailing_slash": True,
    }
    catalog = tmp_path / "forms.json"
    catalog.write_text(json.dumps({"schema": PROTOCOL, "contracts": [definition]}))
    binding = {
        "path": catalog.name,
        "sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
        "rules": rule_identity(),
        "official_data_checksum": compute_data_file_checksum(benchmark.config.test_data_file),
        "site_urls": {
            str(task.task_id): benchmark.config.render_url(definition["page_url"], task.sites)
        },
    }
    contract = load_contracts(binding, tmp_path, benchmark)[528]
    values = {
        "name": "Emma Lopez",
        "email": "emma.lopez@gmail.com",
        "comment": "I need a refund. Order: 000000180. Amount: $12.99. "
        "Reason: It broke after three days of use.",
    }
    state = {
        "page_url": benchmark.config.render_url(definition["page_url"], task.sites) + "/",
        "form_count": 1,
        "form_id": "contact-form",
        "is_form": True,
        "connected": True,
        "visible": True,
        "fields": {
            name: {
                "count": 1,
                "associated": True,
                "visible": True,
                "connected": True,
                "complete": True,
                "value": value,
            }
            for name, value in values.items()
        },
    }
    page = SimpleNamespace(evaluate=lambda script, arguments: copy.deepcopy(state))
    observation = capture_form(
        page, contract, run_id="run", attempt_id="attempt", engine="chromium"
    )
    har = tmp_path / "network.har"
    har.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {
                                "url": state["page_url"],
                                "method": "GET",
                                "headers": [],
                                "queryString": [],
                            },
                            "response": {
                                "status": 200,
                                "headers": [],
                                "content": {"mimeType": "text/html", "text": ""},
                            },
                        }
                    ]
                }
            }
        )
    )
    return SimpleNamespace(
        benchmark=benchmark,
        task=task,
        definition=definition,
        binding=binding,
        contract=contract,
        observation=observation,
        state=state,
        page=page,
        har=har,
        root=tmp_path,
    )


def score(s, observation=None):
    return evaluate_task(
        s.benchmark,
        s.contract,
        agent_response={"task_type": "MUTATE", "status": "SUCCESS", "retrieved_data": None},
        network_trace=s.har,
        observation=s.observation if observation is None else observation,
        run_id="run",
        attempt_id="attempt",
        engine="chromium",
    )


def test_observed_form_passes_without_synthetic_request_and_leaves_inputs_immutable(setup):
    before = setup.task.model_dump(mode="json")
    network_before = setup.har.read_bytes()
    result = score(setup)
    assert result["score"] == 1
    assert [row["evaluator_name"] for row in result["evaluators_results"]] == [
        "AgentResponseEvaluator",
        "ObservableFormStateEvaluator",
        "NetworkEventEvaluator",
    ]
    assert setup.task.model_dump(mode="json") == before
    assert setup.har.read_bytes() == network_before
    original = setup.benchmark.evaluate_task(
        task_id=528,
        agent_response={"task_type": "MUTATE", "status": "SUCCESS", "retrieved_data": None},
        network_trace=setup.har,
    )
    assert original.score == 0


@pytest.mark.parametrize(
    "path,value",
    [
        (("state", "form_id"), "wrong-form"),
        (("state", "form_count"), 2),
        (("state", "connected"), False),
        (("state", "visible"), False),
        (("state", "fields", "comment", "associated"), False),
        (("state", "fields", "comment", "visible"), False),
        (("state", "fields", "comment", "count"), 2),
        (("state", "fields", "comment", "complete"), False),
        (("state", "fields", "comment", "value"), None),
        (("state", "fields", "comment", "value"), "I need a refund. Order: 000000180..."),
        (("capture_status",), "error"),
        (("run_id",), "previous-run"),
        (("attempt_id",), "previous-attempt"),
    ],
)
def test_unrelated_incomplete_or_cross_run_observations_fail(setup, path, value):
    observation = copy.deepcopy(setup.observation)
    node = observation
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    result = score(setup, observation)
    assert result["score"] == (None if path == ("capture_status",) else 0)


@pytest.mark.parametrize("suffix", ["/other/contact", "/contact-evil", "/contact/index/post"])
def test_url_substrings_do_not_prove_target_page(setup, suffix):
    from urllib.parse import urlsplit

    u = urlsplit(setup.observation["state"]["page_url"])
    setup.observation["state"]["page_url"] = f"{u.scheme}://{u.netloc}{suffix}"
    assert score(setup)["score"] == 0


def test_correct_path_on_wrong_origin_fails(setup):
    setup.observation["state"]["page_url"] = "https://unrelated.example/contact"
    assert score(setup)["score"] == 0


@pytest.mark.parametrize(
    "comment,accepted",
    [
        (
            "I need a refund. Order: #180. Amount: 12.99 dollars. "
            "Reason: It broke after three days of use",
            True,
        ),
        (
            "I need a refund. Order: 181. Amount: $12.99. "
            "Reason: It broke after three days of use.",
            False,
        ),
        (
            "I need a refund. Order: 180. Amount: $12.98. "
            "Reason: It broke after three days of use.",
            False,
        ),
    ],
)
def test_official_regex_semantics_not_exact_string_replacement(setup, comment, accepted):
    setup.observation["state"]["fields"]["comment"]["value"] = comment
    assert (score(setup)["score"] == 1) is accepted


@pytest.mark.parametrize("status", [200, 400])
def test_actual_submission_still_fails_official_forbidden_network_rule(setup, status):
    endpoint = setup.benchmark.config.render_url(
        "__SHOPPING__/contact/index/post", setup.task.sites
    )
    setup.har.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {
                                "url": endpoint,
                                "method": "POST",
                                "headers": [],
                                "queryString": [],
                            },
                            "response": {
                                "status": status,
                                "headers": [],
                                "content": {"mimeType": "text/html", "text": ""},
                            },
                        }
                    ]
                }
            }
        )
    )
    result = score(setup)
    assert result["score"] == 0
    assert result["evaluators_results"][-1]["score"] == 0
    # Returning to the unsubmitted-looking form cannot erase an earlier submit.
    network = json.loads(setup.har.read_text())
    network["log"]["entries"].append(
        {
            "request": {"url": setup.state["page_url"], "method": "GET", "headers": []},
            "response": {
                "status": 200,
                "headers": [],
                "content": {"mimeType": "text/html", "text": ""},
            },
        }
    )
    setup.har.write_text(json.dumps(network))
    assert score(setup)["score"] == 0


def test_missing_capture_fails_closed(setup):
    assert score(setup, {})["status"] == "unsupported"
    assert score(setup, {})["score"] is None

    def broken(*_):
        raise RuntimeError("page disappeared")

    observation = capture_form(
        SimpleNamespace(evaluate=broken),
        setup.contract,
        run_id="run",
        attempt_id="attempt",
        engine="chromium",
    )
    assert observation["capture_status"] == "error"
    assert score(setup, observation)["status"] == "unsupported"
    assert score(setup, observation)["score"] is None


def test_no_profile_opt_in_and_changed_rules_cannot_enable_correction(setup):
    assert load_contracts(None, setup.root, setup.benchmark) == {}
    for key in ("source_sha256", "official_evaluator_checksum", "official_package_version"):
        binding = copy.deepcopy(setup.binding)
        binding["rules"][key] = "stale"
        with pytest.raises(ValueError, match="rule identity"):
            load_contracts(binding, setup.root, setup.benchmark)
    binding = {**setup.binding, "sha256": "stale"}
    with pytest.raises(ValueError, match="catalog hash"):
        load_contracts(binding, setup.root, setup.benchmark)


def test_capture_requests_only_real_form_state(setup):
    calls = []
    page = SimpleNamespace(
        evaluate=lambda script, target: calls.append((script, target)) or setup.state
    )
    capture_form(page, setup.contract, run_id="run", attempt_id="attempt", engine="chromium")
    script, definition = calls[0]
    assert "node.form === form" in script
    assert "form.elements" in script
    assert "fetch(" not in script and "submit(" not in script
    assert definition["form_selector"] == "#contact-form"


def test_runtime_host_rebinding_requires_explicit_new_frozen_site_identity(setup):
    alternate = _configure_benchmark(hostname="declared-alternate.test")
    with pytest.raises(ValueError, match="frozen profile site binding"):
        load_contracts(setup.binding, setup.root, alternate)
    binding = copy.deepcopy(setup.binding)
    binding["site_urls"]["528"] = alternate.config.render_url(
        setup.definition["page_url"], alternate.get_task(528).sites
    )
    rebound = load_contracts(binding, setup.root, alternate)[528]
    assert rebound.page_url == binding["site_urls"]["528"]
    assert rebound.page_url != setup.contract.page_url


def test_explicit_zero_port_is_not_default_origin():
    from harness.webarena_state import _same_url

    assert not _same_url("http://example.test:0/contact", "http://example.test/contact", True)


@pytest.mark.parametrize(
    "key,value", [("http_method", "GET"), ("url", "http://example.test/real-payment")]
)
def test_adapter_cannot_replace_real_or_nonpost_network_rule(setup, key, value):
    from harness.webarena_state import _validate_definition

    data = setup.task.model_dump(mode="json")
    data["eval"][1]["expected"][key] = value
    task = type(setup.task).model_validate(data)
    definition = copy.deepcopy(setup.definition)
    definition["official_task_sha256"] = digest(task.model_dump(mode="json"))
    definition["replace_evaluator_sha256"] = digest(task.eval[1].model_dump(mode="json"))
    with pytest.raises(ValueError, match="synthetic form-state"):
        _validate_definition(task, definition)
