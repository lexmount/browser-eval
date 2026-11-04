"""Report helpers shared by both Trajectory Replay tracks."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ASSETS = Path(__file__).parents[1] / "assets"
SUITE_FILTER_JS = (ASSETS / "suite-filter.js").read_text(encoding="utf-8")
EVIDENCE_DIALOG_JS = (ASSETS / "evidence-dialog.js").read_text(encoding="utf-8")


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _trace_text(value: Any) -> str:
    """Remove machine-identifying IPs and diff-breaking line-end whitespace."""
    text = str(value if value is not None else "")
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[IP 已脱敏]", text)
    return "\n".join(line.rstrip() for line in text.splitlines())


def _show(value: Any, divisor: float = 1.0, suffix: str = "") -> str:
    return f"{value / divisor:,.2f}{suffix}" if isinstance(value, (int, float)) else "无数据"


def _stat(metric: dict[str, Any], key: str, divisor: float = 1.0) -> str:
    value = metric.get(key, metric.get("median") if key == "p50" else None)
    return _show(value, divisor)


@dataclass(frozen=True)
class ComparedValue:
    text: str
    css_class: str


def _comparison_class(value: Any, other: Any, *, higher: bool = False) -> str:
    if not isinstance(value, (int, float)) or not isinstance(other, (int, float)):
        return ""
    if value == other:
        return ""
    return "better" if (value > other if higher else value < other) else "worse"


def _numeric_stat(metric: dict[str, Any], key: str) -> float | int | None:
    value = metric.get(key, metric.get("median") if key == "p50" else None)
    return value if isinstance(value, (int, float)) else None


def _display_number(value: float | int | None, divisor: float = 1.0) -> float | None:
    """Return the exact two-decimal number shown in an absolute metric cell."""
    if not isinstance(value, (int, float)):
        return None
    return float(f"{value / divisor:.2f}")


def _difficulty(value: str) -> str:
    named = {"simple": "简单", "medium": "中等", "complex": "复杂"}
    if value in named:
        return named[value]
    match = re.fullmatch(r"step_(\d+)_(\d+)", value)
    if match:
        return f"{int(match.group(1))}–{int(match.group(2))} 步"
    return value or "未标注"


def _action_text(call: dict[str, Any]) -> str:
    action = str(call.get("action") or "")
    return {
        "navigate": f"打开 {call.get('url', '')} 并返回页面观察快照",
        "get_text": f"读取 {call.get('selector', '')} 文本",
        "get_attribute": f"读取 {call.get('selector', '')} 的 {call.get('name', '')} 属性",
        "get_property": f"读取 {call.get('selector', '')} 的 {call.get('name', '')} DOM 属性",
        "click": f"点击 {call.get('selector', '')}",
        "fill": f"向 {call.get('selector', '')} 填入 {call.get('text', '')}",
        "select": f"在 {call.get('selector', '')} 选择 {call.get('value', '')}",
        "snapshot": f"读取 {call.get('selector') or 'body'} 当前快照",
        "scroll": f"向 {call.get('direction', 'down')} 滚动 {call.get('amount', 700)} px",
        "back": "浏览器后退",
        "forward": "浏览器前进",
        "press": f"按键 {call.get('key', '')}",
    }.get(action, action)


def _source_references(source: dict[str, Any]) -> str:
    links = []
    for url in source.get("design_basis") or []:
        parsed = urlparse(str(url))
        if parsed.scheme in {"https", "http"}:
            label = parsed.path.rstrip("/").rsplit("/", 1)[-1] or parsed.hostname
            links.append(f'<a href="{_e(url)}" target="_blank" rel="noopener">{_e(label)}</a>')
    return '<p class="muted">设计依据：' + " · ".join(links) + "</p>" if links else ""


def _annotate_source_marks(escaped_text: str) -> str:
    """Annotate the literal heading permalink character without changing the oracle."""
    return escaped_text.replace(
        "¶", '<span class="source-mark" title="原网页标题锚点字符 U+00B6">¶</span>'
    )


def _source_mark_note(values: Any) -> str:
    return (
        '<p class="source-mark-note">¶ 为原网页标题的锚点字符，参与逐字判定。</p>'
        if "¶" in str(values)
        else ""
    )
