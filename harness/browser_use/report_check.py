"""Validate the autonomous browser-use report and its measured evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup
from lexbench_webfetch.paths import contained_path

from harness.browser_use.failure_cause_review import (
    load_failure_cause_review,
    validate_failure_cause_review,
)
from harness.browser_use.failure_cause_review import (
    review_path as failure_review_path,
)
from harness.browser_use.failure_cause_review import (
    sha256 as file_sha256,
)
from harness.browser_use.publication import evidence_metadata
from harness.browser_use.report import (
    PUBLIC_EXECUTION_ARTIFACTS,
    SITE_LABELS,
    _attempt_id,
    _configure_engine_labels,
    _configure_models,
    _load_evidence_bundles,
    _logical_results,
    _public_attempt,
    _public_execution,
)
from harness.browser_use.selection import load_pilot, report_selection_path
from harness.record_io import load_jsonl
from harness.report_languages import check_language_contract, generated_languages
from harness.reporting.assets import bound_json_asset
from harness.reporting.presentation import visible_text as _visible_text
from harness.reporting.templates import template_identity
from harness.reporting.versions import check_build_version
from harness.web_fetch.safety import reject_evidence_symlinks

FORBIDDEN_READER_TERMS = (
    "模型内完整配对",
    "不等于共同根因",
    "仅基准通过",
    "仅对比通过",
)


def _check_reader_facing_ux(report_dir: Path, rendered_pages: dict[Path, str]) -> None:
    """Enforce stable, data-independent report presentation contracts."""

    for page, text in rendered_pages.items():
        term = next((term for term in FORBIDDEN_READER_TERMS if term in text), None)
        if term:
            raise ValueError(f"formal report contains unexplained internal term {term}: {page}")
    for homepage in (
        report_dir / "index.html",
        *([report_dir / "en/index.html"] if "en" in generated_languages(report_dir) else []),
    ):
        text = rendered_pages[homepage]
        if not re.search(r'<table\b[^>]*\bid=["\']home-core-metrics["\']', text, flags=re.I):
            raise ValueError(f"report home page is missing the core metrics table: {homepage}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_execution_artifacts(
    report_dir: Path,
    logical_rows: list[dict],
    public_results: list[dict],
    evidence_index: dict,
) -> None:
    """Verify that every physical execution has one safe, hash-bound public projection."""

    logical_by_id = {_attempt_id(row): row for row in logical_rows}
    public_by_id = {str(row.get("id")): row for row in public_results}
    indexed_rows = evidence_index.get("results") or []
    indexed_by_id = {str(row.get("result_id")): row for row in indexed_rows}
    expected_ids = set(logical_by_id)
    if len(public_by_id) != len(public_results) or set(public_by_id) != expected_ids:
        raise ValueError("public result projection must identify every logical result exactly once")
    if len(indexed_by_id) != len(indexed_rows) or set(indexed_by_id) != expected_ids:
        raise ValueError("public evidence index must cover every logical result exactly once")
    if evidence_index.get("schema") != "lex_browser_use_public_evidence_index/4":
        raise ValueError("public evidence index schema does not describe physical executions")

    expected_files: set[Path] = set()
    for result_id, logical in logical_by_id.items():
        sources = logical.get("_source_executions") or [logical]
        authority = int(logical["attempt"])
        public = public_by_id[result_id]
        indexed = indexed_by_id[result_id]
        detail_text = (report_dir / "results" / f"{result_id}.html").read_text(encoding="utf-8")
        if f"包含 <strong>{len(sources)}</strong> 次物理执行" not in detail_text:
            raise ValueError(f"result page omits physical execution count: {result_id}")
        expected_public = _public_attempt(logical)
        for key in (
            "execution_count",
            "authoritative_execution",
            "score",
            "duration_ms",
            "action_count",
            "browser_tool_duration_ms",
            "browser_tool_duration_by_action_ms",
            "browser_tool_count_by_action",
            "model_usage",
        ):
            if public.get(key) != expected_public.get(key):
                raise ValueError(
                    f"public logical aggregate cannot be recomputed: {result_id}/{key}"
                )
        for key in (
            "model",
            "engine",
            "task_id",
            "score",
            "execution_count",
            "authoritative_execution",
        ):
            if indexed.get(key) != public.get(key):
                raise ValueError(f"evidence index logical binding differs: {result_id}/{key}")
        if public.get("execution_count") != len(sources):
            raise ValueError(f"public result has wrong physical execution count: {result_id}")
        if public.get("authoritative_execution") != authority:
            raise ValueError(f"public result changed score authority: {result_id}")
        if indexed.get("execution_count") != len(sources):
            raise ValueError(f"evidence index has wrong physical execution count: {result_id}")
        if indexed.get("authoritative_execution") != authority:
            raise ValueError(f"evidence index changed score authority: {result_id}")
        executions = indexed.get("executions") or []
        if len(executions) != len(sources):
            raise ValueError(f"evidence index omitted a physical execution: {result_id}")
        indexed_by_number = {int(item.get("execution") or 0): item for item in executions}
        if len(indexed_by_number) != len(executions):
            raise ValueError(f"evidence index duplicates a physical execution: {result_id}")
        for source in sources:
            number = int(source["attempt"])
            indexed_execution = indexed_by_number.get(number)
            if indexed_execution is None:
                raise ValueError(f"evidence index omitted execution {number}: {result_id}")
            expected_measurements = _public_execution(source, authoritative=number == authority)
            measured = {key: indexed_execution.get(key) for key in expected_measurements}
            if measured != expected_measurements:
                raise ValueError(f"published execution measurements differ: {result_id}/{number}")
            artifacts = indexed_execution.get("artifacts") or {}
            if set(artifacts) != set(PUBLIC_EXECUTION_ARTIFACTS):
                raise ValueError(f"execution artifact inventory differs: {result_id}/{number}")
            for label, filename in PUBLIC_EXECUTION_ARTIFACTS.items():
                relative = Path("artifacts") / result_id / "executions" / str(number) / filename
                artifact = artifacts[label]
                if (
                    set(artifact) != {"path", "sha256"}
                    or artifact.get("path") != relative.as_posix()
                ):
                    raise ValueError(
                        f"unsafe or incorrect artifact link: {result_id}/{number}/{label}"
                    )
                path = report_dir / relative
                expected_files.add(path)
                if not path.is_file():
                    raise ValueError(f"missing physical execution artifact: {path}")
                if artifact.get("sha256") != _sha256(path):
                    raise ValueError(f"physical execution artifact hash differs: {path}")
                if (
                    f'href="{relative.as_posix()}"' not in detail_text
                    or str(artifact["sha256"]) not in detail_text
                ):
                    raise ValueError(
                        f"result page omits execution artifact link or hash: "
                        f"{result_id}/{number}/{label}"
                    )
            screenshots = indexed_execution.get("screenshots") or []
            if number != authority and screenshots:
                raise ValueError(
                    f"only the authoritative execution may publish frames: {result_id}"
                )
            for screenshot in screenshots:
                if (
                    set(screenshot)
                    != {
                        "path",
                        "sha256",
                        "label",
                        "source",
                        "width",
                        "height",
                    }
                    or screenshot.get("source") != "playwright-screencast-frame"
                ):
                    raise ValueError(f"trace frame lacks screencast provenance: {result_id}")
                relative = (
                    Path("artifacts")
                    / result_id
                    / "executions"
                    / str(number)
                    / str(screenshot.get("path") or "")
                )
                path = report_dir / relative
                if path.suffix.lower() not in {".jpeg", ".jpg", ".png"}:
                    raise ValueError(f"unsafe screenshot type: {relative}")
                expected_files.add(path)
                if not path.is_file() or screenshot.get("sha256") != _sha256(path):
                    raise ValueError(f"trace frame binding differs: {relative}")
                from PIL import Image

                with Image.open(path) as image:
                    dimensions = image.size
                if dimensions != (screenshot.get("width"), screenshot.get("height")):
                    raise ValueError(f"trace frame dimensions differ: {relative}")
                if dimensions[0] < 800 or dimensions[1] < 450:
                    raise ValueError(f"trace frame is not a full page image: {relative}")
                if relative.as_posix() not in detail_text:
                    raise ValueError(f"result page omits trace frame: {relative}")
            bundle_path = (
                report_dir
                / "artifacts"
                / result_id
                / "executions"
                / str(number)
                / PUBLIC_EXECUTION_ARTIFACTS["execution_evidence"]
            )
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            if (
                bundle.get("schema") != "lex_browser_use_public_execution_evidence/3"
                or bundle.get("result_id") != result_id
                or bundle.get("execution") != number
                or bundle.get("authoritative") != (number == authority)
                or bundle.get("measurements") != expected_measurements
            ):
                raise ValueError(f"execution evidence binding differs: {result_id}/{number}")
            for key in ("agent_response", "evaluation", "agent_events", "trajectory"):
                if key not in bundle:
                    raise ValueError(
                        f"execution evidence payload is incomplete: {result_id}/{number}/{key}"
                    )
            source_files = (bundle.get("source_evidence") or {}).get("files") or {}
            if source_files != (evidence_metadata(source).get("files") or {}):
                raise ValueError(f"source evidence hashes differ: {result_id}/{number}")
            missing_public_sources = {
                label
                for label in ("response", "evaluation", "events")
                if not (source_files.get(label) or {}).get("present")
            }
            if missing_public_sources:
                raise ValueError(
                    f"physical execution lacks publishable source evidence: "
                    f"{result_id}/{number}/{sorted(missing_public_sources)}"
                )
            for private_label in ("network_har", "agent_trace"):
                if (
                    private_label in source_files
                    and source_files[private_label].get("publication")
                    != "private_only_session_sensitive"
                ):
                    raise ValueError(f"private {private_label} was marked publishable")

    artifact_root = report_dir / "artifacts"
    actual_files = {path for path in artifact_root.rglob("*") if path.is_file()}
    if actual_files != expected_files:
        extra = sorted(str(path) for path in actual_files - expected_files)
        missing = sorted(str(path) for path in expected_files - actual_files)
        raise ValueError(f"execution artifact tree differs; extra={extra}, missing={missing}")
    forbidden_text = re.compile(
        r"(?:Password\.123|admin1234|test1234|hello1234|"
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+[A-Za-z0-9._~+/=-]{12,}|"
        r"\bsk-[A-Za-z0-9_-]{12,}|(?-i:/(?:Users|home|private|tmp|var|opt)/[^\s\"']+))",
        flags=re.I,
    )
    for path in actual_files:
        if path.suffix.lower() in {".har", ".zip"} or path.name in {
            "network.har",
            "agent_trace.zip",
        }:
            raise ValueError(f"private HAR or trace was copied into the public report: {path}")
        if path.suffix.lower() in {
            ".json",
            ".jsonl",
            ".html",
            ".css",
            ".js",
        } and forbidden_text.search(path.read_text(encoding="utf-8")):
            raise ValueError(f"published execution artifact contains sensitive text: {path}")


def _check_public_privacy(report_dir: Path) -> None:
    forbidden_text = re.compile(
        r"(?:Password\.123|admin1234|test1234|hello1234|"
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+[A-Za-z0-9._~+/=-]{12,}|"
        r"\bsk-[A-Za-z0-9_-]{12,}|(?-i:/(?:Users|home|private|tmp|var|opt)/[^\s\"']+))",
        flags=re.I,
    )
    text_suffixes = {".html", ".json", ".jsonl", ".js", ".css"}
    for path in report_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".har" or (
            "trace" in path.name.lower() and path.suffix.lower() == ".zip"
        ):
            raise ValueError(f"private HAR or trace was copied into the public report: {path}")
        if path.suffix.lower() in text_suffixes and forbidden_text.search(
            path.read_text(encoding="utf-8")
        ):
            raise ValueError(f"published report contains sensitive text: {path}")


def check_methodology_evidence(content: str, tasks: list[dict], environment: dict) -> None:
    """Require displayed deployment and evaluator facts to match the selected evidence."""

    soup = BeautifulSoup(content, "html.parser")
    expected = {
        "methodology-evaluators": Counter(
            rule.get("evaluator", "UnknownEvaluator")
            for task in tasks
            for rule in (task.get("contract") or {}).get("evaluation_contract", [])
        ),
        "methodology-sites": Counter(
            SITE_LABELS.get(site, site) for task in tasks for site in task.get("sites", [])
        ),
    }
    for table_id, counts in expected.items():
        table = soup.find("table", id=table_id)
        if table is None:
            raise ValueError("methodology is missing selected-evidence table: " + table_id)
        rows = [row.find_all("td") for row in table.select("tbody tr")]
        values = {
            cells[0].get_text(strip=True): cells[1].get_text(strip=True)
            for cells in rows
            if len(cells) >= 2
        }
        if len(rows) != len(counts) or values != {key: str(value) for key, value in counts.items()}:
            raise ValueError("methodology differs from selected evidence: " + table_id)
    visible = soup.get_text(" ", strip=True)
    for key in ("platform", "site_access_host"):
        if environment.get(key) and str(environment[key]) not in visible:
            raise ValueError("methodology is missing recorded environment: " + key)
    if soup.find(id="deployment-flow") is None:
        raise ValueError("methodology is missing the evaluation-flow diagram")


def check(
    report_dir: Path,
    evidence_dir: Path | list[Path],
    selection_path: Path | None,
    *,
    require_complete: bool,
    authority_root: Path | None = None,
    profile_path: Path | None = None,
) -> None:
    reject_evidence_symlinks(report_dir)
    for evidence in [evidence_dir] if isinstance(evidence_dir, Path) else evidence_dir:
        reject_evidence_symlinks(evidence)
    repository = authority_root if authority_root is not None else Path(__file__).parents[2]
    if profile_path is None:
        if authority_root is None:
            raise ValueError(
                "An explicit report profile is required; pass --profile or --authority-root"
            )
        profile_path = contained_path(
            authority_root, "evaluation/browser-use/current-report.json", reject_symlinks=True
        )
    if profile_path.is_symlink():
        raise ValueError("Symlinked report profile is forbidden")
    if not profile_path.is_file():
        raise ValueError(
            "Browser Use release authority is missing; pass --authority-root "
            "with the selected instance's bound evaluation and research files"
        )
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if profile.get("schema") != "lex_browser_use_current_report/2":
        raise ValueError("unsupported Browser Use release authority schema")
    frozen = profile.get("official_dataset")
    if frozen:
        dataset_path = Path(frozen["path"])
        if (
            dataset_path.is_symlink()
            or not dataset_path.is_file()
            or _sha256(dataset_path) != frozen.get("sha256")
            or frozen.get("sha256") != profile.get("official_data_checksum")
        ):
            raise ValueError("Frozen official dataset differs from the instance profile")
    selection_path = report_selection_path(profile, selection_path, authority_root=repository)
    if selection_path.is_symlink():
        raise ValueError("Symlinked selection is forbidden")
    cause_profile = profile.get("failure_cause_review") or {}
    cause_path = failure_review_path(profile, authority_root=repository)
    if require_complete and cause_profile.get("required_for_publication"):
        configured_hash = cause_profile.get("source_sha256")
        if not isinstance(configured_hash, str) or configured_hash != file_sha256(cause_path):
            raise ValueError("current profile is not bound to the failure-cause review")
    _configure_engine_labels(profile)
    _configure_models(profile)
    engines = tuple(str(engine) for engine in profile.get("engines") or [])
    models = tuple(str(model) for model in profile.get("models") or [])
    if not engines or len(engines) != len(set(engines)):
        raise ValueError("current Browser Use profile must register unique browser engines")
    page_names = (
        "index.html",
        "changes/index.html",
        "datasets/index.html",
        "methodology/index.html",
        "analysis/index.html",
        "review/index.html",
        "adoption/index.html",
        "reproduce/index.html",
        "accounts/index.html",
        "evidence-viewer.html",
        *(f"engines/{engine}.html" for engine in engines),
    )
    pages = tuple(report_dir / name for name in page_names)
    index = pages[0]
    audit_path = report_dir / "report-data.json"
    bundles = _load_evidence_bundles(evidence_dir)
    task_pages = tuple(
        report_dir / "datasets" / f"{task_id}.html"
        for task_id in sorted(task.task_id for task in load_pilot(selection_path).tasks)
    )
    measured_rows = [row for bundle in bundles for row in bundle["source_attempts"]]
    logical_rows = [
        row
        for bundle in bundles
        for row in _logical_results(
            bundle["source_attempts"], bundle["manifest"].get("protocol") or {}
        )
    ]
    result_pages = tuple(
        report_dir / "results" / f"{_attempt_id(row)}.html" for row in logical_rows
    )
    chinese_pages = (*pages, *task_pages, *result_pages)
    _, history = bound_json_asset(profile.get("report_history"), root=repository)
    if json.loads((report_dir / "report-changes.json").read_text()) != history:
        raise ValueError("Report history differs from the selected instance")
    check_language_contract(report_dir)
    english_pages = tuple(
        report_dir / "en" / page.relative_to(report_dir)
        for page in chinese_pages
        if "en" in generated_languages(report_dir)
    )
    data_files = tuple(
        report_dir / "data" / name
        for name in (
            "selection.json",
            "results.jsonl",
            "summary.json",
            "environment.json",
            "review.json",
            "publication-privacy.json",
            "evidence-index.json",
            "failure-cause-review.json",
        )
    )
    artifact_files = tuple(
        report_dir
        / "artifacts"
        / _attempt_id(row)
        / "executions"
        / str(source["attempt"])
        / filename
        for row in logical_rows
        for source in (row.get("_source_executions") or [row])
        for filename in PUBLIC_EXECUTION_ARTIFACTS.values()
    )
    if not all(
        path.is_file()
        for path in (
            *chinese_pages,
            *english_pages,
            *data_files,
            *artifact_files,
            audit_path,
        )
    ):
        raise ValueError("report is missing one or more required report pages")
    selection = load_pilot(selection_path)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    check_build_version(report_dir, audit)
    if audit.get("profile_sha256") and audit.get("template") != template_identity("browser-use"):
        raise ValueError("report bypasses the maintained Browser Use template")
    if audit.get("profile_sha256") and audit["profile_sha256"] != _sha256(profile_path):
        raise ValueError("report was built from a different instance profile")
    if audit.get("selection_sha256") != selection.sha256:
        raise ValueError("report was not built from the current pre-registered selection")
    rows = measured_rows
    selected_ids = {task.task_id for task in selection.tasks}
    expected_primary = {
        (model, engine, task_id, 1)
        for model in models
        for engine in engines
        for task_id in selected_ids
    }
    by_key: dict[tuple[str, str, int, int], dict] = {}
    for row in rows:
        key = (
            str(row["model"]),
            str(row["engine"]),
            int(row["task_id"]),
            int(row["attempt"]),
        )
        if key in by_key:
            raise ValueError(f"duplicate measured attempt: {key}")
        if key[0] not in set(models) or key[1] not in set(engines) or key[2] not in selected_ids:
            raise ValueError(f"attempt outside the registered cohort: {key}")
        if key[3] not in {1, 2}:
            raise ValueError(f"more than one retry is not allowed: {key}")
        by_key[key] = row
    for model, engine, task_id, attempt in by_key:
        if attempt == 2:
            primary = by_key.get((model, engine, task_id, 1))
            if primary is None or float(primary["score"]) == 1.0:
                raise ValueError(
                    f"retry is allowed only after a failed primary: {(model, engine, task_id)}"
                )
    rendered_pages = {
        page: page.read_text(encoding="utf-8") for page in (*chinese_pages, *english_pages)
    }
    _check_reader_facing_ux(report_dir, rendered_pages)
    if any('<html lang="en">' not in rendered_pages[page] for page in english_pages):
        raise ValueError("every Chinese report route must have an English mirror")
    cjk = re.compile(r"[\u3400-\u9fff]")
    localized_attribute = re.compile(r'(?:aria-label|placeholder|title)="([^"]*)"', flags=re.I)
    untranslated_interface = (
        "该逻辑结果包含",
        "物理执行",
        "判分权威",
        "单次调用墙钟时间",
        "查看该浏览器的全部结果",
        "完整结果只保留一份",
        "该执行没有可公开的 trace 页面画面",
        "个物理执行均记录",
        "个物理执行均保存",
        "本轮剔除",
    )
    for page in english_pages:
        text = rendered_pages[page]
        title = re.search(r"<title>(.*?)</title>", text, flags=re.I | re.S)
        if title and cjk.search(title.group(1)):
            raise ValueError(f"English report title is not localized: {page}")
        if any(cjk.search(value) for value in localized_attribute.findall(text)):
            raise ValueError(f"English report accessibility text is not localized: {page}")
        if any(marker in text for marker in untranslated_interface):
            raise ValueError(f"English report interface prose is not localized: {page}")
        excluded_lists = re.findall(r"tasks excluded in this run: ([^<]+)", text)
        if any("、" in value for value in excluded_lists):
            raise ValueError(f"English excluded-task list uses Chinese punctuation: {page}")
    for relative, allowed in {
        "index.html": {"中文"},
    }.items():
        page = report_dir / "en" / relative
        if page not in rendered_pages:
            continue
        visible = re.sub(r"\s+", " ", _visible_text(rendered_pages[page]))
        for token in allowed:
            visible = visible.replace(token, "")
        match = cjk.search(visible)
        if match:
            excerpt = visible[max(0, match.start() - 40) : match.start() + 80]
            raise ValueError(
                f"English reader-facing page contains Chinese prose: {page}: {excerpt}"
            )
    for engine in engines:
        engine_page = rendered_pages[report_dir / "engines" / f"{engine}.html"]
        if "engine-result-table" in engine_page or f"f_engine={engine}" not in engine_page:
            raise ValueError("browser pages must link to the single filtered result inventory")
    if task_pages and any(
        "Ground truth · 官方验收合同" not in rendered_pages[page]
        or "查看本题运行结果" not in rendered_pages[page]
        or "最终结果对照" in rendered_pages[page]
        for page in task_pages
    ):
        raise ValueError(
            "task pages must publish frozen contracts without embedding measured comparisons"
        )
    dataset_index = rendered_pages[report_dir / "datasets/index.html"]
    if "data-result=" in dataset_index or "可完成性证据" in dataset_index:
        raise ValueError("dataset inventory must not mix measured results into task definitions")
    review_index = rendered_pages[report_dir / "review/index.html"]
    if (
        "核验与修订记录" not in review_index
        or "核验轮次" not in review_index
        or "review-result-table" in review_index
        or "逐结果覆盖" in review_index
    ):
        raise ValueError("review page must present review rounds, not a per-result audit table")
    selected_tasks = json.loads((report_dir / "data/selection.json").read_text())["tasks"]
    environment = bundles[0]["manifest"].get("environment") or {}
    for page in (report_dir / "methodology/index.html", report_dir / "en/methodology/index.html"):
        if page in rendered_pages:
            check_methodology_evidence(rendered_pages[page], selected_tasks, environment)
    analysis_index = rendered_pages[report_dir / "analysis/index.html"]
    for marker in (
        "comparison-winner",
        f"完整 {len(selection.tasks)} 题结果入口",
        "BrowserTool 动作延迟",
        "action-card-grid",
        "全部配置 · 单次调用墙钟时间",
        "同题全配置对比",
        'id="configuration-matrix"',
        "查看诊断",
        'class="diagnosis-dialog"',
        "程序报错",
    ):
        if marker not in analysis_index:
            raise ValueError(f"analysis is missing timing or scalable comparison: {marker}")

    guide_index = rendered_pages[report_dir / "index.html"]
    for marker in (
        f"{len(selection.tasks)} 题核心指标",
        "Agent 总耗时",
        "BrowserTool 耗时",
        "均值",
        "P50",
        "P95",
        "comparison-winner",
        f"全部 {len(models) * len(engines)} 种配置中比较",
        "已通过结果没有再做独立语义盲审",
        "程序判分落点与复核根因",
        'class="verdict-strip"',
        'class="cause-bars"',
        'id="home-qualified-results"',
        'class="audit-ladder"',
        'id="home-resource-table"',
    ):
        if marker not in guide_index:
            raise ValueError(f"interpretation is missing an evidence-backed reading aid: {marker}")
    if "首次成功与重试恢复" in guide_index:
        raise ValueError("homepage must present one final logical result, not retry narration")
    for forbidden in (
        "中位数",
        "失败结果定位",
        "是否属于目标变量",
        'id="failure-table"',
        'id="pair-table"',
        'class="finding-list"',
    ):
        if forbidden in analysis_index:
            raise ValueError(f"analysis contains a redundant or retired presentation: {forbidden}")
    participant_index = rendered_pages[report_dir / "adoption/index.html"]
    if "被测浏览器与固定实验条件" not in participant_index or "参评选手" in participant_index:
        raise ValueError("participant page must use neutral reader-facing terminology")
    if result_pages and any(
        marker not in rendered_pages[page]
        for page in result_pages
        for marker in (
            "任务要求与验收结果",
            "查看官方期望与本次观察",
            "CowAgent 自主执行轨迹",
            "运行、日志与证据",
            "runtime-strip",
            "任务与验收",
            "在页面中查看完整执行证据",
            "下载原始 JSON",
        )
    ):
        raise ValueError("every result page must expose result, evaluator, trajectory and logs")
    evidence_viewer = report_dir / "evidence-viewer.html"
    english_evidence_viewer = report_dir / "en/evidence-viewer.html"
    viewers = (evidence_viewer, english_evidence_viewer) if english_pages else (evidence_viewer,)
    if any(path not in rendered_pages for path in viewers):
        raise ValueError("report is missing the shared HTML execution-evidence viewer")
    for marker in (
        'id="evidence-viewer-content"',
        "execution-evidence[.]json",
        "fetch(new URL(source,document.baseURI)",
    ):
        if marker not in rendered_pages[evidence_viewer]:
            raise ValueError(f"execution-evidence viewer is incomplete: {marker}")
    failed_pages = [
        report_dir / "results" / f"{_attempt_id(row)}.html"
        for row in logical_rows
        if float(row["score"]) < 1
    ]
    if failed_pages and any(
        marker not in rendered_pages[page]
        for page in failed_pages
        for marker in (
            "失败诊断",
            'class="failure-lead"',
            "程序报错",
            "程序判定",
            "复核根因",
            "负责范围",
            "失败执行的最后页面画面",
        )
    ):
        raise ValueError("every failed result must expose a complete diagnosis path")
    if any(
        marker in rendered_pages[page]
        for page in result_pages
        for marker in ("Ground truth 与实际最终回答", "官方 evaluator 逐规则判定")
    ):
        raise ValueError("result pages must not duplicate answer and evaluator presentations")
    html = rendered_pages[index]
    article_heading = "程序判分落点与复核根因"
    if article_heading not in rendered_pages[report_dir / "index.html"]:
        raise ValueError("homepage must contain the authored interpretation")
    public_results = load_jsonl(report_dir / "data/results.jsonl", skip_blank=True)
    if len(public_results) != len(logical_rows):
        raise ValueError("public result projection must cover every logical result")
    evidence_index = json.loads(
        (report_dir / "data/evidence-index.json").read_text(encoding="utf-8")
    )
    _check_execution_artifacts(report_dir, logical_rows, public_results, evidence_index)
    _check_public_privacy(report_dir)
    forbidden = {
        "agent_response",
        "paths",
        "model_transport_errors",
    }
    if any(forbidden & set(item) for item in public_results):
        raise ValueError("public result projection contains private evidence fields")
    forbidden_report_terms = (
        "诊断重试",
        "失败重试",
        "首轮试次",
        "assistant reasoning",
        "用户提示词",
        "我的思考",
        "内部工作日志",
        "另一个覆盖更多站点的 179 题",
        "后续 179 题",
        "需要注意：",
        "代理实测返回 team_model_access_denied",
        "证据支持的诊断",
        "共享链路诊断",
        "失败归因：",
    )
    for page, text in rendered_pages.items():
        if any(term in text for term in forbidden_report_terms):
            raise ValueError(
                f"formal report contains forbidden notebook or execution-label text: {page}"
            )
        if re.search(
            r"<svg\b|<(?:img|object|embed)\b[^>]+(?:src|data)=[\"'][^\"']*\.svg(?:[?#][^\"']*)?[\"']",
            text,
            flags=re.I,
        ):
            raise ValueError(
                f"Browser Use reports must use raster or Mermaid diagrams, not SVG: {page}"
            )
    svg_assets = [
        path for path in report_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".svg"
    ]
    if svg_assets:
        raise ValueError(f"Browser Use report contains SVG assets: {svg_assets[0]}")
    style = (report_dir / "assets/style.css").read_text(encoding="utf-8")
    for required_style in (
        "#task-table{min-width:1500px}",
        "background:var(--code-bg)!important",
        ".method-figure img{min-width:960px",
        ".detail-grid .kv{overflow-wrap:anywhere",
        ".comparison-winner{",
        ".browser-comparison{",
        ".action-card-grid{",
        ".attempt-summary{",
        "#result-table{min-width:1580px;table-layout:fixed}",
        ".diagnosis-grid{",
        ".screenshot-grid{",
        ".review-round{",
        ".page-main{overflow-x:clip}",
    ):
        if required_style not in style:
            raise ValueError(f"report is missing publication readability guard: {required_style}")
    if require_complete:
        if not str(profile.get("logical_result") or "").startswith(
            "score authority follows the frozen run manifest"
        ):
            raise ValueError("current report profile must defer score authority to the manifest")
        authorities = audit.get("score_authority") or {}
        review_bindings = (audit.get("review") or {}).get("bindings") or {}
        for bundle in bundles:
            evidence_path = bundle["path"]
            manifest_path = evidence_path / "manifest.json"
            run_config_path = evidence_path / "run-config.json"
            attempts_path = evidence_path / "attempts.jsonl"
            review_path = evidence_path / "result-review.json"
            if not manifest_path.is_file() or not run_config_path.is_file():
                raise ValueError("complete evidence requires manifest.json and run-config.json")
            manifest, run_config = bundle["manifest"], bundle["config"]
            model = bundle["model"]
            official = (manifest.get("environment") or {}).get("official_evaluator") or {}
            if model not in models:
                raise ValueError(
                    f"current report profile does not register measured model: {model}"
                )
            for key, expected in {
                "selection_sha256": selection.sha256,
                "reasoning_effort": (manifest.get("protocol") or {}).get("reasoning_effort"),
                "official_data_checksum": official.get("data_checksum"),
            }.items():
                if profile.get(key) != expected:
                    raise ValueError(f"current report profile mismatch for {key}: {model}")
            browser_versions = set(
                (manifest.get("environment") or {}).get("browser_versions") or {}
            )
            if set(engines) != browser_versions:
                raise ValueError(f"current report engines differ from measured identities: {model}")
            authority = (manifest.get("protocol") or {}).get(
                "official_score_uses"
            ) or "final execution for each browser and task"
            if authorities.get(model) != authority:
                raise ValueError(f"report renderer changed frozen score authority: {model}")
            if manifest.get("selection_sha256") != selection.sha256:
                raise ValueError("manifest selection hash differs from the registered cohort")
            if run_config.get("selection_sha256") != selection.sha256:
                raise ValueError("run configuration differs from the registered cohort")
            if run_config.get("reasoning_effort") != profile.get("reasoning_effort"):
                raise ValueError("run configuration reasoning effort differs from the profile")
            if not run_config.get("browser_eval_commit"):
                raise ValueError("run configuration must identify the browser-eval commit")
            if manifest.get("protocol", {}).get("browser_eval_commit") != run_config.get(
                "browser_eval_commit"
            ):
                raise ValueError("manifest and run configuration disagree on browser-eval commit")
            if manifest.get("protocol", {}).get("model_transport_retries") != 1:
                raise ValueError("pilot allows exactly one model transport retry")
            if not review_path.is_file():
                raise ValueError("complete publication requires result-review.json per model")
            bundle_review = bundle["review"]
            bindings = bundle_review.get("bindings") or {}
            if bindings.get("attempts_sha256") != _sha256(attempts_path):
                raise ValueError(f"result review is not bound to current attempts: {model}")
            if bindings.get("manifest_sha256") != _sha256(manifest_path):
                raise ValueError(f"result review is not bound to current manifest: {model}")
            if bindings.get("selection_sha256") != selection.sha256:
                raise ValueError("result review is not bound to the registered selection")
            model_rows = [row for row in rows if row["model"] == model]
            coverage = bundle_review.get("coverage") or {}
            if coverage != {
                "recorded_attempts": len(model_rows),
                "reviewed_attempts": len(model_rows),
            }:
                raise ValueError(f"result review does not cover every attempt: {model}")
            if bundle_review.get("disagreements") or bundle_review.get("unresolved"):
                raise ValueError(f"result review has disagreements or unresolved checks: {model}")
            reviewed_keys = {
                (model, str(item["engine"]), int(item["task_id"]), int(item["attempt"]))
                for item in bundle_review.get("attempts") or []
            }
            measured_keys = {key for key in by_key if key[0] == model}
            if reviewed_keys != measured_keys:
                raise ValueError(f"result review inventory differs from evidence: {model}")
            if review_bindings.get(model) != bindings:
                raise ValueError(f"rendered report review binding differs: {model}")
        missing = expected_primary - set(by_key)
        if missing:
            raise ValueError(f"missing {len(missing)} primary attempts")
        missing_retries = {
            (model, engine, task_id, 2)
            for model, engine, task_id, _ in expected_primary
            if float(by_key[(model, engine, task_id, 1)]["score"]) != 1.0
            and (model, engine, task_id, 2) not in by_key
        }
        if missing_retries:
            raise ValueError(f"missing {len(missing_retries)} permitted failure retries")
        if "测量尚未完整" in html:
            raise ValueError("complete publication cannot contain an incomplete-results notice")
        cause_profile = profile.get("failure_cause_review") or {}
        cause_required = bool(cause_profile.get("required_for_publication"))
        default_cause_review = load_failure_cause_review(cause_path)
        verified_causes: dict[str, dict] = {}
        for bundle in bundles:
            model = bundle["model"]
            failed_ids = {
                _attempt_id(row)
                for row in logical_rows
                if row["model"] == model and float(row.get("score") or 0) < 1
            }
            verified_causes.update(
                validate_failure_cause_review(
                    default_cause_review,
                    model_id=model,
                    attempts_sha256=bundle["attempts_sha256"],
                    failed_result_ids=failed_ids,
                    require_complete=cause_required,
                )
            )
        if cause_required:
            failed_result_ids = {
                _attempt_id(row) for row in logical_rows if float(row.get("score") or 0) < 1
            }
            if set(verified_causes) != failed_result_ids:
                raise ValueError("every failed logical result must have a verified root cause")
            configured_hash = cause_profile.get("source_sha256")
            if not isinstance(configured_hash, str) or configured_hash != file_sha256(cause_path):
                raise ValueError("current profile is not bound to the failure-cause review")
        published_cause_review = json.loads(
            (report_dir / "data/failure-cause-review.json").read_text(encoding="utf-8")
        )
        if published_cause_review.get("results") != verified_causes:
            raise ValueError("published failure causes differ from verified reviews")
        interpretation = json.loads(
            (report_dir / "data/interpretation-evidence.json").read_text(encoding="utf-8")
        )
        scoring_claim = next(
            (
                item
                for item in interpretation.get("claims") or []
                if item.get("claim") == "官方 evaluator 的失败规则组合"
            ),
            None,
        )
        if not scoring_claim:
            raise ValueError("interpretation evidence is missing programmatic failure locations")
        scoring_basis = scoring_claim.get("basis") or {}
        scoring_classes = scoring_basis.get("classes") or {}
        failed_count = sum(1 for row in logical_rows if float(row.get("score") or 0) < 1)
        if (
            scoring_basis.get("denominator") != failed_count
            or sum(
                int(scoring_classes.get(key) or 0)
                for key in ("network_only", "response_only", "both")
            )
            != failed_count
        ):
            raise ValueError("programmatic failure locations do not cover every final failure")
        remediation = json.loads(
            contained_path(
                repository, "research/browser-use-remediation.json", reject_symlinks=True
            ).read_text(encoding="utf-8")
        )
        if remediation.get("schema") != "lex_browser_use_remediation/1":
            raise ValueError("unsupported Browser Use remediation schema")
        external_prefixes = ("benchmark_", "site_", "environment_", "mixed_")
        expected_quarantine = {
            int(result_id.rsplit("-", 1)[-1])
            for cohort in default_cause_review.get("cohorts", {}).values()
            for result_id, diagnosis in (cohort.get("results") or {}).items()
            if str((diagnosis.get("subcategory") or {}).get("code") or "").startswith(
                external_prefixes
            )
        }
        registered_quarantine = {
            int(task_id)
            for item in remediation.get("items") or []
            if item.get("category")
            in {
                "shared_environment",
                "benchmark_or_evaluator",
                "benchmark_instruction_contract",
                "self_hosted_site",
                "reference_state_mixed",
            }
            for task_id in item.get("task_ids") or []
        }
        if registered_quarantine != expected_quarantine:
            raise ValueError("remediation quarantine differs from verified external interference")
        for bundle in bundles:
            for _page, text in rendered_pages.items():
                if str(bundle["path"]) in text:
                    raise ValueError(
                        "published pages must not contain machine-local evidence paths"
                    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--evidence", required=True, action="append", type=Path)
    parser.add_argument(
        "--selection",
        type=Path,
        help="Explicit selection; defaults to the bound authority root",
    )
    parser.add_argument("--authority-root", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    check(
        args.report,
        args.evidence,
        args.selection,
        require_complete=args.require_complete,
        authority_root=args.authority_root,
        profile_path=args.profile,
    )
    print("Browser Use report integrity check passed")


if __name__ == "__main__":
    main()
