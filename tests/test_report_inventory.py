import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReportInventoryTests(unittest.TestCase):
    def setUp(self):
        self.inventory = json.loads((ROOT/'configs/evaluation-inventory.json').read_text())
        digest = hashlib.sha256(b'fixture').hexdigest()
        self.dataset = {
            'cases': [{'id': cid, 'track': track, 'ground_truth_main_content': 'fixture',
                       'source_verification': {'reference_sha256': digest}}
                      for track, group in self.inventory.items() for cid in group['case_ids']],
            'coverage_plan': {track: group['providers'] for track, group in self.inventory.items()},
        }
        self.records = [
            {'provider': provider, 'case_id': cid, 'attempt_id': f'{provider}__{cid}',
             'artifact_path': f'artifacts/{provider}/{cid}.txt', 'ok': True,
             'metrics': {'content_sha256': digest}}
            for group in self.inventory.values() for cid in group['case_ids']
            for provider in group['providers']
        ]

    def check(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        root = directory / 'report'
        authority = directory / 'authority'
        files = {
            root/'dataset.json': json.dumps(self.dataset),
            root/'results.jsonl': '\n'.join(json.dumps(r, ensure_ascii=False) for r in self.records),
            root/'execution.json': json.dumps({'batches': [{'attempt_ids': [r['attempt_id'] for r in self.records]}]}),
            root/'index.html': '<h2>综合通过率总榜</h2> <h2>6 类任务概览</h2> 本类最高： 判分完整性 <h2>6 类分榜</h2>',
            root/'methodology/index.html': '计入耗时 不计入耗时 供应商上报的服务端耗时只作附加证据 形成可送入统一评分器的最终答案',
            authority/'configs/evaluation-inventory.json': json.dumps(self.inventory),
        }
        from harness.web_fetch.reliability import assess_reliability
        files[root/'reliability.json'] = json.dumps(assess_reliability(self.dataset,self.records))
        for path, text in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        for record in self.records:
            path = root / record['artifact_path']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture')
        for case in self.dataset['cases']:
            path = root / 'references' / (case['id'] + '.txt')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(case['ground_truth_main_content'])
        from harness.report_languages import english_notice
        (root / 'report-languages.json').write_text(json.dumps({'primary_language': 'zh', 'languages': ['zh'], 'entrypoint': 'index.html'}))
        (root / 'en').mkdir()
        (root / 'en/index.html').write_text(english_notice())
        from harness.web_fetch.report_check import main
        main([str(root)], authority_root=authority)

    def test_reviewed_inventory_passes(self):
        self.check()

    def test_unicode_record_strings_preserve_the_reviewed_inventory(self):
        self.records[0]['provider_metadata'] = {'fixture_text': '文本\u0085内容\u2028段落\u2029结束'}
        self.check()

    def test_duplicate_dataset_case_rejected(self):
        self.dataset['cases'].append(copy.deepcopy(self.dataset['cases'][-1]))
        with self.assertRaisesRegex(ValueError, 'Duplicate dataset case'):
            self.check()

    def test_duplicate_inventory_case_rejected(self):
        self.inventory['dynamic']['case_ids'].append(self.inventory['dynamic']['case_ids'][0])
        with self.assertRaisesRegex(ValueError, 'Duplicate inventory case'):
            self.check()

    def test_deleted_case_and_records_rejected(self):
        cid = self.inventory['dynamic']['case_ids'][-1]
        self.dataset['cases'] = [c for c in self.dataset['cases'] if c['id'] != cid]
        self.records = [r for r in self.records if r['case_id'] != cid]
        with self.assertRaisesRegex(ValueError, 'reviewed independent inventory'):
            self.check()


if __name__ == '__main__':
    unittest.main()
