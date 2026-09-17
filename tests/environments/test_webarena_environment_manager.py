from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.environments.webarena.config import RuntimeConfig, SiteConfig
from harness.environments.webarena.manager import EnvironmentManager


class MissingContainers:
    def get(self, _name: str):
        import docker

        raise docker.errors.NotFound("missing")


def _config() -> RuntimeConfig:
    return RuntimeConfig(
        path=Path(__file__),
        hostname="127.0.0.1",
        sites={
            "map": SiteConfig(
                name="map",
                image="example/map@sha256:" + "a" * 64,
                architecture="amd64",
                port=23000,
                control_port=23001,
            ),
            "wikipedia": SiteConfig(
                name="wikipedia",
                image="example/wiki@sha256:" + "b" * 64,
                architecture="arm64",
                port=18888,
                control_port=18889,
            ),
        },
    )


@pytest.mark.parametrize(("site", "internal_port"), [("shopping_admin", 80), ("gitlab", 8023)])
def test_manager_overrides_host_ports_without_changing_internal_ports(site, internal_port):
    config = _config()
    config.sites[site] = SiteConfig(
        name=site,
        image="example/site@sha256:" + "a" * 64,
        architecture="amd64",
        port=17780,
        control_port=17781,
    )
    manager = EnvironmentManager(config, SimpleNamespace(info=lambda: {"Architecture": "x86_64"}))
    upstream = manager._manager(config.sites[site])
    assert upstream.config.docker_img == config.sites[site].image
    assert upstream.config.host_port == 17780
    assert upstream.config.host_env_ctrl_port == 17781
    assert upstream.config.container_port == internal_port


def test_verify_accepts_intel_x86_64_for_amd64_site() -> None:
    client = SimpleNamespace(
        info=lambda: {"Architecture": "x86_64"}, containers=MissingContainers()
    )
    manager = EnvironmentManager(_config(), client)

    receipt = manager.verify("map")

    assert receipt["daemon_architecture"] == "amd64"
    assert receipt["status"] == "not_created"


def test_verify_rejects_arm_image_on_x86_without_emulation() -> None:
    client = SimpleNamespace(
        info=lambda: {"Architecture": "x86_64"}, containers=MissingContainers()
    )
    manager = EnvironmentManager(_config(), client)

    with pytest.raises(RuntimeError, match="requires native arm64"):
        manager.verify("wikipedia")


def test_wait_control_retries_transient_invalid_json(monkeypatch) -> None:
    class Response(BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    responses = iter((Response(b"not-json"), Response(b'{"success": true}')))
    monkeypatch.setattr(
        "harness.environments.webarena.manager.urllib.request.urlopen",
        lambda *_args, **_kwargs: next(responses),
    )
    monkeypatch.setattr("harness.environments.webarena.manager.time.sleep", lambda _seconds: None)

    EnvironmentManager._wait_control("http://127.0.0.1:23001", timeout=1)


def test_verify_uses_upstream_manager_container_name(monkeypatch) -> None:
    requested = []

    class Containers(MissingContainers):
        def get(self, name: str):
            requested.append(name)
            return super().get(name)

    client = SimpleNamespace(info=lambda: {"Architecture": "x86_64"}, containers=Containers())
    manager = EnvironmentManager(_config(), client)
    monkeypatch.setattr(
        manager, "_manager", lambda _site: SimpleNamespace(container_name="upstream-map")
    )

    receipt = manager.verify("map")

    assert requested == ["upstream-map"]
    assert receipt["container_name"] == "upstream-map"


@pytest.mark.parametrize(
    ("site_name", "expected_steps"),
    [
        ("gitlab", ["control", "entrypoint", "explicit", "control"]),
        ("reddit", ["control", "entrypoint", "explicit", "control"]),
        ("shopping", ["control", "entrypoint", "verified"]),
        ("shopping_admin", ["control", "entrypoint", "verified"]),
    ],
)
def test_start_runs_only_the_declared_site_initialization(
    monkeypatch, site_name: str, expected_steps: list[str]
) -> None:
    config = _config()
    config.sites[site_name] = SiteConfig(
        name=site_name,
        image="example/site@sha256:" + "c" * 64,
        architecture="amd64",
        port=23000,
        control_port=23001,
    )
    container = SimpleNamespace()
    client = SimpleNamespace(
        info=lambda: {"Architecture": "x86_64"},
        containers=SimpleNamespace(get=lambda _name: container),
    )
    manager = EnvironmentManager(config, client)
    steps = []
    upstream = SimpleNamespace(
        container_name=f"webarena_verified_{site_name}",
        start=lambda **_kwargs: SimpleNamespace(
            url="http://127.0.0.1:23000",
            env_ctrl_url="http://127.0.0.1:23001",
        ),
    )
    monkeypatch.setattr(manager, "_manager", lambda _site: upstream)
    monkeypatch.setattr(manager, "verify", lambda *_args, **_kwargs: {"status": "running"})
    monkeypatch.setattr(manager, "_wait_control", lambda *_args, **_kwargs: steps.append("control"))
    monkeypatch.setattr(
        manager,
        "_wait_entrypoint_init",
        lambda *_args, **_kwargs: steps.append("entrypoint"),
    )
    monkeypatch.setattr(
        manager, "_explicit_init", lambda *_args, **_kwargs: steps.append("explicit")
    )
    monkeypatch.setattr(
        manager,
        "_verify_entrypoint_init",
        lambda *_args, **_kwargs: steps.append("verified"),
    )

    receipt = manager.start(site_name, reset=True, timeout=1)

    assert steps == expected_steps
    assert receipt["command"] == "reset"
    assert receipt["status"] == "running"


def test_stop_delegates_to_pinned_upstream_manager(monkeypatch) -> None:
    client = SimpleNamespace(
        info=lambda: {"Architecture": "x86_64"}, containers=MissingContainers()
    )
    manager = EnvironmentManager(_config(), client)
    calls = []
    monkeypatch.setattr(
        manager,
        "_manager",
        lambda _site: SimpleNamespace(
            container_name="upstream-map", stop=lambda: calls.append("stop")
        ),
    )

    receipt = manager.stop("map")

    assert calls == ["stop"]
    assert receipt == {"command": "stop", "site": "map", "container_name": "upstream-map"}
