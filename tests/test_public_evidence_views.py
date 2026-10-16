import hashlib
import json

import pytest

from harness.web_fetch.publication_privacy import project_evidence_view
from lexbench_webfetch.answer_projection import project_native_answer


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


def _declared_view_snapshot(tmp_path):
    original = b'Beginning\nSaved to: /Users/example/private/result.txt\nStable ending'
    public_name = 'public-evidence/answer.txt'
    public, receipt = project_evidence_view(public_name, original, role='scored_answer')
    (tmp_path/'public-evidence').mkdir()
    (tmp_path/public_name).write_bytes(public)
    receipt_raw = json.dumps(receipt).encode()
    (tmp_path/'receipt.json').write_bytes(receipt_raw)
    original_binding = {'path': 'artifacts/method/case/run.txt',
                        'sha256': hashlib.sha256(original).hexdigest()}
    alias = tmp_path/original_binding['path']
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.write_bytes(public)
    index = {'kind': 'post_score_public_evidence_view_inventory', 'provider_calls_added': 0,
             'files': [{'original_path': original_binding['path'],
                        'original_sha256': original_binding['sha256'],
                        'view_receipt': {'path': 'receipt.json', 'sha256': hashlib.sha256(receipt_raw).hexdigest()}}]}
    index_raw = json.dumps(index).encode()
    (tmp_path/'public-views.json').write_bytes(index_raw)
    profile = {'publication_evidence_views': {'path': 'public-views.json',
                                            'sha256': hashlib.sha256(index_raw).hexdigest()}}
    (tmp_path/'presentation-binding.json').write_text(json.dumps(profile))
    return original_binding, original, public, index, profile


def test_bound_view_requires_explicit_profile_and_preserves_original_identity(tmp_path):
    from harness.web_fetch.publication_privacy import read_evidence_binding, original_evidence_size
    binding, original, public, _, _ = _declared_view_snapshot(tmp_path)
    assert read_evidence_binding(tmp_path, binding).read_bytes() == public
    assert original_evidence_size(tmp_path, binding) == len(original)
    assert len(public) != len(original)
    (tmp_path/'presentation-binding.json').unlink()
    with pytest.raises(ValueError, match='binding changed'):
        read_evidence_binding(tmp_path, binding)


@pytest.mark.parametrize('defect', ['inventory_hash', 'wrong_original', 'metadata_alias', 'duplicate'])
def test_bound_view_rejects_changed_inventory_and_unrelated_authority(tmp_path, defect):
    from harness.web_fetch.publication_privacy import read_evidence_binding
    binding, _, _, index, profile = _declared_view_snapshot(tmp_path)
    if defect == 'inventory_hash':
        (tmp_path/'public-views.json').write_text('{}')
    else:
        if defect == 'wrong_original': binding['sha256'] = '0'*64
        if defect == 'metadata_alias':
            binding['path'] = 'dataset.json'
            index['files'][0]['original_path'] = binding['path']
        if defect == 'duplicate': index['files'].append(index['files'][0].copy())
        raw = json.dumps(index).encode()
        (tmp_path/'public-views.json').write_bytes(raw)
        profile['publication_evidence_views']['sha256'] = hashlib.sha256(raw).hexdigest()
        (tmp_path/'presentation-binding.json').write_text(json.dumps(profile))
    with pytest.raises(ValueError):
        read_evidence_binding(tmp_path, binding)


def test_public_excerpt_keeps_original_coordinates_without_claiming_reconstruction(tmp_path):
    from harness.web_fetch.publication_privacy import evidence_excerpt_matches
    binding, original, public, _, _ = _declared_view_snapshot(tmp_path)
    excerpt = 'Stable ending'
    start = original.decode().index(excerpt)
    sample = {'start': start, 'end': start+len(excerpt), 'text': excerpt}
    assert evidence_excerpt_matches(tmp_path, binding, sample, public.decode())
    assert public.decode()[start:start+len(excerpt)] != excerpt
    assert not evidence_excerpt_matches(tmp_path, binding, {**sample, 'text': 'invented evidence'}, public.decode())
    (tmp_path/'presentation-binding.json').unlink()
    assert not evidence_excerpt_matches(tmp_path, binding, sample, public.decode())


def test_native_custom_secret_fields_are_redacted_structurally():
    raw = json.dumps({'content': 'Stable answer', 'headers': {'authorization': 'Custom opaque-value'},
                      'meta': {'access_token': 'opaque-session', 'api_key': '<PLACEHOLDER>'},
                      'token_count': 28}).encode()
    public, receipt = project_evidence_view('public-evidence/response.json', raw, role='native_response')
    payload = json.loads(public)
    assert payload['headers']['authorization'] == '[REDACTED]'
    assert payload['meta']['access_token'] == '[REDACTED]'
    assert payload['meta']['api_key'] == '<PLACEHOLDER>'
    assert payload['content'] == 'Stable answer' and payload['token_count'] == 28
    assert receipt['redactions']['sensitive_values'] == 2


def test_text_projection_keeps_native_json_looking_body_format():
    body = '{\n  "path": "/Users/example/private/result.txt",\n  "value": 3\n}'
    raw = json.dumps({'content': body}).encode()
    declaration = {'schema_version': 1, 'content_pointer': ['content'], 'content_format': 'text'}
    public_native, _ = project_evidence_view('public-evidence/response.json', raw, role='native_response')
    public_answer, receipt = project_evidence_view('public-evidence/answer.txt', body.encode(),
                                                   role='scored_answer', content_format='text')
    assert project_native_answer(json.loads(public_native), declaration)[0].encode() == public_answer
    assert receipt['encoding'] == 'utf-8-text'
    assert b'\n  "value": 3\n' in public_answer


def test_release_inventory_requires_complete_current_evidence(tmp_path):
    from harness.web_fetch.publication_privacy import require_public_evidence_view_inventory
    original, _, _, index, profile = _declared_view_snapshot(tmp_path)
    records = [{'artifact_path': original['path'], 'metrics': {'content_sha256': original['sha256']}}]
    assert require_public_evidence_view_inventory(tmp_path, profile['publication_evidence_views'], records)
    records[0]['provider_metadata'] = {'raw_response_path': 'private_artifacts/response.bin',
                                      'raw_response_sha256': '1'*64}
    with pytest.raises(ValueError, match='omits current evidence'):
        require_public_evidence_view_inventory(tmp_path, profile['publication_evidence_views'], records)


def test_empty_native_token_is_not_a_secret_but_nonempty_value_is_masked():
    from harness.web_fetch.publication_privacy import publication_risk
    empty = b'{"metadata":{"csrf-token":""},"content":"Stable body"}'
    assert publication_risk(empty) is None
    assert project_evidence_view('public-evidence/response.json', empty, role='native_response')[0] == empty
    nonempty = b'{"metadata":{"csrf-token":"opaque-private-value"},"content":"Stable body"}'
    assert publication_risk(nonempty) == 'sensitive field value'
    public, _ = project_evidence_view('public-evidence/response.json', nonempty, role='native_response')
    assert json.loads(public)['metadata']['csrf-token'] == '[REDACTED]'
    assert publication_risk(public) is None


def test_serialized_header_name_has_no_value_but_real_header_is_sensitive():
    from harness.web_fetch.publication_privacy import publication_risk
    raw = json.dumps({'header_names': ['Authorization:', 'Proxy-Authorization:'], 'count': 3}).encode()
    assert publication_risk(raw) is None
    assert project_evidence_view('public-evidence/response.json', raw, role='native_response')[0] == raw
    private = json.dumps({'header': 'Authorization: Custom opaque-private-value'}).encode()
    assert publication_risk(private) == 'sensitive field value'
    public, _ = project_evidence_view('public-evidence/response.json', private, role='native_response')
    assert b'opaque-private-value' not in public and publication_risk(public) is None


def test_json_escaped_sensitive_values_are_still_detected():
    from harness.web_fetch.publication_privacy import publication_risk
    assert publication_risk(br'{"content":"failed at \u002fUsers\u002fexample\u002fprivate\u002fa"}') == 'machine-local user path'
    assert publication_risk(br'{"\u0061pi_key":"private-value"}') == 'sensitive field value'


def test_public_call_log_view_support_does_not_weaken_acquisition_checks(tmp_path):
    from tests.test_public_call_logs import run, ResultProvider
    from harness.web_fetch.models import ProviderResult
    from harness.web_fetch.artifacts import verify_record_public_call_logs
    binding, original, public, index, profile = _declared_view_snapshot(tmp_path)
    row = run(tmp_path/'run', ResultProvider(ProviderResult(True, original.decode())))
    root = tmp_path/'run'
    # Install only the explicit public view proof into the simulated public root.
    import shutil
    shutil.copytree(tmp_path/'public-evidence', root/'public-evidence')
    shutil.copy2(tmp_path/'receipt.json', root/'receipt.json')
    index['files'][0]['original_path'] = row['artifact_path']
    raw = json.dumps(index).encode()
    (root/'public-views.json').write_bytes(raw)
    profile['publication_evidence_views']['sha256'] = hashlib.sha256(raw).hexdigest()
    (root/'presentation-binding.json').write_text(json.dumps(profile))
    (root/row['artifact_path']).write_bytes(public)
    with pytest.raises(ValueError, match='output binding changed'):
        verify_record_public_call_logs(root, row)
    verify_record_public_call_logs(root, row, allow_public_views=True)
    (root/'presentation-binding.json').unlink()
    with pytest.raises(ValueError):
        verify_record_public_call_logs(root, row, allow_public_views=True)


def test_report_display_annotations_do_not_replace_original_score_hash(tmp_path):
    from harness.web_fetch.publication_privacy import public_render_views
    binding, _, _, _, _ = _declared_view_snapshot(tmp_path)
    row = {'attempt_id': 'p__case', 'artifact_path': binding['path'],
           'metrics': {'content_sha256': binding['sha256']}}
    before = json.dumps(row, sort_keys=True)
    fields = public_render_views(tmp_path, [row])['p__case']['scored_answer']
    assert fields['original_sha256'] == row['metrics']['content_sha256']
    assert fields['published_sha256'] != fields['original_sha256']
    assert json.dumps(row, sort_keys=True) == before


@pytest.mark.parametrize('url', ['https://example.test/item?auth_key=opaque-private',
                                 'https://example.test/wiki/Function_(math)?token=opaque-private'])
def test_query_redaction_preserves_markdown_link_boundary(url):
    from harness.web_fetch.publication_privacy import redact_url_queries
    original = '[Stable title]('+url+')'
    public, count = redact_url_queries(original)
    assert count == 1 and 'opaque-private' not in public
    assert public.startswith('[Stable title](') and public.endswith(')')
    assert public.count('(') == original.count('(')
    assert public.count(')') == original.count(')')
    assert public == original.replace('opaque-private', '[REDACTED]')
    assert redact_url_queries(public) == (public, 0)


def test_redacting_one_url_cannot_remove_adjacent_markdown_records():
    from harness.web_fetch.publication_privacy import redact_url_queries
    from lexbench_webfetch.reference_quality import markdown_links
    original = ('[Photo](https://cdn.test/image?auth_key=private-value)'
                '[Item](https://example.test/pins/123)'
                '[Other](https://example.test/pins/456)')
    public, count = redact_url_queries(original)
    assert count == 1
    assert public == original.replace('private-value', '[REDACTED]')
    assert len(markdown_links(public)) == len(markdown_links(original))
    assert 'https://example.test/pins/123' in public and 'https://example.test/pins/456' in public


def test_unwrapped_url_token_with_parenthesis_is_fully_redacted():
    from harness.web_fetch.publication_privacy import redact_url_queries
    public, count = redact_url_queries('https://example.test/api?token=private)continued')
    assert count == 1 and 'continued' not in public and 'private' not in public


def test_public_review_excerpts_use_view_policy_without_changing_task_rules():
    from harness.web_fetch.publication_privacy import project_host_metadata_files
    excerpt = 'Example /home/example/private/file.txt and stable body'
    files = {'review.json': json.dumps({'attempts': [{'adjudication_evidence': {
        'samples': [{'start': 0, 'end': len(excerpt), 'text': excerpt}]}}]}).encode(),
        'dataset.json': json.dumps({'cases': [{'required_facts': [{'alternatives': [excerpt]}]}]}).encode()}
    public, receipt = project_host_metadata_files(files, ['/Users/actual-user'], public_excerpt_views=True)
    sample = json.loads(public['review.json'])['attempts'][0]['adjudication_evidence']['samples'][0]
    assert sample['text'] == 'Example [HOST_PATH_OMITTED] and stable body'
    assert sample['start'] == 0 and sample['end'] == len(excerpt)
    assert public['dataset.json'] == files['dataset.json']
    assert receipt['files'][0]['path'] == 'review.json'


def test_query_timestamp_is_not_a_semicolonless_html_times_entity():
    from harness.web_fetch.publication_privacy import publication_risk
    original = '[Image](https://example.test/photo?token=opaque-private&timestamp=123456)'
    assert publication_risk(original.encode()) == 'sensitive URL query value'
    public, _ = project_evidence_view('public-evidence/answer.txt', original.encode(),
                                     role='scored_answer', content_format='text')
    assert b'&timestamp=123456' in public and b'opaque-private' not in public
    assert publication_risk(public) is None
    assert publication_risk(b'https&#58;//example.test/?api_key=opaque-private') == 'sensitive URL query value'


@pytest.mark.parametrize('defect', ['different_display', 'original_leak', 'private_tree'])
def test_release_rejects_wrong_display_or_private_originals(tmp_path, defect):
    from harness.web_fetch.publication_privacy import require_public_evidence_view_inventory
    binding, original, _, _, profile = _declared_view_snapshot(tmp_path)
    rows = [{'artifact_path': binding['path'], 'metrics': {'content_sha256': binding['sha256']}}]
    if defect == 'different_display': (tmp_path/binding['path']).write_bytes(b'wrong answer')
    if defect == 'original_leak': (tmp_path/binding['path']).write_bytes(original)
    if defect == 'private_tree': (tmp_path/'private_artifacts').mkdir()
    with pytest.raises(ValueError):
        require_public_evidence_view_inventory(tmp_path, profile['publication_evidence_views'], rows)


@pytest.mark.parametrize('source,expected', [
    ('https://example.test/api?apiKey&#x3D;opaque-private',
     'https://example.test/api?apiKey&#x3D;[REDACTED]'),
    ('https://example.test/api?x=1&amp;apiKey&#61;opaque-private&amp;view=full',
     'https://example.test/api?x=1&amp;apiKey&#61;[REDACTED]&amp;view=full'),
    ('https://example.test/api?apiKey&#x3D;opaque&#43;private&quot;&gt;safe',
     'https://example.test/api?apiKey&#x3D;[REDACTED]&quot;&gt;safe'),
])
def test_encoded_query_delimiters_are_redacted_without_rewriting_html(source, expected):
    from harness.web_fetch.publication_privacy import redact_url_queries, publication_risk
    public, count = redact_url_queries(source)
    assert public == expected and count == 1
    assert publication_risk(public.encode()) is None
    assert redact_url_queries(public) == (public, 0)


def test_metadata_projection_supports_disk_backed_inputs_and_outputs(tmp_path):
    from collections.abc import MutableMapping
    from harness.web_fetch.publication_privacy import project_host_metadata_files
    class Files(MutableMapping):
        def __init__(self, root): self.root, self.names = root, set()
        def __getitem__(self, key): return (self.root/key).read_bytes()
        def __setitem__(self, key, value):
            path = self.root/key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
            self.names.add(key)
        def __delitem__(self, key): (self.root/key).unlink(); self.names.remove(key)
        def __iter__(self): return iter(sorted(self.names))
        def __len__(self): return len(self.names)
    source, destination = Files(tmp_path/'original'), Files(tmp_path/'public')
    source['child.json'] = b'{"file":"/Users/example/private/input.json"}'
    source['parent.json'] = json.dumps({'child_sha256': hashlib.sha256(source['child.json']).hexdigest()}).encode()
    public, receipt = project_host_metadata_files(source, ['/Users/example'], output_store=destination)
    assert public is destination
    assert json.loads(destination['child.json'])['file'] == '[HOST_PATH_OMITTED]'
    assert json.loads(destination['parent.json'])['child_sha256'] == hashlib.sha256(destination['child.json']).hexdigest()
    assert '/Users/example' in source['child.json'].decode()
    assert len(receipt['files']) == 2


def test_metadata_path_redaction_preserves_following_json_escaped_body():
    from harness.web_fetch.publication_privacy import project_host_metadata_files
    packet = json.dumps({'content': 'Saved to /Users/example/private/a.txt\nStill visible'})
    original = json.dumps({'stdout': packet}).encode()
    public, _ = project_host_metadata_files({'metadata.json': original}, ['/Users/example'])
    inner = json.loads(json.loads(public['metadata.json'])['stdout'])
    assert inner['content'] == 'Saved to [HOST_PATH_OMITTED]\nStill visible'


def test_bilingual_download_link_displays_text_instead_of_escaped_markup():
    from bs4 import BeautifulSoup
    from pathlib import PurePosixPath
    from harness.web_fetch.report import link
    from harness.web_fetch.report_i18n import localize_html
    raw = '<html lang="zh-CN"><body>'+link('../public-evidence/a.json',
        '下载完整原生响应（公开副本）','Download complete native response (public view)')+'</body></html>'
    chinese = BeautifulSoup(raw, 'html.parser').select_one('a')
    assert chinese.text == '下载完整原生响应（公开副本）'
    english = BeautifulSoup(localize_html(raw, PurePosixPath('attempts/a.html')), 'html.parser').select_one('a')
    assert english.text == 'Download complete native response (public view)'
    assert english['href'] == '../../public-evidence/a.json'


def test_privacy_check_reuses_only_identical_bytes():
    from unittest.mock import patch
    from harness.web_fetch import publication_privacy as owner
    owner._cached_publication_risk.cache_clear()
    safe = b'Unique unchanged evidence cache fixture'
    unsafe = safe+b'\nAuthorization: Custom private-credential'
    with patch.object(owner, '_scan_publication_risk', wraps=owner._scan_publication_risk) as scan:
        assert owner.publication_risk(safe) is None
        assert owner.publication_risk(safe) is None
        assert scan.call_count == 1
        assert owner.publication_risk(unsafe) == 'sensitive field value'
        assert scan.call_count == 2
    owner._cached_publication_risk.cache_clear()
