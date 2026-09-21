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
import re
from pathlib import Path

COLORS = ("#239b68", "#d1841e", "#7652c7", "#2775c9", "#c54f79", "#4c8793", "#9a653c")
COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")


def combine_comparisons(methods: list[dict], historical: dict, current: dict | None) -> dict:
    """Keep registry order and baseline while separating unlike resource measurements."""
    sources = {row["id"]: (row, "Linux", "PSS") for row in historical["configurations"]}
    sources.update(
        {
            row["id"]: (
                row,
                row.get("platform", "macOS"),
                row.get("memory_kind", "RSS"),
            )
            for row in (current or {}).get("configurations", [])
        }
    )
    baseline = sources[historical["baseline"]][0]
    rows = []
    for index, method in enumerate(methods):
        source, platform, memory_kind = sources[method["id"]]
        row = dict(source)
        row.update(
            label=method["label"],
            label_en=method["label_en"],
            passed_cases=method["final_passed_cases"],
            success_rate_pct=method["final_passed_cases"] / source["total_cases"] * 100,
            platform=platform,
            memory_kind=memory_kind,
            memory_mib=source[f"{memory_kind.lower()}_mib"],
            memory_change_pct=(source["pss_mib"] / baseline["pss_mib"] - 1) * 100
            if platform == "Linux" and memory_kind == "PSS"
            else None,
            cpu_change_pct=(source["cpu_ms"] / baseline["cpu_ms"] - 1) * 100
            if platform == "Linux"
            else None,
            color=COLORS[index % len(COLORS)],
        )
        if memory_kind == "PSS":
            row["pss_change_pct"] = row["memory_change_pct"]
        row["success_change_pp"] = row["success_rate_pct"] - baseline["success_rate_pct"]
        rows.append(row)
    return {
        "baseline": historical["baseline"],
        "configurations": rows,
        "resource_population": historical["resource_population"],
    }


def _scale(value: float, lower: float, upper: float, start: float, end: float) -> float:
    if upper == lower:
        return (start + end) / 2
    return start + (value - lower) / (upper - lower) * (end - start)


def render_chart(
    data: dict, output: Path, metrics: tuple[str, ...] = ("pss", "cpu"), language: str = "zh"
) -> None:
    if (
        not metrics
        or len(metrics) > 2
        or any(metric not in {"pss", "rss", "memory", "cpu"} for metric in metrics)
    ):
        raise ValueError("metrics must contain one or two of pss, rss, memory and cpu")
    if len(set(metrics)) != len(metrics):
        raise ValueError("metrics must not contain duplicates")
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
        "rss": {"rss_mib", "rss_change_pct"},
        "cpu": {"cpu_ms", "cpu_change_pct"},
        "memory": {"memory_mib", "memory_change_pct"},
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
            raise ValueError(
                f"configuration row {index} missing required field(s): {', '.join(missing)}"
            )
        color = row.get("color")
        if color is not None and (not isinstance(color, str) or COLOR_RE.fullmatch(color) is None):
            raise ValueError(f"configuration row {index} color must be a six-digit hex color")
        numeric_fields = {"passed_cases", "total_cases", "success_rate_pct"}
        for metric in metrics:
            numeric_fields.update(metric_fields[metric])
        for field in sorted(numeric_fields):
            if (
                row.get("platform") == "macOS"
                and field.endswith("_change_pct")
                and row[field] is None
            ):
                continue
            try:
                number = float(row[field])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"configuration row {index} field {field} must be numeric"
                ) from exc
            if not math.isfinite(number):
                raise ValueError(f"configuration row {index} field {field} must be finite")
    ids = [str(row["id"]) for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("configuration ids must be unique")
    for field in ("passed_cases", "total_cases"):
        if any(not float(row[field]).is_integer() for row in rows):
            raise ValueError(f"all configurations must use an integer {field}")
    totals = {float(row["total_cases"]) for row in rows}
    if len(totals) != 1 or next(iter(totals)) <= 0:
        raise ValueError("all configurations must use the same positive total_cases")
    total_cases = int(next(iter(totals)))
    resource = data.get("resource_population")
    required_resource_fields = {"chart_calls", "chart_tasks", "total_tasks", "calls_per_task"}
    if not isinstance(resource, dict) or not required_resource_fields.issubset(resource):
        missing = (
            sorted(required_resource_fields - set(resource or {}))
            if isinstance(resource, dict)
            else sorted(required_resource_fields)
        )
        raise ValueError(f"resource_population missing required field(s): {', '.join(missing)}")
    for field in sorted(required_resource_fields):
        value = resource[field]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"resource_population field {field} must be a positive integer")
    text = {
        "zh": {
            "title": f"{total_cases} 题成功率与资源消耗",
            "subtitle": (
                f"资源实验：{resource['total_tasks']} 题 × {resource['calls_per_task']} 次；"
                f"图中使用预先冻结的 {resource['chart_calls']:,} 次 / "
                f"{resource['chart_tasks']} 题样本；越靠左资源消耗越低"
            ),
            "y": f"{total_cases} 题通过率（每题 3 次全部通过）",
            "pss": "峰值进程树 PSS 中位数（MiB）",
            "rss": "峰值进程树 RSS 中位数（MiB）",
            "memory": "峰值内存中位数（MiB；Linux PSS / macOS RSS）",
            "cpu": "单次执行平均 CPU 时间（ms）",
            "off": "关闭",
            "on": "开启",
        },
        "en": {
            "title": f"Success on {total_cases} cases vs. resource use",
            "subtitle": (
                f"Resource experiment: {resource['total_tasks']} tasks × "
                f"{resource['calls_per_task']} calls; plotted population: "
                f"{resource['chart_calls']:,} pre-frozen calls / "
                f"{resource['chart_tasks']} tasks; farther left is lower"
            ),
            "y": f"Success rate on {total_cases} cases (all 3 attempts pass)",
            "pss": "Median peak process-tree PSS (MiB)",
            "rss": "Median peak process-tree RSS (MiB)",
            "memory": "Median peak memory (MiB; Linux PSS / macOS RSS)",
            "cpu": "Mean CPU time per execution (ms)",
            "off": "off",
            "on": "on",
        },
    }[language]
    mixed = len({(row.get("platform"), row.get("memory_kind")) for row in rows}) > 1
    labelled = all("success_change_pp" in row for row in rows)
    if "memory" in metrics and not mixed:
        text["memory"] = text[rows[0].get("memory_kind", "PSS").lower()]
    if "memory" in metrics and mixed:
        text["subtitle"] = (
            text["subtitle"]
            .replace("越靠左资源消耗越低", "不同平台资源分别解释")
            .replace("farther left is lower", "platform-specific resources")
        )
    width = 1420 if len(metrics) == 2 else 820
    separate_builds = {
        row["id"]
        for row in rows
        if row.get("functional_binary_sha256")
        and row.get("resource_binary_sha256")
        and row["functional_binary_sha256"] != row["resource_binary_sha256"]
    }
    note_height = (24 if labelled else 0) + (24 if mixed else 0) + (24 if separate_builds else 0)
    height = 700 + 30 * len(rows) + note_height
    left, right, top, bottom = 105, 55, 145, 115 + 30 * len(rows)
    gap = 90
    panel_width = (width - left - right - gap * (len(metrics) - 1)) / len(metrics)
    chart_bottom = height - bottom - note_height
    max_success_rate = max(float(row["success_rate_pct"]) for row in rows)
    y_max = max(20, math.ceil(max_success_rate / 20) * 20)
    described_metrics = {
        "zh": "、".join(text[metric] for metric in metrics),
        "en": " and ".join(text[metric] for metric in metrics),
    }[language]
    description = (
        f"{len(rows)} 种配置在 {total_cases} 题通过率与{described_metrics}上的对比。"
        if language == "zh"
        else (
            f"{len(rows)} configurations compared by success rate on "
            f"{total_cases} cases and {described_metrics}."
        )
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{html.escape(text["title"])}</title>',
        f'<desc id="desc">{html.escape(description)}</desc>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",'
        '"Noto Sans SC",sans-serif;fill:#17212f}.muted{fill:#657386}'
        ".grid{stroke:#dfe5eb;stroke-width:1}.axis{stroke:#8b98a6;stroke-width:1.2}"
        ".label{font-size:17px}.tick{font-size:14px}"
        ".point-label{font-size:15px;font-weight:650}.title{font-size:29px;font-weight:760}"
        ".subtitle{font-size:16px}.legend{font-size:14px}</style>",
        f'<text class="title" x="55" y="48">{html.escape(text["title"])}</text>',
        f'<text class="subtitle muted" x="55" y="78">{html.escape(text["subtitle"])}</text>',
    ]
    for tick in range(0, y_max + 1, 20):
        y = _scale(tick, 0, y_max, chart_bottom, top)
        parts.append(
            f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}"/>'
        )
        parts.append(
            f'<text class="tick muted" x="{left - 16}" y="{y + 5:.1f}" '
            f'text-anchor="end">{tick}%</text>'
        )
    parts.append(
        f'<text class="label" transform="translate(28 {(top + chart_bottom) / 2:.1f}) '
        f'rotate(-90)" text-anchor="middle">{html.escape(text["y"])}</text>'
    )
    specs = {
        "pss": (text["pss"], "pss_mib", "pss_change_pct", "MiB"),
        "rss": (text["rss"], "rss_mib", "rss_change_pct", "MiB"),
        "cpu": (text["cpu"], "cpu_ms", "cpu_change_pct", "ms"),
        "memory": (text["memory"], "memory_mib", "memory_change_pct", "MiB"),
    }
    row_colors = {
        row["id"]: str(row.get("color") or COLORS[index % len(COLORS)])
        for index, row in enumerate(rows)
    }
    offsets = ((0, 0), (-24, -24), (24, 24), (-24, 24), (24, -24), (0, -32), (0, 32))
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
        parts.append(
            f'<line class="axis" x1="{x0:.1f}" y1="{chart_bottom}" '
            f'x2="{x1:.1f}" y2="{chart_bottom}"/>'
        )
        parts.append(
            f'<text class="label" x="{(x0 + x1) / 2:.1f}" y="{chart_bottom + 52}" '
            f'text-anchor="middle">{html.escape(title)}</text>'
        )
        for i in range(5):
            value = low + (high - low) * i / 4
            x = _scale(value, low, high, x0, x1)
            parts.append(
                f'<line class="grid" x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{chart_bottom}"/>'
            )
            parts.append(
                f'<text class="tick muted" x="{x:.1f}" y="{chart_bottom + 25}" '
                f'text-anchor="middle">{value:.1f}</text>'
            )
        plotted: list[tuple[float, float]] = []
        for row in rows:
            px = _scale(float(row[key]), low, high, x0, x1)
            py = _scale(float(row["success_rate_pct"]), 0, y_max, chart_bottom, top)
            for dx, dy in offsets:
                candidate = (px + dx, py + dy)
                if all(math.hypot(candidate[0] - x, candidate[1] - y) >= 27 for x, y in plotted):
                    break
            plotted.append(candidate)
        markers = []
        annotations = []
        label_boxes = []
        for row_index, row in enumerate(rows, 1):
            exact_x = _scale(float(row[key]), low, high, x0, x1)
            exact_y = _scale(float(row["success_rate_pct"]), 0, y_max, chart_bottom, top)
            x, y = plotted[row_index - 1]
            color = row_colors[row["id"]]
            number = row_index
            if (x, y) != (exact_x, exact_y):
                parts.append(
                    f'<line x1="{exact_x:.1f}" y1="{exact_y:.1f}" x2="{x:.1f}" '
                    f'y2="{y:.1f}" stroke="{color}" stroke-width="1.5"/>'
                )
                parts.append(
                    f'<circle cx="{exact_x:.1f}" cy="{exact_y:.1f}" r="3" fill="{color}"/>'
                )
            shape = (
                f'<path d="M {x:.1f} {y - 15:.1f} L {x + 15:.1f} {y:.1f} '
                f'L {x:.1f} {y + 15:.1f} L {x - 15:.1f} {y:.1f} Z" '
                if mixed and row["platform"] == "macOS"
                else f'<circle cx="{x:.1f}" cy="{y:.1f}" r="12" '
            )
            markers.append(shape + f'fill="{color}" stroke="#fff" stroke-width="3"/>')
            markers.append(
                f'<text x="{x:.1f}" y="{y + 5:.1f}" text-anchor="middle" '
                f'style="font-size:12px;font-weight:750;fill:#fff">{number}</text>'
            )
            if labelled:
                name = (
                    row["label" if language == "zh" else "label_en"]
                    .removeprefix("Moli ")
                    .split(" · ")[0]
                )
                if row["id"].endswith("_on"):
                    name += " ✦"
                if row["id"] in separate_builds:
                    name += " †"
                delta = row[change_key]
                change = "—" if delta is None else f"{float(delta):+.2f}%"
                detail = f"{row['success_change_pp']:+.2f} pp / {change}"
                box_w, box_h = 182, 46
                # Place readable labels near the true data point, avoiding previous labels.
                candidates = []
                for dy in (-58, -112, -166, 22, 76, 130):
                    for dx in (-box_w / 2, 18, -box_w - 18):
                        bx = min(max(exact_x + dx, x0 + 4), x1 - box_w - 4)
                        by = min(max(exact_y + dy, top + 6), chart_bottom - box_h - 6)
                        overlap = sum(
                            max(0, min(bx + box_w + 8, px + pw) - max(bx - 8, px))
                            * max(0, min(by + box_h + 8, py + ph) - max(by - 8, py))
                            for px, py, pw, ph in label_boxes
                            + [(x - 20, y - 20, 40, 40) for x, y in plotted]
                        )
                        distance = math.hypot(bx + box_w / 2 - exact_x, by + box_h / 2 - exact_y)
                        candidates.append((overlap * 1000 + distance, bx, by))
                _, bx, by = min(candidates)
                label_boxes.append((bx, by, box_w, box_h))
                anchor_x = min(max(exact_x, bx), bx + box_w)
                anchor_y = min(max(exact_y, by), by + box_h)
                annotations.append(
                    f'<g data-point-label="{html.escape(str(row["id"]), quote=True)}" '
                    f'data-panel="{metric}">'
                    f'<line x1="{exact_x:.1f}" y1="{exact_y:.1f}" x2="{anchor_x:.1f}" '
                    f'y2="{anchor_y:.1f}" stroke="{color}" stroke-width="1"/>'
                    f'<rect x="{bx:.1f}" y="{by:.1f}" width="{box_w}" height="{box_h}" '
                    'rx="5" fill="#fff" fill-opacity="0.94"/>'
                    f'<text x="{bx + 7:.1f}" y="{by + 18:.1f}" class="point-label">'
                    f"{html.escape(name)}</text>"
                    f'<text x="{bx + 7:.1f}" y="{by + 36:.1f}" style="font-size:12px" '
                    f'class="muted">{html.escape(detail)}</text></g>'
                )
        parts.extend(annotations)
        parts.extend(markers)
    legend_y = chart_bottom + 82
    for index, row in enumerate(rows):
        color = row_colors[row["id"]]
        label_key = "label" if len(metrics) == 2 else "short_label"
        if language == "en":
            label_key += "_en"
        row_label = row[label_key]
        if labelled:
            row_label = row_label.removeprefix("Moli ").split(" · ")[0]
            if row["id"].endswith("_on"):
                row_label += " ✦"
        if row["id"] in separate_builds:
            row_label += " †"
        fields = [
            f"{int(float(row['passed_cases']))}/{total_cases} "
            f"({float(row['success_rate_pct']):.1f}%)"
        ]
        for metric in metrics:
            _, key, change_key, unit = specs[metric]
            change = row[change_key]
            delta = f"{float(change):+.2f}%" if change is not None else "—"
            prefix = (
                f"{row['platform']} {row['memory_kind']} "
                if mixed and metric == "memory"
                else ""
            )
            fields.append(
                f"{prefix}{float(row[key]):.2f} {unit}" + ("" if labelled else f" ({delta})")
            )
        y = legend_y + index * 30
        parts.append(f'<circle cx="55" cy="{y - 5}" r="8" fill="{color}"/>')
        parts.append(
            f'<text x="55" y="{y - 1}" text-anchor="middle" '
            f'style="font-size:10px;font-weight:750;fill:#fff">{index + 1}</text>'
        )
        parts.append(
            f'<text class="legend" x="72" y="{y}">'
            f"{html.escape(row_label + ': ' + ' · '.join(fields))}</text>"
        )
    notes = []
    if labelled:
        notes = (
            [
                "点旁：方法名；通过率变化（pp）/ 资源变化（%）。"
                "✦ 开启 layout；其余关闭。基线：1.1.4 关闭。",
            ]
            if language == "zh"
            else [
                "Labels: method; success change (pp) / resource change (%). "
                "✦ Layout on; otherwise off. Baseline: 1.1.4 off.",
            ]
        )
    if mixed:
        notes.append(
            "圆形：Docker Linux（PSS）；菱形：原生 macOS（RSS）。不同执行环境不计算资源涨幅（—）。"
            if language == "zh"
            else "Circles: Docker Linux (PSS); diamonds: native macOS (RSS). "
            "Resource changes across environments: —."
        )
    if separate_builds:
        notes.append(
            "† 功能与资源使用不同构建；成功率仅适用于功能构建，身份见报告。"
            if language == "zh"
            else "† Success and resources use different builds; "
            "success applies to the functional build only."
        )
    for i, note in enumerate(notes):
        parts.append(
            f'<text class="legend muted" x="55" y="{height - 20 - (len(notes) - 1 - i) * 22}">'
            f"{html.escape(note)}</text>"
        )
    parts.append("</svg>")
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
