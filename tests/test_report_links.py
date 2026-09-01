import subprocess
import tempfile
import unittest
from pathlib import Path


class ReportLinkCheckTest(unittest.TestCase):
    def run_check(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", "scripts/check_report_links.py", str(root)],
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

    def test_rejects_missing_fragment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text(
                '<a href="#video-ranking">视频榜</a>', encoding="utf-8"
            )
            result = self.run_check(root)
            self.assertEqual(result.returncode, 1)
            self.assertIn("missing fragment", result.stdout)


if __name__ == "__main__":
    unittest.main()
