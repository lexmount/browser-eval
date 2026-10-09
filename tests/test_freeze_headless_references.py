import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from harness.web_fetch.body_coverage import body_requirements
from harness.web_fetch.reference_freeze import (
    _digest_json,
    _require_promotion,
    compute_reference_update,
    main,
)
from harness.web_fetch.scoring_regressions import build_correction_provenance
from harness.web_fetch.reference_freeze import prepare_promotion, promote

from tests.reference_freeze_fixture import ROOT_CAUSE, REGRESSIONS, create_promotion_inputs


def provenance():
    return build_correction_provenance(
        'reference-evidence',ROOT_CAUSE,REGRESSIONS)


class FreezeHeadlessReferenceTests(unittest.TestCase):
    def setUp(self):
        self.change={
            'case_id':'stable-wikipedia-ai-revision',
            'affected_scope':['ground_truth_main_content','body_requirements'],
            'old_reference_sha256':'a'*64,
            'new_reference_sha256':'b'*64,
            'old_body_requirements_sha256':_digest_json({'old':True}),
            'new_body_requirements_sha256':_digest_json({'new':True}),
            'source_revision':{
                'url':'https://en.wikipedia.org/w/index.php?title=AI&oldid=123',
                'revision':'oldid:123','source_snapshot_sha256':'c'*64},
        }

    def test_changed_reference_fails_closed_without_explicit_promotion(self):
        with self.assertRaisesRegex(ValueError,'explicit --reference-promotion'):
            _require_promotion(None,dataset_sha256='d'*64,
                               capture_audit_sha256='e'*64,
                               candidate_dataset_sha256='f'*64,
                               changes=[self.change])

    def test_promotion_binds_reference_body_source_revision_and_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'promotion.json'
            receipt={'schema_version':2,'kind':'reference_freeze_promotion',
                     'input_dataset_sha256':'d'*64,'capture_audit_sha256':'e'*64,
                     'candidate_dataset_sha256':'f'*64,
                     'changes':[self.change],
                     'approved_by':'independent-reference-review',
                     'approved_at':'2026-09-11T00:00:00+00:00',
                     'task_decision_changes':[],
                     'correction_provenance':provenance()}
            path.write_text(json.dumps(receipt))
            self.assertEqual(_require_promotion(
                path,dataset_sha256='d'*64,capture_audit_sha256='e'*64,
                candidate_dataset_sha256='f'*64,changes=[self.change]),receipt)
            tampered=json.loads(json.dumps(receipt))
            tampered['changes'][0]['source_revision']['revision']='oldid:124'
            path.write_text(json.dumps(tampered))
            with self.assertRaisesRegex(ValueError,'does not match'):
                _require_promotion(path,dataset_sha256='d'*64,
                                   capture_audit_sha256='e'*64,
                                   candidate_dataset_sha256='f'*64,
                                   changes=[self.change])

    def test_unchanged_reference_rejects_unneeded_promotion(self):
        self.assertIsNone(_require_promotion(
            None,dataset_sha256='d'*64,capture_audit_sha256='e'*64,
            candidate_dataset_sha256='f'*64,changes=[]))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'promotion.json';path.write_text('{}')
            with self.assertRaisesRegex(ValueError,'no reference scope changed'):
                _require_promotion(path,dataset_sha256='d'*64,
                                   capture_audit_sha256='e'*64,
                                   candidate_dataset_sha256='f'*64,changes=[])

    def test_changed_reference_rejects_receipt_without_correction_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'promotion.json'
            path.write_text(json.dumps({
                'schema_version':2,'kind':'reference_freeze_promotion',
                'input_dataset_sha256':'d'*64,'capture_audit_sha256':'e'*64,
                'candidate_dataset_sha256':'f'*64,'changes':[self.change],
                'approved_by':'independent-reference-review',
                'approved_at':'2026-09-11T00:00:00+00:00',
                'task_decision_changes':[],
            }))
            with self.assertRaisesRegex(ValueError,'valid correction provenance'):
                _require_promotion(path,dataset_sha256='d'*64,
                    capture_audit_sha256='e'*64,
                    candidate_dataset_sha256='f'*64,changes=[self.change])

    def test_changed_reference_requires_both_scope_regression_directions(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'promotion.json'
            path.write_text(json.dumps({
                'schema_version':2,'kind':'reference_freeze_promotion',
                'input_dataset_sha256':'d'*64,'capture_audit_sha256':'e'*64,
                'candidate_dataset_sha256':'f'*64,'changes':[self.change],
                'approved_by':'independent-reference-review',
                'approved_at':'2026-09-11T00:00:00+00:00',
                'task_decision_changes':[],
                'correction_provenance':build_correction_provenance(
                    'reference-evidence',ROOT_CAUSE,[REGRESSIONS[0]])
            }))
            with self.assertRaisesRegex(ValueError,'paired scope regressions'):
                _require_promotion(path,dataset_sha256='d'*64,
                    capture_audit_sha256='e'*64,
                    candidate_dataset_sha256='f'*64,changes=[self.change])

    def test_unavailable_body_reference_recovers_from_complete_clean_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            captures=Path(directory)
            text='Breadcrumb documentation body with the required recovery fact.'
            case={'id':'recoverable-reference','name':'Recoverable reference',
                  'url':'https://example.com/docs','track':'web','task_kind':'body',
                  'ground_truth_main_content':'','body_requirements':{},
                  'required_facts':[{'id':'fact','alternatives':['recovery fact']}],
                  'source_verification':{'state':'unavailable','checked_at':'2026-09-01T00:00:00Z',
                                         'method':'Independent HTTP response'}}
            audit={'case_id':case['id'],'status':200,'state':'captured',
                   'checked_at':'2026-09-14T00:00:00Z','selector':'main',
                   'sha256':'a'*64,
                   'extracted_sha256':hashlib.sha256(text.encode()).hexdigest(),
                   'quality_issues':[]}
            (captures/'audit.json').write_text(json.dumps([audit]))
            target=captures/case['id'];target.mkdir()
            (target/'reference.txt').write_text(text)

            frozen,changes,_=compute_reference_update({'cases':[case]},captures)

            self.assertEqual(frozen['cases'][0]['ground_truth_main_content'],text)
            self.assertEqual(frozen['cases'][0]['source_verification']['state'],'valid')
            self.assertEqual(changes[0]['case_id'],case['id'])

    def test_unavailable_body_reference_stays_unavailable_when_capture_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            captures=Path(directory)
            case={'id':'blocked-reference','name':'Blocked reference',
                  'url':'https://example.com/blocked','track':'web','task_kind':'body',
                  'ground_truth_main_content':'','body_requirements':{},
                  'required_facts':[],
                  'source_verification':{'state':'unavailable','checked_at':'2026-09-01T00:00:00Z',
                                         'method':'Independent HTTP response'}}
            audit={'case_id':case['id'],'status':403,'state':'unavailable',
                   'checked_at':'2026-09-14T00:00:00Z','selector':'main',
                   'sha256':'b'*64,'quality_issues':['http_status_403']}
            (captures/'audit.json').write_text(json.dumps([audit]))

            frozen,changes,_=compute_reference_update({'cases':[case]},captures)

            self.assertEqual(frozen['cases'][0],case)
            self.assertEqual(changes,[])

    def test_freeze_writes_nothing_until_changed_scope_is_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);captures=root/'captures';captures.mkdir()
            old='Stable old article body';new='Changed independently captured article body'
            checked=datetime.now(timezone.utc).isoformat()
            case={'id':'freeze-fixture','name':'Fixture',
                  'url':'https://en.wikipedia.org/w/index.php?title=Fixture&oldid=123',
                  'track':'web','task_kind':'body','ground_truth_main_content':old,
                  'body_requirements':body_requirements(old),'required_facts':[],
                  'source_verification':{'state':'valid','checked_at':checked,
                                         'method':'fixture','reference_sha256':
                                         hashlib.sha256(old.encode()).hexdigest()}}
            dataset={'cases':[case],'coverage_plan':{'web':['p']}}
            dataset_path=root/'dataset.json'
            dataset_path.write_text(json.dumps(dataset))
            for identifier in (case['id'], 'outside-task-scope'):
                folder=root/'sources'/identifier
                folder.mkdir(parents=True)
                (folder/'reference.txt').write_text(identifier)
                reference=root/'list-references'/(identifier+'.json')
                reference.parent.mkdir(exist_ok=True)
                reference.write_text('{}')
            capture={'case_id':'freeze-fixture','status':200,'checked_at':checked,
                     'selector':'#mw-content-text .mw-parser-output','sha256':'c'*64,
                     'extracted_sha256':hashlib.sha256(new.encode()).hexdigest(),
                     'quality_issues':[]}
            audit_path=captures/'audit.json';audit_path.write_text(json.dumps([capture]))
            case_capture=captures/'freeze-fixture';case_capture.mkdir()
            (case_capture/'reference.txt').write_text(new)
            (case_capture/'source.html').write_text('<main>'+new+'</main>')
            output=root/'frozen'

            with self.assertRaises(SystemExit):
                main(['--captures',str(captures),'--dataset',str(dataset_path),
                      '--output',str(output)])
            self.assertFalse(output.exists())

            promotion=root/'promotion.json'
            candidate_path=root/'candidate.json';full_path=root/'full-candidate.json'
            receipt,candidate_bytes=prepare_promotion(dataset_path=dataset_path,captures=captures,
                output=promotion,approved_by='independent-reference-review',
                approved_at=checked,candidate_output=candidate_path,
                full_canonical_output=full_path,
                root_cause_category='reference-evidence',root_cause=ROOT_CAUSE,
                regression_ids=REGRESSIONS)
            self.assertEqual(candidate_path.read_bytes(),candidate_bytes)
            self.assertEqual(json.loads(full_path.read_text())['cases'][0]
                             ['ground_truth_main_content'],new)
            with self.assertRaisesRegex(ValueError,'Destination exists'):
                prepare_promotion(dataset_path=dataset_path,captures=captures,
                    output=promotion,approved_by='independent-reference-review',
                    approved_at=checked,root_cause_category='reference-evidence',
                    root_cause=ROOT_CAUSE,regression_ids=REGRESSIONS)
            change=receipt['changes'][0]
            main(['--captures',str(captures),'--dataset',str(dataset_path),
                  '--output',str(output),'--reference-promotion',str(promotion)])
            refresh=json.loads((output/'reference-refresh.json').read_text())
            self.assertTrue((output/'list-references'/f'{case["id"]}.json').is_file())
            self.assertFalse((output/'sources/outside-task-scope').exists())
            self.assertFalse((output/'list-references/outside-task-scope.json').exists())
            self.assertTrue(refresh['reference_changed'])
            self.assertTrue(refresh['task_rules_changed'])
            self.assertEqual(refresh['changed'],[change])
            self.assertEqual((output/'reference-promotion.json').read_bytes(),
                             promotion.read_bytes())
            frozen=json.loads((output/'dataset.json').read_text())
            self.assertEqual(
                frozen['comparison']['reference_freeze_promotion']['changes'],[change])
            frozen['comparison'].pop('reference_freeze_promotion')
            self.assertEqual((json.dumps(frozen,ensure_ascii=False,indent=2)+'\n').encode(),
                             candidate_bytes)

    def _browser_promotion_fixture(self,root):
        inputs=create_promotion_inputs(root)
        receipt_path=root/'receipt.json';candidate_path=root/'candidate.json'
        prepare_promotion(dataset_path=inputs['dataset_path'],captures=inputs['captures'],
            output=receipt_path,approved_by='independent-reference-review',
            approved_at=inputs['checked_at'],case_ids='javascript-spa',
            browser_observations=inputs['observations_path'],candidate_output=candidate_path,
            root_cause_category='reference-evidence',root_cause=ROOT_CAUSE,
            regression_ids=REGRESSIONS)
        return (inputs['dataset_path'],inputs['captures'],inputs['observations_path'],
                receipt_path,candidate_path,inputs['observation'],inputs['new_reference'])

    def test_prepared_browser_candidate_promotes_bound_observation_not_http_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (canonical,captures,observations,receipt,candidate,
             observation,new)=self._browser_promotion_fixture(root)
            research=root/'research';sources=root/'sources'
            stale=sources/'javascript-spa'/'unavailable-reference.json'
            stale.parent.mkdir(parents=True)
            stale.write_text('{"state":"unavailable"}')
            target=research/'promotion.json'
            result=promote(canonical_path=canonical,candidate_path=candidate,
                captures=captures,receipt_path=receipt,receipt_target=target,
                browser_observations=observations,research_root=research,
                sources_root=sources)
            self.assertEqual(result['affected_case_ids'],['javascript-spa'])
            source=sources/'javascript-spa'
            self.assertEqual(json.loads(
                (source/'current-browser-observation.json').read_text()),observation)
            self.assertEqual((source/'reference.txt').read_text(),new)
            self.assertFalse((source/'current-source.html.txt').exists())
            self.assertFalse((source/'current-capture.json').exists())
            self.assertFalse((source/'unavailable-reference.json').exists())
            verification=json.loads(canonical.read_text())['cases'][0]['source_verification']
            self.assertIn('browser_observation_sha256',verification)
            self.assertIn('rendered_content_sha256',verification)
            self.assertNotIn('source_snapshot_sha256',verification)
            change=json.loads(receipt.read_text())['changes'][0]
            self.assertNotIn('source_snapshot_sha256',change['source_revision'])

    def test_prepared_browser_candidate_promotes_from_observation_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (canonical,captures,_observations,receipt,candidate,
             observation,_)=self._browser_promotion_fixture(root)
            observation_directory=root/'observation-sources'
            observation_source=observation_directory/'javascript-spa'
            observation_source.mkdir(parents=True)
            (observation_source/'current-browser-observation.json').write_text(
                json.dumps(observation))
            result=promote(canonical_path=canonical,candidate_path=candidate,
                captures=captures,receipt_path=receipt,
                receipt_target=root/'research'/'promotion.json',
                browser_observation_directory=observation_directory,
                research_root=root/'research',sources_root=root/'sources')
            self.assertEqual(result['affected_case_ids'],['javascript-spa'])
            self.assertTrue((root/'sources'/'javascript-spa'/
                             'current-browser-observation.json').is_file())

    def test_browser_candidate_promotion_fails_closed_without_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            canonical,captures,_observations,receipt,candidate,_,_=self._browser_promotion_fixture(root)
            with self.assertRaisesRegex(ValueError,'requires independent browser observation'):
                promote(canonical_path=canonical,candidate_path=candidate,
                    captures=captures,receipt_path=receipt,
                    receipt_target=root/'research'/'promotion.json',
                    research_root=root/'research',sources_root=root/'sources')

    def test_browser_candidate_promotion_rejects_mismatched_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (canonical,captures,observations,receipt,candidate,
             observation,_)=self._browser_promotion_fixture(root)
            observation['content_text']='Different rendered content after preparation'
            observations.write_text(json.dumps({'javascript-spa':observation}))
            with self.assertRaisesRegex(ValueError,'Candidate bytes differ'):
                promote(canonical_path=canonical,candidate_path=candidate,
                    captures=captures,receipt_path=receipt,
                    receipt_target=root/'research'/'promotion.json',
                    browser_observations=observations,research_root=root/'research',
                    sources_root=root/'sources')


if __name__=='__main__':
    unittest.main()
