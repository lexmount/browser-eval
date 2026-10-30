"""Maintainer code uses English; original-language task evidence stays intact."""
import ast
import io
from pathlib import Path
import re
import tokenize

import lexbench_webfetch


HAN = re.compile(r"[\u3400-\u9fff]")


def language_findings(source):
    findings = [token.start[0] for token in tokenize.generate_tokens(io.StringIO(source).readline)
                if token.type in (tokenize.NAME, tokenize.COMMENT) and HAN.search(token.string)]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if HAN.search(ast.get_docstring(node) or ""):
                findings.append(getattr(node, 'lineno', 1))
    return findings


def test_original_language_evidence_is_allowed_but_maintainer_text_is_english():
    assert language_findings('value = "原文引述"\n') == []
    assert language_findings('"""维护说明"""\n# 实现注释\n结果 = "原文引述"\n') == [2, 3, 1]


def test_installed_package_identifiers_comments_and_docstrings_use_english():
    root = Path(lexbench_webfetch.__file__).resolve().parent
    findings = [(path.relative_to(root), line) for path in root.rglob('*.py')
                for line in language_findings(path.read_text(encoding='utf-8'))]
    assert findings == []
