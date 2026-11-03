"""The public tree must not expose private hosts, addresses, home directories or share links."""
from __future__ import annotations

import hashlib
import ipaddress
import re
import subprocess
from pathlib import Path
from types import MappingProxyType

import pytest

from harness.environments.webarena.topology import RECORDING_GATEWAYS


ROOT = Path(__file__).resolve().parents[1]

# Only a digest, so this test does not republish the domain it rejects.
INTERNAL_DOMAIN_SHA256 = frozenset(
    {"4d1b2be2e1e82621b7c64f33c321b29320c4ab58927010c87376d522ed72b4f2"}
)
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172." + "16.0.0/12", "192.168.0.0/16")
)
# Generic values for tests that need a private address, and the range boundaries above.
GENERIC_FIXTURE_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/24", "172." + "16.0.0/24", "192.168.0.0/24")
)
PLACEHOLDER_HOMES = frozenset(
    {
        "actual-user",
        "dot_pi",
        "evaluator",
        "example",
        "fixture",
        "fixture-other",
        "k",
        "kim",
        "not-a-text-path",
        "one",
        "person",
        "private",
        "private-home",
        "runner",
        "two",
    }
)
_D = "evaluation/trajectory-replay/webarena-verified"
PINNED_DATASET_INPUTS = MappingProxyType(
    {
        # The pinned webarena-replay dataset binds these and the data lock pins that dataset, so
        # they change only through a new dataset edition. Each entry names a private host or the
        # recording gateway. The digest keeps an exemption to these exact bytes.
        # File hash in corrected-90-v1, and action digests in publication-input-bindings.
        f"{_D}/retrieval-candidates/task-243.json": (
            "6bbd9124f65221a096d90127483f523076046dc936d24cd680768d48640597f5"
        ),
        f"{_D}/trajectories-corrected.json": (
            "624a0ea83c6e4c3533714e961656d7e4159189a9e4941017b06a5299e4dc31d5"
        ),
        # File hash in full-812-v1.
        "research/trajectory-replay-task67-reference-audit.json": (
            "7057e6eb5cfd9c49d23d1984b9fce96e8ea13f68a4698dba5cef0eb70739bef2"
        ),
        # Action digests in publication-input-bindings.
        f"{_D}/retrieval-candidates/task-108-stabilized.json": (
            "b45982774cf2e89f2ae1d6df56024501185efa6a6f420aa6fda731b67ed446af"
        ),
        f"{_D}/retrieval-candidates/task-132.json": (
            "c8737bf60e76d97e1f29d504cb028db2abaa51af5876e0a826abe75afe383cce"
        ),
        f"{_D}/retrieval-candidates/task-168.json": (
            "a7201b1ec4bac5f8092efcdf8bc05696a1f4c354c782b3a7340aba13eb10171e"
        ),
        f"{_D}/retrieval-candidates/task-204.json": (
            "729ece693e0ec1105c1239cb993c9875caacb675668bead4aea00e86f61e358f"
        ),
        f"{_D}/retrieval-candidates/task-29.json": (
            "eb3525342e22372a6241a7a19bfebfd3f491cc5b8ee55d95f4a4ddfdeec5d0fe"
        ),
        f"{_D}/retrieval-candidates/task-303.json": (
            "dd8f29defdc326c2ad618d6c0cbb7c04311cb70203c914360e2fcad841859614"
        ),
        f"{_D}/retrieval-candidates/task-313.json": (
            "2ed9685a48cbd674e424df631f5133fdea800b7ada08dc3e60517f080838ad08"
        ),
        f"{_D}/retrieval-candidates/task-314.json": (
            "36341f8f8bd878ca57f91d570b51b249894e07c11ed0ba3eed3be41d0478b456"
        ),
        f"{_D}/retrieval-candidates/task-386.json": (
            "87b32f3592d08b048d8d3eff74c3ebdae66801aabb6aafd5140580b5939ff805"
        ),
        f"{_D}/retrieval-candidates/task-784.json": (
            "c926ed0c2617186bf70431006c2719aaf0ea1fda6f70224e9c10e114e24c7c63"
        ),
        f"{_D}/trajectory-candidates/task-404.json": (
            "6b498639479005f5d8e089abbea3fd0f63acaa172e7b66ba7a6c50e4d3273609"
        ),
        f"{_D}/trajectory-candidates/task-464.json": (
            "06dc0afaa7d3a2aef1c5e6777520069dc7add2b73e61825ce98692cd5d093746"
        ),
        f"{_D}/trajectory-candidates/task-610.json": (
            "3cfb17cde4629344b3c9688204383c2779ac8030b042e80f5c6a6ca5d8b20d38"
        ),
        f"{_D}/trajectory-candidates/task-771.json": (
            "cb2866eed4f9c08c1fb251db6487c3474aa1f86257c91efce174c385381908d1"
        ),
        # Exact parent of task-108-stabilized in its declared lineage.
        f"{_D}/retrieval-candidates/task-108-ajax-proposal.json": (
            "aae7171e90cb3f309400fa7c5289ae3e6a13254c8c075b0cf591f6468e81514a"
        ),
        # Action prefix equals the bound trajectory in trajectories-corrected.
        f"{_D}/retrieval-candidates/task-66.json": (
            "352bc6f546170e8f7201b0667b101307e0be0a4bf087d24a3384624209bf29c2"
        ),
        f"{_D}/retrieval-candidates/task-66-budget.json": (
            "8066148fbbc8acd2d30e4488f384b053dbed7988a3605c28e5c6dd5033ded206"
        ),
        f"{_D}/retrieval-candidates/task-349.json": (
            "33bb5bb5540970507b518d9d0097a7c378fe00284eaec8b5bb4d03e6b33f869b"
        ),
        # Base of trajectories-corrected, which records this file hash.
        f"{_D}/trajectories.json": (
            "61bb38bce552b5c7eea0e1f5fcca1bc898f55e2e39ee6e72498cff346ca1e4e3"
        ),
    }
)

_SITE = "artifact-" + "site"
_HUB = "artifact-" + "hub"
_HOME = re.compile(r"(?:(?<=file://)|(?<![\w./%-]))/(?:Users|home)/([A-Za-z0-9_][A-Za-z0-9_.-]*)")
_IPV4 = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?!\d|\.\d)")
_HOSTNAME = re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+){2,}")
_SHARE_LINK = re.compile(
    r"https?://(?:artifact-(?:site|hub)[^/\s\"'\\]*|[^/\s\"'\\]*lexmount[^/\s\"'\\]*)"
    r"/(?:v|s)/[A-Za-z0-9_-]+"
)
_PREVIEW_SHARE = re.compile(r"__artifact_share=([A-Za-z0-9_-]+)")
PLACEHOLDER_SHARE_TOKENS = frozenset({"public-share"})


def personal_homes(text: str) -> set[str]:
    return {name for name in _HOME.findall(text) if name not in PLACEHOLDER_HOMES}


def internal_hosts(text: str, digests: frozenset[str] = INTERNAL_DOMAIN_SHA256) -> set[str]:
    found = set()
    for host in set(_HOSTNAME.findall(text)):
        suffix = ".".join(host.lower().split(".")[-3:])
        if hashlib.sha256(suffix.encode()).hexdigest() in digests:
            found.add(host)
    return found


def private_addresses(text: str) -> set[str]:
    found = set()
    for value in set(_IPV4.findall(text)):
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if (
            any(address in network for network in PRIVATE_NETWORKS)
            and not any(address in network for network in GENERIC_FIXTURE_NETWORKS)
        ):
            found.add(value)
    return found


def share_links(text: str) -> set[str]:
    tokens = set(_PREVIEW_SHARE.findall(text)) - PLACEHOLDER_SHARE_TOKENS
    return set(_SHARE_LINK.findall(text)) | tokens


def _tracked_texts() -> dict[str, str]:
    listed = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "-z"], text=True)
    texts = {}
    for name in listed.split("\0"):
        path = ROOT / name
        if not name or path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" not in data:
            texts[name] = data.decode("utf-8", errors="ignore")
    return texts


def test_pinned_inputs_keep_their_exact_bytes() -> None:
    actual = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in PINNED_DATASET_INPUTS
    }
    assert actual == dict(PINNED_DATASET_INPUTS)


def test_tracked_text_has_no_private_values() -> None:
    findings = {}
    exempt_hits = set()
    for name, text in _tracked_texts().items():
        hosts = internal_hosts(text)
        addresses = private_addresses(text)
        if name in PINNED_DATASET_INPUTS:
            gateways = addresses & set(RECORDING_GATEWAYS)
            if hosts or gateways:
                exempt_hits.add(name)
            hosts = set()
            addresses -= gateways
        # Report counts and kinds only, so a failure log does not echo a value.
        kinds = {
            "home": len(personal_homes(text)),
            "host": len(hosts),
            "address": len(addresses),
            "share": len(share_links(text)),
        }
        if any(kinds.values()):
            findings[name] = kinds
    assert findings == {}
    assert exempt_hits == set(PINNED_DATASET_INPUTS), "remove exemptions that no longer apply"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("open /" + "Users/alice/.cache", {"alice"}),
        ('"path": "/' + 'home/alice/run"', {"alice"}),
        ("open /Users/example/.cache", set()),
        ("open /home/runner/work", set()),
        ("https://site.test/Users/alice", set()),
        ("file:///" + "home/alice/run", {"alice"}),
        ("PATH=/bin:/" + "Users/alice/bin", {"alice"}),
        ("file:///Users/example/run", set()),
        ("PATH=/bin:/home/runner/bin", set()),
        ("https://site.test/home/alice", set()),
    ],
)
def test_personal_home_controls(text: str, expected: set[str]) -> None:
    assert personal_homes(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("http://shop.dev.example.test/admin", {"shop.dev.example.test"}),
        ('"pattern": "*.dev.example.test"', {"dev.example.test"}),
        ("http://shop.other.example.test/admin", set()),
        ("http://shop.dev.example.test.evil.org/", set()),
        ("http://adev.example.test/", set()),
    ],
)
def test_internal_host_controls(text: str, expected: set[str]) -> None:
    digest = hashlib.sha256(b"dev.example.test").hexdigest()
    assert internal_hosts(text, frozenset({digest})) == expected


def _ipv4(*octets: int) -> str:
    """Build a control address, so this file's own text stays clean under the scan."""
    return ".".join(map(str, octets))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"http://{_ipv4(10, 20, 30, 40)}:8080/", {_ipv4(10, 20, 30, 40)}),
        (f"route {_ipv4(172, 16, 5, 9)}", {_ipv4(172, 16, 5, 9)}),
        (f'"{_ipv4(192, 168, 77, 1)}"', {_ipv4(192, 168, 77, 1)}),
        ("http://10.0.0.11:8080/", set()),
        ("http://192.168.0.2:7780/", set()),
        ("http://192.0.2.41:18023/", set()),
        ("resolver 8.8.8.8", set()),
        (f"version {_ipv4(10, 20, 30, 40)}.5", set()),
    ],
)
def test_private_address_controls(text: str, expected: set[str]) -> None:
    assert private_addresses(text) == expected


def test_real_digest_ignores_the_reserved_fixture_domain() -> None:
    assert internal_hosts("http://shop.dev.example.test/") == set()


@pytest.mark.parametrize(
    ("text", "found"),
    [
        (f"https://{_SITE}.example.org/v/abcDEF_123", True),
        (f"https://{_HUB}.example.org/s/abc?version=ver_1", True),
        ("https://reports.lex" + "mount.example/v/abc", True),
        ("https://host.test/api/preview/site/?__artifact_" + "share=opaque_Token-1", True),
        ("https://host.test/api/preview/site/?__artifact_share=public-share", False),
        ("https://host.test/api/preview/site/?__artifact_share=", False),
        ("https://artifact-site.example.org", False),
        ("https://artifact-site.example.org/api/preview/abc", False),
        ("https://artifact.example/v/abc", False),
        ("https://api.lexmount.example/v1/fetch", False),
    ],
)
def test_share_link_controls(text: str, found: bool) -> None:
    assert bool(share_links(text)) is found
