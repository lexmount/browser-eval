"""Run CowAgent autonomously against official self-hosted WebArena-Verified sites."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import docker
from dotenv import dotenv_values
from json_repair import repair_json
from webarena_verified import WebArenaVerified
from webarena_verified.environments.container.manager import ContainerManager

from harness.browser_use.model import OpenAIResponsesModel
from harness.browser_use.selection import PilotTask, load_pilot, validate_against_official
from harness.container_isolation import require_linux_cgroup_v2
from harness.environments.browser import (
    ENGINE_PORTS,
    configured_browser_images,
    published_endpoint,
    start_browser_container,
    wait_cdp,
)
from harness.environments.webarena.har import CdpHarRecorder
from harness.environments.webarena.runtime import (
    PHYSICAL_EXECUTION_LOCK_NAMESPACE,
    SITE_PORTS,
    allow_official_container_hostname,
    browser_session_contract,
    browser_tool_config,
    cdp_metadata,
    configure_benchmark,
    container_host_gateway,
    docker_daemon_lock_namespace,
    docker_image_metadata,
    environment_prepare_receipt,
    login_context,
    physical_execution_lock,
    physical_execution_lock_path,
    prepare_browser_viewport,
    prepare_sites,
    require_clean_git_checkout,
    resource_summary,
    site_runtime_compatibility,
    verify_session_viewport,
)
from harness.record_io import load_jsonl
from harness.resource_accounting import (
    DockerStatsResources,
    ResourceSampler,
)

ENGINE_IMAGES = configured_browser_images()

SCHEMA = "lex_browser_use_attempt/1"


def _load_repo_env(repo_root: Path) -> dict[str, str]:
    """Load repository defaults, with explicit process overrides for automation."""
    env_file = repo_root / ".env"
    values = (
        {str(key): str(value) for key, value in dotenv_values(env_file).items() if value}
        if env_file.exists()
        else {}
    )
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "COWAGENT_REPO"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def _limit_cowagent_transport_retries(executor_class: Any) -> None:
    """Limit CowAgent API errors to the initial request plus one retry."""
    if getattr(executor_class, "_lex_browser_eval_retry_limit", False):
        return
    original = executor_class._call_llm_stream

    def limited(
        executor: Any,
        retry_on_empty: bool = True,
        retry_count: int = 0,
        max_retries: int = 1,
        _overflow_stage: int = 0,
    ) -> Any:
        return original(
            executor,
            retry_on_empty=retry_on_empty,
            retry_count=retry_count,
            max_retries=min(int(max_retries), 1),
            _overflow_stage=_overflow_stage,
        )

    executor_class._call_llm_stream = limited
    executor_class._lex_browser_eval_retry_limit = True


def _system_prompt() -> str:
    return """You are the autonomous web agent under evaluation. Use only the browser tool.
Complete the objective from the supplied start URLs. Do not ask for help, download files, or
change state unless the objective requires it. When finished, return only one JSON object with
task_type (RETRIEVE, MUTATE, or NAVIGATE), status (SUCCESS, NOT_FOUND_ERROR,
ACTION_NOT_ALLOWED_ERROR, PERMISSION_DENIED_ERROR, DATA_VALIDATION_ERROR, or UNKNOWN_ERROR),
retrieved_data, and error_details. Treat search and list pages as candidate sets, not proof that
every result satisfies the objective: verify every included item against all stated constraints
using its title, category, breadcrumb, detail page, or other visible site evidence before sorting,
aggregating, or mutating. Before returning, check semantic eligibility, completeness, arithmetic,
and any required saved or submitted state. A successful RETRIEVE returns a list. If an exhaustive
RETRIEVE finds no matching item, return NOT_FOUND_ERROR with retrieved_data null. For MUTATE and
NAVIGATE, retrieved_data is null."""


def _exhausted_agent_steps(events: list[dict[str, Any]], max_steps: int) -> bool:
    """Return whether CowAgent used its final decision turn for another tool call.

    CowAgent's max-step fallback asks the model for a prose summary without tools. That
    fallback is not a benchmark completion and may claim success even though the last
    browser action has not been observed or verified.
    """

    turns = [event for event in events if event.get("type") == "turn_end"]
    if not turns:
        return False
    data = turns[-1].get("data") or {}
    return len(turns) >= max_steps and bool(data.get("has_tool_calls"))


def _max_steps_response(task_type: str, max_steps: int) -> dict[str, Any]:
    return {
        "task_type": task_type.upper(),
        "status": "UNKNOWN_ERROR",
        "retrieved_data": None,
        "error_details": (
            f"Agent reached the {max_steps}-step decision limit before producing "
            "a tool-free, post-action completion."
        ),
    }


def _user_prompt(task: Any, start_urls: list[str]) -> str:
    response_eval = next(item for item in task.eval if item.evaluator == "AgentResponseEvaluator")
    return (
        "Task objective:\n"
        + task.intent
        + "\n\nStart URLs:\n"
        + "\n".join(f"- {url}" for url in start_urls)
        + "\n\nOfficial response contract (schema only; this does not reveal the answer):\n"
        + f"- task_type: {str(response_eval.expected.task_type).upper()}\n"
        + "- retrieved_data JSON Schema: "
        + json.dumps(
            response_eval.model_dump(mode="json")["results_schema"],
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def _parse_agent_response(text: str) -> dict[str, Any] | str:
    try:
        repaired = repair_json(text, return_objects=True)
    except Exception:
        return text
    return repaired if isinstance(repaired, dict) else text


def _run_attempt(  # noqa: PLR0913
    *,
    lock_path: Path,
    lock_namespace: str = PHYSICAL_EXECUTION_LOCK_NAMESPACE,
    **kwargs: Any,
) -> dict[str, Any]:
    with physical_execution_lock(lock_path) as lock_wait_ms:
        return _run_attempt_locked(
            **kwargs,
            lock_wait_ms=lock_wait_ms,
            lock_namespace=lock_namespace,
        )


def _run_attempt_locked(  # noqa: PLR0913
    *,
    benchmark: WebArenaVerified,
    selected: PilotTask,
    engine: str,
    attempt: int,
    endpoint: str,
    container: Any,
    cowagent_repo: Path,
    output_dir: Path,
    model: OpenAIResponsesModel,
    sample_interval_ms: int,
    max_agent_steps: int,
    lock_wait_ms: float,
    lock_namespace: str,
) -> dict[str, Any]:
    task = benchmark.get_task(selected.task_id)
    sites = tuple(task.sites)
    start_urls = list(benchmark.config.render_url(list(task.start_urls), list(task.sites)))

    attempt_dir = output_dir / engine / str(selected.task_id) / f"attempt-{attempt}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    prepare_path = attempt_dir / "environment_prepare.json"
    prepare_started = time.perf_counter()
    prepare_receipt = environment_prepare_receipt(
        sites, lock_wait_ms=lock_wait_ms, lock_namespace=lock_namespace
    )
    try:
        prepare_sites(sites)
        prepare_receipt["status"] = "ready"
    except Exception as exc:
        prepare_receipt["status"] = "failed"
        prepare_receipt["error"] = str(exc)
        raise
    finally:
        prepare_receipt["duration_ms"] = round((time.perf_counter() - prepare_started) * 1000, 3)
        prepare_path.write_text(
            json.dumps(prepare_receipt, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if str(cowagent_repo) not in sys.path:
        sys.path.insert(0, str(cowagent_repo))
    from agent.protocol.agent import Agent  # noqa: PLC0415
    from agent.protocol.agent_stream import AgentStreamExecutor  # noqa: PLC0415
    from agent.tools.browser.browser_tool import BrowserTool  # noqa: PLC0415

    _limit_cowagent_transport_retries(AgentStreamExecutor)

    trace_path = attempt_dir / "agent_trace.zip"
    har_path = attempt_dir / "network.har"
    response_path = attempt_dir / "agent_response.json"
    eval_path = attempt_dir / "eval_result.json"
    events_path = attempt_dir / "agent_events.json"
    tool = BrowserTool(config=browser_tool_config(endpoint))
    allow_official_container_hostname(tool)
    events: list[dict[str, Any]] = []
    resources = DockerStatsResources(container)
    before = resources.counters()
    sampler = ResourceSampler(resources, interval_ms=sample_interval_ms)
    raw_response = ""
    error = ""
    prepared = False
    viewport_evidence: dict[str, Any] = {}
    recorder = CdpHarRecorder()
    sampler_started = False
    browser_session_setup_ms = 0.0
    browser_session_readiness_retries = 0
    agent_run_ms = 0.0
    evidence_finalize_ms = 0.0
    evaluation_ms = 0.0
    cleanup_errors: list[str] = []
    started = time.perf_counter()
    try:
        session_setup_started = time.perf_counter()
        model.reset()
        service = tool._get_service()
        service._submit(lambda: None)
        viewport_evidence = prepare_browser_viewport(service)
        browser_session_readiness_retries = login_context(
            service, sites, benchmark, start_urls, viewport_evidence
        )
        verify_session_viewport(service, viewport_evidence, "after_login")
        service._submit(
            lambda: (
                recorder.start(service._context),
                service._context.tracing.start(screenshots=True, snapshots=True),
            )
        )
        prepared = True
        browser_session_setup_ms = (time.perf_counter() - session_setup_started) * 1000
        sampler.start()
        sampler_started = True
        agent = Agent(
            system_prompt="",
            description="WebArena-Verified CowAgent",
            model=model,
            tools=[tool],
            output_mode="logger",
            max_steps=max_agent_steps,
            enable_skills=False,
            skip_context_files=True,
        )
        # CowAgent rebuilds its base prompt at run time; the suffix is the supported
        # way to retain benchmark-specific completion and output constraints.
        agent.extra_system_suffix = _system_prompt()
        agent_started = time.perf_counter()
        try:
            raw_response = agent.run_stream(
                _user_prompt(task, start_urls), on_event=events.append, clear_history=True
            )
        finally:
            agent_run_ms = (time.perf_counter() - agent_started) * 1000
        if _exhausted_agent_steps(events, max_agent_steps):
            response_eval = next(
                item for item in task.eval if item.evaluator == "AgentResponseEvaluator"
            )
            raw_response = json.dumps(
                _max_steps_response(str(response_eval.expected.task_type), max_agent_steps)
            )
        verify_session_viewport(service, viewport_evidence, "after_agent")
        finalize_started = time.perf_counter()
        try:
            service._submit(
                lambda: (
                    service._page.wait_for_timeout(500),
                    recorder.stop(service._context, har_path),
                    service._context.tracing.stop(path=str(trace_path)),
                )
            )
        finally:
            evidence_finalize_ms = (time.perf_counter() - finalize_started) * 1000
    except Exception:
        error = traceback.format_exc()
        finalize_started = time.perf_counter()
        try:
            service = tool._get_service()
            service._submit(
                lambda: (
                    recorder.stop(service._context, har_path),
                    service._context.tracing.stop(path=str(trace_path)),
                )
            )
        except Exception as exc:
            cleanup_errors.append(f"HAR/trace stop failed: {exc}")
        finally:
            evidence_finalize_ms += (time.perf_counter() - finalize_started) * 1000
    finally:
        if sampler_started:
            try:
                sampler.stop()
            except Exception as exc:
                cleanup_errors.append(f"resource sampler stop failed: {exc}")
        duration_ms = (time.perf_counter() - started) * 1000
        try:
            after = resources.counters()
        except Exception as exc:
            after = None
            cleanup_errors.append(f"final resource counters failed: {exc}")
        try:
            tool.close()
        except Exception as exc:
            cleanup_errors.append(f"browser tool close failed: {exc}")

    if not prepared or not viewport_evidence.get("verified"):
        raise RuntimeError(
            f"{engine} task {selected.task_id}: environment precondition failed\n{error}"
        )

    parsed_response = _parse_agent_response(raw_response)
    response_path.write_text(
        json.dumps(parsed_response, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    events_path.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    if har_path.exists():
        evaluation_started = time.perf_counter()
        evaluation = benchmark.evaluate_task(
            task_id=selected.task_id,
            agent_response=parsed_response,
            network_trace=har_path,
        )
        eval_data = evaluation.model_dump(mode="json")
        evaluation_ms = (time.perf_counter() - evaluation_started) * 1000
    else:
        eval_data = {"status": "error", "score": 0.0, "error_msg": error or "HAR missing"}
    eval_path.write_text(json.dumps(eval_data, ensure_ascii=False, indent=2), encoding="utf-8")
    resource = (
        resource_summary(resources, sampler, before, after, duration_ms)
        if sampler.samples and after is not None
        else {}
    )
    return {
        "schema": SCHEMA,
        "model": model.model,
        "engine": engine,
        "task_id": selected.task_id,
        "tier": selected.tier,
        "estimated_min_steps": selected.estimated_min_steps,
        "attempt": attempt,
        "is_retry": attempt > 1,
        "status": eval_data.get("status", "error"),
        "score": float(eval_data.get("score") or 0.0),
        "duration_ms": round(duration_ms, 3),
        "timings": {
            "environment_prepare_ms": prepare_receipt["duration_ms"],
            "browser_session_setup_ms": round(browser_session_setup_ms, 3),
            "agent_run_ms": round(agent_run_ms, 3),
            "evidence_finalize_ms": round(evidence_finalize_ms, 3),
            "evaluation_ms": round(evaluation_ms, 3),
            "measured_run_wall_ms": round(duration_ms, 3),
        },
        "browser_session_readiness_retries": browser_session_readiness_retries,
        "viewport": viewport_evidence,
        "agent_response": parsed_response,
        "error": error,
        "cleanup_errors": cleanup_errors,
        "har_capture_errors": recorder.errors,
        "resource": resource,
        "model_usage": {
            **model.usage,
            "request_count": model.request_count,
            "request_attempt_count": model.request_attempt_count,
        },
        "model_transport_errors": model.transport_errors,
        "paths": {
            "network_har": str(har_path),
            "agent_trace": str(trace_path),
            "response": str(response_path),
            "evaluation": str(eval_path),
            "events": str(events_path),
            "environment_prepare": str(prepare_path),
        },
    }


def _load_completed_attempts(
    path: Path, expected_model: str | None = None
) -> dict[tuple[str, str, int, int], dict[str, Any]]:
    """Load durable attempt rows so an interrupted multi-hour pilot can resume safely."""
    if not path.exists():
        return {}
    completed: dict[tuple[str, str, int, int], dict[str, Any]] = {}

    for record_number, row in enumerate(load_jsonl(path, skip_blank=True), 1):
        model = str(row.get("model") or expected_model or "")
        if not model:
            raise ValueError(
                f"attempt does not identify its model at {path}, record {record_number}"
            )
        if expected_model and model != expected_model:
            raise ValueError(
                f"attempt model differs from the evidence cohort at {path}, record {record_number}"
            )
        row["model"] = model
        key = (model, str(row["engine"]), int(row["task_id"]), int(row["attempt"]))
        if key in completed:
            raise ValueError(f"duplicate attempt at {path}, record {record_number}: {key}")
        completed[key] = row
    return completed


def _retry_required(primary: dict[str, Any]) -> bool:
    """Apply the declared one-retry rule identically to fresh and resumed runs.

    The protocol records ``failure_retries: 1`` and the release check accepts a
    retry after any primary with a score below 1.0, so every non-passing primary,
    whatever its evaluator status, receives exactly one retry.
    """
    return float(primary.get("score") or 0) != 1.0


def _write_or_validate_run_config(path: Path, planned: dict[str, Any]) -> None:
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != planned:
            raise ValueError("resume configuration differs from the existing evidence cohort")
        return
    path.write_text(json.dumps(planned, ensure_ascii=False, indent=2), encoding="utf-8")


def _scheduled_tasks(tasks: tuple[PilotTask, ...], max_tasks: int | None) -> tuple[PilotTask, ...]:
    """Limit only this invocation; a later invocation resumes the same pre-registered cohort."""
    if max_tasks is None:
        return tasks
    if max_tasks < 1 or max_tasks > len(tasks):
        raise ValueError(f"--max-tasks must be between 1 and {len(tasks)}")
    return tasks[:max_tasks]


def run(args: argparse.Namespace) -> Path:  # noqa: PLR0915
    repo_root = Path(args.repo_root).resolve()
    repo_env = _load_repo_env(repo_root)
    env_file = Path(args.env_file).expanduser().resolve() if args.env_file else None
    env = (
        {key: value for key, value in dotenv_values(env_file).items() if value is not None}
        if env_file
        else repo_env
    )
    api_key = env.get("OPENAI_API_KEY")
    if not api_key:
        source = env_file or repo_root / ".env"
        raise RuntimeError(f"OPENAI_API_KEY is required in {source}")
    cowagent_repo = Path(args.cowagent_repo or repo_env.get("COWAGENT_REPO", "")).resolve()
    if not (cowagent_repo / "agent/protocol/agent.py").exists():
        raise RuntimeError("--cowagent-repo must point to a CowAgent checkout")
    harness_commit = require_clean_git_checkout(repo_root)
    cowagent_commit = require_clean_git_checkout(cowagent_repo)

    output_dir = Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    selection = load_pilot(args.selection)
    benchmark = configure_benchmark()
    resolved = validate_against_official(selection, benchmark)
    model = OpenAIResponsesModel(
        api_key=api_key,
        base_url=env.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=args.model,
        reasoning_effort="low",
    )
    model.verify_access()

    client = docker.from_env()
    require_linux_cgroup_v2(client)
    docker_info = client.info()
    physical_execution_lock_namespace = docker_daemon_lock_namespace(docker_info)
    execution_lock_path = physical_execution_lock_path(physical_execution_lock_namespace)
    run_id = uuid.uuid4().hex[:12]
    containers: dict[str, Any] = {}
    networks: dict[str, Any] = {}
    endpoints: dict[str, str] = {}
    attempts_file = output_dir / "attempts.jsonl"
    run_config_file = output_dir / "run-config.json"
    completed = _load_completed_attempts(attempts_file, args.model)
    image_metadata: dict[str, dict[str, Any]] = {}
    site_image_metadata = {
        site.value: {
            "reference": ContainerManager(
                site=site, hostname="host.docker.internal"
            ).config.docker_img,
            **docker_image_metadata(
                client.images.get(
                    ContainerManager(site=site, hostname="host.docker.internal").config.docker_img
                )
            ),
        }
        for site in SITE_PORTS
    }
    browser_metadata: dict[str, dict[str, str]] = {}
    site_access_host = ""
    docker_architecture = str(docker_info.get("Architecture") or "")
    runtime_compatibility = site_runtime_compatibility(docker_architecture)
    try:
        for engine in ENGINE_IMAGES:
            network = client.networks.create(f"lex-browser-use-{run_id}-{engine}")
            networks[engine] = network
            container = start_browser_container(
                client,
                image=ENGINE_IMAGES[engine], cdp_port=ENGINE_PORTS[engine],
                engine=engine,
                run_id=f"agent-{run_id}",
                cpus=args.cpus,
                memory=args.memory,
                network=network.name,
            )
            containers[engine] = container
            container.reload()
            image_metadata[engine] = docker_image_metadata(container.image)
            http_endpoint = published_endpoint(
                container, engine=engine, cdp_port=ENGINE_PORTS[engine]
            )
            endpoints[engine] = wait_cdp(http_endpoint, container=container)
            browser_metadata[engine] = cdp_metadata(http_endpoint)

        gateway_addresses = {
            container_host_gateway(container) for container in containers.values()
        }
        if len(gateway_addresses) != 1:
            raise RuntimeError(f"browser containers disagree on host gateway: {gateway_addresses}")
        site_access_host = gateway_addresses.pop()
        benchmark = configure_benchmark(hostname=site_access_host)
        resolved = validate_against_official(selection, benchmark)
        _write_or_validate_run_config(
            run_config_file,
            {
                "schema": "lex_browser_use_run_config/1",
                "selection_sha256": selection.sha256,
                "browser_eval_commit": harness_commit,
                "cowagent_commit": cowagent_commit,
                "browser_session_contract": browser_session_contract(),
                "model": args.model,
                "reasoning_effort": "low",
                "model_transport_retries": 1,
                "max_agent_steps": args.max_agent_steps,
                "provider_base_url": env.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                "browser_images": image_metadata,
                "site_images": site_image_metadata,
                "browser_versions": browser_metadata,
                "site_access_host": site_access_host,
                "docker_context": subprocess.run(
                    ["docker", "context", "show"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                "environment_lane": {
                    "lock_namespace": physical_execution_lock_namespace,
                    "isolation_unit": "docker-daemon-and-fixed-site-ports",
                },
                "site_runtime_compatibility": runtime_compatibility,
            },
        )

        with attempts_file.open("a", encoding="utf-8") as stream:
            for task_index, selected in enumerate(
                _scheduled_tasks(selection.tasks, args.max_tasks)
            ):
                order = ("chromium", "moli") if task_index % 2 == 0 else ("moli", "chromium")
                for engine in order:
                    for attempt in (1, 2):
                        primary_key = (args.model, engine, selected.task_id, 1)
                        if attempt == 2 and not _retry_required(completed[primary_key]):
                            break
                        key = (args.model, engine, selected.task_id, attempt)
                        if key in completed:
                            print(
                                json.dumps(
                                    {
                                        "engine": engine,
                                        "task_id": selected.task_id,
                                        "attempt": attempt,
                                        "status": "resume-skip",
                                        "score": completed[key]["score"],
                                    }
                                ),
                                flush=True,
                            )
                            continue
                        row = _run_attempt(
                            lock_path=execution_lock_path,
                            lock_namespace=physical_execution_lock_namespace,
                            benchmark=benchmark,
                            selected=selected,
                            engine=engine,
                            attempt=attempt,
                            endpoint=endpoints[engine],
                            container=containers[engine],
                            cowagent_repo=cowagent_repo,
                            output_dir=output_dir,
                            model=model,
                            sample_interval_ms=args.sample_interval_ms,
                            max_agent_steps=args.max_agent_steps,
                        )
                        row["run_id"] = run_id
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                        stream.flush()
                        completed[key] = row
                        visible = {
                            key: row[key]
                            for key in ("engine", "task_id", "attempt", "status", "score")
                        }
                        print(json.dumps(visible), flush=True)
    finally:
        for container in containers.values():
            try:
                container.remove(force=True)
            except Exception:
                pass
        for network in networks.values():
            try:
                network.remove()
            except Exception:
                pass

    evaluator_provenance: dict[str, Any] = {}
    for row in completed.values():
        eval_path = Path(str((row.get("paths") or {}).get("evaluation") or ""))
        if eval_path.is_file():
            eval_data = json.loads(eval_path.read_text(encoding="utf-8"))
            evaluator_provenance = {
                "version": eval_data.get("webarena_verified_version"),
                "evaluator_checksum": eval_data.get("webarena_verified_evaluator_checksum"),
                "data_checksum": eval_data.get("webarena_verified_data_checksum"),
            }
            break

    manifest = {
        "schema": "lex_browser_use_manifest/1",
        "run_id": run_id,
        "run_ids": sorted({str(row.get("run_id")) for row in completed.values()}),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "selection_sha256": selection.sha256,
        "selection": resolved,
        "protocol": {
            "agent": "CowAgent",
            "browser_eval_commit": harness_commit,
            "cowagent_commit": cowagent_commit,
            "model": args.model,
            "reasoning_effort": "low",
            "primary_attempts_per_engine_task": 1,
            "failure_retries": 1,
            "task_failure_retries": 1,
            "model_transport_retries": 1,
            "max_agent_steps": args.max_agent_steps,
            "official_score_uses": "final execution for each browser and task",
            "logical_result_cost": "sum of all executions used for each browser and task",
            "evaluator": "official webarena-verified package, unmodified",
            "environment_reset": "official site container reset before every attempt",
            "environment_prepare_receipt": "required",
            "environment_lane": {
                "lock_namespace": physical_execution_lock_namespace,
                "isolation_unit": "docker-daemon-and-fixed-site-ports",
                "parallelism_rule": "one active attempt per Docker daemon",
            },
            "engine_order": "alternating by task",
            "sites": [site.value for site in SITE_PORTS],
            "official_container_hostname_allowlist": ["host.docker.internal"],
        },
        "environment": {
            "browser_session_contract": browser_session_contract(),
            "platform": platform.platform(),
            "docker_server_version": client.version().get("Version"),
            "docker_architecture": docker_info.get("Architecture"),
            "docker_name": docker_info.get("Name"),
            "webarena_verified_version": importlib.metadata.version("webarena-verified"),
            "browser_images": image_metadata,
            "browser_versions": browser_metadata,
            "site_images": site_image_metadata,
            "site_access_host": site_access_host,
            "official_evaluator": evaluator_provenance,
            "site_runtime_compatibility": runtime_compatibility,
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument(
        "--selection", default="evaluation/browser-use/webarena-verified-pilot.json"
    )
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--env-file",
        help="explicit dotenv file for provider credentials; defaults to the repository .env",
    )
    parser.add_argument(
        "--cowagent-repo",
        help="trusted, clean CowAgent Git checkout; Python code is imported and executed from it",
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--cpus", type=float, default=2.0)
    parser.add_argument("--memory", default="2g")
    parser.add_argument("--sample-interval-ms", type=int, default=100)
    parser.add_argument(
        "--max-agent-steps",
        type=int,
        default=40,
        help="maximum CowAgent decision turns; exhaustion fails closed instead of summarizing",
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        help=(
            "run only the first N pre-registered tasks in this invocation; "
            "resume later without this option to finish the unchanged cohort"
        ),
    )
    return parser.parse_args()


def main() -> None:
    print(run(parse_args()))


if __name__ == "__main__":
    main()
