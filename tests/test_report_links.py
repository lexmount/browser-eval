import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ReportLinkCheckTest(unittest.TestCase):
    def run_check(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "scripts/check_report_links.py", str(root)],
            cwd=Path(__file__).resolve().parent.parent,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_accepts_existing_fragment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text(
                '<a href="detail.html#image-ranking">图片榜</a>', encoding="utf-8"
            )
            (root / "detail.html").write_text(
                '<section id="image-ranking">图片</section>', encoding="utf-8"
            )
            self.assertEqual(self.run_check(root).returncode, 0)

    def test_rejects_absent_and_empty_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for root in (Path(directory), Path(directory) / "absent"):
                with self.subTest(root=root):
                    self.assertEqual(self.run_check(root).returncode, 1)

    def test_frozen_source_navigation_is_not_report_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "local-evidence" / "capture" / "source.html"
            source.parent.mkdir(parents=True)
            source.write_text('<a href="original-site-only.html">Source navigation</a>')
            home = root / "index.html"
            home.write_text('<a href="local-evidence/capture/source.html">Frozen source</a>')
            self.assertEqual(self.run_check(root).returncode, 0)
            home.write_text('<a href="local-evidence/capture/missing.html">Missing evidence</a>')
            self.assertEqual(self.run_check(root).returncode, 1)
            home.write_text('<a href="local-evidence/capture/source.html">Frozen source</a>')
            source.write_text('<meta content="1" name="browser-eval-report"><a href="missing.html">Report navigation</a>')
            self.assertEqual(self.run_check(root).returncode, 1)

    def test_rejects_missing_fragment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text(
                '<a href="#video-ranking">视频榜</a>', encoding="utf-8"
            )
            result = self.run_check(root)
            self.assertEqual(result.returncode, 1)
            self.assertIn("missing fragment", result.stdout)

    def test_honors_document_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assets").mkdir()
            (root / "assets/style.css").write_text("", encoding="utf-8")
            (root / "nested").mkdir()
            (root / "nested/index.html").write_text(
                '<html><head><base href="../"><link rel="stylesheet" '
                'href="assets/style.css"></head></html>',
                encoding="utf-8",
            )
            self.assertEqual(self.run_check(root).returncode, 0)


if __name__ == "__main__":
    unittest.main()
