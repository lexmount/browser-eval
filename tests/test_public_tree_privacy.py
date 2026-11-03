"""The public tree must not expose private hosts, addresses, home directories or share links."""
from __future__ import annotations

import hashlib
import ipaddress
import re
import subprocess
from pathlib import Path

import pytest


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


def test_tracked_text_has_no_private_values() -> None:
    findings = {}
    for name, text in _tracked_texts().items():
        hosts = internal_hosts(text)
        addresses = private_addresses(text)
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
