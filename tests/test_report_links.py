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

    def test_inspection_links_use_the_declared_filter_contract(self):
        for attribute in ('data-filter','data-wmb-filter'):
            with self.subTest(attribute=attribute), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                (root/'index.html').write_text('<a href="analysis.html#inspect?query=case-a">结果</a>')
                (root/'analysis.html').write_text('<input '+attribute+'="query"><select '+attribute+'="result"><option value="">全部</option><option value="fail">失败</option></select>')
                self.assertEqual(self.run_check(root).returncode,0)
                (root/'index.html').write_text('<a href="analysis.html#inspect?unknown=case-a">结果</a>')
                self.assertEqual(self.run_check(root).returncode,1)
                (root/'index.html').write_text('<a href="analysis.html#inspect?result=invalid">结果</a>')
                self.assertEqual(self.run_check(root).returncode,1)

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


def test_virtual_capture_links_follow_same_frozen_evidence_boundary():
    import pytest
    from harness.reporting.links import validate_virtual_links
    capture = 'en/local-evidence/capture/source.html'
    files = {'index.html':{'mime':'text/html'},capture:{'mime':'text/html'}}
    content = {'index.html':f'<a href="{capture}">Evidence</a>',capture:'<link href="missing.css"><a href="remote-site-page.html">Original</a>'}
    validate_virtual_links(files,content.__getitem__)
    content['index.html'] = '<a href="local-evidence/missing.html">Missing evidence</a>'
    with pytest.raises(ValueError,match='Invalid internal link'):
        validate_virtual_links(files,content.__getitem__)
    content['index.html'] = f'<a href="{capture}">Evidence</a>'
    content[capture] = '<meta name="browser-eval-report" content="1">'+content[capture]
    with pytest.raises(ValueError,match='Invalid internal link'):
        validate_virtual_links(files,content.__getitem__)
