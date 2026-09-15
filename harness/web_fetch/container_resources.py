"""Run one Web Fetch call in an isolated Linux container and account its cgroup."""

from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Any

import docker

from harness.container_isolation import (
    ContainerLimits,
    create_private_bridge,
    require_linux_cgroup_v2,
)
from harness.resource_accounting import (
    DockerStatsResources,
    ResourceSampler,
    delta_counters,
)


@dataclass(frozen=True)
class ContainerCall:
    stdout: bytes
    stderr: bytes
    exit_code: int
    duration_ms: float
    resource: dict[str, int | float]
    environment: dict[str, Any]


class ContainerResourceRunner:
    """Create a disposable container and a dedicated network for one measured call."""

    def __init__(
        self,
        image: str,
        *,
        interval_ms: int = 50,
        cpus: float = 2.0,
        memory: str = "2g",
        pids_limit: int = 512,
    ) -> None:
        if interval_ms < 10:
            raise ValueError("resource sample interval must be at least 10 ms")
        if cpus <= 0:
            raise ValueError("resource CPU limit must be positive")
        self.image = image
        self.interval_ms = int(interval_ms)
        self.cpus = float(cpus)
        self.memory = memory
        self.pids_limit = int(pids_limit)
        self.limits = ContainerLimits(cpus=self.cpus, memory=memory, pids=self.pids_limit)

    def validate(self) -> dict[str, Any]:
        client = docker.from_env()
        info = require_linux_cgroup_v2(client)
        image = client.images.get(self.image)
        attrs = image.attrs
        return {
            "backend": "docker_cgroup_v2",
            "docker_server_version": client.version().get("Version"),
            "docker_os": info.get("OperatingSystem"),
            "docker_architecture": info.get("Architecture"),
            "cgroup_version": info.get("CgroupVersion"),
            "image_id": image.id,
            "image_repo_digests": sorted(attrs.get("RepoDigests") or []),
            "image_architecture": attrs.get("Architecture"),
            "cpus": self.cpus,
            "memory": self.memory,
            "pids_limit": self.pids_limit,
            "sample_interval_ms": self.interval_ms,
            "network_isolation": "one dedicated bridge with no peer containers per call",
            "controller_excluded": True,
        }

    def implementation_identity(
        self, binary: str | None, *, version_args: list[str] | None = None
    ) -> dict[str, Any]:
        environment = self.validate()
        if not binary:
            return {**environment, "binary_sha256": None, "reported_version": None}
        client = docker.from_env()
        digest_output = client.containers.run(
            self.image,
            [binary],
            entrypoint="sha256sum",
            network_disabled=True,
            remove=True,
        ).decode("utf-8", errors="replace")
        match = re.match(r"^([0-9a-f]{64})\s", digest_output)
        if not match:
            raise RuntimeError(f"cannot identify resource binary {binary}")
        reported = None
        probe_error = None
        try:
            output = client.containers.run(
                self.image,
                version_args or ["--version"],
                entrypoint=binary,
                network_disabled=True,
                remove=True,
                stderr=True,
            ).decode("utf-8", errors="replace").strip()
            reported = output[:200] or None
        except Exception as exc:
            probe_error = type(exc).__name__
        return {
            **environment,
            "binary_sha256": match.group(1),
            "reported_version": reported,
            "version_probe_error": probe_error,
        }

    def run(self, request: dict[str, Any], *, timeout: float) -> ContainerCall:
        call_started = time.perf_counter()
        client = docker.from_env()
        environment = self.validate()
        call_id = uuid.uuid4().hex[:12]
        labels = {"lex.web-fetch-resource.call": call_id}
        network = create_private_bridge(
            client, f"lex-web-fetch-resource-{call_id}", labels
        )
        container = None
        try:
            container = client.containers.create(
                self.image,
                command=["sleep", "infinity"],
                detach=True,
                labels=labels,
                **self.limits.docker_kwargs(drop_all_capabilities=True),
                network=network.name,
            )
            container.start()
            source = DockerStatsResources(container)
            before = source.counters()
            sampler = ResourceSampler(source, interval_ms=self.interval_ms)
            sampler.start()
            started = time.perf_counter()
            cleanup_reserve = min(5.0, max(1.0, timeout * 0.1))
            worker_timeout = max(
                0.1,
                timeout - (time.perf_counter() - call_started) - cleanup_reserve,
            )
            try:
                completed = subprocess.run(
                    [
                        "docker",
                        "exec",
                        "-i",
                        container.id,
                        "/workspace/.venv/bin/python",
                        "/workspace/scripts/headless_worker.py",
                    ],
                    input=json.dumps(request).encode("utf-8"),
                    capture_output=True,
                    timeout=worker_timeout,
                    check=False,
                )
                exit_code = completed.returncode
                stdout = completed.stdout
                stderr = completed.stderr
            except subprocess.TimeoutExpired as exc:
                exit_code = 124
                stdout = exc.stdout or b""
                stderr = (exc.stderr or b"") + b"\ncontainer worker timed out"
            finally:
                duration_ms = (time.perf_counter() - started) * 1000
                sampler.stop()
            after = source.counters()
            resource = {**delta_counters(before, after), **sampler.summary()}
            resource["measurement_duration_ms"] = duration_ms
            resource["cpu_average_utilization_pct"] = (
                resource["cpu_ms"] / duration_ms * 100 if duration_ms else 0.0
            )
            resource["network_rx_bytes_per_second"] = (
                resource["network_rx_bytes"] / duration_ms * 1000 if duration_ms else 0.0
            )
            resource["network_tx_bytes_per_second"] = (
                resource["network_tx_bytes"] / duration_ms * 1000 if duration_ms else 0.0
            )
            return ContainerCall(
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                duration_ms=duration_ms,
                resource=resource,
                environment=environment,
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            try:
                network.remove()
            except Exception:
                pass
