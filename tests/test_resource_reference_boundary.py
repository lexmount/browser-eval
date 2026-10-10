"""Independent resource checks preserve declared MIME scope and output evidence."""
import json

import pytest

from harness.reporting.visual import local_report_server
from harness.web_fetch.source_capture import resource_links_main


def test_resource_head_checks_declared_mime_and_preserves_output(tmp_path):
    (tmp_path / 'image.png').write_bytes(b'synthetic source')
    (tmp_path / 'text.txt').write_text('independent source')
    dataset, output = tmp_path / 'dataset.json', tmp_path / 'check.json'
    with local_report_server(tmp_path) as origin:
        dataset.write_text(json.dumps({'cases': [{'id': 'one', 'url': origin + '/',
            'expected_resources': [{'urls': ['image.png', 'text.txt'],
                                    'mime_types': ['image/png']}]}]}))
        args = ['--dataset', str(dataset), '--output', str(output)]
        resource_links_main(args)
    rows = json.loads(output.read_text())['checks']
    assert {row['url'].rsplit('/', 1)[1]: row['pass'] for row in rows} == {
        'image.png': True, 'text.txt': False}
    before = output.read_bytes()
    dataset.unlink()
    with pytest.raises(ValueError, match='output already exists'):
        resource_links_main(args)
    assert output.read_bytes() == before


def test_resource_conflicting_acceptance_is_rejected_before_calls(tmp_path):
    dataset, output = tmp_path / 'dataset.json', tmp_path / 'check.json'
    dataset.write_text(json.dumps({'cases': [{'url': 'https://example.invalid/',
        'expected_resources': [{'urls': ['one'], 'mime_types': ['image/png']},
                               {'urls': ['one'], 'mime_types': ['text/html']}]}]}))
    with pytest.raises(ValueError, match='conflicting'):
        resource_links_main(['--dataset', str(dataset), '--output', str(output)])
    assert not output.exists()
