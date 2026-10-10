"""Public capture scenarios: complete scope, immutable outputs and scoped DNS."""

import hashlib
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from harness.web_fetch.source_capture import collect, main, resolver_override


@pytest.fixture
def source_site():
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            status = (
                404
                if self.path == "/missing"
                else 503
                if self.path == "/retry" and hits.count("/retry") == 1
                else 200
            )
            body = b"<html><title>Source</title><main>Independent source body</main><nav>Menu</nav></html>"
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", hits
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def dataset_file(tmp_path, base):
    path = tmp_path / "dataset.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": name,
                        "name": name,
                        "url": base + "/" + name,
                        "required_facts": [
                            {"id": "body", "alternatives": ["Independent source body"]}
                        ],
                    }
                    for name in ("page", "retry", "missing")
                ]
            }
        )
    )
    return path


def test_capture_commits_complete_independent_http_scope_and_preserves_failures(
    tmp_path, source_site
):
    base, hits = source_site
    dataset = dataset_file(tmp_path, base)
    before = dataset.read_bytes()
    output = tmp_path / "captures"
    results = collect(dataset, output, workers=2)
    assert [row["case_id"] for row in results] == ["page", "retry", "missing"]
    assert [row["state"] for row in results] == ["captured", "captured", "needs_review"]
    assert [row["status"] for row in results[1]["attempts"]] == [503, 200]
    assert results[2]["status"] == 404
    assert hits.count("/retry") == 2
    assert dataset.read_bytes() == before
    assert json.loads((output / "audit.json").read_text()) == results
    for result in results:
        directory = output / result["case_id"]
        assert (
            hashlib.sha256((directory / "source.html").read_bytes()).hexdigest()
            == result["sha256"]
        )
        assert (directory / "reference.txt").read_text() == "Independent source body"
        assert (
            hashlib.sha256((directory / "reference.txt").read_bytes()).hexdigest()
            == result["reference_sha256"]
        )
        assert json.loads((directory / "evidence.json").read_text()) == result
        assert result["independent"] is True
        assert result["scope_identity"]["case_id"] == result["case_id"]
        assert result["scope_identity"]["url"] == result["url"]
        assert result["completion"]["elapsed_ms"] > 0
        assert result["completion"]["timeout_ms"] == 120000
        assert result["completion"]["satisfied"] is (result["status"] == 200)
    assert not list(tmp_path.glob(".source-capture-*"))
    saved = {path: path.read_bytes() for path in output.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="new directory"):
        collect(dataset, output)
    assert all(path.read_bytes() == value for path, value in saved.items())


def test_cli_selects_exact_scope_and_records_declared_dns(tmp_path, source_site):
    base, hits = source_site
    dataset = dataset_file(tmp_path, base)
    output = tmp_path / "selected"
    original = socket.getaddrinfo
    main(
        [
            str(dataset),
            str(output),
            "--only",
            "page",
            "--workers",
            "1",
            "--resolve",
            "127.0.0.1=127.0.0.1",
        ]
    )
    assert hits == ["/page"]
    assert socket.getaddrinfo is original
    audit = json.loads((output / "audit.json").read_text())
    assert [row["case_id"] for row in audit] == ["page"]
    assert audit[0]["dns_override"]["ip"] == "127.0.0.1"
    assert not (output / "retry").exists()


@pytest.mark.parametrize(
    "selection,workers", [(["unknown"], 1), ([], 1), (["page", "page"], 1), (None, 0)]
)
def test_invalid_scope_is_rejected_before_capture(
    tmp_path, selection, workers, monkeypatch
):
    dataset = dataset_file(tmp_path, "https://example.invalid")

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid scope must not make any network request")

    monkeypatch.setattr("requests.get", forbidden)
    output = tmp_path / "captures"
    with pytest.raises(ValueError):
        collect(dataset, output, only=selection, workers=workers)
    assert not output.exists()


@pytest.mark.parametrize(
    "resolve",
    ["missing-ip", "=127.0.0.1", "host=invalid", "host=127.0.0.1,host=127.0.0.2"],
)
def test_cli_rejects_malformed_dns_without_creating_output(tmp_path, resolve):
    dataset = dataset_file(tmp_path, "https://example.invalid")
    output = tmp_path / "captures"
    args = [str(dataset), str(output)]
    for value in resolve.split(","):
        args.extend(["--resolve", value])
    with pytest.raises(SystemExit) as failure:
        main(args)
    assert failure.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize("fail", [False, True])
def test_dns_override_restores_resolver_after_success_or_error(monkeypatch, fail):
    calls = []

    def resolve(host, port, *args, **kwargs):
        calls.append((host, port))
        return []

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    try:
        with resolver_override({"source.test": "127.0.0.2"}):
            socket.getaddrinfo("source.test", 80)
            socket.getaddrinfo("other.test", 443)
            if fail:
                raise RuntimeError("worker interrupted")
    except RuntimeError:
        assert fail
    assert calls == [("127.0.0.2", 80), ("other.test", 443)]
    assert socket.getaddrinfo is resolve


def test_interrupted_capture_leaves_no_partial_package_or_dns_override(
    tmp_path, monkeypatch
):
    dataset = dataset_file(tmp_path, "https://example.invalid")
    output = tmp_path / "captures"
    original = socket.getaddrinfo

    def interrupted(*args, **kwargs):
        raise RuntimeError("worker interrupted")

    monkeypatch.setattr("harness.web_fetch.source_capture.capture", interrupted)
    with pytest.raises(RuntimeError, match="worker interrupted"):
        collect(dataset, output, resolved_hosts={"example.invalid": "127.0.0.1"})
    assert socket.getaddrinfo is original
    assert not output.exists()
    assert not list(tmp_path.glob(".source-capture-*"))


def test_shared_docx_extraction_retains_source_whitespace_and_nested_parts():
    import io
    import zipfile

    from harness.web_fetch.document_text import extract_document_text

    archive = io.BytesIO()
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    with zipfile.ZipFile(archive, "w") as document:
        document.writestr(
            "word/document.xml",
            f'''<w:document xmlns:w="{namespace}"><w:body>
        <w:p><w:r><w:t xml:space="preserve">  Body  </w:t></w:r></w:p>
        <w:p><w:r><w:pict><w:txbxContent><w:p><w:r><w:t>Nested box</w:t></w:r></w:p></w:txbxContent></w:pict></w:r></w:p>
        </w:body></w:document>''',
        )
        document.writestr(
            "word/footnotes.xml",
            f'<w:footnotes xmlns:w="{namespace}"><w:footnote><w:p><w:r><w:t>Note</w:t></w:r></w:p></w:footnote></w:footnotes>',
        )
        document.writestr(
            "word/header1.xml",
            f'<w:hdr xmlns:w="{namespace}"><w:p><w:r><w:t>Header</w:t></w:r></w:p></w:hdr>',
        )
    raw = archive.getvalue()
    source, identity = extract_document_text(raw, preserve_whitespace=True)
    normalized, _ = extract_document_text(raw)
    assert identity == "docx_xml"
    assert source == "  Body  \nNested box\nNote\nHeader"
    assert normalized == "Body\nNested box\nNote\nHeader"
    assert source.count("Nested box") == 1


def test_real_http_capture_can_bind_a_postcheck_without_inventing_receipts(tmp_path, source_site):
    from harness.web_fetch.reference_lifecycle import build_observations

    base, _ = source_site
    dataset = dataset_file(tmp_path, base)
    captures = tmp_path / "captures"
    capture = collect(dataset, captures, only=["page"], workers=1)[0]
    run = tmp_path / "run"
    run.mkdir()
    binding = {"scope_identity": capture["scope_identity"],
               "source_verification": {"observation_route": "http"}}
    (run / "manifest.json").write_text(json.dumps({"reference_checks": {"cases": {"page": binding}}}))
    observed = build_observations(run, captures)[0]
    assert observed["completion"] == capture["completion"]
    assert observed["checked_at"] == capture["checked_at"]
    assert observed["content_text"] == "Independent source body"
    assert observed["content_sha256"] == capture["reference_sha256"]


def test_capture_over_completion_bound_retains_failure_receipt(tmp_path, source_site, monkeypatch):
    from harness.web_fetch import source_capture

    base, _ = source_site
    dataset = dataset_file(tmp_path, base)
    ticks = iter([10.0, 131.0])
    monkeypatch.setattr(source_capture, "monotonic", lambda: next(ticks))
    result = collect(dataset, tmp_path / "captures", only=["page"], workers=1)[0]
    assert result["status"] == 200
    assert result["completion"] == {
        "satisfied": False, "elapsed_ms": 121000, "timeout_ms": 120000,
    }
