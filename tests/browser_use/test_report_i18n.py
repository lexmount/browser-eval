import json
import re
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

from harness.browser_use.report_i18n import localize_html, translate_text


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.values: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.stack:
            self.stack = self.stack[: len(self.stack) - 1 - self.stack[::-1].index(tag)]

    def handle_data(self, data: str) -> None:
        if not any(tag in self.stack for tag in ("pre", "code", "script", "style")):
            self.values.append(data)


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
                assert not re.search(r"[\u4e00-\u9fff]", english[field])
            if collection.endswith("releases"):
                assert len(english["changes"]) == len(record.get("changes") or [])
                assert all(len(target) == len(source) == 4 for source, target in zip(record.get("changes") or [], english["changes"], strict=True))
                assert not re.search(r"[\u4e00-\u9fff]", json.dumps(english["changes"], ensure_ascii=False))


def test_english_ui_is_localized_but_measured_text_is_preserved() -> None:
    source = (
        '<html lang="zh-CN"><body><h1>Browser Use 联合多模型能力实测</h1>'
        '<h2>查看本题运行结果</h2><p>评测集页面保持为冻结输入；实测输出、判分和轨迹在结果页查看。</p>'
        '<div><b>程序自动定位：</b><b>核验根因子类：</b><b>核验原因：</b></div>'
        '<pre>{"raw": "原始测量证据"}</pre><p>创建一个命名明确的空仓库</p></body></html>'
    )
    localized = localize_html(source, PurePosixPath("results/example.html"))
    parser = _VisibleText()
    parser.feed(localized)
    visible = " ".join(parser.values)
    for leaked in ("联合多模型能力实测", "查看本题运行结果", "评测集页面保持", "程序自动定位", "核验根因子类", "核验原因"):
        assert leaked not in visible
    assert "原始测量证据" in localized
    assert "创建一个命名明确的空仓库" in visible


def test_release_translation_comes_from_structured_record() -> None:
    data = json.loads(Path("research/report-changes.json").read_text(encoding="utf-8"))
    release = data["browser_use_releases"][0]
    assert translate_text(release["title"]) == release["i18n"]["en"]["title"]


def test_excluded_task_ids_use_english_punctuation() -> None:
    source = '<html lang="zh-CN"><body><p>本轮剔除 3 题：#1、#2、#3。</p></body></html>'
    localized = localize_html(source, PurePosixPath("analysis/index.html"))

    assert "3 tasks excluded in this run: #1, #2, #3." in localized
    assert "、" not in localized


def test_all_release_and_review_copy_localizes_without_chinese() -> None:
    data = json.loads(Path("research/report-changes.json").read_text(encoding="utf-8"))
    values = [data["history_scope"]]
    for record in data["browser_use_reviews"]:
        values.extend(
            record[field]
            for field in ("title", "status", "scope", "findings", "corrections", "remaining")
        )
    for record in data["browser_use_releases"]:
        values.extend((record["title"], record["summary"]))
        values.extend(cell for row in record.get("changes") or [] for cell in row)

    source = '<html lang="zh-CN"><body>' + "".join(
        f"<p>{value}</p>" for value in values
    ) + "</body></html>"
    localized = localize_html(source, PurePosixPath("changes/index.html"))
    parser = _VisibleText()
    parser.feed(localized)

    assert not re.search(r"[\u4e00-\u9fff]", " ".join(parser.values))
