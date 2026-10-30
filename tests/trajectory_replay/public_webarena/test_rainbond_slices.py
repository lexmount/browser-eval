from __future__ import annotations

import json
import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from harness.environments.webarena.rainbond import (
    LEASE_SCHEMA,
    RECEIPT_SCHEMA,
    STATEFUL_SITES,
    RainbondSlicePool,
    acquire_slice,
    provision_pool,
    reconcile_pool_capacity,
    release_slice,
    scale_pool,
)

PINNED = {
    site: f"example/{site}@sha256:{index:064x}"
    for index, site in enumerate(STATEFUL_SITES, start=1)
}
INITIALIZER_IMAGE = "alpine@sha256:" + "a" * 64


def test_slow_release_does_not_block_acquisition_of_another_slot(tmp_path, monkeypatch):
    output = tmp_path / "runtime"
    _ready_slot(output, 1, 101)
    _ready_slot(output, 2, 102)
    lease = acquire_slice(output, "task-a")
    resetting = threading.Event()
    finish_reset = threading.Event()

    class Pool:
        def __init__(self, _config):
            pass

        def reset(self, receipt_path):
            resetting.set()
            assert finish_reset.wait(5)
            receipt = json.loads(receipt_path.read_text())
            receipt["app_id"] = 201
            receipt_path.write_text(json.dumps(receipt))
            return receipt

        def runtime_config(self, receipt):
            return {"app_id": receipt["app_id"]}

    monkeypatch.setattr("harness.environments.webarena.rainbond.RainbondSlicePool", Pool)
    with ThreadPoolExecutor(max_workers=2) as executor:
        releasing = executor.submit(
            release_slice, tmp_path / "pool.json", output, lease["lease_id"]
        )
        try:
            assert resetting.wait(2)
            acquiring = executor.submit(acquire_slice, output, "task-b")
            assert acquiring.result(timeout=2)["app_id"] == 102
        finally:
            finish_reset.set()
        assert releasing.result(timeout=2)["ready_app_id"] == 201


class FakeAPI:
    def __init__(self, *, fail_domain_for: str | None = None) -> None:
        self.calls = []
        self.next_app_id = 200
        self.fail_domain_for = fail_domain_for
        self.port_hosts = {}
        self.stopped_apps = set()

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "POST" and path.endswith("/operations") and body["action"] == "stop":
            self.stopped_apps.add(path.removesuffix("/operations"))
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
            site = next(site for site in STATEFUL_SITES if site in service_id)
            site_host = f"internal-{service_id}"
            return {
                "ports": [
                    {
                        "container_port": 8023 if site == "gitlab" else 80,
                        "is_inner_service": True,
                        "k8s_service_name": site_host,
                    },
                    {
                        "container_port": 8877,
                        "is_inner_service": True,
                        "k8s_service_name": self.port_hosts.get(
                            service_id, f"control-{service_id}"
                        ),
                    },
                ]
            }
        if method == "PUT" and "/ports/8877" in path:
            service_id = path.split("/services/", 1)[1].split("/", 1)[0]
            self.port_hosts.pop(service_id, None)
            return {}
        if method == "DELETE" and "/ports/8877" in path:
            return {}
        if method == "POST" and path.endswith("/ports"):
            service_id = path.split("/services/", 1)[1].split("/", 1)[0]
            self.port_hosts[service_id] = f"internal-{service_id}"
            return {}
        if method == "GET" and path.endswith("/services"):
            return [
                {
                    "service_id": f"service-initializer-{self.next_app_id}",
                    "status": (
                        "closed"
                        if path.removesuffix("/services") in self.stopped_apps
                        else "running"
                    ),
                }
            ]
        return {}


def _pool_config(tmp_path: Path, *, reset_strategy: str | None = None) -> Path:
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
    value = {
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
    if reset_strategy is not None:
        value["reset_strategy"] = reset_strategy
    config.write_text(
        json.dumps(value),
        encoding="utf-8",
    )
    return config


def _pool(tmp_path: Path, api: FakeAPI) -> RainbondSlicePool:
    pool = RainbondSlicePool(_pool_config(tmp_path), api=api, sleep=lambda _: None)
    pool._wait_site = lambda _url: None
    return pool


def test_new_slice_runtime_config_contains_four_unique_sites_and_two_shared_sites(tmp_path):
    config_path = _pool_config(tmp_path)
    config = json.loads(config_path.read_text())
    shared = {
        "__MAP__": {"urls": ["http://shared-map.test"]},
        "__WIKIPEDIA__": {"urls": ["http://shared-wikipedia.test"]},
    }
    config["shared_environments"] = shared
    config_path.write_text(json.dumps(config))
    pool = RainbondSlicePool(config_path, api=FakeAPI())
    receipt = {
        "components": {
            site: {"site_url": f"http://slice-2-{site}.test"}
            for site in STATEFUL_SITES
        }
    }

    environments = pool.runtime_config(receipt)["environments"]

    assert set(environments) == {f"__{site.upper()}__" for site in STATEFUL_SITES} | set(shared)
    assert environments["__MAP__"] == shared["__MAP__"]
    assert environments["__WIKIPEDIA__"] == shared["__WIKIPEDIA__"]
    assert environments["__GITLAB__"]["urls"] == ["http://slice-2-gitlab.test"]


def test_app_deletion_waits_for_service_stop(tmp_path):
    api = FakeAPI()
    pool = _pool(tmp_path, api)
    pool._delete_app(200)
    methods = [method for method, _, _ in api.calls]
    assert methods == ["GET", "POST", "GET", "DELETE"]
    assert api.calls[1][2]["action"] == "stop"


def test_app_deletion_is_idempotent_after_control_plane_record_is_gone(tmp_path):
    class MissingAppAPI(FakeAPI):
        def request(self, method, path, body=None):
            error = urllib.error.HTTPError(path, 404, "Not Found", None, None)
            raise RuntimeError("Rainbond OpenAPI returned HTTP 404") from error

    pool = _pool(tmp_path, MissingAppAPI())
    pool._delete_app(200)


def test_app_deletion_keeps_record_when_stop_fails(tmp_path):
    class StopFailureAPI(FakeAPI):
        def request(self, method, path, body=None):
            if method == "POST" and path.endswith("/operations"):
                raise RuntimeError("stop failed")
            return super().request(method, path, body)

    api = StopFailureAPI()
    pool = _pool(tmp_path, api)
    with pytest.raises(RuntimeError, match="stop failed"):
        pool._delete_app(200)
    assert not any(method == "DELETE" for method, _, _ in api.calls)


def test_app_deletion_does_not_trust_a_successful_stop_ack(tmp_path, monkeypatch):
    class StillRunningAPI(FakeAPI):
        def request(self, method, path, body=None):
            result = super().request(method, path, body)
            if method == "GET" and path.endswith("/services"):
                result[0]["status"] = "running"
            return result

    api = StillRunningAPI()
    pool = _pool(tmp_path, api)
    ticks = iter([0, 100_000])
    monkeypatch.setattr(
        "harness.environments.webarena.rainbond.time.monotonic",
        lambda: next(ticks),
    )
    with pytest.raises(TimeoutError, match="did not stop before deletion"):
        pool._delete_app(200)
    assert not any(method == "DELETE" for method, _, _ in api.calls)


@pytest.mark.parametrize("stale_reads", [2, 4])
def test_control_port_convergence_is_bounded_and_does_not_repeat_writes(tmp_path, stale_reads):
    class EventuallyConsistentAPI(FakeAPI):
        def request(self, method, path, body=None):
            response = super().request(method, path, body)
            if method == "GET" and path.endswith("/ports") and self.port_hosts:
                if self.stale_reads:
                    self.stale_reads -= 1
                    response["ports"][1]["k8s_service_name"] = "stale-control"
            return response

    api = EventuallyConsistentAPI()
    api.stale_reads = stale_reads
    pool = _pool(tmp_path, api)
    waits = []
    pool.sleep = waits.append

    def configure():
        pool._coalesce_internal_ports(
            200,
            "service-shopping",
            site_port=80,
            control_port=8877,
            control_alias="SHOPPING_CTRL",
        )

    if stale_reads == 2:
        configure()
    else:
        with pytest.raises(RuntimeError, match="did not attach control port"):
            configure()
    assert waits == [1, 2]
    assert sum(method == "POST" for method, _, _ in api.calls) == 1
    assert sum(method == "DELETE" for method, _, _ in api.calls) == 1
    assert sum(method == "GET" for method, _, _ in api.calls) == 4


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
    assert receipt["initializer"]["readiness_url"].endswith(initializer_domain["domain_name"])
    initializer_envs = next(
        call[2]["envs"]
        for call in api.calls
        if call[0] == "PUT" and "service-initializer" in call[1]
    )
    assert {row["name"]: row["value"] for row in initializer_envs} == {
        "REDDIT_CTRL_URL": "http://internal-service-reddit-201:8877",
        "GITLAB_CTRL_URL": "http://internal-service-gitlab-201:8877",
        "ENV_CTRL_MAX_ATTEMPTS": "360",
        "ENV_CTRL_RETRY_SECONDS": "5",
    }
    port_mutations = [call for call in api.calls if "/ports" in call[1] and call[0] != "GET"]
    assert [call[0] for call in port_mutations] == ["PUT", "DELETE", "POST"] * 4
    recreated = [call for call in port_mutations if call[0] == "POST"]
    assert {call[2]["port_alias"] for call in recreated} == {
        "SHOPPING_ADMIN_CTRL",
        "SHOPPING_CTRL",
        "REDDIT_CTRL",
        "GITLAB_CTRL",
    }


def test_provision_accepts_console_items_response_shapes(tmp_path):
    class ItemsAPI(FakeAPI):
        def request(self, method, path, body=None):
            response = super().request(method, path, body)
            if method == "POST" and path.endswith("/192/copy"):
                return {"items": response["services"]}
            if method == "GET" and path.endswith("/ports"):
                return {"ports": {"items": response["ports"]}}
            if method == "GET" and path.endswith("/services"):
                return {"items": response}
            return response

    receipt = _pool(tmp_path, ItemsAPI()).provision("run-items", 1, tmp_path / "slice-receipt.json")

    assert set(receipt["components"]) == set(STATEFUL_SITES)


def test_provision_rejects_copied_site_without_internal_control_port(tmp_path):
    class MissingControlPortAPI(FakeAPI):
        def request(self, method, path, body=None):
            if method == "GET" and path.endswith("/ports"):
                return {"ports": []}
            return super().request(method, path, body)

    api = MissingControlPortAPI()
    pool = _pool(tmp_path, api)

    with pytest.raises(RuntimeError, match="must expose internal ports 80 and 8877"):
        pool.provision("run-a", 1, tmp_path / "slice-receipt.json")

    assert any(method == "DELETE" for method, _path, _body in api.calls)


def test_provision_rejects_copied_site_without_image_identity(tmp_path):
    class MissingImageAPI(FakeAPI):
        def request(self, method, path, body=None):
            response = super().request(method, path, body)
            if method == "POST" and path.endswith("/192/copy"):
                response["services"][0].pop("image")
            return response

    api = MissingImageAPI()
    pool = _pool(tmp_path, api)

    with pytest.raises(RuntimeError, match="has no image in response"):
        pool.provision("run-image", 1, tmp_path / "slice-receipt.json")

    assert any(method == "DELETE" for method, _path, _body in api.calls)


def test_provision_rejects_initializer_without_image_identity(tmp_path):
    class MissingInitializerImageAPI(FakeAPI):
        def request(self, method, path, body=None):
            response = super().request(method, path, body)
            if method == "POST" and path.endswith("/192/copy"):
                response["services"][-1].pop("image")
            return response

    api = MissingInitializerImageAPI()
    pool = _pool(tmp_path, api)

    with pytest.raises(RuntimeError, match="initializer component has no image"):
        pool.provision("run-initializer-image", 1, tmp_path / "slice-receipt.json")

    assert any(method == "DELETE" for method, _path, _body in api.calls)


def test_provision_cleanup_does_not_hide_original_failure(tmp_path):
    class CleanupFailureAPI(FakeAPI):
        def request(self, method, path, body=None):
            if method == "DELETE" and path.endswith("?force=1"):
                raise RuntimeError("cleanup delete failed")
            return super().request(method, path, body)

    api = CleanupFailureAPI(fail_domain_for="reddit")
    pool = _pool(tmp_path, api)

    with pytest.raises(RuntimeError, match="injected domain failure") as caught:
        pool.provision("run-cleanup", 1, tmp_path / "slice-receipt.json")

    assert any("cleanup delete failed" in note for note in caught.value.__notes__)


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
    assert all(f"-{second['app_id']}-" in row["site_url"] for row in second["components"].values())
    assert any(
        method == "DELETE" and f"/{first['app_id']}?force=1" in path
        for method, path, _body in api.calls
    )
    create_replacement = next(
        index
        for index, (method, path, _body) in enumerate(api.calls)
        if method == "POST" and path.endswith("/apps") and index > 0
    )
    delete_old = next(
        index
        for index, (method, path, _body) in enumerate(api.calls)
        if method == "DELETE" and f"/{first['app_id']}?force=1" in path
    )
    assert create_replacement < delete_old
    assert not receipt_path.with_name("slice-receipt.replacement.json").exists()


def test_capacity_safe_reset_stops_old_app_before_creating_replacement(tmp_path):
    api = FakeAPI()
    pool = RainbondSlicePool(
        _pool_config(tmp_path, reset_strategy="stop_before_replace"),
        api=api,
        sleep=lambda _: None,
    )
    pool._wait_site = lambda _url: None
    pool._wait_app_stopped = lambda _receipt: None
    receipt_path = tmp_path / "slice-receipt.json"
    first = pool.provision("run-capacity", 1, receipt_path)

    second = pool.reset(receipt_path)

    stop_index = next(
        index
        for index, (method, path, body) in enumerate(api.calls)
        if method == "POST"
        and path.endswith(f"/{first['app_id']}/operations")
        and body["action"] == "stop"
    )
    replacement_index = next(
        index
        for index, (method, path, _body) in enumerate(api.calls)
        if method == "POST" and path.endswith("/apps") and index > stop_index
    )
    delete_index = next(
        index
        for index, (method, path, _body) in enumerate(api.calls)
        if method == "DELETE" and path.endswith(f"/{first['app_id']}?force=1")
    )
    assert stop_index < replacement_index < delete_index
    assert second["app_id"] != first["app_id"]


def test_stop_before_replace_keeps_old_app_stopped_and_receipt_on_failure(tmp_path):
    api = FakeAPI()
    pool = RainbondSlicePool(
        _pool_config(tmp_path, reset_strategy="stop_before_replace"),
        api=api,
        sleep=lambda _: None,
    )
    pool._wait_site = lambda _url: None
    pool._wait_app_stopped = lambda _receipt: None
    receipt_path = tmp_path / "slice-receipt.json"
    first = pool.provision("run-recovery", 1, receipt_path)
    api.fail_domain_for = "reddit"

    with pytest.raises(RuntimeError, match="injected domain failure"):
        pool.reset(receipt_path)

    assert json.loads(receipt_path.read_text())["generation"] == first["generation"]
    operations = [
        body["action"]
        for method, path, body in api.calls
        if method == "POST" and path.endswith(f"/{first['app_id']}/operations")
    ]
    assert operations[-1:] == ["stop"]
    assert not receipt_path.with_name("slice-receipt.replacement.json").exists()


def test_default_reset_stops_dirty_app_and_keeps_receipt_on_failure(tmp_path):
    api = FakeAPI()
    pool = _pool(tmp_path, api)
    pool._wait_app_stopped = lambda _receipt: None
    receipt_path = tmp_path / "slice-receipt.json"
    first = pool.provision("run-quarantine", 1, receipt_path)
    api.fail_domain_for = "reddit"

    with pytest.raises(RuntimeError, match="injected domain failure"):
        pool.reset(receipt_path)

    assert json.loads(receipt_path.read_text())["generation"] == first["generation"]
    operations = [
        body["action"]
        for method, path, body in api.calls
        if method == "POST" and path.endswith(f"/{first['app_id']}/operations")
    ]
    assert operations[-1:] == ["stop"]
    assert not receipt_path.with_name("slice-receipt.replacement.json").exists()


def test_reset_cleanup_does_not_hide_original_delete_failure(tmp_path):
    class DeleteFailureAPI(FakeAPI):
        def request(self, method, path, body=None):
            if method == "DELETE" and path.endswith("/200?force=1"):
                raise RuntimeError("old app delete failed")
            if method == "DELETE" and path.endswith("/201?force=1"):
                raise RuntimeError("replacement cleanup failed")
            return super().request(method, path, body)

    api = DeleteFailureAPI()
    pool = _pool(tmp_path, api)
    pool._wait_app_stopped = lambda _receipt: None
    receipt_path = tmp_path / "slice-receipt.json"
    first = pool.provision("run-delete", 1, receipt_path)

    with pytest.raises(RuntimeError, match="old app delete failed") as caught:
        pool.reset(receipt_path)

    assert json.loads(receipt_path.read_text())["generation"] == first["generation"]
    assert receipt_path.with_name("slice-receipt.replacement.json").exists()
    assert any("replacement cleanup failed" in note for note in caught.value.__notes__)


def test_pool_config_rejects_unknown_reset_strategy(tmp_path):
    with pytest.raises(ValueError, match="reset_strategy"):
        RainbondSlicePool(
            _pool_config(tmp_path, reset_strategy="unsafe"),
            api=FakeAPI(),
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

    monkeypatch.setattr("harness.environments.webarena.rainbond.RainbondSlicePool", Pool)

    with pytest.raises(RuntimeError, match="second slot"):
        provision_pool(tmp_path / "pool.json", tmp_path / "out", "run-d", 2)

    assert destroyed == [tmp_path / "out" / "slice-1" / "slice-receipt.json"]


def test_provision_pool_preserves_failure_when_rollback_cleanup_fails(tmp_path, monkeypatch):
    class Config:
        max_parallel_provisions = 2

    class Pool:
        config = Config()

        def __init__(self, _config):
            pass

        def provision(self, _pool_id, slot, receipt_path):
            if slot == 2:
                raise RuntimeError("provision failed")
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_text("{}")
            return {"slot": slot}

        def runtime_config(self, _receipt):
            return {"ok": True}

        def destroy(self, _receipt_path):
            raise RuntimeError("rollback failed")

    monkeypatch.setattr("harness.environments.webarena.rainbond.RainbondSlicePool", Pool)

    with pytest.raises(RuntimeError, match="provision failed") as caught:
        provision_pool(tmp_path / "pool.json", tmp_path / "out", "run-d", 2)

    assert any("rollback failed" in note for note in caught.value.__notes__)


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

    monkeypatch.setattr("harness.environments.webarena.rainbond.RainbondSlicePool", Pool)
    output = tmp_path / "pool"

    assert len(scale_pool(tmp_path / "config.json", output, "warm", 3)) == 3
    assert len(scale_pool(tmp_path / "config.json", output, "warm", 1)) == 1

    assert sorted(created) == [1, 2, 3]
    assert [path.parent.name for path in destroyed] == ["slice-3", "slice-2"]
    with pytest.raises(ValueError, match="minimum_ready"):
        scale_pool(tmp_path / "config.json", output, "warm", 0, minimum_ready=1)
    assert scale_pool(tmp_path / "config.json", output, "warm", 0, minimum_ready=0) == []
    assert [path.parent.name for path in destroyed] == ["slice-3", "slice-2", "slice-1"]


def test_scale_pool_rolls_back_every_new_slot_after_partial_failure(tmp_path, monkeypatch):
    destroyed = []

    class Config:
        max_parallel_provisions = 3

    class Pool:
        config = Config()

        def __init__(self, _config):
            pass

        def provision(self, pool_id, slot, receipt_path):
            if slot == 2:
                raise RuntimeError("slot 2 failed")
            receipt = {"pool_id": pool_id, "slot": slot}
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_text(json.dumps(receipt))
            return receipt

        def runtime_config(self, _receipt):
            return {"ok": True}

        def validate_receipt(self, receipt_path):
            return json.loads(receipt_path.read_text())

        def destroy(self, receipt_path):
            destroyed.append(receipt_path.parent.name)
            receipt_path.unlink()

    monkeypatch.setattr("harness.environments.webarena.rainbond.RainbondSlicePool", Pool)
    output = tmp_path / "runtime"

    with pytest.raises(RuntimeError, match="slot 2 failed"):
        scale_pool(tmp_path / "pool.json", output, "warm", 3)

    assert sorted(destroyed) == ["slice-1", "slice-3"]
    assert not list(output.glob("slice-*/slice-receipt.json"))


def test_capacity_keeps_idle_slice_in_addition_to_active_demand(tmp_path, monkeypatch):
    calls = []

    def fake_scale(config, output, pool_id, count, *, minimum_ready):
        calls.append((config, output, pool_id, count, minimum_ready))
        return []

    monkeypatch.setattr("harness.environments.webarena.rainbond.scale_pool", fake_scale)

    reconcile_pool_capacity(
        tmp_path / "pool.json", tmp_path / "runtime", "warm", 3, minimum_ready=1
    )

    assert calls[0][3:] == (4, 1)


@pytest.mark.parametrize("count", [0, 1])
def test_scale_down_is_atomic_when_an_excess_slice_is_leased(tmp_path, monkeypatch, count):
    destroyed = []

    class Config:
        max_parallel_provisions = 2

    class Pool:
        config = Config()

        def __init__(self, _config):
            pass

        def validate_receipt(self, receipt_path):
            return json.loads(receipt_path.read_text())

        def destroy(self, receipt_path):
            destroyed.append(receipt_path)

    monkeypatch.setattr("harness.environments.webarena.rainbond.RainbondSlicePool", Pool)
    output = tmp_path / "runtime"
    for slot in (1, 2, 3):
        slot_dir = output / f"slice-{slot}"
        slot_dir.mkdir(parents=True)
        (slot_dir / "slice-receipt.json").write_text(json.dumps({"pool_id": "warm", "slot": slot}))
    (output / "slice-2" / "slice-lease.json").write_text("{}")

    with pytest.raises(RuntimeError, match="leased Rainbond slices"):
        scale_pool(tmp_path / "pool.json", output, "warm", count, minimum_ready=0)

    assert destroyed == []


def test_scale_down_continues_after_one_destroy_failure(tmp_path, monkeypatch):
    destroyed = []

    class Config:
        max_parallel_provisions = 2

    class Pool:
        config = Config()

        def __init__(self, _config):
            pass

        def validate_receipt(self, receipt_path):
            return json.loads(receipt_path.read_text())

        def destroy(self, receipt_path):
            slot = int(receipt_path.parent.name.removeprefix("slice-"))
            destroyed.append(slot)
            if slot == 3:
                raise RuntimeError("slot 3 delete failed")
            receipt_path.unlink()

    monkeypatch.setattr("harness.environments.webarena.rainbond.RainbondSlicePool", Pool)
    output = tmp_path / "runtime"
    for slot in (1, 2, 3):
        slot_dir = output / f"slice-{slot}"
        slot_dir.mkdir(parents=True)
        (slot_dir / "slice-receipt.json").write_text(json.dumps({"pool_id": "warm", "slot": slot}))

    with pytest.raises(RuntimeError, match="slot 3 delete failed"):
        scale_pool(tmp_path / "pool.json", output, "warm", 1)

    assert destroyed == [3, 2]
    assert (output / "slice-3" / "slice-receipt.json").exists()
    assert not (output / "slice-2" / "slice-receipt.json").exists()


def _ready_slot(output: Path, slot: int, app_id: int) -> Path:
    slot_dir = output / f"slice-{slot}"
    slot_dir.mkdir(parents=True)
    (slot_dir / "slice-receipt.json").write_text(
        json.dumps(
            {
                "schema": RECEIPT_SCHEMA,
                "slot": slot,
                "app_id": app_id,
                "generation": f"generation-{slot}",
            }
        )
    )
    (slot_dir / "webarena.config.json").write_text("{}")
    return slot_dir


def test_acquire_grants_distinct_slices_and_exhausts_pool(tmp_path):
    output = tmp_path / "runtime"
    _ready_slot(output, 1, 101)
    _ready_slot(output, 2, 102)

    first = acquire_slice(output, "task-a")
    second = acquire_slice(output, "task-b")

    assert first["schema"] == LEASE_SCHEMA
    assert first["slot"] != second["slot"]
    with pytest.raises(RuntimeError, match="no unleased"):
        acquire_slice(output, "task-c")


def test_acquire_uses_numeric_slot_order(tmp_path):
    output = tmp_path / "runtime"
    _ready_slot(output, 10, 110)
    _ready_slot(output, 2, 102)

    lease = acquire_slice(output, "task-a")

    assert lease["slot"] == 2


def test_concurrent_thread_acquires_never_share_a_slice(tmp_path):
    output = tmp_path / "runtime"
    _ready_slot(output, 1, 101)
    _ready_slot(output, 2, 102)

    with ThreadPoolExecutor(max_workers=2) as executor:
        leases = list(
            executor.map(
                lambda owner: acquire_slice(output, owner),
                ("task-a", "task-b"),
            )
        )

    assert {lease["slot"] for lease in leases} == {1, 2}
    assert len({lease["lease_id"] for lease in leases}) == 2


def test_release_replaces_dirty_generation_before_unlocking_slice(tmp_path, monkeypatch):
    output = tmp_path / "runtime"
    slot_dir = _ready_slot(output, 1, 101)
    lease = acquire_slice(output, "task-a")

    class Pool:
        def __init__(self, _config):
            pass

        def reset(self, _receipt_path):
            assert (slot_dir / "slice-lease.json").exists()
            return {
                "schema": RECEIPT_SCHEMA,
                "slot": 1,
                "app_id": 201,
                "generation": "generation-new",
            }

        def runtime_config(self, receipt):
            return {"app_id": receipt["app_id"]}

    monkeypatch.setattr("harness.environments.webarena.rainbond.RainbondSlicePool", Pool)

    result = release_slice(tmp_path / "pool.json", output, lease["lease_id"])

    assert result["released_app_id"] == 101
    assert result["ready_app_id"] == 201
    assert not (slot_dir / "slice-lease.json").exists()
    assert json.loads((slot_dir / "webarena.config.json").read_text()) == {"app_id": 201}


def test_failed_release_keeps_lease_so_dirty_slice_cannot_be_reassigned(tmp_path, monkeypatch):
    output = tmp_path / "runtime"
    slot_dir = _ready_slot(output, 1, 101)
    lease = acquire_slice(output, "task-a")

    class Pool:
        def __init__(self, _config):
            pass

        def reset(self, _receipt_path):
            raise RuntimeError("replacement failed")

    monkeypatch.setattr("harness.environments.webarena.rainbond.RainbondSlicePool", Pool)

    with pytest.raises(RuntimeError, match="replacement failed"):
        release_slice(tmp_path / "pool.json", output, lease["lease_id"])

    assert (slot_dir / "slice-lease.json").exists()
    with pytest.raises(RuntimeError, match="no unleased"):
        acquire_slice(output, "task-b")


@pytest.mark.parametrize("failure_point", ["unlink", "config"])
def test_release_retry_only_unlinks_after_reset_was_persisted(tmp_path, monkeypatch, failure_point):
    output = tmp_path / "runtime"
    slot_dir = _ready_slot(output, 1, 101)
    lease = acquire_slice(output, "task-a")
    lease_path = slot_dir / "slice-lease.json"
    reset_calls = []

    class Pool:
        def __init__(self, _config):
            pass

        def reset(self, receipt_path):
            reset_calls.append(receipt_path)
            receipt = {
                "schema": RECEIPT_SCHEMA,
                "slot": 1,
                "app_id": 201,
                "generation": "generation-new",
            }
            receipt_path.write_text(json.dumps(receipt))
            return receipt

        def validate_receipt(self, receipt_path):
            return json.loads(receipt_path.read_text())

        def runtime_config(self, receipt):
            return {"app_id": receipt["app_id"]}

    monkeypatch.setattr("harness.environments.webarena.rainbond.RainbondSlicePool", Pool)
    original_unlink = Path.unlink
    original_replace = Path.replace
    failures = 0

    def fail_first_lease_unlink(path, *args, **kwargs):
        nonlocal failures
        if failure_point == "unlink" and path == lease_path and failures == 0:
            failures += 1
            raise OSError("transient unlink failure")
        return original_unlink(path, *args, **kwargs)

    def fail_first_config_replace(path, target):
        nonlocal failures
        if (
            failure_point == "config"
            and Path(target) == slot_dir / "webarena.config.json"
            and failures == 0
        ):
            failures += 1
            raise OSError("transient config failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "unlink", fail_first_lease_unlink)
    monkeypatch.setattr(Path, "replace", fail_first_config_replace)
    with pytest.raises(OSError, match="transient .* failure"):
        release_slice(tmp_path / "pool.json", output, lease["lease_id"])
    assert lease_path.exists()
    assert len(reset_calls) == 1

    result = release_slice(tmp_path / "pool.json", output, lease["lease_id"])
    assert result["released_app_id"] == 101
    assert result["ready_app_id"] == 201
    assert not lease_path.exists()
    assert len(reset_calls) == 1


def test_release_does_not_mistake_task_preparation_for_cleanup(tmp_path, monkeypatch):
    output = tmp_path / "runtime"
    slot_dir = _ready_slot(output, 1, 101)
    lease = acquire_slice(output, "task-a")
    receipt_path = slot_dir / "slice-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["app_id"] = 201
    receipt_path.write_text(json.dumps(receipt))
    (slot_dir / "webarena.config.json").write_text(json.dumps({"app_id": 201}))
    reset_calls = []

    class Pool:
        def __init__(self, _config):
            pass

        def validate_receipt(self, path):
            return json.loads(path.read_text())

        def runtime_config(self, current):
            return {"app_id": current["app_id"]}

        def reset(self, path):
            reset_calls.append(path)
            assert (
                json.loads((slot_dir / "slice-lease.json").read_text())["release_from_app_id"]
                == 201
            )
            clean = {**receipt, "app_id": 301}
            path.write_text(json.dumps(clean))
            return clean

    monkeypatch.setattr("harness.environments.webarena.rainbond.RainbondSlicePool", Pool)
    result = release_slice(tmp_path / "pool.json", output, lease["lease_id"])
    assert result["ready_app_id"] == 301
    assert reset_calls == [receipt_path]
    assert not (slot_dir / "slice-lease.json").exists()


@pytest.mark.parametrize(("status", "ready"), [(404, True), (499, True), (503, False)])
def test_wait_site_treats_client_error_responses_as_ready(tmp_path, monkeypatch, status, ready):
    pool = RainbondSlicePool(_pool_config(tmp_path), api=FakeAPI(), sleep=lambda _: None)
    calls = []

    def urlopen(request, timeout):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, status, "status", {}, None)

    # Deadline at 1s; two probes fall inside it, then the clock passes it.
    monotonic = iter([0.0, 0.0, 0.5, 5.0])
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr(
        "harness.environments.webarena.rainbond.time.monotonic", lambda: next(monotonic)
    )
    if ready:
        pool._wait_site("http://site.test/")
        assert calls == ["http://site.test/"]
    else:
        with pytest.raises(TimeoutError, match=f"HTTP {status}"):
            pool._wait_site("http://site.test/")
        assert len(calls) == 2
