"""Run CowAgent autonomously against official self-hosted WebArena-Verified sites."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.metadata
import ipaddress
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import docker
import requests
from dotenv import dotenv_values
from json_repair import repair_json
from webarena_verified import WebArenaVerified
from webarena_verified.environments.container.config import get_container_config
from webarena_verified.environments.container.manager import ContainerManager
from webarena_verified.environments.env_ctrl_client import EnvCtrlDockerClient
from webarena_verified.types.config import (
    ContainerConfig,
    ContainerSetupConfig,
    ContainerVolumeSpec,
    EnvironmentConfig,
    WebArenaVerifiedConfig,
)
from webarena_verified.types.task import WebArenaSite

from harness.trajectory_replay.metrics import (
    DockerStatsResources,
    ResourceSampler,
    delta_counters,
)
from harness.trajectory_replay.runner import (
    ENGINE_IMAGES,
    ENGINE_PORTS,
    _published_endpoint,
    _start_browser_container,
    _wait_cdp,
)

from .har import CdpHarRecorder
from .model import OpenAIResponsesModel
from .selection import PilotTask, load_pilot, validate_against_official

SCHEMA = "lex_browser_use_attempt/1"
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
# The official all-in-one shopping image restores Magento, MariaDB and
# Elasticsearch before env-ctrl declares the fixture ready. On Colima/QEMU this
# repeatedly exceeded the package example's 180-second default despite becoming
# healthy shortly afterwards. A longer ceiling changes no measured task time;
# it only keeps environment preparation from aborting a formal cohort.
ENV_CTRL_READY_TIMEOUT_SECONDS = 600
MAGENTO_DEPENDENCY_READY_TIMEOUT_SECONDS = 300
PHYSICAL_EXECUTION_LOCK_NAMESPACE = "webarena-verified-fixed-site-stack-v1"
AMD64_PHP_SITES = frozenset(
    {WebArenaSite.SHOPPING, WebArenaSite.SHOPPING_ADMIN, WebArenaSite.REDDIT}
)
PHP_FPM_COMPAT_CONFIG = Path(__file__).with_name("runtime") / "php-fpm-qemu.ini"
QEMU_X86_64_HOST_PATH = "/usr/bin/qemu-x86_64"
QEMU_X86_64_CONTAINER_PATH = "/usr/local/bin/qemu-x86_64"


def _docker_daemon_lock_namespace(docker_info: dict[str, Any]) -> str:
    """Bind a serialization lane to one Docker daemon and its fixed site ports."""
    daemon_id = str(docker_info.get("ID") or "").strip()
    if not daemon_id:
        raise RuntimeError("Docker daemon ID is required for environment-lane isolation")
    suffix = hashlib.sha256(daemon_id.encode("utf-8")).hexdigest()[:16]
    return f"{PHYSICAL_EXECUTION_LOCK_NAMESPACE}-{suffix}"


def _physical_execution_lock_path(
    lock_namespace: str = PHYSICAL_EXECUTION_LOCK_NAMESPACE,
) -> Path:
    # The WebArena stack uses fixed host ports and is shared by every run owned
    # by this OS user.  The lock therefore must not follow BROWSER_EVAL_DATA_HOME:
    # two cohorts may intentionally write to different evidence directories
    # while still mutating the same containers and databases.
    return (
        Path(tempfile.gettempdir())
        / f"browser-eval-{os.getuid()}"
        / "locks"
        / f"{lock_namespace}.lock"
    )


@contextmanager
def _physical_execution_lock(lock_path: Path) -> Iterator[float]:
    """Serialize the full attempt lifecycle for the shared fixed-port site stack."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        wait_started = time.perf_counter()
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        wait_ms = (time.perf_counter() - wait_started) * 1000
        try:
            yield wait_ms
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _environment_prepare_receipt(
    sites: tuple[WebArenaSite, ...], *, lock_wait_ms: float, lock_namespace: str
) -> dict[str, Any]:
    return {
        "required_sites": [site.value for site in sites],
        "reset_requested": True,
        "physical_execution_lock": {
            "namespace": lock_namespace,
            "wait_ms": round(lock_wait_ms, 3),
        },
        "status": "running",
    }


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


def _docker_image_metadata(image: Any) -> dict[str, Any]:
    attrs = image.attrs
    return {
        "id": image.id,
        "tags": image.tags,
        "repo_digests": attrs.get("RepoDigests") or [],
        "architecture": attrs.get("Architecture"),
        "os": attrs.get("Os"),
        "size_bytes": attrs.get("Size"),
    }


def _container_host_gateway(container: Any) -> str:
    """Return the literal host-gateway address visible to the browser process.

    CowAgent validates URLs in the host process before forwarding them over CDP.
    On Linux/Colima, ``host.docker.internal`` can exist only in the container's
    /etc/hosts, so a literal RFC1918 address is required on both sides.
    """
    result = container.exec_run(["getent", "hosts", "host.docker.internal"])
    if int(result.exit_code) != 0:
        raise RuntimeError("browser container cannot resolve host.docker.internal")
    line = result.output.decode("utf-8", errors="replace").splitlines()[0]
    address = line.split()[0]
    ipaddress.ip_address(address)
    return address


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


def _allow_official_container_hostname(tool: Any) -> None:
    """Allow only the hostname emitted by the official self-hosted images."""
    original = tool._validate_url_safe

    def validate(url: str) -> None:
        if urlparse(url).hostname == "host.docker.internal":
            return
        original(url)

    tool._validate_url_safe = validate


def _configure_benchmark(hostname: str = "host.docker.internal") -> WebArenaVerified:
    def site_url(site: WebArenaSite, host: str) -> str:
        return f"http://{host}:{SITE_PORTS[site][0]}{SITE_URL_SUFFIX.get(site, '')}"

    environments = {
        site: EnvironmentConfig(
            # The gateway IP is active. The official images may redirect to
            # host.docker.internal, so retain that as an equivalent URL for the
            # package's own derendering and NetworkEventEvaluator logic.
            urls=list(
                dict.fromkeys([site_url(site, hostname), site_url(site, "host.docker.internal")])
            ),
            credentials=SITE_CREDENTIALS[site],
            use_header_login=site == WebArenaSite.SHOPPING_ADMIN,
        )
        for site in SITE_PORTS
    }
    return WebArenaVerified(config=WebArenaVerifiedConfig(environments=environments))


def _wait_container_healthy(container: Any, *, timeout_seconds: float = 600) -> None:
    """Wait past GitLab's delayed reconfigure/Puma restart before app probing."""
    deadline = time.monotonic() + timeout_seconds
    last_status = "missing"
    while time.monotonic() < deadline:
        container.reload()
        health = (container.attrs.get("State") or {}).get("Health") or {}
        last_status = str(health.get("Status") or "missing")
        if last_status == "healthy":
            return
        if last_status == "unhealthy":
            logs = health.get("Log") or []
            detail = str(logs[-1].get("Output") or "")[-500:] if logs else ""
            raise RuntimeError(f"GitLab container became unhealthy: {detail}")
        time.sleep(2)
    raise RuntimeError(f"GitLab container health timed out in state {last_status!r}")


def _site_container_manager(site: WebArenaSite) -> ContainerManager:
    """Use QEMU only for PHP-FPM when amd64 site images run on an ARM host.

    Rosetta starts the official images' Java services reliably, but PHP-FPM in
    both Magento images segfaults under Rosetta. Mounting Colima's static QEMU
    interpreter and a one-line supervisor override keeps every other process on
    Rosetta while preserving the official image and site data.
    """
    config = get_container_config(site=site)
    docker_arch = str(docker.from_env().info().get("Architecture") or "")
    if site not in AMD64_PHP_SITES or docker_arch not in {"aarch64", "arm64"}:
        return ContainerManager(site=site, hostname="host.docker.internal")
    if not PHP_FPM_COMPAT_CONFIG.is_file():
        raise RuntimeError(f"missing PHP-FPM compatibility config: {PHP_FPM_COMPAT_CONFIG}")
    compat = ContainerConfig(
        **config.model_dump(exclude={"setup"}),
        setup=ContainerSetupConfig(
            volumes=(
                ContainerVolumeSpec(
                    volume_name=QEMU_X86_64_HOST_PATH,
                    mount_path=QEMU_X86_64_CONTAINER_PATH,
                ),
                ContainerVolumeSpec(
                    volume_name=str(PHP_FPM_COMPAT_CONFIG.resolve()),
                    mount_path="/etc/supervisor.d/php-fpm.ini:ro",
                ),
            )
        ),
    )
    return ContainerManager(
        site=site,
        config=compat,
        hostname="host.docker.internal",
    )


def _verify_arm_php_compatibility(container_name: str) -> None:
    container = docker.from_env().containers.get(container_name)
    processes = container.exec_run(["ps", "-o", "pid,args"])
    process_table = processes.output.decode(errors="replace")
    qemu = container.exec_run(["test", "-x", QEMU_X86_64_CONTAINER_PATH])
    if processes.exit_code != 0 or "/mnt/lima-rosetta/rosetta" not in process_table:
        raise RuntimeError("ARM Docker host must enable Rosetta for official amd64 site images")
    if qemu.exit_code != 0:
        raise RuntimeError("PHP-FPM QEMU compatibility interpreter was not mounted")
    if f"{QEMU_X86_64_CONTAINER_PATH} /usr/local/sbin/php-fpm" not in process_table:
        raise RuntimeError("PHP-FPM did not start through the mounted QEMU interpreter")


def _site_runtime_compatibility(docker_arch: str) -> dict[str, Any]:
    if docker_arch not in {"aarch64", "arm64"}:
        return {"mode": "host-default"}
    return {
        "mode": "rosetta-with-qemu-php-fpm",
        "sites": sorted(site.value for site in AMD64_PHP_SITES),
        "qemu_host_path": QEMU_X86_64_HOST_PATH,
        "supervisor_config_sha256": hashlib.sha256(PHP_FPM_COMPAT_CONFIG.read_bytes()).hexdigest(),
    }


def _ensure_site(site: WebArenaSite, *, reset: bool) -> None:
    port, ctrl_port = SITE_PORTS[site]
    manager = _site_container_manager(site)
    if reset or not manager.is_running():
        manager.start(port=port, env_ctrl_port=ctrl_port, wait=False)
    ready = EnvCtrlDockerClient.create(
        manager.container_name,
        timeout=ENV_CTRL_READY_TIMEOUT_SECONDS,
    ).wait_until_ready(
        timeout=ENV_CTRL_READY_TIMEOUT_SECONDS,
        interval=2,
    )
    if not ready.success:
        raise RuntimeError(f"{site.value} did not become ready: {ready.message}")
    container = docker.from_env().containers.get(manager.container_name)
    if site == WebArenaSite.GITLAB:
        _wait_container_healthy(container)
    if site in {WebArenaSite.SHOPPING, WebArenaSite.SHOPPING_ADMIN}:
        _wait_magento_dependencies_ready(container)
    _wait_application_ready(site, manager.config.health_check_path)
    if QEMU_X86_64_HOST_PATH in manager.config.volumes:
        _verify_arm_php_compatibility(manager.container_name)


def _wait_magento_dependencies_ready(
    container: Any, *, timeout_seconds: float = MAGENTO_DEPENDENCY_READY_TIMEOUT_SECONDS
) -> None:
    """Wait for Magento's search dependency after fixture restoration.

    The official env-ctrl ready signal can precede Elasticsearch accepting
    requests. Magento write paths then fail after committing their primary
    record because the synchronous search-index update cannot connect. Waiting
    for a non-red cluster removes that infrastructure race from measured tasks.
    """
    deadline = time.monotonic() + timeout_seconds
    last_detail = "no response"
    while time.monotonic() < deadline:
        result = container.exec_run(
            [
                "curl",
                "-fsS",
                "http://127.0.0.1:9200/_cluster/health?wait_for_status=yellow&timeout=5s",
            ]
        )
        output = result.output.decode("utf-8", errors="replace")
        last_detail = output[-500:] or f"exit {result.exit_code}"
        if result.exit_code == 0:
            try:
                status = str(json.loads(output).get("status") or "")
            except json.JSONDecodeError:
                status = ""
            if status in {"yellow", "green"}:
                return
        time.sleep(2)
    raise RuntimeError(f"Magento Elasticsearch was not ready: {last_detail}")


def _wait_application_ready(
    site: WebArenaSite,
    health_path: str,
    *,
    timeout_seconds: float = 300,
    stable_responses: int = 3,
) -> None:
    """Require a stable app endpoint after env-ctrl reports ready.

    GitLab's control process can report ready during the Puma handover performed
    by container initialization. A single successful request can therefore be
    followed by a run of 502 responses. Consecutive app-level responses prevent
    that startup race from consuming an Agent attempt.
    """
    port = SITE_PORTS[site][0]
    url = f"http://127.0.0.1:{port}{health_path}"
    deadline = time.monotonic() + timeout_seconds
    consecutive = 0
    last_detail = "no response"
    while time.monotonic() < deadline:
        try:
            # The official shopping images redirect to their configured
            # host.docker.internal origin. That name is intentionally scoped to
            # browser containers and may not resolve on the macOS host. A stable
            # pre-redirect response proves the app port is serving; _login_context
            # then validates the rendered authenticated page from inside the
            # actual browser before tracing or model calls begin.
            response = requests.get(url, timeout=15, allow_redirects=False)
            last_detail = f"HTTP {response.status_code}"
            consecutive = consecutive + 1 if response.status_code < 500 else 0
            if consecutive >= stable_responses:
                return
        except requests.RequestException as exc:
            consecutive = 0
            last_detail = str(exc)
        time.sleep(2)
    raise RuntimeError(f"{site.value} application was not stably ready: {last_detail}")


def _prepare_sites(sites: tuple[WebArenaSite, ...]) -> None:
    """Keep only task-required sites running, then reset those sites from official images."""
    for site in SITE_PORTS:
        manager = _site_container_manager(site)
        if site not in sites and manager.is_running():
            manager.stop()
    for site in sites:
        _ensure_site(site, reset=True)


def _login_context(
    service: Any,
    sites: tuple[WebArenaSite, ...],
    benchmark: WebArenaVerified,
    start_urls: list[str],
) -> int:
    def login() -> None:
        context = service._context
        assert context is not None
        primary_page = service._page
        assert primary_page is not None
        for existing_page in context.pages:
            if existing_page != primary_page:
                existing_page.close()
        context.clear_cookies()
        context.set_extra_http_headers(
            {
                SITE_AUTH_HEADERS[site]: (
                    f"{SITE_CREDENTIALS[site]['username']}:{SITE_CREDENTIALS[site]['password']}"
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
        primary_page.set_default_timeout(60_000)
        primary_page.set_default_navigation_timeout(90_000)
        primary_page.goto(start_urls[0], wait_until="domcontentloaded")
        primary_page.wait_for_timeout(500)

    readiness_retries = 0
    try:
        service._submit(login)
    except Exception as exc:
        # Moli can expose its CDP endpoint just before the default browser
        # context is ready. This is a session precondition failure: no Agent
        # step has started and no score-bearing attempt exists yet. Recover it
        # once, record the recovery on the eventual attempt, and keep every
        # other setup failure fatal.
        if "BrowserContextNotLoaded" not in str(exc):
            raise
        readiness_retries = 1
        time.sleep(1)
        service._submit(login)
    return readiness_retries


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
    lock_path: Path,
    lock_namespace: str = PHYSICAL_EXECUTION_LOCK_NAMESPACE,
    **kwargs: Any,
) -> dict[str, Any]:
    with _physical_execution_lock(lock_path) as lock_wait_ms:
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
    prepare_receipt = _environment_prepare_receipt(
        sites, lock_wait_ms=lock_wait_ms, lock_namespace=lock_namespace
    )
    try:
        _prepare_sites(sites)
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
    tool = BrowserTool(
        config={
            "cdp_endpoint": endpoint,
            "headless": True,
            "viewport": {"width": 1280, "height": 900},
            "startup_timeout": 90,
        }
    )
    _allow_official_container_hostname(tool)
    events: list[dict[str, Any]] = []
    resources = DockerStatsResources(container)
    before = resources.counters()
    sampler = ResourceSampler(resources, interval_ms=sample_interval_ms)
    raw_response = ""
    error = ""
    prepared = False
    recorder = CdpHarRecorder()
    sampler_started = False
    browser_session_setup_ms = 0.0
    browser_session_readiness_retries = 0
    agent_run_ms = 0.0
    evidence_finalize_ms = 0.0
    evaluation_ms = 0.0
    started = time.perf_counter()
    try:
        session_setup_started = time.perf_counter()
        model.reset()
        service = tool._get_service()
        service._submit(lambda: None)
        browser_session_readiness_retries = _login_context(service, sites, benchmark, start_urls)
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
        raw_response = agent.run_stream(
            _user_prompt(task, start_urls), on_event=events.append, clear_history=True
        )
        if _exhausted_agent_steps(events, max_agent_steps):
            response_eval = next(
                item for item in task.eval if item.evaluator == "AgentResponseEvaluator"
            )
            raw_response = json.dumps(
                _max_steps_response(str(response_eval.expected.task_type), max_agent_steps)
            )
        agent_run_ms = (time.perf_counter() - agent_started) * 1000
        finalize_started = time.perf_counter()
        service._submit(
            lambda: (
                service._page.wait_for_timeout(500),
                recorder.stop(service._context, har_path),
                service._context.tracing.stop(path=str(trace_path)),
            )
        )
        evidence_finalize_ms = (time.perf_counter() - finalize_started) * 1000
    except Exception:
        error = traceback.format_exc()
        try:
            service = tool._get_service()
            service._submit(
                lambda: (
                    recorder.stop(service._context, har_path),
                    service._context.tracing.stop(path=str(trace_path)),
                )
            )
        except Exception:
            pass
    finally:
        cleanup_errors: list[str] = []
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

    if not prepared:
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
        _resource_summary(resources, sampler, before, after, duration_ms)
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
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        model = str(row.get("model") or expected_model or "")
        if not model:
            raise ValueError(f"attempt does not identify its model at {path}:{line_number}")
        if expected_model and model != expected_model:
            raise ValueError(
                f"attempt model differs from the evidence cohort at {path}:{line_number}"
            )
        row["model"] = model
        key = (model, str(row["engine"]), int(row["task_id"]), int(row["attempt"]))
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
    harness_commit = _require_clean_git_checkout(repo_root)
    cowagent_commit = _require_clean_git_checkout(cowagent_repo)

    output_dir = Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    selection = load_pilot(args.selection)
    benchmark = _configure_benchmark()
    resolved = validate_against_official(selection, benchmark)
    model = OpenAIResponsesModel(
        api_key=api_key,
        base_url=env.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=args.model,
        reasoning_effort="low",
    )
    model.verify_access()

    client = docker.from_env()
    docker_info = client.info()
    physical_execution_lock_namespace = _docker_daemon_lock_namespace(docker_info)
    physical_execution_lock_path = _physical_execution_lock_path(
        physical_execution_lock_namespace
    )
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
            **_docker_image_metadata(
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
    site_runtime_compatibility = _site_runtime_compatibility(docker_architecture)
    try:
        for engine in ENGINE_IMAGES:
            network = client.networks.create(f"lex-browser-use-{run_id}-{engine}")
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
            image_metadata[engine] = _docker_image_metadata(container.image)
            http_endpoint = _published_endpoint(
                container, engine=engine, cdp_port=ENGINE_PORTS[engine]
            )
            endpoints[engine] = _wait_cdp(http_endpoint, container=container)
            browser_metadata[engine] = _cdp_metadata(http_endpoint)

        gateway_addresses = {
            _container_host_gateway(container) for container in containers.values()
        }
        if len(gateway_addresses) != 1:
            raise RuntimeError(f"browser containers disagree on host gateway: {gateway_addresses}")
        site_access_host = gateway_addresses.pop()
        benchmark = _configure_benchmark(hostname=site_access_host)
        resolved = validate_against_official(selection, benchmark)

        _write_or_validate_run_config(
            run_config_file,
            {
                "schema": "lex_browser_use_run_config/1",
                "selection_sha256": selection.sha256,
                "browser_eval_commit": harness_commit,
                "cowagent_commit": cowagent_commit,
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
                "site_runtime_compatibility": site_runtime_compatibility,
            },
        )

        with attempts_file.open("a", encoding="utf-8") as stream:
            for task_index, selected in enumerate(
                _scheduled_tasks(selection.tasks, args.max_tasks)
            ):
                order = ("chromium", "moli") if task_index % 2 == 0 else ("moli", "chromium")
                for engine in order:
                    for attempt in (1, 2):
                        key = (args.model, engine, selected.task_id, attempt)
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
                        primary = completed.get((args.model, engine, selected.task_id, 1))
                        if attempt == 2 and primary is not None and float(primary["score"]) == 1.0:
                            break
                        row = _run_attempt(
                            lock_path=physical_execution_lock_path,
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
                        if row["score"] == 1.0:
                            break
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
            "site_runtime_compatibility": site_runtime_compatibility,
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
    run(parse_args())


if __name__ == "__main__":
    main()
