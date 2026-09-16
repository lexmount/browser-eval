from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.trajectory_replay.public_webarena.rainbond_slices import (
    RECEIPT_SCHEMA,
    STATEFUL_SITES,
    RainbondSlicePool,
    provision_pool,
)

PINNED = {
    site: f"example/{site}@sha256:{index:064x}"
    for index, site in enumerate(STATEFUL_SITES, start=1)
}


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
                        "service_cname": site,
                        "image": PINNED[site],
                    }
                    for site in STATEFUL_SITES
                ]
            }
        if method == "POST" and path.endswith("/domains"):
            service_id = body["http"]["service_id"]
            if self.fail_domain_for and self.fail_domain_for in service_id:
                raise RuntimeError("injected domain failure")
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
    assert all("run-a-1" in row["site_url"] for row in receipt["components"].values())
    assert "TEST_RAINBOND_TOKEN" not in receipt_path.read_text()
    assert not any("secret-token" in json.dumps(call) for call in api.calls)
    deploy = next(call for call in api.calls if call[1].endswith("/operations"))
    assert set(deploy[2]["service_ids"]) == {
        row["service_id"] for row in receipt["components"].values()
    }
    domains = [call[2]["http"] for call in api.calls if call[1].endswith("/domains")]
    admin = next(row for row in domains if "shopping-admin" in row["domain_name"])
    reddit = next(row for row in domains if "reddit" in row["domain_name"])
    assert admin["configuration"]["proxy_buffer_size"] == 64
    assert admin["configuration"]["proxy_buffer_numbers"] == 8
    assert reddit["configuration"]["proxy_buffer_size"] == 4


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
