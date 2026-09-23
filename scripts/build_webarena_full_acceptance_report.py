"""Build the Chinese 812-case Chromium replay report from audited evidence."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import statistics
from collections import Counter
from pathlib import Path

from harness.trajectory_replay.public_webarena.suite import load_suite
from harness.trajectory_replay.remote_runtime import load_remote_sites, remote_benchmark


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


SITE_LABELS = {
    "gitlab": "GitLab", "map": "地图", "reddit": "Reddit",
    "shopping": "购物网站", "shopping_admin": "购物后台", "wikipedia": "维基百科",
    "wikipedia_runtime": "维基运行容器",
}


def _sanitize(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items()
                if str(key).lower() not in {"cookie", "cookies", "authorization", "password", "secret", "token"}}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"/Users/[^\s\"']+", "[本地路径]", value)
        value = re.sub(r"(?i)(authorization|set-cookie|cookie):\s*[^\r\n]+", r"\1: [已隐藏]", value)
        return value
    return value


STYLE = """
:root{color-scheme:light dark;font:16px/1.6 system-ui,sans-serif}
body{max-width:1120px;margin:auto;padding:22px;color:#14212b;background:#f7f9fb}
nav{display:flex;gap:18px;flex-wrap:wrap;margin-bottom:24px}a{color:#075fb5}a:focus-visible,button:focus-visible,input:focus-visible{outline:3px solid #f29221}
h1{line-height:1.25}h2{margin-top:34px}.lead{font-size:1.15rem}.muted{color:#526273}
.panel{background:white;border:1px solid #d9e2e8;border-radius:12px;padding:20px;margin:16px 0}
.scroll{max-height:580px;overflow:auto;border:1px solid #d9e2e8;border-radius:10px}
table{border-collapse:collapse;width:100%;min-width:650px;background:white}th,td{text-align:left;vertical-align:top;border-bottom:1px solid #e3e9ee;padding:9px 12px}th{position:sticky;top:0;background:#edf3f7;z-index:1}
.ok{color:#08734a;font-weight:700}.bad{color:#b7352c;font-weight:700}code,pre{font-family:ui-monospace,monospace;overflow-wrap:anywhere}pre{white-space:pre-wrap;max-height:550px;overflow:auto;background:#f0f4f7;padding:14px;border-radius:8px}
input{font:inherit;padding:8px 12px;width:min(100%,420px);border:1px solid #8194a3;border-radius:7px}button{font:inherit;padding:7px 12px}details{margin:12px 0}summary{cursor:pointer;font-weight:650}
img{max-width:100%;height:auto}footer{margin:34px 0;color:#526273;font-size:.9rem}
@media(max-width:650px){body{padding:12px}.panel{padding:14px}.scroll{max-height:65vh}th,td{padding:7px 9px}}
@media(prefers-color-scheme:dark){body{color:#edf3f7;background:#111c25}.panel,table{background:#1b2b36;border-color:#37505f}th{background:#283b48}td{border-color:#37505f}a{color:#85c1ff}.muted,footer{color:#b2c0c9}pre{background:#13232e}input{color:#edf3f7;background:#1b2b36;border-color:#8194a3}}
"""


def _page(title: str, body: str, *, prefix: str = "") -> str:
    nav = (f'<nav><a href="{prefix}index.html">结果</a>'
           f'<a href="{prefix}dataset.html">数据集</a>'
           f'<a href="{prefix}method.html">方法与边界</a></nav>')
    return ("<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{_e(title)}</title><style>{STYLE}</style><body>{nav}{body}"
            '<footer>WebArena-Verified 全量轨迹重放 · Chromium</footer></body></html>')


def _percentile(values: list[float], p: float) -> float:
    values = sorted(values)
    index = (len(values) - 1) * p
    lo = int(index)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (index - lo)


def _run_index(ledger: dict, root: Path) -> dict[str, Path]:
    wanted = {row["progress_sha256"]: row for row in ledger["runs"]}
    found: dict[str, Path] = {}
    for progress in root.glob("*/progress.jsonl"):
        if progress.is_symlink() or progress.parent.is_symlink():
            continue
        digest = _hash(progress)
        if digest in wanted:
            inputs = progress.parent / "inputs.json"
            if _hash(inputs) != wanted[digest]["inputs_sha256"]:
                raise ValueError("run inputs changed after acceptance audit")
            found[digest] = progress.parent
    if set(found) != set(wanted):
        raise ValueError("audited run evidence is missing")
    return found


def _failure_reason(task_id: int) -> str:
    groups = {
        "页面跳转后的定位或等待": {66, 349, 548, 549, 550, 556, 557, 558, 559, 560, 561},
        "站点状态与任务相互影响": {496, 497, 500, 525, 527, 666, 678, 679},
        "目标定位或过滤条件错误": {121, 346, 658},
        "Magento 表单缺少必填字段": {700, 701, 703},
        "零结果网格的轨迹观察不兼容": {790},
    }
    matches = [name for name, ids in groups.items() if task_id in ids]
    if len(matches) != 1:
        raise ValueError(f"failure cause for {task_id} is not uniquely reviewed")
    return matches[0]


def build(ledger_path: Path, run_root: Path, site_map: Path, catalog_path: Path,
          suite_path: Path, output: Path) -> dict:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(output)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if ledger.get("schema") != "lex_webarena_full_acceptance_ledger/1" or ledger["counts"]["pass"] != 812 or ledger["counts"]["unresolved"] != 0:
        raise ValueError("full acceptance ledger is incomplete")
    scope_path = Path("evaluation/trajectory-replay/webarena-verified-full/scope.json")
    environment_path = Path("evaluation/trajectory-replay/webarena-verified-full/environment.json")
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    if _hash(scope_path) != ledger["scope_sha256"] or scope["task_ids"] != list(range(812)):
        raise ValueError("official scope changed")
    runs = _run_index(ledger, run_root)
    benchmark = remote_benchmark(load_remote_sites(site_map))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    suite = load_suite(suite_path)
    if (catalog.get("schema") != "lex_webarena_full_replay_catalog/1"
            or catalog.get("portable_suite_sha256") != _hash(suite_path)
            or [row["task_id"] for row in catalog["rows"]] != list(range(812))):
        raise ValueError("final trajectory catalog differs from the full scope")
    suite_tasks = {int(task.task_id.removeprefix("webarena-")): task for task in suite.tasks}
    output.mkdir(parents=True, exist_ok=True)
    (output / "case").mkdir()
    (output / "evidence").mkdir()
    (output / "en").mkdir()
    site_counts: Counter[str] = Counter()
    site_groups: Counter[str] = Counter()
    timings: list[float] = []
    all_timing = 0.0
    failures: list[int] = []
    result_rows: list[str] = []
    dataset_rows: list[str] = []
    contracts: list[dict] = []
    for result in ledger["rows"]:
        task_id = result["task_id"]
        task = benchmark.get_task(task_id)
        sites = sorted(site.value for site in task.sites)
        site_counts.update(sites)
        site_groups[" + ".join(sites)] += 1
        task_label = "、".join(SITE_LABELS[site] for site in sites)
        catalog_row = catalog["rows"][task_id]
        source_actions = (list(suite_tasks[task_id].tool_calls)
                          if catalog_row["mode"] == "portable" else catalog_row["source_actions"])
        contracts.append(_sanitize({
            "task_id": task_id, "intent": task.intent, "sites": sites,
            "official_evaluators": [item.model_dump(mode="json") for item in task.eval],
            "trajectory_source": catalog_row["source_kind"],
            "fixed_actions": source_actions,
        }))
        attempts = result["attempts"]
        if attempts[0]["status"] != "pass":
            failures.append(task_id)
        rendered_attempts = []
        for attempt in attempts:
            directory = runs[attempt["progress_sha256"]]
            progress = next(row for row in (json.loads(line) for line in (directory / "progress.jsonl").read_text(encoding="utf-8").splitlines()) if row["task_id"] == task_id)
            case_dir = directory / f"task-{task_id}"
            trace_path = case_dir / "trace.json"
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            if _hash(trace_path) != attempt["evidence_sha256"]["trace"]:
                raise ValueError(f"task {task_id} trace changed")
            item = {"status": attempt["status"], "score": attempt["score"],
                    "duration_ms": progress.get("duration_ms"), "run_hash": attempt["progress_sha256"],
                    "error": _sanitize(progress.get("error")), "trace": _sanitize(trace)}
            if "evaluation" in attempt["evidence_sha256"]:
                evaluation_path = case_dir / "evaluation.json"
                if _hash(evaluation_path) != attempt["evidence_sha256"]["evaluation"]:
                    raise ValueError(f"task {task_id} evaluation changed")
                evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
                if evaluation["webarena_verified_data_checksum"] != scope["official_dataset_sha256"]:
                    raise ValueError(f"task {task_id} official evaluator differs")
                if attempt["status"] == "pass" and float(evaluation["score"]) != 1:
                    raise ValueError(f"task {task_id} official pass differs")
                item["evaluation"] = _sanitize(evaluation)
            if "outputs" in attempt["evidence_sha256"]:
                outputs_path = case_dir / "outputs.json"
                if _hash(outputs_path) != attempt["evidence_sha256"]["outputs"]:
                    raise ValueError(f"task {task_id} outputs changed")
                item["outputs"] = _sanitize(json.loads(outputs_path.read_text(encoding="utf-8")))
            rendered_attempts.append(item)
            all_timing += float(progress.get("duration_ms") or 0)
        final = rendered_attempts[-1]
        timings.append(float(final["duration_ms"] or 0))
        evidence_file = f"evidence/{task_id:03d}.json"
        (output / evidence_file).write_text(json.dumps({"task_id": task_id, "attempts": rendered_attempts}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        cause = _failure_reason(task_id) if task_id in failures else ""
        detail = (f'<h1>第 {task_id} 题 · Chromium 通过</h1><p class="lead">{_e(task.intent)}</p>'
                  f'<section class="panel" data-report-detail><h2>官方判分</h2><p class="ok">通过 · score 1.0</p>'
                  f'<p>网站：{_e(task_label)}；最终执行耗时：{float(final["duration_ms"] or 0)/1000:.2f} 秒；物理执行：{len(attempts)} 次。</p>'
                  f'<p>官方数据校验：<code>{_e(scope["official_dataset_sha256"])}</code></p></section>')
        if cause:
            first = next(item for item in rendered_attempts if item["status"] != "pass")
            first_error = first.get("error") or (first.get("evaluation") or {}).get("error_msg")
            exact = str(first_error or json.dumps(first.get("evaluation") or {}, ensure_ascii=False))
            detail += (f'<section class="panel"><h2>首轮失败与处理</h2><p>归因：{_e(cause)}。</p>'
                       f'<details><summary>查看原始错误</summary><pre>{_e(exact)}</pre></details>'
                       f'<p>后续补跑通过；原失败仍保留在证据链中。</p></section>')
        if task_id == 790:
            detail += ('<section class="panel"><h2>验证边界</h2><p>备用站点运行时目标差评已不存在。官方 evaluator 确认目标状态满足，'
                       '但这次通过不能证明该轨迹能从仍有差评的初始状态执行删除。</p></section>')
        detail += (f'<section class="panel"><h2>执行证据</h2><p><a href="../evidence.html?task={task_id:03d}">查看全部 BrowserTool 响应与官方评测结果</a>'
                   f' · <a href="../{evidence_file}">下载 JSON</a></p></section>')
        (output / "case" / f"{task_id:03d}.html").write_text(_page(f"第 {task_id} 题结果", detail, prefix="../"), encoding="utf-8")
        result_rows.append(f'<tr data-search="{_e(str(task_id)+" "+task_label+" "+task.intent)}"><td><a href="case/{task_id:03d}.html">{task_id}</a></td><td>{_e(task_label)}</td><td class="ok">通过</td><td>{len(attempts)}</td><td>{float(final["duration_ms"] or 0)/1000:.2f}</td></tr>')
        evaluator_names = "、".join(item.evaluator for item in task.eval)
        dataset_rows.append(f'<tr><td><a href="contract.html?task={task_id:03d}">{task_id}</a></td><td>{_e(task_label)}</td><td>{_e(task.intent)}</td><td>{_e(evaluator_names)}</td></tr>')
    if len(failures) != 26 or sum(site_groups.values()) != 812:
        raise ValueError("initial failure or site population changed")
    summary = (f'<h1>WebArena-Verified 全量轨迹重放</h1><p class="lead">Chromium：812 / 812 题获得一次官方 evaluator 通过（100%）。'
               f'按官方题号顺序的验收主轮首次通过 786 / 812；失败 26 题在定向修复后通过。</p>')
    summary += ('<section class="panel"><h2>核心结果</h2><div class="scroll"><table><thead><tr><th>浏览器</th><th>题数</th><th>通过</th><th>主轮首次通过</th><th>Mean 耗时</th><th>P50 耗时</th><th>P95 耗时</th></tr></thead><tbody>'
                f'<tr><td>Chromium</td><td>812</td><td>812</td><td>786</td><td>{statistics.mean(timings)/1000:.2f} 秒</td><td>{statistics.median(timings)/1000:.2f} 秒</td><td>{_percentile(timings,.95)/1000:.2f} 秒</td></tr></tbody></table></div>'
                '<p class="muted">耗时统计每题最终一次物理调用；Mean 为算术平均，P50 为中位数，P95 为第 95 百分位。'
                f'本次审计核对 {ledger["counts"]["physical_attempts"]} 次物理执行；其中 {ledger["counts"]["excluded_duplicate_attempts"]} 次重复执行不进入逐题结果。'
                f'其余 {sum(len(row["attempts"]) for row in ledger["rows"])} 次调用累计耗时 {all_timing/1000:.1f} 秒。早期探索和诊断调用不在此数内。</p></section>')
    summary += '<section class="panel"><h2>网站分布</h2><p>一题可涉及多个网站，所以下表网站计数之和大于 812。</p><div class="scroll"><table><thead><tr><th>网站</th><th>涉及题数</th></tr></thead><tbody>'
    summary += ''.join(f'<tr><td>{_e(SITE_LABELS[site])}</td><td>{count}</td></tr>' for site,count in sorted(site_counts.items()))
    summary += '</tbody></table></div></section>'
    summary += '<section class="panel"><h2>逐题结果</h2><label for="find">筛选题号、网站或任务内容</label><p><input id="find" type="search" aria-label="筛选逐题结果"></p><div class="scroll"><table id="results"><thead><tr><th>题号</th><th>网站</th><th>最终判分</th><th>选入执行次数</th><th>最终耗时（秒）</th></tr></thead><tbody>' + ''.join(result_rows) + '</tbody></table></div></section>'
    summary += '<script>const q=document.getElementById("find");q.addEventListener("input",()=>{for(const row of document.querySelectorAll("#results tbody tr"))row.hidden=!row.dataset.search.toLowerCase().includes(q.value.toLowerCase())})</script>'
    (output / "index.html").write_text(_page("全量轨迹重放结果", summary), encoding="utf-8")
    dataset = ('<h1>评测集：812 题</h1><p>任务、顺序与接受条件来自固定的 WebArena-Verified 官方数据。'
               '这里仅列输入，不列测得分数；实测结果见结果页。</p>'
               f'<p>官方数据 SHA-256：<code>{_e(scope["official_dataset_sha256"])}</code>。90 题采样集是独立 split。</p>'
               '<div class="scroll"><table><thead><tr><th>题号</th><th>网站</th><th>任务</th><th>官方检查类型</th></tr></thead><tbody>' + ''.join(dataset_rows) + '</tbody></table></div>')
    (output / "dataset.html").write_text(_page("评测集", dataset), encoding="utf-8")
    (output / "dataset-contracts.json").write_text(json.dumps({"schema": "lex_webarena_full_contracts/1", "tasks": contracts}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    contract_viewer = ('<h1>任务接受条件与固定轨迹</h1><p><a href="dataset.html">返回数据集</a></p>'
                       '<pre id="payload" role="region" aria-label="官方接受条件与固定轨迹">加载中…</pre>'
                       '<script>const id=Number(new URLSearchParams(location.search).get("task"));'
                       'if(Number.isInteger(id)&&id>=0&&id<812)fetch("dataset-contracts.json").then(r=>r.json()).then(x=>document.getElementById("payload").textContent=JSON.stringify(x.tasks[id],null,2)).catch(e=>document.getElementById("payload").textContent=String(e));'
                       'else document.getElementById("payload").textContent="无效的题号";</script>')
    (output / "contract.html").write_text(_page("任务接受条件与固定轨迹", contract_viewer), encoding="utf-8")
    method = ('<h1>方法与适用边界</h1><section class="panel"><h2>执行链</h2>'
              '<pre>官方 812 题与 evaluator → 固定 BrowserTool 动作 → Chromium 执行 → 官方 evaluator → 不通过题定向修复 → 首次通过锁定</pre>'
              '<p>按题号 0–811 执行主轮；每题仅在失败后补跑。浏览器引擎为 Chromium，二进制和每次运行输入按 SHA-256 绑定。六个网站部署在 Rainbond。</p>'
              '<p>可执行候选轨迹并不等于官方人工 gold 轨迹。仓库另有 179 条公开人工 Playwright 录制；其原始录制本身不保证官方判分通过。</p></section>'
              '<section class="panel"><h2>判分与审计</h2><p>每题最后一次选入调用必须由原版官方 evaluator 给出 score 1.0；失败调用仍保留。'
              '评测答案和通过标准未因浏览器表现而放松。主轮 26 题首次失败均有补跑证据；通过题不因诊断再测。</p>'
              f'<p>验收账本 SHA-256：<code>{_hash(ledger_path)}</code>；官方 scope SHA-256：<code>{_hash(scope_path)}</code>。</p>'
              '<p>此次展示一套 Chromium 配置，不据此推断 Moli、Firefox 或 WebKit 的表现。站点会话与数据库在题间有延续，部分定向补跑使用备用部署；通过率是这些固定轨迹在该部署状态和官方 evaluator 下“至少通过一次”的记录，不能解释为从任意全新状态必然成功。</p>'
              '<p>轮前 Rainbond 收据记录了组件更新与站点就绪，没有数据库恢复到初始种子的证据。因此这轮结果不能称为数据重置后的验收。</p>'
              '<p>第 790 题目标差评在备用站点已不存在，详情页单独标注该边界。</p></section>')
    method += ('<section class="panel"><h2>部署与镜像</h2>'
               f'<p>Rainbond 主应用 {environment["deployment"]["primary_app_id"]}，备用应用 {environment["deployment"]["repair_app_id"]}；'
               f'地图应用 {environment["deployment"]["shared_map_app_id"]}，维基应用 {environment["deployment"]["shared_wikipedia_app_id"]}。'
               '地图和维基共用只读部署；其余站点在主应用与备用应用各有一份。下表是部署组件读取的镜像摘要，维基条目为运行容器镜像。</p>'
               '<div class="scroll"><table><thead><tr><th>组件</th><th>镜像摘要</th></tr></thead><tbody>'
               + ''.join(f'<tr><td>{_e(SITE_LABELS[site])}</td><td><code>{_e(digest)}</code></td></tr>' for site,digest in sorted(environment["images"].items()))
               + '</tbody></table></div></section>')
    (output / "method.html").write_text(_page("方法与边界", method), encoding="utf-8")
    viewer = ('<h1>证据查看器</h1><p><a id="back" href="index.html">返回结果</a></p><pre id="payload" role="region" aria-label="完整执行证据">加载中…</pre>'
              '<script>const id=new URLSearchParams(location.search).get("task");const ok=/^\\d{3}$/.test(id||"");'
              'if(ok){document.getElementById("back").href="case/"+id+".html";fetch("evidence/"+id+".json").then(r=>{if(!r.ok)throw Error("证据不存在");return r.json()}).then(x=>document.getElementById("payload").textContent=JSON.stringify(x,null,2)).catch(e=>document.getElementById("payload").textContent=String(e))}'
              'else document.getElementById("payload").textContent="无效的题号";</script>')
    (output / "evidence.html").write_text(_page("证据查看器", viewer), encoding="utf-8")
    (output / "en" / "index.html").write_text(_page("English edition not generated", '<h1>English edition not generated</h1><p>本次仅生成中文版。<a href="../index.html">返回中文报告</a></p>', prefix="../"), encoding="utf-8")
    manifest = {"schema": "lex_webarena_full_acceptance_report/1", "generated_languages": ["zh"],
                "ledger_sha256": _hash(ledger_path), "scope_sha256": _hash(scope_path),
                "environment_sha256": _hash(environment_path),
                "catalog_sha256": _hash(catalog_path), "portable_suite_sha256": _hash(suite_path),
                "task_count": 812, "pass": 812, "initial_failures": failures,
                "site_counts": dict(sorted(site_counts.items())), "site_group_counts": dict(sorted(site_groups.items())),
                "evaluator_checksum": rendered_attempts[-1]["evaluation"]["webarena_verified_evaluator_checksum"]}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--portable-suite", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.ledger, args.run_root, args.site_map,
                           args.catalog, args.portable_suite, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
