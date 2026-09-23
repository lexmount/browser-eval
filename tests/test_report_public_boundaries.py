import tempfile
import unittest
from pathlib import Path

from harness.web_fetch.report_build import copy_speed_cohort_public_files


class ReportPublicBoundaryTests(unittest.TestCase):
    def _minimal_cohort(self, root: Path) -> None:
        (root / 'dataset.json').write_text('{"cases": []}')
        (root / 'results.jsonl').write_text('')
        (root / 'speed-comparison.json').write_text('{}')

    def test_speed_cohort_copies_only_declared_public_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'source', root / 'target'
            source.mkdir()
            self._minimal_cohort(source)
            (source / 'private-session-note.txt').write_text('internal')
            (source / 'artifacts').mkdir()
            (source / 'artifacts/unbound-debug.txt').write_text('internal')

            copy_speed_cohort_public_files(source, target, {'cases': []}, [], [])

            self.assertTrue((target / 'dataset.json').is_file())
            self.assertTrue((target / 'results.jsonl').is_file())
            self.assertTrue((target / 'speed-comparison.json').is_file())
            self.assertFalse((target / 'private-session-note.txt').exists())
            self.assertFalse((target / 'artifacts/unbound-debug.txt').exists())

    def test_speed_cohort_rejects_a_symlinked_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside, source, target = root / 'outside', root / 'speed-cohort', root / 'target'
            outside.mkdir()
            self._minimal_cohort(outside)
            source.symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, 'root must not be a symlink'):
                copy_speed_cohort_public_files(source, target, {'cases': []}, [], [])
            self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
