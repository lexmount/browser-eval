"""Render a compatibility/resource summary for any number of configurations.

The two resource axes intentionally use separate panels because MiB and CPU
milliseconds are different units.  Both panels share the 372-case success-rate
axis.  CPU can be omitted with ``--metrics pss``.
"""
from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path


COLORS = ("#239b68", "#d1841e", "#7652c7", "#2775c9", "#c54f79", "#4c8793")


def _scale(value: float, lower: float, upper: float, start: float, end: float) -> float:
    if upper == lower:
        return (start + end) / 2
    return start + (value - lower) / (upper - lower) * (end - start)


def render_chart(data: dict, output: Path, metrics: tuple[str, ...] = ("pss", "cpu"), language: str = "zh") -> None:
    if not metrics or any(metric not in {"pss", "cpu"} for metric in metrics):
        raise ValueError("metrics must contain pss and/or cpu")
    if language not in {"zh", "en"}:
        raise ValueError("language must be zh or en")
    rows = data.get("configurations")
    if not isinstance(rows, list) or not rows:
        raise ValueError("configurations must contain at least one row")
    required_row_fields = {"id", "passed_cases", "total_cases", "success_rate_pct"}
    required_row_fields.update(
        {"label", "short_label"} if language == "zh" else {"label_en", "short_label_en"}
    )
    metric_fields = {
        "pss": {"pss_mib", "pss_change_pct"},
        "cpu": {"cpu_ms", "cpu_change_pct"},
    }
    for metric in metrics:
        required_row_fields.update(metric_fields[metric])
    for index, row in enumerate(rows):
        missing = (
            sorted(required_row_fields - set(row))
            if isinstance(row, dict)
            else sorted(required_row_fields)
        )
        if missing:
            raise ValueError(f"configuration row {index} missing required field(s): {', '.join(missing)}")
    totals = {int(row["total_cases"]) for row in rows}
    if len(totals) != 1 or next(iter(totals)) <= 0:
        raise ValueError("all configurations must use the same positive total_cases")
    total_cases = next(iter(totals))
    resource = data.get("resource_population")
    required_resource_fields = {"chart_calls", "chart_tasks"}
    if not isinstance(resource, dict) or not required_resource_fields.issubset(resource):
        missing = (
            sorted(required_resource_fields - set(resource or {}))
            if isinstance(resource, dict)
            else sorted(required_resource_fields)
        )
        raise ValueError(f"resource_population missing required field(s): {', '.join(missing)}")
    text = {
        "zh": {
            "title": f"{total_cases} 题成功率与资源消耗",
            "subtitle": f"资源实验：557 题 × 5 次；图中使用预先冻结的 {resource['chart_calls']:,} 次 / {resource['chart_tasks']} 题样本；越靠左资源消耗越低",
            "y": f"{total_cases} 题通过率（每题 3 次全部通过）",
            "pss": "峰值进程树 PSS 中位数（MiB）",
            "cpu": "单次执行平均 CPU 时间（ms）",
            "off": "关闭",
            "on": "开启",
        },
        "en": {
            "title": f"Success on {total_cases} cases vs. resource use",
            "subtitle": f"Resource experiment: 557 tasks × 5; plotted population: {resource['chart_calls']:,} pre-frozen calls / {resource['chart_tasks']} tasks; farther left is lower",
            "y": f"Success rate on {total_cases} cases (all 3 attempts pass)",
            "pss": "Median peak process-tree PSS (MiB)",
            "cpu": "Mean CPU time per execution (ms)",
            "off": "off",
            "on": "on",
        },
    }[language]
    width = 1420 if len(metrics) == 2 else 820
    height = 700 + 30 * len(rows)
    left, right, top, bottom = 105, 55, 145, 115 + 30 * len(rows)
    gap = 90
    panel_width = (width - left - right - gap * (len(metrics) - 1)) / len(metrics)
    chart_bottom = height - bottom
    max_success_rate = max(float(row["success_rate_pct"]) for row in rows)
    y_max = max(20, math.ceil(max_success_rate / 20) * 20)
    described_metrics = {
        "zh": "、".join(text[metric] for metric in metrics),
        "en": " and ".join(text[metric] for metric in metrics),
    }[language]
    description = (
        f"{len(rows)} 种配置在 {total_cases} 题通过率与{described_metrics}上的对比。"
        if language == "zh"
        else f"{len(rows)} configurations compared by success rate on {total_cases} cases and {described_metrics}."
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{html.escape(text["title"])}</title>',
        f'<desc id="desc">{html.escape(description)}</desc>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC",sans-serif;fill:#17212f}.muted{fill:#657386}.grid{stroke:#dfe5eb;stroke-width:1}.axis{stroke:#8b98a6;stroke-width:1.2}.label{font-size:17px}.tick{font-size:14px}.point-label{font-size:15px;font-weight:650}.title{font-size:29px;font-weight:760}.subtitle{font-size:16px}.legend{font-size:14px}</style>',
        f'<text class="title" x="55" y="48">{html.escape(text["title"])}</text>',
        f'<text class="subtitle muted" x="55" y="78">{html.escape(text["subtitle"])}</text>',
    ]
    for tick in range(0, y_max + 1, 20):
        y = _scale(tick, 0, y_max, chart_bottom, top)
        parts.append(f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}"/>')
        parts.append(f'<text class="tick muted" x="{left-16}" y="{y+5:.1f}" text-anchor="end">{tick}%</text>')
    parts.append(f'<text class="label" transform="translate(28 {(top+chart_bottom)/2:.1f}) rotate(-90)" text-anchor="middle">{html.escape(text["y"])}</text>')
    specs = {
        "pss": (text["pss"], "pss_mib", "pss_change_pct", "MiB"),
        "cpu": (text["cpu"], "cpu_ms", "cpu_change_pct", "ms"),
    }
    row_colors = {
        row["id"]: str(row.get("color") or COLORS[index % len(COLORS)])
        for index, row in enumerate(rows)
    }
    for panel, metric in enumerate(metrics):
        title, key, change_key, unit = specs[metric]
        x0 = left + panel * (panel_width + gap)
        x1 = x0 + panel_width
        values = [float(row[key]) for row in rows]
        spread = max(values) - min(values)
        center = sum(values) / len(values)
        padding = max(spread * 0.22, abs(center) * 0.03, 1.0)
        low = max(0.0, min(values) - padding)
        high = max(values) + padding
        parts.append(f'<line class="axis" x1="{x0:.1f}" y1="{chart_bottom}" x2="{x1:.1f}" y2="{chart_bottom}"/>')
        parts.append(f'<text class="label" x="{(x0+x1)/2:.1f}" y="{chart_bottom+52}" text-anchor="middle">{html.escape(title)}</text>')
        for i in range(5):
            value = low + (high - low) * i / 4
            x = _scale(value, low, high, x0, x1)
            parts.append(f'<line class="grid" x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{chart_bottom}"/>')
            parts.append(f'<text class="tick muted" x="{x:.1f}" y="{chart_bottom+25}" text-anchor="middle">{value:.1f}</text>')
        plotted: list[tuple[float, float]] = []
        offsets = ((0, 0), (-18, -18), (18, 18), (-18, 18), (18, -18), (0, -24), (0, 24))
        for row in rows:
            exact_x = _scale(float(row[key]), low, high, x0, x1)
            exact_y = _scale(float(row["success_rate_pct"]), 0, y_max, chart_bottom, top)
            x, y = exact_x, exact_y
            for dx, dy in offsets:
                candidate = (exact_x + dx, exact_y + dy)
                if all(math.hypot(candidate[0] - px, candidate[1] - py) >= 27 for px, py in plotted):
                    x, y = candidate
                    break
            plotted.append((x, y))
            color = row_colors[row["id"]]
            number = rows.index(row) + 1
            if (x, y) != (exact_x, exact_y):
                parts.append(f'<line x1="{exact_x:.1f}" y1="{exact_y:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="{color}" stroke-width="1.5"/>')
                parts.append(f'<circle cx="{exact_x:.1f}" cy="{exact_y:.1f}" r="3" fill="{color}"/>')
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="12" fill="{color}" stroke="#fff" stroke-width="3"/>')
            parts.append(f'<text x="{x:.1f}" y="{y+5:.1f}" text-anchor="middle" style="font-size:12px;font-weight:750;fill:#fff">{number}</text>')
    legend_y = chart_bottom + 82
    for index, row in enumerate(rows):
        color = row_colors[row["id"]]
        label_key = "label" if len(metrics) == 2 else "short_label"
        if language == "en":
            label_key += "_en"
        row_label = row[label_key]
        fields = [f'{row["passed_cases"]}/{total_cases} ({row["success_rate_pct"]:.1f}%)']
        for metric in metrics:
            _, key, change_key, unit = specs[metric]
            fields.append(f'{row[key]:.2f} {unit} ({float(row[change_key]):+.2f}%)')
        y = legend_y + index * 30
        parts.append(f'<circle cx="55" cy="{y-5}" r="8" fill="{color}"/>')
        parts.append(f'<text x="55" y="{y-1}" text-anchor="middle" style="font-size:10px;font-weight:750;fill:#fff">{index+1}</text>')
        parts.append(f'<text class="legend" x="72" y="{y}">{html.escape(row_label + ": " + " · ".join(fields))}</text>')
    parts.append('</svg>')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(parts) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--metrics", default="pss,cpu")
    parser.add_argument("--language", choices=("zh", "en"), default="zh")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text())
    metrics = tuple(item.strip() for item in args.metrics.split(",") if item.strip())
    render_chart(payload, args.output, metrics, args.language)


if __name__ == "__main__":
    main()
