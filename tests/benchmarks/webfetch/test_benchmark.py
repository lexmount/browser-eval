"""Input contracts: complete versions export; corrupt, partial or unsafe inputs fail."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lexbench_webfetch import benchmark as bench


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return bench.digest(path)


class PublicDefinitions(unittest.TestCase):
    def test_all_shipped_suites_are_consistent(self):
        rows = [bench.validate(row['id']) for row in bench.registry()['suites']]
        self.assertEqual(len(rows), 5)
        self.assertEqual(sum(row['case_count'] for row in rows), 550)

    def test_nested_reference_bodies_are_bound_without_being_distributed(self):
        body = 'Third-party reference paragraph. ' * 400
        source = 'Captured source lines. ' * 300
        case = {'id': 'article', 'body_requirements': {
            'regions': [{'id': 'main', 'text': body, 'source_text': source,
                         'min_coverage': 0.9}], 'min_region_coverage': 0.8}}
        public = bench.public_task(case)
        encoded = json.dumps(public)
        self.assertNotIn(body, encoded)
        self.assertNotIn(source, encoded)
        region = public['body_requirements']['regions'][0]
        self.assertEqual(region['text_binding'], {
            'sha256': hashlib.sha256(body.encode()).hexdigest(), 'chars': len(body)})
        self.assertEqual(region['min_coverage'], 0.9)
        self.assertEqual(public['body_requirements']['min_region_coverage'], 0.8)
        self.assertEqual(case['body_requirements']['regions'][0]['text'], body)

    def test_file_acceptance_metadata_is_public_without_capture_payloads(self):
        public = bench.public_task({'source_verification': {
            'mime': 'image/png', 'file_sha256': 'a'*64, 'capture_log': 'private source capture'}})
        self.assertEqual(public['source_verification'], {'mime': 'image/png', 'file_sha256': 'a'*64})
        self.assertNotIn('private source capture', json.dumps(public))

    def test_registered_revision_keeps_original_suites_and_has_new_dataset_ids(self):
        import copy
        revision = bench.read(bench.SUITES/'annotation-revisions/serialization-20261021.json')
        changed = 0
        for identifier in revision['parents']:
            _, root = bench.suite(identifier)
            data = bench.read(root/'tasks.json');before=copy.deepcopy(data)
            new = bench.apply_annotation_revision(identifier, data, revision['id'])
            self.assertEqual(data, before)
            self.assertNotEqual(new['id'], data['id'])
            self.assertEqual(len(new['cases']), len(data['cases']))
            changed += sum(a != b for a,b in zip(new['cases'],data['cases']))
        self.assertEqual(changed, len(revision['changes']))

    def test_unknown_suite_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown suite'):
            bench.validate('not-a-suite')


class FrozenInputs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.suites = self.root / 'suites'
        self.source = self.root / 'source'
        suite = self.suites / 'synthetic'
        case = {'id': 'article', 'name': 'Article', 'url': 'https://example.test',
                'category': 'web', 'task_prompt': 'Return the article',
                'expected_any': ['unique fact'], 'ground_truth_main_content': 'unique fact'}
        data = {'id': 'test', 'name': 'Test', 'cases': [case]}
        sha = write(self.source / 'dataset.json', data)
        ref_sha = write(self.source / 'sources/article.json', {'reference': 'unique fact'})
        definition = hashlib.sha256(json.dumps(case, ensure_ascii=False, sort_keys=True,
                                               separators=(',', ':')).encode()).hexdigest()
        catalog_sha = write(suite / 'dataset-catalog.json', {
            'kind': 'frozen_dataset_catalog', 'id': 'test', 'name': 'Test', 'case_count': 1, 'dataset_sha256': sha,
            'cases': [{'id': 'article', 'name': 'Article', 'url': 'https://example.test',
                       'definition_sha256': definition}]})
        write(suite / 'tasks.json', {'id': 'test', 'cases': [{k: v for k, v in case.items()
                                                          if k != 'ground_truth_main_content'}]})
        write(suite / 'source-inputs.json', {
            'schema_version': 2, 'kind': 'frozen_reference_inputs', 'id': 'synthetic-inputs',
            'catalog': 'dataset-catalog.json',
            'catalog_sha256': catalog_sha, 'dataset_sha256': sha, 'dataset': 'dataset.json',
            'files': {'dataset.json': sha}, 'trees': {'sources': {'article.json': ref_sha}}})
        self.bind_registry()
        self.patch = patch.object(bench, 'SUITES', self.suites)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def bind_registry(self):
        suite = self.suites / 'synthetic'
        write(self.suites / 'registry.json', {'schema_version': 1, 'suites': [{
            'id': 'synthetic', 'dataset_id': 'test', 'case_count': 1,
            'files': {p.name: bench.digest(p) for p in suite.iterdir()}}]})

    def test_complete_inputs_export_without_private_extras(self):
        (self.source / 'private.log').write_text('not public')
        output = self.root / 'export'
        result = bench.export('synthetic', self.source, output)
        self.assertEqual(result, output / 'dataset.json')
        self.assertEqual({p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()},
                         {'dataset.json', 'sources/article.json'})
        self.assertEqual(bench.verify_inputs('synthetic', output)['case_count'], 1)

    def annotation_revision(self):
        from lexbench_webfetch.scoring_regressions import load_scoring_regressions
        task = bench.read(self.suites/'synthetic/tasks.json')['cases'][0]
        encoded = json.dumps(task, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        data = {'schema_version': 1, 'kind': 'score_only_annotation_revision',
                'id': 'source-annotation-v2', 'parents': {'synthetic': {
                    'tasks_sha256': bench.digest(self.suites/'synthetic/tasks.json'),
                    'source_inputs_sha256': bench.digest(self.suites/'synthetic/source-inputs.json')}},
                'regression_ids': [next(iter(load_scoring_regressions()))],
                'changes': [{'suite': 'synthetic', 'case_id': 'article',
                             'before_sha256': hashlib.sha256(encoded).hexdigest(),
                             'set': {'required_facts': [{'id': 'main', 'alternatives': ['unique fact']}]},
                             'remove': []}]}
        revision_path = self.suites/'annotation-revisions/source-annotation-v2.json'
        write(revision_path, data)
        delta = [{'path': '/required_facts', 'after': [{'id': 'main', 'alternatives': ['unique fact']}]}]
        data['regression_ids'] = ['source-backed-diagnostic-source-discusses-status',
                                  'source-backed-diagnostic-status-heading']
        write(revision_path, data)
        write(self.suites/'annotation-revisions/reviews.json', {'schema_version': 1, 'revisions': {
            data['id']: {'revision_sha256': bench.digest(revision_path), 'changes': [{
                'suite': 'synthetic', 'case_id': 'article',
                'reason': 'The frozen independent reference explicitly requires unique fact.',
                'changes_sha256': hashlib.sha256(json.dumps(delta, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                'sources': [{'path': 'sources/article.json',
                             'sha256': bench.digest(self.source/'sources/article.json'),
                             'scope': 'Independent synthetic article reference'}],
                'regression_ids': data['regression_ids']}]} }})
        return data

    def test_shared_contract_references_reject_missing_or_conflicting_definitions(self):
        contract = {'version': 1, 'schema': 'quote', 'minimum_records': 1,
                    'stable_text_anchors': []}
        for variant in ('missing', 'patch', 'replacement', 'removal'):
            with self.subTest(variant=variant):
                definition = self.annotation_revision()
                definition['shared_acceptance_contracts'] = {'quotes': contract}
                change = definition['changes'][0]
                change['acceptance_contract_ref'] = 'absent' if variant == 'missing' else 'quotes'
                if variant == 'patch':
                    change['acceptance_patch'] = {}
                elif variant == 'replacement':
                    change['set']['acceptance_contract'] = contract
                elif variant == 'removal':
                    change['remove'].append('acceptance_contract')
                write(self.suites/'annotation-revisions/source-annotation-v2.json', definition)
                with self.assertRaisesRegex(ValueError, 'shared acceptance reference'):
                    bench.apply_annotation_revision('synthetic',
                        bench.read(self.suites/'synthetic/tasks.json'), 'source-annotation-v2')

    def additional_review_revision(self):
        definition = self.annotation_revision()
        self.review_source = self.root / 'independent-review'
        relative = 'review-evidence/article.html'
        target = self.review_source / relative
        target.parent.mkdir(parents=True)
        target.write_text('<main>unique fact and independently checked fact</main>')
        definition['review_inputs'] = {'synthetic': {relative: bench.digest(target)}}
        definition['changes'][0]['set']['required_facts'].append({
            'id': 'independent', 'alternatives': ['independently checked fact']})
        path = self.suites / 'annotation-revisions/source-annotation-v2.json'
        write(path, definition)
        review_path = self.suites / 'annotation-revisions/reviews.json'
        reviews = bench.read(review_path)
        review = reviews['revisions'][definition['id']]
        review['revision_sha256'] = bench.digest(path)
        entry = review['changes'][0]
        old = bench.read(self.suites/'synthetic/tasks.json')['cases'][0]
        new = {**old, **definition['changes'][0]['set']}
        entry['changes_sha256'] = hashlib.sha256(json.dumps(
            bench.annotation_changes(old, new), ensure_ascii=False,
            sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        entry['sources'] = [{'path': relative, 'sha256': bench.digest(target),
                             'scope': 'Separately versioned independent review observation'}]
        write(review_path, reviews)
        return target

    def test_independent_review_evidence_is_explicit_bound_and_not_a_parent_replacement(self):
        target = self.additional_review_revision()
        original = (self.source/'dataset.json').read_bytes()
        (self.review_source/'private.log').write_text('not an input')
        path = bench.export('synthetic', self.source, self.root/'revised',
                            revision='source-annotation-v2', review_source=self.review_source)
        report = bench.read(path.parent/'annotation-review.json')
        self.assertEqual((path.parent/'review-evidence/article.html').read_bytes(), target.read_bytes())
        self.assertFalse((path.parent/'private.log').exists())
        self.assertEqual(report['changes'][0]['source_coverage'], 'revision_review_source_declared')
        self.assertTrue(report['reference_controls']['complete'])
        self.assertEqual(report['reference_controls']['cases'][0]['reference_candidates'][-1]['source']['path'],
                         'review-evidence/article.html')
        self.assertEqual((self.source/'dataset.json').read_bytes(), original)
        self.assertEqual(bench.verify_inputs('synthetic', self.source)['case_count'], 1)

    def test_optional_evidence_export_is_not_a_pass_or_a_parent_replacement(self):
        self.additional_review_revision()
        original = (self.source/'dataset.json').read_bytes()
        path = bench.export('synthetic', self.source, self.root/'without-evidence',
                            revision='source-annotation-v2')
        report = bench.read(path.parent/'annotation-review.json')['reference_controls']
        self.assertFalse(report['complete'])
        self.assertFalse(report['changed_rules_complete'])
        self.assertEqual(report['cases'][0]['status'], 'unavailable')
        self.assertEqual(report['cases'][0]['unavailable_review_sources'],
                         ['review-evidence/article.html'])
        self.assertFalse((path.parent/'review-evidence').exists())
        self.assertEqual((self.source/'dataset.json').read_bytes(), original)
        self.assertEqual(bench.verify_inputs('synthetic', self.source)['case_count'], 1)

    def test_supplied_review_evidence_cannot_be_changed_missing_or_symlinked(self):
        target = self.additional_review_revision()
        original = target.read_bytes()
        for mode in ('changed', 'missing', 'symlink'):
            with self.subTest(mode=mode):
                if target.exists() or target.is_symlink():
                    target.unlink()
                target.write_bytes(original)
                if mode == 'changed':
                    target.write_text('changed observation')
                if mode == 'missing':
                    target.unlink()
                if mode == 'symlink':
                    target.unlink()
                    target.symlink_to(self.source/'sources/article.json')
                output = self.root/mode
                with self.assertRaises(ValueError):
                    bench.export('synthetic', self.source, output, revision='source-annotation-v2',
                                 review_source=self.review_source)
                self.assertFalse(output.exists())

    def test_review_evidence_manifest_rejects_unsafe_or_ambiguous_paths(self):
        for path in ('dataset.json', '../review-evidence/a', '/review-evidence/a',
                     'review-evidence/../dataset.json', 'review-evidence//a'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                bench.review_input_files({'parents': {'synthetic': {}},
                    'review_inputs': {'synthetic': {path: '0'*64}}}, 'synthetic')
        with self.assertRaises(ValueError):
            bench.export('synthetic', self.source, self.root/'unused', review_source=self.source)

    def test_revised_export_has_a_distinct_identity_and_preserves_frozen_inputs(self):
        self.annotation_revision()
        before = (self.source/'dataset.json').read_bytes()
        path = bench.export('synthetic', self.source, self.root/'revised', revision='source-annotation-v2')
        revised = bench.read(path)
        self.assertEqual(revised['id'], 'test--source-annotation-v2')
        self.assertEqual(revised['definition_revision']['parent_dataset_id'], 'test')
        self.assertEqual(revised['cases'][0]['required_facts'][0]['alternatives'], ['unique fact'])
        review = bench.read(path.parent/'annotation-review.json')
        self.assertEqual(review['changes'][0]['changes'][0]['path'], '/required_facts')
        self.assertTrue(review['changes'][0]['sources'])
        self.assertTrue(review['reference_controls']['complete'])
        self.assertEqual(review['reference_controls']['cases'][0]['status'], 'passed')
        self.assertEqual((self.source/'dataset.json').read_bytes(), before)
        self.assertEqual(bench.verify_inputs('synthetic', self.source)['case_count'], 1)
        old = bench.export('synthetic', self.source, self.root/'unchanged')
        self.assertEqual(old.read_bytes(), before)

    def test_revised_export_rejects_wrong_parent_unknown_controls_and_collection_changes(self):
        import copy
        definition = self.annotation_revision()
        for defect in ['parent', 'task', 'controls', 'collection']:
            with self.subTest(defect=defect):
                bad = copy.deepcopy(definition)
                if defect == 'parent': bad['parents']['synthetic']['tasks_sha256'] = '0'*64
                elif defect == 'task': bad['changes'][0]['before_sha256'] = '0'*64
                elif defect == 'controls': bad['regression_ids'] = ['unknown']
                else: bad['changes'][0]['set']['url'] = 'https://other.test/'
                write(self.suites/'annotation-revisions/source-annotation-v2.json', bad)
                output = self.root/defect
                with self.assertRaises(ValueError):
                    bench.export('synthetic', self.source, output, revision='source-annotation-v2')
                self.assertFalse(output.exists())

    def test_shared_review_reason_expands_in_export_and_rejects_invalid_references(self):
        import copy
        definition = self.annotation_revision()
        path = self.suites/'annotation-revisions/reviews.json'
        review = bench.read(path)
        revision = review['revisions'][definition['id']]
        item = revision['changes'][0]
        reason = item.pop('reason')
        item['reason_ref'] = 'content'
        revision['shared_reasons'] = {'content': reason}
        write(path, review)
        output = self.root/'shared-reason'
        bench.export('synthetic', self.source, output, revision=definition['id'])
        exported = bench.read(output/'annotation-review.json')
        self.assertEqual(exported['changes'][0]['reason'], reason)
        self.assertNotIn('reason_ref', exported['changes'][0])
        for defect in ('unknown', 'conflict', 'empty', 'non-string', 'bad-map'):
            with self.subTest(defect=defect):
                bad = copy.deepcopy(review)
                revision = bad['revisions'][definition['id']]
                item = revision['changes'][0]
                if defect == 'unknown': item['reason_ref'] = 'missing'
                elif defect == 'conflict': item['reason'] = reason
                elif defect == 'empty': revision['shared_reasons']['content'] = ' '
                elif defect == 'non-string': item['reason_ref'] = []
                else: revision['shared_reasons'] = []
                write(path, bad)
                with self.assertRaises(ValueError):
                    bench.export('synthetic', self.source, self.root/defect,
                                 revision=definition['id'])

    def test_revised_export_rejects_unreviewed_or_unproven_changes(self):
        import copy
        definition = self.annotation_revision()
        for defect in ('missing-review', 'diff', 'source', 'missing-source-binding', 'reason', 'one-direction', 'failing-control'):
            with self.subTest(defect=defect):
                self.annotation_revision()
                review_path = self.suites/'annotation-revisions/reviews.json'
                review = bench.read(review_path) if review_path.exists() else {'revisions': {}}
                if defect == 'missing-review':
                    if review_path.exists(): review_path.unlink()
                elif review['revisions']:
                    item = review['revisions']['source-annotation-v2']['changes'][0]
                    if defect == 'diff': item['changes_sha256'] = '0'*64
                    elif defect == 'source': item['sources'][0]['sha256'] = '0'*64
                    elif defect == 'missing-source-binding': item['sources'] = [{'scope': 'No bound input'}]
                    elif defect == 'reason': item['reason'] = ''
                    elif defect == 'one-direction': item['regression_ids'] = ['source-backed-diagnostic-status-heading']
                    write(review_path, review)
                output = self.root/('invalid-'+defect)
                if defect == 'failing-control':
                    from lexbench_webfetch.scoring_regressions import load_scoring_regressions
                    controls = copy.deepcopy(load_scoring_regressions())
                    controls['source-backed-diagnostic-source-discusses-status']['expected_task_pass'] = False
                    with patch('lexbench_webfetch.scoring_regressions.load_scoring_regressions', return_value=controls):
                        with self.assertRaises(ValueError):
                            bench.export('synthetic', self.source, output, revision=definition['id'])
                else:
                    with self.assertRaises(ValueError):
                        bench.export('synthetic', self.source, output, revision=definition['id'])
                self.assertFalse(output.exists())

    def test_existing_output_is_preserved(self):
        output = self.root / 'existing'
        output.mkdir()
        marker = output / 'saved'
        marker.write_text('evidence')
        with self.assertRaisesRegex(ValueError, 'new directory'):
            bench.export('synthetic', self.source, output)
        self.assertEqual(marker.read_text(), 'evidence')

    def test_missing_and_changed_inputs_fail_without_export(self):
        target = self.source / 'sources/article.json'
        original = target.read_bytes()
        for replacement in (None, b'changed'):
            with self.subTest(replacement=replacement):
                if replacement is None:
                    target.unlink()
                else:
                    target.write_bytes(replacement)
                with self.assertRaises((OSError, ValueError)):
                    bench.export('synthetic', self.source, self.root / 'output')
                self.assertFalse((self.root / 'output').exists())
                target.write_bytes(original)

    def test_symlinked_input_is_rejected(self):
        target = self.source / 'sources/article.json'
        outside = self.root / 'outside.json'
        target.rename(outside)
        target.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'Symlinked'):
            bench.verify_inputs('synthetic', self.source)

    def test_public_acceptance_cannot_differ_from_frozen_task(self):
        path = self.suites / 'synthetic/tasks.json'
        data = bench.read(path)
        data['cases'][0]['expected_any'] = ['easier requirement']
        write(path, data)
        self.bind_registry()
        with self.assertRaisesRegex(ValueError, 'Public task differs'):
            bench.verify_inputs('synthetic', self.source)

    def test_omitting_an_acceptance_requirement_is_rejected(self):
        path = self.suites / 'synthetic/tasks.json'
        data = bench.read(path)
        del data['cases'][0]['expected_any']
        write(path, data)
        self.bind_registry()
        with self.assertRaisesRegex(ValueError, 'Public task differs'):
            bench.verify_inputs('synthetic', self.source)

    def test_task_removal_and_reordering_are_rejected(self):
        path = self.suites / 'synthetic/tasks.json'
        write(path, {'id': 'test', 'cases': []})
        self.bind_registry()
        with self.assertRaisesRegex(ValueError, 'inventory'):
            bench.validate('synthetic')

    def test_public_validation_rejects_escaping_catalog(self):
        path = self.suites / 'synthetic/source-inputs.json'
        data = bench.read(path)
        data['catalog'] = '../escape.json'
        write(path, data)
        self.bind_registry()
        with self.assertRaises(ValueError):
            bench.validate('synthetic')

    def test_public_validation_rejects_missing_manifest_identity(self):
        path = self.suites / 'synthetic/source-inputs.json'
        data = bench.read(path)
        del data['id']
        write(path, data)
        self.bind_registry()
        with self.assertRaises((ValueError, KeyError)):
            bench.validate('synthetic')

    def test_changed_shipped_file_fails(self):
        (self.suites / 'synthetic/tasks.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Changed suite file'):
            bench.validate('synthetic')

    def test_escaping_paths_are_rejected(self):
        for value in ('../secret', '/absolute', 'sources/../../secret', 'sources\\secret'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bench.contained_path(self.source, value, reject_symlinks=True)



class ReferenceControls(unittest.TestCase):
    def test_reference_checks_reject_missing_required_content_and_report_missing_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            case = {'id': 'article', 'url': 'https://example.test/',
                    'required_facts': [{'id': 'body', 'alternatives': ['substantive article']}],
                    'ground_truth_main_content': 'substantive article'}
            review = {'changes': [{'case_id': 'article', 'sources': [{'path': 'dataset.json'}]}]}
            result = bench.check_revision_references({'cases': [case]}, review, source)
            self.assertTrue(result['complete'])
            self.assertFalse(result['cases'][0]['error_page_passed'])
            case['ground_truth_main_content'] = 'Home Terms Privacy'
            self.assertEqual(bench.check_revision_references({'cases': [case]}, review, source)['cases'][0]['status'], 'failed')
            review['review_inputs'] = {'review-evidence/absent.html': 'a' * 64}
            review['changes'][0]['sources'].append({
                'path': 'review-evidence/absent.html', 'sha256': 'a' * 64})
            optional = bench.check_revision_references(
                {'cases': [case]}, review, source, include_review_evidence=False)
            self.assertEqual(optional['cases'][0]['status'], 'unavailable')
            self.assertFalse(optional['cases'][0]['reference_candidates'][0]['passed'])
            self.assertFalse(optional['changed_rules_complete'])
            review['changes'][0]['sources'].pop()
            del case['ground_truth_main_content']
            missing = bench.check_revision_references({'cases': [case]}, review, source)
            self.assertFalse(missing['complete'])
            self.assertEqual(missing['cases'][0]['status'], 'unavailable')

    def test_missing_reference_cannot_hide_an_accepted_error_page(self):
        with tempfile.TemporaryDirectory() as directory:
            case = {'id': 'unchecked', 'url': 'https://example.test/', 'min_content_chars': 1}
            review = {'changes': [{'case_id': 'unchecked', 'sources': [{'path': 'dataset.json'}]}]}
            result = bench.check_revision_references({'cases': [case]}, review, Path(directory))
            self.assertTrue(result['cases'][0]['error_page_passed'])
            self.assertEqual(result['cases'][0]['status'], 'failed')
            self.assertFalse(result['complete'])
            review['review_inputs'] = {'review-evidence/absent.html': 'a' * 64}
            review['changes'][0]['sources'].append({
                'path': 'review-evidence/absent.html', 'sha256': 'a' * 64})
            result = bench.check_revision_references(
                {'cases': [case]}, review, Path(directory), include_review_evidence=False)
            self.assertTrue(result['cases'][0]['error_page_passed'])
            self.assertEqual(result['cases'][0]['status'], 'failed')


    def test_media_reference_uses_observed_urls_in_an_image_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            digest = write(source / 'observed.json', {'resource_candidates': [
                {'tag': 'a', 'attribute': 'href', 'url': 'https://example.test/download/42'}]})
            case = {'id': 'image', 'url': 'https://example.test/', 'task_kind': 'resource',
                    'content_score_applicable': False,
                    'expected_resources': [{'id': 'image', 'kind': 'image', 'urls': ['https://example.test/download/42']}],
                    'resource_output_contract': {'candidate_scope': 'image_answer_fields', 'unexpected': 'allow'}}
            review = {'changes': [{'case_id': 'image', 'sources': [{'path': 'observed.json', 'sha256': digest}]}]}
            self.assertTrue(bench.check_revision_references({'cases': [case]}, review, source)['complete'])
            case['expected_resources'][0]['urls'] = ['https://example.test/download/99']
            self.assertFalse(bench.check_revision_references({'cases': [case]}, review, source)['complete'])


if __name__ == '__main__':
    unittest.main()
