import base64
import json
import logging
import pytest
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
    assert [r["event"] for r in rows]==["http_request_started","http_headers_received","http_response_received","collection_completed"]
    assert rows[1]["status"]==response.status==503
    assert rows[2]["body"]=='{"error":"actual failure"}'
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

def test_timeout_retains_product_stderr_even_without_returned_metadata(tmp_path):
    sink=CallDiagnostics(tmp_path / "deadline.jsonl", "timed-out")
    # Model a real process writing its persistent stderr before it is stopped.
    from pathlib import Path
    product_log=Path(str(sink.path)+".moli.stderr")
    with product_log.open("wb") as stream:
        stream.write(b"DEBUG native processing started\n");stream.flush()
    result=ProviderResult(False,"",error_type="timeout")
    sink.finish(result)
    raw=base64.b64decode(result.metadata["_diagnostic_log_b64"])
    assert b"DEBUG native processing started" in raw
    assert result.content==""


@pytest.mark.parametrize('outcome', ['content', 'unmatched', 'empty', 'product_error', 'network_error', 'startup_error', 'timeout'])
def test_runner_keeps_real_observations_for_every_outcome(tmp_path, outcome):
    import hashlib
    import threading
    from dataclasses import asdict
    from lexbench_webfetch.models import EvalCase
    from harness.web_fetch.providers.base import Provider
    from harness.web_fetch.runner import run_evaluation

    class ObservedFixture(Provider):
        def __init__(self):
            super().__init__('fixture', {'type': 'fixture', 'log_level': 'debug'})
            self.stop = threading.Event()
            self.calls = 0
        def begin_call(self):
            self.stop.clear()
            if outcome == 'startup_error':
                raise OSError('fixture startup unavailable')
        def fetch(self, case, timeout):
            self.calls += 1
            logging.getLogger('urllib3').debug('actual fixture log before ' + outcome)
            if outcome == 'network_error':
                raise ConnectionError('fixture connection refused')
            if outcome == 'timeout':
                self.stop.wait(2)
                return ProviderResult(True, 'late answer')
            if outcome == 'product_error':
                return ProviderResult(False, '', error_type='fixture_error', error_message='fixture product rejected request')
            return ProviderResult(True, 'Expected' if outcome == 'content' else 'Other content' if outcome == 'unmatched' else '')
        def cancel(self):
            self.stop.set()
        @property
        def cancellation_cleanup_seconds(self):
            return 0.5

    case = EvalCase('case', 'Case', 'https://example.invalid/', 'static', ['Expected'])
    dataset = {'tool': 'web_fetch', 'cases': [asdict(case)]}
    source = tmp_path / 'dataset.json';source.write_text(json.dumps(dataset))
    config = tmp_path / 'providers.json';config.write_text('{}')
    provider = ObservedFixture()
    records, _ = run_evaluation(output_dir=tmp_path/'evidence', dataset_meta=dataset,
        dataset_path=source, cases=[case], providers=[provider], provider_config_path=config,
        runs=1, timeout=0.1, delay=0)
    assert len(records) == 1
    record = records[0];meta = record['provider_metadata']
    raw = (tmp_path/'evidence'/meta['diagnostic_log_path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == meta['diagnostic_log_sha256']
    assert len(raw) == meta['diagnostic_log_bytes']
    assert not meta['diagnostic_log_truncated']
    rows = [json.loads(line) for line in raw.splitlines()]
    assert rows[0]['event'] == 'call_started'
    assert rows[-1]['event'] == 'collection_completed'
    assert len({r['call_id'] for r in rows}) == 1
    assert provider.calls == (0 if outcome == 'startup_error' else 1 if outcome == 'content' else 2)
    if outcome != 'startup_error':
        assert ('actual fixture log before '+outcome).encode() in raw
    if outcome in {'startup_error','product_error'}:
        assert ('fixture startup unavailable' if outcome=='startup_error' else 'fixture product rejected request').encode() in raw
    if outcome == 'network_error':
        assert b'fixture connection refused' in raw
    if outcome == 'timeout':
        assert record['error_type'] == 'timeout'
        assert (tmp_path/'evidence'/record['artifact_path']).read_bytes() == b''
        assert b'late answer' not in raw
    assert record['ok'] == (outcome in {'content', 'unmatched', 'empty'})
    assert record['metrics']['content_pass'] == (outcome == 'content')


@pytest.mark.parametrize("stage", ["connect_or_headers", "body"])
def test_http_failure_retains_observed_stage_and_closes_response(tmp_path, stage):
    sink = CallDiagnostics(tmp_path / "failed.jsonl", "failed")
    class Response:
        status = 200
        headers = {}
        closed = False
        def geturl(self): return "https://fixture.invalid/"
        def read(self, n): raise TimeoutError("body stalled")
        def close(self): self.closed = True
    response = Response()
    with diagnostic_context(sink), patch("urllib.request.build_opener") as opener:
        if stage == "body":
            opener.return_value.open.return_value = response
        else:
            opener.return_value.open.side_effect = TimeoutError("connection stalled")
        with pytest.raises(TimeoutError):
            request("https://fixture.invalid/", timeout=30)
    sink.finish(ProviderResult(False, "", error_type="timeout"))
    rows = [json.loads(line) for line in sink.path.read_text().splitlines()]
    failed = next(r for r in rows if r["event"] == "http_request_failed")
    assert failed["phase"] == stage
    assert any(r["event"] == "http_headers_received" for r in rows) == (stage == "body")
    assert response.closed == (stage == "body")
    assert not any(r["event"] == "http_response_received" for r in rows)
