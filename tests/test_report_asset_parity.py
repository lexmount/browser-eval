import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.report_asset_parity import ArticleAssetParity


class ArticleAssetParityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        folder = self.root / 'assets/editorial'
        folder.mkdir(parents=True)
        self.raw = {'zh': b'Chinese PNG', 'en': b'English PNG'}
        assets = {}
        for locale, raw in self.raw.items():
            (folder / f'{locale}.png').write_bytes(raw)
            assets[locale] = {'file': f'{locale}.png', 'sha256': hashlib.sha256(raw).hexdigest()}
        self.manifest = self.root / 'manifest.json'
        self.manifest.write_text(json.dumps({'figures': [{'slot': 'overview_figure', 'assets': assets}]}))
        self.parity = ArticleAssetParity(self.root, self.manifest)

    def rewrite(self, locale, attrs, figure='overview_figure', route='guide/article.html', tag='a'):
        return self.parity.rewrite(self.root / (('en/' if locale == 'en' else '') + route), figure, tag, attrs)

    def test_declared_local_and_embedded_variants_compare_equal(self):
        for attribute, tag in [('href', 'a'), ('src', 'img')]:
            zh = [(attribute, '../assets/editorial/zh.png'), ('data-en-' + attribute, '../assets/editorial/en.png')]
            en = [(attribute, '../../assets/editorial/en.png')]
            self.assertEqual(self.rewrite('zh', zh, tag=tag), self.rewrite('en', en, tag=tag))
            uri = {locale: 'data:image/png;base64,' + base64.b64encode(raw).decode() for locale, raw in self.raw.items()}
            zh = [(attribute, uri['zh']), ('data-en-' + attribute, uri['en'])]
            en = [(attribute, uri['en'])]
            self.assertEqual(self.rewrite('zh', zh, tag=tag), self.rewrite('en', en, tag=tag))

    def test_undeclared_links_wrong_alternates_and_wrong_scope_remain_visible(self):
        attrs = [('href', '../assets/editorial/zh.png'), ('data-en-href', '../assets/editorial/unregistered.png')]
        normalized = self.rewrite('zh', attrs)
        self.assertIn(attrs[1], normalized)
        self.assertNotEqual(normalized, self.rewrite('en', [('href', '../../assets/editorial/en.png')]))
        unknown = [('href', 'https://example.com/unregistered.png')]
        self.assertEqual(unknown, self.rewrite('zh', unknown))
        for options in ({'figure': 'unregistered'}, {'route': 'attempts/example.html'}):
            self.assertEqual(attrs, self.rewrite('zh', attrs, **options))
        wrong_locale = [('href', '../assets/editorial/en.png')]
        self.assertEqual(wrong_locale, self.rewrite('zh', wrong_locale))

    def test_asset_bytes_must_match_manifest(self):
        (self.root / 'assets/editorial/en.png').write_bytes(b'Changed PNG')
        with self.assertRaisesRegex(ValueError, 'differs from manifest'):
            ArticleAssetParity(self.root, self.manifest)
