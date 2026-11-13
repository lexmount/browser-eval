"""Paid-call safety and replay integrity through the public judging entry point."""

import base64
import copy
import hashlib
import io
import json
import urllib.error
import urllib.request

import pytest

from harness.web_search.relevance import judge_main, validate_model_evidence


@pytest.fixture
def run_judge(tmp_path, monkeypatch):
    from harness.web_fetch import config
    from harness.web_search import report

    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("SEARCH_JUDGE_API_KEY=test-only\nSEARCH_JUDGE_BASE_URL=https://test.invalid/v1\n", encoding="utf-8")

    profile = {
        "protocol": {"top_k": 5},
        "cases": [{"id": "q", "query": "Install the package", "family": "web"}],
        "methods": [{"id": "a"}],
    }
    rows = [
        {
            "case_id": "q",
            "method": "a",
            "results": [
                {
                    "url": "https://example.org/guide",
                    "title": "Install",
                    "snippet": "pip install package",
                }
            ],
        }
    ]
    monkeypatch.setattr(report, "check", lambda *a, **kw: (profile, rows))
    monkeypatch.setenv("SEARCH_JUDGE_API_KEY", "test-only")
    monkeypatch.setattr("time.sleep", lambda n: None)
    args = [
        "--report",
        str(tmp_path / "report"),
        "--output",
        str(tmp_path / "judge"),
        "--model",
        "test-model",
        "--base-url",
        "https://test.invalid/v1",
        "--workers",
        "1",
    ]

    def run(extra=()):
        judge_main(args + list(extra))

    run.output = tmp_path / "judge"
    return run


def answer(request):
    packet = json.loads(json.loads(request.data)["messages"][1]["content"])
    items = [
        {
            "candidate_id": c["candidate_id"],
            "grade": 3,
            "reason": "Installation steps",
            "evidence": {"field": "snippet", "preview_index": 0},
        }
        for c in packet["candidates"]
    ]
    return (
        json.dumps(
            {
                "model": "test-model",
                "choices": [{"message": {"content": json.dumps({"judgments": items})}}],
            }
        ).encode()
        + b"\r\n"
    )


class Response(io.BytesIO):
    status = 200


@pytest.mark.parametrize('level',['l1','l2'])
def test_public_judge_selects_native_fields_and_replays_l2_admission(run_judge,monkeypatch,tmp_path,level):
    from harness.record_io import bytes_sha256
    from harness.web_search import report
    from tests.web_search.test_field_admission import example

    profile,rows,receipt,artifacts=example()
    profile['cases'][0].pop('reference_answer')
    monkeypatch.setattr(report,'check',lambda *a,**kw:(profile,rows))
    args=['--level',level]
    if level == 'l2':
        for name,raw in artifacts.items():
            (tmp_path/name).write_bytes(raw)
        raw=json.dumps(receipt).encode();(tmp_path/'admission.json').write_bytes(raw)
        bundle=dict(schema='web-search-field-admissions/1',receipts={'web':dict(path='admission.json',sha256=bytes_sha256(raw))},
                    artifacts=[dict(path=name,sha256=bytes_sha256(value)) for name,value in artifacts.items()])
        (tmp_path/'admissions.json').write_text(json.dumps(bundle))
        args+=['--field-admissions',str(tmp_path/'admissions.json')]
    requests=[]
    def transport(request,**kwargs):
        assert request.full_url.endswith('/chat/completions')
        message=json.loads(request.data)
        packet=json.loads(message['messages'][1]['content'])
        for candidate in packet['candidates']:
            assert set(candidate['previews'][0]) == {'title','snippet'}
            actual=candidate['previews'][0]['snippet']
            assert actual == ('Primary preview' if level == 'l1' else rows[0]['results'][0]['summary'])
        requests.append(request)
        return Response(answer(request))
    monkeypatch.setattr(urllib.request,'urlopen',transport)
    run_judge(args)
    evidence=json.loads((run_judge.output/'relevance.json').read_bytes())
    assert evidence['evaluation_level'] == level
    assert evidence['judge']['configuration']['extra_params']['enable_thinking'] is False
    validate_model_evidence(evidence)
    assert len(requests) == 1


@pytest.mark.parametrize('level', ['l1','l2'])
def test_layered_judge_receipts_bind_level_prompt_and_exact_fields(run_judge, monkeypatch, level):
    from harness.web_search import report
    from harness.web_search.relevance import verify_provenance
    from lexbench_websearch.rubric import judge_prompt

    profile = dict(protocol=dict(top_k=5,relevance_input='delivered-previews/1',relevance_level=level),
                   cases=[dict(id='q',query='Install the package',family='web')],methods=[dict(id='a')])
    rows = [dict(case_id='q',method='a',results=[dict(url='https://example.test/guide',
                                                   title='Install',snippet='pip install package')])]
    monkeypatch.setattr(report,'check',lambda *a,**kw:(profile,rows))
    requests=[]
    def transport(request,**kwargs):
        payload=json.loads(request.data)
        packet=json.loads(payload['messages'][1]['content'])
        assert packet['evaluation_level'] == level
        assert ('url' in packet['candidates'][0]) == (level == 'l1')
        assert payload['messages'][0]['content'] == judge_prompt(level)
        requests.append(request)
        return Response(answer(request))
    monkeypatch.setattr(urllib.request,'urlopen',transport)
    run_judge(['--disable-thinking'])
    evidence=json.loads((run_judge.output/'relevance.json').read_bytes())
    assert evidence['evaluation_level'] == level
    assert evidence['judge']['configuration']['extra_params']['enable_thinking'] is False
    verify_provenance(profile,rows,evidence)
    corrupted=copy.deepcopy(evidence)
    corrupted['evaluation_level']='l2' if level == 'l1' else 'l1'
    with pytest.raises(ValueError,match='same output level'):
        validate_model_evidence(corrupted)
    run_judge(['--disable-thinking'])
    assert len(requests) == 1


def test_adding_provider_reuses_only_unchanged_blind_inputs(run_judge, monkeypatch, tmp_path):
    from harness.web_search import report

    requests = []

    def transport(request, **kwargs):
        requests.append(request)
        return Response(answer(request))

    monkeypatch.setattr(urllib.request, "urlopen", transport)
    run_judge()
    predecessor = run_judge.output / "relevance.json"
    original = predecessor.read_bytes()
    profile = {
        "protocol": {"top_k": 5},
        "cases": [{"id": "q", "query": "Install the package", "family": "web"}],
        "methods": [{"id": "a"}, {"id": "b"}],
    }
    item = {"url": "https://example.org/guide", "title": "Install",
            "snippet": "pip install package"}
    rows = [{"case_id": "q", "method": m, "results": [dict(item)]} for m in ("a", "b")]
    monkeypatch.setattr(report, "check", lambda *a, **kw: (profile, rows))
    reused = tmp_path / "added-provider"
    run_judge(["--output", str(reused), "--reuse-evidence", str(predecessor)])
    assert len(requests) == 1
    evidence = json.loads((reused / "relevance.json").read_bytes())
    assert evidence["judge"]["inherited_case_ids"] == ["q"]
    assert set(evidence["provider_rankings"]["q"]) == {"a", "b"}
    validate_model_evidence(evidence)

    rows[1]["results"][0]["url"] = "https://another.example/guide"
    expanded = tmp_path / "expanded-pool"
    run_judge(["--output", str(expanded), "--reuse-evidence", str(predecessor)])
    assert len(requests) == 2
    evidence = json.loads((expanded / "relevance.json").read_bytes())
    assert evidence["judge"]["inherited_case_ids"] == []
    assert len(evidence["labels"]["q"]) == 2
    validate_model_evidence(evidence)

    rows[0]["results"][0]["snippet"] = "A changed original provider output"
    with pytest.raises(ValueError, match="candidate text differs"):
        run_judge(["--output", str(tmp_path / "changed-old-output"),
                   "--reuse-evidence", str(predecessor)])
    assert len(requests) == 2
    assert predecessor.read_bytes() == original


def test_every_physical_attempt_is_bound_and_success_resume_never_calls(run_judge, monkeypatch):
    requests = []

    def transport(request, **kwargs):
        requests.append(request)
        if len(requests) == 1:
            raise urllib.error.HTTPError(
                request.full_url, 429, "rate limit", {}, io.BytesIO(b"rate limited\xff\r\n")
            )
        return Response(answer(request))

    monkeypatch.setattr(urllib.request, "urlopen", transport)
    run_judge(['--retries', '2'])
    input_packet = json.loads(json.loads(requests[-1].data)["messages"][1]["content"])
    assert "example.org" not in json.dumps(input_packet)
    assert all("source_eligible" in c for c in input_packet["candidates"])
    evidence = json.loads((run_judge.output / "relevance.json").read_text(encoding="utf-8"))
    attempts = evidence["judge"]["calls"][0]["attempts"]
    assert [a["status"] for a in attempts] == [429, 200]
    assert base64.b64decode(attempts[0]["response_bytes_base64"]) == b"rate limited\xff\r\n"
    validate_model_evidence(evidence)
    run_judge(['--retries', '2'])
    assert len(requests) == 2
    for change in ("bytes", "tokens", "thinking", "configuration"):
        bad = copy.deepcopy(evidence)
        if change == "bytes":
            bad["judge"]["calls"][0]["attempts"][0]["response_bytes_base64"] = "eA=="
        elif change == "tokens":
            del bad["judge"]["calls"][0]["request"]["max_tokens"]
        elif change == "thinking":
            bad["judge"]["calls"][0]["request"]["extra_params"] = {"enable_thinking": True}
        else:
            del bad["judge"]["configuration"]["max_tokens"]
        with pytest.raises(ValueError):
            validate_model_evidence(bad)
    with pytest.raises(ValueError, match="different inputs or configuration"):
        run_judge(["--disable-thinking", "--retries", "2"])
    assert len(requests) == 2


@pytest.mark.parametrize(
    "status,body,expected",
    [(401, b"invalid key\xff", 1), (503, b"unavailable", 3), (200, b"not JSON\xff\r\n", 1)],
)
def test_failures_keep_bytes_and_total_retry_budget_survives_resume(
    run_judge, monkeypatch, status, body, expected
):
    count = 0

    def transport(request, **kwargs):
        nonlocal count
        count += 1
        if status == 200:
            return Response(body)
        raise urllib.error.HTTPError(request.full_url, status, "failed", {}, io.BytesIO(body))

    monkeypatch.setattr(urllib.request, "urlopen", transport)
    for _ in range(2):
        with pytest.raises(ValueError, match="Incomplete judge run"):
            run_judge(['--retries', '2'])
    assert count == expected
    journal = json.loads(next(run_judge.output.glob("*-attempts.json")).read_text(encoding="utf-8"))
    assert len(journal) == expected
    assert all(base64.b64decode(a["response_bytes_base64"]) == body for a in journal)


def test_invalid_model_answer_is_retained_and_never_automatically_replaced(run_judge, monkeypatch):
    count = 0

    def transport(request, **kwargs):
        nonlocal count
        count += 1
        raw = json.loads(answer(request))
        raw["choices"][0]["message"]["content"] = '{"judgments":[]}'
        return Response(json.dumps(raw).encode())

    monkeypatch.setattr(urllib.request, "urlopen", transport)
    for _ in range(2):
        with pytest.raises(ValueError, match="Incomplete judge run"):
            run_judge()
    assert count == 1
    assert next(run_judge.output.glob("*-attempts.json")).exists()


def test_a_changed_valid_response_cannot_authorize_a_paid_format_repair(run_judge, monkeypatch):
    requests = []

    def transport(request, **kwargs):
        requests.append(request)
        return Response(answer(request))

    monkeypatch.setattr(urllib.request, 'urlopen', transport)
    run_judge()
    path = run_judge.output / (hashlib.sha256(b'q').hexdigest()[:16] + '.json')
    saved = json.loads(path.read_bytes())
    saved['response']['choices'][0]['message']['content'] = '{"judgments":[]}'
    path.write_text(json.dumps(saved), encoding='utf-8')
    with pytest.raises(ValueError, match='Incomplete judge run'):
        run_judge(['--repair-format'])
    assert len(requests) == 1


@pytest.mark.parametrize('invalid_format', ['omitted-candidates', 'quoted-string', 'url-reference'])
def test_explicit_format_repair_preserves_invalid_call_and_never_repeats_valid_answers(
    run_judge, monkeypatch, invalid_format
):
    requests = []

    def transport(request, **kwargs):
        requests.append(request)
        raw = json.loads(answer(request))
        if len(requests) == 1:
            if invalid_format == 'omitted-candidates':
                raw['choices'][0]['message']['content'] = '{"judgments":[]}'
            else:
                items = json.loads(raw['choices'][0]['message']['content'])
                items['judgments'][0]['evidence'] = ('pip install package' if invalid_format == 'quoted-string'
                                                    else {'field': 'url', 'preview_index': 0})
                raw['choices'][0]['message']['content'] = json.dumps(items)
        return Response(json.dumps(raw).encode())

    monkeypatch.setattr(urllib.request, 'urlopen', transport)
    with pytest.raises(ValueError, match='Incomplete judge run'):
        run_judge()
    original = json.loads(next(p for p in run_judge.output.glob('*.json')
                               if p.name not in ('contract.json',) and not p.name.endswith('-attempts.json')).read_bytes())
    run_judge(['--repair-format'])
    evidence = json.loads((run_judge.output / 'relevance.json').read_bytes())
    validate_model_evidence(evidence)
    assert evidence['judge']['calls'][0]['format_repair']['prior_call'] == original
    for field in ('response', 'request-hash'):
        changed = copy.deepcopy(evidence)
        prior = changed['judge']['calls'][0]['format_repair']['prior_call']
        if field == 'response':
            prior['response']['model'] = 'other-model'
        else:
            prior['attempts'][0]['request_sha256'] = '0' * 64
        with pytest.raises(ValueError):
            validate_model_evidence(changed)
    assert len(requests) == 2
    run_judge(['--repair-format'])
    assert len(requests) == 2


def test_corrupt_failure_journal_never_authorizes_a_new_request(run_judge, monkeypatch):
    count = 0

    def transport(request, **kwargs):
        nonlocal count
        count += 1
        raise urllib.error.HTTPError(request.full_url, 401, "failed", {}, io.BytesIO(b"denied"))

    monkeypatch.setattr(urllib.request, "urlopen", transport)
    with pytest.raises(ValueError, match="Incomplete judge run"):
        run_judge()
    journal_path = next(run_judge.output.glob("*-attempts.json"))
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal[0]["retryable"] = True
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    with pytest.raises(ValueError, match="Incomplete judge run"):
        run_judge()
    assert count == 1


def test_new_model_receipts_reject_free_form_evidence_without_another_call(run_judge, monkeypatch):
    count = 0

    def transport(request, **kwargs):
        nonlocal count
        count += 1
        response = json.loads(answer(request))
        items = json.loads(response["choices"][0]["message"]["content"])
        items["judgments"][0]["evidence"] = "p"
        response["choices"][0]["message"]["content"] = json.dumps(items)
        return Response(json.dumps(response).encode())

    monkeypatch.setattr(urllib.request, "urlopen", transport)
    for _ in range(2):
        with pytest.raises(ValueError, match="Incomplete judge run"):
            run_judge()
    assert count == 1


def test_missing_repository_credential_fails_before_output_creation(run_judge, monkeypatch):
    (run_judge.output.parent / ".env").unlink()
    monkeypatch.setenv("SEARCH_JUDGE_API_KEY", "shell-only-is-not-authorized")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: pytest.fail("No API call"))
    with pytest.raises(ValueError, match="repository .env"):
        run_judge()
    assert not run_judge.output.exists()


def test_custom_proxy_credential_is_loaded_from_real_repository_env_file(run_judge, monkeypatch):
    name = "BIFROST_API_KEY"
    (run_judge.output.parent / ".env").write_text(f"{name}=repository-test-value\nSEARCH_JUDGE_BASE_URL=https://test.invalid/v1\n", encoding="utf-8")
    monkeypatch.setenv(name, "stale-shell-value")
    calls = []

    def transport(request, **kwargs):
        assert request.get_header("Authorization") == "Bearer repository-test-value"
        calls.append(request)
        return Response(answer(request))

    monkeypatch.setattr(urllib.request, "urlopen", transport)
    run_judge(["--token-env", name])
    assert len(calls) == 1


@pytest.mark.parametrize(
    "base",
    [
        "http://external.example/v1",
        "https://reader:secret@example.org/v1",
        "https://example.org/v1?redirect=other",
        "https://example.org/v1#fragment",
        "https:///missing-host",
        "https://example.org:invalid/v1",
        "https://example.org\n/v1",
    ],
)
def test_invalid_base_url_fails_before_output_creation(run_judge, monkeypatch, base):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: pytest.fail("No API call"))
    with pytest.raises(ValueError, match="Judge base URL") as error:
        run_judge(["--base-url", base])
    assert "secret" not in str(error.value)
    assert not run_judge.output.exists()


@pytest.mark.parametrize("name", ["", "INVALID-NAME", "KEY\nOTHER", "1INVALID"])
def test_invalid_credential_variable_name_creates_no_output(run_judge, name):
    with pytest.raises(ValueError, match="valid environment variable name"):
        run_judge(["--token-env", name])
    assert not run_judge.output.exists()


@pytest.mark.parametrize(
    "base", ["http://localhost:8000/v1", "http://127.0.0.1/v1", "http://[::1]/v1"]
)
def test_loopback_http_is_usable_for_local_transport_tests(run_judge, monkeypatch, base):
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, **kw: Response(answer(request)))
    from harness.web_fetch import config
    (config.ROOT / ".env").write_text("SEARCH_JUDGE_API_KEY=test-only\nSEARCH_JUDGE_BASE_URL=" + base + "\n", encoding="utf-8")
    run_judge(["--base-url", base])
    assert (run_judge.output / "relevance.json").is_file()


def test_correction_reuses_identical_inputs_and_calls_only_changed_cases(
    run_judge, monkeypatch, tmp_path
):
    from harness.web_search import report

    profile, rows = report.check(None)
    profile["cases"].append(dict(profile["cases"][0], id="unchanged"))
    rows.append(dict(rows[0], case_id="unchanged"))
    requests = []

    def transport(request, **kwargs):
        requests.append(request)
        return Response(answer(request))

    monkeypatch.setattr(urllib.request, "urlopen", transport)
    rules = tmp_path / "source-rules.json"
    rules.write_text("{}", encoding="utf-8")
    run_judge(["--source-requirements", str(rules)])
    assert len(requests) == 2
    prior = run_judge.output / "relevance.json"
    # Exercise a genuine legacy receipt shape without inventing physical attempts.
    previous = json.loads(prior.read_text(encoding="utf-8"))
    previous["judge"]["configuration"].pop("receipt_schema")
    for call in previous["judge"]["calls"]:
        del call["attempts"]
    import hashlib
    pinned = tmp_path / "legacy-evidence.json"
    pinned.write_text(json.dumps({"model":[hashlib.sha256(json.dumps(previous,ensure_ascii=False,sort_keys=True).encode()).hexdigest()]}), encoding="utf-8")
    authority = json.loads(pinned.read_bytes())
    destination = tmp_path / "report/assessment-authority.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_bytes(pinned.read_bytes())
    validate_model_evidence(previous, authority=authority)
    prior.write_text(json.dumps(previous), encoding="utf-8")
    changed = tmp_path / "changed-rules.json"
    changed.write_text(json.dumps({"q": {"domains": ["required.invalid"]}}), encoding="utf-8")
    output = tmp_path / "corrected"
    run_judge(
        [
            "--output",
            str(output),
            "--reuse-evidence",
            str(prior),
            "--source-requirements",
            str(changed),
        ]
    )
    assert len(requests) == 3  # one changed source-policy input, not both queries
    evidence = json.loads((output / "relevance.json").read_text(encoding="utf-8"))
    assert evidence["judge"]["inherited_case_ids"] == ["unchanged"]
    assert (
        next(c for c in evidence["judge"]["calls"] if c["case_id"] == "unchanged")
        == previous["judge"]["calls"][1]
    )
    assert evidence["labels"]["q"] == {
        candidate["candidate_id"]: 0
        for candidate in evidence["blind_candidate_packets"][0]["candidates"]
    }
    validate_model_evidence(evidence, authority=authority)
    run_judge(
        [
            "--output",
            str(output),
            "--reuse-evidence",
            str(prior),
            "--source-requirements",
            str(changed),
        ]
    )
    assert len(requests) == 3
    bad = copy.deepcopy(evidence)
    bad["judge"]["predecessor"]["judge"]["calls"][0]["response"]["model"] = "fabricated"
    with pytest.raises(ValueError, match="Inherited judge evidence changed"):
        validate_model_evidence(bad, authority=authority)
    bad = copy.deepcopy(evidence)
    inherited = next(c for c in bad["judge"]["calls"] if c["case_id"] == "unchanged")
    inherited["elapsed_ms"] = -1
    with pytest.raises(ValueError, match="differs from its original"):
        validate_model_evidence(bad, authority=authority)

    bad = copy.deepcopy(evidence)
    bad["judge"]["inherited_case_ids"].append("q")
    with pytest.raises(ValueError, match="differs from its original"):
        validate_model_evidence(bad, authority=authority)
    with pytest.raises(ValueError, match="transport configuration changed|endpoint host"):
        run_judge(
            [
                "--output",
                str(tmp_path / "wrong-transport"),
                "--reuse-evidence",
                str(prior),
                "--base-url",
                "https://another.invalid/v1",
                "--source-requirements",
                str(changed),
            ]
        )
    assert len(requests) == 3


def test_judge_host_allowlist_is_not_interpolated_from_the_shell(run_judge, monkeypatch):
    from harness.web_fetch import config

    monkeypatch.setenv("JUDGE_BASE_FROM_SHELL", "https://test.invalid/v1")
    (config.ROOT / ".env").write_text(
        "SEARCH_JUDGE_API_KEY=test-only\nSEARCH_JUDGE_BASE_URL=${JUDGE_BASE_FROM_SHELL}\n",
        encoding="utf-8"
    )
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **kw: pytest.fail("token sent to a shell-chosen host")
    )
    with pytest.raises(ValueError):
        run_judge([])
    assert not run_judge.output.exists()


def test_judge_can_score_verified_collection_without_building_reader_pages(run_judge, monkeypatch, tmp_path):
    from harness.web_search.example import example

    seed = tmp_path / 'seed'
    example(seed)
    requests = []

    def transport(request, **kwargs):
        requests.append(request)
        return Response(answer(request))

    monkeypatch.setattr(urllib.request, 'urlopen', transport)
    output = tmp_path / 'collection-judge'
    judge_main(['--source', str(seed / 'fixture'), '--output', str(output),
                '--model', 'test-model', '--base-url', 'https://test.invalid/v1', '--workers', '1'])
    evidence = json.loads((output / 'relevance.json').read_bytes())
    assert len(requests) == len(evidence['plan']['case_ids'])
    assert evidence['labels']
    assert not list((output / 'inputs').rglob('*.html'))


def test_default_judge_failure_is_one_attempt_even_when_resumed(run_judge, monkeypatch):
    spent = []
    def transport(request, **kwargs):
        spent.append(request)
        raise urllib.error.HTTPError(request.full_url, 503, 'unavailable', {}, io.BytesIO(b'error'))
    monkeypatch.setattr(urllib.request, 'urlopen', transport)
    for _ in range(2):
        with pytest.raises(ValueError, match='Incomplete judge run'):
            run_judge()
    assert len(spent) == 1
    assert json.loads((run_judge.output/'contract.json').read_bytes())['attempt_limit'] == 1


def test_interrupted_format_repair_keeps_original_and_recovers_without_paid_call(tmp_path, monkeypatch):
    from pathlib import Path

    from harness.web_search.relevance import run_judge_request
    path = tmp_path/'call.json'
    original = {'paid': 'original'}
    path.write_text(json.dumps(original))
    config = dict(base_url='https://test.invalid/v1', request_headers={}, attempt_limit=1)
    request = dict(messages=[])
    spent = []
    def transport(*args, **kwargs):
        spent.append(1)
        return Response(b'{"choices": []}')
    monkeypatch.setattr(urllib.request, 'urlopen', transport)
    replace = Path.replace
    def interrupted(self, target):
        if Path(target) == path:
            raise OSError('Disk full')
        return replace(self, target)
    monkeypatch.setattr(Path, 'replace', interrupted)
    with pytest.raises(OSError, match='Disk full'):
        run_judge_request(path, 'q', request, config, 'fixture', prior_call=original)
    assert json.loads(path.read_bytes()) == original
    monkeypatch.setattr(Path, 'replace', replace)
    restored = run_judge_request(path, 'q', request, config, 'fixture', prior_call=original)
    assert len(spent) == 1 and restored['format_repair']['prior_call'] == original
    assert json.loads(path.read_bytes()) == restored
