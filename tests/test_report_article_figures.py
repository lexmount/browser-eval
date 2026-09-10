import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.web_fetch.report_article_figures import render_article_figures


class ArticleFigureTests(unittest.TestCase):
    def test_verified_images_are_copied_and_embedded_without_changing_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'editorial/moli-webfetch'
            source.mkdir(parents=True)
            png = bytes.fromhex('89504e470d0a1a0a0000000d49484452') + b'fixture-png'
            en = png + b'english-variant'
            for name, raw in [('zh.png', png), ('en.png', en)]:
                (source/name).write_bytes(raw)
            manifest = root/'figures.json'
            manifest.write_text(json.dumps({'figures':[{'slot':'overview_figure', 'kind':'measured-summary',
                'alt':'Summary', 'caption':'Measured results', 'assets':{
                    'zh':{'file':'zh.png', 'sha256':hashlib.sha256(png).hexdigest()},
                    'en':{'file':'en.png', 'sha256':hashlib.sha256(en).hexdigest()}}}]}))
            out = root/'report'
            with patch('harness.web_fetch.report_article_figures.data_home', return_value=root), patch('harness.web_fetch.report_article_figures.MANIFEST', manifest):
                online, embedded, provenance = render_article_figures(out)
                self.assertEqual((out/'assets/editorial/zh.png').read_bytes(), png)
                self.assertEqual((out/'assets/editorial/en.png').read_bytes(), en)
                self.assertIn('data-en-src="../assets/editorial/en.png"', online['overview_figure'])
                self.assertIn(base64.b64encode(png).decode(), embedded['overview_figure'])
                self.assertIn(base64.b64encode(en).decode(), embedded['overview_figure'])
                self.assertEqual(len(provenance), 2)
                (source/'zh.png').write_bytes(png + b'changed')
                with self.assertRaisesRegex(ValueError, 'illustration changed'):
                    render_article_figures(out)


if __name__ == '__main__':
    unittest.main()
