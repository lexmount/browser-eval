"""Public HTML conversion behavior and explicit dependency selection."""
import json
import subprocess

import pytest

from harness.web_fetch.markdown import (
    DEFAULT_PACKAGE,
    converter_command,
    require_converter,
)


def test_converter_preserves_headings_links_table_and_chinese_from_another_directory(tmp_path):
    package = require_converter(str(DEFAULT_PACKAGE))
    html = '<h1>中文标题</h1><p>独立内容 <a href="https://example.test/detail">详细说明</a></p><table><tr><th>指标</th><th>值</th></tr><tr><td>结果</td><td>42</td></tr></table>'
    result = subprocess.run(converter_command(package), input=html, text=True,
                            capture_output=True, check=True, cwd=tmp_path)
    assert '# 中文标题' in result.stdout
    assert '[详细说明](https://example.test/detail)' in result.stdout
    table = [[cell.strip() for cell in line.split('|')[1:-1]]
             for line in result.stdout.splitlines() if line.startswith('|')]
    assert table[0] == ['指标', '值']
    assert table[2] == ['结果', '42']


@pytest.mark.parametrize('version', [None, '1.0.0', '2.0.1'])
def test_converter_rejects_absent_or_wrong_dependency_without_using_another_copy(tmp_path, version):
    if version is not None:
        (tmp_path / 'package.json').write_text(json.dumps({'version': version}))
    with pytest.raises(ValueError, match='node-html-markdown 2.0.0'):
        require_converter(str(tmp_path))


@pytest.mark.parametrize('markup', ['<script setup>', '<span data-value="literal">'])
def test_converter_keeps_literal_markup_in_link_labels_and_prose(tmp_path, markup):
    from html import escape
    from harness.web_fetch.scoring import score_attempt
    from harness.web_fetch.metrics import task_passed

    package = require_converter(str(DEFAULT_PACKAGE))
    literal = escape(markup)
    html = (f'<p>Syntax: {literal}</p>'
            f'<a href="https://example.test/syntax">{literal}</a>'
            '<p>Following explanation remains readable.</p>'
            '<span data-secret="not reader text">Visible text</span>')
    result = subprocess.run(converter_command(package), input=html, text=True,
                            capture_output=True, check=True, cwd=tmp_path)
    case = {'url': 'https://example.test/', 'required_facts': [
        {'id': 'following-prose', 'alternatives': ['Following explanation remains readable.']},
        {'id': 'literal-syntax', 'alternatives': [markup]},
    ]}
    assert task_passed(True, score_attempt(case, result.stdout.encode(), ok=True))
    assert 'not reader text' not in result.stdout


def test_converter_keeps_fenced_literal_code_without_prose_escaping(tmp_path):
    package = require_converter(str(DEFAULT_PACKAGE))
    html = '<pre><code class="language-html">&lt;span&gt;Example&lt;/span&gt;</code></pre>'
    result = subprocess.run(converter_command(package), input=html, text=True,
                            capture_output=True, check=True, cwd=tmp_path)
    assert '```html\n<span>Example</span>\n```' in result.stdout
