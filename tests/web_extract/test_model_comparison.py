"""Model diagnostics retain real streams, failure evidence and completed work."""

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from harness.record_io import bytes_sha256, canonical_json_sha256
from harness.web_extract.model_comparison import collect, request_body, stream_call
from harness.web_extract.pipeline import read_json, save_json


@contextmanager
def server(events, *, delay=0, status=200, identity=None, barrier=None, interrupt=(), drip=None):
    requests = []
    identity = identity if identity is not None else {"digest": "a" * 64}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def reply_json(self, value):
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            pause = (drip or {}).get(self.path, 0)
            try:
                chunks = (body[i:i+1] for i in range(len(body))) if pause else [body]
                for chunk in chunks:
                    if pause:
                        time.sleep(pause)
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            value = (
                {"version": "fixture"}
                if self.path == "/api/version"
                else {"models": [{"name": "fixture", "digest": identity["digest"]}]}
            )
            self.reply_json(value)

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if self.path == "/api/generate":
                self.reply_json({"done": True, "response": ""})
                return
            if barrier is not None:
                barrier.wait(timeout=5)
            self.send_response(status)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for event in events:
                    time.sleep(delay)
                    raw = (event if isinstance(event, str) else json.dumps(event)) + "\n"
                    body = raw.encode()
                    self.wfile.write(f"{len(body):x}\r\n".encode() + body + b"\r\n")
                    self.wfile.flush()
                if len(requests) not in interrupt:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                self.close_connection = True

    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_port}", requests
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


def test_stream_distinguishes_reasoning_answer_and_completion():
    events = [
        {"message": {"thinking": "consider"}},
        {"message": {"content": '{"name":"原文"}'}},
        {"done": True},
    ]
    with server(events, delay=0.025) as (url, calls):
        result = stream_call(({"endpoint": url, "protocol": "ollama"}, {}), timeout=2)
    assert result["success"] and result["data"] == {"name": "原文"}
    assert 0 < result["first_any_output_seconds"] < result["first_answer_seconds"]
    assert result["first_answer_seconds"] < result["complete_json_seconds"]
    assert len(calls) == 1 and len(result["events"]) == 3


def test_openai_sse_preserves_usage_and_ignores_empty_chunks():
    events = [
        'data: {"choices":[{"delta":{}}]}',
        'data: {"choices":[{"delta":{"reasoning_content":"reason"}}]}',
        'data: {"choices":[{"delta":{"content":"{\\"name\\":\\"value\\"}"}}]}',
        'data: {"choices":[],"usage":{"total_tokens":12}}',
        "data: [DONE]",
    ]
    with server(events) as (url, _):
        result = stream_call(({"endpoint": url, "protocol": "openai"}, {}), timeout=2)
    assert result["success"] and result["data"] == {"name": "value"}
    assert result["events"][-1]["event"]["usage"]["total_tokens"] == 12


@pytest.mark.parametrize(
    "events,error",
    [
        ([{"message": {"content": '{"name":"value"}'}}], "completion marker"),
        ([{"message": {"content": "[]"}}, {"done": True}], "JSON object"),
        ([{"message": {"content": "broken"}}, {"done": True}], "JSONDecodeError"),
        ([{"message": {"content": '{"name":NaN}'}}, {"done": True}], "ValueError"),
        ([{"message": {"content": '{"name":1e400}'}}, {"done": True}], "ValueError"),
        ([{"message": {"content": '{"name":"\\ud800"}'}}, {"done": True}], "UnicodeEncodeError"),
        ([{"error": "unsupported context"}], "unsupported context"),
    ],
)
def test_broken_streams_remain_failures_without_retries(events, error):
    with server(events) as (url, calls):
        result = stream_call(({"endpoint": url, "protocol": "ollama"}, {}), timeout=2)
    assert not result["success"] and error in result["error"] and len(calls) == 1
    assert result["elapsed_seconds"] > 0 and result["complete_json_seconds"] is None


def test_wall_deadline_interrupts_a_continuing_stream():
    with server([{"message": {"thinking": "still thinking"}}] * 50, delay=0.03) as (url, calls):
        started = time.monotonic()
        result = stream_call(({"endpoint": url, "protocol": "ollama"}, {}), timeout=0.15)
        elapsed = time.monotonic() - started
    assert not result["success"] and result["error"]
    assert elapsed < 0.5 and len(calls) == 1


def plan(url):
    return {
        "system_prompt": "Extract fields from the supplied page.",
        "timeout_seconds": 2,
        "wall_time_budget_seconds": 30,
        "execution_policy": {
            "purpose": "quality",
            "max_in_flight": 1,
            "serial_reason": "One test HTTP service",
            "backend": "process",
            "resources": {"host": {"capacity": 1, "reason": "Shared model host"}},
        },
        "models": [
            {
                "id": "local",
                "model": "fixture",
                "endpoint": url + "/api/chat",
                "protocol": "ollama",
                "runtime": {"base": url, "version": "fixture", "digest": "a" * 64},
                "resources": ["host"],
                "parameters": {"think": False},
            }
        ],
        "cases": [
            {
                "id": "page",
                "text": "Visible source",
                "schema": {"type": "object"},
                "text_sha256": bytes_sha256(b"Visible source"),
                "reference": "never send me",
            }
        ],
        "calls": [
            {"model_id": "local", "case_id": "page", "phase": phase}
            for phase in ("preflight", "comparison")
        ],
    }


def test_resume_reuses_failures_and_rejects_changed_evidence(tmp_path):
    events = [{"message": {"content": '{"name":"value"}'}}, {"done": True}]
    with server(events) as (url, calls):
        value = plan(url)
        path, output = tmp_path / "plan.json", tmp_path / "run"
        save_json(path, value)
        with pytest.raises(ValueError, match="preflight"):
            collect(path, output, phase="comparison")
        assert not calls
        assert collect(path, output, phase="preflight")["new_calls"] == 1
        events[:] = [{"message": {"content": "broken"}}, {"done": True}]
        assert collect(path, output, phase="comparison")["new_calls"] == 1
        failed = next(
            row
            for row in map(read_json, (output / "calls").glob("*.json"))
            if row["binding"]["call"]["phase"] == "comparison"
        )
        assert failed["response"]["success"] is False
        assert collect(path, output, phase="comparison")["new_calls"] == 0
        assert len(calls) == 2
        target = next((output / "calls").glob("*.json"))
        row = read_json(target)
        row["response"]["data"] = {"name": "changed"}
        save_json(target, row)
        with pytest.raises(ValueError, match="frozen request|preflight"):
            collect(path, output, phase="comparison")
        assert len(calls) == 2


def test_pending_call_and_changed_inputs_stop_before_dispatch(tmp_path):
    with server([]) as (url, calls):
        value = plan(url)
        path, output = tmp_path / "plan.json", tmp_path / "run"
        save_json(path, value)
        call = value["calls"][0]
        save_json(output / "calls" / (canonical_json_sha256(call) + ".pending"), {})
        with pytest.raises(ValueError, match="Unresolved physical call"):
            collect(path, output, phase="preflight")
        value["cases"][0]["text"] = "different source"
        save_json(path, value)
        with pytest.raises(ValueError, match="input text changed"):
            collect(path, output, phase="preflight")
        assert not calls


def test_reference_changes_cannot_change_the_model_prompt():
    value = plan("http://localhost")
    case = value["cases"][0]
    before = request_body(value, value["models"][0], case)
    case["reference"] = "different expected answer"
    assert request_body(value, value["models"][0], case) == before
    assert "never send me" not in json.dumps(before)


def test_malformed_json_is_a_quality_result_not_a_readiness_exclusion(tmp_path):
    with server([{"message": {"content": "not JSON"}}, {"done": True}]) as (url, calls):
        path, output = tmp_path / "plan.json", tmp_path / "run"
        save_json(path, plan(url))
        collect(path, output, phase="preflight")
        collect(path, output, phase="comparison")
        assert len(calls) == 2
        rows = [read_json(p)["response"] for p in (output / "calls").glob("*.json")]
        assert all(r["transport_complete"] and not r["success"] for r in rows)


def test_model_weight_change_stops_before_another_generation(tmp_path):
    identity = {"digest": "a" * 64}
    with server([{"message": {"content": "{}"}}, {"done": True}], identity=identity) as (
        url,
        calls,
    ):
        path, output = tmp_path / "plan.json", tmp_path / "run"
        save_json(path, plan(url))
        collect(path, output, phase="preflight")
        identity["digest"] = "b" * 64
        with pytest.raises(ValueError, match="weights or Ollama version"):
            collect(path, output, phase="comparison")
        assert len(calls) == 1


def test_independent_model_resources_run_together_with_real_process_workers(tmp_path):
    barrier = threading.Barrier(2)
    events = [{"message": {"content": "{}"}}, {"done": True}]
    with (
        server(events, barrier=barrier) as (first, first_calls),
        server(events, barrier=barrier) as (second, second_calls),
    ):
        value = plan(first)
        value["execution_policy"]["max_in_flight"] = 2
        value["execution_policy"]["resources"]["other"] = {
            "capacity": 1,
            "reason": "Independent second service",
        }
        model = {
            **value["models"][0],
            "id": "other",
            "resources": ["other"],
            "endpoint": second + "/api/chat",
            "runtime": {"base": second, "version": "fixture", "digest": "a" * 64},
        }
        value["models"].append(model)
        value["calls"] += [{**call, "model_id": "other"} for call in value["calls"]]
        path, output = tmp_path / "plan.json", tmp_path / "run"
        save_json(path, value)
        collect(path, output, phase="preflight")
        collect(path, output, phase="comparison")
        assert len(first_calls) == len(second_calls) == 2
        assert all(read_json(p)["response"]["success"] for p in (output / "calls").glob("*.json"))


def test_account_denial_stops_the_shared_lane_and_retains_the_call(tmp_path):
    with server([], status=402) as (url, calls):
        value = plan(url)
        value["models"].append({**value["models"][0], "id": "second"})
        value["calls"] += [{**call, "model_id": "second"} for call in value["calls"]]
        path, output = tmp_path / "plan.json", tmp_path / "run"
        save_json(path, value)
        with pytest.raises(RuntimeError, match="account blocked"):
            collect(path, output, phase="preflight")
        assert len(calls) == 1 and len(list((output / "calls").glob("*.json"))) == 1
        with pytest.raises(ValueError, match="retained account denial"):
            collect(path, output, phase="preflight")
        assert len(calls) == 1


def test_thinking_variants_share_one_weight_load_per_phase(tmp_path):
    events = [{"message": {"content": "{}"}}, {"done": True}]
    with server(events) as (url, calls):
        value = plan(url)
        first = value["models"][0]
        first.update(
            load_endpoint=url + "/api/generate",
            parameters={"think": False, "options": {"num_ctx": 20000}},
        )
        value["models"].append(
            {
                **first,
                "id": "thinking",
                "parameters": {"think": True, "options": {"num_ctx": 20000}},
            }
        )
        value["calls"] += [{**call, "model_id": "thinking"} for call in value["calls"]]
        path, output = tmp_path / "plan.json", tmp_path / "run"
        save_json(path, value)
        collect(path, output, phase="preflight")
        collect(path, output, phase="comparison")
        loads = [c for c in calls if "prompt" in c]
        generations = [c for c in calls if "messages" in c]
        assert len(loads) == 2 and len(generations) == 4


def test_result_index_write_interruption_recovers_without_another_inference(tmp_path, monkeypatch):
    from harness.web_extract import model_comparison

    events = [{"message": {"content": "{}"}}, {"done": True}]
    with server(events) as (url, calls):
        path, output = tmp_path / "plan.json", tmp_path / "run"
        save_json(path, plan(url))
        original = model_comparison.save_json

        def fail_index(target, *args, **kwargs):
            if target.name == "call-hashes.json":
                raise OSError("Interrupted between result and index")
            return original(target, *args, **kwargs)

        monkeypatch.setattr(model_comparison, "save_json", fail_index)
        with pytest.raises(OSError, match="between result and index"):
            collect(path, output, phase="preflight")
        assert len(calls) == 1 and list((output / "calls").glob("*.pending"))
        monkeypatch.setattr(model_comparison, "save_json", original)
        assert collect(path, output, phase="preflight")["new_calls"] == 0
        assert len(calls) == 1 and not list((output / "calls").glob("*.pending"))
        collect(path, output, phase="comparison")
        assert len(calls) == 2


def test_unreachable_connection_stops_admission_and_requires_reconciliation(tmp_path):
    import socket

    with socket.socket() as unavailable:
        unavailable.bind(('127.0.0.1', 0))
        value = plan('http://127.0.0.1:' + str(unavailable.getsockname()[1]))
        value['models'][0]['protocol'] = 'openai'
        value['models'].append({**value['models'][0], 'id': 'second'})
        value['calls'] += [{**c, 'model_id': 'second'} for c in value['calls']]
        path, output = tmp_path / 'plan.json', tmp_path / 'run'
        save_json(path, value)
        with pytest.raises(RuntimeError, match='transport failed'):
            collect(path, output, phase='preflight')
        records = list((output / 'calls').glob('*.json'))
        assert len(records) == 1
        result = read_json(records[0])['response']
        assert result.get('http_status') is None
        assert result['elapsed_seconds'] > 0 and not result['events']
        with pytest.raises(ValueError, match='retained (connection refusal|transport failure)'):
            collect(path, output, phase='preflight')
        assert len(list((output / 'calls').glob('*.json'))) == 1


@pytest.mark.parametrize("failed_phase", ["preflight", "comparison"])
def test_partial_transport_recovery_continues_only_undispatched_calls(tmp_path, failed_phase):
    import copy

    from harness.web_extract.model_comparison import transport_repair_plan

    events = [{"message": {"content": "{}"}}, {"done": True}]
    with server(events, interrupt={1 if failed_phase == "preflight" else 3}) as (url, calls):
        value = plan(url)
        later = copy.deepcopy(value["cases"][0])
        later["id"] = "later"
        value["cases"].append(later)
        value["calls"].append({"model_id": "local", "case_id": "later", "phase": "comparison"})
        second = copy.deepcopy(value["models"][0])
        second.update(id="second", parameters={"think": True})
        value["models"].append(second)
        value["calls"] += [{**call, "model_id": "second"} for call in value["calls"]]
        path, run = tmp_path / "plan.json", tmp_path / "run"
        save_json(path, value)
        if failed_phase == "comparison":
            collect(path, run, phase="preflight")
        with pytest.raises(RuntimeError, match="transport failed"):
            collect(path, run, phase=failed_phase)
        original = {p.name: p.read_bytes() for p in (run / "calls").glob("*.json")}
        repair_path, repair_run = tmp_path / "repair.json", tmp_path / "repair-run"
        repaired = transport_repair_plan(path, run, repair_path)
        assert len(repaired["transport_repair"]["predecessors"]) == 1
        collect(repair_path, repair_run, phase="preflight")
        if failed_phase == "comparison":
            collect(repair_path, repair_run, phase="comparison")
        recovery = (repair_path, repair_run)
        collect(path, run, phase=failed_phase, repair=recovery)
        collect(path, run, phase="comparison", repair=recovery)
        assert len(calls) == (7 if failed_phase == "preflight" else 8)
        count = len(calls)
        collect(path, run, phase="comparison", repair=recovery)
        assert len(calls) == count
        assert len(read_json(run / "call-hashes.json")) == 6
        assert all((run / "calls" / name).read_bytes() == raw for name, raw in original.items())


def test_later_outage_keeps_prior_recovery_and_never_replays_its_answer(tmp_path):
    import copy

    from harness.web_extract.model_comparison import transport_repair_plan

    events = [{'message': {'content': '{}'}}, {'done': True}]
    with server(events, interrupt={2, 5}) as (url, calls):
        value = plan(url)
        for cid in ['second', 'third']:
            case = copy.deepcopy(value['cases'][0])
            case['id'] = cid
            value['cases'].append(case)
            value['calls'].append({'model_id': 'local', 'case_id': cid, 'phase': 'comparison'})
        path, run = tmp_path/'plan.json', tmp_path/'run'
        save_json(path, value)
        collect(path, run, phase='preflight')
        with pytest.raises(RuntimeError, match='transport failed'):
            collect(path, run, phase='comparison')
        first_plan, first_run = tmp_path/'first.json', tmp_path/'first-run'
        transport_repair_plan(path, run, first_plan)
        collect(first_plan, first_run, phase='preflight')
        collect(first_plan, first_run, phase='comparison')
        first = (first_plan, first_run)
        with pytest.raises(RuntimeError, match='transport failed'):
            collect(path, run, phase='comparison', repair=first)
        second_plan, second_run = tmp_path/'second.json', tmp_path/'second-run'
        second = transport_repair_plan(path, run, second_plan, repair=first)
        assert len(second['transport_repair']['predecessors']) == 1
        assert [c['case_id'] for c in second['calls'] if c['phase']=='comparison'] == ['second']
        collect(second_plan, second_run, phase='preflight')
        collect(second_plan, second_run, phase='comparison')
        with pytest.raises(ValueError, match='every prior'):
            collect(path, run, phase='comparison', repair=(second_plan, second_run))
        repairs = [first, (second_plan, second_run)]
        collect(path, run, phase='comparison', repair=repairs)
        assert len(calls) == 8
        collect(path, run, phase='comparison', repair=repairs)
        assert len(calls) == 8
        with pytest.raises(ValueError, match='No recoverable'):
            transport_repair_plan(path, run, tmp_path/'repeat.json', repair=repairs)


@pytest.mark.parametrize(
    'status,message,blocked',
    [(429, 'insufficient_quota', True), (429, 'insufficient_quota \ud800', True),
     (403, 'Permission denied for your account', True),
     (429, 'Temporary rate limit; retry later', False), (403, 'Target origin denied', False)],
)
def test_http_error_body_controls_account_stop_without_disclosing_keys(
    tmp_path, status, message, blocked
):
    secret = 'fixture-secret-not-for-output'
    events = [{'error': {'message': message, 'api_key': secret}}]
    with server(events, status=status) as (url, calls):
        value = plan(url)
        value['models'].append({**value['models'][0], 'id': 'second'})
        value['calls'] += [{**c, 'model_id': 'second'} for c in value['calls']]
        path, output = tmp_path/'plan.json', tmp_path/'run'
        save_json(path, value)
        if blocked:
            with pytest.raises(RuntimeError, match='account blocked'):
                collect(path, output, phase='preflight')
            with pytest.raises(ValueError, match='retained account denial'):
                collect(path, output, phase='preflight')
        else:
            collect(path, output, phase='preflight')
        assert len(calls) == (1 if blocked else 2)
        records = list((output/'calls').glob('*.json'))
        assert all(secret not in p.read_text(encoding='utf-8') for p in records)
        assert all(message in json.loads(read_json(p)['response']['non_json_response'])[
            'error']['message'] for p in records)


def test_error_body_has_a_byte_limit_and_a_wall_deadline():
    from harness.web_extract.pipeline import access_blocker

    with server(['x'*20000], status=500) as (url, _):
        response = stream_call(({'endpoint': url, 'protocol': 'ollama'}, {}), timeout=2)
        assert len(response['non_json_response'].encode()) == 16384
    events = [{'error': {'message': 'insufficient_quota'}}] + ['x'*1000]*50
    with server(events, status=429, delay=.02) as (url, _):
        started = time.monotonic()
        response = stream_call(({'endpoint': url, 'protocol': 'ollama'}, {}), timeout=.12)
        assert time.monotonic()-started < .6
        assert access_blocker({'http_status': 429, 'response': response}) is not None


@pytest.mark.parametrize('path', ['/api/version', '/api/tags', '/api/generate'])
def test_auxiliary_slow_drip_has_a_total_deadline_before_inference(tmp_path, path):
    import requests

    with server([{'message': {'content': '{}'}}, {'done': True}], drip={path: .01}) as (url, calls):
        value = plan(url)
        stage = 'load' if path=='/api/generate' else 'verification'
        value['models'][0]['auxiliary_timeouts'] = {stage: .08}
        if stage=='load':
            value['models'][0]['load_endpoint'] = url+path
        plan_path, output = tmp_path/'plan.json', tmp_path/'run'
        save_json(plan_path, value)
        started = time.monotonic()
        with pytest.raises((TimeoutError, requests.RequestException)):
            collect(plan_path, output, phase='preflight')
        assert time.monotonic()-started < 1
        assert all(c.get('prompt')=='' for c in calls)
        assert not list((output/'calls').glob('*.json'))


def test_telemetry_slow_drip_keeps_and_checkpoints_the_completed_answer(tmp_path):
    events = [{'message': {'content': '{}'}}, {'done': True}]
    with server(events, drip={'/api/ps': .01}) as (url, calls):
        value = plan(url)
        value['models'][0].update(telemetry_endpoint=url+'/api/ps',
                                  auxiliary_timeouts={'telemetry': .08})
        path, output = tmp_path/'plan.json', tmp_path/'run'
        save_json(path, value)
        collect(path, output, phase='preflight')
        record = read_json(next((output/'calls').glob('*.json')))['response']
        assert record['success'] and record['data']=={} and record['transport_complete']
        assert record['telemetry_error'] and record['telemetry_seconds'] < .6
        assert len(calls)==1


def test_optional_load_options_use_native_defaults(tmp_path):
    with server([{'message': {'content': '{}'}}, {'done': True}]) as (url, calls):
        value = plan(url)
        value['models'][0]['load_endpoint'] = url+'/api/generate'
        path, output = tmp_path/'plan.json', tmp_path/'run'
        save_json(path, value)
        collect(path, output, phase='preflight')
        assert len(calls)==2 and calls[0]['options']=={}


def test_overlapping_load_does_not_hide_work_from_budget_projection(tmp_path, monkeypatch):
    from harness.web_extract import model_comparison

    inference_started, second_loaded = threading.Event(), threading.Event()
    value = plan('http://fixture')
    value['execution_policy'] = {
        'purpose': 'quality', 'max_in_flight': 2, 'backend': 'process',
        'resources': {name: {'capacity': 1, 'reason': 'Independent model server'}
                      for name in ('a', 'b')},
    }
    value['models'] = [
        {**value['models'][0], 'id': name, 'model': name, 'resources': [name],
         'load_endpoint': 'http://fixture/load'} for name in ('a', 'b')
    ]
    value['calls'] = [
        {'model_id': name, 'case_id': 'page', 'phase': 'preflight'} for name in ('a', 'b')
    ] + [
        {'model_id': name, 'case_id': 'page', 'phase': 'comparison', 'ordinal': n}
        for n in range(10) for name in ('a', 'b')
    ]
    value['wall_time_budget_seconds'] = 1

    original_auxiliary = model_comparison.auxiliary_json

    def load(*args, payload=None, **kwargs):
        if payload is None:
            return original_auxiliary(*args, **kwargs)
        if payload['model'] == 'b':
            assert inference_started.wait(timeout=2)
            time.sleep(.2)
            second_loaded.set()
        return {'done': True}

    class OverlappingInference:
        def wait(self, timeout):
            inference_started.set()
            assert second_loaded.wait(timeout=timeout)

    monkeypatch.setattr(model_comparison, 'auxiliary_json', load)
    monkeypatch.setattr(model_comparison, 'verify_models', lambda models: None)
    events = [{'message': {'content': '{}'}}, {'done': True}]
    with server(events, barrier=OverlappingInference()) as (url, requests):
        for model in value['models']:
            model['endpoint'] = url+'/api/chat'
            model['runtime']['base'] = url
        path, output = tmp_path/'plan.json', tmp_path/'run'
        save_json(path, value)
        collect(path, output, phase='preflight')
        sample = read_json(output/'preflight.json')
        assert sample['wall_seconds'] >= sample['elapsed_wall_seconds']
        assert sample['separately_recorded_model_load_seconds'] >= .2
        value = read_json(output/'execution-plan.json')
        value['wall_time_budget_seconds'] = sample['elapsed_wall_seconds'] * 5
        save_json(output/'execution-plan.json', value)
        with pytest.raises(ValueError, match='exceeds the budget'):
            collect(path, output, phase='comparison')
        assert len(requests) == 2


@pytest.mark.parametrize('invalid', [0, -1, float('inf'), float('nan'), True])
def test_invalid_auxiliary_deadlines_fail_before_any_model_call(tmp_path, invalid):
    with server([]) as (url, calls):
        value = plan(url)
        value['models'][0]['auxiliary_timeouts'] = {'telemetry': invalid}
        path = tmp_path/'plan.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        with pytest.raises(ValueError, match='finite positive'):
            collect(path, tmp_path/'run', phase='preflight')
        assert not calls
