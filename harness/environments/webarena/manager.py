"""Create and verify WebArena-Verified containers from immutable inputs."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

import docker
from webarena_verified.environments.container.config import get_container_config
from webarena_verified.environments.container.manager import ContainerManager
from webarena_verified.types.task import WebArenaSite

from .backend import DockerBackend
from .config import RuntimeConfig, SiteConfig, normalized_architecture

STATEFUL_SITES = frozenset({"shopping_admin", "shopping", "reddit", "gitlab"})


class EnvironmentManager:
    def __init__(
        self,
        config: RuntimeConfig,
        client: Any | None = None,
        *,
        defer_removal: bool = False,
    ) -> None:
        self.config = config
        self.client = client or docker.from_env(timeout=900)
        self.backend = DockerBackend(self.client, defer_removal=defer_removal)

    def _require_native_architecture(self, site: SiteConfig) -> str:
        daemon_arch = normalized_architecture(self.client.info().get("Architecture"))
        if daemon_arch != site.architecture:
            raise RuntimeError(
                f"{site.name} requires native {site.architecture}; Docker daemon is {daemon_arch}"
            )
        return daemon_arch

    def _manager(self, site: SiteConfig) -> ContainerManager:
        self._require_native_architecture(site)
        upstream = get_container_config(site=WebArenaSite(site.name))
        locked = upstream.model_copy(
            update={
                "docker_img": site.image,
                "host_port": site.port,
                "host_env_ctrl_port": site.control_port,
            }
        )
        return ContainerManager(
            site=WebArenaSite(site.name),
            config=locked,
            backend=self.backend,
            hostname=self.config.hostname,
        )

    def verify(self, name: str, *, require_running: bool = False) -> dict[str, Any]:
        site = self.config.sites[name]
        manager = self._manager(site)
        daemon_arch = site.architecture
        result: dict[str, Any] = {
            "site": name,
            "daemon_architecture": daemon_arch,
            "required_architecture": site.architecture,
            "requested_image": site.image,
            "container_name": manager.container_name,
        }
        try:
            container = self.client.containers.get(result["container_name"])
        except docker.errors.NotFound as exc:
            if require_running:
                raise RuntimeError(
                    f"WebArena container is missing: {result['container_name']}"
                ) from exc
            result["status"] = "not_created"
            return result
        container.reload()
        image = container.image
        if image is None:
            raise RuntimeError(f"{name} container has no image metadata")
        repo_digests = sorted(image.attrs.get("RepoDigests") or [])
        if site.image not in repo_digests:
            raise RuntimeError(f"{name} image mismatch: expected {site.image}, got {repo_digests}")
        if require_running and container.status != "running":
            raise RuntimeError(f"WebArena container is not running: {result['container_name']}")
        upstream = get_container_config(site=WebArenaSite(name))
        expected_ports = {
            int(upstream.container_port): site.port,
            int(upstream.env_ctrl_port): site.control_port,
        }
        observed_ports = self.backend.get_container_ports(name=str(result["container_name"]))
        if observed_ports != expected_ports:
            raise RuntimeError(
                f"{name} port mapping mismatch: expected {expected_ports}, got {observed_ports}"
            )
        result.update(
            {
                "status": container.status,
                "container_id": container.id,
                "image_id": image.id,
                "repo_digests": repo_digests,
                "port_mappings": observed_ports,
                "site_url": f"http://{self.config.hostname}:{site.port}",
                "control_url": f"http://{self.config.hostname}:{site.control_port}",
            }
        )
        return result

    def start(
        self,
        name: str,
        *,
        reset: bool,
        timeout: int,
        data_dir: str | None = None,
    ) -> dict[str, Any]:
        site = self.config.sites[name]
        manager = self._manager(site)
        if not reset and self.backend.container_running(name=manager.container_name):
            return {"command": "start", **self.verify(name, require_running=True)}
        started = manager.start(
            port=site.port,
            env_ctrl_port=site.control_port,
            data_dir=data_dir,
            wait=False,
        )
        container = self.client.containers.get(manager.container_name)
        self._wait_control(started.env_ctrl_url, timeout=timeout)
        if name in {"gitlab", "reddit"}:
            self._wait_entrypoint_init(container, timeout=timeout)
            self._explicit_init(container, started.url, site=name)
            self._wait_control(started.env_ctrl_url, timeout=timeout)
        elif name in {"shopping", "shopping_admin"}:
            self._wait_entrypoint_init(container, timeout=timeout)
            self._verify_entrypoint_init(container)
        receipt = self.verify(name, require_running=True)
        receipt.update(
            {
                "command": "reset" if reset else "start",
                "site_url": started.url,
                "control_url": started.env_ctrl_url,
                "retired_containers": list(self.backend.retired_containers),
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        return receipt

    def stop(self, name: str) -> dict[str, Any]:
        manager = self._manager(self.config.sites[name])
        manager.stop()
        return {"command": "stop", "site": name, "container_name": manager.container_name}

    @staticmethod
    def _wait_control(url: str, *, timeout: int) -> None:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url.rstrip("/") + "/status", timeout=5) as response:
                    payload = json.load(response)
                    if response.status == 200 and payload.get("success") is True:
                        return
            except (json.JSONDecodeError, OSError, urllib.error.URLError) as exc:
                last_error = exc
            time.sleep(0.5)
        raise RuntimeError(f"WebArena control endpoint did not become ready: {last_error}")

    @staticmethod
    def _wait_entrypoint_init(container: Any, *, timeout: int) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            processes = container.top(ps_args="-eo pid,args").get("Processes") or []
            active = any(
                "environment_control.cli init" in " ".join(map(str, row))
                or re.search(r"\benv-ctrl\b.*\binit\b", " ".join(map(str, row)))
                for row in processes
            )
            if not active:
                return
            time.sleep(0.5)
        raise RuntimeError("WebArena entrypoint initialization did not finish")

    @staticmethod
    def _explicit_init(container: Any, url: str, *, site: str) -> None:
        result = container.exec_run(
            ["env-ctrl", "--verbose", "init", "--base-url", url], demux=False
        )
        output = result.output.decode("utf-8", errors="replace")
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"WebArena {site} init returned invalid JSON") from exc
        if result.exit_code != 0 or payload.get("success") is not True:
            raise RuntimeError(f"WebArena {site} init failed: {payload.get('message')}")

    @staticmethod
    def _verify_entrypoint_init(container: Any) -> None:
        result = container.exec_run(["sh", "-lc", "tail -n 1 /tmp/env-ctrl-init.log"], demux=False)
        output = result.output.decode("utf-8", errors="replace").strip()
        if result.exit_code != 0 or output != "[OK]":
            raise RuntimeError("WebArena entrypoint initialization failed")
