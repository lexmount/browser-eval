import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from lexbench_webfetch.body_coverage import body_requirements, compact_ngrams, evaluate_body_requirements
from harness.web_fetch.call_boundary import fetch_with_deadline, prepare_answer
from lexbench_webfetch.metrics import evaluate_content, task_measured, task_passed
from lexbench_webfetch.models import EvalCase
from harness.web_fetch.models import ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.reference_inputs import read_dataset_bytes
from harness.web_fetch.report import case_attrs, ranking, result_label
from lexbench_webfetch.response_input import apply_request_status
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.stable_tasks import evaluate_stable_task

ROOT = Path(__file__).resolve().parents[1]


class FairnessTests(unittest.TestCase):
    def historical_dataset(self):
        try:
            return json.loads(read_dataset_bytes(ROOT/'evaluation/dataset.json'))
        except FileNotFoundError:
            self.skipTest('requires externally installed historical reference inputs')

    def test_legacy_body_region_result_preserves_auditable_counts_and_missing_words(self):
        contract={'version':1,'unit':'region_token_recall','minimum_coverage':.8,
                  'regions':[{'id':'body-region-1','text':'alpha beta beta gamma delta epsilon'}]}
        result=evaluate_body_requirements('alpha beta gamma',contract)['body_region_results'][0]
        self.assertEqual(result['expected_tokens'],6)
        self.assertEqual(result['matched_tokens'],3)
        self.assertEqual(result['missing_tokens'],3)
        self.assertIn({'token':'beta','count':1},result['missing_sample'])

    def test_v3_body_regions_ignore_markdown_boundaries_but_reject_omissions(self):
        contract=body_requirements('Albert Einstein Tags change deep thoughts')
        complete=evaluate_body_requirements('AlbertEinstein\nTags:changedeep-thoughts',contract)
        self.assertTrue(complete['body_coverage_pass'])
        result=complete['body_region_results'][0]
        self.assertEqual(result['matched_units'],result['expected_units'])
        self.assertEqual(result['missing_units'],0)
        omitted=evaluate_body_requirements('Albert Einstein Tags change',contract)
        self.assertFalse(omitted['body_coverage_pass'])
        self.assertGreater(omitted['body_region_results'][0]['missing_units'],0)

    def test_v3_body_regions_reject_repeated_short_fragment_borrowing(self):
        reference = ' '.join(
            f'section{i} common api request response parameter example item{i}x{j}'
            for i in range(1, 6) for j in range(5))
        contract = body_requirements(reference)
        # Repeating one section many times must not satisfy the omitted sections.
        repeated = ' '.join(['section1 common api request response parameter example'] * 20)
        result = evaluate_body_requirements(repeated, contract)
        self.assertFalse(result['body_coverage_pass'])
        self.assertLess(result['body_target_regions'], result['body_required_target_regions'])

    def test_v3_body_regions_allocate_shared_fragments_globally(self):
        shared = 'abcdefghijklmnopqrstuvwx' * 3
        contract = {
            'version': 3,
            'unit': 'region_compact_hexagram_recall',
            'minimum_coverage': .8,
            'minimum_floor': .6,
            'minimum_region_passes': 1,
            'regions': [
                {'id': 'opening', 'text': shared + 'AAAAAAAABBBBBBBB'},
                {'id': 'ending', 'text': shared + 'CCCCCCCCDDDDDDDD'},
            ],
        }
        # There is enough shared capacity for one target and both floors, but a
        # sequential first-region match consumes too much and falsely fails ending.
        content = shared + shared[:30] + 'AAAAAAAABBBBBBBBCCCCCCCCDDDDDDDD'
        result = evaluate_body_requirements(content, contract)
        self.assertTrue(result['body_coverage_pass'])
        self.assertEqual(result['body_target_regions'], 1)
        self.assertTrue(all(row['floor_pass'] for row in result['body_region_results']))
        self.assertEqual(
            sum(row['matched_units'] for row in result['body_region_results']),
            119,
        )

    def test_v3_body_verdict_and_counts_ignore_region_order(self):
        shared = 'abcdefghijklmnopqrstuvwx' * 3
        contract = {
            'version': 3,
            'unit': 'region_compact_hexagram_recall',
            'minimum_coverage': .8,
            'minimum_floor': .6,
            'minimum_region_passes': 1,
            'regions': [
                {'id': 'opening', 'text': shared + 'AAAAAAAABBBBBBBB'},
                {'id': 'ending', 'text': shared + 'CCCCCCCCDDDDDDDD'},
            ],
        }
        content = shared + shared[:30] + 'AAAAAAAABBBBBBBBCCCCCCCCDDDDDDDD'
        forward = evaluate_body_requirements(content, contract)
        reverse_contract = dict(contract, regions=list(reversed(contract['regions'])))
        reverse = evaluate_body_requirements(content, reverse_contract)
        self.assertEqual(forward['body_coverage_pass'], reverse['body_coverage_pass'])
        self.assertEqual(
            {row['id']: (row['matched_units'], row['coverage'])
             for row in forward['body_region_results']},
            {row['id']: (row['matched_units'], row['coverage'])
             for row in reverse['body_region_results']},
        )

    def test_v3_one_fragment_instance_cannot_fill_two_regions(self):
        shared = 'abcdefghijklmnopqrstuvwx' * 3
        contract = {
            'version': 3,
            'unit': 'region_compact_hexagram_recall',
            'minimum_coverage': .8,
            'minimum_floor': .6,
            'minimum_region_passes': 1,
            'regions': [
                {'id': 'opening', 'text': shared},
                {'id': 'ending', 'text': shared},
            ],
        }
        result = evaluate_body_requirements(shared, contract)
        self.assertFalse(result['body_coverage_pass'])
        self.assertLessEqual(
            sum(row['matched_units'] for row in result['body_region_results']),
            len(compact_ngrams(shared, 6)),
        )

    def test_v3_allows_one_mild_conversion_region_but_not_a_missing_region(self):
        reference = ' '.join(
            f'{chr(97+i)*8}{j:02d}{chr(107+i)*8}{j:02d}'
            for i in range(5) for j in range(25))
        contract = body_requirements(reference)
        regions = [r['text'] for r in contract['regions']]
        mild = ' '.join(regions[:4] + [regions[4][:int(len(regions[4])*.7)]])
        self.assertTrue(evaluate_body_requirements(mild, contract)['body_coverage_pass'])
        severe = ' '.join(regions[:4] + [regions[4][:int(len(regions[4])*.4)]])
        self.assertFalse(evaluate_body_requirements(severe, contract)['body_coverage_pass'])

    def test_reviewed_region_threshold_keeps_every_floor_but_allows_reordering(self):
        contract = body_requirements(' '.join(
            f'{chr(97+i)*8}{j:02d}{chr(107+i)*8}{j:02d}'
            for i in range(5) for j in range(25)))
        contract['minimum_region_passes'] = 3
        regions = [row['text'] for row in contract['regions']]
        content = ' '.join(regions[:3] + [row[:int(len(row)*.7)] for row in regions[3:]])
        result = evaluate_body_requirements(content, contract)
        self.assertTrue(result['body_coverage_pass'])
        self.assertEqual(result['body_target_regions'], 3)
        below_floor = ' '.join(regions[:4] + [regions[4][:int(len(regions[4])*.4)]])
        self.assertFalse(evaluate_body_requirements(below_floor, contract)['body_coverage_pass'])

    def test_markdown_table_contract_rejects_flattened_cells(self):
        rule = {'id':'table', 'match_scope':'output', 'pattern':r'(?mi)^(?:\s*\|)?\s*:?-{3,}:?\s*\|\s*:?-{3,}:?\s*\|\s*:?-{3,}:?\s*(?:\|\s*)?$|<table\b'}
        valid = '| A | B | C |\n| --- | --- | --- |\n| 1 | 2 | 3 |'
        self.assertTrue(evaluate_content(valid, [], required_patterns=[rule])['content_pass'])
        self.assertTrue(evaluate_content('<table><tr><td>A</td></tr></table>', [], required_patterns=[rule])['content_pass'])
        self.assertFalse(evaluate_content('A\nB\nC\n1\n2\n3', [], required_patterns=[rule])['content_pass'])

    def test_live_conversion_is_consumed_once_and_equals_offline(self):
        from lexbench_webfetch.response_input import answer_input
        case={'url':'https://example.com','expected_any':['Answer']}
        c=EvalCase('x','x',case['url'],'test',['Answer'])
        with patch('harness.web_fetch.call_boundary.answer_input', wraps=answer_input) as convert:
            result=prepare_answer(ProviderResult(True,'Answer'),c)
        self.assertEqual(convert.call_count,1)
        with patch('lexbench_webfetch.response_input.answer_input',side_effect=AssertionError('duplicate conversion')):
            live=score_attempt(case,b'Answer',ok=True,prepared_answer=result.prepared_answer)
        self.assertEqual(live,score_attempt(case,b'Answer',ok=True))

    def test_file_runner_persists_bytes_with_relative_output_path(self):
        from harness.web_fetch.providers.http import HttpProvider
        from harness.web_fetch.providers.http_client import HttpResponse
        from harness.web_fetch.runner import run_evaluation
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

    def test_body_task_fingerprints_incidental_binary_without_accepting_it_as_body(self):
        raw = b'%PDF-' + b'payload' * 10
        case = EvalCase('body', 'body', 'https://example.com/file.pdf', 'document', [])

        result = prepare_answer(ProviderResult(True, '', files=[{'data': raw}]), case)

        files = result.metadata['file_evaluation']['files']
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertFalse(task_passed(result.ok, score_attempt(case, b'', ok=result.ok)))

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
        cases = self.historical_dataset()['cases']
        for c in cases:
            if not c.get('body_requirements'):
                continue
            with self.subTest(case=c['id']):
                reference = c['ground_truth_main_content']
                self.assertTrue(evaluate_body_requirements(reference,c['body_requirements'])['body_coverage_pass'])
                prefix_text = reference[:max(1,len(reference)//10)]
                prefix = evaluate_body_requirements(prefix_text,c['body_requirements'])
                self.assertFalse(prefix['body_coverage_pass'])
                scored = score_attempt(c, prefix_text.encode(), ok=True)
                self.assertIsNotNone(scored['word_f1'])
                self.assertGreaterEqual(scored['word_f1'], 0)

    def test_reviewed_css_scope_preserves_both_document_tocs(self):
        cases = self.historical_dataset()['cases']
        c = next(c for c in cases if c['id'] == 'live-024-cascading-style-sheets-level-2-r')
        self.assertIn('Quick Table of Contents', c['ground_truth_main_content'])
        self.assertIn('Full Table of Contents', c['ground_truth_main_content'])
        self.assertGreater(len(c['ground_truth_main_content']), 40000)

    def test_react_fields_cannot_be_omitted_or_borrowed(self):
        cases = self.historical_dataset()['cases']
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

    def test_product_prefix_fields_follow_explicit_card_boundaries(self):
        contract = {'version': 1, 'schema': 'product', 'minimum_records': 2,
                    'field_minimum_records': {'shipping': 2}}
        prefixed = ('Free shipping\nAlpha\n$10\n\n'
                    'Free shipping\nBeta\n$20')
        scored = score_attempt(
            {'url': 'https://example.test/products', 'acceptance_contract': contract},
            prefixed.encode(), ok=True)
        self.assertTrue(task_passed(True, scored))
        self.assertEqual(scored['stable_field_results'][0]['count'], 2)
        self.assertEqual(
            [(record['fields']['name'], record['fields'].get('shipping'))
             for record in scored['stable_records']],
            [('Alpha', 'Free shipping'), ('Beta', 'Free shipping')],
        )

        missing = score_attempt(
            {'url': 'https://example.test/products', 'acceptance_contract': contract},
            b'Alpha\n$10\n\nFree shipping\nBeta\n$20', ok=True)
        self.assertFalse(task_passed(True, missing))
        self.assertEqual(missing['stable_field_results'][0]['count'], 1)
        self.assertNotIn('shipping', missing['stable_records'][0]['fields'])
        self.assertEqual(missing['stable_records'][1]['fields']['shipping'], 'Free shipping')

    def test_dynamic_declared_exclusions_are_executable_not_prose_only(self):
        cases = {case['id']: case for case in self.historical_dataset()['cases']}
        supplemental = {
            'dynamic-react-cart': [
                'Add to cart',
                'Work in the Netherlands',
                'jeremy-akeze-doghouse-it-recruitment',
            ],
            'dynamic-product-reviews': [
                'data-testid="review-stars"',
                'Similar Products',
            ],
        }
        for identifier, tokens in supplemental.items():
            case = cases[identifier]
            target_only = case['ground_truth_main_content']
            clean = score_attempt(case, target_only.encode(), ok=True)
            with self.subTest(case=identifier, variant='target-only'):
                self.assertTrue(task_passed(True, clean))
                self.assertEqual(clean['forbidden_hits'], [])
            for token in tokens:
                extended = score_attempt(
                    case, f'{target_only}\n\n{token}'.encode(), ok=True)
                with self.subTest(case=identifier, supplemental=token):
                    self.assertTrue(extended['stable_structure_pass'])
                    if identifier == 'dynamic-react-cart' or token == 'Similar Products':
                        self.assertEqual(extended['forbidden_hits'], [token])
                        self.assertFalse(task_passed(True, extended))
                    else:
                        self.assertEqual(extended['forbidden_hits'], [])
                        self.assertTrue(task_passed(True, extended))

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
        self.assertIn('0/1', ranking([r],{'x':c},'media_file'))

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
