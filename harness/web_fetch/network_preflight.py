"""Reject non-public DNS addresses before a public local/remote comparison spends calls."""
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

GUARDED_NATIVE_TYPES = {"openclaw_builtin", "deepseek_harness_builtin", "cowagent_builtin", "langchain_webbase", "moli_webfetch", "crawl4ai"}

_FAKE_IP_RANGE = ipaddress.ip_network("198.18.0.0/15")


def inspect_system_dns(urls, *, resolver=None):
    resolver = resolver or socket.getaddrinfo
    hosts = sorted({urlsplit(url).hostname for url in urls if urlsplit(url).hostname})
    def inspect(host):
        try:
            addresses = sorted({item[4][0] for item in resolver(host, 443, type=socket.SOCK_STREAM)})
        except OSError as error:
            return {"host": host, "addresses": [], "dns_error": type(error).__name__,
                    "synthetic": False, "non_public": False}
        parsed = [ipaddress.ip_address(address) for address in addresses]
        synthetic = any(address.version == 4 and address in _FAKE_IP_RANGE
                        for address in parsed)
        non_public = any(not address.is_global for address in parsed)
        return {"host": host, "addresses": addresses, "synthetic": synthetic,
                "non_public": non_public}
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(inspect, hosts))
    return {
        "schema_version": 1,
        "hosts": rows,
        "usable_for_guarded_local_comparison": not any(
            row["synthetic"] or row.get("non_public") or row.get("dns_error")
            for row in rows
        ),
    }


def require_usable_public_dns(audit, *, allow_partial_resolution_failures=False):
    """Reject unsafe answers and environment-wide DNS failure."""
    synthetic = [row["host"] for row in audit["hosts"] if row["synthetic"]]
    if synthetic:
        raise ValueError("System DNS returned synthetic 198.18.0.0/15 addresses for: " + ", ".join(synthetic) +
                         "; use a real-public-address DNS environment before comparison. Do not disable product safety guards.")
    non_public = [row["host"] for row in audit["hosts"] if row.get("non_public")]
    if non_public:
        raise ValueError("DNS returned non-public addresses for public benchmark hosts: "
                         + ", ".join(non_public)
                         + "; use the declared public_doh environment before comparison. "
                           "Do not disable product safety guards.")
    unresolved = [row["host"] for row in audit["hosts"] if row.get("dns_error")]
    resolved = [row["host"] for row in audit["hosts"] if not row.get("dns_error")]
    if unresolved and (not allow_partial_resolution_failures or not resolved):
        scope = "all declared public benchmark hosts" if not resolved else "public benchmark hosts"
        raise ValueError("DNS resolution failed for " + scope + ": " + ", ".join(unresolved))


    # Recompute from retained addresses as well, so older preflight receipts
    # without the diagnostic field cannot conceal a private-address answer.
    non_public = [row["host"] for row in audit["hosts"]
                  if any(not ipaddress.ip_address(address).is_global
                         for address in row.get("addresses", []))]
    if non_public:
        raise ValueError("System DNS returned non-public addresses for: " + ", ".join(non_public) +
                         "; use a real-public-address DNS environment before comparison. Do not disable product safety guards.")


def preflight_guarded_providers(providers, cases):
    """Require one declared DNS environment for every local comparison method."""
    from .network_dns import lookup_public_dns, validate_dns_mode
    native = {"openclaw_builtin", "deepseek_harness_builtin", "cowagent_builtin", "langchain_webbase", "moli_webfetch", "crawl4ai"}
    selected = [
        provider for provider in providers
        if provider.type in native or (
            provider.type in {"headless_markdown", "headless_html"}
            and provider.config.get("engine") != "kitesurf"
        )
    ]
    isolated = [provider for provider in selected
                if provider.config.get("network_policy") == "loopback_only"
                and provider.config.get("allow_private_network") is True]
    if isolated:
        for case in cases:
            host = urlsplit(case.url).hostname
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = host == "localhost"
            if not loopback:
                raise ValueError("Isolated fixture runs require loopback task URLs")
        selected = [provider for provider in selected if provider not in isolated]
    if not selected:
        return None
    modes = {validate_dns_mode(provider.config.get("dns_mode", "system")) for provider in selected}
    if len(modes) != 1:
        raise ValueError("Selected local methods must declare the same dns_mode")
    mode = modes.pop()
    if mode == "system":
        audit = inspect_system_dns([case.url for case in cases])
    else:
        def resolve(host, port, **kwargs):
            return [(socket.AF_INET if family == 4 else socket.AF_INET6,
                     socket.SOCK_STREAM, 6, '', (address, port))
                    for address, family in lookup_public_dns(host, timeout=45)]
        audit = inspect_system_dns([case.url for case in cases], resolver=resolve)
    audit["dns_mode"] = mode
    audit["unresolved_hosts"] = [
        row["host"] for row in audit["hosts"] if row.get("dns_error")
    ]
    # ``usable_for_guarded_local_comparison`` keeps its original strict
    # meaning: every declared host resolved only to public addresses.  Under
    # either declared resolver, a partial lookup outage is left to the affected
    # measured call. No successful lookup in a nonempty scope still indicates
    # an unusable environment; unsafe answers always fail closed above.
    require_usable_public_dns(audit, allow_partial_resolution_failures=True)
    return audit
