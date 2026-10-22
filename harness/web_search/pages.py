"""One top-level page registry for navigation, release checks and local captures."""
_STANDARD = (
    ("home", "index.html", "Overview", "概览", True),
    ("dataset", "dataset.html", "Queries", "评测集", True),
    ("results", "results.html", "Results", "结果", True),
    ("interpretation", "interpretation.html", "Findings", "结果解读", True),
    ("performance", "performance.html", "Latency diagnostics", "性能诊断", True),
    ("method", "research.html", "Method & market", "方法与竞品", True),
)
_COMPARISON = (
    ("home", "index.html", "Decisions", "选型结论", True),
    ("capabilities", "capabilities.html", "API outputs", "接口与输出", True),
    ("quality", "quality.html", "Retrieval quality", "检索效果", True),
    ("performance", "performance.html", "Speed and cost", "速度与费用", True),
    ("dataset", "dataset.html", "Tests and sources", "测试集与来源", True),
    ("results", "results.html", "Query comparison", "逐题对照", True),
    ("method", "research.html", "Method & FAQ", "方法与问答", False),
)


def reader_pages(presentation=None, *, reviewed=False):
    rows = list(_COMPARISON if presentation == "comparison-report" else _STANDARD)
    if reviewed:
        rows.append(("review", "review.html", "Result audit", "结果复核", presentation != "comparison-report"))
    return [dict(zip(("key", "route", "en", "zh", "nav"), row, strict=True)) for row in rows]
