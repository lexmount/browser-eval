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
    """Require one declared DNS environment for all supported native callers."""
    from .network_dns import lookup_public_dns, validate_dns_mode
    native = {"openclaw_builtin", "deepseek_harness_builtin", "cowagent_builtin", "langchain_webbase"}
    selected = [provider for provider in providers if provider.type in native]
    if not selected:
        return None
    modes = {validate_dns_mode(provider.config.get("dns_mode", "system")) for provider in selected}
    if len(modes) != 1:
        raise ValueError("Selected native callers must declare the same dns_mode")
    mode = modes.pop()
    if mode == "system":
        audit = inspect_system_dns([case.url for case in cases])
    else:
        def resolve(host, port, **kwargs):
            return [(socket.AF_INET if family == 4 else socket.AF_INET6,
                     socket.SOCK_STREAM, 6, '', (address, port))
                    for address, family in lookup_public_dns(host, timeout=45)]
        audit = inspect_system_dns([case.url for case in cases], resolver=resolve)
        failed = [row["host"] for row in audit["hosts"] if row.get("dns_error")]
        if failed:
            raise ValueError("Public DNS preflight failed for: " + ", ".join(failed))
    audit["dns_mode"] = mode
    require_usable_public_dns(audit)
    return audit
