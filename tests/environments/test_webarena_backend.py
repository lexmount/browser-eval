from __future__ import annotations

from types import SimpleNamespace

import docker
import pytest
from webarena_verified.environments.container.config import get_container_config
from webarena_verified.environments.container.manager import ContainerManager
from webarena_verified.types.task import WebArenaSite

from harness.environments.webarena.backend import DockerBackend


def test_container_run_translates_upstream_host_to_container_port_mapping() -> None:
    calls = []
    containers = SimpleNamespace(run=lambda *args, **kwargs: calls.append((args, kwargs)))
    backend = DockerBackend(SimpleNamespace(containers=containers))

    backend.container_run(
        name="webarena_verified_map",
        image="example/map@sha256:" + "a" * 64,
        port_mappings={23000: 3000, 23001: 5555},
        volume_mappings={},
    )

    assert calls[0][1]["ports"] == {"3000/tcp": 23000, "5555/tcp": 23001}


@pytest.mark.parametrize(
    ("site", "expected_variable", "expected_value"),
    [
        (WebArenaSite.SHOPPING, "WA_ENV_CTRL_CMD_TIMEOUT_SEC", "600"),
        (WebArenaSite.SHOPPING_ADMIN, "WA_ENV_CTRL_CMD_TIMEOUT_SEC", "600"),
        (WebArenaSite.GITLAB, "WA_ENV_CTRL_SKIP_RECONFIGURE", "true"),
    ],
)
def test_pinned_upstream_start_uses_expected_name_ports_and_environment(
    site: WebArenaSite, expected_variable: str, expected_value: str
) -> None:
    calls = []

    class Containers:
        def get(self, _name: str):
            raise docker.errors.NotFound("missing")

        def run(self, *args, **kwargs):
            calls.append((args, kwargs))

    backend = DockerBackend(SimpleNamespace(containers=Containers()))
    manager = ContainerManager(site=site, backend=backend)
    config = get_container_config(site=site)

    manager.start(port=23000, env_ctrl_port=23001, wait=False)

    assert manager.container_name == f"webarena_verified_{site.value}"
    assert calls[0][1]["name"] == manager.container_name
    assert calls[0][1]["ports"] == {
        f"{config.container_port}/tcp": 23000,
        f"{config.env_ctrl_port}/tcp": 23001,
    }
    assert calls[0][1]["environment"][expected_variable] == expected_value


def test_deferred_removal_stops_and_retains_the_named_container() -> None:
    actions = []

    class Container:
        id = "1234567890abcdef"
        status = "running"

        def reload(self):
            actions.append("reload")

        def stop(self, *, timeout: int):
            actions.append(("stop", timeout))

        def rename(self, name: str):
            actions.append(("rename", name))

        def remove(self, *, force: bool):
            actions.append(("remove", force))

    backend = DockerBackend(
        SimpleNamespace(containers=SimpleNamespace(get=lambda _name: Container())),
        defer_removal=True,
    )

    backend.container_remove(name="webarena_verified_shopping")

    retired = "webarena_verified_shopping__retired__1234567890ab"
    assert actions == ["reload", ("stop", 10), ("rename", retired)]
    assert backend.retired_containers == [retired]
