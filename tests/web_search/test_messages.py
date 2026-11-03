"""The Search catalog is keyed by the English text that its renderers pass in."""
import ast
import json
import re
import string
from pathlib import Path

import pytest

from harness.web_search.messages import report_text, zh_text
from harness.web_search.pages import reader_pages

PACKAGE = Path(__file__).resolve().parents[2] / "src/harness/web_search"
CATALOG = json.loads((PACKAGE / "messages.json").read_text(encoding="utf-8"))["zh"]
HAN = re.compile("[\u3400-\u9fff]")
PLACEHOLDER = re.compile(r"\{\w+(?::\.?\d*[a-z%]?)?\}")


def _lookups():
    """Every catalog lookup in the track, with the names its call supplies."""
    for path in sorted(set(PACKAGE.glob("*.py")) - {PACKAGE / "messages.py"}):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") in {"t", "report_text", "zh_text"}):
                continue
            source = node.args[0]
            if not (isinstance(source, ast.Constant) and isinstance(source.value, str)):
                yield path.name, node.lineno, None, None, None
                continue
            keywords = {item.arg: item.value for item in node.keywords}
            context = keywords.pop("context", None)
            keywords.pop("zh", None)
            yield path.name, node.lineno, source.value, getattr(context, "value", None), set(keywords)


def _fields(template):
    return {field for _, field, _, _ in string.Formatter().parse(template) if field}


def test_every_lookup_is_a_literal_catalog_key_and_every_key_is_used():
    lookups = list(_lookups())
    dynamic = [name for name, _, source, _, _ in lookups if source is None]
    pages = {f"navigation|{row['en']}" for presentation in (None, "comparison-report")
             for row in reader_pages(presentation, reviewed=True)}
    assert dynamic == ["pages.py"]
    used = {f"{context}|{source}" if context else source for _, _, source, context, _ in lookups if source is not None}
    assert set(CATALOG) - used == pages
    assert used - set(CATALOG) == set()


def test_placeholders_are_supplied_in_both_languages():
    for name, line, source, context, values in _lookups():
        if source is None:
            continue
        translation = CATALOG[f"{context}|{source}" if context else source]
        if values:
            assert _fields(source) <= values and _fields(translation) <= values, (name, line, source)
        else:
            assert not PLACEHOLDER.search(source) and not PLACEHOLDER.search(translation), (name, line, source)


def test_placeholder_names_do_not_carry_generated_or_collection_names():
    fields = {field for key, translation in CATALOG.items() for field in re.findall(r"\{(\w+)", key + translation)}
    assert fields and not [field for field in fields if re.search(r"(^|_)ids$|_\d+$", field)]


def test_catalog_keys_are_english_and_translations_are_catalog_only():
    assert not [key for key in CATALOG if HAN.search(key)]
    assert CATALOG == dict(sorted(CATALOG.items()))
    for name in ("comparison", "delivery", "pages", "relevance", "report", "review"):
        assert not HAN.search((PACKAGE / f"{name}.py").read_text(encoding="utf-8")), name


def test_lookup_selects_the_edition_and_fills_named_values():
    assert report_text("Benchmark", False) == "Benchmark"
    assert report_text("Benchmark", True) == CATALOG["Benchmark"]
    assert report_text("Mean ms", True, context="duration") == CATALOG["duration|Mean ms"]
    assert report_text("Known page@{cutoff}", False, cutoff=5) == "Known page@5"
    assert report_text("Known page@{cutoff}", True, cutoff=5) == CATALOG["Known page@{cutoff}"].format(cutoff=5)
    with pytest.raises(KeyError):
        zh_text("Not in the catalog")
