"""Shared WebArena site preparation, owned browser sessions and execution isolation."""
from __future__ import annotations

import fcntl
import hashlib
import ipaddress
import json
import os
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import docker
import requests
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

from harness.environments.webarena.topology import (
    CONTAINER_HOST_ALIAS,
    HOST_LOOPBACK,
    MAGENTO_ELASTICSEARCH_PORT,
    SITE_CREDENTIALS,
    SITE_PORTS,
    site_url,
)
from harness.resource_accounting import DockerStatsResources, ResourceSampler, delta_counters

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


def docker_daemon_lock_namespace(docker_info: dict[str, Any]) -> str:
    """Bind a serialization lane to one Docker daemon and its fixed site ports."""
    daemon_id = str(docker_info.get("ID") or "").strip()
    if not daemon_id:
        raise RuntimeError("Docker daemon ID is required for environment-lane isolation")
    suffix = hashlib.sha256(daemon_id.encode("utf-8")).hexdigest()[:16]
    return f"{PHYSICAL_EXECUTION_LOCK_NAMESPACE}-{suffix}"


def physical_execution_lock_path(
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
def physical_execution_lock(lock_path: Path) -> Iterator[float]:
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


def environment_prepare_receipt(
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


def git_head(path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def require_clean_git_checkout(path: Path) -> str:
    commit = git_head(path)
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


def cdp_metadata(http_endpoint: str) -> dict[str, str]:
    response = requests.get(http_endpoint.rstrip("/") + "/json/version", timeout=15)
    response.raise_for_status()
    payload = response.json()
    return {
        key: str(payload[key])
        for key in ("Browser", "Protocol-Version", "User-Agent", "V8-Version")
        if payload.get(key)
    }


def docker_image_metadata(image: Any) -> dict[str, Any]:
    attrs = image.attrs
    return {
        "id": image.id,
        "tags": image.tags,
        "repo_digests": attrs.get("RepoDigests") or [],
        "architecture": attrs.get("Architecture"),
        "os": attrs.get("Os"),
        "size_bytes": attrs.get("Size"),
    }


def container_host_gateway(container: Any) -> str:
    """Return the literal host-gateway address visible to the browser process.

    CowAgent validates URLs in the host process before forwarding them over CDP.
    On Linux/Colima, ``host.docker.internal`` can exist only in the container's
    /etc/hosts, so a literal RFC1918 address is required on both sides.
    """
    result = container.exec_run(["getent", "hosts", CONTAINER_HOST_ALIAS])
    if int(result.exit_code) != 0:
        raise RuntimeError(f"browser container cannot resolve {CONTAINER_HOST_ALIAS}")
    line = result.output.decode("utf-8", errors="replace").splitlines()[0]
    address = line.split()[0]
    ipaddress.ip_address(address)
    return address


def allow_official_container_hostname(tool: Any) -> None:
    """Allow only the hostname emitted by the official self-hosted images."""
    original = tool._validate_url_safe

    def validate(url: str) -> None:
        if urlparse(url).hostname == CONTAINER_HOST_ALIAS:
            return
        original(url)

    tool._validate_url_safe = validate


def configure_benchmark(hostname: str = CONTAINER_HOST_ALIAS) -> WebArenaVerified:
    environments = {
        site: EnvironmentConfig(
            # The gateway IP is active. The official images may redirect to
            # host.docker.internal, so retain that as an equivalent URL for the
            # package's own derendering and NetworkEventEvaluator logic.
            urls=list(
                dict.fromkeys([site_url(site, hostname), site_url(site, CONTAINER_HOST_ALIAS)])
            ),
            credentials=SITE_CREDENTIALS[site],
            use_header_login=site == WebArenaSite.SHOPPING_ADMIN,
        )
        for site in SITE_PORTS
    }
    return WebArenaVerified(config=WebArenaVerifiedConfig(environments=environments))


def wait_container_healthy(container: Any, *, timeout_seconds: float = 600) -> None:
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


def site_container_manager(site: WebArenaSite) -> ContainerManager:
    """Use QEMU only for PHP-FPM when amd64 site images run on an ARM host.

    Rosetta starts the official images' Java services reliably, but PHP-FPM in
    both Magento images segfaults under Rosetta. Mounting Colima's static QEMU
    interpreter and a one-line supervisor override keeps every other process on
    Rosetta while preserving the official image and site data.
    """
    config = get_container_config(site=site)
    docker_arch = str(docker.from_env().info().get("Architecture") or "")
    if site not in AMD64_PHP_SITES or docker_arch not in {"aarch64", "arm64"}:
        return ContainerManager(site=site, hostname=CONTAINER_HOST_ALIAS)
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
        hostname=CONTAINER_HOST_ALIAS,
    )


def verify_arm_php_compatibility(container_name: str) -> None:
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


def site_runtime_compatibility(docker_arch: str) -> dict[str, Any]:
    if docker_arch not in {"aarch64", "arm64"}:
        return {"mode": "host-default"}
    return {
        "mode": "rosetta-with-qemu-php-fpm",
        "sites": sorted(site.value for site in AMD64_PHP_SITES),
        "qemu_host_path": QEMU_X86_64_HOST_PATH,
        "supervisor_config_sha256": hashlib.sha256(PHP_FPM_COMPAT_CONFIG.read_bytes()).hexdigest(),
    }


def ensure_site(site: WebArenaSite, *, reset: bool) -> None:
    port, ctrl_port = SITE_PORTS[site]
    manager = site_container_manager(site)
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
        wait_container_healthy(container)
    if site in {WebArenaSite.SHOPPING, WebArenaSite.SHOPPING_ADMIN}:
        wait_magento_dependencies_ready(container)
    wait_application_ready(site, manager.config.health_check_path)
    if QEMU_X86_64_HOST_PATH in manager.config.volumes:
        verify_arm_php_compatibility(manager.container_name)


def wait_magento_dependencies_ready(
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
                f"http://{HOST_LOOPBACK}:{MAGENTO_ELASTICSEARCH_PORT}"
                "/_cluster/health?wait_for_status=yellow&timeout=5s",
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


def wait_application_ready(
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
    url = f"http://{HOST_LOOPBACK}:{port}{health_path}"
    deadline = time.monotonic() + timeout_seconds
    consecutive = 0
    last_detail = "no response"
    while time.monotonic() < deadline:
        try:
            # The official shopping images redirect to their configured
            # host.docker.internal origin. That name is intentionally scoped to
            # browser containers and may not resolve on the macOS host. A stable
            # pre-redirect response proves the app port is serving; login_context
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


def prepare_sites(sites: tuple[WebArenaSite, ...]) -> None:
    """Keep only task-required sites running, then reset those sites from official images."""
    for site in SITE_PORTS:
        manager = site_container_manager(site)
        if site not in sites and manager.is_running():
            manager.stop()
    for site in sites:
        ensure_site(site, reset=True)


def browser_session_contract() -> dict[str, Any]:
    return {
        "mode": "owned_cdp_context",
        "viewport": dict(BENCHMARK_VIEWPORT),
        "postmill_auth": "single_request_bootstrap",
        "verification": "primary and inherited new page before navigation, then after execution",
    }


def browser_tool_config(endpoint: str) -> dict[str, Any]:
    return {
        "cdp_endpoint": endpoint,
        "headless": True,
        "viewport_width": BENCHMARK_VIEWPORT["width"],
        "viewport_height": BENCHMARK_VIEWPORT["height"],
        "cdp_isolated_context": True,
        "locale": "en-US",
        "startup_timeout": 90,
    }


def record_page_viewport(
    page: Any,
    evidence: dict[str, Any],
    phase: str,
    observer: Any | None = None,
) -> None:
    try:
        observed = (
            observer(page)
            if observer is not None
            else page.evaluate(
                "() => ({width: innerWidth, height: innerHeight, "
                "device_scale_factor: devicePixelRatio})"
            )
        )
    except Exception:
        evidence["verified"] = False
        raise
    evidence["observations"].append({"phase": phase, **observed})
    if {key: observed.get(key) for key in BENCHMARK_VIEWPORT} != BENCHMARK_VIEWPORT:
        evidence["verified"] = False
        raise RuntimeError(f"browser viewport mismatch during {phase}: {observed}")


def verify_session_viewport(service: Any, evidence: dict[str, Any], phase: str) -> None:
    def verify() -> None:
        pages = service._context.pages
        if not pages:
            evidence["verified"] = False
            raise RuntimeError("browser viewport cannot be verified without a live page")
        observer = getattr(service, "viewport_observer", None)
        for page in pages:
            record_page_viewport(page, evidence, phase, observer)

    service._submit(verify)


def prepare_browser_viewport(service: Any) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "requested": dict(BENCHMARK_VIEWPORT),
        "mode": "owned_cdp_context",
        "verified": False,
        "observations": [],
        "resource_scope": "whole browser process, including pre-existing default contexts",
    }

    def prepare() -> None:
        if not getattr(service, "_owns_cdp_context", False):
            raise RuntimeError("CowAgent lacks the required owned CDP context contract")
        observer = getattr(service, "viewport_observer", None)
        record_page_viewport(
            service._page, evidence, "primary_before_navigation", observer
        )
        if getattr(service, "_supports_multiple_pages", True):
            # Verify inheritance using a blank child before any login/site script.
            child = service._context.new_page()
            try:
                record_page_viewport(
                    child, evidence, "new_page_before_navigation", observer
                )
            finally:
                child.close()
            evidence["page_model"] = "multiple_pages"
        else:
            # Some CDP implementations expose one target per browser session.
            # The primary page still proves the requested viewport was applied.
            evidence["page_model"] = "single_target"
        evidence["retained_external_context_count"] = sum(
            context != service._context for context in service._browser.contexts
        )
        evidence["verified"] = True

    service._submit(prepare)
    return evidence


def bootstrap_postmill_session(context: Any, base_url: str) -> None:
    """Authenticate once, without loading subresources or following redirects.

    The image's header authenticator migrates (and destroys) the previous
    session on every authenticated request. A persistent context header makes
    concurrent thumbnail requests race with CSRF form sessions. The context's
    API client shares the browser cookie jar but does not execute page content.
    Use the configured, host-accessible origin, not a browser-only DNS alias.
    """
    creds = SITE_CREDENTIALS[WebArenaSite.REDDIT]
    response = context.request.get(
        base_url,
        headers={
            SITE_AUTH_HEADERS[WebArenaSite.REDDIT]: (f"{creds['username']}:{creds['password']}")
        },
        max_redirects=0,
        max_retries=0,
        timeout=90_000,
    )
    try:
        if not 200 <= response.status < 300:
            raise RuntimeError(
                f"Postmill session bootstrap requires a direct 2xx response; got {response.status}"
            )
        if not any(
            cookie.get("name") == "PHPSESSID" and cookie.get("value")
            for cookie in context.cookies(base_url)
        ):
            raise RuntimeError("Postmill session bootstrap did not establish a session cookie")
    finally:
        response.dispose()


def bootstrap_postmill_browser_session(context: Any, page: Any, base_url: str) -> None:
    """Establish Postmill auth through the browser when API cookies are not shared."""

    creds = SITE_CREDENTIALS[WebArenaSite.REDDIT]
    page.set_extra_http_headers(
        {
            SITE_AUTH_HEADERS[WebArenaSite.REDDIT]: (
                f"{creds['username']}:{creds['password']}"
            )
        }
    )
    try:
        response = page.goto(base_url, wait_until="commit", timeout=90_000)
    finally:
        page.set_extra_http_headers({})
    if (
        response is None
        or not 200 <= response.status < 300
        or response.request.redirected_from is not None
    ):
        status = response.status if response is not None else "no response"
        raise RuntimeError(
            f"Postmill browser bootstrap requires a direct 2xx response; got {status}"
        )
    if not any(
        cookie.get("name") == "PHPSESSID" and cookie.get("value")
        for cookie in context.cookies(base_url)
    ):
        raise RuntimeError("Postmill browser bootstrap did not establish a session cookie")


def login_context(
    service: Any,
    sites: tuple[WebArenaSite, ...],
    benchmark: WebArenaVerified,
    start_urls: list[str],
    viewport_evidence: dict[str, Any] | None = None,
) -> int:
    def publish_layout(page: Any) -> None:
        publisher = getattr(service, "_publish_page_layout", None)
        if publisher is not None:
            publisher(page)

    def wait_for_login_ready(
        page: Any, selector: str, *, exact_text: str | None = None
    ) -> None:
        waiter = getattr(service, "_wait_for_login_ready", None)
        if waiter is not None:
            waiter(page, selector, exact_text=exact_text)
            return
        if exact_text is not None:
            page.get_by_role("button", name=exact_text, exact=True).wait_for()
            return
        locator = page.locator(selector)
        if "," in selector:
            locator = locator.first
        locator.wait_for()

    def login() -> None:
        context = service._context
        assert context is not None
        primary_page = service._page
        assert primary_page is not None
        for existing_page in context.pages:
            if existing_page != primary_page:
                existing_page.close()
        fresh_context = getattr(service, "_fresh_context_per_task", False)
        if not fresh_context:
            context.clear_cookies()
        auth_headers = {
            SITE_AUTH_HEADERS[site]: (
                f"{SITE_CREDENTIALS[site]['username']}:{SITE_CREDENTIALS[site]['password']}"
            )
            for site in sites
            if site in SITE_AUTH_HEADERS and site != WebArenaSite.REDDIT
        }
        if auth_headers or not fresh_context:
            context.set_extra_http_headers(auth_headers)
        for site in sites:
            base = benchmark.config.get_environment(site).active_url
            creds = SITE_CREDENTIALS.get(site)
            reuse_primary = not getattr(service, "_supports_multiple_pages", True)
            page = primary_page if reuse_primary else context.new_page()
            try:
                if viewport_evidence is not None:
                    record_page_viewport(
                        page,
                        viewport_evidence,
                        "login_before_navigation",
                        getattr(service, "viewport_observer", None),
                    )
                page.set_default_timeout(60_000)
                page.set_default_navigation_timeout(90_000)
                if site == WebArenaSite.SHOPPING:
                    page.goto(f"{base}/customer/account", wait_until="commit")
                    page.evaluate("localStorage.clear(); sessionStorage.clear()")
                    page.reload(wait_until="domcontentloaded")
                    publish_layout(page)
                    wait_for_login_ready(
                        page, ".block-dashboard-info, .box-information"
                    )
                elif site == WebArenaSite.SHOPPING_ADMIN:
                    page.goto(base, wait_until="commit")
                    page.evaluate("localStorage.clear(); sessionStorage.clear()")
                    page.reload(wait_until="domcontentloaded")
                    publish_layout(page)
                    wait_for_login_ready(page, ".page-title")
                elif site == WebArenaSite.REDDIT:
                    if getattr(service, "_supports_context_request_cookie_sharing", True):
                        bootstrap_postmill_session(context, base)
                    else:
                        bootstrap_postmill_browser_session(context, page, base)
                    page.goto(base, wait_until="commit")
                    page.evaluate("localStorage.clear(); sessionStorage.clear()")
                    page.reload(wait_until="domcontentloaded")
                    publish_layout(page)
                    wait_for_login_ready(
                        page, "button", exact_text=creds["username"]
                    )
                elif site == WebArenaSite.GITLAB:
                    page.goto(f"{base}/users/sign_in", wait_until="commit")
                    page.evaluate("localStorage.clear(); sessionStorage.clear()")
                    page.reload(wait_until="domcontentloaded")
                    publish_layout(page)
                    page.get_by_test_id("username-field").fill(creds["username"])
                    publish_layout(page)
                    page.get_by_test_id("password-field").fill(creds["password"])
                    publish_layout(page)
                    sign_in = page.get_by_test_id("sign-in-button")
                    if getattr(service, "_native_login_form_submit", False):
                        service._submit_login_form(page, sign_in)
                    else:
                        sign_in.click()
                    page.wait_for_url("**/", timeout=90_000)
                elif site not in {WebArenaSite.MAP, WebArenaSite.WIKIPEDIA}:
                    raise ValueError(f"unsupported WebArena login site: {site}")
                page.wait_for_timeout(1_000)
            finally:
                if not reuse_primary:
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


def resource_summary(
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


BENCHMARK_VIEWPORT = {"width": 1280, "height": 900}
