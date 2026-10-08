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
    def test_interpretation_shows_requirements_and_actual_browser_error_not_metadata(self):
        from harness.web_fetch.report_diagnostic import diagnostic_reference_excerpt, diagnostic_source_excerpt
        row={'reference':{'path':'task.json'},'source':{'path':'source.json'}}
        files={'task.json':json.dumps({'acceptance_contract':{'schema':'named_link',
            'minimum_records':5,'stable_text_anchors':[{'alternatives':['Help','帮助']} ]}}).encode(),
            'source.json':json.dumps({'status':'failure','attempt_errors':[{'message':'net::ERR_CONNECTION_RESET'}]}).encode()}
        reference=diagnostic_reference_excerpt(row,files)
        self.assertIn('5',reference);self.assertIn('Help / 帮助',reference)
        self.assertIn('net::ERR_CONNECTION_RESET',diagnostic_source_excerpt(row,files))
        self.assertNotIn('attempt_errors',diagnostic_source_excerpt(row,files))

    def test_fresh_cohort_adapter_requires_complete_bound_review_and_preserves_answers(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);base=diagnostic_example(root/'example')
            run=root/'run';run.mkdir();(run/'artifacts').mkdir()
            cases=[];records=[];observations={};reviews=[]
            for row in base['rows']:
                cid=row['id'];raw=(root/'example'/row['first_answer']['path']).read_bytes()
                (run/'artifacts'/f'{cid}.txt').write_bytes(raw)
                cases.append({'id':cid,'url':row['url'],'name':cid,'task_kind':'body'})
                records.append(dict(attempt_id=cid,run_id='call-'+cid,case_id=cid,
                    provider='moli-webfetch',ok=row['initial_pass'],elapsed_ms=100,
                    artifact_path=f'artifacts/{cid}.txt',metrics=dict(content_pass=row['initial_pass'],
                    request_ok=row['initial_pass'],content_sha256=hashlib.sha256(raw).hexdigest()),
                    provider_metadata=dict(reported_version='moli 1.1.15',binary_sha256='a'*64)))
                observations[cid]=dict(case_id=cid,independent=True,content_text='Required article body',
                    checked_at='2026-10-08T00:00:00Z',outer_html='<body>Private DOM</body>')
                reviews.append(dict(case_id=cid,reviewed=True,cause=row['cause'],cause_detail=row['cause_detail'],
                    followup_status=row['followup_status'],source_state='rubric_visible'))
            (run/'dataset.json').write_text(json.dumps({'cases':cases}))
            (run/'results.jsonl').write_text('\n'.join(json.dumps(r) for r in records))
            (run/'execution.json').write_text(json.dumps({'aggregate':{'case_ids':[c['id'] for c in cases],
                'completed_at':'2026-10-08T00:00:00Z'},'batches':[{'attempt_ids':[r['attempt_id'] for r in records]}]}))
            observation_path=root/'observations.json';observation_path.write_text(json.dumps(observations))
            review=dict(bindings={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in
                [('dataset_sha256',run/'dataset.json'),('results_sha256',run/'results.jsonl'),('observations_sha256',observation_path)]},rows=reviews)
            review_path=root/'review.json';review_path.write_text(json.dumps(review))
            base['source_labels']={'rubric_visible':'参考要求可见'}
            spec=root/'input.json';spec.write_text(json.dumps(dict(schema='webfetch_diagnostic_inputs/1',report=base,
                cohorts=[dict(id='articles',label='Articles',run='run',observations='observations.json',review='review.json')])) )
            output=root/'report';build_diagnostic_cohorts(spec,output)
            from harness.web_fetch.report_diagnostic import load_diagnostic
            model,files=load_diagnostic(output)
            self.assertEqual(model['metrics']['overall'],[1,2])
            self.assertEqual(model['cohorts'][0]['version'],'moli 1.1.15')
            self.assertEqual(files[model['rows'][0]['first_answer']['path']],b'Required article body')
            self.assertNotIn('Private DOM',files[model['rows'][0]['source']['path']].decode())
            # Publishing can redact sensitive links, but must not change the
            # scored answer or silently claim the public view is byte-identical.
            answer_path=run/records[0]['artifact_path'];original=answer_path.read_bytes()
            private=original+b' https://example.com/download?token=private-synthetic-value'
            answer_path.write_bytes(private)
            records[0]['metrics']['content_sha256']=hashlib.sha256(private).hexdigest()
            (run/'results.jsonl').write_text('\n'.join(json.dumps(r) for r in records))
            review['bindings']['results_sha256']=hashlib.sha256((run/'results.jsonl').read_bytes()).hexdigest()
            review_path.write_text(json.dumps(review))
            public_output=root/'redacted-report';build_diagnostic_cohorts(spec,public_output)
            public_model,public_files=load_diagnostic(public_output)
            binding=public_model['rows'][0]['first_answer']
            self.assertNotIn(b'private-synthetic-value',public_files[binding['path']])
            self.assertEqual(answer_path.read_bytes(),private)
            self.assertEqual(binding['publication_projection']['original_sha256'],records[0]['metrics']['content_sha256'])
            binding['publication_projection']['original_sha256']='0'*64
            (public_output/'report.json').write_text(json.dumps(public_model))
            with self.assertRaisesRegex(ValueError,'scored original'):load_diagnostic(public_output)
            answer_path.write_bytes(original)
            records[0]['metrics']['content_sha256']=hashlib.sha256(original).hexdigest()
            (run/'results.jsonl').write_text('\n'.join(json.dumps(r) for r in records))
            review['bindings']['results_sha256']=hashlib.sha256((run/'results.jsonl').read_bytes()).hexdigest()
            review_path.write_text(json.dumps(review))
            for mode in ('review_missing','review_stale','answer_changed','incomplete_run'):
                with self.subTest(mode=mode):
                    original_review=review_path.read_bytes();original_execution=(run/'execution.json').read_bytes()
                    answer_path=run/records[0]['artifact_path'];original_answer=answer_path.read_bytes()
                    if mode=='review_missing':review_path.write_text(json.dumps({**review,'rows':reviews[:1]}))
                    elif mode=='review_stale':review_path.write_text(json.dumps({**review,'bindings':{}}))
                    elif mode=='answer_changed':answer_path.write_text('Invented answer')
                    else:(run/'execution.json').write_text(json.dumps({'aggregate':{},'batches':[]}))
                    with self.assertRaises(ValueError):build_diagnostic_cohorts(spec,root/mode)
                    self.assertFalse((root/mode).exists())
                    review_path.write_bytes(original_review);(run/'execution.json').write_bytes(original_execution);answer_path.write_bytes(original_answer)

    def test_saved_runtime_failure_is_not_explained_as_its_secondary_parse_error(self):
        from harness.web_fetch.report_diagnostic import diagnostic_findings
        row={'initial_pass':False,'log':{'path':'call.json'}}
        for exit_code in (0,1):
            with self.subTest(exit_code=exit_code):
                log={'ok':False,'error_message':'JSON could not be parsed',
                     'process':{'exit_code':exit_code,'stderr':'DEBUG starting\nError: fetch failed\nReason: SSL connection reset'}}
                finding=diagnostic_findings(row,{'call.json':json.dumps(log).encode()})[0]
                self.assertEqual(finding['actual'],'Error: fetch failed\nReason: SSL connection reset' if exit_code else 'JSON could not be parsed')

    def test_summary_uses_all_calls_and_never_a_partial_latency_population(self):
        from bs4 import BeautifulSoup
        for missing in (False, True):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source=root/'source';model=diagnostic_example(source)
                for row,elapsed in zip(model['rows'],(1000,9000)):
                    log={'ok':row['initial_pass'],'elapsed_ms':elapsed}
                    if missing and not row['initial_pass']:log.pop('elapsed_ms')
                    raw=json.dumps(log).encode();binding=row['log']
                    (source/binding['path']).write_bytes(raw)
                    binding['sha256']=hashlib.sha256(raw).hexdigest()
                (source/'report.json').write_text(json.dumps(model))
                output=root/'report';build_diagnostic_cohorts(source,output)
                home=BeautifulSoup((output/'index.html').read_text(),'html.parser')
                cells=home.select_one('[data-summary-group]').select('td')
                self.assertEqual([c['data-sort'] for c in cells[3:6]],['2','1','1'])
                self.assertEqual([float(c['data-sort']) for c in cells[6:]],
                                 [-1,-1,-1] if missing else [5000,5000,9000])
                target=output/'index.html'
                target.write_text(target.read_text().replace('data-sort="'+cells[6]['data-sort']+'"','data-sort="1000"',1))
                with self.assertRaisesRegex(ValueError,'Summary'):check_diagnostic_report(output)

    def test_diagnostic_explains_failed_checks_and_keeps_review_separate(self):
        from bs4 import BeautifulSoup
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';model=diagnostic_example(source)
            row=model['rows'][1]
            row['cause_detail']='Independent browser also encountered a challenge'
            log={'ok':True,'metrics':{'blocked_page':True,
                'blocked_page_hits':['Verify you are human'],
                'stable_anchor_results':[{'id':'anchor-3','alternatives':['Images'],'pass':False}],
                'stable_structure_pass':False,'stable_minimum_records':8,
                'stable_unique_record_count':2}}
            binding=row['log'];raw=json.dumps(log).encode()
            (source/binding['path']).write_bytes(raw)
            binding['sha256']=hashlib.sha256(raw).hexdigest()
            (source/'report.json').write_text(json.dumps(model))
            output=root/'report';build_diagnostic_cohorts(source,output)
            detail=BeautifulSoup((output/'attempts/challenge.html').read_text(),'html.parser')
            checks=detail.select_one('#failed-requirements')
            self.assertIsNotNone(checks)
            self.assertIn('Images',checks.get_text())
            self.assertIn('8',checks.get_text())
            self.assertIn('2',checks.get_text())
            self.assertIn('Verify you are human',checks.get_text())
            self.assertNotIn(row['cause_detail'],checks.get_text())
            analysis=BeautifulSoup((output/'analysis/index.html').read_text(),'html.parser')
            for kind in ('blocked','text','records'):
                count=analysis.select_one('[data-failure-type="'+kind+'"]')
                self.assertIsNotNone(count)
                self.assertEqual(count['data-count'],'1')
            for path,old,new in [('analysis/index.html','data-count="1"','data-count="9"'),
                                 ('attempts/challenge.html','Images','Bogus requirement')]:
                target=output/path;original=target.read_text()
                target.write_text(original.replace(old,new,1))
                with self.assertRaises(ValueError):check_diagnostic_report(output)
                target.write_text(original)

    def test_default_webfetch_and_wcxb_use_distinct_authoritative_profiles(self):
        import shlex
        from urllib.parse import urlsplit,parse_qs
        template=resolve_template('webfetch')
        self.assertEqual(template['profile'],'evaluation/current-report.json')
        command=shlex.split(template['build'])
        self.assertEqual(command[command.index('--profile')+1],template['profile'])
        self.assertEqual(command[command.index('--output')+1],'reports/webfetch')
        self.assertEqual(template['check'],'make report-check')
        wcxb=json.loads((ROOT/template['wcxb_profile']).read_text())
        command=shlex.split(template['wcxb_build'])
        self.assertEqual(command[command.index('--profile')+1],template['wcxb_profile'])
        self.assertEqual(command[command.index('--output')+1],'reports/wcxb')
        self.assertEqual(template['example_profile'],template['wcxb_profile'])
        self.assertEqual(urlsplit(template['example']).path,'/s/'+wcxb['artifact_hub_site'])
        self.assertEqual(parse_qs(urlsplit(template['example']).query)['version'],[wcxb['artifact_hub_expected_version']])

    def test_optional_variant_profiles_must_exist_within_the_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'configs').mkdir()
            registry=json.loads((ROOT/'configs/report-templates.json').read_text())
            row=registry['tracks']['webfetch']
            for key in ('renderer','entrypoint','profile','tests',*(key for key in row if key.endswith('_profile'))):
                path=root/row[key];path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{}')
            for value in ('missing-profile.json',str(ROOT/'evaluation/wcxb/current-report.json')):
                row['wcxb_profile']=value
                (root/'configs/report-templates.json').write_text(json.dumps(registry))
                with self.assertRaisesRegex(ValueError,'profile'):
                    resolve_template('webfetch',root=root)
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

    def test_local_nested_pages_resolve_resources_navigation_and_lazy_evidence(self):
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin, urlsplit
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';model=diagnostic_example(source)
            for row in model['rows']:
                binding=row['first_answer'];raw=('Saved full output\n'*3000).encode()
                (source/binding['path']).write_bytes(raw)
                binding['sha256']=hashlib.sha256(raw).hexdigest()
            (source/'report.json').write_text(json.dumps(model))
            output=root/'report';build_diagnostic_cohorts(source,output)
            for route in ('attempts/complete.html','attempts/challenge.html','datasets/complete.html'):
                soup=BeautifulSoup((output/route).read_text(),'html.parser')
                document='https://local.test/reports/'+route
                base=soup.select_one('base[href]')
                base_url=urljoin(document,base['href']) if base else document
                for node in soup.select('[href], [src], [data-evidence-file]'):
                    if node.name=='base':continue
                    value=node.get('href') or node.get('src') or node.get('data-evidence-file')
                    resolved=urlsplit(urljoin(base_url,value))
                    if resolved.netloc!='local.test':continue
                    self.assertTrue(resolved.path.startswith('/reports/'))
                    self.assertTrue((output/resolved.path.removeprefix('/reports/')).is_file(),(route,value))

    def test_cohort_chart_uses_each_own_denominator_and_rejects_tampering(self):
        from bs4 import BeautifulSoup
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';model=diagnostic_example(source)
            model['rows'][0]['cohort']='small'
            model['rows'][1]['cohort']='large'
            extra=copy.deepcopy(model['rows'][1]);extra.update(id='challenge-two',run_id='call-challenge-two')
            model['rows'].append(extra)
            model['cohorts']=[{'id':'small','label':'Small','case_count':1},{'id':'large','label':'Large','case_count':2}]
            model['metrics']={'small':[1,1],'large':[0,2],'overall':[1,3]}
            (source/'report.json').write_text(json.dumps(model))
            output=root/'report';build_diagnostic_cohorts(source,output)
            page=output/'index.html';soup=BeautifulSoup(page.read_text(),'html.parser')
            bars=soup.select('#cohort-results .editorial-bar-row')
            self.assertEqual([b.select_one('b').get_text() for b in bars],['1 / 1','0 / 2'])
            self.assertIn('100',bars[0].select_one('i')['style'])
            bars[0].select_one('b').string='1 / 2';page.write_text(str(soup))
            with self.assertRaises(ValueError):check_diagnostic_report(output)

    def test_release_rejects_wrong_document_base_even_when_root_targets_exist(self):
        from bs4 import BeautifulSoup
        for mode in ('missing', 'wrong', 'external'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source=root/'source';diagnostic_example(source)
                output=root/'report';build_diagnostic_cohorts(source,output)
                page=output/'attempts/challenge.html';soup=BeautifulSoup(page.read_text(),'html.parser')
                base=soup.select_one('base[href]')
                if mode=='missing':base.decompose()
                else:base['href']='./' if mode=='wrong' else 'https://wrong.test/'
                page.write_text(str(soup))
                with self.assertRaises(ValueError):check_diagnostic_report(output)

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
        for mode in ('dialog','renderer','verdict','certification','evidence','guide','guide_evidence','guide_summary'):
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
                elif mode.startswith('guide'):
                    path=output/'guide/index.html';text=path.read_text()
                    if mode=='guide':text=text.replace('id="guide-results"','id="missing-outcome"')
                    elif mode=='guide_evidence':text=text.replace('Verify you are human','Invented output')
                    else:text=text.replace('1 / 2','2 / 2',1)
                    path.write_text(text)
                else:
                    path=output/'report-manifest.json';manifest=json.loads(path.read_text())
                    if mode=='renderer':manifest['template']['renderer']='work/copied.py'
                    else:manifest['certified']=True
                    path.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):check_diagnostic_report(output)
