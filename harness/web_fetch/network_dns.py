"""Explicit process-local DNS environment; never change product safety policy."""

import asyncio
import hashlib
import ipaddress
import json
import math
import os
import pathlib
import platform
import secrets
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from harness.web_fetch.diagnostics import emit

DNS_ENDPOINT = "https://cloudflare-dns.com/dns-query"
DNS_MODES = {"system", "public_doh"}
_PATCH_LOCK = threading.Lock()
_DNS_LOCAL = threading.local()
_PATCH_USERS = 0
_SYSTEM_GETADDRINFO = socket.getaddrinfo


def _dispatch_lookup(*args, **kwargs):
    lookup = getattr(_DNS_LOCAL, "lookup", None)
    return (lookup or _SYSTEM_GETADDRINFO)(*args, **kwargs)


def validate_dns_mode(mode):
    if mode not in DNS_MODES:
        raise ValueError("dns_mode must be system or public_doh")
    return mode


def configure_chromium_dns_profile(profile, mode):
    """Configure a fresh Chromium profile for one declared DNS mode."""
    mode = validate_dns_mode(mode)
    if mode == "system":
        return {"dns_mode": "system"}
    profile = Path(profile)
    state = profile / "Local State"
    if state.exists() or state.is_symlink():
        raise ValueError("Chromium DNS profile must be fresh")
    state.write_text(json.dumps({
        "dns_over_https": {"mode": "secure", "templates": DNS_ENDPOINT + "{?dns}"},
        "built_in_dns_client_enabled": True,
    }))
    return {
        "dns_mode": "public_doh",
        "dns_endpoint": DNS_ENDPOINT,
        "secure_dns_mode": "secure",
        "system_dns_fallback": False,
        "dns_bootstrap": "system resolver for the fixed DoH endpoint only",
    }


def lookup_public_dns(hostname, *, timeout, opener=None):
    """Resolve A and AAAA once each; no fallback or retry; leave safety to the product."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise TimeoutError("DNS query exceeded shared deadline")
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
        started = time.monotonic()
        phase = "connect_or_headers"
        emit("doh_query_started", hostname=hostname, record_type=rrtype,
             resolver=DNS_ENDPOINT, timeout_seconds=remaining)
        try:
            with opener(request, timeout=remaining) as response:
                phase = "body"
                emit("doh_headers_received", hostname=hostname, record_type=rrtype)
                raw = response.read(65537)
            if time.monotonic() >= deadline:
                raise TimeoutError("DNS response arrived after shared deadline")
        except Exception as error:
            emit("doh_query_failed", hostname=hostname, record_type=rrtype,
                 phase=phase, error_type=type(error).__name__, message=str(error),
                 elapsed_ms=round((time.monotonic() - started) * 1000, 2))
            raise
        if len(raw) > 65536:
            raise ValueError("DNS response exceeds 64 KiB")
        payload = json.loads(raw)
        if payload.get("Status") != 0 or payload.get("TC"):
            raise socket.gaierror("Public DNS returned an unsuccessful or truncated answer")
        emit("doh_query_completed", hostname=hostname, record_type=rrtype,
             elapsed_ms=round((time.monotonic() - started) * 1000, 2))
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
        previous = getattr(_DNS_LOCAL, "lookup", None)
        _DNS_LOCAL.lookup = None
        try:
            yield traces
        finally:
            _DNS_LOCAL.lookup = previous
        return
    global _PATCH_USERS, _SYSTEM_GETADDRINFO
    resolver = resolver or lookup_public_dns
    deadline = time.monotonic() + timeout
    active = False
    previous = getattr(_DNS_LOCAL, "lookup", None)
    with _PATCH_LOCK:
        if _PATCH_USERS == 0:
            _SYSTEM_GETADDRINFO = socket.getaddrinfo
            socket.getaddrinfo = _dispatch_lookup
        _PATCH_USERS += 1
        original = _SYSTEM_GETADDRINFO
    def lookup(host, port, family=0, type=0, proto=0, flags=0):
        nonlocal active
        if active or not host:
            return original(host, port, family, type, proto, flags)
        host = host.decode() if isinstance(host, bytes) else str(host)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("DNS query exceeded shared deadline")
        active = True
        try:
            emit("dns_resolution_started", hostname=host, timeout_seconds=remaining)
            addresses = resolver(host, timeout=remaining)
            if time.monotonic() >= deadline:
                raise TimeoutError("DNS response arrived after shared deadline")
        except Exception as error:
            emit("dns_resolution_failed", hostname=host,
                 error_type=error.__class__.__name__, message=str(error))
            raise
        finally:
            active = False
        emit("dns_resolution_completed", hostname=host, addresses=[value for value, _ in addresses])
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
    _DNS_LOCAL.lookup = lookup
    try:
        yield traces
    finally:
        _DNS_LOCAL.lookup = previous
        with _PATCH_LOCK:
            _PATCH_USERS -= 1
            if _PATCH_USERS == 0:
                socket.getaddrinfo = _SYSTEM_GETADDRINFO

class NativeDnsBroker:
    """Authenticated loopback bridge for native getaddrinfo consumers.

    One active call deadline covers every query. The bridge never caches,
    filters private addresses, retries, or falls back to system DNS. The system
    resolver is used only by this parent's HTTPS transport to the fixed DoH host.
    """
    def __init__(self, *, resolver=None, diagnostics=None):
        self.resolver = resolver or lookup_public_dns
        self.diagnostics = diagnostics
        self.token = secrets.token_hex(32)
        self.deadline = 0.0
        self.generation = 0
        self.traces = []
        self.trace_lock = threading.Lock()
        broker = self
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(2)
                line = self.rfile.readline(4097)
                observed_host = None
                started = time.monotonic()
                try:
                    if len(line) > 4096 or not line.endswith(b'\n'):
                        raise ValueError('invalid DNS bridge request')
                    token, host = line.decode('ascii').strip().split(' ', 1)
                    if not secrets.compare_digest(token, broker.token) or len(host) > 253 or any(c.isspace() for c in host):
                        raise ValueError('invalid DNS bridge request')
                    observed_host = host
                    if broker.diagnostics:
                        broker.diagnostics.emit('caller_dns', 'resolver_query_started', hostname=host)
                    with broker.trace_lock:
                        deadline, generation = broker.deadline, broker.generation
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('DNS call deadline expired')
                    answers = broker.resolver(host, timeout=remaining)
                    if not answers or len(answers) > 256:
                        raise ValueError('invalid DNS answer count')
                    rows = []
                    for address, family in answers:
                        ip = ipaddress.ip_address(address)
                        if ip.version != family:
                            raise ValueError('DNS answer family mismatch')
                        rows.append(f'{family} {ip}\n')
                    if time.monotonic() >= deadline:
                        raise TimeoutError('DNS answer arrived after deadline')
                    with broker.trace_lock:
                        if generation != broker.generation:
                            raise TimeoutError("DNS call has been superseded")
                        broker.traces.append({'hostname': host, 'addresses': [address for address, _ in answers]})
                    self.wfile.write(('OK\n' + ''.join(rows) + 'END\n').encode('ascii'))
                    if broker.diagnostics:
                        broker.diagnostics.emit('caller_dns', 'resolver_query_returned', hostname=host, addresses=[address for address, _ in answers], elapsed_ms=round((time.monotonic()-started)*1000,2))
                except (OSError, ValueError, UnicodeError, TimeoutError) as error:
                    if broker.diagnostics:
                        broker.diagnostics.emit('caller_dns', 'resolver_query_failed' if observed_host else 'bridge_request_rejected', hostname=observed_host, error_type=type(error).__name__, message=str(error), elapsed_ms=round((time.monotonic()-started)*1000,2))
                    try:
                        self.wfile.write(b'ERROR\n')
                    except OSError:
                        pass
        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            block_on_close = False
        self.server = Server(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.05}, daemon=True)
        self.thread.start()

    def begin_call(self, timeout):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("DNS timeout must be positive and finite")
        with self.trace_lock:
            self.deadline = time.monotonic() + timeout
            self.generation += 1
            self.traces = []

    def environment(self, library, base=None):
        env = dict(os.environ if base is None else base)
        env.update(DYLD_INSERT_LIBRARIES=str(library),
                   BROWSER_EVAL_DNS_PORT=str(self.server.server_address[1]),
                   BROWSER_EVAL_DNS_TOKEN=self.token,
                   BROWSER_EVAL_DNS_TIMEOUT_SECONDS=str(max(0.001, self.deadline - time.monotonic())))
        return env

    def close(self):
        self.deadline = 0.0
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@asynccontextmanager
async def crawl4ai_public_dns_browser(config):
    """Give the configured Chrome channel a private strict-DoH Local State.

    Crawl4AI 0.9.3's persistent launch omits its chrome_channel. Start that
    channel with Playwright, then let Crawl4AI use its supported CDP route.
    Native launch arguments and extraction remain owned by Crawl4AI. The
    profile, browser and driver are all closed when the worker exits.
    """
    from crawl4ai.browser_manager import BrowserManager
    from playwright.async_api import async_playwright

    if config.chrome_channel != "chrome":
        raise ValueError("public_doh requires the frozen Chrome channel")
    with tempfile.TemporaryDirectory(prefix="webfetch-crawl-dns-") as directory:
        profile = pathlib.Path(directory)
        dns_metadata = configure_chromium_dns_profile(profile, "public_doh")
        arguments = BrowserManager(config)._build_browser_args()
        arguments["args"] += ["--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1"]
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(directory, **arguments)
            try:
                # Chrome owns endpoint allocation; do not reserve a fixed port.
                endpoint = profile / "DevToolsActivePort"
                while not endpoint.exists():
                    await asyncio.sleep(0.01)
                port = int(endpoint.read_text().splitlines()[0])
                if not 1 <= port <= 65535:
                    raise ValueError("invalid Chrome debugging endpoint")
                yield config.clone(cdp_url=f"http://127.0.0.1:{port}"), {
                    **dns_metadata,
                    "browser_channel": config.chrome_channel,
                    "browser_version": context.browser.version if context.browser else None,
                }
            finally:
                await context.close()


def _native_dns_sources():
    return [pathlib.Path(__file__).resolve().parent / "workers/native_dns_darwin.c"]


def validate_native_dns_library(library):
    """Bind native resolver bytes to current source, architecture and platform."""
    if platform.system() != "Darwin":
        raise ValueError("native public DNS bridge currently requires Darwin")
    if not library:
        raise ValueError("native_dns_library is required for Moli public_doh")
    path = pathlib.Path(library).expanduser().resolve()
    try:
        manifest = json.loads(path.with_suffix(".json").read_text())
        if manifest.get("platform") != "Darwin" or manifest.get("architecture") != platform.machine():
            raise ValueError("native DNS platform or architecture mismatch")
        expected = {source.name: hashlib.sha256(source.read_bytes()).hexdigest() for source in _native_dns_sources()}
        if manifest.get("source_sha256") != expected:
            raise ValueError("native DNS source changed; rebuild helper before measurement")
        if manifest.get("library_sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError("native DNS library hash mismatch")
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("native DNS library or binding manifest unavailable") from error
    return path, manifest


def build_native_dns_library(destination):
    """Explicit preparation step; never compile or swap runtimes during a call."""
    if platform.system() != "Darwin":
        raise ValueError("native public DNS bridge currently requires Darwin")
    path = pathlib.Path(destination).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    source, = _native_dns_sources()
    with tempfile.TemporaryDirectory(dir=path.parent) as directory:
        staged = pathlib.Path(directory) / path.name
        subprocess.run(["cc", "-dynamiclib", "-O2", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(staged)], check=True)
        manifest = {"platform": "Darwin", "architecture": platform.machine(),
                    "compiler": subprocess.check_output(["cc", "--version"], text=True).splitlines()[0],
                    "source_sha256": {
                        source.name: hashlib.sha256(source.read_bytes()).hexdigest()
                    },
                    "library_sha256": hashlib.sha256(staged.read_bytes()).hexdigest()}
        staged.replace(path)
        path.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n")
    return validate_native_dns_library(path)


@contextmanager
def native_process_dns_environment(mode, library, deadline, metadata, base=None):
    """Apply the selected resolver to one Darwin browser process."""
    validate_dns_mode(mode)
    metadata["dns_mode"] = mode
    if mode == "system":
        yield base
        return
    path, binding = validate_native_dns_library(library)
    with NativeDnsBroker() as broker:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("DNS setup exceeded the call deadline")
        broker.begin_call(remaining)
        try:
            yield broker.environment(path, base=base)
        finally:
            metadata["dns_resolutions"] = list(broker.traces)
            metadata["native_dns_binding"] = binding
