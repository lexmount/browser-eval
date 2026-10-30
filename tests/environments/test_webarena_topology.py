"""The WebArena topology registry must reproduce every frozen host, port and redaction table."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from webarena_verified.types.task import WebArenaSite

from harness.browser_use.publication import sanitize, sanitize_text
from harness.environments.webarena import runtime, topology
from harness.environments.webarena.config import SITES, verify_deployment_contract
from harness.trajectory_replay import remote_runtime
from harness.trajectory_replay.public_webarena import webarena_environment
from harness.trajectory_replay.remote_runtime import rebind_remote_calls
from harness.trajectory_replay.webarena_replay import _json_sha256

ROOT = Path(__file__).resolve().parents[2]

# Tables as they were hard-coded before the registry existed. Frozen catalogs,
# public evidence and recorded trajectories were produced with exactly these.
FROZEN_SITE_PORTS = {
    WebArenaSite.SHOPPING: (7770, 7771),
    WebArenaSite.SHOPPING_ADMIN: (7780, 7781),
    WebArenaSite.REDDIT: (9999, 9998),
    WebArenaSite.GITLAB: (8023, 8024),
}
FROZEN_READ_ONLY_PORTS = {8888: WebArenaSite.WIKIPEDIA, 3000: WebArenaSite.MAP}
FROZEN_LOCAL_HOSTS = {"127.0.0.1", "192.168.5.2", "host.docker.internal"}
FROZEN_TRUSTED_HOSTS = FROZEN_LOCAL_HOSTS | {
    "onestopmarket.example.com",
    "gitlab.example.com",
    "openstreetmap.example.com",
    "wikipedia.example.com",
    "ec2-3-133-227-75.us-east-2.compute.amazonaws.com",
    "ec2-3-135-39-80.us-east-2.compute.amazonaws.com",
}
FROZEN_RECORDED_PORT_SITES = {
    3000: "map",
    4399: "reddit",
    7770: "shopping",
    7780: "shopping_admin",
    8023: "gitlab",
    8888: "wikipedia",
    9999: "reddit",
}
FROZEN_OFFICIAL_TRACE_PORTS = {
    "map": (3000,),
    "shopping": (7770,),
    "shopping_admin": (7780,),
    "gitlab": (8023,),
    "wikipedia": (8080, 8888),
    "reddit": (9999,),
}
FROZEN_ORIGIN_REPLACEMENTS = {
    "http://host.docker.internal:7770": "__SHOPPING__",
    "http://host.docker.internal:7780/admin": "__SHOPPING_ADMIN__",
    "http://host.docker.internal:7780": "__SHOPPING_ADMIN__",
    "http://host.docker.internal:9999": "__REDDIT__",
    "http://host.docker.internal:8023": "__GITLAB__",
    "http://192.168.5.2:7770": "__SHOPPING__",
    "http://192.168.5.2:7780/admin": "__SHOPPING_ADMIN__",
    "http://192.168.5.2:7780": "__SHOPPING_ADMIN__",
    "http://192.168.5.2:9999": "__REDDIT__",
    "http://192.168.5.2:8023": "__GITLAB__",
}
FROZEN_SECRETS = ("Password.123", "admin1234", "test1234", "hello1234")

REMOTE_SITES = {
    WebArenaSite.SHOPPING: "https://shopping.remote.test",
    WebArenaSite.SHOPPING_ADMIN: "https://admin.remote.test/admin",
    WebArenaSite.REDDIT: "https://reddit.remote.test",
    WebArenaSite.GITLAB: "https://gitlab.remote.test",
    WebArenaSite.MAP: "https://map.remote.test",
    WebArenaSite.WIKIPEDIA: "https://wiki.remote.test",
}
RECORDED_CALLS = [
    {"action": "navigate", "url": "http://192.168.5.2:9999/f/DIY"},
    {"action": "navigate", "url": "http://192.168.5.2:7780/admin/sales/order/"},
    {"action": "navigate", "url": "http://host.docker.internal:7770/checkout/cart/"},
    {"action": "fill", "text": "See http://127.0.0.1:8023/byteblaze/project and more"},
    {"action": "click", "selector": "a[href='http://onestopmarket.example.com:7770/x.html']"},
    {"action": "navigate", "url": "http://wikipedia.example.com:8888/wiki/Pittsburgh"},
    {
        "action": "navigate",
        "url": "http://ec2-3-135-39-80.us-east-2.compute.amazonaws.com:3000/#map=7/42/-71",
    },
    {"action": "navigate", "url": "http://untrusted.test:7770/"},
]
# Digest of RECORDED_CALLS rebound with the frozen trusted host set on main.
FROZEN_BOUND_CALLS_SHA256 = "6a2e9d5502b92dc4c1e709896beeed562870f3718fffa1f42c908e7a8b423ed8"


def test_registry_reproduces_frozen_port_and_host_tables() -> None:
    assert runtime.SITE_PORTS == FROZEN_SITE_PORTS
    assert list(runtime.SITE_PORTS) == list(FROZEN_SITE_PORTS)
    assert all(type(site) is WebArenaSite for site in runtime.SITE_PORTS)
    assert list(runtime.SITE_CREDENTIALS) == list(FROZEN_SITE_PORTS)
    assert remote_runtime.READ_ONLY_PORTS == FROZEN_READ_ONLY_PORTS
    assert topology.LOCAL_RECORDED_HOSTS == FROZEN_LOCAL_HOSTS
    assert topology.TRUSTED_RECORDED_HOSTS == FROZEN_TRUSTED_HOSTS
    assert topology.RECORDED_PORT_SITES == FROZEN_RECORDED_PORT_SITES
    assert topology.OFFICIAL_TRACE_PORTS == FROZEN_OFFICIAL_TRACE_PORTS
    assert topology.SITE_CONTAINER_PORTS == {
        "shopping": 80,
        "shopping_admin": 80,
        "reddit": 80,
        "gitlab": 8023,
        "map": 8080,
    }
    assert set(topology.TOPOLOGY["sites"]) == set(SITES)
    assert sorted(remote_runtime.UNREBOUND_SOURCE_HOST.pattern.split("|")) == sorted(
        re.escape(host) for host in FROZEN_LOCAL_HOSTS
    )
    assert webarena_environment._LEGACY_ORIGIN_RE.pattern == (
        r"https?://[a-z0-9.-]+:(?P<port>3000|4399|7770|7780|8023|8888|9999)"
    )


def test_recording_gateway_is_configuration_not_code() -> None:
    payload = json.loads(topology.TOPOLOGY_PATH.read_text(encoding="utf-8"))
    assert list(payload["hosts"]["recording_gateways"]) == ["192.168.5.2"]
    pattern = re.compile(r"192\\?\.168\\?\.5\\?\.2(?![0-9])")
    offenders = [
        str(path.relative_to(ROOT))
        for path in sorted((ROOT / "src").rglob("*.py"))
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_frozen_bound_calls_digest_is_unchanged() -> None:
    frozen = rebind_remote_calls(RECORDED_CALLS, REMOTE_SITES, set(FROZEN_TRUSTED_HOSTS))
    current = rebind_remote_calls(
        RECORDED_CALLS, REMOTE_SITES, set(topology.TRUSTED_RECORDED_HOSTS)
    )

    assert current == frozen
    assert _json_sha256(current) == FROZEN_BOUND_CALLS_SHA256
    assert current[0]["url"] == "https://reddit.remote.test/f/DIY"
    assert current[-1]["url"] == "http://untrusted.test:7770/"


def test_bound_calls_digest_detects_a_missing_recording_gateway() -> None:
    without_gateway = set(topology.TRUSTED_RECORDED_HOSTS) - set(topology.RECORDING_GATEWAYS)
    rebound = rebind_remote_calls(RECORDED_CALLS, REMOTE_SITES, without_gateway)

    assert rebound[0]["url"] == "http://192.168.5.2:9999/f/DIY"
    assert _json_sha256(rebound) != FROZEN_BOUND_CALLS_SHA256


def _frozen_sanitize_text(value: str) -> str:
    text = value
    for origin, placeholder in sorted(
        FROZEN_ORIGIN_REPLACEMENTS.items(), key=lambda item: len(item[0]), reverse=True
    ):
        text = text.replace(origin, placeholder)
    for secret in FROZEN_SECRETS:
        text = text.replace(secret, "[REDACTED]")
    text = text.replace("host.docker.internal", "__BENCHMARK_HOST__")
    text = text.replace("192.168.5.2", "__BENCHMARK_HOST__")
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    return re.sub(r"/(?:Users|home|private|tmp|var|opt)/[^\s\"']+", "<LOCAL_PATH>", text)


@pytest.mark.parametrize(
    "text",
    [
        "http://192.168.5.2:7780/admin/sales/order/view/order_id/302/",
        "http://host.docker.internal:7780/catalog and http://192.168.5.2:7770/cart",
        "login MarvelsGrantMan136 / test1234 at http://192.168.5.2:9999/login",
        "admin1234 Password.123 hello1234 http://host.docker.internal:8023/explore",
        "gateway 192.168.5.2 and alias host.docker.internal:3000/#map",
        "loopback http://127.0.0.1:7770/ stays unredacted",
        "Bearer abc.def from /Users/example/run/output.json",
    ],
)
def test_public_redaction_output_is_unchanged(text: str) -> None:
    assert sanitize_text(text) == _frozen_sanitize_text(text)


def test_public_redaction_still_removes_every_site_password() -> None:
    record = {"message": " ".join(FROZEN_SECRETS), "url": "http://192.168.5.2:7780/admin/x"}
    public = sanitize(record)

    assert public == {
        "message": "[REDACTED] [REDACTED] [REDACTED] [REDACTED]",
        "url": "__SHOPPING_ADMIN__/x",
    }
    assert topology.known_site_secrets() == FROZEN_SECRETS


def _copy_deployment_contract(root: Path) -> None:
    for relative in (
        "evaluation/browser-use-public/webarena-environment-images.json",
        "deploy/webarena/runtime.example.json",
        "deploy/webarena/site-delivery-contract.json",
        "examples/trajectory_replay/public_webarena/webarena.config.example.json",
        "examples/trajectory_replay/public_webarena/rainbond-pool.example.json",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())


def test_deployment_contract_rejects_template_ports_that_leave_the_registry(
    tmp_path: Path,
) -> None:
    assert verify_deployment_contract(ROOT)["status"] == "pass"
    _copy_deployment_contract(tmp_path)
    runtime_path = tmp_path / "deploy/webarena/runtime.example.json"
    evaluation_path = (
        tmp_path / "examples/trajectory_replay/public_webarena/webarena.config.example.json"
    )
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    runtime["sites"]["reddit"]["port"] = 4399
    evaluation["environments"]["__REDDIT__"]["urls"] = ["http://__HOST__:4399"]
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")

    with pytest.raises(ValueError, match="topology registry: reddit"):
        verify_deployment_contract(tmp_path)


@pytest.mark.parametrize(
    ("site", "field", "value"),
    [
        ("gitlab", "service_port", 80),
        ("map", "service_port", 80),
        ("reddit", "control_port", 8878),
    ],
)
def test_deployment_contract_rejects_delivery_ports_that_leave_the_registry(
    tmp_path: Path, site: str, field: str, value: int
) -> None:
    _copy_deployment_contract(tmp_path)
    delivery_path = tmp_path / "deploy/webarena/site-delivery-contract.json"
    delivery = json.loads(delivery_path.read_text(encoding="utf-8"))
    assert (
        delivery["components"][site]["service_port"],
        delivery["components"][site]["control_port"],
    ) == (topology.SITE_CONTAINER_PORTS[site], topology.ENV_CTRL_CONTAINER_PORT)
    delivery["components"][site][field] = value
    delivery_path.write_text(json.dumps(delivery), encoding="utf-8")

    with pytest.raises(
        ValueError, match=f"container ports differ from the topology registry: {site}"
    ):
        verify_deployment_contract(tmp_path)


def test_registry_rejects_shared_ports(tmp_path: Path) -> None:
    payload = json.loads(topology.TOPOLOGY_PATH.read_text(encoding="utf-8"))
    payload["sites"]["wikipedia"]["control_port"] = payload["sites"]["map"]["port"]
    path = tmp_path / "topology.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="ports must be unique: wikipedia"):
        topology._load(path)


@pytest.mark.parametrize("field", ["recorded_ports", "official_trace_ports"])
def test_registry_rejects_ports_attributed_to_two_sites(tmp_path: Path, field: str) -> None:
    payload = json.loads(topology.TOPOLOGY_PATH.read_text(encoding="utf-8"))
    payload["sites"]["wikipedia"][field].append(payload["sites"]["reddit"][field][-1])
    path = tmp_path / "topology.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=f"{field} must attribute each port once: wikipedia"):
        topology._load(path)


def test_registry_consumers_import_without_the_optional_webarena_evaluator() -> None:
    program = """
import importlib.abc
import sys
class BlockEvaluator(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] == 'webarena_verified':
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, BlockEvaluator())
import harness.browser_use.publication
import harness.environments.webarena.config
import harness.environments.webarena.rainbond
import harness.trajectory_replay.public_webarena.publication
import harness.trajectory_replay.public_webarena.qualification
from harness.environments.webarena.topology import site_url
assert site_url('shopping_admin', 'h') == 'http://h:7780/admin'
"""
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
