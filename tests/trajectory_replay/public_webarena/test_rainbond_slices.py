from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.trajectory_replay.public_webarena.rainbond_slices import (
    RECEIPT_SCHEMA,
    STATEFUL_SITES,
    RainbondSlicePool,
    provision_pool,
    scale_pool,
)

PINNED = {
    site: f"example/{site}@sha256:{index:064x}"
    for index, site in enumerate(STATEFUL_SITES, start=1)
}
INITIALIZER_IMAGE = "alpine@sha256:" + "a" * 64


class FakeAPI:
    def __init__(self, *, fail_domain_for: str | None = None) -> None:
        self.calls = []
        self.next_app_id = 200
        self.fail_domain_for = fail_domain_for

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "POST" and path.endswith("/apps"):
            app_id = self.next_app_id
            self.next_app_id += 1
            return {"ID": app_id}
        if method == "POST" and path.endswith("/192/copy"):
            return {
                "services": [
                    {
                        "service_id": f"service-{site}-{self.next_app_id}",
                        "service_alias": f"alias-{site}-{self.next_app_id}",
                        "service_cname": f"{site}-copy",
                        "image": PINNED[site],
                    }
                    for site in STATEFUL_SITES
                ]
                + [
                    {
                        "service_id": f"service-initializer-{self.next_app_id}",
                        "service_alias": f"alias-initializer-{self.next_app_id}",
                        "service_cname": "slice-initializer-copy",
                        "image": INITIALIZER_IMAGE,
                    }
                ]
            }
        if method == "POST" and path.endswith("/domains"):
            service_id = body["http"]["service_id"]
            if self.fail_domain_for and self.fail_domain_for in service_id:
                raise RuntimeError("injected domain failure")
        if method == "GET" and path.endswith("/ports"):
            service_id = path.split("/services/", 1)[1].split("/", 1)[0]
            return {
                "ports": [
                    {
                        "container_port": 8877,
                        "is_inner_service": True,
                        "k8s_service_name": f"internal-{service_id}",
                    }
                ]
            }
        if method == "GET" and path.endswith("/services"):
            return [
                {
                    "service_id": f"service-initializer-{self.next_app_id}",
                    "status": "running",
                }
            ]
        return {}


def _pool_config(tmp_path: Path) -> Path:
    lock = tmp_path / "images.json"
    lock.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_environment_images/1",
                "images": {site: {"pinned": image} for site, image in PINNED.items()},
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "pool.json"
    config.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_rainbond_pool/1",
                "api_base_url": "https://rainbond.example",
                "team_id": "team-id",
                "team_name": "efficiency",
                "region_name": "rainbond",
                "source_app_id": 192,
                "source_services": {site: f"source-{site}" for site in STATEFUL_SITES},
                "initializer_service": "source-initializer",
                "initializer_image": INITIALIZER_IMAGE,
                "app_prefix": "webarena-slice",
                "domain_suffix": "dev.example.test",
                "token_env": "TEST_RAINBOND_TOKEN",
                "image_lock": "images.json",
                "readiness_timeout_seconds": 1,
            }
        ),
        encoding="utf-8",
    )
    return config


def _pool(tmp_path: Path, api: FakeAPI) -> RainbondSlicePool:
    pool = RainbondSlicePool(_pool_config(tmp_path), api=api, sleep=lambda _: None)
    pool._wait_site = lambda _url: None
    return pool


def test_provision_creates_one_complete_isolated_app_without_serializing_token(tmp_path):
    api = FakeAPI()
    pool = _pool(tmp_path, api)
    receipt_path = tmp_path / "slice-1" / "slice-receipt.json"

    receipt = pool.provision("run-a", 1, receipt_path)

    assert receipt["schema"] == RECEIPT_SCHEMA
    assert set(receipt["components"]) == set(STATEFUL_SITES)
    assert len({row["site_url"] for row in receipt["components"].values()}) == 4
    assert receipt["initializer"]["service_id"] == "service-initializer-201"
    assert all("run-a-1" in row["site_url"] for row in receipt["components"].values())
    assert "TEST_RAINBOND_TOKEN" not in receipt_path.read_text()
    assert not any("secret-token" in json.dumps(call) for call in api.calls)
    deploy = next(call for call in api.calls if call[1].endswith("/operations"))
    assert set(deploy[2]["service_ids"]) == {
        row["service_id"] for row in receipt["components"].values()
    } | {receipt["initializer"]["service_id"]}
    domains = [call[2]["http"] for call in api.calls if call[1].endswith("/domains")]
    assert len(domains) == 5
    admin = next(row for row in domains if "shopping-admin" in row["domain_name"])
    reddit = next(row for row in domains if "reddit" in row["domain_name"])
    assert admin["configuration"]["proxy_buffer_size"] == 64
    assert admin["configuration"]["proxy_buffer_numbers"] == 8
    assert reddit["configuration"]["proxy_buffer_size"] == 4
    initializer_domain = next(
        row for row in domains if row["service_id"] == "service-initializer-201"
    )
    assert initializer_domain["container_port"] == 8080
    assert receipt["initializer"]["readiness_url"].endswith(
        initializer_domain["domain_name"]
    )
    initializer_envs = next(
        call[2]["envs"]
        for call in api.calls
        if call[0] == "PUT" and "service-initializer" in call[1]
    )
    assert {row["name"]: row["value"] for row in initializer_envs} == {
        "REDDIT_CTRL_URL": "http://internal-service-reddit-201:8877",
        "GITLAB_CTRL_URL": "http://internal-service-gitlab-201:8877",
    }


def test_provision_rejects_copied_site_without_internal_control_port(tmp_path):
    class MissingControlPortAPI(FakeAPI):
        def request(self, method, path, body=None):
            if method == "GET" and path.endswith("/ports"):
                return {"ports": []}
            return super().request(method, path, body)

    api = MissingControlPortAPI()
    pool = _pool(tmp_path, api)

    with pytest.raises(RuntimeError, match="no internal service on port 8877"):
        pool.provision("run-a", 1, tmp_path / "slice-receipt.json")

    assert any(method == "DELETE" for method, _path, _body in api.calls)


def test_reset_deletes_the_whole_app_and_recreates_a_new_generation(tmp_path):
    api = FakeAPI()
    pool = _pool(tmp_path, api)
    receipt_path = tmp_path / "slice-receipt.json"
    first = pool.provision("run-b", 2, receipt_path)

    second = pool.reset(receipt_path)

    assert second["app_id"] != first["app_id"]
    assert second["generation"] != first["generation"]
    assert {row["site_url"] for row in second["components"].values()}.isdisjoint(
        {row["site_url"] for row in first["components"].values()}
    )
    assert all(
        f"-{second['app_id']}-" in row["site_url"]
        for row in second["components"].values()
    )
    assert any(
        method == "DELETE" and f"/{first['app_id']}?force=1" in path
        for method, path, _body in api.calls
    )


def test_partial_provision_failure_removes_the_new_app_and_leaves_no_receipt(tmp_path):
    api = FakeAPI(fail_domain_for="reddit")
    pool = _pool(tmp_path, api)
    receipt_path = tmp_path / "slice-receipt.json"

    with pytest.raises(RuntimeError, match="injected"):
        pool.provision("run-c", 1, receipt_path)

    assert not receipt_path.exists()
    assert any(method == "DELETE" and "/200?force=1" in path for method, path, _ in api.calls)


def test_provision_pool_rolls_back_already_created_slots(tmp_path, monkeypatch):
    destroyed = []

    class Pool:
        def __init__(self, _config):
            pass

        def provision(self, _pool_id, slot, receipt_path):
            if slot == 2:
                raise RuntimeError("second slot failed")
            receipt_path.parent.mkdir(parents=True)
            receipt_path.write_text("{}")
            return {"slot": slot}

        def runtime_config(self, _receipt):
            return {"ok": True}

        def destroy(self, receipt_path):
            destroyed.append(receipt_path)

    monkeypatch.setattr(
        "harness.trajectory_replay.public_webarena.rainbond_slices.RainbondSlicePool", Pool
    )

    with pytest.raises(RuntimeError, match="second slot"):
        provision_pool(tmp_path / "pool.json", tmp_path / "out", "run-d", 2)

    assert destroyed == [tmp_path / "out" / "slice-1" / "slice-receipt.json"]


def test_scale_pool_keeps_one_warm_slice_and_removes_only_excess(tmp_path, monkeypatch):
    created = []
    destroyed = []

    class Config:
        max_parallel_provisions = 2

    class Pool:
        config = Config()

        def __init__(self, _config):
            pass

        def provision(self, pool_id, slot, receipt_path):
            created.append(slot)
            receipt = {
                "schema": RECEIPT_SCHEMA,
                "pool_id": pool_id,
                "slot": slot,
                "app_id": 100 + slot,
                "components": {site: {} for site in STATEFUL_SITES},
            }
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_text(json.dumps(receipt))
            return receipt

        def runtime_config(self, _receipt):
            return {"ok": True}

        def validate_receipt(self, receipt_path):
            return json.loads(receipt_path.read_text())

        def destroy(self, receipt_path):
            destroyed.append(receipt_path)
            receipt_path.unlink()

    monkeypatch.setattr(
        "harness.trajectory_replay.public_webarena.rainbond_slices.RainbondSlicePool", Pool
    )
    output = tmp_path / "pool"

    assert len(scale_pool(tmp_path / "config.json", output, "warm", 3)) == 3
    assert len(scale_pool(tmp_path / "config.json", output, "warm", 1)) == 1

    assert sorted(created) == [1, 2, 3]
    assert [path.parent.name for path in destroyed] == ["slice-3", "slice-2"]
    with pytest.raises(ValueError, match="minimum_ready"):
        scale_pool(tmp_path / "config.json", output, "warm", 0, minimum_ready=1)
