"""Docker backend with WebArena initialization safeguards."""

from __future__ import annotations

import socket
from typing import Any

import docker

MAGENTO_INIT_TIMEOUT_SECONDS = 600


class DockerBackend:
    """Bind WebArena's container manager to an explicit Docker SDK client."""

    def __init__(self, client: Any, *, defer_removal: bool = False) -> None:
        self.client = client
        self.defer_removal = defer_removal
        self.retired_containers: list[str] = []

    def _container(self, name: str) -> Any | None:
        try:
            return self.client.containers.get(name)
        except docker.errors.NotFound:
            return None

    def container_exists(self, *, name: str) -> bool:
        return self._container(name) is not None

    def container_running(self, *, name: str) -> bool:
        container = self._container(name)
        if container is None:
            return False
        container.reload()
        return container.status == "running"

    def container_remove(self, *, name: str) -> None:
        container = self._container(name)
        if container is not None:
            if not self.defer_removal:
                container.remove(force=True)
                return
            # Removing WebArena's tens-of-gigabytes writable layer can block a
            # rootless daemon for hours.  Stopping releases the fixed ports;
            # renaming preserves the old layer for deferred garbage collection.
            # The next container still starts from the immutable pinned image,
            # and these official images have no shared writable mounts.
            container.reload()
            if container.status == "running":
                container.stop(timeout=10)
            retired_name = f"{name}__retired__{container.id[:12]}"
            container.rename(retired_name)
            self.retired_containers.append(retired_name)

    def container_run(
        self,
        *,
        name: str,
        image: str,
        port_mappings: dict[int, int],
        volume_mappings: dict[str, str],
        env_vars: dict[str, str] | None = None,
    ) -> None:
        environment = dict(env_vars or {})
        if self.defer_removal and volume_mappings:
            raise RuntimeError(
                "deferred WebArena container removal requires no shared writable mounts"
            )
        if name in {
            "webarena_verified_shopping",
            "webarena_verified_shopping_admin",
        }:
            # Magento's official init performs cache/database work after the
            # services become healthy.  The image default of 180 seconds is
            # too short on a busy qualification host and can leave the baked
            # CMU origin active even though env-ctrl /status is healthy.
            environment["WA_ENV_CTRL_CMD_TIMEOUT_SEC"] = str(MAGENTO_INIT_TIMEOUT_SECONDS)
        # GitLab's image entrypoint does not run env-ctrl init.  Its fallback
        # reconfiguration also rewrites the internal nginx listener to the
        # public host port, breaking Docker's host-port -> 8023 mapping.  Keep
        # the baked internal listener here; WebArenaEnvironment performs one
        # explicit official init after the service is healthy.
        if name == "webarena_verified_gitlab":
            environment["WA_ENV_CTRL_SKIP_RECONFIGURE"] = "true"
        self.client.containers.run(
            image,
            detach=True,
            name=name,
            ports={
                f"{container_port}/tcp": host_port
                for host_port, container_port in port_mappings.items()
            },
            volumes={
                source: {"bind": target, "mode": "rw"} for source, target in volume_mappings.items()
            },
            environment=environment,
        )

    def get_container_ports(self, *, name: str) -> dict[int, int]:
        container = self._container(name)
        if container is None:
            return {}
        container.reload()
        result: dict[int, int] = {}
        ports = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
        for key, bindings in ports.items():
            if bindings:
                result[int(str(key).split("/", 1)[0])] = int(bindings[0]["HostPort"])
        return result

    def find_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as value:
            value.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            value.bind(("", 0))
            return int(value.getsockname()[1])
