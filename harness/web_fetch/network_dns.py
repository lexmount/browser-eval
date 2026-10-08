"""Explicit process-local DNS environment; never change product safety policy."""
from contextlib import contextmanager
import ipaddress
import json
import socket
import threading
import time
import urllib.parse
import urllib.request

DNS_ENDPOINT = "https://cloudflare-dns.com/dns-query"
DNS_MODES = {"system", "public_doh"}
_PATCH_LOCK = threading.Lock()


def validate_dns_mode(mode):
    if mode not in DNS_MODES:
        raise ValueError("dns_mode must be system or public_doh")
    return mode


def lookup_public_dns(hostname, *, timeout, opener=None):
    """Resolve A and AAAA once each; no fallback or retry; leave safety to the product."""
    try:
        literal = ipaddress.ip_address(hostname)
        return [(str(literal), 4 if literal.version == 4 else 6)]
    except ValueError:
        pass
    opener = opener or urllib.request.urlopen
    deadline = time.monotonic() + timeout
    addresses = []
    for rrtype, family in ((1, 4), (28, 6)):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("DNS query exceeded shared deadline")
        query = urllib.parse.urlencode({"name": hostname, "type": rrtype})
        request = urllib.request.Request(DNS_ENDPOINT + "?" + query,
                                         headers={"Accept": "application/dns-json"})
        with opener(request, timeout=remaining) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError("DNS response exceeds 64 KiB")
        payload = json.loads(raw)
        if payload.get("Status") != 0 or payload.get("TC"):
            raise socket.gaierror("Public DNS returned an unsuccessful or truncated answer")
        for answer in payload.get("Answer", []):
            if answer.get("type") != rrtype:
                continue
            value = str(ipaddress.ip_address(answer["data"]))
            if ipaddress.ip_address(value).version != family:
                raise ValueError("DNS record family mismatch")
            if (value, family) not in addresses:
                addresses.append((value, family))
    if not addresses:
        raise socket.gaierror("Public DNS returned no addresses")
    return addresses


@contextmanager
def public_dns_context(mode, timeout, *, resolver=None):
    """Scope socket resolution to the calling thread, including redirects.

    Other threads and the resolver's own HTTPS connection keep system DNS.
    Native private-address checks still inspect the real answer set. There is
    no cache, URL rewrite, product fallback or fetch retry.
    """
    validate_dns_mode(mode)
    traces = []
    if mode == "system":
        yield traces
        return
    resolver = resolver or lookup_public_dns
    thread_id = threading.get_ident()
    deadline = time.monotonic() + timeout
    active = False
    with _PATCH_LOCK:
        original = socket.getaddrinfo
        def lookup(host, port, family=0, type=0, proto=0, flags=0):
            nonlocal active
            if threading.get_ident() != thread_id or active or not host:
                return original(host, port, family, type, proto, flags)
            host = host.decode() if isinstance(host, bytes) else str(host)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("DNS query exceeded shared deadline")
            active = True
            try:
                addresses = resolver(host, timeout=remaining)
            finally:
                active = False
            traces.append({"hostname": host, "addresses": [value for value, _ in addresses]})
            rows = []
            for address, version in addresses:
                address_family = socket.AF_INET if version == 4 else socket.AF_INET6
                if family not in (0, socket.AF_UNSPEC, address_family):
                    continue
                rows.extend(original(address, port, address_family, type, proto, flags | socket.AI_NUMERICHOST))
            if not rows:
                raise socket.gaierror("DNS returned no addresses for requested family")
            return rows
        socket.getaddrinfo = lookup
        try:
            yield traces
        finally:
            socket.getaddrinfo = original
