"""Actual browser controls for native binary delivery, independent of websites."""
import base64
import os
import io
import zipfile
import threading
import unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from harness.web_fetch.models import EvalCase
from harness.web_fetch.providers.headless import output_mode_for_case
from harness.web_fetch.providers.headless_worker import fetch

BINARY = os.environ.get("CHROMIUM_BINARY")
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3e8AAAAASUVORK5CYII=")


class NativeFileRouteTests(unittest.TestCase):
    def test_direct_document_route_preserves_html_viewer_and_plain_text_tasks(self):
        for url in ('https://example.test/paper.pdf','https://example.test/paper.pdf#page=2','https://example.test/file.docx?download=1','https://example.test/pdf/1706.03762'):
            case=EvalCase('doc','Document',url,'document',[],track='document')
            for engine in ('moli','chromium','obscura','lightpanda','kitesurf'):
                self.assertEqual(output_mode_for_case(engine,'headless_markdown',case),'markdown')
        for url in ('https://example.test/plain.txt','https://example.test/documentation'):
            case=EvalCase('doc','Document',url,'document',[],track='document')
            self.assertEqual(output_mode_for_case('chromium','headless_markdown',case),'markdown')

    def test_file_contract_selects_browser_bytes_for_supported_engines(self):
        case = EvalCase("file", "File", "https://example.test/download", "image", [], task_kind="file")
        for engine in ("moli", "chromium", "obscura", "lightpanda", "kitesurf"):
            self.assertEqual(output_mode_for_case(engine, "headless_markdown", case), "file")


@unittest.skipUnless(BINARY, "set CHROMIUM_BINARY for actual browser controls")
class ChromiumFileDeliveryTests(unittest.TestCase):
    def test_inline_redirect_attachment_and_http_error_use_native_navigation(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                calls.append(self.path)
                if self.path == "/redirect":
                    self.send_response(302); self.send_header("Location", "/inline"); self.end_headers(); return
                body = b"Access denied" if self.path == "/denied" else PNG
                self.send_response(403 if self.path == "/denied" else 200)
                self.send_header("Content-Type", "text/plain" if self.path == "/denied" else "image/png")
                self.send_header("Content-Length", str(len(body)))
                if self.path == "/attachment": self.send_header("Content-Disposition", 'attachment; filename="file.png"')
                self.end_headers(); self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        origin = "http://127.0.0.1:" + str(server.server_port)
        try:
            for path in ("/inline", "/redirect", "/attachment", "/denied"):
                with self.subTest(path=path):
                    before = len(calls)
                    result = fetch({"engine": "chromium", "binary": BINARY,
                                    "url": origin + path, "timeout": 10,
                                    "output_mode": "file", "task_kind": "file", "track": "image"})
                    if path == "/denied":
                        self.assertFalse(result["ok"], result)
                        self.assertEqual(result["status_code"], 403)
                    else:
                        self.assertTrue(result["ok"], result)
                        self.assertEqual([base64.b64decode(value) for value in result["files_b64"]], [PNG])
                        self.assertEqual(result["final_url"], origin + ("/inline" if path == "/redirect" else path))
                    self.assertEqual(calls[before:].count(path), 1 + result.get("metadata", {}).get("native_attachment_fetch_count", 0), "only the declared native attachment interface may add a fetch")
        finally:
            server.shutdown(); server.server_close(); thread.join(1)


@unittest.skipUnless(BINARY, "set CHROMIUM_BINARY for actual browser controls")
class ChromiumDocumentDeliveryTests(unittest.TestCase):
    def test_native_document_bytes_use_the_shared_text_extractor(self):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        writer = PdfWriter(); page = writer.add_blank_page(width=200, height=100)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject(); stream.set_data(b"BT /F1 12 Tf 20 50 Td (Frozen PDF body) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        pdf = io.BytesIO(); writer.write(pdf)
        docx = io.BytesIO()
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Frozen DOCX body</w:t></w:r></w:p></w:body></w:document>')
        files = {"/fixture.pdf": (pdf.getvalue(), "application/pdf", "Frozen PDF body"),
                 "/fixture.docx": (docx.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "Frozen DOCX body")}
        files.update({"/inline.docx": files["/fixture.docx"],
                      "/wrong-mime.docx": (docx.getvalue(), "text/html", "Frozen DOCX body"),
                      "/not-a-document.pdf": (b"<html><body>Access challenge</body></html>", "text/html", None)})
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                calls.append(self.path)
                raw, mime, _ = files.get(self.path, (b"missing", "text/plain", ""))
                self.send_response(200 if self.path in files else 404)
                self.send_header("Content-Type", mime); self.send_header("Content-Length", str(len(raw)))
                if self.path == "/fixture.docx": self.send_header("Content-Disposition", 'attachment; filename="fixture.docx"')
                self.end_headers(); self.wfile.write(raw)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        origin = "http://127.0.0.1:" + str(server.server_port)
        try:
            for path, (raw, _, expected) in files.items():
                with self.subTest(path=path):
                    result = fetch({"engine": "chromium", "binary": BINARY,
                                    "url": origin + path, "timeout": 10,
                                    "output_mode": "document", "task_kind": "body", "track": "document"})
                    if expected is None:
                        self.assertFalse(result["ok"], result)
                        self.assertEqual(calls.count(path), 1, "invalid document bytes must not trigger another request")
                        continue
                    self.assertTrue(result["ok"], result)
                    self.assertIn(expected, result["content"])
                    self.assertEqual(base64.b64decode(result["metadata"]["_raw_stdout_b64"]), raw)
                    self.assertEqual(calls.count(path), 1 + result.get("metadata", {}).get("native_attachment_fetch_count", 0), "only the declared native attachment interface may add a fetch")
        finally:
            server.shutdown(); server.server_close(); thread.join(1)


@unittest.skipUnless(BINARY, "set CHROMIUM_BINARY for actual browser controls")
class ChromiumFileTrustTests(unittest.TestCase):
    def test_fixture_tls_exception_is_limited_to_declared_loopback_fixture(self):
        import ssl
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            certificate = Path(temporary) / "certificate.pem"
            key = Path(temporary) / "key.pem"
            subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048",
                            "-nodes", "-keyout", str(key), "-out", str(certificate),
                            "-days", "1", "-subj", "/CN=localhost"],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args): pass
                def do_GET(self):
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(PNG)))
                    self.end_headers(); self.wfile.write(PNG)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(certificate, key)
            server.socket = tls.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            request = {"engine": "chromium", "binary": BINARY,
                       "url": f"https://127.0.0.1:{server.server_port}/file.png",
                       "timeout": 10, "output_mode": "file", "task_kind": "file", "track": "image"}
            try:
                for declared in ({}, {"ca_cert": str(certificate)},
                                 {"network_policy": "loopback_only"}):
                    result = fetch(dict(request, **declared))
                    self.assertFalse(result["ok"], result)
                result = fetch(dict(request, ca_cert=str(certificate), network_policy="loopback_only"))
                self.assertTrue(result["ok"], result)
                self.assertEqual(base64.b64decode(result["files_b64"][0]), PNG)
            finally:
                server.shutdown(); server.server_close(); thread.join(1)


@unittest.skipUnless(os.environ.get("OBSCURA_BINARY"), "set OBSCURA_BINARY for actual browser controls")
class ObscuraDocumentDeliveryTests(unittest.TestCase):
    def test_original_document_bytes_use_shared_extraction(self):
        # The same independent PDF/DOCX fixture and byte-identity assertions
        # apply to each native route; only the supported invocation differs.
        from unittest.mock import patch
        original_fetch = fetch
        def obscura_fetch(request):
            return original_fetch({**request, "engine": "obscura", "allow_private_network": True})
        with patch(__name__ + ".BINARY", os.environ["OBSCURA_BINARY"]), patch(__name__ + ".fetch", obscura_fetch):
            ChromiumDocumentDeliveryTests().test_native_document_bytes_use_the_shared_text_extractor()


@unittest.skipUnless(os.environ.get("LIGHTPANDA_BINARY"), "set LIGHTPANDA_BINARY for actual browser controls")
class LightpandaDocumentDeliveryTests(unittest.TestCase):
    def test_original_document_bytes_use_shared_extraction(self):
        from unittest.mock import patch
        original_fetch = fetch
        def native_fetch(request):
            return original_fetch({**request, "engine": "lightpanda"})
        with patch(__name__ + ".BINARY", os.environ["LIGHTPANDA_BINARY"]), patch(__name__ + ".fetch", native_fetch):
            ChromiumDocumentDeliveryTests().test_native_document_bytes_use_the_shared_text_extractor()


@unittest.skipUnless(os.environ.get("MOLI_BINARY"), "set MOLI_BINARY for actual browser controls")
class MoliNativeDeliveryTests(unittest.TestCase):
    def test_inline_attachment_redirect_and_denial_deliver_only_native_bytes(self):
        self._run_shared(ChromiumFileDeliveryTests().test_inline_redirect_attachment_and_http_error_use_native_navigation)

    def test_document_body_matches_native_markdown_without_external_parsing(self):
        import hashlib, subprocess
        from unittest.mock import patch
        from pypdf import PdfWriter
        from harness.web_fetch.providers.moli import MoliWebFetchProvider
        pdf = io.BytesIO()
        writer = PdfWriter(); writer.add_blank_page(width=200, height=100); writer.write(pdf)
        docx = io.BytesIO()
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Document body</w:t></w:r></w:p></w:body></w:document>')
        files = {"/body.html": (b"<html><body><main>Native document body</main></body></html>", "text/html"),
                 "/plain.txt": (b"Native document body", "text/plain"),
                 "/fixture.pdf": (pdf.getvalue(), "application/pdf"),
                 "/fixture.docx": (docx.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                raw, mime = files[self.path]
                self.send_response(200); self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        binary = os.environ["MOLI_BINARY"]
        provider = MoliWebFetchProvider("moli", {"binary": binary, "block_private_networks": False,
            "expected_reported_version": subprocess.check_output([binary, "--version"], text=True).strip(),
            "expected_binary_sha256": hashlib.sha256(Path(binary).read_bytes()).hexdigest()})
        try:
            for path in files:
                with self.subTest(path=path):
                    url = "http://127.0.0.1:" + str(server.server_port) + path
                    native = subprocess.run([binary, "fetch", "--dump", "markdown", "--wait-until", "done",
                        "--timeout", "10000", url], capture_output=True, timeout=12)
                    with patch("harness.web_fetch.providers.headless_worker.extract_document_text",
                               side_effect=AssertionError("external document conversion is forbidden")) as external:
                        result = provider.fetch(EvalCase("native-document", "Document", url, "document", [], track="document"), 10)
                    external.assert_not_called()
                    if path == "/body.html":
                        self.assertEqual(result.metadata["route"], "document_page_runtime")
                    else:
                        self.assertEqual(result.metadata["route"], "ordinary_markdown")
                        self.assertEqual(result.metadata["output"], "markdown")
                    if native.returncode == 0:
                        self.assertTrue(result.ok, result.error_message)
                        self.assertEqual(result.content.strip(), native.stdout.decode("utf-8", errors="replace").strip())
                    else:
                        self.assertFalse(result.ok, result)
                        self.assertEqual(result.error_type, "tool_error")
                    if path in {"/body.html", "/plain.txt"}:
                        self.assertTrue(result.ok, result)
                        self.assertIn("Native document body", result.content)
        finally:
            server.shutdown(); server.server_close(); thread.join(1)

    def test_original_file_byte_budget_is_preserved(self):
        import hashlib, subprocess
        from harness.web_fetch.providers.moli import MoliWebFetchProvider
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                calls.append(self.path)
                self.send_response(200);self.send_header('Content-Type','image/png')
                self.send_header('Content-Length',str(len(PNG)));self.end_headers();self.wfile.write(PNG)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            binary=os.environ['MOLI_BINARY']
            provider=MoliWebFetchProvider('moli',{'binary':binary,'block_private_networks':False,'max_bytes':50,
                'expected_reported_version':subprocess.check_output([binary,'--version'],text=True).strip(),
                'expected_binary_sha256':hashlib.sha256(Path(binary).read_bytes()).hexdigest()})
            case=EvalCase('original-byte-budget','Fixture','http://127.0.0.1:'+str(server.server_port)+'/file.png',
                          'image',[],track='image',task_kind='file')
            result=provider.fetch(case,5)
            self.assertFalse(result.ok,result)
            self.assertEqual(result.error_type,'output_too_large')
            self.assertEqual(result.metadata['original_file_bytes'],len(PNG))
            self.assertEqual(calls,['/file.png'])
        finally:
            server.shutdown();server.server_close();thread.join(1)

    def _run_shared(self, control):
        from unittest.mock import patch
        import hashlib, subprocess
        from harness.web_fetch.providers.moli import MoliWebFetchProvider
        binary = os.environ["MOLI_BINARY"]
        config = {"binary":binary,"block_private_networks":False,
                  "expected_reported_version":subprocess.check_output([binary,"--version"],text=True).strip(),
                  "expected_binary_sha256":hashlib.sha256(Path(binary).read_bytes()).hexdigest()}
        def native_fetch(request):
            provider=MoliWebFetchProvider('moli',config)
            case=EvalCase('independent-native-delivery','Fixture',request['url'],'fixture',[],
                          track=request['track'],task_kind=request['task_kind'])
            result=provider.fetch(case,request['timeout'])
            return dict(ok=result.ok,content=result.content,status_code=result.status_code,final_url=result.final_url,
                        error_type=result.error_type,error_message=result.error_message,metadata=result.metadata,
                        files_b64=[base64.b64encode(file['data']).decode() for file in result.files])
        with patch(__name__ + ".BINARY", binary), patch(__name__ + ".fetch", native_fetch):
            control()
