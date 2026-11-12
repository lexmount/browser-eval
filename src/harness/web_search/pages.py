"""One top-level page registry for navigation, release checks and local captures."""

from harness.web_search.messages import zh_text

_STANDARD = (
    ("home", "index.html", "Overview", True),
    ("dataset", "dataset.html", "Queries", True),
    ("results", "results.html", "Results", True),
    ("interpretation", "interpretation.html", "Findings", True),
    ("performance", "performance.html", "Latency diagnostics", True),
    ("method", "research.html", "Method & market", True),
)
_COMPARISON = (
    ("home", "index.html", "Evaluation overview", True),
    ("capabilities", "capabilities.html", "API outputs", True),
    ("quality", "quality.html", "Retrieval quality", True),
    ("performance", "performance.html", "Speed and cost", True),
    ("dataset", "dataset.html", "Tests and sources", True),
    ("results", "results.html", "Query comparison", True),
    ("method", "research.html", "Method & FAQ", False),
)


def reader_pages(presentation=None, *, reviewed=False):
    rows = list(_COMPARISON if presentation == "comparison-report" else _STANDARD)
    if reviewed:
        rows.append(("review", "review.html", "Result audit", presentation != "comparison-report"))
    return [{"key": key, "route": route, "en": en, "zh": zh_text(en, context="navigation"), "nav": nav} for key, route, en, nav in rows]
