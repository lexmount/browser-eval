"""One report across preserved Moli versions, with run-bound execution evidence."""

# ruff: noqa: E501
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from .diagnostics import diagnose_summary
from .metrics import _passed, summarize_attempts
from .report import (
    METRICS,
    PAGES,
    REPORT_STEM,
    _absolute_metric_cell,
    _detail_filename,
    _duration_ratio_audit,
    _e,
    _execution_details,
    _execution_explorer,
    _method_page,
    _metric_guide,
    _ratio_metric_cell,
    _shell,
    _show,
    _source_code_entry,
    _suite_page,
    _table,
)
from .version_comparison import compare_runs

ENGINES = ("chromium", "moli-1.0.6", "moli-1.1.1")
LABELS = ("Chromium", "Moli 1.0.6", "Moli 1.1.1")


def _load(path: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads((path / "manifest.json").read_text())
    rows = [json.loads(line) for line in (path / "attempts.jsonl").read_text().splitlines()]
    ids = [task["task_id"] for task in manifest["suite"]]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("Task IDs must be nonempty and unique")
    if any(row.get("run_id") != manifest["run_id"] for row in rows):
        raise ValueError("Execution run ID does not match manifest")
    coverage = {(row["task_id"], row["engine"]) for row in rows}
    if coverage != {(tid, engine) for tid in ids for engine in ("chromium", "moli")}:
        raise ValueError("Incomplete task/provider coverage")
    return manifest, rows


def _time(manifest: dict) -> str:
    date = datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
    return date.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")


def _final(rows: list[dict], engine: str) -> dict[str, dict]:
    return {
        row["task_id"]: row
        for row in sorted(rows, key=lambda r: r["attempt"])
        if row["engine"] == engine
    }


def _ledger(runs: list[tuple[dict, list[dict]]]) -> str:
    rows = []
    for label, (manifest, attempts) in zip(
        ("历史基线", "1.0.6 复跑", "1.1.1 评测"), runs, strict=True
    ):
        score = sum(_passed(r, "result") for r in _final(attempts, "chromium").values())
        rows.append(
            [label, _time(manifest), manifest["run_id"], f"{score}/{len(manifest['suite'])}"]
        )
    return _table(["轮次", "北京时间", "运行编号", "Chromium 正确任务"], rows, "运行来源")


def _historical_evidence(runs: list[tuple[dict, list[dict]]], details: dict[str, str]) -> str:
    historical = _final(runs[0][1], "chromium")
    changed = sorted(
        {
            tid
            for _, rows in runs[1:]
            for tid, row in _final(rows, "chromium").items()
            if _passed(row, "result") != _passed(historical[tid], "result")
        }
    )
    if not changed:
        return ""
    blocks = []
    for tid in changed:
        cells = []
        for index, (manifest, rows) in enumerate(runs):
            # Separate evidence keys avoid overwriting the current Chromium detail.
            engine = ("chromium-history", "chromium-control-1.0.6", "chromium")[index]
            selected = [
                {**row, "engine": engine}
                for row in rows
                if row["engine"] == "chromium" and row["task_id"] == tid
            ]
            summary = summarize_attempts(selected, engines=(engine,))
            diagnose_summary(summary)
            _execution_details(manifest, summary, details)
            last = max(selected, key=lambda row: row["attempt"])
            passed = _passed(last, "result")
            evidence = json.dumps(last.get("trace", []), ensure_ascii=False)
            state = (
                "答案匹配"
                if passed
                else "返回 Client Challenge；未取得目标标题"
                if "Client Challenge" in evidence
                else "任务未通过，查看执行证据"
            )
            cells.append(f'<td><a href="{_detail_filename(tid, engine)}">{_e(state)} →</a></td>')
        title = next(task["title"] for task in runs[0][0]["suite"] if task["task_id"] == tid)
        blocks.append(
            f'<tr><td>{_e(title)}<small class="record-id" style="display:block">任务 ID <code>{_e(tid)}</code></small></td>{"".join(cells)}</tr>'
        )
    before = sum(_passed(row, "result") for row in historical.values())
    after = sum(_passed(row, "result") for row in _final(runs[2][1], "chromium").values())
    count = len(historical)
    return (
        f'<details id="chromium-history"><summary>Chromium 历史 {before}/{count}，本次 {after}/{count} · 查看差异</summary>'
        "<p>任务集和 Chromium 镜像相同，但运行时间不同。以下保留各轮真实结果和差异任务的页面证据；历史成绩不替代本次成绩，也不据此认定浏览器版本退化。</p>"
        + _ledger(runs)
        + '<div class="table-wrap"><table><thead><tr><th>差异任务</th><th>历史基线</th><th>1.0.6 复跑中的 Chromium</th><th>1.1.1 评测中的 Chromium</th></tr></thead><tbody>'
        + "".join(blocks)
        + "</tbody></table></div></details>"
    )


def _performance(data: dict) -> str:
    sections = []
    names = ("new_chromium", "old_moli", "new_moli")
    for index, (group, metrics) in enumerate(METRICS):
        header = (
            '<tr><th rowspan="2">指标</th>'
            + "".join(f'<th colspan="3">{stat}</th>' for stat in ("P50", "P95", "平均值"))
            + "</tr>"
        )
        header += (
            "<tr>" + "".join(f"<th>{name}</th>" for _ in range(3) for name in LABELS) + "</tr>"
        )
        rows, ratios = [], []
        for key, label, divisor, unit in metrics:
            cells = []
            for stat in ("p50", "p95", "mean"):
                values = [data["metrics"][key][name][stat] for name in names]
                for i, value in enumerate(values):
                    cells.append(
                        _absolute_metric_cell(
                            value, min(v for j, v in enumerate(values) if j != i), divisor
                        )
                    )
            rows.append(f"<tr><td>{label} ({unit})</td>{''.join(cells)}</tr>")
            ratios.append(
                f"<tr><td>{label}</td>"
                + "".join(
                    _ratio_metric_cell(data["metrics"][key]["new_over_old_moli"][stat])
                    for stat in ("p50", "p95", "mean")
                )
                + "</tr>"
            )
        sections.append(
            f'<details class="resource-group"{" open" if index == 0 else ""}><summary>{group}<small>{len(metrics)} 项指标</small></summary><div class="resource-content"><div class="table-wrap"><table><caption>{group} · 同一批任务绝对值</caption><thead>{header}</thead><tbody>{"".join(rows)}</tbody></table></div>'
            '<details><summary>版本变化 · 逐任务 1.1.1 / 1.0.6 比值</summary><p>每个任务先相除，再汇总；0.5× 表示新版本是旧版本的一半，2× 表示两倍。</p><div class="table-wrap"><table><thead><tr><th>指标</th><th>P50</th><th>P95</th><th>平均值</th></tr></thead><tbody>'
            + "".join(ratios)
            + "</tbody></table></div></details></div></details>"
        )
    return "".join(sections)


def build_report(historical_dir: Path, old_dir: Path, new_dir: Path, output: Path) -> Path:
    """Read-only inputs; output must be a distinct report directory."""
    if any(
        output.resolve() == path.resolve()
        or output.resolve() in path.resolve().parents
        or path.resolve() in output.resolve().parents
        for path in (historical_dir, old_dir, new_dir)
    ):
        raise ValueError("Output must not overwrite an input run")
    data = compare_runs(old_dir, new_dir)
    if not data["common_task_ids"]:
        raise ValueError("No common successful tasks for resource comparison")
    runs = [_load(path) for path in (historical_dir, old_dir, new_dir)]
    if (
        runs[0][0]["suite_sha256"] != runs[2][0]["suite_sha256"]
        or runs[0][0]["suite"] != runs[2][0]["suite"]
    ):
        raise ValueError("Historical task set differs")
    if (
        runs[0][0]["environment"]["images"]["chromium"]["id"]
        != runs[2][0]["environment"]["images"]["chromium"]["id"]
    ):
        raise ValueError("Historical Chromium image differs")
    for index, version in ((1, "1.0.6"), (2, "1.1.1")):
        if version not in runs[index][0]["environment"]["browser_versions"]["moli"]:
            raise ValueError(f"Expected Moli {version}")
    merged = []
    source_runs = {}
    for engine, run_index, source_engine in zip(
        ENGINES, (2, 1, 2), ("chromium", "moli", "moli"), strict=True
    ):
        source_runs[engine] = runs[run_index][0]
        merged.extend(
            {**row, "engine": engine, "source_engine": source_engine}
            for row in runs[run_index][1]
            if row["engine"] == source_engine
        )
    summary = summarize_attempts(merged, engines=ENGINES)
    diagnose_summary(summary)
    manifest = copy.deepcopy(runs[2][0])
    manifest["provider_runs"] = source_runs
    manifest["report_label"] = (
        "版本对照 · Chromium / Moli 1.0.6 / Moli 1.1.1 · " + runs[2][0]["created_at"][:10]
    )
    manifest["environment"]["browser_versions"]["moli"] = "1.0.6 与 1.1.1（各自独立轮次）"
    details: dict[str, str] = {}
    analysis = _execution_explorer(manifest, summary, details)
    history = _historical_evidence(runs, details)
    counts = [summary["functional"][engine]["result_final_passes"] for engine in ENGINES]
    correctness = []
    for engine, label, count in zip(ENGINES, LABELS, counts, strict=True):
        values = summary["functional"][engine]
        n = values["tasks"]
        combined = values["combined_final_passes"]
        winner = ' class="better"' if count == max(counts) and min(counts) < count else ""
        combined_winner = (
            ' class="better"'
            if combined
            > max(summary["functional"][e]["combined_final_passes"] for e in ENGINES if e != engine)
            else ""
        )
        correctness.append(
            f'<tr><td>{label}</td><td{winner}>{count}/{n}（{count / n:.2%}）</td><td{combined_winner}>{combined}/{n}（{combined / n:.2%}）</td><td><a class="provider-failure-link" href="{REPORT_STEM}-analysis.html#provider={quote(engine)}&amp;status=fail">{n - count} 个错误 · 查看 →</a></td></tr>'
        )
    restored = sum(task["change"] == "恢复" for task in data["tasks"])
    regressed = sum(task["change"] == "退化" for task in data["tasks"])
    changes = "".join(
        f"<p>{_e(task['title'])} · <code>{_e(task['task_id'])}</code> · {task['change']}　"
        f'<a href="{_detail_filename(task["task_id"], ENGINES[1])}">1.0.6 证据</a> / <a href="{_detail_filename(task["task_id"], ENGINES[2])}">1.1.1 证据</a></p>'
        for task in data["tasks"]
        if task["change"] in ("恢复", "退化")
    )
    common = set(data["common_task_ids"])
    paired = summarize_attempts(
        [row for row in runs[2][1] if row["task_id"] in common], engines=("chromium", "moli")
    )
    audit = _duration_ratio_audit(paired).replace("Moli", "Moli 1.1.1")
    for tid in common:
        audit = audit.replace(_detail_filename(tid, "moli"), _detail_filename(tid, ENGINES[2]))
    duration = data["metrics"]["duration_ms"]
    memory = data["metrics"]["memory_mean_bytes"]
    upgrade = (
        f"共同成功任务中，Moli 平均耗时 {_show(duration['old_moli']['mean'])} → {_show(duration['new_moli']['mean'])} ms；"
        f"平均内存 {_show(memory['old_moli']['mean'], 1048576)} → {_show(memory['new_moli']['mean'], 1048576)} MiB。"
    )
    leaders = "、".join(
        label for label, count in zip(LABELS, counts, strict=True) if count == max(counts)
    )
    delta = counts[2] - counts[1]
    change_text = (
        f"{'多' if delta >= 0 else '少'}完成 {abs(delta)} 个任务" if delta else "完成任务数相同"
    )
    home = (
        _metric_guide()
        + f'<section class="card note"><h2>结论摘要</h2><p><strong>本轮 {leaders} 任务正确率最高；Moli 1.1.1 与 1.0.6 相比，{change_text}。</strong></p><p>'
        + upgrade
        + "</p></section>"
        + '<section class="card"><h2>任务正确率</h2><div class="table-wrap"><table><thead><tr><th>Provider / 版本</th><th>结果正确</th><th>结果与参考过程均正确</th><th>任务分析</th></tr></thead><tbody>'
        + "".join(correctness)
        + '</tbody></table></div><p class="muted">Chromium 取 1.1.1 评测轮的对照结果；Moli 1.0.6 取本次复跑，不混用历史轮次。</p>'
        + history
        + f"<details><summary>升级变化 · {restored} 个恢复 / {regressed} 个退化</summary>{changes}</details></section>"
        + f'<section class="card" id="performance"><h2>速度与资源</h2><p>统一比较两轮全部浏览器均成功的 {len(common)} 个任务；失败与重试不进入资源统计。绿色粗体标记较低数值，利用率、速率不代表越低越好。</p>'
        + '<p class="muted">两轮先后运行，公网状态会变化；这里是实测对比，不把所有差异归因于版本升级。</p>'
        + _performance(data)
        + audit
        + "</section>"
    )
    methods = (
        '<section class="card"><h2>版本与轮次</h2>'
        + _ledger(runs)
        + "<p>主表采用本次两轮结果；历史轮仅用于差异溯源。两轮任务集、系统依赖、Chromium 镜像与资源限制一致，Moli 1.1.1 镜像仅替换 1.0.6 镜像中的浏览器二进制。</p>"
        + "<details><summary>查看 Chromium 对照波动</summary>"
        + _table(
            ["指标", "1.0.6 复跑轮 Chromium", "1.1.1 评测轮 Chromium"],
            [
                [
                    "共同任务平均耗时 (ms)",
                    _show(duration["old_chromium"]["mean"]),
                    _show(duration["new_chromium"]["mean"]),
                ]
            ],
            "相同 Chromium 镜像的两次测量",
        )
        + "</details></section>"
        + _method_page(manifest)
    )
    source_links = "".join(
        f'<li>{_e(label)}：<a href="data/{prefix}/manifest.json">环境与运行记录</a> · <a href="data/{prefix}/attempts.jsonl">原始执行数据</a></li>'
        for label, prefix in (
            ("历史基线", "historical"),
            ("1.0.6 复跑", "old"),
            ("1.1.1 评测", "new"),
        )
    )
    appendix = (
        _source_code_entry()
        + '<section class="card"><h2>原始数据</h2><ul>'
        + source_links
        + "</ul><p>任务 ID 跨版本保持一致；浏览器版本和运行编号定位具体执行。旧数据只读保留。</p></section>"
    )
    bodies = {
        "index": home,
        "suite": _suite_page(manifest),
        "method": methods,
        "analysis": analysis,
        "appendix": appendix,
    }
    output.mkdir(parents=True, exist_ok=True)
    for slug, _, filename in PAGES:
        (output / filename).write_text(_shell(manifest, slug, bodies[slug]), encoding="utf-8")
    for filename, document in details.items():
        (output / filename).write_text(document, encoding="utf-8")
    (output / "index.html").write_text(
        (output / f"{REPORT_STEM}.html").read_text(), encoding="utf-8"
    )
    provenance = []
    for prefix, directory, (run, _) in zip(
        ("historical", "old", "new"), (historical_dir, old_dir, new_dir), runs, strict=True
    ):
        target = output / "data" / prefix
        target.mkdir(parents=True, exist_ok=True)
        hashes = {}
        for name in ("manifest.json", "attempts.jsonl"):
            content = (directory / name).read_bytes()
            (target / name).write_bytes(content)
            hashes[name] = hashlib.sha256(content).hexdigest()
        provenance.append({"run_id": run["run_id"], "path": f"data/{prefix}", "sha256": hashes})
    document = {
        "schema": "lex_browser_use_consolidated_report/1",
        "source_runs": provenance,
        "providers": {
            engine: {
                "run_id": run["run_id"],
                "source_engine": "chromium" if engine == "chromium" else "moli",
            }
            for engine, run in source_runs.items()
        },
        "functional": summary["functional"],
        "comparison": data,
        "task_index": [
            {
                "task_id": tid,
                "executions": [
                    {
                        "engine": engine,
                        "run_id": source_runs[engine]["run_id"],
                        "detail_page": _detail_filename(tid, engine),
                    }
                    for engine in ENGINES
                ],
            }
            for tid in summary["by_task"]
        ],
    }
    (output / f"{REPORT_STEM}-data.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output / "index.html"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("historical", "old", "new", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    print(build_report(args.historical, args.old, args.new, args.output))


if __name__ == "__main__":
    main()
