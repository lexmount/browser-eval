"""HTML conversion preserves executable examples, not just navigation labels."""
import shutil
import subprocess
import unittest

import markdown
from bs4 import BeautifulSoup

from harness.web_fetch.markdown import converter_command, require_converter


@unittest.skipUnless(shutil.which('node'), 'HTML conversion requires Node')
class HtmlToMarkdownTests(unittest.TestCase):
    def test_preformatted_examples_retain_literal_characters_and_whitespace(self):
        converter = converter_command(require_converter())
        scenarios = [
            ('<pre>LANG=<i>en_US.UTF-8</i></pre>', 'LANG=en_US.UTF-8'),
            ('<pre># fdisk <i>/dev/the_disk_to_be_partitioned</i></pre>',
             '# fdisk /dev/the_disk_to_be_partitioned'),
            ('<pre><code class="language-sh">  a_b &lt; x &amp;&amp; y\n\t**literal**</code></pre>',
             '  a_b < x && y\n\t**literal**'),
            ('<pre>```\n<i>_literal_</i>\n```</pre>', '```\n_literal_\n```'),
        ]
        for html, expected in scenarios:
            with self.subTest(expected=expected):
                result = subprocess.run(converter, input=html,
                                        text=True, capture_output=True, check=True)
                self.assertIn(expected, result.stdout)
                rendered = BeautifulSoup(markdown.markdown(result.stdout,
                    extensions=['fenced_code']), 'html.parser')
                code = rendered.select_one('pre code')
                self.assertIsNotNone(code)
                self.assertEqual(code.get_text().removesuffix('\n'), expected.expandtabs(4))
        ordinary = subprocess.run(converter,
            input='<p><em>ordinary emphasis</em> <a href="https://example.com">link</a></p>',
            text=True, capture_output=True, check=True).stdout
        rendered = BeautifulSoup(markdown.markdown(ordinary), 'html.parser')
        self.assertEqual(rendered.em.get_text(), 'ordinary emphasis')
        self.assertEqual(rendered.a['href'], 'https://example.com')
