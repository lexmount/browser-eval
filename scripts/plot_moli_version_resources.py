"""Render the three-configuration compatibility/resource summary as SVG.

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


COLORS = {
    "114_off": "#239b68",
    "118_off": "#d1841e",
    "118_on": "#7652c7",
}


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
    required_row_fields = {"id", "passed_cases", "success_rate_pct"}
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
    unknown_ids = sorted({str(row.get("id")) for row in rows} - COLORS.keys())
    if unknown_ids:
        raise ValueError(f"unknown configuration id(s): {', '.join(unknown_ids)}")
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
            "title": "372 题成功率与资源消耗",
            "subtitle": f"资源实验：557 题 × 5 次；图中使用预先冻结的 {resource['chart_calls']:,} 次 / {resource['chart_tasks']} 题样本；越靠左资源消耗越低",
            "y": "372 题通过率（每题 3 次全部通过）",
            "pss": "峰值进程树 PSS 中位数（MiB）",
            "cpu": "单次执行平均 CPU 时间（ms）",
            "off": "关闭",
            "on": "开启",
        },
        "en": {
            "title": "Success on 372 cases vs. resource use",
            "subtitle": f"Resource experiment: 557 tasks × 5; plotted population: {resource['chart_calls']:,} pre-frozen calls / {resource['chart_tasks']} tasks; farther left is lower",
            "y": "Success rate on 372 cases (all 3 attempts pass)",
            "pss": "Median peak process-tree PSS (MiB)",
            "cpu": "Mean CPU time per execution (ms)",
            "off": "off",
            "on": "on",
        },
    }[language]
    width = 1420 if len(metrics) == 2 else 820
    height = 780
    left, right, top, bottom = 105, 55, 145, 150
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
        f"三种配置在 372 题通过率与{described_metrics}上的对比。"
        if language == "zh"
        else f"Three configurations compared by success rate on 372 cases and {described_metrics}."
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
    label_offsets = {
        "114_off": (14, -44, "start"),
        "118_off": (-14, -14, "end"),
        "118_on": (14, -18, "start"),
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
        for row in rows:
            x = _scale(float(row[key]), low, high, x0, x1)
            y = _scale(float(row["success_rate_pct"]), 0, y_max, chart_bottom, top)
            color = COLORS[row["id"]]
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="10" fill="{color}" stroke="#fff" stroke-width="3"/>')
            dx, dy, anchor = label_offsets[row["id"]]
            short_label = row["short_label"] if language == "zh" else row["short_label_en"]
            change = float(row[change_key])
            label = f'{short_label} · {row[key]:.2f} {unit} ({change:+.2f}%)'
            parts.append(f'<text class="point-label" x="{x+dx:.1f}" y="{y+dy:.1f}" text-anchor="{anchor}" fill="{color}">{html.escape(label)}</text>')
    method_note = (
        "1.1.4：仅复测 1.1.8 关闭布局唯一通过的候选题；3 次均失败，其余 371 题沿用共同失败关系。"
        if language == "zh"
        else "1.1.4: the sole case passed by layout-off 1.1.8 was retested 3 times and failed; the other 371 retain the shared-failure relation."
    )
    parts.append(f'<text class="tick muted" x="55" y="{height-78}">{html.escape(method_note)}</text>')
    legend_y = height - 42
    cursor = 55
    if len(metrics) == 1:
        reserved_label_width = 220
        legend_step = (width - 110 - reserved_label_width) / max(1, len(rows) - 1)
    else:
        legend_step = 420
    for row in rows:
        color = COLORS[row["id"]]
        label_key = "label" if len(metrics) == 2 else "short_label"
        if language == "en":
            label_key += "_en"
        row_label = row[label_key]
        legend_text = f'{row_label}: {row["passed_cases"]}/372 ({row["success_rate_pct"]:.1f}%)'
        parts.append(f'<circle cx="{cursor}" cy="{legend_y}" r="7" fill="{color}"/>')
        parts.append(f'<text class="legend" x="{cursor+13}" y="{legend_y+5}">{html.escape(legend_text)}</text>')
        cursor += legend_step
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
