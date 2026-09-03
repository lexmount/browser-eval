"""Keep the evidence browser usable without requiring the editorial article."""
import unittest
from harness.web_fetch.report_components import task_pass_guide, examples, metric_legend


class ContentOwnershipTests(unittest.TestCase):
    def test_every_ranking_links_to_its_methodology_example(self):
        page = examples()
        for track in ('web', 'dynamic', 'document', 'image', 'video', 'challenge'):
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
