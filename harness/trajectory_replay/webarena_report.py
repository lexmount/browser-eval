"""Build the evidence-backed WebArena Browser Trajectory Replay report."""

from __future__ import annotations

import hashlib
import html
import json
import math
import re
import shutil
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from harness.trajectory_replay.webarena_review import (
    load_validated_failure_review,
    programmatic_failure,
)

LOCAL_PATH_RE = re.compile(r"/(?:Users|home|private|tmp|var|opt)/[^\s\"']+")


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stat(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "mean": None, "p50": None, "p95": None}
    ordered = sorted(values)

    def rank(quantile: float) -> float:
        return ordered[min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1)]
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "p50": rank(0.5),
        "p95": rank(0.95),
    }


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _clean(item)
            for key, item in value.items()
            if key not in {"path", "paths"}
        }
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, str):
        value = LOCAL_PATH_RE.sub("[local path removed]", value)
        value = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[secret removed]", value)
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_clean(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_artifact(ref: dict[str, Any] | None) -> Any:
    if not ref or not ref.get("path"):
        return None
    path = Path(str(ref["path"])).expanduser().resolve()
    if not path.is_file() or _sha256(path) != ref.get("sha256"):
        raise ValueError(f"evidence artifact is missing or changed: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _format_ms(value: float | None) -> str:
    return "—" if value is None else f"{value / 1000:.1f} 秒"


def _format_mib(value: float | None) -> str:
    return "—" if value is None else f"{value / 1024 / 1024:.1f} MiB"


def _metric_lines(
    metric: dict[str, Any],
    formatter: Any,
    method_id: str,
    winners: dict[str, set[str]],
    *,
    lang: str,
) -> str:
    labels = (("mean", "均值"), ("p50", "P50"), ("p95", "P95"))
    if lang == "en":
        labels = (("mean", "Mean"), ("p50", "P50"), ("p95", "P95"))
    return "<br>".join(
        f'<span class="metric-label">{label}</span> '
        + _winner(formatter(metric.get(key)), method_id in winners[key], lang=lang)
        for key, label in labels
    )


def _unique_winners(
    rows: list[dict[str, Any]], key: str, *, higher: bool
) -> set[str]:
    numeric = [(row["id"], row.get(key)) for row in rows if isinstance(row.get(key), (int, float))]
    if not numeric:
        return set()
    best = (max if higher else min)(value for _, value in numeric)
    winners = {name for name, value in numeric if value == best}
    return winners if len(winners) == 1 else set()


def _nav(lang: str, active: str, *, depth: int) -> str:
    labels = {
        "zh": (("index", "结论"), ("dataset", "评测集"), ("results", "结果与诊断"), ("environment", "环境与方法"), ("review", "核验记录")),
        "en": (("index", "Summary"), ("dataset", "Dataset"), ("results", "Results & diagnosis"), ("environment", "Environment & method"), ("review", "Review history")),
    }[lang]
    local_prefix = "../" * depth
    switch = local_prefix + ("../index.html" if lang == "en" else "en/index.html")
    switch_label = "中文" if lang == "en" else "English"
    brand = "Trajectory Replay" if lang == "en" else "轨迹重放"
    theme_label = "Toggle theme" if lang == "en" else "切换主题"
    links = "".join(
        f'<a href="{local_prefix}{slug}.html"'
        + (' aria-current="page"' if slug == active else "")
        + f">{label}</a>"
        for slug, label in labels
    )
    return f'<header class="top"><a class="brand" href="{local_prefix}index.html">{brand}</a><nav>{links}</nav><a class="language" href="{switch}">{switch_label}</a><button class="theme" type="button" aria-label="{theme_label}">◐</button></header>'


def _shell(lang: str, active: str, title: str, body: str, *, depth: int = 0) -> str:
    prefix = "../" * depth
    asset_prefix = prefix if lang == "zh" else "../" + prefix
    footer = (
        "WebArena-Verified 1.2.3 · 浏览器轨迹重放"
        if lang == "zh"
        else "WebArena-Verified 1.2.3 · Browser Trajectory Replay"
    )
    return f'''<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)}</title><link rel="stylesheet" href="{asset_prefix}assets/report.css"></head><body>{_nav(lang, active, depth=depth)}<main>{body}</main><footer>{footer}</footer><script src="{asset_prefix}assets/app.js"></script></body></html>'''


def _winner(value: str, won: bool, *, lang: str) -> str:
    badge = "优" if lang == "zh" else "Best"
    return f'<strong class="winner">{value}<span>{badge}</span></strong>' if won else value


def _metrics_table(methods: list[dict[str, Any]], lang: str) -> str:
    pass_winners = _unique_winners(methods, "pass_rate", higher=True)
    metric_winners = {
        metric: {
            stat: _unique_winners(
                [{"id": row["id"], "value": row[metric][stat]} for row in methods],
                "value",
                higher=False,
            )
            for stat in ("mean", "p50", "p95")
        }
        for metric in ("duration", "action_latency", "cpu", "memory")
    }
    headers = (
        ("浏览器", "通过", "通过率", "成功交集端到端耗时", "单次动作耗时", "CPU 时间", "平均内存")
        if lang == "zh"
        else ("Browser", "Passed", "Pass rate", "End-to-end time on shared passes", "Per-action latency", "CPU time", "Mean memory")
    )
    body = []

    def time_value(value: float | None) -> str:
        if lang == "zh":
            return _format_ms(value)
        return "—" if value is None else f"{value / 1000:.1f} s"

    for row in methods:
        key = row["id"]
        cells = [
            row["label"],
            _winner(
                f'{row["passed"]}/{row["total"]}',
                key in pass_winners,
                lang=lang,
            ),
            _winner(f'{row["pass_rate"] * 100:.1f}%', key in pass_winners, lang=lang),
            _metric_lines(
                row["duration"], time_value, key, metric_winners["duration"], lang=lang
            ),
            _metric_lines(
                row["action_latency"],
                time_value,
                key,
                metric_winners["action_latency"],
                lang=lang,
            ),
            _metric_lines(row["cpu"], time_value, key, metric_winners["cpu"], lang=lang),
            _metric_lines(
                row["memory"], _format_mib, key, metric_winners["memory"], lang=lang
            ),
        ]
        body.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    return f'<div class="table-scroll"><table class="metrics"><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in headers)}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _copy_failure_screenshot(attempt: dict[str, Any], output: Path, key: str) -> str | None:
    ref = (attempt.get("evidence") or {}).get("failure_screenshot")
    if not ref or not ref.get("path"):
        return None
    source = Path(str(ref["path"])).expanduser().resolve()
    if not source.is_file() or _sha256(source) != ref.get("sha256"):
        raise ValueError(f"failure screenshot is missing or changed: {key}")
    target = output / "assets/screenshots" / f"{key}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return f"assets/screenshots/{key}.png"


def _public_evidence(
    attempt: dict[str, Any], review: dict[str, Any] | None, output: Path
) -> dict[str, Any]:
    key = f'{attempt["engine"]}-{attempt["task_id"]}'
    trace = _load_artifact((attempt.get("evidence") or {}).get("trace")) or []
    evaluation = _load_artifact((attempt.get("evidence") or {}).get("evaluation"))
    failed = programmatic_failure(attempt)
    screenshot = _copy_failure_screenshot(attempt, output, key)
    public = {
        "result": {
            "engine": attempt["engine"],
            "task_id": attempt["task_id"],
            "status": attempt["status"],
            "action_count": attempt["action_count"],
            "completed_action_count": attempt["completed_action_count"],
            "duration_ms": attempt["duration_ms"],
            "timings": attempt.get("timings") or {},
            "resource": attempt.get("resource") or {},
        },
        "programmatic_failure": failed,
        "review": (
            {
                "cause_code": review["cause_group"]["code"],
                "label_zh": review["cause_group"]["label_zh"],
                "label_en": review["cause_group"]["label_en"],
                "reason_zh": review["reason_zh"],
                "reason_en": review["reason_en"],
                "responsibility": review["cause_group"]["responsibility"],
                "remediation_owner": review["cause_group"]["remediation_owner"],
                "remediation_status": review["cause_group"]["remediation_status"],
            }
            if review
            else None
        ),
        "trajectory": trace,
        "official_evaluation": evaluation,
        "screenshot": screenshot,
    }
    _write_json(output / "data/evidence" / f"{key}.json", public)
    return public


def _detail_page(
    lang: str,
    attempt: dict[str, Any],
    task: dict[str, Any],
    method: dict[str, Any],
    evidence: dict[str, Any],
) -> str:
    failed = evidence["programmatic_failure"]
    review = evidence.get("review")
    zh = lang == "zh"
    status = ("通过" if attempt["status"] == "pass" else "失败") if zh else attempt["status"].title()
    reason = (review.get("reason_zh") if zh else review.get("reason_en")) if review else ("—")
    label = (review.get("label_zh") if zh else review.get("label_en")) if review else "—"
    key = f'{attempt["engine"]}-{attempt["task_id"]}'
    report_root = "../" if zh else "../../"
    rows = []
    for step in evidence["trajectory"]:
        arguments = step.get("arguments") or {}
        action = arguments.get("action") or ""
        target = arguments.get("selector") or arguments.get("url") or arguments.get("key") or ""
        result_lines = str(step.get("result") or "").splitlines()
        result = (result_lines[0] if result_lines else "")[:220]
        failed_step = failed.get("step") == step.get("index")
        rows.append(
            f'<tr class="{"failed-step" if failed_step else ""}"><td>{step.get("index")}</td><td><code>{_e(action)}</code></td><td><code>{_e(target)}</code></td><td>{_e(step.get("duration_ms"))} ms</td><td>{_e(step.get("status"))}</td><td>{_e(result)}</td></tr>'
        )
    screenshot = (
        f'<figure><img src="{report_root}{evidence["screenshot"]}" alt="{"失败后的最后页面" if zh else "Last page after failure"}"><figcaption>{"失败后保存的最后页面；不等同于错误发生瞬间。" if zh else "Last page saved after failure; not necessarily the exact error instant."}</figcaption></figure>'
        if evidence.get("screenshot")
        else ""
    )
    body = f'''
    <section class="detail-hero"><p class="eyebrow">{_e(key)}</p><h1>{_e(method["label"])} · Task #{attempt["task_id"]}</h1><p>{_e(task["task"]["intent"])}</p></section>
    <section><h2>{"结论与定位" if zh else "Verdict and failure location"}</h2><div class="diagnosis-grid">
      <article class="verdict {attempt["status"]}"><span>{"结果" if zh else "Verdict"}</span><strong>{status}</strong><small>{attempt["completed_action_count"]}/{attempt["action_count"]} {"个动作完成" if zh else "actions completed"}</small></article>
      <article><span>{"程序定位" if zh else "Programmatic location"}</span><strong>{_e(failed["stage"])}</strong><p>{_e(failed["message"])}</p></article>
      <article><span>{"复核根因" if zh else "Reviewed root cause"}</span><strong>{_e(label)}</strong><p>{_e(reason)}</p></article>
      <article><span>{"处理" if zh else "Remediation"}</span><strong>{_e(review.get("remediation_status") if review else "—")}</strong><p>{_e(review.get("remediation_owner") if review else "—")}</p></article>
    </div></section>
    <section><h2>{"固定操作轨迹" if zh else "Fixed action trajectory"}</h2><div class="table-scroll"><table><thead><tr><th>#</th><th>{"动作" if zh else "Action"}</th><th>{"目标" if zh else "Target"}</th><th>{"耗时" if zh else "Time"}</th><th>{"状态" if zh else "Status"}</th><th>{"结果" if zh else "Result"}</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
    <section><h2>{"日志与证据" if zh else "Logs and evidence"}</h2><div class="evidence-actions"><a href="../evidence-viewer.html?file={"data/evidence/" if zh else "../data/evidence/"}{key}.json">{"在网页中查看完整证据" if zh else "Open evidence viewer"}</a><a href="{report_root}data/evidence/{key}.json">{"原始 JSON" if zh else "Raw JSON"}</a></div>{screenshot}</section>'''
    title = (
        f"{method['label']} · 任务 #{attempt['task_id']}"
        if zh
        else f"{method['label']} · Task #{attempt['task_id']}"
    )
    return _shell(lang, "results", title, body, depth=1)


def _assets(output: Path) -> None:
    css = r''':root{--bg:#f4f7fb;--panel:#fff;--ink:#102235;--muted:#60748a;--line:#d4e0ec;--accent:#0969da;--good:#087f4f;--bad:#c52c45;--wash:#eaf3ff}html[data-theme=dark]{--bg:#0c1520;--panel:#121f2d;--ink:#eef5fb;--muted:#9db0c2;--line:#2b4054;--accent:#62a8ff;--good:#4ed29b;--bad:#ff7d91;--wash:#142d49}*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 Inter,ui-sans-serif,system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}.top{height:70px;display:flex;align-items:center;gap:28px;padding:0 max(24px,calc((100% - 1440px)/2));background:var(--panel);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:10}.brand{font-weight:900;color:var(--ink);text-decoration:none;white-space:nowrap}.top nav{display:flex;gap:22px;overflow:auto;flex:1}.top nav a,.language{color:var(--accent);text-decoration:none;white-space:nowrap}.top nav a[aria-current=page]{color:var(--ink);font-weight:800;border-bottom:3px solid var(--ink)}.theme{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:50%;width:38px;height:38px}main,footer{width:min(1440px,calc(100% - 48px));margin:auto}footer{padding:36px 0;color:var(--muted);border-top:1px solid var(--line)}section{padding:34px 0}.hero{padding:46px 0 28px}.hero h1,.detail-hero h1{font-size:clamp(36px,5vw,68px);line-height:1.08;margin:8px 0 14px;letter-spacing:-.04em}.hero p,.detail-hero p{max-width:880px;color:var(--muted);font-size:18px}.eyebrow{text-transform:uppercase;letter-spacing:.14em;color:var(--accent)!important;font-size:13px!important;font-weight:800}h2{font-size:30px;margin:0 0 16px}.table-scroll{overflow:auto;border:1px solid var(--line);border-radius:16px;background:var(--panel)}table{border-collapse:collapse;width:100%;min-width:900px}th,td{text-align:left;vertical-align:top;padding:14px 16px;border-bottom:1px solid var(--line)}th{background:color-mix(in srgb,var(--wash) 70%,var(--panel));font-size:14px;position:sticky;top:0}td{overflow-wrap:anywhere}.metrics td{min-width:150px}.metric-label{display:inline-block;width:42px;color:var(--muted);font-size:13px}.winner{color:var(--good)}.winner>span{display:inline-grid;place-items:center;margin-left:7px;min-width:24px;height:24px;padding:0 6px;border-radius:12px;background:var(--good);color:white;font-size:12px}.callout{padding:16px 18px;border-left:4px solid var(--accent);background:var(--panel);border-radius:0 12px 12px 0}.summary-grid,.diagnosis-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}.summary-grid article,.diagnosis-grid article{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;min-height:150px}.summary-grid strong,.diagnosis-grid strong{display:block;font-size:24px;margin:8px 0}.summary-grid span,.diagnosis-grid span{color:var(--muted);font-size:13px}.verdict.fail{border-left:5px solid var(--bad)}.verdict.pass{border-left:5px solid var(--good)}.filters{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}.filters input,.filters select{padding:10px 12px;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:9px;min-width:170px}.inventory{height:620px;overflow:auto}.inventory table{min-width:1500px}.inventory tbody tr{cursor:pointer}.inventory tbody tr:hover{background:var(--wash)}.status-pass{color:var(--good);font-weight:800}.status-fail{color:var(--bad);font-weight:800}.failed-step{background:color-mix(in srgb,var(--bad) 10%,var(--panel))}.failed-step td:first-child{border-left:4px solid var(--bad)}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:420px;overflow:auto;background:#071923;color:#dff4ff;padding:14px;border-radius:10px;font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}figure{margin:22px 0}figure img{display:block;max-width:100%;border:1px solid var(--line);border-radius:12px}figcaption{color:var(--muted);font-size:13px}.evidence-actions{display:flex;gap:12px;flex-wrap:wrap}.evidence-actions a{padding:10px 14px;border:1px solid var(--line);border-radius:9px;background:var(--panel);color:var(--accent);text-decoration:none}.detail-hero{padding-top:40px}.viewer{height:75vh}.viewer pre{height:100%;max-height:none}dialog{width:min(850px,calc(100% - 32px));border:1px solid var(--line);border-radius:16px;background:var(--panel);color:var(--ink);padding:0}dialog::backdrop{background:#07111bbf}.dialog-head{display:flex;justify-content:space-between;padding:18px;border-bottom:1px solid var(--line)}.dialog-body{padding:18px}.dialog-head button{border:0;background:none;color:var(--ink);font-size:24px}.mermaid{width:100%;max-width:100%;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:24px;overflow:auto}.flow-fallback{display:flex;align-items:stretch;gap:10px;overflow:auto;padding:18px;background:var(--panel);border:1px solid var(--line);border-radius:14px}.flow-node{min-width:150px;padding:14px;border-radius:10px;background:var(--wash)}.flow-node strong{display:block;color:var(--accent);margin-bottom:6px}.flow-arrow{display:grid;place-items:center;color:var(--muted);font-size:24px}@media(max-width:720px){.top{padding:0 16px;gap:14px}.top nav{gap:14px}main,footer{width:calc(100% - 28px)}section{padding:26px 0}.hero h1,.detail-hero h1{font-size:36px}.inventory{height:560px}.language{display:none}}'''
    js = r'''document.querySelector('.theme')?.addEventListener('click',()=>{const root=document.documentElement;root.dataset.theme=root.dataset.theme==='dark'?'light':'dark';localStorage.setItem('report-theme',root.dataset.theme)});document.documentElement.dataset.theme=localStorage.getItem('report-theme')||'light';const table=document.querySelector('[data-results]');if(table){const rows=[...table.querySelectorAll('tbody tr')],controls=[...document.querySelectorAll('[data-filter]')],dialog=document.querySelector('#diagnosis-dialog');function filter(){const q=(document.querySelector('[data-filter=q]')?.value||'').toLowerCase();rows.forEach(row=>{const ok=controls.every(c=>!c.value||c.dataset.filter==='q'||row.dataset[c.dataset.filter]===c.value)&&(!q||row.textContent.toLowerCase().includes(q));row.hidden=!ok})}controls.forEach(c=>c.addEventListener('input',filter));filter();const resultsUrl=document.documentElement.lang==='zh'?'data/results.json':'../data/results.json';fetch(resultsUrl).then(r=>r.json()).then(data=>{const byKey=Object.fromEntries(data.map(x=>[x.key,x]));rows.forEach(row=>row.addEventListener('click',e=>{if(e.target.closest('a'))return;const x=byKey[row.dataset.key];if(!x||!dialog)return;dialog.querySelector('[data-dialog-title]').textContent=x.browser+' · Task #'+x.task_id;dialog.querySelector('[data-dialog-stage]').textContent=x.failure_stage;dialog.querySelector('[data-dialog-error]').textContent=x.program_error||'—';dialog.querySelector('[data-dialog-cause]').textContent=(document.documentElement.lang==='zh'?x.reason_zh:x.reason_en)||'—';dialog.querySelector('[data-dialog-link]').href='result/'+x.key+'.html';dialog.showModal()}))});dialog?.querySelector('button')?.addEventListener('click',()=>dialog.close())}const viewer=document.querySelector('[data-evidence-viewer]');if(viewer){const file=new URLSearchParams(location.search).get('file');const safe=/^(?:\.\.\/)?data\/evidence\/[a-z0-9.-]+\.json$/.test(file||'');if(safe)fetch(file).then(r=>{if(!r.ok)throw Error(r.status);return r.json()}).then(x=>viewer.textContent=JSON.stringify(x,null,2)).catch(e=>viewer.textContent='Evidence could not be loaded: '+e.message);else viewer.textContent='No evidence file selected.'}'''
    (output / "assets").mkdir(parents=True, exist_ok=True)
    (output / "assets/report.css").write_text(css, encoding="utf-8")
    (output / "assets/app.js").write_text(js, encoding="utf-8")


def build_webarena_replay_report(
    *,
    results_path: str | Path,
    dataset_path: str | Path,
    review_path: str | Path,
    profile_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    validated = load_validated_failure_review(results_path, review_path)
    results = validated["results"]
    reviews = validated["by_result"]
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    output = Path(output_dir).resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    _assets(output)

    tasks = {int(item["task_id"]): item for item in dataset["trajectories"]}
    methods = {item["engine"]: item for item in profile["methods"]}
    attempts = results["attempts"]
    if {(item["engine"], int(item["task_id"])) for item in attempts} != {
        (engine, task_id) for engine in methods for task_id in tasks
    }:
        raise ValueError("report requires the complete participant-task matrix")

    action_by_engine: dict[str, list[float]] = defaultdict(list)
    action_by_name: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for item in results["physical_executions"]:
        key = (str(item["engine"]), int(item["task_id"]))
        trace = _load_artifact((item.get("evidence") or {}).get("trace")) or []
        for step in trace:
            value = step.get("duration_ms")
            if isinstance(value, (int, float)):
                action_by_engine[key[0]].append(float(value))
                action = str((step.get("arguments") or {}).get("action") or "unknown")
                action_by_name[action][key[0]].append(float(value))

    passing_tasks = {
        task_id
        for task_id in tasks
        if all(
            next(
                item for item in attempts if item["engine"] == engine and item["task_id"] == task_id
            )["status"]
            == "pass"
            for engine in methods
        )
    }
    method_rows = []
    for engine, method in methods.items():
        rows = [item for item in attempts if item["engine"] == engine]
        passes = sum(item["status"] == "pass" for item in rows)
        duration = _stat(
            [float(item["duration_ms"]) for item in rows if item["task_id"] in passing_tasks]
        )
        action_latency = _stat(action_by_engine[engine])
        comparable_rows = [item for item in rows if item["task_id"] in passing_tasks]
        cpu = _stat(
            [float((item.get("resource") or {}).get("cpu_ms") or 0) for item in comparable_rows]
        )
        memory = _stat(
            [
                float((item.get("resource") or {}).get("memory_mean_bytes") or 0)
                for item in comparable_rows
            ]
        )
        method_rows.append(
            {
                "id": method["id"],
                "engine": engine,
                "label": method["label"],
                "passed": passes,
                "total": len(rows),
                "pass_rate": passes / len(rows),
                "duration": duration,
                "duration_mean": duration["mean"],
                "action_latency": action_latency,
                "action_mean": action_latency["mean"],
                "cpu": cpu,
                "cpu_mean": cpu["mean"],
                "memory": memory,
                "memory_mean": memory["mean"],
                "physical_executions": len(
                    [item for item in results["physical_executions"] if item["engine"] == engine]
                ),
            }
        )

    public_rows = []
    for attempt in attempts:
        key = (attempt["engine"], int(attempt["task_id"]))
        task = tasks[key[1]]
        review = reviews.get(key)
        evidence = _public_evidence(attempt, review, output)
        failure = evidence["programmatic_failure"]
        cause = review["cause_group"] if review else {}
        row = {
            "key": f"{key[0]}-{key[1]}",
            "engine": key[0],
            "browser": methods[key[0]]["label"],
            "task_id": key[1],
            "intent": task["task"]["intent"],
            "sites": task["task"].get("sites") or [],
            "status": attempt["status"],
            "completed_actions": attempt["completed_action_count"],
            "action_count": attempt["action_count"],
            "failure_stage": failure["stage"],
            "program_error": failure["message"],
            "cause_zh": cause.get("label_zh", ""),
            "cause_en": cause.get("label_en", ""),
            "reason_zh": review.get("reason_zh", "") if review else "",
            "reason_en": review.get("reason_en", "") if review else "",
        }
        public_rows.append(row)
        for lang in ("zh", "en"):
            target = output / ("result" if lang == "zh" else "en/result") / f"{row['key']}.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                _detail_page(lang, attempt, task, methods[key[0]], evidence), encoding="utf-8"
            )

    _write_json(output / "data/results.json", public_rows)
    _write_json(output / "data/metrics.json", {"methods": method_rows, "passing_intersection": len(passing_tasks)})

    source_counts: dict[str, int] = defaultdict(int)
    for task in tasks.values():
        source_counts[str((task.get("source") or {}).get("kind") or "unknown")] += 1
    browser_use_source_count = source_counts["authoritative_successful_browser_use_execution"]
    repaired_source_count = source_counts["expert_repair_spec"]
    reference_engine = str((profile.get("qualification") or {}).get("reference_engine") or "")
    reference_method = methods.get(reference_engine)
    reference_row = next(
        (item for item in method_rows if item["engine"] == reference_engine),
        None,
    )
    if reference_method is None or reference_row is None:
        raise ValueError("profile qualification reference engine must be a registered participant")
    reference_label = _e(reference_method["label"])
    reviewed_failure_count = len(reviews)

    for lang in ("zh", "en"):
        zh = lang == "zh"
        root = output if zh else output / "en"
        root.mkdir(exist_ok=True)
        action_headers = ["动作" if zh else "Action", *[item["label"] for item in methods.values()]]
        action_rows = []
        for action in sorted(action_by_name):
            action_metrics = {
                engine: _stat(action_by_name[action][engine]) for engine in methods
            }
            action_winners = {
                stat: _unique_winners(
                    [
                        {"id": methods[engine]["id"], "value": metric[stat]}
                        for engine, metric in action_metrics.items()
                    ],
                    "value",
                    higher=False,
                )
                for stat in ("mean", "p50", "p95")
            }
            cells = []
            for engine in methods:
                metric = action_metrics[engine]
                text = _metric_lines(
                    metric,
                    (
                        lambda value, is_zh=zh: _format_ms(value)
                        if is_zh
                        else ("—" if value is None else f"{value / 1000:.1f} s")
                    ),
                    methods[engine]["id"],
                    action_winners,
                    lang=lang,
                )
                calls_label = "次调用" if zh else "calls"
                cells.append(f"<td>{text}<br><small>{metric['n']} {calls_label}</small></td>")
            action_rows.append(f'<tr><td><code>{_e(action)}</code></td>{"".join(cells)}</tr>')
        action_table = f'<div class="table-scroll"><table><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in action_headers)}</tr></thead><tbody>{"".join(action_rows)}</tbody></table></div>'
        method_cards = []
        for row in method_rows:
            failed_count = row["total"] - row["passed"]
            if failed_count:
                note = (
                    f"{failed_count} 条最终失败已逐条复核。"
                    if zh
                    else f"{failed_count} final failures were reviewed individually."
                )
            else:
                note = (
                    "固定轨迹与验收合同全部通过。"
                    if zh
                    else "Every fixed trajectory and acceptance contract passed."
                )
            method_cards.append(
                f'<article><span>{"浏览器结果" if zh else "Browser result"}</span>'
                f'<strong>{_e(row["label"])} {row["passed"]}/{row["total"]}</strong>'
                f"<p>{note}</p></article>"
            )
        task_count = len(tasks)
        shared_count = len(passing_tasks)
        eyebrow = (
            f"WebArena-Verified · {task_count} 个任务 · 模型调用 0 次"
            if zh
            else f"WebArena-Verified · {task_count} tasks · model calls 0"
        )
        summary_body = f'''<section class="hero"><p class="eyebrow">{eyebrow}</p><h1>{"浏览器轨迹重放评测" if zh else "Browser Trajectory Replay"}</h1><p>{"同一组经 Chromium 资格验证的固定 BrowserTool 轨迹，依次在各注册浏览器中执行。Agent 与模型不参与测试。" if zh else "The same Chromium-qualified BrowserTool trajectories run on every registered browser. No Agent or model participates in the benchmark."}</p></section><section><h2>{"核心指标" if zh else "Core metrics"}</h2><p class="callout">{f"正确率覆盖全部 {task_count} 题；端到端耗时、CPU 和内存只比较所有浏览器共同通过的 {shared_count} 题，避免提前失败缩短工作量。单次动作统计包含重试中的实际调用；调用数不判优。" if zh else f"Correctness covers all {task_count} tasks. End-to-end time, CPU, and memory use the {shared_count} tasks passed by every browser so early failure cannot reduce workload. Per-action latency includes actual retry calls; call counts are not ranked."}</p>{_metrics_table(method_rows, lang)}</section><section><h2>{"结论" if zh else "Findings"}</h2><div class="summary-grid">{"".join(method_cards)}<article><span>{"执行隔离" if zh else "Execution isolation"}</span><strong>{"串行" if zh else "Serial"}</strong><p>{"每题重置；方法之间销毁浏览器和站点。" if zh else "Reset per task; browser and sites torn down between methods."}</p></article></div></section><section><h2>{"浏览器动作耗时" if zh else "Browser action latency"}</h2><p>{"每格给出均值、P50、P95 和调用次数；耗时在所有注册浏览器间判优，调用次数只反映工作量。" if zh else "Each cell shows mean, P50, P95, and call count. Latency is ranked across every registered browser; counts only describe workload."}</p>{action_table}</section>'''
        (root / "index.html").write_text(_shell(lang, "index", "浏览器轨迹重放" if zh else "Browser Trajectory Replay", summary_body), encoding="utf-8")

        headers = ["Task", "Site", *[item["label"] for item in methods.values()]]
        matrix_rows = []
        for task_id, task in tasks.items():
            values = []
            for engine in methods:
                row = next(item for item in public_rows if item["engine"] == engine and item["task_id"] == task_id)
                label = ("通过" if row["status"] == "pass" else "失败") if zh else row["status"].title()
                values.append(f'<a class="status-{row["status"]}" href="result/{row["key"]}.html">{label}</a>')
            matrix_rows.append(f'<tr><td>{task_id}</td><td>{_e(", ".join(task["task"].get("sites") or []))}</td>{"".join(f"<td>{v}</td>" for v in values)}</tr>')
        dataset_body = f'<section class="hero"><h1>{f"{task_count} 条标准轨迹" if zh else f"{task_count} qualified trajectories"}</h1><p>{f"每题一条固定操作轨迹。{browser_use_source_count} 条来自干净的 Browser Use 成功执行，{repaired_source_count} 条经修复后重新在 {reference_label} 验证。" if zh else f"One fixed trajectory per task: {browser_use_source_count} clean Browser Use successes and {repaired_source_count} repaired trajectories re-qualified on {reference_label}."}</p></section><section><h2>{"逐题结果矩阵" if zh else "Per-task result matrix"}</h2><div class="table-scroll"><table><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in headers)}</tr></thead><tbody>{"".join(matrix_rows)}</tbody></table></div></section>'
        (root / "dataset.html").write_text(_shell(lang, "dataset", "评测集" if zh else "Dataset", dataset_body), encoding="utf-8")

        result_rows = []
        for row in public_rows:
            cause = row["cause_zh"] if zh else row["cause_en"]
            reason = row["reason_zh"] if zh else row["reason_en"]
            status = ("通过" if row["status"] == "pass" else "失败") if zh else row["status"].title()
            result_rows.append(f'<tr data-key="{row["key"]}" data-engine="{_e(row["engine"])}" data-status="{row["status"]}" data-site="{_e(row["sites"][0] if row["sites"] else "")}"><td><a href="result/{row["key"]}.html">{_e(row["browser"])} · {row["task_id"]}</a></td><td>{_e(row["sites"][0] if row["sites"] else "")}</td><td class="status-{row["status"]}">{status}</td><td>{row["completed_actions"]}/{row["action_count"]}</td><td>{_e(row["failure_stage"])}</td><td>{_e(row["program_error"])}</td><td><strong>{_e(cause)}</strong><br>{_e(reason)}</td></tr>')
        browser_options = "".join(f'<option value="{_e(key)}">{_e(item["label"])}</option>' for key, item in methods.items())
        results_body = f'''<section class="hero"><h1>{"结果与失败诊断" if zh else "Results and failure diagnosis"}</h1><p>{"默认显示失败。点击任一行先看诊断弹窗，再进入详情页查看轨迹、日志和最后页面。" if zh else "Failures are shown by default. Open a row for the diagnosis dialog, then continue to the detail page for trajectory, logs, and last page."}</p></section><section><div class="filters"><input data-filter="q" placeholder="{"搜索任务或错误" if zh else "Search task or error"}"><select data-filter="engine"><option value="">{"全部浏览器" if zh else "All browsers"}</option>{browser_options}</select><select data-filter="status"><option value="fail">{"失败" if zh else "Fail"}</option><option value="pass">{"通过" if zh else "Pass"}</option><option value="">{"全部结果" if zh else "All results"}</option></select></div><div class="table-scroll inventory" data-results><table><thead><tr><th>{"结果" if zh else "Result"}</th><th>{"站点" if zh else "Site"}</th><th>{"判定" if zh else "Verdict"}</th><th>{"动作" if zh else "Actions"}</th><th>{"失败阶段" if zh else "Failure stage"}</th><th>{"程序报错" if zh else "Program error"}</th><th>{"复核根因" if zh else "Reviewed root cause"}</th></tr></thead><tbody>{''.join(result_rows)}</tbody></table></div></section><dialog id="diagnosis-dialog"><div class="dialog-head"><strong data-dialog-title></strong><button type="button" aria-label="{"关闭" if zh else "Close"}">×</button></div><div class="dialog-body"><h3>{"失败阶段" if zh else "Failure stage"}</h3><p data-dialog-stage></p><h3>{"程序报错" if zh else "Program error"}</h3><pre data-dialog-error></pre><h3>{"复核根因" if zh else "Reviewed root cause"}</h3><p data-dialog-cause></p><a data-dialog-link>{"打开完整详情" if zh else "Open full detail"}</a></div></dialog>'''
        (root / "results.html").write_text(_shell(lang, "results", "结果与诊断" if zh else "Results", results_body), encoding="utf-8")

        flow_labels = (
            ("重放器", "浏览器容器", "本地站点", "逐题重置", "证据采集", "官方验收", "完全销毁")
            if zh
            else ("Replay runner", "Browser container", "Local sites", "Per-task reset", "Evidence", "Official evaluator", "Full teardown")
        )
        fallback = '<div class="flow-fallback" data-topology-fallback>' + "".join(
            f'<div class="flow-node"><strong>{index}</strong>{_e(label)}</div>'
            + ('<div class="flow-arrow" aria-hidden="true">→</div>' if index < len(flow_labels) else "")
            for index, label in enumerate(flow_labels, 1)
        ) + "</div>"
        topology = '''<pre class="mermaid" hidden>flowchart LR
  R[Replay runner] -->|one method at a time| B[Browser container]
  B -->|gateway 192.168.5.2| S[Self-hosted WebArena sites]
  S --> D[(Per-task reset data)]
  B --> E[Trace · HAR · resource samples]
  E --> V[Official evaluator]
  V --> X[Evidence-bound result]
  X --> T[Teardown browser and sites]
  T --> R</pre>'''
        environment_body = f'''<section class="hero"><h1>{"环境与评测流程" if zh else "Environment and evaluation flow"}</h1><p>{"192.168.5.2 是 Colima 虚拟机中的宿主网关：浏览器容器通过它访问运行在同一台 Mac 虚拟化环境里的 WebArena 站点。它不是公网测试网站。" if zh else "192.168.5.2 is the host gateway inside the Colima VM. Browser containers use it to reach the WebArena sites running in the same Mac virtualization environment; it is not a public test website."}</p></section><section><h2>{"执行拓扑" if zh else "Execution topology"}</h2>{fallback}{topology}</section><section><h2>{"隔离规则" if zh else "Isolation rules"}</h2><div class="summary-grid"><article><strong>1</strong><p>{"一套固定端口站点环境，因此只允许一个方法运行。" if zh else "One fixed-port site stack permits only one running method."}</p></article><article><strong>{task_count}</strong><p>{"一个浏览器完整跑完全部任务，每题前恢复官方夹具。" if zh else "A browser completes all tasks; official fixtures reset before each task."}</p></article><article><strong>0</strong><p>{"没有 Agent 决策和模型请求；输入是冻结的 BrowserTool 动作。" if zh else "No Agent decisions or model calls; inputs are frozen BrowserTool actions."}</p></article></div></section><script type="module">import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';const node=document.querySelector('.mermaid');try{{mermaid.initialize({{startOnLoad:false,theme:document.documentElement.dataset.theme==='dark'?'dark':'neutral'}});const rendered=await mermaid.render('replay-topology',node.textContent);node.innerHTML=rendered.svg;node.hidden=false;document.querySelector('[data-topology-fallback]').hidden=true}}catch(error){{node.hidden=true}}</script>'''
        (root / "environment.html").write_text(_shell(lang, "environment", "环境与方法" if zh else "Environment", environment_body), encoding="utf-8")

        review_body = f'''<section class="hero"><h1>{"核验记录" if zh else "Review history"}</h1><p>{"记录影响当前数据集与结论的核验轮次；逐题证据位于结果详情页。" if zh else "Review rounds that changed the current dataset or conclusions. Per-result evidence remains on result pages."}</p></section><section><div class="summary-grid"><article><span>2026-09-15</span><strong>{f"{task_count} 条轨迹资格核验" if zh else f"{task_count}-trajectory qualification"}</strong><p>{f"修复已确认的非浏览器问题，重新验证后 {reference_label} {reference_row['passed']}/{reference_row['total']}。" if zh else f"Confirmed non-browser defects were repaired; {reference_label} then qualified {reference_row['passed']}/{reference_row['total']}."}</p></article><article><span>2026-09-15</span><strong>{f"{reviewed_failure_count} 条失败根因复核" if zh else f"{reviewed_failure_count}-failure root-cause review"}</strong><p>{f"自动失败位置与复核根因分离；{reviewed_failure_count}/{reviewed_failure_count} 结果绑定当前证据哈希，无未解决项。" if zh else f"Programmatic locations remain separate from reviewed causes; {reviewed_failure_count}/{reviewed_failure_count} are bound to the current evidence hash with no unresolved items."}</p></article></div></section>'''
        (root / "review.html").write_text(_shell(lang, "review", "核验记录" if zh else "Review history", review_body), encoding="utf-8")

        viewer_body = f'<section class="hero"><h1>{"证据查看器" if zh else "Evidence viewer"}</h1><p>{"网页直接加载选中的净化证据；也可下载原始 JSON。" if zh else "Loads the selected sanitized evidence directly in the page; raw JSON remains downloadable."}</p></section><section class="viewer"><pre data-evidence-viewer></pre></section>'
        (root / "evidence-viewer.html").write_text(_shell(lang, "results", "证据查看器" if zh else "Evidence viewer", viewer_body), encoding="utf-8")

    manifest = {
        "schema": "lex_webarena_trajectory_replay_report/1",
        "results_sha256": _sha256(Path(results_path)),
        "dataset_sha256": _sha256(Path(dataset_path)),
        "review_sha256": _sha256(Path(review_path)),
        "task_count": len(tasks),
        "participant_count": len(methods),
        "participants": list(methods),
        "logical_results": len(public_rows),
        "failed_results": sum(row["status"] != "pass" for row in public_rows),
        "reviewed_failures": len(reviews),
        "routes": 12 + 2 * len(public_rows),
    }
    _write_json(output / "report-manifest.json", manifest)
    return manifest
