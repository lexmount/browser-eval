"""Paragraph corrections retain the frozen comparison and every evidence page."""
import copy
import json

import pytest
from bs4 import BeautifulSoup

from harness.web_search.example import example
from harness.web_search.report import build, check, main


def fixture(root):
    example(root)
    content = {'sections': [{'page': 'method', 'title': {'en': 'Method', 'zh': 'Method'},
                            'paragraphs': [{'en': 'Original explanation.',
                                            'zh': 'Original translated explanation.'}]}]}
    path = root / 'content.json'
    path.write_text(json.dumps(content), encoding='utf-8')
    build(root / 'report', source=root / 'fixture', content=path, include_english=True)
    return path, content


def test_public_paragraph_revision_preserves_all_measured_bytes_and_is_idempotent(tmp_path):
    path, content = fixture(tmp_path)
    report = tmp_path / 'report'
    before = json.loads((report / 'manifest.json').read_bytes())
    frozen = {p: p.read_bytes() for p in report.rglob('*')
              if p.is_file() and ('details' in p.parts or 'calls' in p.parts)}
    content['sections'][0]['paragraphs'][0] = {
        'en': 'Updated explanation with <symbols> & text.',
        'zh': 'Updated translated explanation.'}
    path.write_text(json.dumps(content), encoding='utf-8')
    args = ['--output', str(report), '--revise-paragraphs', str(path)]
    main(args)
    assert check(report)
    after = json.loads((report / 'manifest.json').read_bytes())
    assert after['rows'] == before['rows'] and after['bundle'] == before['bundle']
    assert after['profile'] == before['profile'] and after['webstone'] == before['webstone']
    assert after['content_revisions'][0]['provider_calls'] == 0
    assert all(p.read_bytes() == raw for p, raw in frozen.items())
    page = BeautifulSoup((report / 'en/research.html').read_text(encoding='utf-8'), 'html.parser')
    assert (page.select_one('[data-authored] > p').get_text()
            == content['sections'][0]['paragraphs'][0]['en'])
    saved = (report / 'manifest.json').read_bytes()
    main(args)
    assert (report / 'manifest.json').read_bytes() == saved


def test_same_revision_recovers_when_manifest_commit_fails(tmp_path, monkeypatch):
    from harness.web_search import report as owner

    path, content = fixture(tmp_path)
    report = tmp_path / 'report'
    original = (report / 'manifest.json').read_bytes()
    content['sections'][0]['paragraphs'][0] = {
        'en': 'Revised explanation.', 'zh': 'Revised translated explanation.'}
    path.write_text(json.dumps(content), encoding='utf-8')
    args = ['--output', str(report), '--revise-paragraphs', str(path)]

    def fail_commit(*args):
        raise OSError('Simulated full disk during manifest commit')

    with monkeypatch.context() as patch:
        patch.setattr(owner, 'write_json', fail_commit)
        with pytest.raises(OSError, match='full disk'):
            main(args)
    assert (report / 'manifest.json').read_bytes() == original
    main(args)
    assert check(report)
    assert len(json.loads((report / 'manifest.json').read_bytes())['content_revisions']) == 1


@pytest.mark.parametrize('field,value', [
    ('before_manifest_sha256', '0' * 64), ('before_content_sha256', '0' * 64),
    ('content_sha256', '0' * 64), ('changed_sections', []), ('provider_calls', 1),
    ('before_manifest_gzip_base64', 'invalid'),
    ('schema', 'unknown'), ('content_utf8', '{}'),
])
def test_check_rejects_tampered_revision_claims(tmp_path, field, value):
    path, content = fixture(tmp_path)
    report = tmp_path / 'report'
    content['sections'][0]['paragraphs'][0]['en'] = 'Revised explanation.'
    path.write_text(json.dumps(content), encoding='utf-8')
    main(['--output', str(report), '--revise-paragraphs', str(path)])
    manifest_path = report / 'manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['content_revisions'][0][field] = value
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='revision provenance'):
        check(report)


def test_multiple_revisions_keep_a_verifiable_predecessor_chain(tmp_path):
    path, content = fixture(tmp_path)
    report = tmp_path / 'report'
    for text in ('First correction.', 'Second correction.'):
        content['sections'][0]['paragraphs'][0]['en'] = text
        path.write_text(json.dumps(content), encoding='utf-8')
        main(['--output', str(report), '--revise-paragraphs', str(path)])
        assert check(report)
    manifest_path = report / 'manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['content_revisions'][0]['provider_calls'] = 1
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='revision provenance'):
        check(report)


@pytest.mark.parametrize('change', ['inputs', 'structure', 'language', 'missing-edition'])
def test_paragraph_revision_rejects_other_changes_without_partial_writes(tmp_path, change):
    path, content = fixture(tmp_path)
    report = tmp_path / 'report'
    revised = copy.deepcopy(content)
    revised['sections'][0]['paragraphs'][0]['en'] = 'Updated explanation.'
    if change == 'inputs':
        revised['comparison_methods'] = ['other-provider']
    elif change == 'structure':
        revised['sections'][0]['title']['en'] = 'Another structure'
    elif change == 'language':
        revised['sections'][0]['paragraphs'][0].pop('zh')
    else:
        page = report / 'en/research.html'
        page.write_text(page.read_text(encoding='utf-8').replace(
            'data-authored=', 'removed-authored='), encoding='utf-8')
    path.write_text(json.dumps(revised), encoding='utf-8')
    before = {p: p.read_bytes() for p in report.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):
        main(['--output', str(report), '--revise-paragraphs', str(path)])
    assert all(p.read_bytes() == raw for p, raw in before.items())
