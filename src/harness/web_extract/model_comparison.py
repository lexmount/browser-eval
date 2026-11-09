"""Stream a frozen model-only diagnostic through the shared execution scheduler."""

import fcntl
import json
import time
from pathlib import Path

import requests

from harness import benchmark_execution
from harness.benchmark_execution import ExecutionPolicy, Job, assess_execution, run_jobs
from harness.record_io import bytes_sha256, canonical_json_sha256, file_sha256
from harness.web_extract.pipeline import (
    access_blocker,
    read_json,
    redact,
    require_external_output,
    save_json,
    total_deadline,
)
from harness.web_fetch.config import repository_env


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
                    json.dumps(event, allow_nan=False)
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
            json.dumps(data, allow_nan=False)
            if not isinstance(data, dict):
                raise ValueError("Extraction must return one JSON object")
            result["data"] = data
            result["success"] = True
    except Exception as exc:
        # Retain failures as physical calls; a resume must not silently retry them.
        result["error"] = redact(f"{type(exc).__name__}: {exc}")
    result.update(
        content="".join(content),
        thinking="".join(reasoning),
        complete_json_seconds=time.monotonic() - started,
    )
    return result


def _execute(payload):
    model, body, timeout = payload
    result = stream_call((model, body), timeout=timeout)
    if model.get("telemetry_endpoint"):
        try:
            result["loaded_models_after"] = requests.get(
                model["telemetry_endpoint"], timeout=5
            ).json()
        except requests.RequestException as exc:
            result["telemetry_error"] = type(exc).__name__
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
        if base not in observed:
            version = requests.get(base + "/api/version", timeout=10)
            tags = requests.get(base + "/api/tags", timeout=10)
            version.raise_for_status()
            tags.raise_for_status()
            observed[base] = (version.json()["version"], tags.json()["models"])
        version, tags = observed[base]
        actual = next((m["digest"] for m in tags if m["name"] == model["model"]), None)
        if version != runtime["version"] or actual != runtime["digest"]:
            raise ValueError("Model weights or Ollama version differ from the frozen runtime")


def collect(plan_path, output, *, phase):
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
    with (output / "execution.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        bound = output / "binding.json"
        if bound.exists():
            if read_json(bound) != binding:
                raise ValueError("Model diagnostic contract or collector changed")
        else:
            save_json(bound, binding, exclusive=True)
        index_path = output / "call-hashes.json"
        index = read_json(index_path) if index_path.exists() else {}
        if phase == "comparison":
            for call in (c for c in plan["calls"] if c["phase"] == "preflight"):
                key = canonical_json_sha256(call)
                target = output / "calls" / (key + ".json")
                if (
                    not target.exists()
                    or index.get(key) != file_sha256(target)
                    or not read_json(target)["response"].get("transport_complete")
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
                prior = read_json(target)["response"]
                if access_blocker({"http_status": prior.get("http_status"), "response": prior}):
                    raise ValueError("Resolve the retained account denial before a new contract")
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
                    "options": model["parameters"]["options"],
                }
                started = time.monotonic()
                response = requests.post(model["load_endpoint"], json=load_body, timeout=180)
                response.raise_for_status()
                save_json(
                    output / "loads" / (str(time.time_ns()) + ".json"),
                    {
                        "model_id": model["id"],
                        "phase": phase,
                        "request": load_body,
                        "response": response.json(),
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
            save_json(
                target, {"binding": expected, "request": body, "response": result}, exclusive=True
            )
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
