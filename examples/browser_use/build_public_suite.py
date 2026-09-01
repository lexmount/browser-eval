"""Build the checked-in public-web BrowserTool evaluation suite.

The generator keeps task metadata, exact oracles and action sequences in one
place.  It never fetches a page and never changes an oracle at evaluation time.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LEXBENCH_BASIS = (
    "https://github.com/lexmount/Lexbench-Headless-Browser/tree/"
    "3f1580a20922fc3f339b5fc279d9bbdb06d5d194/tasks/scenarios"
)
MOLI_TIMING_BASIS = (
    "https://github.com/lexmount/moli/blob/"
    "bb9ecdd24c5d861b383cb3529ff60a9ffe85ed42/"
    "moli-protocol-server/src/cdp_scheduler.rs"
)


def nav(url: str, save_as: str = "page") -> dict[str, object]:
    return {"action": "navigate", "url": url, "save_as": save_as}


def text(selector: str, save_as: str) -> dict[str, object]:
    return {"action": "get_text", "selector": selector, "save_as": save_as, "strip": True}


def click(selector: str, timeout_ms: int | None = None) -> dict[str, object]:
    result: dict[str, object] = {"action": "click", "selector": selector}
    if timeout_ms is not None:
        result["timeout_ms"] = timeout_ms
    return result


def fill(selector: str, value: str) -> dict[str, object]:
    return {"action": "fill", "selector": selector, "text": value}


def select(selector: str, value: str) -> dict[str, object]:
    return {"action": "select", "selector": selector, "value": value}


def prop(selector: str, name: str, save_as: str) -> dict[str, object]:
    return {
        "action": "get_property",
        "selector": selector,
        "name": name,
        "save_as": save_as,
    }


def attr(selector: str, name: str, save_as: str) -> dict[str, object]:
    return {
        "action": "get_attribute",
        "selector": selector,
        "name": name,
        "save_as": save_as,
    }


def task(
    task_id: str,
    title: str,
    difficulty: str,
    tags: list[str],
    calls: list[dict[str, object]],
    expected: dict[str, str],
    *,
    target_url: str,
    derivation: str,
    timing_probe: bool = False,
) -> dict[str, object]:
    basis = [LEXBENCH_BASIS]
    if timing_probe:
        basis.append(MOLI_TIMING_BASIS)
    return {
        "id": task_id,
        "title": title,
        "difficulty": difficulty,
        "tags": [difficulty, "public-web", *tags, "exact-oracle"],
        "tool_calls": calls,
        "assertions": [{"output": output, "equals": value} for output, value in expected.items()],
        "ground_truth": {
            "oracle": "deterministic_exact",
            "expected_outputs": expected,
            "expected_actions": [str(call["action"]) for call in calls],
            "derivation": derivation,
        },
        "source": {
            "kind": "public_web",
            "url": target_url,
            "design_basis": basis,
        },
    }


EXAMPLE = "https://example.com/"
PYPI = "https://pypi.org/project/requests/2.32.5/"
ARXIV = "https://arxiv.org/abs/1706.03762"
PYTHON = "https://docs.python.org/3/tutorial/index.html"
RFC = "https://www.rfc-editor.org/rfc/rfc9110.html"
MDN = "https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API"
MOLI = "https://github.com/lexmount/moli"
LEXBENCH = "https://github.com/lexmount/Lexbench-Headless-Browser"
QUOTES = "https://quotes.toscrape.com/"
BOOKS = "https://books.toscrape.com/"
SELENIUM = "https://www.selenium.dev/selenium/web/web-form.html"
SELENIUM_DYNAMIC = "https://www.selenium.dev/selenium/web/dynamic.html"


TASKS = [
    # Simple: one public page and one exact observation.
    task(
        "example-heading",
        "读取 Example Domain 标题",
        "simple",
        ["observation"],
        [nav(EXAMPLE), text("h1", "answer")],
        {"answer": "Example Domain"},
        target_url=EXAMPLE,
        derivation="IANA 保留示例域名首页的唯一 h1。",
    ),
    task(
        "pypi-requests-pinned",
        "读取固定版本 requests 包标题",
        "simple",
        ["package-index"],
        [nav(PYPI), text(".project-header__name", "answer")],
        {"answer": "requests 2.32.5"},
        target_url=PYPI,
        derivation="固定版本 PyPI 页面项目标题。",
    ),
    task(
        "arxiv-transformer-title",
        "读取 Transformer 论文标题",
        "simple",
        ["research"],
        [nav(ARXIV), text("h1.title", "answer")],
        {"answer": "Title:Attention Is All You Need"},
        target_url=ARXIV,
        derivation="固定 arXiv 论文编号页面标题。",
    ),
    task(
        "python-tutorial-heading",
        "读取 Python 教程标题",
        "simple",
        ["documentation"],
        [nav(PYTHON), text("h1", "answer")],
        {"answer": "The Python Tutorial¶"},
        target_url=PYTHON,
        derivation="Python 官方教程索引页唯一 h1。",
    ),
    task(
        "rfc9110-number",
        "读取 RFC 9110 编号",
        "simple",
        ["standard"],
        [nav(RFC), text("#rfcnum", "answer")],
        {"answer": "RFC 9110"},
        target_url=RFC,
        derivation="RFC Editor 固定标准页编号。",
    ),
    task(
        "mdn-fetch-heading",
        "读取 MDN Fetch API 标题",
        "simple",
        ["documentation"],
        [nav(MDN), text("h1", "answer")],
        {"answer": "Fetch API"},
        target_url=MDN,
        derivation="MDN Fetch API 文档唯一 h1。",
    ),
    task(
        "github-moli-readme-heading",
        "读取 Moli README 标题",
        "simple",
        ["repository"],
        [nav(MOLI), text("article.markdown-body h1", "answer")],
        {"answer": "Moli"},
        target_url=MOLI,
        derivation="Moli 仓库 README 的一级标题。",
    ),
    task(
        "github-lexbench-readme-heading",
        "读取 Lexbench README 标题",
        "simple",
        ["repository"],
        [nav(LEXBENCH), text("article.markdown-body h1", "answer")],
        {"answer": "Lexbench-Headless-Browser"},
        target_url=LEXBENCH,
        derivation="Lexbench 仓库 README 的一级标题。",
    ),
    task(
        "quotes-first-author",
        "读取首页首位名言作者",
        "simple",
        ["catalogue"],
        [nav(QUOTES), text(".quote:first-of-type .author", "answer")],
        {"answer": "Albert Einstein"},
        target_url=QUOTES,
        derivation="Quotes to Scrape 首页第一条记录作者。",
    ),
    task(
        "books-catalogue-heading",
        "读取图书目录标题",
        "simple",
        ["catalogue"],
        [nav(BOOKS), text(".page-header h1", "answer")],
        {"answer": "All products"},
        target_url=BOOKS,
        derivation="Books to Scrape 根目录标题。",
    ),
    # Medium: distinct control and navigation spaces, not repeated click-only probes.
    task(
        "selenium-form-submit",
        "填写文本并提交公开表单",
        "medium",
        ["form", "fill", "submit"],
        [
            nav(SELENIUM),
            fill("input[name='my-text']", "Lexmount public eval"),
            click("button"),
            text("#message", "receipt"),
        ],
        {"receipt": "Received!"},
        target_url=SELENIUM,
        derivation="Selenium 官方公开表单提交后的固定回执。",
    ),
    task(
        "selenium-select-option",
        "选择公开下拉选项并读取选中项",
        "medium",
        ["form", "select"],
        [
            nav(SELENIUM),
            select("select[name='my-select']", "2"),
            text("select[name='my-select'] option:checked", "answer"),
        ],
        {"answer": "Two"},
        target_url=SELENIUM,
        derivation="Selenium 官方表单 value=2 的固定标签。",
    ),
    task(
        "selenium-checkbox-state",
        "切换公开复选框并读取状态",
        "medium",
        ["native-control", "checkbox"],
        [nav(SELENIUM), click("#my-check-2"), prop("#my-check-2", "checked", "checked")],
        {"checked": "true"},
        target_url=SELENIUM,
        derivation="未选复选框点击后的 checked DOM 属性。",
    ),
    task(
        "selenium-radio-state",
        "切换公开单选框并读取状态",
        "medium",
        ["native-control", "radio"],
        [nav(SELENIUM), click("#my-radio-2"), prop("#my-radio-2", "checked", "checked")],
        {"checked": "true"},
        target_url=SELENIUM,
        derivation="未选单选框点击后的 checked DOM 属性。",
    ),
    task(
        "quotes-page-two",
        "翻页后读取第二页首位作者",
        "medium",
        ["pagination"],
        [nav(f"{QUOTES}page/2/"), text(".quote:first-of-type .author", "answer")],
        {"answer": "Marilyn Monroe"},
        target_url=QUOTES,
        derivation="公开分页路由第二页第一条记录作者。",
    ),
    task(
        "quotes-author-detail",
        "打开作者详情并读取出生日期",
        "medium",
        ["detail", "navigation"],
        [
            nav(QUOTES),
            click(".quote:first-of-type a[href*='/author/']"),
            text(".author-born-date", "answer"),
        ],
        {"answer": "March 14, 1879"},
        target_url=QUOTES,
        derivation="Albert Einstein 固定作者页出生日期。",
    ),
    task(
        "books-mystery-category",
        "打开悬疑分类并读取首本书",
        "medium",
        ["catalogue", "category"],
        [
            nav(BOOKS),
            click("a[href='catalogue/category/books/mystery_3/index.html']"),
            text(".page-header h1", "category"),
            text("article.product_pod h3 a", "first_book"),
        ],
        {"category": "Mystery", "first_book": "Sharp Objects"},
        target_url=BOOKS,
        derivation="公开悬疑分类第一页标题与首条目录文本。",
    ),
    task(
        "books-product-detail",
        "打开目录首本书并读取详情",
        "medium",
        ["catalogue", "detail"],
        [
            nav(BOOKS),
            click("article.product_pod h3 a"),
            text(".product_main h1", "title"),
            text(".product_main .price_color", "price"),
        ],
        {"title": "A Light in the Attic", "price": "£51.77"},
        target_url=BOOKS,
        derivation="公开目录第一本书的固定标题与价格。",
    ),
    task(
        "python-appetite-navigation",
        "打开 Python 教程第一章",
        "medium",
        ["documentation", "navigation"],
        [nav(PYTHON), click("li.toctree-l1 a[href='appetite.html']"), text("h1", "answer")],
        {"answer": "1. Whetting Your Appetite¶"},
        target_url=PYTHON,
        derivation="Python 教程索引进入第一章后的唯一 h1。",
    ),
    task(
        "mdn-using-fetch-navigation",
        "打开 MDN Using Fetch 指南",
        "medium",
        ["documentation", "navigation"],
        [nav(MDN), click("main a[href*='/Using_Fetch']", 20_000), text("h1", "answer")],
        {"answer": "Using the Fetch API"},
        target_url=MDN,
        derivation="Fetch API 页进入 Using Fetch 后的唯一 h1。",
    ),
    # Complex: multi-step flows, history restoration and delayed DOM controls.
    task(
        "selenium-delayed-reveal",
        "等待延迟输入框显现后填写",
        "complex",
        ["dynamic", "fill", "actionability", "timing"],
        [
            nav(SELENIUM_DYNAMIC),
            click("#reveal"),
            fill("#revealed", "Lexmount public timing probe"),
            prop("#revealed", "value", "answer"),
        ],
        {"answer": "Lexmount public timing probe"},
        target_url=SELENIUM_DYNAMIC,
        derivation="Selenium 官方动态页延迟显现输入框的 value 属性。",
        timing_probe=True,
    ),
    task(
        "selenium-delayed-box",
        "等待异步插入元素并读取类名",
        "complex",
        ["dynamic", "dom-insertion", "timing"],
        [nav(SELENIUM_DYNAMIC), click("#adder"), attr("#box0", "class", "answer")],
        {"answer": "redbox"},
        target_url=SELENIUM_DYNAMIC,
        derivation="Selenium 官方动态页异步插入方块的固定 class。",
        timing_probe=True,
    ),
    task(
        "selenium-multi-control-submit",
        "填写多控件表单并提交",
        "complex",
        ["form", "fill", "select", "native-control", "submit"],
        [
            nav(SELENIUM),
            fill("input[name='my-text']", "Lexmount"),
            fill("textarea[name='my-textarea']", "Chromium and Moli"),
            select("select[name='my-select']", "3"),
            click("#my-check-2"),
            click("#my-radio-2"),
            click("button"),
            text("#message", "receipt"),
        ],
        {"receipt": "Received!"},
        target_url=SELENIUM,
        derivation="Selenium 官方多控件表单提交后的固定回执。",
    ),
    task(
        "quotes-three-page-history",
        "连续三页名言并恢复历史",
        "complex",
        ["pagination", "history", "multi-page"],
        [
            nav(QUOTES),
            text(".quote:first-of-type .author", "one"),
            nav(f"{QUOTES}page/2/"),
            text(".quote:first-of-type .author", "two"),
            nav(f"{QUOTES}page/3/"),
            text(".quote:first-of-type .author", "three"),
            {"action": "back"},
            text(".quote:first-of-type .author", "back_two"),
            {"action": "back"},
            text(".quote:first-of-type .author", "back_one"),
            {"action": "forward"},
            text(".quote:first-of-type .author", "forward_two"),
        ],
        {
            "one": "Albert Einstein",
            "two": "Marilyn Monroe",
            "three": "Pablo Neruda",
            "back_two": "Marilyn Monroe",
            "back_one": "Albert Einstein",
            "forward_two": "Marilyn Monroe",
        },
        target_url=QUOTES,
        derivation="公开目录前三页首位作者及后退/前进恢复结果。",
    ),
    task(
        "quotes-login-flow",
        "登录公开演示站并确认会话",
        "complex",
        ["form", "login", "session"],
        [
            nav(f"{QUOTES}login"),
            fill("#username", "lexmount-eval"),
            fill("#password", "browser-eval"),
            click("input[type='submit']"),
            text("a[href='/logout']", "answer"),
        ],
        {"answer": "Logout"},
        target_url=f"{QUOTES}login",
        derivation="Quotes to Scrape 接受任意非空凭据，成功后固定显示 Logout。",
    ),
    task(
        "books-mystery-page-two-detail",
        "悬疑分类翻页并打开详情",
        "complex",
        ["catalogue", "pagination", "detail", "history"],
        [
            nav(BOOKS),
            click("a[href='catalogue/category/books/mystery_3/index.html']"),
            click(".next a"),
            text("article.product_pod h3 a", "list_title"),
            click("article.product_pod h3 a"),
            text(".product_main h1", "detail_title"),
            text(".product_main .price_color", "price"),
            {"action": "back"},
        ],
        {
            "list_title": "The Mysterious Affair at ...",
            "detail_title": "The Mysterious Affair at Styles (Hercule Poirot #1)",
            "price": "£24.80",
        },
        target_url=BOOKS,
        derivation="悬疑分类第二页首条目录及其固定详情。",
    ),
    task(
        "python-two-chapter-history",
        "遍历两章 Python 教程并恢复历史",
        "complex",
        ["documentation", "multi-page", "history"],
        [
            nav(PYTHON),
            text("h1", "index"),
            click("li.toctree-l1 a[href='appetite.html']"),
            text("h1", "appetite"),
            nav("https://docs.python.org/3/tutorial/interpreter.html", "interpreter_page"),
            text("h1", "interpreter"),
            {"action": "back"},
            text("h1", "back_appetite"),
            {"action": "back"},
            text("h1", "back_index"),
        ],
        {
            "index": "The Python Tutorial¶",
            "appetite": "1. Whetting Your Appetite¶",
            "interpreter": "2. Using the Python Interpreter¶",
            "back_appetite": "1. Whetting Your Appetite¶",
            "back_index": "The Python Tutorial¶",
        },
        target_url=PYTHON,
        derivation="官方教程两章标题及两级历史恢复。",
    ),
    task(
        "rfc9110-section-history",
        "遍历 RFC 9110 多章节并恢复锚点",
        "complex",
        ["standard", "anchor", "history", "long-document"],
        [
            nav(RFC),
            text("#rfcnum", "number"),
            nav(f"{RFC}#section-1", "intro_page"),
            text("#section-1 h2", "intro"),
            nav(f"{RFC}#section-3", "terms_page"),
            text("#section-3 h2", "terms"),
            {"action": "back"},
            text("#section-1 h2", "back_intro"),
            {"action": "forward"},
            text("#section-3 h2", "forward_terms"),
        ],
        {
            "number": "RFC 9110",
            "intro": "1. Introduction",
            "terms": "3. Terminology and Core Concepts",
            "back_intro": "1. Introduction",
            "forward_terms": "3. Terminology and Core Concepts",
        },
        target_url=RFC,
        derivation="RFC 固定章节标题及锚点历史恢复。",
    ),
    task(
        "mdn-fetch-history",
        "进入 Using Fetch 并验证前进后退",
        "complex",
        ["documentation", "history", "navigation"],
        [
            nav(MDN),
            text("h1", "api"),
            click("main a[href*='/Using_Fetch']", 20_000),
            text("h1", "guide"),
            {"action": "back"},
            text("h1", "back_api"),
            {"action": "forward"},
            text("h1", "forward_guide"),
        ],
        {
            "api": "Fetch API",
            "guide": "Using the Fetch API",
            "back_api": "Fetch API",
            "forward_guide": "Using the Fetch API",
        },
        target_url=MDN,
        derivation="MDN 两篇固定文档标题及历史恢复。",
    ),
    task(
        "github-moli-issues-history",
        "从 Moli README 进入 Issues 并恢复历史",
        "complex",
        ["repository", "history", "multi-page"],
        [
            nav(MOLI),
            text("article.markdown-body h1", "readme"),
            nav(f"{MOLI}/issues", "issues_page"),
            text("main h1", "issues"),
            {"action": "back"},
            text("article.markdown-body h1", "back_readme"),
            {"action": "forward"},
            text("main h1", "forward_issues"),
        ],
        {
            "readme": "Moli",
            "issues": "All issues",
            "back_readme": "Moli",
            "forward_issues": "All issues",
        },
        target_url=MOLI,
        derivation="GitHub 仓库 README 与 Issues 固定一级标题及历史恢复。",
    ),
]


def main() -> None:
    payload = {"schema": "lex_browser_use_tasks/3", "tasks": TASKS}
    (ROOT / "tasks.v4.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
