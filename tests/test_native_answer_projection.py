"""Projection defaults cannot introduce facts absent from native responses."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.native_projection import (
    native_projection_regressions, project_native_answer, require_native_answer_projection,
)
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


@pytest.mark.parametrize('declaration,field', [
    ([], 'declaration'),
    ({'schema_version': True}, 'Unsupported'),
    ({'schema_version': 2, 'answer_fields': []}, 'answer_fields'),
    ({'schema_version': 2, 'result_fields': []}, 'result_fields'),
    ({'schema_version': 2, 'answer_fields': {'images': None}}, 'answer_fields.images'),
    ({'schema_version': 2, 'result_fields': {'title': {'pointer': [True]}}}, 'result_fields.title.pointer'),
    ({'schema_version': 2, 'answer_fields': {'images': {'pointer': [-1]}}}, 'answer_fields.images.pointer'),
    ({'schema_version': 2, 'files': {'pointer': 'file'}}, 'files.pointer'),
    ({'schema_version': 2, 'answer_fields': {'videos': {'pointer': ['videos'], 'legacy': []}}}, 'answer_fields.videos.legacy'),
    ({'schema_version': 2, 'result_fields': {'title': {'default': None}}}, 'result_fields.title.pointer'),
])
def test_malformed_projection_names_the_bad_field(declaration, field):
    from harness.web_fetch.native_projection import require_native_projection_schema
    with pytest.raises(ValueError, match=field):
        require_native_projection_schema(declaration)

@pytest.mark.parametrize('field,default', [
    ('images', ['https://example.test/forged.png']),
    ('links', [{'text': 'Invented', 'href': 'https://example.test/forged'}]),
    ('extras', {'images': ['https://example.test/forged.png']}),
    ('videos', ['https://example.test/forged.mp4']),
])
def test_nonempty_defaults_cannot_supply_native_answers(field, default):
    declaration = {'schema_version': 2, 'content_format': 'text',
                   'content_pointer': ['content'], 'answer_fields': {
                       field: {'pointer': [field], 'default': default}}}
    case = {'url': 'https://example.test/page', 'expected_any': [],
            'task_kind': 'resource', 'content_score_applicable': False,
            'expected_resources': [{'id': 'asset', 'urls': ['https://example.test/forged.png']}]}
    with pytest.raises(ValueError, match='defaults must be empty'):
        require_native_answer_projection('', {'native_answer_projection': declaration, field: default},
                                         b'{"content":""}', request_url=case['url'])


@pytest.mark.parametrize('present', [False, True])
def test_empty_default_keeps_missing_and_native_resource_distinct(present):
    url = 'https://example.test/native.png'
    payload = {'content': '', **({'images': [url]} if present else {})}
    declaration = {'schema_version': 2, 'content_format': 'text',
                   'content_pointer': ['content'], 'answer_fields': {
                       'images': {'pointer': ['images'], 'default': []}}}
    content, fields = project_native_answer(payload, declaration)
    case = {'url': 'https://example.test/page', 'expected_any': [],
            'task_kind': 'resource', 'content_score_applicable': False,
            'expected_resources': [{'id': 'asset', 'urls': [url]}]}
    metadata = {'native_answer_projection': declaration, **fields}
    require_native_answer_projection(content, metadata, json.dumps(payload).encode(), request_url=case['url'])
    metrics = score_attempt(case, content.encode(), ok=True, metadata=metadata)
    assert task_passed(True, metrics, 'resource') is present


@pytest.mark.parametrize('spec,payload,message', [
    ({}, {}, 'Unknown native file projection'),
    ({'format': 'raw_bytes'}, {}, 'requires bytes'),
    ({'format': 'base64_file', 'pointer': ['file']}, {'file': 7}, 'requires text'),
    ({'format': 'base64_file', 'pointer': ['file']}, {'file': '%%%bad'}, 'invalid base64'),
    ({'format': 'worker_files', 'pointer': ['files']}, {'files': {}}, 'require objects'),
    ({'format': 'worker_files', 'pointer': ['files']}, {'files': [None]}, 'require objects'),
    ({'format': 'worker_files', 'pointer': ['files']}, {'files': [{}]}, 'require objects'),
    ({'format': 'worker_files', 'pointer': ['files']}, {'files': [{'base64': 7}]}, 'require objects'),
])
def test_native_file_projection_rejects_invalid_shapes(spec, payload, message):
    from harness.web_fetch.native_projection import project_native_files
    with pytest.raises(ValueError, match=message):
        project_native_files(payload, {'files': spec})


@pytest.mark.parametrize('spec,payload,expected', [
    ({'format': 'raw_bytes'}, b'PDF bytes', [b'PDF bytes']),
    ({'format': 'raw_bytes'}, b'', []),
    ({'format': 'base64_file', 'pointer': ['file']}, {'file': 'cGRm'}, [b'pdf']),
    ({'format': 'base64_file', 'pointer': ['file']}, {'file': ''}, []),
    ({'format': 'worker_files', 'pointer': ['files']}, {'files': [{'base64': 'cGRm'}]}, [b'pdf']),
    ({'format': 'worker_files', 'pointer': ['files']}, {'files': []}, []),
])
def test_native_file_projection_decodes_only_declared_bytes(spec, payload, expected):
    from harness.web_fetch.native_projection import project_native_files
    assert project_native_files(payload, {'files': spec}) == expected


def test_optional_result_fields_preserve_empty_but_reject_invented_defaults():
    from harness.web_fetch.native_projection import project_native_result_fields
    declaration = {'schema_version': 2, 'result_fields': {
        'title': {'pointer': ['title'], 'format': 'text_or_none'}}}
    assert project_native_result_fields({}, declaration) == {'title': None, 'final_url': None}
    declaration['result_fields']['title']['default'] = None
    assert project_native_result_fields({}, declaration) == {'title': None, 'final_url': None}
    assert project_native_result_fields({'title': 'Native title'}, declaration)['title'] == 'Native title'
    declaration['result_fields']['title']['default'] = 'Invented title'
    with pytest.raises(ValueError, match='defaults must be empty'):
        project_native_result_fields({}, declaration)


@pytest.mark.parametrize('attribute,expected', [
    ('/image.png?crop=10,20 1x, /large.png 2x',
     ['https://example.test/image.png?crop=10,20', 'https://example.test/large.png']),
    ('data:image/png;base64,YQ== 1x, /large.png 2x',
     ['data:image/png;base64,YQ==', 'https://example.test/large.png']),
    ('/one.png, /two.png', ['https://example.test/one.png', 'https://example.test/two.png']),
])
def test_attribute_projection_preserves_commas_inside_urls(attribute, expected):
    declaration = {'schema_version': 2, 'content_format': 'empty', 'answer_fields': {
        'images': {'pointer': ['elements'], 'format': 'html_attribute_urls', 'srcset_parser': 'html-candidates-v1',
                   'base_url': 'https://example.test/page'}}}
    payload = {'elements': [{'results': [{'attributes': [{'name': 'srcset', 'value': attribute}]}]}]}
    assert project_native_answer(payload, declaration)[1]['images'] == sorted(expected)


CONTROLS = list(native_projection_regressions().values())


@pytest.mark.parametrize('row', CONTROLS, ids=lambda row: row['id'])
def test_retained_native_projection_controls(row):
    metadata = row.get('metadata', {})
    def validate():
        require_native_answer_projection(row['artifact'], metadata, row['native_response'].encode(),
            title=row.get('title'), final_url=row.get('final_url'), request_url=row['case']['url'])
    if row.get('expected_validation_error'):
        with pytest.raises(ValueError, match=row['expected_validation_error']):
            validate()
        assert row['expected_task_pass'] is False
        return
    validate()
    metrics = score_attempt(row['case'], row['artifact'].encode(), ok=row['ok'], metadata=metadata,
        title=row.get('title'), final_url=row.get('final_url'))
    assert task_passed(row['ok'], metrics, row['case'].get('task_kind', 'body')) == row['expected_task_pass']
    for key, expected in row.get('expected_metrics', {}).items():
        assert metrics.get(key) == expected, (row['id'], key)


@pytest.mark.parametrize('row', CONTROLS, ids=lambda row: row['id'])
def test_projection_controls_cover_public_persistence_and_saved_verification(tmp_path, row):
    from harness.web_fetch.models import ProviderResult
    from harness.web_fetch.native_routes import verify_saved_native_answer
    from harness.web_fetch.runner import persist_result_artifacts
    raw = row['native_response'].encode()
    result = ProviderResult(row['ok'], row['artifact'], title=row.get('title'), final_url=row.get('final_url'),
        metadata={**deepcopy(row.get('metadata', {})), '_raw_response_b64': base64.b64encode(raw).decode(),
                  'raw_response_sha256': hashlib.sha256(raw).hexdigest(), 'raw_response_bytes': len(raw)})
    artifact = Path('artifacts/answer.txt')
    if row.get('expected_validation_error'):
        with pytest.raises(ValueError, match=row['expected_validation_error']):
            persist_result_artifacts(tmp_path, artifact, result, request_url=row['case']['url'])
        assert not (tmp_path / artifact).exists()
        return
    persist_result_artifacts(tmp_path, artifact, result, request_url=row['case']['url'])
    record = {'ok': result.ok, 'url': row['case']['url'], 'title': result.title, 'final_url': result.final_url,
        'artifact_path': str(artifact), 'provider_metadata': result.metadata,
        'metrics': {'content_sha256': hashlib.sha256(row['artifact'].encode()).hexdigest()}}
    verify_saved_native_answer(tmp_path, record)


@pytest.mark.parametrize('source, current_parser', [
    ('b17fc68566409232d3840f69aa4511c6dcdfd154c774eb169ea43787659496f7', False),
    ('bd08ad5ba4a6ef6962391ef0ea6f3ed7cccaac3294708de87cfd1b9e95d6969c', False),
    ('7e1b468e981cfe1da89d443047aff7facd841cd03f00bfc4b016ed67ba292da3', True),
    ('d042394ea0e8c3aa7aed5851663eaf73772fc4357781999e20a87f48e857e00a', False),
])
def test_historical_srcset_replay_is_source_bound_and_exact(tmp_path, source, current_parser):
    from harness.web_fetch.native_routes import verify_saved_native_answer
    payload = {'elements': [{'results': [{'attributes': [
        {'name': 'srcset', 'value': '/image?crop=10,20 1x, /large.png 2x'}]}]}]}
    declaration = {'schema_version': 2, 'content_format': 'empty', 'answer_fields': {
        'images': {'pointer': ['elements'], 'format': 'html_attribute_urls',
                   'base_url': 'https://example.test/page'}}}
    expected = ['https://example.test/20', 'https://example.test/image?crop=10',
                'https://example.test/large.png']
    legacy = expected
    current = ['https://example.test/image?crop=10,20', 'https://example.test/large.png']
    expected = current if current_parser else legacy
    raw = json.dumps(payload).encode()
    (tmp_path / 'answer.txt').write_bytes(b'')
    (tmp_path / 'raw.json').write_bytes(raw)
    metadata = {'native_answer_projection': declaration, 'images': expected,
                'raw_response_path': 'raw.json', 'raw_response_sha256': hashlib.sha256(raw).hexdigest()}
    record = {'ok': True, 'url': 'https://example.test/page', 'artifact_path': 'answer.txt',
              'provider_metadata': metadata, 'metrics': {
                  'content_sha256': hashlib.sha256(b'').hexdigest(), 'scoring_source_sha256': source}}
    frozen = deepcopy(record)
    verify_saved_native_answer(tmp_path, record)
    assert record == frozen
    assert (tmp_path / 'raw.json').read_bytes() == raw
    # Neither parser's answer may rescue a receipt bound to the other parser.
    metadata['images'] = legacy if current_parser else current
    with pytest.raises(ValueError, match='differs from its declared native field projection'):
        verify_saved_native_answer(tmp_path, record)
    metadata['images'] = expected
    record['metrics']['scoring_source_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='lacks a recognized parser binding'):
        verify_saved_native_answer(tmp_path, record)
    record['metrics']['scoring_source_sha256'] = source
    declaration['answer_fields']['images']['srcset_parser'] = 'html-candidates-v1'
    if current_parser:
        verify_saved_native_answer(tmp_path, record)
    else:
        with pytest.raises(ValueError, match='differs from its bound semantics'):
            verify_saved_native_answer(tmp_path, record)


@pytest.mark.parametrize('parser', [None, '', 1, True, 'future-v9', 'comma-split-v1'])
def test_current_srcset_rejects_absent_or_unknown_parser(parser):
    spec = {'pointer': ['elements'], 'format': 'html_attribute_urls',
            'base_url': 'https://example.test/'}
    if parser is not None:
        spec['srcset_parser'] = parser
    declaration = {'schema_version': 2, 'content_format': 'empty', 'answer_fields': {'images': spec}}
    payload = {'elements': [{'results': [{'attributes': [{'name': 'srcset', 'value': '/one.png 1x'}]}]}]}
    with pytest.raises(ValueError, match='srcset'):
        project_native_answer(payload, declaration)


def test_acquisition_declares_current_srcset_semantics():
    from harness.web_fetch.native_projection import native_answer_projection
    declaration = native_answer_projection('cloudflare_markdown',
        {'track': 'image', 'task_kind': 'resource', 'url': 'https://example.test/'},
        {'success': True, 'result': []}, 200)
    assert declaration['answer_fields']['images']['srcset_parser'] == 'html-candidates-v1'


def test_historical_selector_policy_requires_exact_trusted_identity():
    from harness.web_fetch.native_projection import native_projection_policy_version
    digest = '7a8e133780f75c44c4cc0127d1d69a784f4b00c4cd693a1e35571f22ad95b7bf'
    policy = {'kind': 'native-answer-selection', 'version': 1, 'provider_type': 'browserbase_fetch',
              'source_sha256': digest, 'source_path': 'native-answer-policies/'+digest+'.py'}
    assert native_projection_policy_version(policy, 'browserbase_fetch') == 1
    for key, value in [('source_sha256', '0'*64), ('version', 2), ('provider_type', 'firecrawl'),
                       ('source_path', 'other.py'), ('kind', 'other')]:
        with pytest.raises(ValueError, match='differs from its pre-call freeze'):
            native_projection_policy_version({**policy, key: value}, 'browserbase_fetch')


def test_historical_selector_replays_old_file_scope_and_checks_saved_source(tmp_path, monkeypatch):
    from harness.web_fetch import native_projection as projection
    from harness.web_fetch.native_routes import verify_saved_native_answer
    source = b'# synthetic trusted legacy selector fixture\n'
    digest = hashlib.sha256(source).hexdigest()
    monkeypatch.setitem(projection.HISTORICAL_SELECTION_SOURCES, digest, 1)
    policy = {'kind': 'native-answer-selection', 'version': 1, 'provider_type': 'browserbase_fetch',
              'source_sha256': digest, 'source_path': 'native-answer-policies/'+digest+'.py'}
    source_path = tmp_path / policy['source_path']
    source_path.parent.mkdir()
    source_path.write_bytes(source)
    case = {'id': 'file', 'url': 'https://example.test/file.pdf', 'task_kind': 'file'}
    payload = {'content': 'JVBERg==', 'encoding': 'base64', 'statusCode': 200}
    raw = json.dumps(payload).encode()
    (tmp_path / 'dataset.json').write_text(json.dumps({'cases': [case]}))
    (tmp_path / 'raw.json').write_bytes(raw)
    (tmp_path / 'answer.txt').write_text(payload['content'])
    old = projection.native_answer_projection('browserbase_fetch', case, payload, 200, policy_version=1)
    metadata = {'native_answer_projection': old, 'api_http_status': 200,
                'native_route_receipt': {'answer_selection_policy': policy, 'answer_fields': ['content']},
                'raw_response_path': 'raw.json', 'raw_response_sha256': hashlib.sha256(raw).hexdigest()}
    record = {'case_id': 'file', 'provider_type': 'browserbase_fetch', 'url': case['url'], 'ok': False,
              'title': None, 'final_url': None, 'artifact_path': 'answer.txt', 'provider_metadata': metadata,
              'metrics': {'content_sha256': hashlib.sha256(payload['content'].encode()).hexdigest()}}
    verify_saved_native_answer(tmp_path, record)
    frozen = deepcopy(record)
    source_path.write_bytes(source + b'tampered')
    with pytest.raises(ValueError):
        verify_saved_native_answer(tmp_path, record)
    source_path.write_bytes(source)
    metadata['native_answer_projection'] = projection.native_answer_projection('browserbase_fetch', case, payload, 200)
    assert metadata['native_answer_projection']['files']
    with pytest.raises(ValueError, match='differs from its pre-call selection policy'):
        verify_saved_native_answer(tmp_path, record)
    metadata['native_answer_projection'] = old
    assert record == frozen
