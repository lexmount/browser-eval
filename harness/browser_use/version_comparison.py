"""Compare two preserved runs on identical tasks; never modify either run."""
# ruff: noqa: E501 -- inline HTML, matching report.py's template convention

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from .metrics import _metric, _passed
from .report import METRICS, _absolute_metric_cell, _detail_filename, _ratio_metric_cell


def compare_runs(old_dir: Path, new_dir: Path) -> dict:
    runs = []
    for directory in (old_dir, new_dir):
        manifest = json.loads((directory / "manifest.json").read_text())
        rows = [
            json.loads(line) for line in (directory / "attempts.jsonl").read_text().splitlines()
        ]
        runs.append((manifest, rows))
    old_manifest, new_manifest = (run[0] for run in runs)
    for key in ("suite_sha256", "suite", "protocol"):
        if old_manifest[key] != new_manifest[key]:
            raise ValueError(f"Incomparable runs: {key} differs")
    for key in ("docker_architecture", "docker_cpus", "docker_memory_bytes"):
        if old_manifest["environment"][key] != new_manifest["environment"][key]:
            raise ValueError(f"Incomparable runs: {key} differs")
    if (
        old_manifest["environment"]["images"]["chromium"]["id"]
        != new_manifest["environment"]["images"]["chromium"]["id"]
    ):
        raise ValueError("Incomparable runs: Chromium image differs")
    ids = [task["task_id"] for task in old_manifest["suite"]]
    first, final = [], []
    for _, rows in runs:
        initial, last = {}, {}
        for row in sorted(rows, key=lambda r: r["attempt"]):
            key = (row["task_id"], row["engine"])
            initial.setdefault(key, row)
            last[key] = row
        if set(initial) != {(task, engine) for task in ids for engine in ("chromium", "moli")}:
            raise ValueError("Incomplete task/provider coverage")
        first.append(initial)
        final.append(last)
    common = [
        task
        for task in ids
        if all(
            _passed(first[i][task, engine], "result")
            for i in (0, 1)
            for engine in ("chromium", "moli")
        )
    ]
    tasks = []
    for task in old_manifest["suite"]:
        tid = task["task_id"]
        statuses = [
            [_passed(final[i][tid, engine], "result") for engine in ("chromium", "moli")]
            for i in (0, 1)
        ]
        before, after = statuses[0][1], statuses[1][1]
        tasks.append(
            {
                "task_id": tid,
                "title": task["title"],
                "status": statuses,
                "change": "恢复"
                if not before and after
                else "退化"
                if before and not after
                else "均通过"
                if after
                else "仍失败",
            }
        )
    metrics = {}
    for _, group in METRICS:
        for key, _, _, _ in group:
            values = [
                [
                    first[i][tid, engine].get(key)
                    if key == "duration_ms"
                    else first[i][tid, engine]["resource"].get(key)
                    for tid in common
                ]
                for i in (0, 1)
                for engine in ("chromium", "moli")
            ]
            if any(
                not isinstance(v, (int, float)) for group_values in values for v in group_values
            ):
                raise ValueError(f"Missing metric: {key}")
            metrics[key] = {
                "old_chromium": _metric(values[0]),
                "old_moli": _metric(values[1]),
                "new_chromium": _metric(values[2]),
                "new_moli": _metric(values[3]),
                "new_over_old_moli": _metric(
                    [b / a for a, b in zip(values[1], values[3], strict=True) if a > 0]
                ),
                "values": [
                    {
                        "task_id": tid,
                        "old_chromium": values[0][i],
                        "old_moli": values[1][i],
                        "new_chromium": values[2][i],
                        "new_moli": values[3][i],
                        "new_over_old_moli": values[3][i] / values[1][i]
                        if values[1][i] > 0
                        else None,
                    }
                    for i, tid in enumerate(common)
                ],
            }
    return {
        "runs": [m for m, _ in runs],
        "task_count": len(ids),
        "tasks": tasks,
        "common_task_ids": common,
        "metrics": metrics,
        "correctness": [
            [
                sum(_passed(final[i][tid, engine], "result") for tid in ids)
                for engine in ("chromium", "moli")
            ]
            for i in (0, 1)
        ],
    }


def render(data: dict) -> str:
    esc = html.escape
    old, new = [m["environment"]["browser_versions"]["moli"] for m in data["runs"]]
    names = [old, new]
    paths = ["moli-1.0.6-rerun", "moli-1.1.1"]
    links = "".join(
        f'<a href="{path}/browser-use-evaluation-latest.html">{esc(name)} 完整评测 →</a>'
        for name, path in zip(names, paths, strict=True)
    )
    counts = data["correctness"]
    correctness = ""
    for col, provider in enumerate(("Chromium 对照", "Moli")):
        a, b = counts[0][col], counts[1][col]
        correctness += (
            f"<tr><td>{provider}</td>"
            + "".join(
                f'<td class="{"better" if v > other else ""}">{v}/{data["task_count"]}</td>'
                for v, other in ((a, b), (b, a))
            )
            + "</tr>"
        )
    sections = []
    for group_name, group in METRICS:
        rows = []
        for key, label, divisor, unit in group:
            metric = data["metrics"][key]
            for stat, stat_label in (("p50", "P50"), ("p95", "P95"), ("mean", "平均值")):
                a, b = metric["old_moli"][stat], metric["new_moli"][stat]
                rows.append(
                    f"<tr><td>{esc(label)} · {stat_label} ({unit})</td>"
                    + _absolute_metric_cell(a, b, divisor)
                    + _absolute_metric_cell(b, a, divisor)
                    + _ratio_metric_cell(metric["new_over_old_moli"][stat])
                    + "</tr>"
                )
        sections.append(
            f'<details><summary>{esc(group_name)}：展开全部指标</summary><div class="scroll"><table>'
            f"<thead><tr><th>指标</th><th>{esc(old)}</th><th>{esc(new)}</th><th>逐任务新版 / 旧版</th></tr></thead>"
            + "<tbody>"
            + "".join(rows)
            + "</tbody></table></div></details>"
        )
    task_rows = []
    passed_rows = []
    for task in sorted(data["tasks"], key=lambda t: t["change"] == "均通过"):
        cells = []
        for i in (0, 1):
            passed = task["status"][i][1]
            href = paths[i] + "/" + _detail_filename(task["task_id"], "moli")
            cells.append(
                f'<td><a class="{"better" if passed else "worse"}" href="{href}">{"通过" if passed else "失败"} →</a></td>'
            )
        (passed_rows if task["change"] == "均通过" else task_rows).append(
            f"<tr><td>{esc(task['title'])}<small>{esc(task['task_id'])}</small></td>"
            + "".join(cells)
            + f"<td>{task['change']}</td></tr>"
        )
    n = len(data["common_task_ids"])
    restored = sum(t["change"] == "恢复" for t in data["tasks"])
    regressed = sum(t["change"] == "退化" for t in data["tasks"])
    quick_rows = []
    for key, label, stat, divisor in (
        ("duration_ms", "耗时 P50 (ms)", "p50", 1),
        ("duration_ms", "耗时 P95 (ms)", "p95", 1),
        ("memory_mean_bytes", "时间加权平均内存 · 均值 (MiB)", "mean", 1048576),
        ("cpu_ms", "CPU 时间 · 均值 (ms)", "mean", 1),
    ):
        metric = data["metrics"][key]
        a, b = metric["old_moli"][stat], metric["new_moli"][stat]
        quick_rows.append(
            f"<tr><td>{label}</td>"
            + _absolute_metric_cell(a, b, divisor)
            + _absolute_metric_cell(b, a, divisor)
            + "</tr>"
        )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Moli 版本升级对比</title><style>
body{{margin:0;background:#f2f5fa;color:#18243b;font:15px/1.65 system-ui,sans-serif}}main{{max-width:1120px;margin:auto;padding:32px 20px}}h1{{margin:6px 0 12px}}h2{{font-size:20px}}section{{background:white;border:1px solid #dce3ef;border-radius:14px;padding:24px;margin:20px 0}}nav{{display:flex;gap:20px;flex-wrap:wrap}}a{{color:#245be0}}small{{display:block;color:#63718a;font:12px/1.8 monospace}}table{{width:100%;border-collapse:collapse;text-align:left}}th,td{{padding:12px;border-bottom:1px solid #e0e6ef}}th{{background:#f4f7fc}}.scroll{{overflow:auto}}.better{{color:#007a55;font-weight:700}}.worse{{color:#bf2525;font-weight:700}}details{{border:1px solid #dce3ef;border-radius:10px;margin:12px 0;padding:14px}}summary{{cursor:pointer;font-weight:600}}.muted{{color:#63718a}}.hero{{font-size:20px;font-weight:650}}
</style></head><body><main><small>BROWSER HARNESS EVAL · VERSION COMPARISON</small><h1>Moli 1.0.6 → 1.1.1</h1>
<nav>{links}<a href="browser-use-evaluation-latest.html">原始 1.0.6 报告 →</a></nav>
<section><h2>任务正确率</h2><p class="hero">{restored} 个任务恢复 · {regressed} 个任务退化</p>
<div class="scroll"><table><thead><tr><th>Provider</th><th>1.0.6 复跑轮</th><th>1.1.1 评测轮</th></tr></thead><tbody>{correctness}</tbody></table></div>
<p class="muted">相同的 {data["task_count"]} 个任务、系统依赖、Chromium 镜像和资源限制。两轮先后运行，公网内容与网络仍可能变化；恢复或退化表示本次观测变化，不单独证明版本因果。</p></section>
<section><h2>速度与资源</h2><p>固定比较两轮 Chromium 和 Moli 都直接成功的 {n} 个任务；失败和重试不进入性能统计。绝对值越低越好；逐任务新版 / 旧版低于 1× 表示新版更低。</p>
<p><a href="moli-1.1.1/browser-use-evaluation-latest.html#metric-guide">查看各项指标说明 →</a></p>
<div class="scroll"><table><thead><tr><th>主要指标</th><th>1.0.6</th><th>1.1.1</th></tr></thead><tbody>{"".join(quick_rows)}</tbody></table></div>
{"".join(sections)}<details><summary>Chromium 对照与统计口径</summary><p>P50 看典型任务，P95 看较慢或占用较高的一端。比值先逐任务相除，再求 P50、P95 和平均值，并非左右聚合值相除。</p>
<p>Chromium 两轮耗时均值：{data["metrics"]["duration_ms"]["old_chromium"]["mean"]} ms → {data["metrics"]["duration_ms"]["new_chromium"]["mean"]} ms。完整对照指标及共同任务 ID 见 <a href="version-comparison.json">计算数据</a>。</p></details></section>
<section><h2>任务变化与详情</h2><div class="scroll"><table><thead><tr><th>任务</th><th>1.0.6</th><th>1.1.1</th><th>变化</th></tr></thead><tbody>{"".join(task_rows)}</tbody></table></div></section>
<details><summary>两版均通过的 {len(passed_rows)} 个任务</summary><div class="scroll"><table><thead><tr><th>任务</th><th>1.0.6</th><th>1.1.1</th><th>变化</th></tr></thead><tbody>{"".join(passed_rows)}</tbody></table></div></details>
<p class="muted">原始 1.0.6 报告和运行数据保持不变。<a href="version-comparison.json">下载本次对比数据</a></p></main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = compare_runs(args.old, args.new)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "version-comparison.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2)
    )
    (args.output / "index.html").write_text(render(data))
    print(json.dumps({"correctness": data["correctness"], "common": len(data["common_task_ids"])}))


if __name__ == "__main__":
    main()
