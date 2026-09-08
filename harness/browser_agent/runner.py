"""Run CowAgent autonomously against official self-hosted WebArena-Verified sites."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import docker
import requests
from dotenv import dotenv_values
from json_repair import repair_json
from webarena_verified import WebArenaVerified
from webarena_verified.environments.container.manager import ContainerManager
from webarena_verified.environments.env_ctrl_client import EnvCtrlDockerClient
from webarena_verified.types.config import EnvironmentConfig, WebArenaVerifiedConfig
from webarena_verified.types.task import WebArenaSite

from harness.browser_kernel.metrics import (
    DockerStatsResources,
    ResourceSampler,
    delta_counters,
)
from harness.browser_kernel.runner import (
    ENGINE_IMAGES,
    ENGINE_PORTS,
    _published_endpoint,
    _start_browser_container,
    _wait_cdp,
)

from .model import AstraResponsesModel
from .selection import PilotTask, load_pilot, validate_against_official

SCHEMA = "lex_browser_agent_attempt/1"
SITE_PORTS = {
    WebArenaSite.SHOPPING: (7770, 7771),
    WebArenaSite.SHOPPING_ADMIN: (7780, 7781),
    WebArenaSite.REDDIT: (9999, 9998),
    WebArenaSite.GITLAB: (8023, 8024),
}
SITE_CREDENTIALS = {
    WebArenaSite.SHOPPING: {"username": "emma.lopez@gmail.com", "password": "Password.123"},
    WebArenaSite.SHOPPING_ADMIN: {"username": "admin", "password": "admin1234"},
    WebArenaSite.REDDIT: {"username": "MarvelsGrantMan136", "password": "test1234"},
    WebArenaSite.GITLAB: {"username": "byteblaze", "password": "hello1234"},
}
SITE_URL_SUFFIX = {WebArenaSite.SHOPPING_ADMIN: "/admin"}
SITE_AUTH_HEADERS = {
    WebArenaSite.SHOPPING: "X-M2-Customer-Auto-Login",
    WebArenaSite.SHOPPING_ADMIN: "X-M2-Admin-Auto-Login",
    WebArenaSite.REDDIT: "X-Postmill-Auto-Login",
}


def _load_repo_env(repo_root: Path) -> dict[str, str]:
    """Credentials are intentionally read only from this repository's .env."""
    env_file = repo_root / ".env"
    if not env_file.exists():
        return {}
    return {str(key): str(value) for key, value in dotenv_values(env_file).items() if value}


def _git_head(path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _require_clean_git_checkout(path: Path) -> str:
    commit = _git_head(path)
    status = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status.strip():
        raise RuntimeError(
            "CowAgent checkout must be clean so its recorded commit identifies the code"
        )
    return commit


def _cdp_metadata(http_endpoint: str) -> dict[str, str]:
    response = requests.get(http_endpoint.rstrip("/") + "/json/version", timeout=15)
    response.raise_for_status()
    payload = response.json()
    return {
        key: str(payload[key])
        for key in ("Browser", "Protocol-Version", "User-Agent", "V8-Version")
        if payload.get(key)
    }


def _configure_benchmark(hostname: str = "host.docker.internal") -> WebArenaVerified:
    environments = {
        site: EnvironmentConfig(
            urls=[
                f"http://{hostname}:{SITE_PORTS[site][0]}"
                f"{SITE_URL_SUFFIX.get(site, '')}"
            ],
            credentials=SITE_CREDENTIALS[site],
            use_header_login=site == WebArenaSite.SHOPPING_ADMIN,
        )
        for site in SITE_PORTS
    }
    return WebArenaVerified(config=WebArenaVerifiedConfig(environments=environments))


def _ensure_site(site: WebArenaSite, *, reset: bool) -> None:
    port, ctrl_port = SITE_PORTS[site]
    manager = ContainerManager(site=site, hostname="host.docker.internal")
    if reset or not manager.is_running():
        manager.start(port=port, env_ctrl_port=ctrl_port, wait=False)
    ready = EnvCtrlDockerClient.create(manager.container_name, timeout=180).wait_until_ready(
        timeout=180, interval=2
    )
    if not ready.success:
        raise RuntimeError(f"{site.value} did not become ready: {ready.message}")


def _prepare_sites(sites: tuple[WebArenaSite, ...]) -> None:
    """Keep only task-required sites running, then reset those sites from official images."""
    for site in SITE_PORTS:
        manager = ContainerManager(site=site, hostname="host.docker.internal")
        if site not in sites and manager.is_running():
            manager.stop()
    for site in sites:
        _ensure_site(site, reset=True)


def _login_context(
    service: Any, sites: tuple[WebArenaSite, ...], benchmark: WebArenaVerified
) -> None:
    def login() -> None:
        context = service._context
        assert context is not None
        context.clear_cookies()
        context.set_extra_http_headers(
            {
                SITE_AUTH_HEADERS[site]: (
                    f"{SITE_CREDENTIALS[site]['username']}:"
                    f"{SITE_CREDENTIALS[site]['password']}"
                )
                for site in sites
                if site in SITE_AUTH_HEADERS
            }
        )
        for site in sites:
            base = benchmark.config.get_environment(site).active_url
            creds = SITE_CREDENTIALS[site]
            page = context.new_page()
            try:
                page.set_default_timeout(60_000)
                page.set_default_navigation_timeout(90_000)
                if site == WebArenaSite.SHOPPING:
                    page.goto(f"{base}/customer/account", wait_until="commit")
                    page.evaluate("localStorage.clear(); sessionStorage.clear()")
                    page.reload(wait_until="domcontentloaded")
                    page.locator(".block-dashboard-info, .box-information").first.wait_for()
                elif site == WebArenaSite.SHOPPING_ADMIN:
                    page.goto(base, wait_until="commit")
                    page.evaluate("localStorage.clear(); sessionStorage.clear()")
                    page.reload(wait_until="domcontentloaded")
                    page.locator(".page-title").wait_for()
                elif site == WebArenaSite.REDDIT:
                    page.goto(base, wait_until="commit")
                    page.evaluate("localStorage.clear(); sessionStorage.clear()")
                    page.reload(wait_until="domcontentloaded")
                    page.get_by_role("button", name=creds["username"], exact=True).wait_for()
                elif site == WebArenaSite.GITLAB:
                    page.goto(f"{base}/users/sign_in", wait_until="commit")
                    page.evaluate("localStorage.clear(); sessionStorage.clear()")
                    page.reload(wait_until="domcontentloaded")
                    page.get_by_test_id("username-field").fill(creds["username"])
                    page.get_by_test_id("password-field").fill(creds["password"])
                    page.get_by_test_id("sign-in-button").click()
                    page.wait_for_url("**/", timeout=90_000)
                page.wait_for_timeout(1_000)
            finally:
                page.close()

    service._submit(login)


def _system_prompt() -> str:
    return """You are the autonomous browser agent under evaluation. Use only the browser tool.
Complete the objective from the supplied start URLs. Do not ask for help, download files, or
change state unless the objective requires it. When finished, return only one JSON object with
task_type (RETRIEVE, MUTATE, or NAVIGATE), status (SUCCESS, NOT_FOUND_ERROR,
ACTION_NOT_ALLOWED_ERROR, PERMISSION_DENIED_ERROR, DATA_VALIDATION_ERROR, or UNKNOWN_ERROR),
retrieved_data, and error_details. For RETRIEVE, retrieved_data is always a list. For other task
types it is null."""


def _user_prompt(task: Any, start_urls: list[str]) -> str:
    response_eval = next(
        item for item in task.eval if item.evaluator == "AgentResponseEvaluator"
    )
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


def _resource_summary(
    resources: DockerStatsResources,
    sampler: ResourceSampler,
    before: Any,
    after: Any,
    duration_ms: float,
) -> dict[str, Any]:
    result = {**delta_counters(before, after), **sampler.summary()}
    if duration_ms > 0:
        result["cpu_average_utilization_pct"] = result["cpu_ms"] / duration_ms * 100
    return result


def _run_attempt(  # noqa: PLR0913
    *,
    benchmark: WebArenaVerified,
    selected: PilotTask,
    engine: str,
    attempt: int,
    endpoint: str,
    container: Any,
    cowagent_repo: Path,
    output_dir: Path,
    model: AstraResponsesModel,
    sample_interval_ms: int,
) -> dict[str, Any]:
    task = benchmark.get_task(selected.task_id)
    sites = tuple(task.sites)
    _prepare_sites(sites)

    if str(cowagent_repo) not in sys.path:
        sys.path.insert(0, str(cowagent_repo))
    from agent.protocol.agent import Agent  # noqa: PLC0415
    from agent.tools.browser.browser_tool import BrowserTool  # noqa: PLC0415

    attempt_dir = output_dir / engine / str(selected.task_id) / f"attempt-{attempt}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    trace_path = attempt_dir / "network.zip"
    response_path = attempt_dir / "agent_response.json"
    eval_path = attempt_dir / "eval_result.json"
    events_path = attempt_dir / "agent_events.json"
    tool = BrowserTool(
        config={
            "cdp_endpoint": endpoint,
            "headless": True,
            "viewport": {"width": 1280, "height": 900},
            "startup_timeout": 90,
        }
    )
    events: list[dict[str, Any]] = []
    resources = DockerStatsResources(container)
    before = resources.counters()
    sampler = ResourceSampler(resources, interval_ms=sample_interval_ms)
    raw_response = ""
    error = ""
    prepared = False
    sampler_started = False
    started = time.perf_counter()
    try:
        model.reset()
        service = tool._get_service()
        service._submit(lambda: None)
        _login_context(service, sites, benchmark)
        service._submit(lambda: service._context.tracing.start(screenshots=True, snapshots=True))
        prepared = True
        sampler.start()
        sampler_started = True
        agent = Agent(
            system_prompt="",
            description="WebArena-Verified CowAgent",
            model=model,
            tools=[tool],
            output_mode="logger",
            max_steps=30,
            enable_skills=False,
            skip_context_files=True,
        )
        # CowAgent rebuilds its base prompt at run time; the suffix is the supported
        # way to retain benchmark-specific completion and output constraints.
        agent.extra_system_suffix = _system_prompt()
        start_urls = list(benchmark.config.render_url(list(task.start_urls), list(task.sites)))
        raw_response = agent.run_stream(
            _user_prompt(task, start_urls), on_event=events.append, clear_history=True
        )
        service._submit(lambda: service._context.tracing.stop(path=str(trace_path)))
    except Exception:
        error = traceback.format_exc()
        try:
            service = tool._get_service()
            service._submit(lambda: service._context.tracing.stop(path=str(trace_path)))
        except Exception:
            pass
    finally:
        if sampler_started:
            sampler.stop()
        duration_ms = (time.perf_counter() - started) * 1000
        after = resources.counters()
        tool.close()

    if not prepared:
        raise RuntimeError(
            f"{engine} task {selected.task_id}: environment precondition failed\n{error}"
        )

    parsed_response = _parse_agent_response(raw_response)
    response_path.write_text(
        json.dumps(parsed_response, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    events_path.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    if trace_path.exists():
        evaluation = benchmark.evaluate_task(
            task_id=selected.task_id,
            agent_response=parsed_response,
            network_trace=trace_path,
        )
        eval_data = evaluation.model_dump(mode="json")
    else:
        eval_data = {"status": "error", "score": 0.0, "error_msg": error or "trace missing"}
    eval_path.write_text(json.dumps(eval_data, ensure_ascii=False, indent=2), encoding="utf-8")
    resource = (
        _resource_summary(resources, sampler, before, after, duration_ms)
        if sampler.samples
        else {}
    )
    return {
        "schema": SCHEMA,
        "engine": engine,
        "task_id": selected.task_id,
        "tier": selected.tier,
        "estimated_min_steps": selected.estimated_min_steps,
        "attempt": attempt,
        "is_retry": attempt > 1,
        "status": eval_data.get("status", "error"),
        "score": float(eval_data.get("score") or 0.0),
        "duration_ms": round(duration_ms, 3),
        "agent_response": parsed_response,
        "error": error,
        "resource": resource,
        "model_usage": {**model.usage, "request_count": model.request_count},
        "paths": {
            "trace": str(trace_path),
            "response": str(response_path),
            "evaluation": str(eval_path),
            "events": str(events_path),
        },
    }


def _load_completed_attempts(path: Path) -> dict[tuple[str, int, int], dict[str, Any]]:
    """Load durable attempt rows so an interrupted multi-hour pilot can resume safely."""
    if not path.exists():
        return {}
    completed: dict[tuple[str, int, int], dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        key = (str(row["engine"]), int(row["task_id"]), int(row["attempt"]))
        if key in completed:
            raise ValueError(f"duplicate attempt at {path}:{line_number}: {key}")
        completed[key] = row
    return completed


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
    env = _load_repo_env(repo_root)
    api_key = env.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(f"OPENAI_API_KEY is required in {repo_root / '.env'}")
    cowagent_repo = Path(args.cowagent_repo or env.get("COWAGENT_REPO", "")).resolve()
    if not (cowagent_repo / "agent/protocol/agent.py").exists():
        raise RuntimeError("--cowagent-repo must point to a CowAgent checkout")
    cowagent_commit = _require_clean_git_checkout(cowagent_repo)

    output_dir = Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    selection = load_pilot(args.selection)
    benchmark = _configure_benchmark()
    resolved = validate_against_official(selection, benchmark)
    model = AstraResponsesModel(
        api_key=api_key,
        base_url=env.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=args.model,
        reasoning_effort="low",
    )
    model.verify_access()

    client = docker.from_env()
    run_id = uuid.uuid4().hex[:12]
    containers: dict[str, Any] = {}
    networks: dict[str, Any] = {}
    endpoints: dict[str, str] = {}
    attempts_file = output_dir / "attempts.jsonl"
    run_config_file = output_dir / "run-config.json"
    completed = _load_completed_attempts(attempts_file)
    image_metadata: dict[str, dict[str, Any]] = {}
    browser_metadata: dict[str, dict[str, str]] = {}
    docker_info: dict[str, Any] = {}
    try:
        for engine in ENGINE_IMAGES:
            network = client.networks.create(f"lex-browser-agent-{run_id}-{engine}")
            networks[engine] = network
            container = _start_browser_container(
                client,
                engine=engine,
                run_id=f"agent-{run_id}",
                cpus=args.cpus,
                memory=args.memory,
                network=network.name,
            )
            containers[engine] = container
            container.reload()
            image_metadata[engine] = {
                "id": container.image.id,
                "tags": container.image.tags,
            }
            http_endpoint = _published_endpoint(
                container, engine=engine, cdp_port=ENGINE_PORTS[engine]
            )
            endpoints[engine] = _wait_cdp(http_endpoint, container=container)
            browser_metadata[engine] = _cdp_metadata(http_endpoint)

        _write_or_validate_run_config(
            run_config_file,
            {
                "schema": "lex_browser_agent_run_config/1",
                "selection_sha256": selection.sha256,
                "cowagent_commit": cowagent_commit,
                "model": args.model,
                "reasoning_effort": "low",
                "browser_images": image_metadata,
                "browser_versions": browser_metadata,
                "docker_context": subprocess.run(
                    ["docker", "context", "show"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
            },
        )

        with attempts_file.open("a", encoding="utf-8") as stream:
            for task_index, selected in enumerate(
                _scheduled_tasks(selection.tasks, args.max_tasks)
            ):
                order = ("chromium", "moli") if task_index % 2 == 0 else ("moli", "chromium")
                for engine in order:
                    for attempt in (1, 2):
                        key = (engine, selected.task_id, attempt)
                        if key in completed:
                            row = completed[key]
                            print(
                                json.dumps(
                                    {
                                        "engine": engine,
                                        "task_id": selected.task_id,
                                        "attempt": attempt,
                                        "status": "resume-skip",
                                        "score": row["score"],
                                    }
                                ),
                                flush=True,
                            )
                            if float(row["score"]) == 1.0:
                                break
                            continue
                        primary = completed.get((engine, selected.task_id, 1))
                        if attempt == 2 and primary is not None and float(primary["score"]) == 1.0:
                            break
                        row = _run_attempt(
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
                        if row["score"] == 1.0:
                            break
        docker_info = client.info()
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

    manifest = {
        "schema": "lex_browser_agent_manifest/1",
        "run_id": run_id,
        "run_ids": sorted({str(row.get("run_id")) for row in completed.values()}),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "selection_sha256": selection.sha256,
        "selection": resolved,
        "protocol": {
            "agent": "CowAgent",
            "cowagent_commit": cowagent_commit,
            "model": args.model,
            "reasoning_effort": "low",
            "primary_attempts_per_engine_task": 1,
            "failure_retries": 1,
            "official_score_uses": "primary attempt only",
            "retry_score_use": "reported separately as recovery diagnostic",
            "evaluator": "official webarena-verified package, unmodified",
            "environment_reset": "official site container reset before every attempt",
            "engine_order": "alternating by task",
            "sites": [site.value for site in SITE_PORTS],
        },
        "environment": {
            "platform": platform.platform(),
            "docker_server_version": client.version().get("Version"),
            "docker_architecture": docker_info.get("Architecture"),
            "docker_name": docker_info.get("Name"),
            "webarena_verified_version": importlib.metadata.version("webarena-verified"),
            "browser_images": image_metadata,
            "browser_versions": browser_metadata,
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
        "--selection", default="evaluation/browser-agent/webarena-verified-pilot.json"
    )
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--cowagent-repo",
        help="trusted, clean CowAgent Git checkout; Python code is imported and executed from it",
    )
    parser.add_argument("--model", default="gpt-6-astra")
    parser.add_argument("--cpus", type=float, default=2.0)
    parser.add_argument("--memory", default="2g")
    parser.add_argument("--sample-interval-ms", type=int, default=100)
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
    run(parse_args())


if __name__ == "__main__":
    main()
