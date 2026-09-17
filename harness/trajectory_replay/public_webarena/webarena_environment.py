"""Reset, authenticate, and open WebArena-Verified task environments."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import docker
from webarena_verified.environments.container.config import get_container_config
from webarena_verified.environments.container.manager import ContainerManager
from webarena_verified.types.task import WebArenaSite

from harness.environments.webarena.backend import MAGENTO_INIT_TIMEOUT_SECONDS, DockerBackend
from harness.environments.webarena.manager import EnvironmentManager
from harness.environments.webarena.rainbond import RainbondSlicePool

from .suite import BrowserTask

STATEFUL_SITES = frozenset({"shopping_admin", "shopping", "reddit", "gitlab"})
LEGACY_PORT_SITES = {
    3000: "map",
    4399: "reddit",
    7770: "shopping",
    7780: "shopping_admin",
    8023: "gitlab",
    8888: "wikipedia",
    9999: "reddit",
}
_LEGACY_ORIGIN_RE = re.compile(
    r"https?://[a-z0-9.-]+:(?P<port>3000|4399|7770|7780|8023|8888|9999)",
    flags=re.IGNORECASE,
)


@contextmanager
def wall_clock_timeout(seconds: float, *, operation: str = "environment reset"):
    """Bound one blocking environment phase, including Docker SDK calls, on POSIX."""
    if seconds <= 0:
        raise ValueError("wall-clock timeout must be positive")
    if threading.current_thread() is not threading.main_thread() or not hasattr(
        signal, "setitimer"
    ):
        raise RuntimeError("WebArena reset wall-clock timeout requires the POSIX main thread")
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if previous_timer != (0.0, 0.0):
        raise RuntimeError("WebArena reset refuses to replace an active process timer")
    previous_handler = signal.getsignal(signal.SIGALRM)

    def expire(_signum: int, _frame: Any) -> None:
        raise TimeoutError(f"WebArena {operation} exceeded {seconds:g}s wall-clock deadline")

    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


class WebArenaEnvironment:
    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path)
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.environments = self.config.get("environments") or {}
        self.control_urls = self.config.get("control_urls") or {}
        lock_value = self.config.get("image_lock")
        if not isinstance(lock_value, str) or not lock_value:
            raise ValueError("WebArena config requires image_lock")
        lock_path = Path(lock_value)
        self.image_lock_path = (
            lock_path
            if lock_path.is_absolute()
            else (self.config_path.parent / lock_path).resolve()
        )
        self.image_lock = json.loads(self.image_lock_path.read_text(encoding="utf-8"))
        if self.image_lock.get("schema") != "lex_webarena_environment_images/1":
            raise ValueError("WebArena image lock schema is invalid")
        self.remote_receipts = self.config.get("remote_runtime_receipts") or {}
        slice_value = self.config.get("rainbond_slice")
        self.rainbond_pool: RainbondSlicePool | None = None
        self.rainbond_receipt_path: Path | None = None
        if slice_value is not None:
            if not isinstance(slice_value, dict):
                raise ValueError("rainbond_slice must be an object")
            pool_raw = str(slice_value.get("pool_config") or "").strip()
            receipt_raw = str(slice_value.get("receipt") or "").strip()
            if not pool_raw or not receipt_raw:
                raise ValueError("rainbond_slice requires pool_config and receipt")
            pool_value = Path(pool_raw)
            receipt_value = Path(receipt_raw)
            pool_path = (
                pool_value
                if pool_value.is_absolute()
                else (self.config_path.parent / pool_value).resolve()
            )
            self.rainbond_receipt_path = (
                receipt_value
                if receipt_value.is_absolute()
                else (self.config_path.parent / receipt_value).resolve()
            )
            self.rainbond_pool = RainbondSlicePool(pool_path)
            current_receipt = self.rainbond_pool.validate_receipt(self.rainbond_receipt_path)
            current_runtime = self.rainbond_pool.runtime_config(current_receipt)
            self.config = {**self.config, **current_runtime}
            self.environments = self.config["environments"]

    def site_docker_client(self, default_client: Any) -> tuple[Any, bool]:
        host = self.config.get("site_docker_host")
        if host is None:
            return default_client, False
        value = str(host)
        if not value.startswith("unix:///"):
            raise ValueError("site_docker_host must be an absolute unix socket URL")
        # Recreating the large Shopping/Admin/GitLab images can spend more than
        # Docker SDK's 60-second default inside a force-remove request.  The
        # daemon continues the deletion, but the premature client timeout
        # aborts the qualification shard before any task can run.
        client = docker.DockerClient(base_url=value, timeout=900)
        client.ping()
        return client, True

    def verify_runtime(self, docker_client: Any, tasks: tuple[BrowserTask, ...]) -> dict[str, Any]:
        sites = sorted(
            {str(site) for task in tasks for site in (task.environment or {}).get("sites") or []}
        )
        rows: dict[str, Any] = {}
        slice_receipt = (
            self.rainbond_pool.validate_receipt(self.rainbond_receipt_path)
            if self.rainbond_pool is not None and self.rainbond_receipt_path is not None
            else None
        )
        for site in sites:
            locked = self.image_lock["images"][site]
            expected = str(locked["pinned"])
            if slice_receipt is not None and site in STATEFUL_SITES:
                component = (slice_receipt.get("components") or {}).get(site) or {}
                if component.get("image") != expected:
                    raise RuntimeError(f"Rainbond slice image mismatch for {site}")
                site_url = str(component.get("site_url") or "")
                configured_urls = self._site_config(site).get("urls") or []
                if configured_urls != [site_url]:
                    raise RuntimeError(f"Rainbond slice URL mismatch for {site}")
                self._wait_site_ready(site_url, timeout_seconds=60)
                rows[site] = {
                    "mode": "remote_rainbond_isolated_slice",
                    "app_id": slice_receipt["app_id"],
                    "generation": slice_receipt["generation"],
                    "service_id": component["service_id"],
                    "pinned_image": expected,
                    "receipt_sha256": hashlib.sha256(
                        self.rainbond_receipt_path.read_bytes()
                    ).hexdigest(),
                }
                continue
            receipt_value = self.remote_receipts.get(site)
            if receipt_value is not None:
                if site in STATEFUL_SITES:
                    raise ValueError(f"remote runtime is allowed only for read-only sites: {site}")
                receipt_path = Path(str(receipt_value))
                if not receipt_path.is_absolute():
                    receipt_path = (self.config_path.parent / receipt_path).resolve()
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if (
                    receipt.get("command") != "start"
                    or receipt.get("site") != site
                    or receipt.get("requested_image") != expected
                    or expected not in (receipt.get("repo_digests") or [])
                ):
                    raise RuntimeError(f"WebArena remote receipt is invalid for {site}")
                site_config = self._site_config(site)
                site_urls = site_config.get("urls") or []
                index = int(site_config.get("active_url_idx") or 0)
                if not site_urls or index < 0 or index >= len(site_urls):
                    raise ValueError(f"WebArena environment has no active URL for site {site!r}")
                self._wait_site_ready(str(site_urls[index]), timeout_seconds=60)
                rows[site] = {
                    "mode": "remote_read_only",
                    "pinned_image": expected,
                    "receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                }
                continue
            daemon_arch = str(docker_client.info().get("Architecture") or "")
            daemon_arch = {"aarch64": "arm64", "x86_64": "amd64"}.get(daemon_arch, daemon_arch)
            image_arch = str(locked.get("architecture") or "")
            if image_arch and image_arch != daemon_arch:
                raise RuntimeError(
                    f"WebArena {site} requires native {image_arch}; Docker daemon is {daemon_arch}"
                )
            container_name = f"webarena_verified_{site}"
            try:
                container = docker_client.containers.get(container_name)
            except Exception as exc:
                raise RuntimeError(f"WebArena container is not running: {container_name}") from exc
            container.reload()
            if container.status != "running":
                raise RuntimeError(f"WebArena container is not running: {container_name}")
            repo_digests = sorted(container.image.attrs.get("RepoDigests") or [])
            if expected not in repo_digests:
                raise RuntimeError(
                    f"WebArena {site} image mismatch: expected {expected}, got {repo_digests}"
                )
            rows[site] = {
                "mode": "local_docker",
                "container_name": container_name,
                "container_id": container.id,
                "image_id": container.image.id,
                "pinned_image": expected,
            }
        return {
            "image_lock_sha256": hashlib.sha256(self.image_lock_path.read_bytes()).hexdigest(),
            "sites": rows,
        }

    def _site_config(self, site: str) -> dict[str, Any]:
        for key in (site, site.lower(), site.upper(), f"__{site.upper()}__"):
            value = self.environments.get(key)
            if isinstance(value, dict):
                return value
        raise ValueError(f"WebArena environment config is missing site {site!r}")

    def _credentials(self, site: str) -> dict[str, str]:
        config = self._site_config(site)
        direct = config.get("credentials")
        references = config.get("credentials_env")
        if direct is not None and references is not None:
            raise ValueError(f"WebArena {site} credentials are configured twice")
        if references is not None:
            if not isinstance(references, dict) or set(references) != {"username", "password"}:
                raise ValueError(
                    f"WebArena {site} credentials_env requires username and password variable names"
                )
            names = {key: str(value or "").strip() for key, value in references.items()}
            if not all(names.values()):
                raise ValueError(
                    f"WebArena {site} credentials_env requires non-empty variable names"
                )
            missing = [name for name in names.values() if not os.environ.get(name)]
            if missing:
                raise ValueError(
                    f"WebArena {site} credential environment variables are missing: {missing}"
                )
            return {key: os.environ[name] for key, name in names.items()}
        if not isinstance(direct, dict):
            return {}
        return {
            "username": str(direct.get("username") or ""),
            "password": str(direct.get("password") or ""),
        }

    def resolve_url(self, value: str) -> str:
        if not value.startswith("__"):
            return value
        token = next(
            (
                key
                for key in sorted(self.environments, key=len, reverse=True)
                if value == key or value.startswith(key + "/") or value.startswith(key + "?")
            ),
            None,
        )
        config = self.environments.get(token) if token is not None else None
        if not isinstance(config, dict):
            raise ValueError(f"WebArena environment token is not configured: {value}")
        urls = config.get("urls") or []
        index = int(config.get("active_url_idx") or 0)
        if not urls or index >= len(urls):
            raise ValueError(f"WebArena environment token has no active URL: {value}")
        base = str(urls[index])
        suffix = value[len(token) :]
        return base.rstrip("/") + suffix if suffix else base

    def rewrite_legacy_origins(self, value: Any) -> Any:
        """Materialize baked WebArena origins inside recorded action arguments."""
        if isinstance(value, dict):
            return {key: self.rewrite_legacy_origins(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.rewrite_legacy_origins(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.rewrite_legacy_origins(item) for item in value)
        if not isinstance(value, str):
            return value

        def replace(match: re.Match[str]) -> str:
            site = LEGACY_PORT_SITES[int(match.group("port"))]
            config = self._site_config(site)
            urls = config.get("urls") or []
            index = int(config.get("active_url_idx") or 0)
            if not urls or index < 0 or index >= len(urls):
                raise ValueError(f"WebArena environment has no active URL for site {site!r}")
            active = urlparse(str(urls[index]))
            if active.scheme not in {"http", "https"} or not active.netloc:
                raise ValueError(f"WebArena environment URL is invalid for site {site!r}")
            return f"{active.scheme}://{active.netloc}"

        return _LEGACY_ORIGIN_RE.sub(replace, value)

    def materialize_task_actions(self, task: BrowserTask) -> BrowserTask:
        """Resolve runtime-specific origins without changing the canonical contract."""
        calls = tuple(self.rewrite_legacy_origins(call) for call in task.tool_calls)
        return BrowserTask(
            task_id=task.task_id,
            title=task.title,
            tool_calls=calls,
            assertions=task.assertions,
            tags=task.tags,
            difficulty=task.difficulty,
            ground_truth=task.ground_truth,
            source=task.source,
            evaluator=task.evaluator,
            environment=task.environment,
            trajectory=task.trajectory,
        )

    @staticmethod
    def _explicit_port(url: str, *, label: str) -> int:
        try:
            parsed = urlparse(url)
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"WebArena {label} URL has an invalid port: {url!r}") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or port is None:
            raise ValueError(
                f"WebArena {label} URL must be absolute and include an explicit port: {url!r}"
            )
        return port

    def _local_ports(self, site: str) -> tuple[int, int]:
        site_config = self._site_config(site)
        urls = site_config.get("urls") or []
        index = int(site_config.get("active_url_idx") or 0)
        if not urls or index < 0 or index >= len(urls):
            raise ValueError(f"WebArena environment has no active URL for site {site!r}")
        site_port = self._explicit_port(str(urls[index]), label=f"{site} site")
        control_port = self._explicit_port(self._control_url(site), label=f"{site} control")
        if site_port == control_port:
            raise ValueError(f"WebArena {site} site and control ports must differ")
        return site_port, control_port

    def reset(
        self, task: BrowserTask, docker_client: Any, *, timeout_seconds: float = 900
    ) -> dict[str, Any]:
        with wall_clock_timeout(timeout_seconds):
            return self._reset_impl(task, docker_client, timeout_seconds=timeout_seconds)

    def _reset_impl(
        self, task: BrowserTask, docker_client: Any, *, timeout_seconds: float
    ) -> dict[str, Any]:
        sites = list((task.environment or {}).get("sites") or [])
        if self.rainbond_pool is not None and any(site in STATEFUL_SITES for site in sites):
            assert self.rainbond_receipt_path is not None
            receipt = self.rainbond_pool.reset(self.rainbond_receipt_path)
            self.config = self.rainbond_pool.persist_runtime_config(
                receipt,
                self.config_path,
                base_config=self.config,
            )
            self.environments = self.config["environments"]
            for site in sites:
                site_config = self._site_config(site)
                site_urls = site_config.get("urls") or []
                index = int(site_config.get("active_url_idx") or 0)
                if not site_urls or index < 0 or index >= len(site_urls):
                    raise ValueError(f"WebArena environment has no active URL for site {site!r}")
                self._wait_site_ready(str(site_urls[index]), timeout_seconds=timeout_seconds)
            return {
                "mode": "remote_rainbond_isolated_slice",
                "reset": "application_deleted_and_recreated_from_pinned_baseline",
                "app_id": receipt["app_id"],
                "generation": receipt["generation"],
                "sites": {
                    site: {
                        "service_id": receipt["components"][site]["service_id"],
                        "site_url": receipt["components"][site]["site_url"],
                    }
                    for site in sites
                    if site in receipt["components"]
                },
            }
        receipts: dict[str, Any] = {}
        for site in sites:
            if site in self.remote_receipts:
                if site in STATEFUL_SITES:
                    raise ValueError(f"remote reset is unsupported for stateful site {site}")
                site_config = self._site_config(site)
                site_urls = site_config.get("urls") or []
                index = int(site_config.get("active_url_idx") or 0)
                if not site_urls or index < 0 or index >= len(site_urls):
                    raise ValueError(f"WebArena environment has no active URL for site {site!r}")
                self._wait_site_ready(str(site_urls[index]), timeout_seconds=timeout_seconds)
                receipts[site] = {
                    "mode": "remote_read_only",
                    "reset": "site_health_check_only",
                }
                continue
            url = self._control_url(site)
            if site in STATEFUL_SITES:
                locked = self.image_lock["images"][site]
                upstream = get_container_config(site=WebArenaSite(site))
                site_port, control_port = self._local_ports(site)
                config = upstream.model_copy(
                    update={
                        "docker_img": str(locked["pinned"]),
                        "host_port": site_port,
                        "host_env_ctrl_port": control_port,
                    }
                )
                defer_removal = bool(self.config.get("defer_stateful_container_removal", False))
                backend = DockerBackend(docker_client, defer_removal=defer_removal)
                manager = ContainerManager(
                    site=WebArenaSite(site),
                    config=config,
                    backend=backend,
                    hostname=str(self.config.get("docker_site_hostname") or "host.docker.internal"),
                )
                started = manager.start(
                    port=int(config.host_port),
                    env_ctrl_port=int(config.host_env_ctrl_port),
                    wait=False,
                )
                container = docker_client.containers.get(manager.container_name)
                container.reload()
                receipts[site] = {
                    "mode": "local_docker",
                    "reset": (
                        "container_recreated_from_pinned_image_deferred_gc"
                        if defer_removal
                        else "container_recreated_from_pinned_image"
                    ),
                    "container_name": manager.container_name,
                    "site_url": started.url,
                    "control_url": started.env_ctrl_url,
                    "pinned_image": str(locked["pinned"]),
                    "container_id": container.id,
                    "image_id": container.image.id,
                    "repo_digests": sorted(container.image.attrs.get("RepoDigests") or []),
                    "retired_containers": list(backend.retired_containers),
                }
                self._wait_ready(url, timeout_seconds=timeout_seconds)
                if site == "gitlab":
                    self._configure_external_url(container, started.url, site=site)
                    self._wait_ready(url, timeout_seconds=timeout_seconds)
                    receipts[site]["external_url_configuration"] = {
                        "external_url": started.url,
                        "internal_port": 8023,
                        "mode": "explicit_official_init_verified",
                    }
                elif site == "reddit":
                    # Reddit's official Ops implementation does not read
                    # WA_ENV_CTRL_EXTERNAL_SITE_URL, so its supervisor attempt
                    # exits before mutating state. Supply the URL explicitly
                    # after that no-op attempt has exited.
                    self._wait_container_initialization(container, timeout_seconds=timeout_seconds)
                    self._configure_external_url(container, started.url, site=site)
                    self._wait_ready(url, timeout_seconds=timeout_seconds)
                    receipts[site]["external_url_configuration"] = {
                        "external_url": started.url,
                        "mode": "explicit_official_init_verified",
                    }
                else:
                    # Shopping and Shopping Admin run their one env-ctrl init
                    # from the official image entrypoint.
                    self._wait_container_initialization(container, timeout_seconds=timeout_seconds)
                    self._verify_entrypoint_initialization(container)
                    receipts[site]["external_url_configuration"] = {
                        "external_url": started.url,
                        "mode": "entrypoint_init_verified",
                        **(
                            {"command_timeout_seconds": MAGENTO_INIT_TIMEOUT_SECONDS}
                            if site in {"shopping", "shopping_admin"}
                            else {}
                        ),
                    }
            else:
                receipts[site] = {
                    "mode": "local_docker",
                    "reset": "site_health_check_only",
                }
            if site in STATEFUL_SITES:
                self._wait_ready(url, timeout_seconds=timeout_seconds)
            site_config = self._site_config(site)
            site_urls = site_config.get("urls") or []
            index = int(site_config.get("active_url_idx") or 0)
            if not site_urls or index < 0 or index >= len(site_urls):
                raise ValueError(f"WebArena environment has no active URL for site {site!r}")
            self._wait_site_ready(str(site_urls[index]), timeout_seconds=timeout_seconds)
        return {"sites": receipts}

    def _control_url(self, site: str) -> str:
        url = self.control_urls.get(site) or self.control_urls.get(f"__{site.upper()}__")
        if not isinstance(url, str) or not url:
            raise ValueError(f"WebArena control URL is missing for site {site!r}")
        return url

    @staticmethod
    def _wait_ready(url: str, *, timeout_seconds: float) -> None:
        EnvironmentManager._wait_control(url, timeout=timeout_seconds)

    @staticmethod
    def _wait_container_initialization(container: Any, *, timeout_seconds: float) -> None:
        EnvironmentManager._wait_entrypoint_init(container, timeout=timeout_seconds)

    @staticmethod
    def _configure_external_url(container: Any, external_url: str, *, site: str) -> None:
        EnvironmentManager._explicit_init(container, external_url, site=site)

    @staticmethod
    def _verify_entrypoint_initialization(container: Any) -> None:
        EnvironmentManager._verify_entrypoint_init(container)

    @staticmethod
    def _wait_site_ready(url: str, *, timeout_seconds: float) -> None:
        """Require two successful public-site responses after control-plane readiness."""
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        consecutive = 0
        while time.monotonic() < deadline:
            try:
                request = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(request, timeout=30) as response:
                    expected = urlparse(url)
                    observed = urlparse(str(getattr(response, "geturl", lambda: url)()))
                    same_origin = (
                        expected.scheme,
                        expected.hostname,
                        expected.port,
                    ) == (observed.scheme, observed.hostname, observed.port)
                    if response.status < 500 and same_origin:
                        consecutive += 1
                        if consecutive == 2:
                            return
                    else:
                        consecutive = 0
                        if not same_origin:
                            last_error = RuntimeError(
                                f"site redirected to unexpected origin: {observed.geturl()}"
                            )
            except urllib.error.HTTPError as exc:
                expected = urlparse(url)
                observed = urlparse(str(exc.geturl()))
                same_origin = (
                    expected.scheme,
                    expected.hostname,
                    expected.port,
                ) == (observed.scheme, observed.hostname, observed.port)
                if exc.code < 500 and same_origin:
                    consecutive += 1
                    if consecutive == 2:
                        return
                else:
                    consecutive = 0
                    last_error = (
                        exc
                        if same_origin
                        else RuntimeError(
                            f"site redirected to unexpected origin: {observed.geturl()}"
                        )
                    )
            except (OSError, urllib.error.URLError, TimeoutError) as exc:
                consecutive = 0
                last_error = exc
            time.sleep(0.5)
        raise RuntimeError(f"WebArena site did not become ready: {last_error}")

    def prepare(self, task: BrowserTask, page: Any, *, timeout_seconds: float = 300) -> Any:
        with wall_clock_timeout(timeout_seconds, operation="environment prepare"):
            return self._prepare_impl(task, page)

    def _prepare_impl(self, task: BrowserTask, page: Any) -> Any:
        environment = task.environment or {}
        if environment.get("kind") != "webarena_verified":
            return page
        sites = list(environment.get("sites") or [])
        for site in sites:
            login = getattr(self, f"_login_{site}", None)
            if login is not None:
                login(page)

        start_urls = [self.resolve_url(str(value)) for value in environment.get("start_urls") or []]
        if not start_urls:
            raise ValueError(f"{task.task_id}: WebArena start_urls are missing")
        pages = [page]
        for _ in start_urls[1:]:
            extra = page.context.new_page()
            extra.set_viewport_size({"width": 1280, "height": 720})
            pages.append(extra)
        for target_page, url in zip(pages, start_urls, strict=True):
            target_page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        return page

    def _login_credentials(self, site: str) -> tuple[str, str]:
        credentials = self._credentials(site)
        username = str(credentials.get("username") or "")
        password = str(credentials.get("password") or "")
        if not username or not password:
            raise ValueError(f"WebArena credentials are missing for site {site!r}")
        return username, password

    def _login_shopping(self, page: Any) -> None:
        config = self._site_config("shopping")
        username, password = self._login_credentials("shopping")
        base_url = str((config.get("urls") or [""])[0]).rstrip("/")
        if not base_url:
            raise ValueError("WebArena Shopping URL is missing")
        page.goto(
            base_url + "/customer/account/login/",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        email = page.get_by_label("Email", exact=True)
        if email.count():
            email.fill(username)
            page.get_by_label("Password", exact=True).fill(password)
            with page.expect_navigation(wait_until="commit", timeout=120_000):
                page.get_by_role("button", name="Sign In", exact=True).click(
                    no_wait_after=True, timeout=30_000
                )
            # Do not race the submitted form's redirect with the canonical
            # authenticated-route check below.
        page.goto(base_url + "/wishlist/", wait_until="domcontentloaded", timeout=60_000)
        if "/customer/account/login" in page.url:
            raise RuntimeError("WebArena Shopping login failed")

    def _login_shopping_admin(self, page: Any) -> None:
        config = self._site_config("shopping_admin")
        username, password = self._login_credentials("shopping_admin")
        base_url = str((config.get("urls") or [""])[0]).rstrip("/")
        if not base_url:
            raise ValueError("WebArena Shopping Admin URL is missing")
        page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
        user_field = page.get_by_placeholder("user name")
        if user_field.count():
            user_field.fill(username)
            page.get_by_placeholder("password").fill(password)
            with page.expect_navigation(wait_until="commit", timeout=120_000):
                page.get_by_role("button", name="Sign in", exact=False).click(
                    no_wait_after=True, timeout=30_000
                )
            # Magento's admin shell may keep subresources pending after the
            # authenticated redirect.  Re-open the canonical URL instead of
            # tying login success to the old form locator's navigation wait.
            page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
        if page.get_by_placeholder("user name").count():
            raise RuntimeError("WebArena Shopping Admin login failed")

    def _login_reddit(self, page: Any) -> None:
        config = self._site_config("reddit")
        username, password = self._login_credentials("reddit")
        base_url = str((config.get("urls") or [""])[0]).rstrip("/")
        if not base_url:
            raise ValueError("WebArena Reddit URL is missing")
        page.goto(base_url + "/login", wait_until="domcontentloaded", timeout=60_000)
        user_field = page.get_by_label("Username")
        if user_field.count():
            user_field.fill(username)
            page.get_by_label("Password").fill(password)
            with page.expect_navigation(wait_until="commit", timeout=120_000):
                page.get_by_role("button", name="Log in", exact=True).click(
                    no_wait_after=True, timeout=30_000
                )
            # Supersede the completed form redirect with a deterministic
            # authenticated route instead of reading from the stale form.
        post_login_url = page.url
        page.goto(
            f"{base_url}/user/{username}/account",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        if "/login" in page.url:
            try:
                messages = page.locator(
                    "[role='alert'], .alert, .form-error, .invalid-feedback"
                ).all_inner_texts()
            except Exception as exc:
                messages = [f"diagnostic capture failed: {type(exc).__name__}"]
            raise RuntimeError(
                "WebArena Reddit login failed; "
                f"post_login_url={post_login_url!r}; messages={messages[:3]!r}"
            )

    def _login_gitlab(self, page: Any) -> None:
        config = self._site_config("gitlab")
        username, password = self._login_credentials("gitlab")
        urls = config.get("urls") or []
        if not urls:
            raise ValueError("WebArena GitLab URL is missing")
        page.goto(str(urls[0]).rstrip("/") + "/users/sign_in", wait_until="domcontentloaded")
        if page.locator("#user_login").count():
            user_field = page.locator("#user_login")
            user_field.fill(username)
            page.locator("#user_password").fill(password)
            page.get_by_role("button", name="Sign in", exact=False).click(
                no_wait_after=True, timeout=30_000
            )
            user_field.wait_for(state="detached", timeout=120_000)
        if page.locator("#user_login").count():
            raise RuntimeError("WebArena GitLab login failed")
