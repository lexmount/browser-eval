"""A fresh session can locate and build supported report types and diagnostics."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from harness.reporting.templates import ROOT, resolve_template
from harness.web_fetch.report_build import build_diagnostic_cohorts
from harness.web_fetch.report_diagnostic import check_diagnostic_report


def diagnostic_example(root):
    """Two independent scenarios: complete content and a challenge response."""
    root.mkdir()
    rows=[]
    for cid,passed,answer in [('complete',True,'Required article body'),('challenge',False,'Verify you are human')]:
        bindings={}
        for kind,text in [('reference','Required article body'),('first_answer',answer),('source','Required article body'),('log','HTTP 200; '+answer)]:
            relative=f'evidence/{cid}-{kind}.txt'
            path=root/relative;path.parent.mkdir(exist_ok=True);path.write_text(text)
            bindings[kind]={'path':relative,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'scope':'declared task'}
        rows.append({'id':cid,'cohort':'articles','initial_pass':passed,'run_id':'call-'+cid,
                     'url':'https://example.com/'+cid,'task':'Read required article body',
                     'cause':'passed' if passed else 'challenge','cause_detail':'Full content' if passed else 'Challenge page',
                     'failure':'' if passed else 'Required body missing','followup_status':'Saved call',
                     'answer':bindings['first_answer'],**bindings})
    model={'schema':'webfetch_cohort_diagnostic/1','certified':False,'title':'Diagnostic example',
           'subtitle':'Two scenarios','scope_note':'Diagnostic observations','metric_note':'One pass per task',
           'cohorts':[{'id':'articles','label':'Articles','case_count':2}],
           'cause_labels':{'passed':'Passed','challenge':'Challenge'},'source_labels':{},
           'metrics':{'articles':[1,2],'overall':[1,2]},'rows':rows,
           'success_audit':{'audited_passes':0,'source_state_counts':{}},'pages':{}}
    (root/'report.json').write_text(json.dumps(model))
    return model


class ReportTemplateTests(unittest.TestCase):
    def test_all_supported_tracks_resolve_existing_owners_and_pinned_examples(self):
        for track in ('webfetch','trajectory-replay','browser-use'):
            with self.subTest(track=track):
                template=resolve_template(track)
                self.assertIn('?version=ver_',template['example'])
                self.assertTrue((ROOT/template['entrypoint']).is_file())
                self.assertTrue(template['check'])

    def test_unknown_track_cannot_silently_fall_back(self):
        with self.assertRaisesRegex(ValueError,'Unregistered'):
            resolve_template('new-report')

    def test_reference_scope_is_required_before_rendering_or_writing(self):
        for value in (None, '', '  ', 42, ['partial']):
            with self.subTest(scope=value),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source=root/'source';model=diagnostic_example(source)
                binding=model['rows'][0]['reference']
                if value is None:binding.pop('scope')
                else:binding['scope']=value
                (source/'report.json').write_text(json.dumps(model))
                with self.assertRaisesRegex(ValueError,'reference.*scope'):
                    build_diagnostic_cohorts(source,root/'report')
                self.assertFalse((root/'report').exists())

    def test_malformed_evidence_bindings_fail_at_the_model_boundary(self):
        for field in ('reference','first_answer','answer','source','log'):
            for value in ('not-an-object', {'path':'a.txt'}, {'path':None,'sha256':'invalid'}):
                with self.subTest(field=field,value=value),tempfile.TemporaryDirectory() as temp:
                    root=Path(temp);source=root/'source';model=diagnostic_example(source)
                    model['rows'][0][field]=value
                    (source/'report.json').write_text(json.dumps(model))
                    with self.assertRaisesRegex(ValueError,field):
                        build_diagnostic_cohorts(source,root/'report')
                    self.assertFalse((root/'report').exists())

    def test_large_output_is_retained_and_loaded_from_bound_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';model=diagnostic_example(source)
            binding=model['rows'][0]['first_answer']
            raw=('Required article body\n'*3000).encode()
            (source/binding['path']).write_bytes(raw)
            binding['sha256']=hashlib.sha256(raw).hexdigest()
            (source/'report.json').write_text(json.dumps(model))
            output=root/'report';build_diagnostic_cohorts(source,output)
            self.assertEqual((output/binding['path']).read_bytes(),raw)
            from bs4 import BeautifulSoup
            soup=BeautifulSoup((output/'attempts/complete.html').read_text(),'html.parser')
            panel=soup.select_one('[data-evidence-kind="first_answer"] pre')
            self.assertEqual(panel['data-evidence-file'],binding['path'])

    def test_release_checks_distinct_diagnostic_output(self):
        from bs4 import BeautifulSoup
        for mode in ('missing', 'wrong-file', 'wrong-content'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source=root/'source';model=diagnostic_example(source)
                relative='evidence/complete-diagnostic.txt'
                raw=b'Required article body from supplemental call'
                (source/relative).write_bytes(raw)
                model['rows'][0]['answer']={'path':relative,'sha256':hashlib.sha256(raw).hexdigest(),'scope':'supplemental call'}
                (source/'report.json').write_text(json.dumps(model))
                output=root/'report';build_diagnostic_cohorts(source,output)
                self.assertEqual(check_diagnostic_report(output)['cases'],2)
                page=output/'attempts/complete.html'
                soup=BeautifulSoup(page.read_text(),'html.parser')
                panel=soup.select_one('[data-evidence-kind="answer"]')
                if mode=='missing':panel.decompose()
                elif mode=='wrong-file':panel.select_one('pre')['data-evidence-file']=model['rows'][0]['first_answer']['path']
                else:panel.select_one('pre').string='Wrong supplemental output'
                page.write_text(str(soup))
                with self.assertRaises(ValueError):check_diagnostic_report(output)

    def test_build_preserves_outputs_and_separates_dataset_from_verdicts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';model=diagnostic_example(source)
            before={p.relative_to(source):p.read_bytes() for p in source.rglob('*') if p.is_file()}
            build_diagnostic_cohorts(source,root/'report')
            self.assertEqual(check_diagnostic_report(root/'report')['cases'],2)
            for relative,raw in before.items():
                self.assertEqual((source/relative).read_bytes(),raw)
                self.assertEqual((root/'report'/relative).read_bytes(),raw)
            dataset=(root/'report/datasets/complete.html').read_text()
            self.assertNotIn('首轮通过',dataset)
            failed=(root/'report/attempts/challenge.html').read_text()
            self.assertIn('Required body missing',failed)
            self.assertIn('Verify you are human',failed)
            manifest=json.loads((root/'report/report-manifest.json').read_text())
            self.assertEqual(manifest['provider_calls_added'],0)
            self.assertFalse(manifest['certified'])

    def test_tampered_output_and_summary_are_rejected_before_build(self):
        for mode in ('output','summary','duplicate','symlink'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source=root/'source';model=diagnostic_example(source)
                if mode=='output':(source/model['rows'][0]['answer']['path']).write_text('tampered')
                elif mode=='summary':model['metrics']['overall']=[2,2]
                elif mode=='duplicate':model['rows'].append(copy.deepcopy(model['rows'][0]))
                else:
                    original=source/model['rows'][0]['answer']['path']
                    original.rename(source/'retained.txt')
                    original.symlink_to(source/'retained.txt')
                (source/'report.json').write_text(json.dumps(model))
                with self.assertRaises(ValueError):build_diagnostic_cohorts(source,root/'report')
                self.assertFalse((root/'report').exists())

    def test_release_rejects_missing_evidence_dialog_and_parallel_renderer(self):
        for mode in ('dialog','renderer','verdict','certification','evidence'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source=root/'source';diagnostic_example(source)
                output=root/'report';build_diagnostic_cohorts(source,output)
                if mode=='dialog':
                    path=output/'attempts/challenge.html'
                    path.write_text(path.read_text().replace('id="result-logs"','id="missing-log"'))
                elif mode=='evidence':
                    path=output/'attempts/challenge.html'
                    path.write_text(path.read_text().replace('Verify you are human','Different output'))
                elif mode=='verdict':
                    path=output/'attempts/challenge.html'
                    path.write_text(path.read_text().replace('data-report-detail="failed"','data-report-detail="success"'))
                else:
                    path=output/'report-manifest.json';manifest=json.loads(path.read_text())
                    if mode=='renderer':manifest['template']['renderer']='work/copied.py'
                    else:manifest['certified']=True
                    path.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):check_diagnostic_report(output)
