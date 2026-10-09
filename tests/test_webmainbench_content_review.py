from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.web_fetch.review_execution import (
    run_content_packet, sha, validate_content_review, write_content_document,
)


class ContentReviewTests(unittest.TestCase):
    def test_resume_reuses_review_only_while_all_evidence_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            sources = {}
            for name in ['source', 'outline', 'reference', 'target', 'chromium']:
                path = work / name
                path.write_text('literal content')
                sources[name] = {'path': str(path), 'sha256': sha(path)}
            case = {'case_id': 'one', 'target': sources['target'],
                    'comparisons': {'chromium': sources['chromium']}}
            for name, key in [('source', 'source_html'), ('outline', 'source_outline'),
                              ('reference', 'reference')]:
                case[key] = sources[name]['path']
                case[name + '_sha256'] = sources[name]['sha256']
            packet = work / 'packet.json'
            write_content_document(packet, {'cases': [case]})
            item = {'path': str(packet), 'sha256': sha(packet)}
            review = {'cases': [{'case_id': 'one', 'page_purpose': 'article',
                                 'expected_content': 'body', 'verdict': 'acceptable',
                                 'coverage_evidence': ['beginning', 'ending'],
                                 'reference_assessment': 'reference covers the body', 'issues': []}]}
            write_content_document(work / 'reviews' / packet.name, review)
            with patch('harness.web_fetch.review_execution.subprocess.run') as call:
                self.assertEqual(run_content_packet(item, work, 'model', 'codex'), review)
                call.assert_not_called()
                for name in sources:
                    path = Path(sources[name]['path'])
                    path.write_text('changed')
                    with self.assertRaisesRegex(ValueError, 'Review input changed'):
                        run_content_packet(item, work, 'model', 'codex')
                    path.write_text('literal content')
                call.assert_not_called()

    def test_review_cannot_omit_or_repeat_a_page(self):
        packet = {'cases': [{'case_id': 'one'}, {'case_id': 'two'}]}
        for rows in [[], [{'case_id': 'one'}], [{'case_id': 'one'}, {'case_id': 'one'}]]:
            with self.assertRaisesRegex(ValueError, 'exact page inventory'):
                validate_content_review({'cases': rows}, packet)

    def test_review_cannot_invent_an_output_quote(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'output.md'
            path.write_text('actual content')
            packet = {'cases': [{'case_id': 'one', 'target': {'path': str(path)}}]}
            review = {'cases': [{'case_id': 'one', 'page_purpose': 'article',
                                 'expected_content': 'body', 'verdict': 'defect',
                                 'coverage_evidence': ['beginning', 'ending'],
                                 'reference_assessment': 'reference covers the body',
                                 'issues': [{'description': 'problem', 'source_locator': 'p',
                                             'output_quote': 'invented text'}]}]}
            with self.assertRaisesRegex(ValueError, 'not literal'):
                validate_content_review(review, packet)

    def test_review_requires_reference_assessment(self):
        packet = {'cases': [{'case_id': 'one'}]}
        review = {'cases': [{'case_id': 'one', 'page_purpose': 'article',
                             'expected_content': 'body', 'verdict': 'acceptable',
                             'coverage_evidence': ['beginning', 'ending'],
                             'reference_assessment': '', 'issues': []}]}
        with self.assertRaisesRegex(ValueError, 'source-grounded'):
            validate_content_review(review, packet)


def content_fixture(root):
    import json
    from harness.web_fetch.review_execution import sha

    evidence, correction = root / 'evidence', root / 'correction'
    evidence.mkdir(); correction.mkdir()
    (evidence / 'sources').mkdir(); (evidence / 'references').mkdir()
    cases, rows = [], []
    for name in ('one', 'two'):
        source = evidence / 'sources' / f'{name}.html.txt'
        reference = evidence / 'references' / f'{name}.md'
        source.write_text('<main>synthetic body</main><script>hidden</script>')
        reference.write_text('synthetic body')
        cases.append({'id': name, 'url': 'https://example.invalid/' + name,
                      'input_html_sha256': sha(source), 'reference_sha256': sha(reference)})
        for provider in ('custom', 'comparison'):
            path = evidence / f'{provider}-{name}.md'
            path.write_text('synthetic body')
            rows.append({'provider': provider, 'case_id': name, 'artifact_path': path.name,
                         'output_sha256': sha(path)})
    (evidence / 'dataset.json').write_text(json.dumps({'cases': cases}))
    (evidence / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    (correction / 'results.jsonl').write_text('')
    (correction / 'certificate.json').write_text('{}')
    return evidence, correction


def test_content_preparation_uses_declared_provider_and_population(tmp_path):
    import json
    from harness.web_fetch.review_execution import prepare_content_review

    evidence, correction = content_fixture(tmp_path)
    work = tmp_path / 'work'
    manifest = prepare_content_review(evidence, correction, work, provider='custom')
    assert manifest['case_count'] == 2 and manifest['provider'] == 'custom'
    packet = json.loads(Path(manifest['packets'][0]['path']).read_text())
    assert {row['case_id'] for row in packet['cases']} == {'one', 'two'}
    assert all(set(row['comparisons']) == {'comparison'} for row in packet['cases'])
    assert all('hidden' not in Path(row['source_outline']).read_text() for row in packet['cases'])


def test_prepare_only_never_runs_reviewer_and_rejects_changed_resume(tmp_path):
    import pytest
    from harness.web_fetch.review_execution import content_review_main

    evidence, correction = content_fixture(tmp_path)
    argv = ['--evidence', str(evidence), '--correction', str(correction),
            '--work', str(tmp_path / 'work'), '--provider', 'custom', '--prepare-only']
    with patch('harness.web_fetch.review_execution.subprocess.run') as call:
        content_review_main(argv)
        content_review_main(argv)
        with pytest.raises(ValueError, match='workspace does not match'):
            content_review_main([*argv, '--provider', 'comparison'])
        (correction / 'certificate.json').write_text('{"changed":true}')
        with pytest.raises(ValueError, match='workspace does not match'):
            content_review_main(argv)
        call.assert_not_called()


def test_missing_target_and_duplicate_results_fail_before_packets(tmp_path):
    import pytest
    from harness.web_fetch.review_execution import prepare_content_review

    evidence, correction = content_fixture(tmp_path)
    work = tmp_path / 'work'
    with pytest.raises(ValueError, match='Target provider must cover'):
        prepare_content_review(evidence, correction, work, provider='absent')
    results = evidence / 'results.jsonl'
    row = results.read_text().splitlines()[0]
    results.write_text(results.read_text() + row + '\n')
    with pytest.raises(ValueError, match='duplicate or unexpected'):
        prepare_content_review(evidence, correction, work, provider='custom')
    assert not (work / 'packets').exists()


def test_input_drift_during_reviewer_execution_cannot_install_review(tmp_path):
    import json
    import subprocess
    import pytest
    from harness.web_fetch.review_execution import prepare_content_review, run_content_packet

    evidence, correction = content_fixture(tmp_path)
    work = tmp_path / 'work'
    manifest = prepare_content_review(evidence, correction, work, provider='custom')
    item = manifest['packets'][0]

    def run(command, **kwargs):
        raw = Path(command[command.index('--output-last-message') + 1])
        raw.write_text(json.dumps({'cases': []}))
        (evidence / 'sources' / 'one.html.txt').write_text('changed source')
        return subprocess.CompletedProcess(command, 0)

    with patch('harness.web_fetch.review_execution.subprocess.run', side_effect=run):
        with pytest.raises(ValueError, match='Review input changed'):
            run_content_packet(item, work, 'synthetic-model', 'synthetic-reviewer')
    assert not (work / 'reviews' / Path(item['path']).name).exists()
