"""The Browser Use report keeps English inline and reads Chinese from its catalog by stable ID."""

import ast
import json
import re
import string
from pathlib import Path

from harness.browser_use import report as report_module
from harness.browser_use.report_i18n import MESSAGES
from harness.reporting.source_policy import HAN
from tests.browser_use.test_report import build_browser_use_fixture

RENDERERS = ("report.py", "report_check.py")
LOOKUPS = {"t", "_ZH", "_EN", "_translated"}
CATALOG = json.loads(MESSAGES.read_text(encoding="utf-8"))
SEMANTIC_ID = re.compile(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)+")


def _lookups():
    for name in RENDERERS:
        tree = ast.parse((MESSAGES.parent / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # `_translated` forwards its own `key` and `source` parameters.
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", "") in LOOKUPS
                and getattr(node.args[0], "id", "") != "key"
            ):
                yield f"{name}:{node.lineno}", node


def _fields(template: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(template) if field}


def test_every_lookup_has_a_semantic_id_and_literal_english_source() -> None:
    sources: dict[str, str] = {}
    for where, node in _lookups():
        key, source = node.args
        assert isinstance(key, ast.Constant) and SEMANTIC_ID.fullmatch(key.value), where
        assert isinstance(source, ast.Constant) and isinstance(source.value, str), where
        assert not HAN.search(source.value), where
        assert sources.setdefault(key.value, source.value) == source.value, where
    assert sources


def test_catalog_entries_are_exactly_the_used_ids() -> None:
    assert set(CATALOG["zh"]) == {node.args[0].value for _, node in _lookups()}


def test_placeholders_are_supplied_in_both_languages() -> None:
    for where, node in _lookups():
        values = {item.arg for item in node.keywords}
        assert _fields(node.args[1].value) <= values, where
        assert _fields(CATALOG["zh"][node.args[0].value]) <= values, where


def test_renderers_hold_no_chinese_text() -> None:
    for name in RENDERERS:
        assert not HAN.search((MESSAGES.parent / name).read_text(encoding="utf-8")), name


def test_browser_use_change_records_have_complete_structured_english() -> None:
    data = json.loads(Path("research/report-changes.json").read_text(encoding="utf-8"))
    assert data["history_scope_i18n"]["en"]
    for collection in ("browser_use_reviews", "browser_use_releases"):
        for record in data[collection]:
            english = record["i18n"]["en"]
            for field in ("title", "summary") if collection.endswith("releases") else (
                "title",
                "status",
                "scope",
                "findings",
                "corrections",
                "remaining",
            ):
                assert english[field]
                assert not re.search(r"[一-鿿]", english[field])
            if collection.endswith("releases"):
                assert len(english["changes"]) == len(record.get("changes") or [])
                assert all(
                    len(target) == len(source) == 4
                    for source, target in zip(
                        record.get("changes") or [], english["changes"], strict=True
                    )
                )
                assert not re.search(
                    r"[一-鿿]",
                    json.dumps(english["changes"], ensure_ascii=False),
                )


def test_release_fields_read_the_edition_being_rendered(monkeypatch) -> None:
    data = json.loads(Path("research/report-changes.json").read_text(encoding="utf-8"))
    release = data["browser_use_releases"][0]
    assert report_module._record_text(release, "title") == release["title"]
    monkeypatch.setattr(report_module, "t", report_module._EN)
    assert report_module._record_text(release, "title") == release["i18n"]["en"]["title"]


def test_english_edition_is_rendered_directly_and_keeps_dataset_text(
    tmp_path: Path, monkeypatch
) -> None:
    output, _, _ = build_browser_use_fixture(tmp_path, monkeypatch, "en")

    home = (output / "en/index.html").read_text(encoding="utf-8")
    assert '<html lang="en">' in home
    assert "Core metrics across" in home and "切换主题" not in home
    # The selection rationale of this task has no English and stays as written.
    task_page = (output / "en/datasets/475.html").read_text(encoding="utf-8")
    assert "创建一个命名明确的空仓库" in task_page
    assert report_module.t is report_module._ZH
