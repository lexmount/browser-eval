import base64
import json
import logging
from unittest.mock import patch
from harness.web_fetch.diagnostics import CallDiagnostics, diagnostic_context
from harness.web_fetch.models import ProviderResult
from harness.web_fetch.providers.http_client import request

def test_http_diagnostics_observe_request_response_without_credentials(tmp_path):
    sink=CallDiagnostics(tmp_path / "trace.jsonl", "call-1")
    class Response:
        status=503
        headers={"content-type":"application/json", "x-request-id":"actual-id"}
        def read(self, n): return b'{"error":"actual failure"}'
        def geturl(self): return "https://example.invalid/"
        def close(self): pass
    with diagnostic_context(sink), patch("urllib.request.build_opener") as opener:
        opener.return_value.open.return_value=Response()
        response=request("https://example.invalid/",method="POST",headers={"Authorization":"Bearer secret"},json_body={"url":"https://target.invalid/"},timeout=45)
    result=ProviderResult(False,"",error_type="api_error")
    sink.finish(result)
    raw=base64.b64decode(result.metadata["_diagnostic_log_b64"])
    assert b"secret" not in raw
    rows=[json.loads(line) for line in raw.splitlines()]
    assert [r["event"] for r in rows]==["http_request_started","http_response_received","collection_completed"]
    assert rows[1]["status"]==response.status==503
    assert rows[1]["body"]=='{"error":"actual failure"}'
    assert rows[1]["headers"]["x-request-id"]=="actual-id"

def test_native_logging_is_observed_and_late_logs_do_not_mutate_evidence(tmp_path):
    sink=CallDiagnostics(tmp_path / "native.jsonl", "first")
    logger=logging.getLogger("urllib3")
    level=logger.level
    with diagnostic_context(sink): logger.debug("native diagnostic message")
    assert logger.level==level
    result=ProviderResult(True,"real answer")
    sink.finish(result)
    saved=sink.path.read_bytes()
    sink.emit("caller","late_response")
    assert sink.path.read_bytes()==saved
    assert b"native diagnostic message" in saved
    assert b"native_library" in saved
