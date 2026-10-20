"""JSONL records are framed by LF bytes, including during concurrent writes."""
import json
import hashlib
from dataclasses import asdict

import pytest

from harness.record_io import canonical_json_sha256, load_jsonl, parse_jsonl, read_completed_records, same_json_record


@pytest.mark.parametrize('value,raw', [
    ({'z': 'English', 'a': '中文'}, '{"a":"中文","z":"English"}'),
    ({'nested': [{'b': False, 'a': None}, [1, True, 'é']]},
     '{"nested":[{"a":null,"b":false},[1,true,"é"]]}'),
    ({'n': float('nan')}, '{"n":NaN}'),
    ({'n': float('inf')}, '{"n":Infinity}'),
    ({'n': float('-inf')}, '{"n":-Infinity}'),
])
def test_canonical_hash_preserves_frozen_utf8_json_bytes(value, raw):
    assert canonical_json_sha256(value) == hashlib.sha256(raw.encode('utf-8')).hexdigest()


@pytest.mark.parametrize('left,right', [
    ({'ok': True}, {'ok': 1}),
    ({'n': 1}, {'n': 1.0}),
    ({'text': '中文'}, {'text': '\\u4e2d\\u6587'}),
    ({'n': float('nan')}, {'n': 'NaN'}),
    ([1, 2], [2, 1]),
])
def test_canonical_hash_does_not_collapse_distinct_json_bytes(left, right):
    assert canonical_json_sha256(left) != canonical_json_sha256(right)


def test_canonical_hash_sorts_keys_without_changing_unicode_representation():
    assert canonical_json_sha256({'b': 1, 'a': '中文'}) == canonical_json_sha256({'a': '中文', 'b': 1})
    raw = json.dumps({'a': '中文'}, sort_keys=True, separators=(',', ':')).encode('utf-8')
    assert canonical_json_sha256({'a': '中文'}) != hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('left,right,expected', [
    ({'ok': False, 'content': '中文'}, {'content': '中文', 'ok': False}, True),
    ({'ok': False}, {'ok': 0}, False),
    ({'score': 1}, {'score': True}, False),
    ({'elapsed': 1}, {'elapsed': 1.0}, False),
    ([{'ok': True}, {'ok': False}], [{'ok': False}, {'ok': True}], False),
    ({'content': 'a\u2028b'}, {'content': 'a\u2028b'}, True),
])
def test_json_record_agreement_preserves_scalar_types_and_order(left, right, expected):
    assert same_json_record(left, right) is expected


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_json_records_cannot_establish_agreement(value):
    with pytest.raises(ValueError):
        same_json_record({'score': value}, {'score': value})


@pytest.mark.parametrize('separator', ['\u0085', '\u2028', '\u2029'])
@pytest.mark.parametrize('ending', [b'\n', b'\r\n', b''])
def test_completed_reader_keeps_unicode_separators_inside_json_strings(tmp_path, separator, ending):
    rows = [{'case_id': 'one', 'output': 'left' + separator + 'right'}, {'case_id': 'two'}]
    path = tmp_path / 'results.jsonl'
    path.write_bytes(b'\n'.join(json.dumps(row, ensure_ascii=False).encode() for row in rows) + ending)
    assert read_completed_records(path) == rows


def test_completed_reader_ignores_a_partial_utf8_tail(tmp_path):
    path = tmp_path / 'results.jsonl'
    path.write_bytes(b'{"case_id":"one"}\n{"output":"' + '中'.encode()[:2])
    assert read_completed_records(path) == [{'case_id': 'one'}]


@pytest.mark.parametrize('raw', [b'{"case_id":"one"}\n\xff', b'{"case_id":"one"}\n{"output":"\xe4\xb8\n'])
def test_completed_reader_rejects_invalid_or_terminated_partial_utf8(tmp_path, raw):
    path = tmp_path / 'results.jsonl'
    path.write_bytes(raw)
    with pytest.raises(UnicodeDecodeError):
        read_completed_records(path)


@pytest.mark.parametrize('separator', ['\u0085', '\u2028', '\u2029'])
def test_finalized_readers_preserve_unicode_and_reject_partial_records(tmp_path, separator):
    rows = [{'id': 'one', 'text': 'left' + separator + 'right'}, {'id': 'two'}]
    raw = '\r\n'.join(json.dumps(row, ensure_ascii=False) for row in rows).encode()
    path = tmp_path / 'records.jsonl'
    path.write_bytes(raw)
    assert load_jsonl(path) == parse_jsonl(raw) == parse_jsonl(raw.decode()) == rows
    for partial in (b'{"unfinished":', b'{"text":"\xe4\xb8'):
        path.write_bytes(raw + b'\n' + partial)
        with pytest.raises((json.JSONDecodeError, UnicodeDecodeError)):
            load_jsonl(path)


def test_blank_line_policy_is_explicit_and_bare_cr_is_not_a_record_separator(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_bytes(b'\n {"id":"one"}\r\n\t \n')
    assert load_jsonl(path, skip_blank=True) == [{'id': 'one'}]
    with pytest.raises(json.JSONDecodeError):
        load_jsonl(path)
    with pytest.raises(json.JSONDecodeError):
        parse_jsonl(b'{"id":"one"}\r{"id":"two"}')
    assert parse_jsonl(b'') == []


def test_unicode_evidence_survives_execution_assembly_and_report(tmp_path):
    from harness.web_fetch.assembly import assemble
    from lexbench_webfetch.models import EvalCase
    from harness.web_fetch.models import ProviderResult
    from harness.web_fetch.providers.base import Provider
    from harness.web_fetch.report_build import build_report
    from harness.web_fetch.runner import run_evaluation

    text = 'Expected 中\u0085文\u2028段落\u2029结束'

    class Fixture(Provider):
        def fetch(self, case, timeout):
            return ProviderResult(True, text, metadata={'unicode_detail': text})

    source = tmp_path / 'source'
    case = EvalCase('one', 'One', 'https://example.invalid/', 'synthetic', ['Expected'])
    definition = {'id': 'unicode', 'cases': [asdict(case)],
                  'coverage_plan': {'web': ['unicode']}, 'comparison': {}}
    rows, _ = run_evaluation(output_dir=source, dataset_meta=definition,
        dataset_path=tmp_path / 'input.json', cases=[case],
        providers=[Fixture('unicode', {'type': 'fixture'})],
        provider_config_path=tmp_path / 'providers.json', runs=1, timeout=1, delay=0)
    # UTF-8 JSONL is also a supported external producer format. The built-in
    # runner currently escapes non-ASCII strings; retain the same logical rows.
    (source / 'results.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))
    snapshot = (source / 'results.jsonl').read_bytes()
    assert '\u2028'.encode() in snapshot
    output = tmp_path / 'assembled'
    assemble(source, tmp_path / 'certificates', [], output)
    report = build_report(output, tmp_path / 'report').parent
    for root in (source, output, report):
        assert load_jsonl(root / 'results.jsonl') == rows
    assert (source / 'results.jsonl').read_bytes() == snapshot
    assert rows[0]['provider_metadata']['unicode_detail'] == text


def test_browser_use_resume_preserves_unicode_in_attempt_records(tmp_path):
    from harness.browser_use.runner import _load_completed_attempts

    path = tmp_path / 'attempts.jsonl'
    row = {'model': 'fixture', 'engine': 'fixture', 'task_id': 1, 'attempt': 1,
           'error': '文本\u2028段落\u2029结束'}
    path.write_text(json.dumps(row, ensure_ascii=False) + '\n')
    assert _load_completed_attempts(path, 'fixture') == {('fixture', 'fixture', 1, 1): row}
