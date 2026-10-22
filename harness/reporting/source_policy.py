"""Check the boundary between implementation and localized report content."""
from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

HAN = re.compile(r"[\u3400-\u9fff]")
CATALOG_RENDERERS = (
    "scripts/moli_report/app.js",
    "harness/trajectory_replay/interpretation.py",
    "harness/web_fetch/methods.py",
    "harness/web_fetch/report_interfaces.py",
)


def check_source_languages(root: Path) -> list[str]:
    findings = []
    for directory in ("harness", "scripts"):
        for path in sorted((root / directory).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for token in tokenize.generate_tokens(io.StringIO(text).readline):
                if token.type in (tokenize.COMMENT, tokenize.NAME) and HAN.search(token.string):
                    findings.append(f"{path.relative_to(root)}:{token.start[0]}: use English in code and comments")
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    if HAN.search(ast.get_docstring(node) or ""):
                        findings.append(f"{path.relative_to(root)}:{getattr(node, 'lineno', 1)}: use English in docstrings")
    for relative in CATALOG_RENDERERS:
        path = root / relative
        if path.is_file() and HAN.search(path.read_text(encoding="utf-8")):
            findings.append(f"{relative}: move report messages into the language catalog")
    for name in ("current-report.json", "current-run.json", "current-comparison.json"):
        for path in (root / "evaluation").rglob(name):
            findings.append(f"{path.relative_to(root)}: keep report instances outside Git")
    return findings
