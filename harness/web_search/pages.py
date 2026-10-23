"""One top-level page registry for navigation, release checks and local captures."""

from .messages import report_text
_STANDARD = (
    ("home", "index.html", "Overview", report_text('text_fea405f9b01d1416'), True),
    ("dataset", "dataset.html", "Queries", report_text('text_a18713d1bce780df'), True),
    ("results", "results.html", "Results", report_text('text_bb7ef73495dd8a74'), True),
    ("interpretation", "interpretation.html", "Findings", report_text('text_8979f9672f58bc41'), True),
    ("performance", "performance.html", "Latency diagnostics", report_text('text_51464dd5dcb1bce3'), True),
    ("method", "research.html", "Method & market", report_text('text_1e130b2503848791'), True),
)
_COMPARISON = (
    ("home", "index.html", "Decisions", report_text('text_842776f8a14cc259'), True),
    ("capabilities", "capabilities.html", "API outputs", report_text('text_aabf873ca20d6909'), True),
    ("quality", "quality.html", "Retrieval quality", report_text('text_243ff1bffea7c23d'), True),
    ("performance", "performance.html", "Speed and cost", report_text('text_050b4ad5cfeb66ad'), True),
    ("dataset", "dataset.html", "Tests and sources", report_text('text_1825163793da2dc1'), True),
    ("results", "results.html", "Query comparison", report_text('text_57f428a0741e0981'), True),
    ("method", "research.html", "Method & FAQ", report_text('text_8adb3edf2fc73aea'), False),
)


def reader_pages(presentation=None, *, reviewed=False):
    rows = list(_COMPARISON if presentation == "comparison-report" else _STANDARD)
    if reviewed:
        rows.append(("review", "review.html", "Result audit", report_text('text_521deef44b4ce505'), presentation != "comparison-report"))
    return [dict(zip(("key", "route", "en", "zh", "nav"), row, strict=True)) for row in rows]
