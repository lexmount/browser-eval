import hashlib
import json
import time
import unittest
import tempfile
from unittest.mock import patch
from pathlib import Path

from harness.web_fetch.body_coverage import body_requirements
from harness.web_fetch.call_boundary import fetch_with_deadline, prepare_answer
from harness.web_fetch.metrics import task_passed, task_measured
from harness.web_fetch.models import EvalCase, ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.report import case_attrs, result_label, ranking
from harness.web_fetch.response_input import apply_request_status
from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.stable_tasks import evaluate_stable_task

ROOT = Path(__file__).resolve().parents[1]


class FairnessTests(unittest.TestCase):
    def test_live_conversion_is_consumed_once_and_equals_offline(self):
        from harness.web_fetch.response_input import answer_input
        case={'url':'https://example.com','expected_any':['Answer']}
        c=EvalCase('x','x',case['url'],'test',['Answer'])
        with patch('harness.web_fetch.call_boundary.answer_input', wraps=answer_input) as convert:
            result=prepare_answer(ProviderResult(True,'Answer'),c)
        self.assertEqual(convert.call_count,1)
        with patch('harness.web_fetch.response_input.answer_input',side_effect=AssertionError('duplicate conversion')):
            live=score_attempt(case,b'Answer',ok=True,prepared_answer=result.prepared_answer)
        self.assertEqual(live,score_attempt(case,b'Answer',ok=True))

    def test_file_runner_persists_bytes_with_relative_output_path(self):
        from harness.web_fetch.runner import run_evaluation
        from harness.web_fetch.providers.http import HttpProvider
        from harness.web_fetch.providers.http_client import HttpResponse
        raw=b'\x89PNG\r\n\x1a\n'+b'payload'*10
        c=EvalCase('file','file','https://example.com/file','image',[],task_kind='file',
                   source_verification={'file_sha256':hashlib.sha256(raw).hexdigest()})
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            relative=Path(directory).relative_to(Path.cwd())/'output'
            with patch('harness.web_fetch.providers.http.request',return_value=HttpResponse(200,c.url,{'content-type':'image/png'},raw)):
                records,_=run_evaluation(output_dir=relative,dataset_meta={'cases':[]},dataset_path=Path('fixture.json'),
                    cases=[c],providers=[HttpProvider('http',{})],provider_config_path=Path('fixture-config.json'),runs=1,timeout=1,delay=0)
            r=records[0]
            self.assertTrue(task_passed(r['ok'],r['metrics']))
            file=r['provider_metadata']['file_evaluation']['files'][0]
            self.assertEqual((relative/file['artifact_path']).read_bytes(),raw)

    def test_explicit_links_equal_markdown_not_arbitrary_metadata(self):
        contract = {'version': 1, 'schema': 'named_link', 'minimum_records': 3,
                    'hosts': ['example.com'], 'entry_path': '/item/\\d+'}
        c = {'url': 'https://example.com', 'acceptance_contract': contract}
        links = [{'text': f'Product {i}', 'href': f'/item/{i}'} for i in range(3)]
        markdown = '\n'.join(f"[{x['text']}]({x['href']})" for x in links)
        a = score_attempt(c, markdown.encode(), ok=True)
        b = score_attempt(c, b'', ok=True, metadata={'links': links})
        self.assertTrue(a['stable_task_pass'] and b['stable_task_pass'])
        self.assertEqual(a['stable_records'], b['stable_records'])
        self.assertFalse(score_attempt(c, b'', ok=True, metadata={'logs': {'links': links}})['stable_task_pass'])
        self.assertFalse(score_attempt(c, b'', ok=True, metadata={'links': ['/item/1']})['stable_task_pass'])
        softbreak = markdown.replace('Product ', 'Product\n')
        self.assertEqual(score_attempt(c, softbreak.encode(), ok=True)['stable_records'], a['stable_records'])

    def test_api_status_normalization_does_not_depend_on_provider_name(self):
        for name in ('a', 'b', 'cloudflare-markdown'):
            raw = b'{"success":true,"result":""}'
            m = score_attempt({'url': 'https://example.com'}, raw, ok=False, status_code=200)
            r = {'provider': name, 'ok': False, 'error_type': 'api_error', 'provider_metadata': {}, 'metrics': m}
            apply_request_status(r)
            self.assertTrue(r['ok'])
            self.assertFalse(r['raw_request_status']['ok'])
            self.assertFalse(task_passed(r['ok'], m))
            self.assertEqual(m['content_chars'], 0)
            self.assertEqual(m['content_sha256'], hashlib.sha256(raw).hexdigest())

    def test_real_full_reference_passes_and_small_prefix_is_rejected(self):
        cases = json.loads((ROOT/'evaluation/dataset.json').read_text())['cases']
        for c in cases:
            if not c.get('body_requirements'):
                continue
            with self.subTest(case=c['id']):
                reference = c['ground_truth_main_content']
                self.assertTrue(score_attempt(c, reference.encode(), ok=True)['content_pass'])
                prefix = score_attempt(c, reference[:max(1,len(reference)//10)].encode(), ok=True)
                self.assertFalse(prefix['content_pass'])
                self.assertIsNotNone(prefix['word_f1'])
                self.assertGreaterEqual(prefix['word_f1'], 0)

    def test_reviewed_css_scope_preserves_both_document_tocs(self):
        cases = json.loads((ROOT/'evaluation/dataset.json').read_text())['cases']
        c = next(c for c in cases if c['id'] == 'live-024-cascading-style-sheets-level-2-r')
        self.assertIn('Quick Table of Contents', c['ground_truth_main_content'])
        self.assertIn('Full Table of Contents', c['ground_truth_main_content'])
        self.assertGreater(len(c['ground_truth_main_content']), 40000)

    def test_react_fields_cannot_be_omitted_or_borrowed(self):
        cases = json.loads((ROOT/'evaluation/dataset.json').read_text())['cases']
        c = next(c for c in cases if c['id'] == 'dynamic-react-cart')
        m = score_attempt(c, c['ground_truth_main_content'].encode(), ok=True)
        self.assertTrue(m['stable_task_pass'])
        self.assertEqual([x['count'] for x in m['stable_field_results']], [15, 13])
        only = '\n\n'.join(f"{r['fields']['name']}\n{r['fields']['price']}" for r in m['stable_records'])
        stripped = score_attempt(c, only.encode(), ok=True)
        self.assertFalse(stripped['stable_task_pass'])
        self.assertEqual(stripped['stable_unique_record_count'], 16)
        contract = {'version':1, 'schema':'product','minimum_records':2,'field_minimum_records':{'shipping':2}}
        m = evaluate_stable_task('Alpha\n$10\nFree shipping\n\nBeta\n$20', contract)
        self.assertFalse(m['stable_task_pass'])
        self.assertEqual(m['stable_field_results'][0]['count'], 1)

    def test_end_to_end_processing_is_timed_and_late_answers_rejected(self):
        class Slow(Provider):
            def fetch(self, case, timeout):
                time.sleep(.025)  # download
                time.sleep(.065)  # local extraction
                return ProviderResult(True, 'finished')
        c = EvalCase('x','x','https://example.com','test',[])
        start = time.perf_counter()
        result, worker = fetch_with_deadline(Slow('slow',{}),c,1)
        self.assertGreaterEqual(time.perf_counter()-start, .085)
        self.assertTrue(result.ok)
        start = time.perf_counter()
        result, worker = fetch_with_deadline(Slow('slow',{}),c,.04)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type,'timeout')
        self.assertLess(time.perf_counter()-start,.085)
        worker.join(.2)
        self.assertEqual(result.content,'')
        self.assertFalse(task_passed(True,{'content_pass':True,'deadline_overrun':True}))

    def test_historical_file_missing_is_unmeasured_not_failed(self):
        c = {'id':'x','name':'x','url':'https://example.com','task_kind':'file','track':'image','category':'image'}
        m = score_attempt(c,b'link only',ok=True)
        r = {'provider':'p','ok':True,'metrics':m,'elapsed_ms':20,'case_id':'x'}
        self.assertFalse(task_measured(m))
        self.assertEqual(result_label(r,c),'未测')
        self.assertIn('data-result="unmeasured"',case_attrs(r,c))
        self.assertIn('0/1', ranking([r],{'x':c},'image'))

    def test_file_bytes_are_typed_hashed_and_target_bound(self):
        raw = b'\x89PNG\r\n\x1a\n' + b'payload'*10
        c = EvalCase('x','x','https://example.com/image','image',[],task_kind='file',
                     source_verification={'file_sha256':hashlib.sha256(raw).hexdigest()})
        good = prepare_answer(ProviderResult(True,'',files=[{'data':raw}]),c)
        self.assertTrue(good.metadata['file_evaluation']['pass'])
        bad = prepare_answer(ProviderResult(True,'',files=[{'data':raw+b'wrong'}]),c)
        self.assertFalse(bad.metadata['file_evaluation']['pass'])
        text = prepare_answer(ProviderResult(True,c.url),c)
        self.assertFalse(text.metadata['file_evaluation']['pass'])
