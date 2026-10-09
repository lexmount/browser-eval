from __future__ import annotations

import pytest

from harness.trajectory_replay.public_webarena.diagnostics import site_urls


def test_runtime_smoke_selects_active_urls_without_credentials():
    config = {
        "environments": {
            "__SHOPPING__": {
                "urls": ["http://old.example", "http://current.example"],
                "active_url_idx": 1,
                "credentials_env": {
                    "username": "WEBARENA_SHOPPING_USERNAME",
                    "password": "WEBARENA_SHOPPING_PASSWORD",
                },
            },
            "__REDDIT__": {"urls": ["http://reddit.example"]},
        }
    }

    assert site_urls(config, ("shopping", "reddit")) == {
        "shopping": "http://current.example",
        "reddit": "http://reddit.example",
    }


def test_runtime_smoke_rejects_persisted_or_missing_configuration():
    config = {
        "environments": {
            "__SHOPPING__": {
                "urls": ["http://shopping.example"],
                "credentials": {"username": "saved", "password": "saved"},
            }
        }
    }

    with pytest.raises(ValueError, match="must not persist credentials"):
        site_urls(config, ("shopping",))
    with pytest.raises(ValueError, match="missing __REDDIT__"):
        site_urls(config, ("reddit",))


def test_runtime_smoke_validates_cpu_before_environment_access(tmp_path, monkeypatch):
    import json

    import docker

    from harness.trajectory_replay.public_webarena.diagnostics import runtime_smoke_main

    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"environments": {"__WIKIPEDIA__": {"urls": ["https://wikipedia.example"]}}})
    )
    monkeypatch.setattr(
        docker,
        "from_env",
        lambda: (_ for _ in ()).throw(AssertionError("invalid CPU must fail before Docker access")),
    )
    for cpu in ("nan", "inf", "-1", "0"):
        with pytest.raises(ValueError, match="finite and positive"):
            runtime_smoke_main(
                [
                    "--config",
                    str(config),
                    "--output",
                    str(tmp_path / "output"),
                    "--site",
                    "wikipedia",
                    "--cpus",
                    cpu,
                ]
            )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("failure", ["none", "connect", "start"])
def test_runtime_smoke_uses_registered_engine_and_sites_and_cleans_resources(
    tmp_path, monkeypatch, failure
):
    import hashlib
    import json
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    import docker
    import playwright.sync_api

    from harness.environments import browser as owner
    from harness.environments.webarena import har
    from harness.trajectory_replay.public_webarena.diagnostics import runtime_smoke_main

    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"environments": {"__WIKIPEDIA__": {"urls": ["https://wikipedia.example"]}}})
    )
    output = tmp_path / "output"
    client, container, network, browser, manager = [MagicMock() for _ in range(5)]
    client.networks.create.return_value = network
    network.name = "synthetic-network"
    container.image.id = "synthetic-image"
    page = MagicMock()
    page.goto.return_value.status = 200
    page.url = "https://wikipedia.example"
    page.title.return_value = "Synthetic page"
    browser.contexts = [SimpleNamespace(pages=[page])]
    manager.chromium.connect_over_cdp.return_value = browser
    if failure == "connect":
        manager.chromium.connect_over_cdp.side_effect = RuntimeError("synthetic failure")
    monkeypatch.setattr(docker, "from_env", lambda: client)

    def start():
        if failure == "start":
            raise RuntimeError("synthetic failure")
        return manager

    monkeypatch.setattr(
        playwright.sync_api, "sync_playwright", lambda: SimpleNamespace(start=start)
    )
    launches = []
    monkeypatch.setattr(
        owner,
        "start_browser_container",
        lambda *args, **kwargs: launches.append(kwargs) or container,
    )
    monkeypatch.setattr(owner, "configured_browser_images", lambda: {"chromium": "custom-image"})
    monkeypatch.setattr(owner, "published_endpoint", lambda *args, **kwargs: "http://synthetic")
    monkeypatch.setattr(owner, "wait_cdp", lambda *args, **kwargs: "ws://synthetic")

    class Recorder:
        def __init__(self, page):
            pass

        def stop(self, path):
            path.write_text('{"log":{"entries":[]}}')
            return {"entries": 0}

    monkeypatch.setattr(har, "CdpHarRecorder", Recorder)
    argv = [
        "--config",
        str(config),
        "--output",
        str(output),
        "--site",
        "wikipedia",
        "--engine",
        "chromium",
    ]
    if failure != "none":
        with pytest.raises(RuntimeError, match="synthetic failure"):
            runtime_smoke_main(argv)
        assert not (output / "receipt.json").exists()
        network.remove.assert_called_once()
        if failure == "connect":
            container.remove.assert_called_once_with(force=True)
            manager.stop.assert_called_once()
        return
    runtime_smoke_main(argv)
    receipt = json.loads((output / "receipt.json").read_text())
    assert set(receipt["engines"]) == {"chromium"}
    assert receipt["diagnostic_only"] and not receipt["qualification_evidence"]
    assert receipt["config_sha256"] == hashlib.sha256(config.read_bytes()).hexdigest()
    assert launches[0]["image"] == "custom-image"
    browser.close.assert_called_once()
    container.remove.assert_called_once_with(force=True)
    network.remove.assert_called_once()
    manager.stop.assert_called_once()
