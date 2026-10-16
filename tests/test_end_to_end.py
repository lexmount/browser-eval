import hashlib
import tempfile
import unittest
from pathlib import Path
from lexbench_webfetch.models import EvalCase
from harness.web_fetch.providers.fixture import FixtureProvider
from harness.web_fetch.report_build import build_report
from harness.web_fetch.runner import run_evaluation


class EndToEndTest(unittest.TestCase):
    def test_fixture_run_and_current_report(self):
        dataset={'schema_version':1,'id':'test','name':'Test','tool':'web_fetch','cases':[
            {'id':cid,'name':cid,'url':'https://example.com/'+cid,'category':'static','track':'web','task_prompt':'Read expected content','expected_any':['Expected']}
            for cid in ['ok','fail']]}
        with tempfile.TemporaryDirectory() as directory:
            run=Path(directory)/'run'
            records,_=run_evaluation(output_dir=run,dataset_meta=dataset,dataset_path=Path('dataset.json'),cases=[EvalCase(**c) for c in dataset['cases']],providers=[FixtureProvider('fixture',{'type':'fixture','fail_cases':['fail']})],provider_config_path=Path('providers.json'),runs=1,timeout=1,delay=0)
            report=build_report(run,run/'report')
            self.assertTrue(report.exists())
            analysis=(report.parent/'analysis/index.html').read_text()
            self.assertIn('value="fail" selected',analysis)
            self.assertIn('失败分类',analysis)
            self.assertIn('data-result="pass"',analysis)
            self.assertIn('data-result="fail"',analysis)
            self.assertIn('fixture__fail__r001.html',analysis)
            attempt=(report.parent/'attempts/fixture__fail__r001.html').read_text()
            self.assertIn('Configured fixture failure',attempt)
            self.assertIn('供应商请求 ID',attempt)
            self.assertIn(records[1]['run_id'],analysis)
            self.assertIn('开始时间（UTC）',attempt)
            self.assertGreaterEqual(attempt.count('<pre data-kind='),3)
            self.assertIn('查看本次真实日志',attempt)
            for kind in ('reference', 'output', 'metadata'):
                self.assertIn(f'<pre data-kind="{kind}">', attempt)
            self.assertTrue((report.parent/'review/index.html').exists())
            for removed in ['capabilities','benchmarks','execution','market']:
                self.assertFalse((report.parent/removed).exists())
            for page in report.parent.rglob('*.html'):
                self.assertNotIn('人工二次复核',page.read_text())
                self.assertNotIn('需辅助',page.read_text())
            for r in records:
                self.assertEqual(hashlib.sha256((report.parent/r['artifact_path']).read_bytes()).hexdigest(),r['metrics']['content_sha256'])
            (report.parent/'stale.html').write_text('stale generated page')
            self.assertEqual(build_report(run,report.parent),report)
            self.assertFalse((report.parent/'stale.html').exists())
            untouched=run/'manual';untouched.mkdir();(untouched/'notes.txt').write_text('user data')
            with self.assertRaises(ValueError):build_report(run,untouched)
            self.assertEqual((untouched/'notes.txt').read_text(),'user data')
