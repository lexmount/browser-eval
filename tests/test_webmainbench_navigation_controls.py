import unittest

from scripts.diagnose_webmainbench_navigation import controls, TRIGGER


class NavigationControlTests(unittest.TestCase):
    def test_original_is_preserved_and_ablation_removes_only_the_guard(self):
        html = '<p>Frozen text</p><script>' + TRIGGER + '</script>'
        result = controls(html)
        self.assertEqual(len(result), 5)
        self.assertEqual(result['original'], html)
        self.assertEqual(result['original_without_frame_guard'], '<p>Frozen text</p><script></script>')
        self.assertEqual(result['minimal_without_child_frame'],
                         result['minimal_with_child_frame'].replace('<iframe></iframe>', ''))
        self.assertEqual(result['minimal_correct_frame_guard'],
                         result['minimal_with_child_frame'].replace('top.frames.length!=0', 'top !== self'))

    def test_changed_source_guard_fails_closed(self):
        for html in ('<html></html>', TRIGGER * 2):
            with self.assertRaises(ValueError):
                controls(html)
