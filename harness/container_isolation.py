"""Shared Linux-container isolation policy for resource-bearing benchmarks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContainerLimits:
    cpus: float = 2.0
    memory: str = "2g"
    pids: int = 512
    shm_size: str = "512m"

    def docker_kwargs(self, *, drop_all_capabilities: bool = False) -> dict[str, Any]:
        if self.cpus <= 0 or self.pids <= 0:
            raise ValueError("container CPU and PID limits must be positive")
        values: dict[str, Any] = {
            "nano_cpus": int(self.cpus * 1_000_000_000),
            "mem_limit": self.memory,
            "pids_limit": self.pids,
            "shm_size": self.shm_size,
            "security_opt": ["no-new-privileges"],
        }
        if drop_all_capabilities:
            values["cap_drop"] = ["ALL"]
        return values


def require_linux_cgroup_v2(client: Any) -> dict[str, Any]:
    """Fail closed when the engine cannot provide the publication accounting contract."""
    info = client.info()
    if info.get("OSType") != "linux":
        raise RuntimeError("resource publication requires a Linux Docker engine")
    if info.get("CgroupVersion") != "2":
        raise RuntimeError("resource publication requires cgroup v2")
    return info


def create_private_bridge(client: Any, name: str, labels: dict[str, str]) -> Any:
    """Create a benchmark-owned bridge; callers must remove it in a finally block."""
    return client.networks.create(name, driver="bridge", labels=labels)


__all__ = ["ContainerLimits", "create_private_bridge", "require_linux_cgroup_v2"]
