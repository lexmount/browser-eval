"""Saved answer representation is distinct from origin response Content-Type."""
import hashlib
import json
import subprocess
import sys

import pytest

from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.response_input import answer_input
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.metrics import task_passed

CASE = {'id': 'literal', 'url': 'https://example.test/', 'required_facts': [
    {'id': 'fact', 'alternatives': ['Required phrase.']}], 'min_content_chars': 1}
TEXT = b'Use <style> to style a component.\nRequired phrase.'


def passed(raw=TEXT, metadata=None, **kwargs):
    return task_passed(True, score_attempt(CASE, raw, ok=True, metadata=metadata, **kwargs))


def test_plain_text_is_literal_without_changing_artifact():
    original = hashlib.sha256(TEXT).hexdigest()
    assert passed(metadata={'answer_format': 'plain_text'})
    assert hashlib.sha256(TEXT).hexdigest() == original
    assert not passed(TEXT.replace(b'Required phrase.', b''), {'answer_format': 'plain_text'})


@pytest.mark.parametrize('format_value', [None, 'html', 'markdown'])
def test_default_html_and_markdown_still_hide_real_script(format_value):
    metadata = {} if format_value is None else {'answer_format': format_value}
    assert not passed(b'<main><script>Required phrase.</script><p>Other prose.</p></main>', metadata)
    assert passed(b'<main><p>Required phrase.</p></main>', metadata)


def test_origin_mime_never_declares_answer_representation():
    assert not passed(metadata={'content_type': 'text/plain', 'mime': 'text/plain'})
    assert passed(metadata={'content_type': 'text/html', 'answer_format': 'plain_text'})


@pytest.mark.parametrize('invalid', [None, '', 'text/plain', 'auto', 'PLAIN_TEXT', 3, [], {}])
def test_invalid_explicit_format_raises_value_error(invalid):
    with pytest.raises(ValueError, match='answer_format must be'):
        score_attempt(CASE, TEXT, ok=True, metadata={'answer_format': invalid})


def test_prepared_live_saved_offline_and_standalone_are_equal():
    metadata = {'answer_format': 'plain_text', 'links': [{'text': 'Guide', 'url': 'https://example.test/guide'}]}
    prepared = answer_input(TEXT, metadata, ok=True)
    original = tuple(prepared)
    live = score_attempt(CASE, TEXT, ok=True, metadata=metadata, prepared_answer=prepared)
    saved_metadata = json.loads(json.dumps(metadata))
    offline = score_attempt(CASE, TEXT, ok=True, metadata=saved_metadata, previous_metrics=live)
    standalone = evaluate_response(CASE, TEXT, metadata=saved_metadata)['metrics']
    assert live == offline == standalone
    assert prepared == original
    assert live['answer_format'] == 'plain_text'
    assert live['normalized_named_link_count'] == 1


def test_envelope_empty_result_is_not_replaced_by_raw_json():
    artifact = b'{"success":true,"result":""}'
    metadata = {'answer_format': 'plain_text'}
    prepared = answer_input(artifact, metadata, ok=False, status_code=200)
    a = score_attempt(CASE, artifact, ok=False, metadata=metadata, status_code=200)
    b = score_attempt(CASE, artifact, ok=False, metadata=metadata, prepared_answer=prepared)
    assert a == b
    assert a['request_ok'] is True
    assert a['request_status_basis'] == 'http_200_success_envelope_empty_result'
    assert not task_passed(True, a)


def test_literal_entities_are_not_reinterpreted_as_hidden_markup():
    case = {**CASE, 'required_facts': [{'id': 'entity', 'alternatives': ['&amp;']}]}
    metrics = score_attempt(case, b'Literal &amp; text.', ok=True,
                            metadata={'answer_format': 'plain_text'})
    assert task_passed(True, metrics)


def test_cli_rescores_saved_answer_with_saved_metadata(tmp_path):
    task, response, metadata = (tmp_path / name for name in ('task.json', 'answer.txt', 'metadata.json'))
    task.write_text(json.dumps(CASE))
    response.write_bytes(TEXT)
    metadata.write_text(json.dumps({'answer_format': 'plain_text'}))
    original = hashlib.sha256(response.read_bytes()).hexdigest()
    args = [sys.executable, '-m', 'lexbench_webfetch.cli', 'score', '--task', str(task),
            '--response', str(response), '--metadata', str(metadata)]
    run = subprocess.run(args, capture_output=True, text=True, check=True)
    result = json.loads(run.stdout)
    assert result['passed']
    assert result['metrics']['answer_format'] == 'plain_text'
    assert hashlib.sha256(response.read_bytes()).hexdigest() == original
    metadata.write_text(json.dumps({'answer_format': 'invalid'}))
    run = subprocess.run(args, capture_output=True, text=True)
    assert run.returncode == 1
    assert 'answer_format must be' in run.stderr


@pytest.mark.parametrize('heading', [
    'Controllers & Actions', 'Screenshots & Gifs',
    'Returning Furniture & Select Decor', 'Literal &amp; notation',
    'Use <style> literally',
])
def test_plain_text_section_titles_preserve_literal_characters(heading):
    case = {'id': 'literal-section', 'url': 'https://example.test/manual',
            'document_structure': {
                'version': 1, 'minimum_heading_coverage': 1,
                'minimum_body_chars': 20,
                'headings': [{'id': 'section', 'text': heading, 'level': 2,
                              'body_required': True}],
            }}
    metadata = {'answer_format': 'plain_text'}
    answer = (heading + '\nA complete description of this feature.').encode()
    metrics = score_attempt(case, answer, ok=True, metadata=metadata)
    assert metrics['document_structure_pass']
    assert task_passed(True, metrics)
    missing = score_attempt(case, b'A complete description of this feature.',
                            ok=True, metadata=metadata)
    assert not missing['document_structure_pass']
    assert not task_passed(True, missing)
    empty = score_attempt(case, heading.encode(), ok=True, metadata=metadata)
    assert not task_passed(True, empty)


@pytest.mark.parametrize('pattern,passed', [('a < b', True), ('&lt;', False)])
def test_plain_text_output_patterns_match_original_characters(pattern, passed):
    case = {'id': 'raw-pattern', 'url': 'https://example.test',
            'required_patterns': [{'id': 'literal', 'pattern': pattern, 'match_scope': 'output'}]}
    metrics = score_attempt(case, b'a < b', ok=True, metadata={'answer_format': 'plain_text'})
    assert metrics['pattern_results'][0]['pass'] is passed
