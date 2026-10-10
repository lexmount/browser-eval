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
