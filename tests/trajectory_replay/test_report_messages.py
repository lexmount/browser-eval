"""Replay renderers keep English inline and read Chinese from the shared catalog by stable ID."""
import ast
import json
import re
import string

import pytest

from harness.reporting.source_policy import HAN
from harness.trajectory_replay.messages import MESSAGES, report_text

PACKAGE = MESSAGES.parent
RENDERERS = ("webarena_report.py", "failure_matrix.py", "report.py", "public_webarena/report.py")
CATALOG = json.loads(MESSAGES.read_text(encoding="utf-8"))
SEMANTIC_ID = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")


def _trees():
    for name in RENDERERS:
        yield name, ast.parse((PACKAGE / name).read_text(encoding="utf-8"))


def _is_lookup(node: ast.Call) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id == "t"
    return isinstance(node.func, ast.Call) and getattr(node.func.func, "id", "") == "report_text"


def _lookups():
    for name, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_lookup(node):
                yield name, node


def _fields(template: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(template) if field}


def test_every_lookup_is_a_semantic_id_with_literal_english_source():
    sources = {}
    for name, node in _lookups():
        where = f"{name}:{node.lineno}"
        key, source = node.args
        assert isinstance(key, ast.Constant) and SEMANTIC_ID.fullmatch(key.value), where
        assert isinstance(source, ast.Constant) and isinstance(source.value, str), where
        assert not HAN.search(source.value), where
        assert sources.setdefault(key.value, source.value) == source.value, where
    assert sources


def test_catalog_entries_are_exactly_the_used_ids():
    """Keys without a dot belong to the interpretation renderer's two-language catalog."""
    used = {node.args[0].value for _, node in _lookups()}
    catalog = {key for key in CATALOG["zh"] if key != "nav.other_edition" and "." in key}
    assert catalog == used
    undotted = {key for key in CATALOG["zh"] if "." not in key}
    assert set(CATALOG["en"]) == undotted | {"nav.other_edition"}


def test_placeholders_are_supplied_in_both_languages():
    for name, node in _lookups():
        values = {item.arg for item in node.keywords}
        assert None not in values, f"{name}:{node.lineno}"
        source, translation = node.args[1].value, CATALOG["zh"][node.args[0].value]
        assert _fields(source) == values, f"{name}:{node.lineno}"
        assert _fields(translation) <= values, f"{name}:{node.lineno}"


def _language_ternaries(source: str) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.IfExp) and "zh" in ast.unparse(node.test)
        and HAN.search(ast.unparse(node.body) + ast.unparse(node.orelse))
    ]


@pytest.mark.parametrize("name", RENDERERS)
def test_renderers_have_no_inline_language_ternary(name):
    assert _language_ternaries((PACKAGE / name).read_text(encoding="utf-8")) == []


def test_language_ternary_check_rejects_inline_chinese_but_not_paths():
    assert _language_ternaries('label = "结果" if zh else "Result"') == [1]
    assert _language_ternaries('label = f"{n} 题" if lang == "zh" else f"{n} tasks"') == [1]
    assert _language_ternaries('root = "../" if zh else "../../"') == []


def test_english_is_source_text_and_chinese_comes_from_the_catalog():
    english, chinese = report_text("en"), report_text("zh")
    assert english("unit.seconds", "{seconds:.1f} s", seconds=1.25) == "1.2 s"
    assert chinese("unit.seconds", "{seconds:.1f} s", seconds=1.25) == "1.2 秒"
    assert english("no.such.id", "Source only") == "Source only"
    with pytest.raises(ValueError, match="zh/no.such.id"):
        chinese("no.such.id", "Source only")
