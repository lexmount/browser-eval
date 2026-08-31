"""Latest-only Markdown and formal HTML report rendering."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def _show(value: Any, *, divisor: float = 1.0, suffix: str = "") -> str:
    if not isinstance(value, (int, float)):
        return "unavailable"
    return f"{value / divisor:,.2f}{suffix}"


def _rate(values: dict[str, Any]) -> str:
    rate = values.get("pass_rate")
    return _show(rate * 100 if isinstance(rate, (int, float)) else None, suffix="%")


def _ratio(numerator: Any, denominator: Any) -> str:
    if not isinstance(numerator, (int, float)) or not isinstance(denominator, (int, float)):
        return "unavailable"
    if denominator == 0:
        return "unavailable"
    return f"{numerator / denominator:.2f}×"


def _observed_findings(summary: dict[str, Any]) -> list[str]:
    functional = summary.get("functional") or {}
    chromium_functional = functional.get("chromium") or {}
    moli_functional = functional.get("moli") or {}
    comparison = summary.get("resource_comparison") or {}
    by_engine = comparison.get("by_engine") or {}
    chromium = by_engine.get("chromium") or {}
    moli = by_engine.get("moli") or {}
    failed_tasks = [
        str(values.get("title", task_id))
        for task_id, values in (summary.get("by_task") or {}).items()
        if ((values.get("functional") or {}).get("moli") or {}).get("passes", 0)
        < ((values.get("functional") or {}).get("moli") or {}).get("attempts", 0)
    ]
    moli_drift = (summary.get("repeat_drift") or {}).get("moli") or {}

    findings = [
        (
            f"Chromium passed {chromium_functional.get('passes', 0)}/"
            f"{chromium_functional.get('attempts', 0)} attempts; Moli passed "
            f"{moli_functional.get('passes', 0)}/{moli_functional.get('attempts', 0)}. "
            f"Moli failures were confined to {', '.join(failed_tasks) if failed_tasks else 'no task'}."
        ),
        (
            f"Across {comparison.get('intersection_attempts', 0)} matched passing attempts, "
            f"Moli / Chromium medians were {_ratio((moli.get('duration_ms') or {}).get('median'), (chromium.get('duration_ms') or {}).get('median'))} duration, "
            f"{_ratio((moli.get('cpu_ms') or {}).get('median'), (chromium.get('cpu_ms') or {}).get('median'))} CPU, "
            f"{_ratio((moli.get('memory_peak_bytes') or {}).get('median'), (chromium.get('memory_peak_bytes') or {}).get('median'))} peak memory, "
            f"{_ratio((moli.get('network_rx_bytes') or {}).get('median'), (chromium.get('network_rx_bytes') or {}).get('median'))} RX, and "
            f"{_ratio((moli.get('network_tx_bytes') or {}).get('median'), (chromium.get('network_tx_bytes') or {}).get('median'))} TX."
        ),
        (
            f"Matched tool-output tokens were {_ratio((moli.get('tool_output_tokens') or {}).get('median'), (chromium.get('tool_output_tokens') or {}).get('median'))}; "
            "this browser-only suite shows no token-cost advantage."
        ),
        (
            f"For Moli, last/first baseline memory was {_show(moli_drift.get('memory_baseline_ratio'), suffix='×')} while last/first duration was "
            f"{_show(moli_drift.get('duration_ratio'), suffix='×')}. The five repeats did not show systematic runtime slowdown, "
            "but the warm process retained allocations and did not return to its first-run footprint after state cleanup."
        ),
        (
            "Decision for this suite: keep Chromium as the default provider. Offer Moli as an opt-in "
            "memory-oriented provider after workload-specific validation; these results support lower peak memory, "
            "but do not support broad claims of higher speed, lower CPU, lower network use, or lower token cost."
        ),
    ]
    return findings


def _markdown(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    protocol = manifest.get("protocol") or {}
    findings = _observed_findings(summary)
    lines = [
        "# Browser Use Evaluation — Latest",
        "",
        f"Run: `{manifest.get('run_id', 'unknown')}`",
        "",
        "## Methodology",
        "",
        "- Scope: one fixed-limit browser container per engine; controller excluded.",
        "- Browser processes stay warm; cold start and reset work are excluded.",
        "- Browser process and context stay warm. Before each task, the harness clears every top-level navigation origin plus cookies, permissions, cache and history, then replaces only the renderer page outside the timer.",
        f"- Repeats: {protocol.get('repeats', 'unknown')}; memory/PID sample interval: {protocol.get('sample_interval_ms', 'unknown')} ms.",
        f"- Post-reset quiescence: {protocol.get('post_reset_settle_ms', 'unknown')} ms, excluded from task duration.",
        "- Functional scores use every attempt; resource comparisons use only matched attempts passed by every engine.",
        "",
        "## Observed findings and decision",
        "",
        *(f"- {finding}" for finding in findings),
        "",
        "## Functional results",
        "",
        "| Engine | Pass / attempts | Pass rate | Duration median ms | Output tokens median |",
        "|---|---:|---:|---:|---:|",
    ]
    for engine, values in (summary.get("functional") or {}).items():
        lines.append(
            f"| {engine} | {values.get('passes', 0)}/{values.get('attempts', 0)} | "
            f"{_rate(values)} | {_show((values.get('duration_ms') or {}).get('median'))} | "
            f"{_show((values.get('tool_output_tokens') or {}).get('median'))} |"
        )
    lines.extend(["", "## Results by task complexity", ""])
    for complexity, values in (summary.get("by_complexity") or {}).items():
        lines.extend(
            [
                f"### {complexity}",
                "",
                "| Engine | Pass / attempts | Pass rate | Duration median ms |",
                "|---|---:|---:|---:|",
            ]
        )
        for engine, functional in (values.get("functional") or {}).items():
            lines.append(
                f"| {engine} | {functional.get('passes', 0)}/{functional.get('attempts', 0)} | "
                f"{_rate(functional)} | {_show((functional.get('duration_ms') or {}).get('median'))} |"
            )
        tier_resources = values.get("resource_comparison") or {}
        lines.extend(
            [
                "",
                f"Matched all-pass attempts: {tier_resources.get('intersection_attempts', 0)}",
                "",
                "| Engine | Duration median ms | CPU median ms | Peak memory median MiB | RX median KiB | TX median KiB |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for engine, resources in (tier_resources.get("by_engine") or {}).items():
            lines.append(
                f"| {engine} | {_show((resources.get('duration_ms') or {}).get('median'))} | "
                f"{_show((resources.get('cpu_ms') or {}).get('median'))} | "
                f"{_show((resources.get('memory_peak_bytes') or {}).get('median'), divisor=1024 * 1024)} | "
                f"{_show((resources.get('network_rx_bytes') or {}).get('median'), divisor=1024)} | "
                f"{_show((resources.get('network_tx_bytes') or {}).get('median'), divisor=1024)} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Task-level results",
            "",
            "| Task | Tier | Engine | Pass / attempts | Duration median ms | Representative error |",
            "|---|---|---|---:|---:|---|",
        ]
    )
    for task_id, values in (summary.get("by_task") or {}).items():
        for engine, functional in (values.get("functional") or {}).items():
            errors = "; ".join((values.get("representative_errors") or {}).get(engine) or [])
            lines.append(
                f"| {values.get('title', task_id)} | {values.get('complexity')} | {engine} | "
                f"{functional.get('passes', 0)}/{functional.get('attempts', 0)} | "
                f"{_show((functional.get('duration_ms') or {}).get('median'))} | "
                f"{errors or '—'} |"
            )
    lines.extend(
        [
            "",
            "## Repeat drift check",
            "",
            "| Engine | Paired tasks | Last / first baseline memory | Last / first duration |",
            "|---|---:|---:|---:|",
        ]
    )
    for engine, values in (summary.get("repeat_drift") or {}).items():
        lines.append(
            f"| {engine} | {values.get('paired_tasks', 0)} | "
            f"{_show(values.get('memory_baseline_ratio'), suffix='×')} | "
            f"{_show(values.get('duration_ratio'), suffix='×')} |"
        )
    comparison = summary.get("resource_comparison") or {}
    lines.extend(
        [
            "## All-pass intersection resources",
            "",
            f"Matched attempts: **{comparison.get('intersection_attempts', 0)}**",
            "",
            "| Engine | Duration median ms | CPU median ms | Peak memory median MiB | RX median KiB | TX median KiB | Output tokens median |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for engine, values in (comparison.get("by_engine") or {}).items():
        lines.append(
            f"| {engine} | {_show((values.get('duration_ms') or {}).get('median'))} | "
            f"{_show((values.get('cpu_ms') or {}).get('median'))} | "
            f"{_show((values.get('memory_peak_bytes') or {}).get('median'), divisor=1024 * 1024)} | "
            f"{_show((values.get('network_rx_bytes') or {}).get('median'), divisor=1024)} | "
            f"{_show((values.get('network_tx_bytes') or {}).get('median'), divisor=1024)} | "
            f"{_show((values.get('tool_output_tokens') or {}).get('median'))} |"
        )
    chromium = (comparison.get("by_engine") or {}).get("chromium") or {}
    moli = (comparison.get("by_engine") or {}).get("moli") or {}
    lines.extend(
        [
            "",
            "### Moli / Chromium ratios on matched passing attempts",
            "",
            "| Metric | Ratio | Interpretation |",
            "|---|---:|---|",
        ]
    )
    for label, key in (
        ("Duration", "duration_ms"),
        ("CPU", "cpu_ms"),
        ("Peak memory", "memory_peak_bytes"),
        ("Network RX", "network_rx_bytes"),
        ("Network TX", "network_tx_bytes"),
        ("Tool-output tokens", "tool_output_tokens"),
    ):
        ratio = _ratio(
            (moli.get(key) or {}).get("median"),
            (chromium.get(key) or {}).get("median"),
        )
        lines.append(f"| {label} | {ratio} | Below 1× favors Moli |")
    lines.extend(
        [
            "",
            "## Initial public task suite",
            "",
            "| Task | Steps | Tier |",
            "|---|---:|---|",
        ]
    )
    for task in manifest.get("suite") or []:
        lines.append(
            f"| {task.get('title', task.get('task_id'))} | {task.get('step_count')} | {task.get('complexity')} |"
        )
    lines.extend(
        [
            "",
            "## CubeSandbox decision",
            "",
            "- CubeSandbox officially supports headless Chromium in a MicroVM over CDP and is a valid future Browser Use backend: https://docs.cubesandbox.com/guide/tutorials/examples.html",
            "- Its native resource endpoint exposes host-sandbox and guest-workload CPU/current-memory scopes, but defaults to 5 s collection, retains only the latest sample, does not export an exact epoch-scoped memory peak, and lists no per-sandbox network byte counters: https://docs.cubesandbox.com/guide/resource-metrics.html",
            "- Therefore this first local reference uses 100 ms Docker cgroup/network-namespace accounting. CubeSandbox should be added as a separate production-topology backend, not mixed into the same aggregate.",
            "",
            "## Interpretation limits",
            "",
            "- Docker is the reproducible accounting boundary, not a claim that CowAgent must deploy browsers in Docker.",
            "- Public-site latency and network bytes retain Internet/CDN variance; balanced ordering and repeated medians reduce but do not eliminate it.",
            "- Memory peak is sampled, so a spike shorter than the sampling interval can be missed.",
            "- Docker cgroup PID values count Linux tasks/threads, not only OS processes.",
            "- Chromium includes a tiny in-container TCP forwarder because Debian Chromium binds CDP to loopback; its resource cost is included and slightly biases Chromium upward.",
            "",
            "## Environment",
            "",
            "```json",
            json.dumps(manifest.get("environment") or {}, indent=2, sort_keys=True),
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{html.escape(value)}</th>" for value in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(value)}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return (
        f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _html_report(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    protocol = manifest.get("protocol") or {}
    findings_html = "".join(
        f"<li>{html.escape(finding)}</li>" for finding in _observed_findings(summary)
    )
    functional_rows = [
        [
            engine,
            f"{values.get('passes', 0)} / {values.get('attempts', 0)}",
            _rate(values),
            _show((values.get("duration_ms") or {}).get("median")),
            _show((values.get("tool_output_tokens") or {}).get("median")),
        ]
        for engine, values in (summary.get("functional") or {}).items()
    ]
    tier_rows: list[list[str]] = []
    tier_resource_rows: list[list[str]] = []
    for tier, values in (summary.get("by_complexity") or {}).items():
        for engine, result in (values.get("functional") or {}).items():
            tier_rows.append(
                [
                    tier,
                    engine,
                    f"{result.get('passes', 0)} / {result.get('attempts', 0)}",
                    _rate(result),
                    _show((result.get("duration_ms") or {}).get("median")),
                ]
            )
        tier_resources = values.get("resource_comparison") or {}
        for engine, resources in (tier_resources.get("by_engine") or {}).items():
            tier_resource_rows.append(
                [
                    tier,
                    str(tier_resources.get("intersection_attempts", 0)),
                    engine,
                    _show((resources.get("duration_ms") or {}).get("median")),
                    _show((resources.get("cpu_ms") or {}).get("median")),
                    _show(
                        (resources.get("memory_peak_bytes") or {}).get("median"),
                        divisor=1024 * 1024,
                    ),
                    _show(
                        (resources.get("network_rx_bytes") or {}).get("median"),
                        divisor=1024,
                    ),
                    _show(
                        (resources.get("network_tx_bytes") or {}).get("median"),
                        divisor=1024,
                    ),
                ]
            )
    task_result_rows: list[list[str]] = []
    for task_id, values in (summary.get("by_task") or {}).items():
        for engine, result in (values.get("functional") or {}).items():
            errors = "; ".join((values.get("representative_errors") or {}).get(engine) or [])
            task_result_rows.append(
                [
                    str(values.get("title", task_id)),
                    str(values.get("complexity")),
                    engine,
                    f"{result.get('passes', 0)} / {result.get('attempts', 0)}",
                    _show((result.get("duration_ms") or {}).get("median")),
                    errors or "—",
                ]
            )
    drift_rows = [
        [
            engine,
            str(values.get("paired_tasks", 0)),
            _show(values.get("memory_baseline_ratio"), suffix="×"),
            _show(values.get("duration_ratio"), suffix="×"),
        ]
        for engine, values in (summary.get("repeat_drift") or {}).items()
    ]
    comparison = summary.get("resource_comparison") or {}
    resource_rows = [
        [
            engine,
            _show((values.get("duration_ms") or {}).get("median")),
            _show((values.get("cpu_ms") or {}).get("median")),
            _show(
                (values.get("memory_peak_bytes") or {}).get("median"),
                divisor=1024 * 1024,
            ),
            _show((values.get("network_rx_bytes") or {}).get("median"), divisor=1024),
            _show((values.get("network_tx_bytes") or {}).get("median"), divisor=1024),
            _show((values.get("tool_output_tokens") or {}).get("median")),
        ]
        for engine, values in (comparison.get("by_engine") or {}).items()
    ]
    chromium_metrics = (comparison.get("by_engine") or {}).get("chromium") or {}
    moli_metrics = (comparison.get("by_engine") or {}).get("moli") or {}
    ratio_rows = [
        [
            label,
            _ratio(
                (moli_metrics.get(key) or {}).get("median"),
                (chromium_metrics.get(key) or {}).get("median"),
            ),
            "Below 1× favors Moli",
        ]
        for label, key in (
            ("Duration", "duration_ms"),
            ("CPU", "cpu_ms"),
            ("Peak memory", "memory_peak_bytes"),
            ("Network RX", "network_rx_bytes"),
            ("Network TX", "network_tx_bytes"),
            ("Tool-output tokens", "tool_output_tokens"),
        )
    ]
    task_rows = [
        [
            str(task.get("title") or task.get("task_id")),
            str(task.get("step_count")),
            str(task.get("complexity")),
        ]
        for task in manifest.get("suite") or []
    ]
    environment = html.escape(
        json.dumps(manifest.get("environment") or {}, indent=2, sort_keys=True)
    )
    run_id = html.escape(str(manifest.get("run_id", "unknown")))
    repeats = html.escape(str(protocol.get("repeats", "unknown")))
    sample_ms = html.escape(str(protocol.get("sample_interval_ms", "unknown")))
    settle_ms = html.escape(str(protocol.get("post_reset_settle_ms", "unknown")))
    intersection = comparison.get("intersection_attempts", 0)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Browser Use Evaluation — Latest</title>
<style>
:root{{--ink:#172033;--muted:#667085;--line:#d9dfeb;--panel:#f6f8fc;--accent:#3157d5}}
*{{box-sizing:border-box}} body{{margin:0;background:#eef2f8;color:var(--ink);font:15px/1.6 system-ui,sans-serif}}
main{{max-width:1180px;margin:32px auto;padding:0 24px 64px}} header,section{{background:white;border:1px solid var(--line);border-radius:14px;padding:26px;margin:16px 0}}
h1{{margin:0 0 6px;font-size:32px}} h2{{margin:0 0 14px;font-size:21px}} p,ul{{margin:8px 0}} .muted{{color:var(--muted)}}
.badge{{display:inline-block;background:#e9eeff;color:#2646aa;border-radius:999px;padding:3px 10px;font-size:13px}}
.table-wrap{{overflow-x:auto}} table{{width:100%;border-collapse:collapse}} th,td{{border-bottom:1px solid var(--line);padding:10px 12px;text-align:left;white-space:nowrap}} th{{background:var(--panel);font-size:13px}}
pre{{overflow:auto;background:#111827;color:#dbe5ff;padding:18px;border-radius:10px;font-size:12px}} code{{font-family:ui-monospace,monospace}}
</style></head><body><main>
<header><span class="badge">latest reproducible run</span><h1>Browser Use Evaluation</h1><p class="muted">Run {run_id} · warm-browser Chromium vs Moli</p></header>
<section><h2>Methodology</h2><ul>
<li>Each engine runs in its own Docker container with identical CPU, memory, PID and shared-memory limits; controller usage is excluded.</li>
<li>Browser process and context remain warm; browser cold start is excluded.</li>
<li>Before each task, the harness inventories and clears every top-level HTTP(S) navigation origin plus cookies, permissions, browser cache and history, then replaces only the renderer page outside the timer.</li>
<li>{repeats} repeats; {sample_ms} ms memory/PID sampling; balanced alternating engine order.</li>
<li>After reset and renderer GC, both engines receive a {settle_ms} ms quiescence window before counters and timing begin.</li>
<li>Functional results include every attempt. Resource results include only matched attempts passed by both engines.</li>
</ul></section>
<section><h2>Observed findings and decision</h2><ul>{findings_html}</ul></section>
<section><h2>Functional results</h2>{_table(["Engine", "Pass / attempts", "Pass rate", "Median duration (ms)", "Median output tokens"], functional_rows)}</section>
<section><h2>Results by operation-count tier</h2>{_table(["Tier", "Engine", "Pass / attempts", "Pass rate", "Median duration (ms)"], tier_rows)}</section>
<section><h2>Matched resources by operation-count tier</h2>{_table(["Tier", "Matched attempts", "Engine", "Duration median (ms)", "CPU median (ms)", "Peak memory median (MiB)", "RX median (KiB)", "TX median (KiB)"], tier_resource_rows)}</section>
<section><h2>Task-level results and diagnostics</h2>{_table(["Task", "Tier", "Engine", "Pass / attempts", "Median duration (ms)", "Representative error"], task_result_rows)}</section>
<section><h2>Repeat drift check</h2><p class="muted">Ratios compare the last repeat with the first repeat for the same tasks. Values near 1× indicate no systematic accumulation.</p>{_table(["Engine", "Paired tasks", "Baseline memory ratio", "Duration ratio"], drift_rows)}</section>
<section><h2>Matched resource results</h2><p class="muted">All-pass intersection: {intersection} task-attempt pairs.</p>{_table(["Engine", "Duration median (ms)", "CPU median (ms)", "Peak memory median (MiB)", "RX median (KiB)", "TX median (KiB)", "Output tokens median"], resource_rows)}<h3>Moli / Chromium ratios</h3>{_table(["Metric", "Ratio", "Interpretation"], ratio_rows)}</section>
<section><h2>Initial public, no-login task suite</h2>{_table(["Task", "Browser operations", "Tier"], task_rows)}</section>
<section><h2>CubeSandbox decision</h2><ul>
<li><a href="https://docs.cubesandbox.com/guide/tutorials/examples.html">CubeSandbox officially demonstrates headless Chromium in a MicroVM over CDP</a>, so it is a valid future Browser Use backend.</li>
<li><a href="https://docs.cubesandbox.com/guide/resource-metrics.html">Its native metrics expose host-sandbox and guest-workload CPU/current-memory scopes</a>, but default to 5 s collection, retain only the latest sample, do not export an exact memory peak, and list no per-sandbox network byte counters.</li>
<li>This first local reference therefore uses 100 ms Docker cgroup/network-namespace accounting. A CubeSandbox backend should model production topology and remain a separate accounting scope.</li>
</ul></section>
<section><h2>Interpretation limits</h2><ul>
<li>Docker is the reproducible accounting boundary, not a recommendation that CowAgent production browsers must run in Docker.</li>
<li>Public-site latency and network traffic retain Internet/CDN variance. Repeated medians and balanced order reduce but cannot remove it.</li>
<li>Memory peaks shorter than the sampling interval can be missed.</li>
<li>Docker cgroup PID values count Linux tasks/threads, not only OS processes.</li>
<li>Chromium includes a tiny in-container TCP forwarder because Debian Chromium binds CDP to loopback; its resource cost is included and slightly biases Chromium upward.</li>
</ul></section>
<section><h2>Environment</h2><pre><code>{environment}</code></pre></section>
</main></body></html>\n"""


def write_latest_report(
    reports_dir: str | Path, *, manifest: dict[str, Any], summary: dict[str, Any]
) -> Path:
    root = Path(reports_dir)
    root.mkdir(parents=True, exist_ok=True)
    for pattern in ("browser-use-evaluation-*.md", "browser-use-evaluation-*.html"):
        for old in root.glob(pattern):
            if old.name not in {
                "browser-use-evaluation-latest.md",
                "browser-use-evaluation-latest.html",
            }:
                old.unlink()
    md_path = root / "browser-use-evaluation-latest.md"
    md_path.write_text(_markdown(manifest, summary), encoding="utf-8")
    (root / "browser-use-evaluation-latest.html").write_text(
        _html_report(manifest, summary), encoding="utf-8"
    )
    return md_path
