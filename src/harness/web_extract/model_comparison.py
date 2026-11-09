"""Stream a frozen model-only diagnostic through the shared execution scheduler."""

import copy
import fcntl
import json
import math
import time
from pathlib import Path

import requests

from harness import benchmark_execution
from harness.benchmark_execution import ExecutionPolicy, Job, assess_execution, run_jobs
from harness.record_io import bytes_sha256, canonical_json_sha256, file_sha256, json_bytes
from harness.web_extract.pipeline import (
    access_blocker,
    read_json,
    redact,
    require_external_output,
    save_json,
    total_deadline,
)
from harness.web_fetch.config import repository_env

TRANSPORT_ERRORS = ("ConnectionError:", "ConnectTimeout:", "ChunkedEncodingError:")
AUXILIARY_TIMEOUTS = {"verification": 10, "load": 180, "telemetry": 5}


def auxiliary_timeout(model, stage):
    settings = model.get("auxiliary_timeouts", {})
    if not isinstance(settings, dict) or set(settings) - set(AUXILIARY_TIMEOUTS):
        raise ValueError("Unknown auxiliary HTTP deadline configuration")
    value = settings.get(stage, AUXILIARY_TIMEOUTS[stage])
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not (
        math.isfinite(value) and value > 0
    ):
        raise ValueError("Auxiliary HTTP deadlines must be finite positive seconds")
    return value


@total_deadline
def auxiliary_json(url, *, timeout, payload=None):
    """Bound complete HTTP delivery and parsing, including a continuously dripped body."""
    method = requests.get if payload is None else requests.post
    options = {} if payload is None else {"json": payload}
    with method(url, timeout=timeout, stream=True, **options) as response:
        response.raise_for_status()
        raw = bytearray()
        for chunk in response.iter_content(chunk_size=8192):
            raw.extend(chunk)
            if len(raw) > 1_048_576:
                raise ValueError("Auxiliary JSON response exceeds one MiB")
        value = json.loads(raw.decode("utf-8"))
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return value


def request_body(plan, model, case):
    """Reference answers never enter the request; every configuration gets the same text."""
    messages = [
        {"role": "system", "content": plan["system_prompt"]},
        {
            "role": "user",
            "content": "JSON schema:\n"
            + json.dumps(case["schema"], ensure_ascii=False)
            + "\n\nFrozen page text:\n"
            + case["text"],
        },
    ]
    return {**model["parameters"], "model": model["model"], "messages": messages, "stream": True}


def repairable_transport_failure(response):
    """Recover delivery failures, never a completed answer or model budget failure."""
    if response.get("success") or response.get("transport_complete"):
        return False
    if (response.get("error", "").startswith("ChunkedEncodingError:")
            and response.get("http_status") == 200):
        return True
    return (
        not response.get("http_status")
        and not response.get("events") and not response.get("first_any_output_seconds")
        and response.get("error", "").startswith("ConnectionError:")
        and "Connection refused" in response["error"]
    )


def transport_repair_plan(plan_path, run, output, *, repair=None):
    """Freeze transport recovery; completed answers and quality failures stay untouched."""
    plan_path, run = Path(plan_path), Path(run)
    plan = read_json(plan_path)
    binding, index = read_json(run / "binding.json"), read_json(run / "call-hashes.json")
    if binding["plan_sha256"] != file_sha256(plan_path):
        raise ValueError("Transport repair parent plan changed")
    resolved = transport_replacements(plan_path, run, repair) if repair else {}
    predecessors, selected = {}, []
    for call in plan["calls"]:
        key = canonical_json_sha256(call)
        if key in resolved:
            continue
        path = run / "calls" / (key + ".json")
        if not path.exists():
            if key in index or path.with_suffix(".pending").exists():
                raise ValueError("Reconcile missing or pending transport evidence first")
            continue
        if index.get(key) != file_sha256(path):
            raise ValueError("Transport repair parent evidence changed")
        record = read_json(path)
        model = next(m for m in plan["models"] if m["id"] == call["model_id"])
        case = next(c for c in plan["cases"] if c["id"] == call["case_id"])
        request = request_body(plan, model, case)
        if (record["binding"] != {**binding, "call": call,
                                 "request_sha256": canonical_json_sha256(request)}
                or record["request"] != request):
            raise ValueError("Transport repair parent request changed")
        if repairable_transport_failure(record["response"]):
            predecessors[key] = index[key]
            selected.append(call)
    if not selected:
        raise ValueError("No recoverable model transport failures")
    models = {c["model_id"] for c in selected}
    preflights = [c for c in plan["calls"] if c["phase"] == "preflight"
                  and c["model_id"] in models]
    case_ids = {c["case_id"] for c in selected + preflights}
    repaired = copy.deepcopy(plan)
    repaired.update(
        purpose="One final call for each frozen transport-failed result; no quality retries.",
        models=[m for m in plan["models"] if m["id"] in models],
        cases=[c for c in plan["cases"] if c["id"] in case_ids],
        calls=preflights + [c for c in selected if c["phase"] == "comparison"],
        transport_repair={"parent_plan_sha256": file_sha256(plan_path),
                          "parent_index_sha256": file_sha256(run / "call-hashes.json"),
                          "parent_index": index,
                          "previous_repairs": repair_identities(repair),
                          "predecessors": predecessors},
    )
    require_external_output(Path(output))
    save_json(Path(output), repaired, exclusive=True)
    return repaired


def transport_repair_records(plan_path, run, repair_plan_path, repair_run):
    """Verify the complete frozen repair before selecting any replacement response."""
    plan_path, run = Path(plan_path), Path(run)
    repair_plan_path, repair_run = Path(repair_plan_path), Path(repair_run)
    plan, repaired = read_json(plan_path), read_json(repair_plan_path)
    authority = repaired["transport_repair"]
    parent_index = read_json(run / "call-hashes.json")
    snapshot = authority.get("parent_index")
    index_matches = (
        all(parent_index.get(key) == checksum for key, checksum in snapshot.items())
        if snapshot is not None
        else authority["parent_index_sha256"] == file_sha256(run / "call-hashes.json")
    )
    if (authority["parent_plan_sha256"] != file_sha256(plan_path)
            or not index_matches
            or repaired.get("case_contract_sha256") != plan.get("case_contract_sha256")):
        raise ValueError("Transport repair parent binding changed")
    for field in ("timeout_seconds", "execution_policy", "wall_time_budget_seconds"):
        if repaired[field] != plan[field]:
            raise ValueError("Transport repair execution budget changed: " + field)
    binding = read_json(repair_run / "binding.json")
    index = read_json(repair_run / "call-hashes.json")
    if binding["plan_sha256"] != file_sha256(repair_plan_path):
        raise ValueError("Transport repair plan changed after collection")
    expected = authority["predecessors"]
    declared = {canonical_json_sha256(c): c for c in repaired["calls"]}
    comparison = {key for key, call in declared.items() if call["phase"] == "comparison"}
    if not expected or not set(expected) <= set(declared) or not comparison <= set(expected):
        raise ValueError("Transport repair scope differs from its frozen predecessors")
    result = {}
    for call in repaired["calls"]:
        key = canonical_json_sha256(call)
        path = repair_run / "calls" / (key + ".json")
        if index.get(key) != file_sha256(path):
            raise ValueError("Missing or changed transport repair evidence")
        record = read_json(path)
        model = next(m for m in repaired["models"] if m["id"] == call["model_id"])
        case = next(c for c in repaired["cases"] if c["id"] == call["case_id"])
        request = request_body(repaired, model, case)
        if (record["binding"] != {**binding, "call": call,
                                 "request_sha256": canonical_json_sha256(request)}
                or record["request"] != request):
            raise ValueError("Transport repair request binding changed")
        if key not in expected:
            continue
        prior_path = run / "calls" / (key + ".json")
        prior = read_json(prior_path)
        parent_call = next((c for c in plan["calls"] if c == call), None)
        parent_model = next(m for m in plan["models"] if m["id"] == call["model_id"])
        parent_case = next(c for c in plan["cases"] if c["id"] == call["case_id"])
        if (file_sha256(prior_path) != expected[key] or parent_call is None
                or not repairable_transport_failure(prior["response"])
                or prior["request"] != request
                or request_body(plan, parent_model, parent_case) != request
                or model != parent_model or case != parent_case):
            raise ValueError("Transport repair changes a delivered request or quality result")
        durations = [prior["response"].get("elapsed_seconds"),
                     record["response"].get("elapsed_seconds")]
        result[key] = (record, index[key], expected[key],
                       sum(durations) if all(v is not None for v in durations) else None)
    return result


def repair_pairs(repair):
    if not repair:
        return []
    if len(repair) == 2 and all(isinstance(value, (str, Path)) for value in repair):
        return [repair]
    return list(repair)


def repair_identities(repair):
    return [{"plan_sha256": file_sha256(Path(plan)),
             "index_sha256": file_sha256(Path(run) / "call-hashes.json")}
            for plan, run in repair_pairs(repair)]


def transport_replacements(plan_path, run, repair):
    """Compose declared, disjoint recoveries without replaying an earlier final result."""
    result, previous = {}, []
    for repair_plan, repair_run in repair_pairs(repair):
        authority = read_json(repair_plan)["transport_repair"]
        if authority.get("previous_repairs", []) != repair_identities(previous):
            raise ValueError("Supply every prior transport repair in frozen order")
        selected = transport_repair_records(plan_path, run, repair_plan, repair_run)
        if set(selected) & set(result):
            raise ValueError("A logical model result already has its final recovery")
        result.update(selected)
        previous.append((repair_plan, repair_run))
    return result


@total_deadline
def stream_call(payload, *, timeout):
    """A process-level deadline also bounds a stalled or slowly dripped response."""
    model, body = payload
    headers = {}
    if model.get("key_env"):
        headers["Authorization"] = "Bearer " + repository_env()[model["key_env"]]
    started = time.monotonic()
    result = {
        "success": False,
        "transport_complete": False,
        "events": [],
        "first_any_output_seconds": None,
        "first_answer_seconds": None,
    }
    content, reasoning = [], []
    terminal = False
    try:
        with requests.post(
            model["endpoint"],
            json=body,
            headers=headers,
            stream=True,
            timeout=(min(10, timeout), timeout),
        ) as response:
            result.update(
                http_status=response.status_code, headers_seconds=time.monotonic() - started
            )
            if response.status_code >= 400:
                raw_error = bytearray()
                try:
                    for chunk in response.iter_content(chunk_size=1):
                        raw_error.extend(chunk)
                        if len(raw_error) >= 16_384:
                            break
                finally:
                    text = raw_error.decode("utf-8", errors="replace")
                    try:
                        text = json.dumps(redact(json.loads(text)), ensure_ascii=True)
                    except ValueError:
                        text = redact(text)
                    if model.get("key_env"):
                        secret = repository_env().get(model["key_env"])
                        if secret:
                            text = text.replace(secret, "[REDACTED]")
                    result["non_json_response"] = text
            response.raise_for_status()
            for raw in response.iter_lines(chunk_size=1):
                if not raw:
                    continue
                line = raw.decode("utf-8")
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    terminal = True
                    break
                if not line.startswith("{"):
                    continue
                try:
                    event = json.loads(line)
                    json.dumps(event, ensure_ascii=False, allow_nan=False).encode("utf-8")
                except ValueError:
                    result["invalid_stream_line"] = line
                    raise
                elapsed = time.monotonic() - started
                result["events"].append({"seconds": elapsed, "event": event})
                if event.get("error"):
                    raise ValueError(str(event["error"]))
                if model["protocol"] == "ollama":
                    delta = event.get("message", {})
                    answer, thought = delta.get("content", ""), delta.get("thinking", "")
                    terminal = event.get("done", False)
                else:
                    choices = event.get("choices") or []
                    delta = choices[0].get("delta", {}) if choices else {}
                    answer, thought = (
                        delta.get("content") or "",
                        delta.get("reasoning_content") or "",
                    )
                if (answer.strip() or thought.strip()) and result[
                    "first_any_output_seconds"
                ] is None:
                    result["first_any_output_seconds"] = elapsed
                if answer.strip() and result["first_answer_seconds"] is None:
                    result["first_answer_seconds"] = elapsed
                content.append(answer)
                reasoning.append(thought)
            if not terminal:
                raise ValueError("Stream ended without its completion marker")
            result["transport_complete"] = True
            data = json.loads("".join(content))
            json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if not isinstance(data, dict):
                raise ValueError("Extraction must return one JSON object")
            result["data"] = data
            result["success"] = True
    except Exception as exc:
        # Retain failures as physical calls; a resume must not silently retry them.
        result["error"] = redact(f"{type(exc).__name__}: {exc}")
    elapsed = time.monotonic() - started
    result.update(
        content="".join(content),
        thinking="".join(reasoning),
        elapsed_seconds=elapsed,
        complete_json_seconds=elapsed if result["success"] else None,
    )
    return result


def _execute(payload):
    model, body, timeout = payload
    result = stream_call((model, body), timeout=timeout)
    if model.get("telemetry_endpoint"):
        started = time.monotonic()
        try:
            result["loaded_models_after"] = auxiliary_json(
                model["telemetry_endpoint"], timeout=auxiliary_timeout(model, "telemetry")
            )
        except (requests.RequestException, TimeoutError, ValueError) as exc:
            result["telemetry_error"] = type(exc).__name__
        finally:
            result["telemetry_seconds"] = time.monotonic() - started
    return result


def verify_models(models):
    """Check the controlled server and exact installed weights, including on resume."""
    observed = {}
    for model in models.values():
        if model["protocol"] != "ollama":
            continue
        runtime = model["runtime"]
        base = runtime["base"].rstrip("/")
        if model["endpoint"] != base + "/api/chat":
            raise ValueError("Model identity does not bind the inference endpoint")
        timeout = auxiliary_timeout(model, "verification")
        key = (base, timeout)
        if key not in observed:
            version = auxiliary_json(base + "/api/version", timeout=timeout)
            tags = auxiliary_json(base + "/api/tags", timeout=timeout)
            observed[key] = (version["version"], tags["models"])
        version, tags = observed[key]
        actual = next((m["digest"] for m in tags if m["name"] == model["model"]), None)
        if version != runtime["version"] or actual != runtime["digest"]:
            raise ValueError("Model weights or Ollama version differ from the frozen runtime")


def collect(plan_path, output, *, phase, repair=None):
    """Reserve before dispatch, checkpoint every terminal call, and reuse completed work."""
    plan_path, output = Path(plan_path), Path(output)
    require_external_output(output)
    plan = read_json(plan_path)
    policy = ExecutionPolicy.from_dict(plan["execution_policy"])
    if policy.backend != "process":
        raise ValueError("Streaming wall deadlines require the process execution backend")
    models = {m["id"]: m for m in plan["models"]}
    cases = {c["id"]: c for c in plan["cases"]}
    if len(models) != len(plan["models"]) or len(cases) != len(plan["cases"]):
        raise ValueError("Duplicate model or case identity")
    for model in models.values():
        if model["protocol"] not in ("ollama", "openai"):
            raise ValueError("Unsupported streaming protocol")
        for stage in AUXILIARY_TIMEOUTS:
            auxiliary_timeout(model, stage)
        if model.get("key_env") and not repository_env().get(model["key_env"]):
            raise ValueError("Missing model credential in the repository .env")
    verify_models(models)
    for case in cases.values():
        if bytes_sha256(case["text"].encode("utf-8")) != case["text_sha256"]:
            raise ValueError("Frozen model input text changed")
    calls = [c for c in plan["calls"] if c["phase"] == phase]
    if not calls:
        raise ValueError("No declared calls for this phase")
    output.mkdir(parents=True, exist_ok=True)
    plan_hash = file_sha256(plan_path)
    binding = {
        "plan_sha256": plan_hash,
        "collector_sha256": file_sha256(Path(__file__)),
        "scheduler_sha256": file_sha256(Path(benchmark_execution.__file__)),
    }
    with (output / "execution.lock").open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        bound = output / "binding.json"
        if bound.exists():
            if read_json(bound) != binding:
                raise ValueError("Model diagnostic contract or collector changed")
        else:
            save_json(bound, binding, exclusive=True)
        index_path = output / "call-hashes.json"
        index = read_json(index_path) if index_path.exists() else {}
        for pending in (output / "calls").glob("*.pending"):
            target = pending.with_suffix(".json")
            if not target.exists():
                continue
            receipt, reservation = read_json(target), read_json(pending)
            checksum = reservation.pop("completed_sha256", None)
            if (
                checksum != file_sha256(target)
                or receipt["binding"] != reservation
                or any(reservation.get(k) != v for k, v in binding.items())
                or canonical_json_sha256(receipt["request"]) != reservation["request_sha256"]
                or index.get(pending.stem) not in (None, checksum)
            ):
                raise ValueError("Cannot reconcile a changed or unbound model call")
            index[pending.stem] = checksum
            save_json(index_path, index)
            pending.unlink()
        replacements = transport_replacements(plan_path, output, repair) if repair else {}
        if phase == "comparison":
            for call in (c for c in plan["calls"] if c["phase"] == "preflight"):
                key = canonical_json_sha256(call)
                target = output / "calls" / (key + ".json")
                if (
                    not target.exists()
                    or index.get(key) != file_sha256(target)
                    or not (replacements[key][0] if key in replacements else read_json(target))[
                        "response"].get("transport_complete")
                ):
                    raise ValueError("Complete the declared model preflight before comparison")
            if {c["model_id"] for c in plan["calls"] if c["phase"] == "preflight"} != set(models):
                raise ValueError("Every model requires a declared preflight")
            assessment = read_json(output / "execution-plan.json")
            if assessment["binding"] != binding:
                raise ValueError("Execution preflight differs from this diagnostic")
            save_json(
                output / "execution-assessment.json",
                assess_execution(assessment, evidence_root=output),
            )
        clock = output / (phase + "-clock.json")
        if not clock.exists():
            save_json(clock, {"started_at": time.time()}, exclusive=True)
        jobs, prepared = [], {}
        seen = set()
        for call in calls:
            model, case = models[call["model_id"]], cases[call["case_id"]]
            body = request_body(plan, model, case)
            key = canonical_json_sha256(call)
            if key in seen:
                raise ValueError("Duplicate physical call declaration")
            seen.add(key)
            target = output / "calls" / (key + ".json")
            expected = {**binding, "call": call, "request_sha256": canonical_json_sha256(body)}
            if target.exists():
                if read_json(target)["binding"] != expected or index.get(key) != file_sha256(
                    target
                ):
                    raise ValueError("Completed model call differs from frozen request")
                prior = (replacements[key][0] if key in replacements else read_json(target))[
                    "response"]
                if access_blocker({"http_status": prior.get("http_status"), "response": prior}):
                    raise ValueError("Resolve the retained account denial before a new contract")
                if repairable_transport_failure(prior):
                    raise ValueError("Resolve retained transport failure through a frozen repair")
                if prior.get("error", "").startswith(TRANSPORT_ERRORS):
                    raise ValueError("Reconcile retained transport failure before continuing")
                continue
            pending = target.with_suffix(".pending")
            if pending.exists():
                raise ValueError("Unresolved physical call; reconcile it before resuming")
            prepared[key] = (target, pending, expected, body)
            jobs.append(Job(key, tuple(model["resources"]), (model, body, plan["timeout_seconds"])))

        current_model = None

        def prepare(job):
            nonlocal current_model
            model, _, _ = job.payload
            weights = canonical_json_sha256(
                {
                    "model": model["model"],
                    "endpoint": model.get("load_endpoint"),
                    "options": model["parameters"].get("options", {}),
                }
            )
            if model.get("load_endpoint") and weights != current_model:
                load_body = {
                    "model": model["model"],
                    "prompt": "",
                    "stream": False,
                    "keep_alive": "15m",
                    "options": model["parameters"].get("options", {}),
                }
                started = time.monotonic()
                response = auxiliary_json(
                    model["load_endpoint"], payload=load_body,
                    timeout=auxiliary_timeout(model, "load"),
                )
                save_json(
                    output / "loads" / (str(time.time_ns()) + ".json"),
                    {
                        "model_id": model["id"],
                        "phase": phase,
                        "request": load_body,
                        "response": response,
                        "seconds": time.monotonic() - started,
                    },
                    exclusive=True,
                )
                current_model = weights
            _, pending, expected, _ = prepared[job.key]
            save_json(pending, expected, exclusive=True)
            return job.payload

        def commit(job, result, error):
            target, pending, expected, body = prepared[job.key]
            if error:
                result = {"success": False, "error": redact(f"{type(error).__name__}: {error}")}
            receipt = {"binding": expected, "request": body, "response": result}
            save_json(pending, {**expected, "completed_sha256": bytes_sha256(json_bytes(receipt))})
            save_json(target, receipt, exclusive=True)
            index[job.key] = file_sha256(target)
            save_json(index_path, index)
            pending.unlink()
            print(
                json.dumps(
                    {
                        "call": expected["call"],
                        "success": result["success"],
                        "seconds": result.get("complete_json_seconds"),
                    }
                ),
                flush=True,
            )
            blocker = access_blocker({"http_status": result.get("http_status"), "response": result})
            if blocker:
                raise RuntimeError("Model account blocked: " + blocker)
            if result.get("error", "").startswith(TRANSPORT_ERRORS):
                raise RuntimeError("Model transport failed; retained call requires reconciliation")

        run_jobs(jobs, _execute, commit, policy, prepare=prepare)
        verify_models(models)
        if phase == "preflight" and not (output / "execution-plan.json").exists():
            elapsed = time.time() - read_json(clock)["started_at"]
            setup = sum(
                read_json(p)["seconds"]
                for p in (output / "loads").glob("*.json")
                if read_json(p)["phase"] == phase
            )
            sample = {
                "completed_calls": len(calls),
                "wall_seconds": max(0.000001, elapsed - setup),
                "max_in_flight": policy.max_in_flight,
                "elapsed_wall_seconds": elapsed,
                "separately_recorded_model_load_seconds": setup,
            }
            save_json(output / "preflight.json", sample)
            execution = {
                "binding": binding,
                "execution_policy": plan["execution_policy"],
                "planned_calls": sum(c["phase"] == "comparison" for c in plan["calls"]),
                "setup_seconds": setup,
                "wall_time_budget_seconds": plan["wall_time_budget_seconds"],
                "preflight": {
                    **sample,
                    "evidence_path": "preflight.json",
                    "evidence_sha256": file_sha256(output / "preflight.json"),
                },
            }
            if plan.get("over_budget_reason"):
                execution["over_budget_reason"] = plan["over_budget_reason"]
            save_json(output / "execution-plan.json", execution)
    return {"phase": phase, "declared_calls": len(calls), "new_calls": len(jobs)}
