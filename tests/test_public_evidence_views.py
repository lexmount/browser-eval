import hashlib
import json

import pytest

from harness.web_fetch.publication_privacy import project_evidence_view
from harness.web_fetch.response_input import project_native_answer


def test_native_envelope_view_redacts_decoded_paths_without_changing_other_fields():
    original = json.dumps({'content': 'Article\nSaved to: /Users/example/private/run/body.txt\nDone',
                           'elapsed_ms': 1234.5, 'count': 7, 'success': True}, ensure_ascii=True).encode()
    declaration = {'schema_version': 1, 'content_pointer': ['content'], 'content_format': 'text'}
    scored, _ = project_native_answer(json.loads(original), declaration)
    public, receipt = project_evidence_view('public-evidence/native/response.json', original,
                                           role='native_response')
    payload = json.loads(public)
    assert payload['elapsed_ms'] == 1234.5 and payload['count'] == 7 and payload['success'] is True
    assert payload['content'] == 'Article\nSaved to: [HOST_PATH_OMITTED]\nDone'
    assert 'private/run' not in public.decode()
    assert '/Users/example' in scored
    assert receipt['original_sha256'] == hashlib.sha256(original).hexdigest()
    assert receipt['published_sha256'] == hashlib.sha256(public).hexdigest()
    assert receipt['original_sha256'] != receipt['published_sha256']
    assert receipt['provider_calls_added'] == 0


def test_answer_and_envelope_public_views_preserve_native_field_projection():
    raw = json.dumps({'body': 'Answer\nSaved to: /Users/example/private/file.md\nEnd'}).encode()
    declaration = {'schema_version': 1, 'content_pointer': ['body'], 'content_format': 'text'}
    answer, _ = project_native_answer(json.loads(raw), declaration)
    public_response, _ = project_evidence_view('public-evidence/response.json', raw, role='native_response')
    public_answer, receipt = project_evidence_view('public-evidence/answer.txt', answer.encode(), role='scored_answer')
    assert project_native_answer(json.loads(public_response), declaration)[0].encode() == public_answer
    assert receipt['role'] == 'scored_answer'
    assert receipt['original_sha256'] == hashlib.sha256(answer.encode()).hexdigest()


def test_view_handles_json_escaped_paths_and_retains_clean_original_bytes():
    raw = br'{"content":"failed at \u002fUsers\u002fexample\u002fprivate\u002frun.txt","count":4}'
    public, _ = project_evidence_view('public-evidence/response.json', raw, role='native_response')
    assert json.loads(public)['content'] == 'failed at [HOST_PATH_OMITTED]'
    clean = b'{ "count": 4, "content": "Normal body" }\n'
    assert project_evidence_view('public-evidence/response.json', clean, role='native_response')[0] == clean


def test_view_hides_explicit_temp_paths_and_credential_shapes():
    raw = b'Failed at /private/tmp/example/session.log\nkey: ghp_' + b'X'*32
    public, receipt = project_evidence_view('public-evidence/answer.txt', raw, role='scored_answer',
                                           host_prefixes=['/private/tmp'])
    assert b'/private/tmp' not in public and b'example/session' not in public
    assert b'ghp_' not in public
    assert receipt['redactions']['credential_shape'] == 1


@pytest.mark.parametrize('name', ['private_artifacts/response.bin', '../response.txt',
                                  '/public-evidence/response.txt', 'public-evidence/.env'])
def test_view_rejects_paths_that_could_confuse_original_and_public_evidence(name):
    with pytest.raises(ValueError, match='separate safe'):
        project_evidence_view(name, b'answer', role='scored_answer')


def test_view_does_not_accept_unknown_roles_or_binary_guessing():
    with pytest.raises(ValueError, match='role'):
        project_evidence_view('public-evidence/a.txt', b'a', role='reference')
    with pytest.raises(UnicodeDecodeError):
        project_evidence_view('public-evidence/a.txt', b'\xff\xfe', role='native_response')


def test_redaction_cannot_merge_distinct_native_metadata_fields():
    raw = json.dumps({'/Users/one/a': 1, '/Users/two/b': 2}).encode()
    with pytest.raises(ValueError, match='merge distinct'):
        project_evidence_view('public-evidence/response.json', raw, role='native_response')


def test_saved_public_view_verifier_rejects_changed_bytes_or_false_identity(tmp_path):
    from harness.web_fetch.publication_privacy import require_public_evidence_view
    name = 'public-evidence/answer.txt'
    public, receipt = project_evidence_view(name, b'Answer /Users/example/private/a.txt', role='scored_answer')
    path = tmp_path/name
    path.parent.mkdir()
    path.write_bytes(public)
    proof = tmp_path/'view-receipt.json'
    def binding():
        proof.write_text(json.dumps(receipt))
        return {'path': proof.name, 'sha256': hashlib.sha256(proof.read_bytes()).hexdigest()}
    assert require_public_evidence_view(tmp_path, binding()) == receipt
    path.write_bytes(b'different')
    with pytest.raises(ValueError, match='binding changed'):
        require_public_evidence_view(tmp_path, binding())
    path.write_bytes(public)
    receipt['original_sha256'] = receipt['published_sha256']
    with pytest.raises(ValueError, match='identity does not match'):
        require_public_evidence_view(tmp_path, binding())


def test_public_view_verifier_rejects_symlink_even_with_matching_bytes(tmp_path):
    from harness.web_fetch.publication_privacy import require_public_evidence_view
    public, receipt = project_evidence_view('public-evidence/a.txt', b'answer', role='scored_answer')
    (tmp_path/'public-evidence').mkdir()
    (tmp_path/'original.txt').write_bytes(public)
    (tmp_path/'public-evidence/a.txt').symlink_to(tmp_path/'original.txt')
    raw = json.dumps(receipt).encode()
    (tmp_path/'receipt.json').write_bytes(raw)
    with pytest.raises(ValueError):
        require_public_evidence_view(tmp_path, {'path': 'receipt.json', 'sha256': hashlib.sha256(raw).hexdigest()})
