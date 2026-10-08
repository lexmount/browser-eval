"""Reject synthetic DNS addresses before a local/remote comparison spends calls."""
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

_FAKE_IP_RANGE = ipaddress.ip_network("198.18.0.0/15")


def inspect_system_dns(urls, *, resolver=None):
    resolver = resolver or socket.getaddrinfo
    hosts = sorted({urlsplit(url).hostname for url in urls if urlsplit(url).hostname})
    def inspect(host):
        try:
            addresses = sorted({item[4][0] for item in resolver(host, 443, type=socket.SOCK_STREAM)})
        except OSError as error:
            return {"host": host, "addresses": [], "dns_error": type(error).__name__, "synthetic": False}
        synthetic = any(ipaddress.ip_address(address).version == 4 and
                        ipaddress.ip_address(address) in _FAKE_IP_RANGE for address in addresses)
        return {"host": host, "addresses": addresses, "synthetic": synthetic}
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(inspect, hosts))
    return {"schema_version": 1, "hosts": rows, "usable_for_guarded_local_comparison": not any(row["synthetic"] for row in rows)}


def require_usable_public_dns(audit):
    bad = [row["host"] for row in audit["hosts"] if row["synthetic"]]
    if bad:
        raise ValueError("System DNS returned synthetic 198.18.0.0/15 addresses for: " + ", ".join(bad) +
                         "; use a real-public-address DNS environment before comparison. Do not disable product safety guards.")


def preflight_guarded_providers(providers, cases):
    """Apply the same environment gate to every entry point using guarded tools."""
    guarded = {"openclaw_builtin", "deepseek_harness_builtin", "cowagent_builtin"}
    if not any(provider.type in guarded for provider in providers):
        return None
    audit = inspect_system_dns([case.url for case in cases])
    require_usable_public_dns(audit)
    return audit
