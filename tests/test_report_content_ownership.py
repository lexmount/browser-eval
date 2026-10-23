"""Keep the evidence browser usable without requiring the editorial article."""
import unittest
import json
from pathlib import Path
from bs4 import BeautifulSoup
from harness.web_fetch.report_components import task_pass_guide, examples, metric_legend, TRACK_CONTEXT
from harness.web_fetch.report import ranking, TRACKS
from harness.web_fetch.models import REPORT_FAMILIES


class ContentOwnershipTests(unittest.TestCase):
    def test_reusable_dataset_does_not_store_report_instance(self):
        root = Path(__file__).resolve().parents[1]
        authority = root / "evaluation/webfetch-seven-family-430"
        self.assertFalse((authority / "current-report.json").exists())
        self.assertFalse((authority / "rerun-scope.json").exists())
        assembly = json.loads((authority / "dataset-assembly.json").read_text())
        self.assertNotIn("profile", assembly)
        archive = json.loads((root / "evaluation/publication-archives.json").read_text())
        self.assertEqual(archive["schema_version"], 1)
        self.assertTrue(archive["archives"][0]["site_url"].startswith("https://"))

    def test_every_track_explains_meaning_example_and_boundary(self):
        self.assertTrue(set(TRACKS) <= set(TRACK_CONTEXT))
        for track in TRACKS:
            soup=BeautifulSoup(task_pass_guide(track),'html.parser')
            self.assertTrue(soup.select_one('.track-meaning').get_text().strip())
            self.assertIn('怎样才算任务通过',soup.select_one('.pass-contract').get_text())
            self.assertEqual([x.get_text() for x in soup.select('.track-more dt')],['一个例子','范围与边界'])
            self.assertFalse(soup.select_one('.track-more').has_attr('open'))
            self.assertIsNone(soup.select_one('.track-meaning').find_parent('details'))
            self.assertIsNone(soup.select_one('.pass-contract').find_parent('details'))
            self.assertGreaterEqual(len(soup.select('.track-flow li')),4)
            self.assertIsNone(soup.select_one('.track-flow').find_parent('details'))
            self.assertTrue(soup.select_one('.track-flow p').get_text().strip())

    def test_all_registered_task_guides_and_labels_have_english_editions(self):
        from pathlib import PurePosixPath
        from harness.web_fetch.report_i18n import TEXT, localize_html, untranslated_english_narrative
        for track, label in TRACKS.items():
            with self.subTest(track=track):
                self.assertIn(label, TEXT)
                page = localize_html(task_pass_guide(track), PurePosixPath('datasets/index.html'))
                self.assertEqual(untranslated_english_narrative(page), [])

    def test_rendering_and_value_changes_are_not_confused(self):
        text=task_pass_guide('dynamic')
        for phrase in ['不是文字经常更新','异步取数','客户端组装','嵌入子页','JavaScript 不是唯一','不要求滚动']:
            self.assertIn(phrase,text)
        self.assertIn('不能等同于“纯静态网页”',task_pass_guide('web'))
        self.assertIn('既要取得列表，也要保留登记的栏目、说明或固定字段标签',task_pass_guide('challenge'))

    def test_examples_link_only_when_present_in_dataset(self):
        absent=BeautifulSoup(task_pass_guide('dynamic','../',{}),'html.parser')
        self.assertFalse(absent.select('a[href*="datasets/"]'))
        present=BeautifulSoup(task_pass_guide('dynamic','../',{'dynamic-quotes-api':{}}),'html.parser')
        self.assertEqual(present.select_one('a[href*="datasets/"]')['href'],'../datasets/dynamic-quotes-api.html')

    def test_home_metric_details_precede_table_and_start_closed(self):
        soup=BeautifulSoup(ranking([],{},'web',compact=True),'html.parser')
        details=soup.select_one('.ranking-metric-help')
        self.assertFalse(details.has_attr('open'))
        self.assertIn('任务通过率',[x.get_text() for x in details.select('dt')])
        self.assertIsNotNone(soup.select_one('table').parent.find_previous_sibling('details',class_='ranking-metric-help'))

    def test_every_ranking_links_to_its_methodology_example(self):
        page = examples()
        for track in REPORT_FAMILIES:
            guide = task_pass_guide(track, '../')
            self.assertIn(f'../methodology/index.html#task-examples-{track}', guide)
            self.assertIn(f'id="task-examples-{track}"', page)
            self.assertNotIn('guide/index.html', guide)

    def test_local_metric_definitions_remain_visible(self):
        # Deduplication must not force readers elsewhere for a table's units.
        legend = metric_legend(['请求成功率', '任务通过率', 'F1', '耗时'])
        for label in ('请求成功率', '任务通过率', 'F1', '耗时'):
            self.assertIn(f'<dt>{label}</dt>', legend)
        self.assertNotIn('<details', legend)
