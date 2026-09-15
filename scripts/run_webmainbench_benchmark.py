"""Run one browser method against the frozen WebMainBench-545 HTML inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.config import load_provider_config, load_runtime_env
from harness.web_fetch.models import EvalCase, ProviderResult
from harness.web_fetch.providers import build_provider
from harness.web_fetch.providers.cloudflare import CloudflareMarkdownProvider
from harness.web_fetch.providers.headless import LOOPBACK_SANDBOX_PROFILE
from harness.web_fetch.runner import classify_exception


DATASET_SHA256 = "0efaa4b49a45e320a27fe6e5a0b6aad5b57259fc3321ac3448519cacc74c537e"
SCORER_COMMIT = "9d991bdc00c57b57521499494d96be85c31317ba"
PROVIDERS = ("kitesurf", "lightpanda", "obscura", "moli", "chromium")
EXPECTED_VERSIONS = {"obscura": "0.2.2", "moli": "1.1.5"}
CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_record(provider, cloudflare, record: dict, target_url: str, timeout: float) -> ProviderResult:
    """Convert adapter exceptions into one inspectable physical-call result."""
    try:
        if cloudflare is not None:
            return cloudflare.fetch_html(case_for(record, record["url"]), record["html"], timeout)
        return provider.fetch(case_for(record, target_url), timeout)
    except Exception as error:
        error_type, error_message = classify_exception(error)
        return ProviderResult(
            ok=False,
            content="",
            error_type=error_type,
            error_message=error_message,
            metadata={"exception_class": type(error).__name__},
        )


def load_source(path: Path, selected_ids: set[str] | None = None) -> list[dict]:
    payload = path.read_bytes()
    actual = sha256_bytes(payload)
    if actual != DATASET_SHA256:
        raise ValueError(f"Unexpected WebMainBench-545 SHA-256: {actual}")
    records = [json.loads(line) for line in payload.splitlines() if line.strip()]
    if len(records) != 545:
        raise ValueError(f"Expected 545 records, found {len(records)}")
    if len({record["track_id"] for record in records}) != 545:
        raise ValueError("WebMainBench track_id values are not unique")
    if selected_ids is not None:
        records = [record for record in records if record["track_id"] in selected_ids]
        missing = selected_ids - {record["track_id"] for record in records}
        if missing:
            raise ValueError("Unknown track IDs: " + ", ".join(sorted(missing)))
    return records


def load_selected_ids(path: Path) -> tuple[set[str], dict]:
    """Load either the frozen JSON sample manifest or a plain one-ID-per-line file."""
    payload = path.read_bytes()
    binding = {"path": str(path), "sha256": sha256_bytes(payload)}
    if path.suffix.lower() != ".json":
        selected = {line.strip() for line in payload.decode("utf-8").splitlines() if line.strip()}
        return selected, {**binding, "format": "track_id_lines", "case_count": len(selected)}
    manifest = json.loads(payload)
    parent = manifest.get("parent", {})
    cases = manifest.get("cases", [])
    selected = {row.get("track_id") for row in cases}
    if (manifest.get("schema_version") != 1
            or parent.get("sha256") != DATASET_SHA256
            or len(cases) != manifest.get("sampling", {}).get("size")
            or None in selected or len(selected) != len(cases)):
        raise ValueError("Invalid WebMainBench sample manifest")
    return selected, {
        **binding,
        "format": "webmainbench_sample_manifest_v1",
        "dataset_id": manifest.get("dataset_id"),
        "selection_seed": manifest.get("sampling", {}).get("seed"),
        "case_count": len(selected),
    }


def case_for(record: dict, url: str) -> EvalCase:
    return EvalCase(
        id=record["track_id"],
        name=record["track_id"],
        url=url,
        category="webmainbench",
        expected_any=[],
        track="static_html",
        source_benchmark={"name": "WebMainBench-545", "original_url": record["url"]},
    )


@contextmanager
def fixture_server(records: list[dict]):
    bodies = {
        "/case/" + record["track_id"]: record["html"].encode("utf-8")
        for record in records
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
            body = bodies.get(self.path)
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    openssl = shutil.which("openssl")
    if not openssl:
        raise ValueError("openssl is required for the HTTPS frozen-input fixture")
    with tempfile.TemporaryDirectory(prefix="webmainbench-tls-") as directory:
        tls = Path(directory)
        ca_key, ca_cert = tls / "ca-key.pem", tls / "ca.pem"
        leaf_key, leaf_csr, leaf_cert = tls / "leaf-key.pem", tls / "leaf.csr", tls / "leaf.pem"
        commands = [
            [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(ca_key),
             "-out", str(ca_cert), "-days", "1", "-subj", "/CN=WebMainBench-Local-CA",
             "-addext", "basicConstraints=critical,CA:TRUE"],
            [openssl, "req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(leaf_key),
             "-out", str(leaf_csr), "-subj", "/CN=localhost",
             "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
            [openssl, "x509", "-req", "-in", str(leaf_csr), "-CA", str(ca_cert),
             "-CAkey", str(ca_key), "-CAcreateserial", "-out", str(leaf_cert),
             "-days", "1", "-copy_extensions", "copy"],
        ]
        for command in commands:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
            if completed.returncode:
                raise ValueError("Failed to create HTTPS fixture certificate: " + completed.stderr[-1000:])
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(leaf_cert, leaf_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"https://localhost:{server.server_port}", ca_cert
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def provider_version(identity: dict) -> str | None:
    reported = identity.get("reported_version") or ""
    for token in reported.split():
        if token[:1].isdigit() and "." in token:
            return token
    return None


def check_identity(name: str, identity: dict) -> None:
    expected = EXPECTED_VERSIONS.get(name)
    if expected and provider_version(identity) != expected:
        raise ValueError(
            f"{name}: expected version {expected}, got {identity.get('reported_version') or 'unavailable'}"
        )


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def public_metadata(value: dict) -> dict:
    """Keep diagnostics while excluding private/duplicated raw payloads."""
    return {key: item for key, item in value.items() if not key.startswith("_")}


def verify_loopback_sandbox(fixture_base: str, ca_cert: Path, record: dict) -> dict:
    """Prove that the measured child can read the fixture but cannot connect externally."""
    parsed = urlsplit(fixture_base)
    path = "/case/" + record["track_id"]
    probe = (
        "import errno,hashlib,json,socket,ssl,sys;"
        "host,port,path,expected,ca=sys.argv[1],int(sys.argv[2]),sys.argv[3],sys.argv[4],sys.argv[5];"
        "s=ssl.create_default_context(cafile=ca).wrap_socket(socket.create_connection((host,port),2),server_hostname=host);"
        "s.sendall(('GET '+path+' HTTP/1.0\\r\\nHost: '+host+'\\r\\n\\r\\n').encode());"
        "raw=b'';"
        "\nwhile True:\n chunk=s.recv(65536)\n if not chunk: break\n raw+=chunk\n"
        "s.close();body=raw.split(b'\\r\\n\\r\\n',1)[1];"
        "result={'loopback_body_sha256':hashlib.sha256(body).hexdigest()};"
        "\ntry:\n socket.create_connection(('1.1.1.1',443),1);result['external_connect_errno']=0\n"
        "except OSError as exc:\n result['external_connect_errno']=exc.errno\n"
        "print(json.dumps(result));"
        "sys.exit(0 if result['loopback_body_sha256']==expected and result['external_connect_errno'] in (errno.EPERM,errno.EACCES) else 9)"
    )
    completed = subprocess.run(
        ["/usr/bin/sandbox-exec", "-p", LOOPBACK_SANDBOX_PROFILE, sys.executable, "-c", probe,
         parsed.hostname or "127.0.0.1", str(parsed.port), path,
         sha256_bytes(record["html"].encode("utf-8")), str(ca_cert)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if completed.returncode != 0:
        raise ValueError("Loopback-only network isolation probe failed: " + completed.stderr.strip())
    result = json.loads(completed.stdout)
    return {
        "mode": "macos_sandbox_exec_loopback_only",
        "profile_sha256": sha256_bytes(LOOPBACK_SANDBOX_PROFILE.encode()),
        **result,
    }


def run(args: argparse.Namespace) -> None:
    if args.output.exists():
        raise ValueError("Output exists; measured evidence is immutable")
    selected = None
    selection_binding = None
    if args.case_ids_file:
        selected, selection_binding = load_selected_ids(args.case_ids_file)
    records = load_source(args.dataset, selected)
    if args.limit:
        records = records[: args.limit]
    if not records:
        raise ValueError("No cases selected")

    load_runtime_env(args.env_file)
    config = load_provider_config(args.config)
    provider_config = dict(config[args.provider])
    if args.provider != "kitesurf":
        provider_config["network_policy"] = "loopback_only"
    if args.provider == "obscura":
        provider_config["allow_private_network"] = True
    provider = build_provider(args.provider, provider_config)
    if missing := provider.validate():
        raise ValueError(args.provider + ": " + ", ".join(missing))
    before = provider.implementation_identity()
    check_identity(args.provider, before)

    args.output.mkdir(parents=True)
    artifact_root = args.output / "artifacts" / args.provider
    artifact_root.mkdir(parents=True)
    physical_path = args.output / "physical-attempts.jsonl"
    result_path = args.output / "results.jsonl"
    source_manifest = {
        "dataset_id": "webmainbench-545",
        "source_sha256": DATASET_SHA256,
        "official_scorer_commit": SCORER_COMMIT,
        "case_count": len(records),
        "case_ids": [record["track_id"] for record in records],
        "selection_manifest": selection_binding,
        "input_contract": {
            "body": "official html UTF-8 bytes without mutation",
            "subresources": "blocked",
            "javascript": "inline page-authored JavaScript may run; external script and data access is blocked",
            "network_policy": "local engines run under a loopback-only OS sandbox; Kitesurf rejects subresource types",
            "csp_response_header": CSP,
        },
    }
    write_json(args.output / "dataset.json", source_manifest)

    started_at = iso_now()
    run_id = "wmb-" + uuid.uuid4().hex[:20]
    cloudflare = None
    if args.provider == "kitesurf":
        cloudflare = CloudflareMarkdownProvider(args.provider, {
            "account_id": provider_config["account_id"],
            "api_token": provider_config["api_token"],
            "browser": "kitesurf",
            "max_bytes": provider_config.get("max_bytes", 20_000_000),
        })

    network_isolation_probe = None
    fixture_ca_artifact = None
    with fixture_server(records) as (fixture_base, ca_cert), physical_path.open("w", encoding="utf-8") as physical, result_path.open("w", encoding="utf-8") as results:
        if args.provider == "kitesurf":
            network_isolation_probe = {"mode": "cloudflare_reject_external_resource_types"}
        else:
            provider.config["ca_cert"] = str(ca_cert)
            shutil.copy2(ca_cert, args.output / "fixture-ca.pem")
            fixture_ca_artifact = "fixture-ca.pem"
            network_isolation_probe = verify_loopback_sandbox(fixture_base, ca_cert, records[0])
            network_isolation_probe["fixture_ca_sha256"] = sha256_bytes(ca_cert.read_bytes())
        for index, record in enumerate(records, 1):
            body = record["html"].encode("utf-8")
            reference = record["groundtruth_content"].encode("utf-8")
            attempts = []
            total_elapsed = 0.0
            for retry in range(args.max_failure_retries + 1):
                target_url = f"{fixture_base}/case/{record['track_id']}"
                call_started = iso_now()
                clock = time.monotonic()
                response = fetch_record(provider, cloudflare, record, target_url, args.timeout)
                elapsed_ms = round((time.monotonic() - clock) * 1000, 2)
                total_elapsed += elapsed_ms
                attempt = {
                    "attempt_id": f"{args.provider}__{record['track_id']}__p{retry + 1:02d}",
                    "run_id": run_id,
                    "provider": args.provider,
                    "track_id": record["track_id"],
                    "original_url": record["url"],
                    "physical_attempt": retry + 1,
                    "started_at": call_started,
                    "completed_at": iso_now(),
                    "elapsed_ms": elapsed_ms,
                    "ok": response.ok,
                    "status_code": response.status_code,
                    "error_type": response.error_type,
                    "error_message": response.error_message,
                    "provider_metadata": public_metadata(response.metadata),
                    "input_html_sha256": sha256_bytes(body),
                    "reference_sha256": sha256_bytes(reference),
                    "output_sha256": sha256_bytes(response.content.encode("utf-8")),
                    "output_chars": len(response.content),
                }
                physical.write(json.dumps(attempt, ensure_ascii=False) + "\n")
                physical.flush()
                attempts.append((attempt, response))
                if response.ok:
                    break
            authoritative, final_response = attempts[-1]
            artifact = artifact_root / f"{record['track_id']}.md"
            artifact.write_text(final_response.content, encoding="utf-8")
            logical = {
                **{key: value for key, value in authoritative.items() if key not in {"attempt_id", "physical_attempt"}},
                "attempt_id": f"{args.provider}__{record['track_id']}__r001",
                "artifact_path": str(artifact.relative_to(args.output)),
                "physical_attempt_count": len(attempts),
                "total_elapsed_ms": round(total_elapsed, 2),
                "retry_selection": "final_physical_attempt_is_authoritative",
                "meta": record["meta"],
            }
            results.write(json.dumps(logical, ensure_ascii=False) + "\n")
            results.flush()
            print(f"[{index}/{len(records)}] {args.provider} {record['track_id']} {'ok' if final_response.ok else final_response.error_type}", flush=True)

    after = provider.implementation_identity(refresh=True)
    if before != after:
        raise ValueError("Provider implementation identity changed during measurement")
    execution = {
        "schema_version": 1,
        "run_id": run_id,
        "provider": args.provider,
        "started_at": started_at,
        "completed_at": iso_now(),
        "case_count": len(records),
        "timeout_seconds": args.timeout,
        "max_failure_retries": args.max_failure_retries,
        "implementation_identity": before,
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "source_dataset_sha256": DATASET_SHA256,
        "selection_manifest": selection_binding,
        "runner_sha256": sha256_bytes(Path(__file__).read_bytes()),
        "network_isolation_probe": network_isolation_probe,
        "fixture_ca_artifact": fixture_ca_artifact,
    }
    write_json(args.output / "execution.json", execution)


def main() -> None:
    data_home = Path(os.environ.get("BROWSER_EVAL_DATA_HOME", Path.home() / ".browser-eval"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=PROVIDERS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=data_home / "public-benchmarks/webmainbench-545/WebMainBench_545.jsonl")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/providers.headless.json")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--case-ids-file", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--max-failure-retries", type=int, choices=(0, 1), default=1)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
