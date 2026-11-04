"""Build the evidence-backed WebArena Browser Trajectory Replay report."""

from __future__ import annotations

import argparse
import html
import importlib.resources
import json
import math
import re
import shutil
import statistics
import tempfile
import zipfile
from collections import Counter, defaultdict
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from harness.data_home import data_home
from harness.record_io import canonical_json_sha256, file_sha256, load_jsonl, read_json, write_json
from harness.report_languages import (
    add_language_argument,
    includes_english,
    primary_report_language,
    select_report_language,
)
from harness.reporting.assets import copy_bound_assets
from harness.reporting.locales import translate
from harness.reporting.mermaid import mermaid_script_attributes
from harness.reporting.privacy import TRAJECTORY_REPLAY
from harness.reporting.templates import template_identity
from harness.reporting.versions import bind_build_version
from harness.trajectory_replay.catalog_contracts import (
    LEGACY_CATALOG_CONTRACTS_SCHEMA,
    LEGACY_CATALOG_REPORT_SCHEMA,
)
from harness.trajectory_replay.evaluator_transition import reevaluate_pass
from harness.trajectory_replay.failure_matrix import (
    browser_failure_profile,
    failure_category_matrix,
    failure_signature_cards,
)
from harness.trajectory_replay.interpretation import render_interpretation, standalone_document
from harness.trajectory_replay.messages import MESSAGES, report_text
from harness.trajectory_replay.participants import (
    bind_participant,
    participant_registry,
    resolve_participant_id,
)
from harness.trajectory_replay.remote_runtime import load_remote_sites, remote_benchmark
from harness.trajectory_replay.report_inputs import bound_dataset_path, verified_dataset_path
from harness.trajectory_replay.runtime_identity import request_bridge_identity
from harness.trajectory_replay.webarena_corrections import load_corrections
from harness.trajectory_replay.webarena_review import (
    load_validated_failure_review,
    programmatic_failure,
    review_evidence_path,
)
from lexbench_webfetch.metrics import percentile

ASSETS = Path(__file__).with_name("assets")
REPORT_CSS = (ASSETS / "webarena-report.css").read_text(encoding="utf-8")
REPORT_JS = (ASSETS / "webarena-report.js").read_text(encoding="utf-8")
ARTICLE_CSS = (ASSETS / "webarena-article.css").read_text(encoding="utf-8")

# This notice describes one retained historical cohort, not trajectory replay in
# general.  Keying all three immutable inputs prevents a future requalified run
# from inheriting legacy limitations merely because it also contains 90 tasks.
LEGACY_REPLAY_VALIDITY_INPUTS = {
    (
        "abaaa0e90aced74a831093f2d6d7c3371639acb984b3a2c057f3cc39632ea935",
        "61bb38bce552b5c7eea0e1f5fcca1bc898f55e2e39ee6e72498cff346ca1e4e3",
        "3ca5d91cfec570490df202c9cb332957f7e850b70fc5af80b684709e97ef0893",
    )
}


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _legacy_retrieval_provenance(
    results: dict[str, Any], dataset: dict[str, Any]
) -> dict[str, Any]:
    """Prove whether retained retrieval verdicts used saved Agent answers."""
    source = Path(results["source_seed_manifest"])
    if (not source.is_absolute()
            or any(part.is_symlink() for part in (source, *source.parents))
            or not source.is_file()):
        raise ValueError("legacy replay seed manifest must be an absolute file without symlinks")
    if file_sha256(source) != results.get("source_seed_manifest_sha256"):
        raise ValueError("legacy replay seed manifest hash mismatch")
    seeds = json.loads(source.read_text(encoding="utf-8"))
    by_id = {int(row["task_id"]): row for row in seeds["tasks"]}
    retrieval_ids = {
        int(row["task_id"])
        for row in dataset["trajectories"]
        if row["evaluation_input"]["task_type"] == "RETRIEVE"
    }
    rows = []
    for attempt in results["attempts"]:
        task_id = int(attempt["task_id"])
        if task_id not in retrieval_ids:
            continue
        seed = by_id[task_id]
        candidates = [
            item for item in seed["candidates"]
            if item["id"] == seed["canonical_candidate_id"]
        ]
        if len(candidates) != 1:
            raise ValueError(f"task {task_id}: canonical replay seed is ambiguous")
        saved_hash = canonical_json_sha256(candidates[0]["recorded_response"])
        actual_hash = attempt.get("evaluation_input_sha256")
        if actual_hash != saved_hash:
            raise ValueError(f"task {task_id}: retrieval input differs from frozen Agent answer")
        rows.append({
            "task_id": task_id, "browser": attempt["engine"],
            "evaluation_input_sha256": actual_hash,
            "source": "frozen_agent_response_not_current_page_observation",
        })
    browsers = {str(browser) for browser in results["engines"]}
    expected = {(browser, task_id) for browser in browsers for task_id in retrieval_ids}
    actual = {(row["browser"], row["task_id"]) for row in rows}
    if actual != expected or len(rows) != len(expected):
        raise ValueError("legacy retrieval provenance lacks the full browser-task matrix")
    return {
        "schema": "trajectory_replay_legacy_retrieval_provenance/1",
        "source_seed_manifest_sha256": results["source_seed_manifest_sha256"],
        "retrieval_tasks": len(retrieval_ids),
        "retrieval_results": len(rows),
        "frozen_answer_matches": len(rows),
        "meaning": "A matching verdict verifies the saved answer against the official evaluator, not an answer reconstructed from this browser run.",
        "results": sorted(rows, key=lambda item: (item["task_id"], item["browser"])),
    }


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


def _outcome_metrics(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Keep the declared population; missing samples never become zero."""
    fields = {"duration": "duration_ms", "cpu": "cpu_ms", "memory": "memory_mean_bytes",
              "memory_peak": "memory_peak_bytes", "network_rx": "network_rx_bytes",
              "network_tx": "network_tx_bytes", "cpu_utilization": "cpu_average_utilization_pct"}
    result = {}
    for name, field in fields.items():
        values = []
        for row in rows:
            source = row if name == "duration" else (row.get("resource") or {})
            value = source.get(field)
            if (source.get("available") is not False and type(value) in (int, float)
                    and math.isfinite(value) and value >= 0):
                values.append(float(value))
        result[name] = {**_stat(values), "population": len(rows), "missing": len(rows) - len(values)}
    return result


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
        value = TRAJECTORY_REPLAY.redact(value)
        value = re.sub(
            r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{12,}",
            "[secret removed]",
            value,
        )
    return value


def _write_clean_json(path: Path, value: Any) -> None:
    write_json(path, _clean(value), parents=True)


def _validated_planned_actions(attempt: dict[str, Any]) -> list[dict[str, Any]]:
    actions = attempt.get("planned_actions")
    digest = attempt.get("planned_actions_sha256")
    if (
        not isinstance(actions, list)
        or not actions
        or any(not isinstance(action, dict) or not action.get("action") for action in actions)
        or _clean(actions) != actions
        or len(actions) != attempt.get("action_count")
        or not isinstance(digest, str)
        or digest != canonical_json_sha256(actions)
    ):
        raise ValueError(
            f"method-specific planned actions missing or changed for "
            f"{attempt.get('participant_id')}/{attempt.get('task_id')}"
        )
    return actions


def _reference_validity_by_task(attempts: list[dict[str, Any]]) -> dict[int, str]:
    task_validity: dict[int, str] = {}
    for attempt in attempts:
        validity = attempt.get("reference_validity") or {}
        status = validity.get("status")
        if status not in {"valid", "unverifiable"}:
            raise ValueError(f"reference validity is missing for task {attempt['task_id']}")
        task_id = int(attempt["task_id"])
        if task_id in task_validity and task_validity[task_id] != status:
            raise ValueError(f"methods disagree on task {task_id} reference validity")
        task_validity[task_id] = status
    return task_validity


def require_all_reference_validity(
    profile: dict[str, Any], results: dict[str, Any], dataset: dict[str, Any]
) -> None:
    """Reject a formal replay release with any unverified task reference."""

    if (profile.get("scoring") or {}).get("require_all_reference_validity") is not True:
        return
    method_rows = profile.get("methods") or []
    task_rows = dataset.get("trajectories") or []
    task_ids = [row.get("task_id") for row in task_rows if isinstance(row, dict)]
    declared_count = (profile.get("task_selection") or {}).get("task_count")
    if (
        len(method_rows) < 2
        or not task_ids
        or len(task_ids) != declared_count
        or len(set(task_ids)) != len(task_ids)
    ):
        raise ValueError("formal reference-validity gate requires registered methods and the declared task set")
    methods = participant_registry(method_rows)
    attempts = [bind_participant(row, methods) for row in results.get("attempts") or []]
    expected = {(method_id, task_id) for method_id in methods for task_id in task_ids}
    actual = {(row["participant_id"], row.get("task_id")) for row in attempts}
    if len(attempts) != len(methods) * len(task_ids) or actual != expected:
        raise ValueError("formal reference-validity gate requires every method-task result")
    validity = _reference_validity_by_task(attempts)
    unavailable = sorted(task_id for task_id, status in validity.items() if status != "valid")
    if unavailable:
        raise ValueError(f"formal reference-validity gate rejects unverifiable tasks: {unavailable}")


def _measured_validity_identity(
    results: dict[str, Any], results_source: Path, dataset_source: Path
) -> dict[str, str]:
    # The current publication profile is deliberately not an input: changing
    # host/version cannot change the limitations of an already measured run.
    return {
        "results_sha256": file_sha256(results_source),
        "dataset_sha256": file_sha256(dataset_source),
        "profile_sha256": results.get("profile_sha256", ""),
    }


def _historical_replay_validity_notice(
    *, results_sha256: str, dataset_sha256: str, profile_sha256: str, lang: str
) -> str:
    """Disclose limitations only for the hash-bound legacy 90-task cohort."""

    identity = (results_sha256, dataset_sha256, profile_sha256)
    if identity not in LEGACY_REPLAY_VALIDITY_INPUTS:
        return ""
    t = report_text(lang)
    heading = t("legacy.heading", "The historical 90-task scores remain, but they are not current qualification proof")
    body = t(
        "legacy.body",
        "Chromium 90/90 and Moli 54/90 remain the original acceptance results for this historical fixed-trajectory cohort; no score is manually changed. "
        "However, the legacy qualification flow accepted snapshot actions as tool-wrapper successes even when their returned content began [Snapshot error: …]. "
        "All 74 results for the 37 RETRIEVE tasks used frozen recorded_response values for acceptance without binding answers to data read from the current page, and the historical runs used mismatched viewports: Chromium 780×437/493 and Moli 1920×1080. "
        "These scores therefore describe fixed-path acceptance under the legacy protocol. They do not prove that all 90 trajectories satisfy the current fail-closed qualification rules or measure browser success at a common viewport. A corrected formal comparison requires renewed qualification and a complete rerun.",
    )
    return f'<section class="callout historical-validity"><h2>{_e(heading)}</h2><p>{_e(body)}</p></section>'


def _load_artifact(ref: dict[str, Any] | None) -> Any:
    if not ref or not ref.get("path"):
        return None
    path = review_evidence_path(ref["path"])
    if (
        any(part.is_symlink() for part in (path, *path.parents))
        or not path.is_file()
        or file_sha256(path) != ref.get("sha256")
    ):
        raise ValueError(f"evidence artifact is missing or changed: {path.name}")
    return read_json(path)


def _supplemental_diagnostic(review: dict[str, Any] | None, task_id: int) -> Any:
    ref = (review or {}).get("supplemental_diagnostic")
    if not ref:
        return None
    diagnostic = _load_artifact(ref)
    if (diagnostic.get("schema") != "lex_replay_supplemental_diagnostic/1"
            or diagnostic.get("task_id") != task_id
            or diagnostic.get("scope") not in {
                "read_only_prefix_on_current_site_not_a_replacement_score",
                "isolated_local_http_fixture_not_a_replacement_score",
            }
            or diagnostic.get("mutations_submitted") is not False):
        raise ValueError("supplemental diagnostic scope or task differs")
    return diagnostic


def _supplemental_diagnostic_html(diagnostic: dict | None, lang: str) -> str:
    if not diagnostic:
        return ""
    t = report_text(lang)
    rows = []
    component_rows = []
    readiness_rows = []
    network_rows = []
    header_rows = []
    for method in diagnostic["observations"]:
        label = method.get("label") or method.get("version") or {
            "release": t("diagnostic.build.release", "Release build"),
            "pr": t("diagnostic.build.candidate", "Candidate build"),
        }.get(method["method"], method["method"])
        for sample in method["observations"]:
            for request in sample["state"].get("headerRecording", []):
                cookie_label = t("common.yes", "Yes") if request["cookie_present"] else t("common.no", "No")
                missing = t("diagnostic.header.missing", "Missing")
                values = (label, request["api"], cookie_label, request["status"],
                          request["wire_referer_path"] or missing, request["har_referer_path"] or missing)
                header_rows.append("<tr>" + "".join(f"<td>{_e(value)}</td>" for value in values) + "</tr>")
            for request in sample["state"].get("httpReads", []):
                values = (label, request.get("path") or request.get("url") or (t("diagnostic.path.not_recorded", "Not recorded")), request.get("status", "—"),
                          request.get("bytes", "—"), request.get("error", ""))
                network_rows.append("<tr>" + "".join(f"<td>{_e(value)}</td>" for value in values) + "</tr>")
            readiness = sample["state"].get("readiness")
            if readiness:
                values = (label, f'{sample["delay"]} s', readiness["target"],
                          readiness["disabled"], readiness["pendingModules"],
                          readiness["formValidationReady"])
                readiness_rows.append("<tr>" + "".join(f"<td>{_e(value)}</td>" for value in values) + "</tr>")
            for component in sample["state"].get("components", []):
                values = (label, f'{sample["delay"]} s', component["initialized"],
                          ", ".join(component["labels"]), component["wizardMatches"],
                          component["stepMatches"], component["selectedMatches"], component["handlerCount"])
                component_rows.append("<tr>" + "".join(f"<td>{_e(value)}</td>" for value in values) + "</tr>")
            for control in sample["state"].get("buttons", []) + sample["state"].get("dialogs", []):
                rect = " × ".join(str(value) for value in control["rect"])
                offset_parent = control.get("offsetParent")
                values = (label, f'{sample["delay"]} s', control["text"],
                          f'{control.get("display") or "—"} / {control.get("visibility") or "—"}', rect,
                          control["rectCount"] if control.get("rectCount") is not None else "—",
                          "—" if offset_parent is None else "non-null" if offset_parent else "null")
                rows.append("<tr>" + "".join(f"<td>{_e(value)}</td>" for value in values) + "</tr>")
    headers = (t("common.method", "Method"), t("diagnostic.column.additional_wait", "Additional wait"),
               t("diagnostic.column.target_text", "Target text"), "display / visibility",
               t("diagnostic.column.width_height", "Width × height (px)"),
               t("diagnostic.column.client_rects", "Client rects"), "offsetParent")
    title = t("diagnostic.title.target_state", "Supplemental probe: target-state comparison")
    if header_rows:
        title = t("diagnostic.title.headers", "Supplemental probe: sent and recorded headers")
    scope = (t("diagnostic.scope.read_only", "The probe only opened the interface or searched; no form or invitation was submitted. These later read-only observations do not replace the original trace or score."))
    if diagnostic.get("scope") == "isolated_local_http_fixture_not_a_replacement_score":
        scope = (t("diagnostic.scope.local_fixture", "An isolated local HTTP fixture, without accessing benchmark sites, uses the frozen browsers and production HAR recorder. It tests a mechanism and does not replace original trajectories or scores."))
    scope_note = (diagnostic.get("scope_note") or {}).get(lang)
    if scope_note:
        scope += " " + str(scope_note)
    if header_rows:
        scope += (t("diagnostic.scope.scroll_hint", " Scroll horizontally to compare server-observed headers with HAR records."))
    components = ""
    if component_rows:
        component_headers = (t("common.method", "Method"), t("diagnostic.column.additional_wait", "Additional wait"),
                             t("diagnostic.column.component_initialized", "Component initialized"),
                             t("diagnostic.column.attribute_options", "Attribute options"),
                             t("diagnostic.column.wizard_matches", "Wizard matches"),
                             t("diagnostic.column.step_matches", "Step matches"),
                             t("diagnostic.column.selection_matches", "Selection matches"),
                             t("diagnostic.column.event_handlers", "Event handlers"))
        components = ('<div class="table-scroll"><table data-component-readiness><thead><tr>'
                      + "".join(f"<th>{_e(header)}</th>" for header in component_headers)
                      + '</tr></thead><tbody>' + "".join(component_rows) + '</tbody></table></div>')
    readiness_table = ""
    if readiness_rows:
        readiness_headers = (t("common.method", "Method"), t("diagnostic.column.wait_after_dom_ready", "Wait after DOM ready"),
                             t("common.target", "Target"), t("diagnostic.column.disabled", "Disabled"),
                             t("diagnostic.column.pending_modules", "Pending modules"),
                             t("diagnostic.column.form_validation_bound", "Form validation bound"))
        readiness_table = ('<div class="table-scroll"><table data-readiness-comparison><thead><tr>'
                           + "".join(f"<th>{_e(header)}</th>" for header in readiness_headers)
                           + '</tr></thead><tbody>' + "".join(readiness_rows) + '</tbody></table></div>')
    geometry_table = (('<div class="table-scroll"><table><thead><tr>'
                       + "".join(f"<th>{_e(header)}</th>" for header in headers)
                       + '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>') if rows else "")
    network_table = ""
    if network_rows:
        network_headers = (t("common.method", "Method"), t("diagnostic.column.post_path", "Post path"),
                           t("diagnostic.column.http_status", "HTTP status"),
                           t("diagnostic.column.body_bytes", "Body UTF-8 bytes"), t("diagnostic.column.error", "Error"))
        network_table = ('<div class="table-scroll"><table data-http-read-comparison><thead><tr>'
                         + "".join(f"<th>{_e(header)}</th>" for header in network_headers)
                         + '</tr></thead><tbody>' + "".join(network_rows) + '</tbody></table></div>')
    header_table = ""
    if header_rows:
        header_headers = (t("common.method", "Method"), t("diagnostic.column.request_api", "Request API"),
                          t("diagnostic.column.cookie_present", "Cookie present"),
                          t("diagnostic.column.http_status", "HTTP status"),
                          t("diagnostic.column.referer_server", "Referer path received by server"),
                          t("diagnostic.column.referer_har", "Referer path in HAR"))
        header_table = ('<div class="table-scroll"><table data-header-recording-comparison><thead><tr>'
                        + "".join(f"<th>{_e(header)}</th>" for header in header_headers)
                        + '</tr></thead><tbody>' + "".join(header_rows) + '</tbody></table></div>')
    return (f'<section data-supplemental-diagnostic><details><summary>{title}</summary><p>{_e(scope)}</p>'
            + components + readiness_table + geometry_table + network_table + header_table + '</details></section>')


def _corrected_acceptance_by_task(
    attempts: list[dict[str, Any]], *, declared_revisions: dict | None = None,
) -> dict[int, dict[str, Any]]:
    """Expose corrected evaluator expectations without replacing the frozen dataset."""
    corrected: dict[int, dict[str, Any]] = {}
    declarations = {int(key): value for key, value in (declared_revisions or {}).items()}
    versions: dict[int, list[dict]] = {}
    for attempt in attempts:
        evaluation = _load_artifact((attempt.get("evidence") or {}).get("evaluation"))
        if not evaluation or not evaluation.get("contract_correction_id"):
            continue
        task_id = int(attempt["task_id"])
        if evaluation.get("task_id") != task_id:
            raise ValueError("corrected evaluation belongs to another task")
        row = {
            "correction_id": evaluation["contract_correction_id"],
            "corrected_task_sha256": evaluation.get("corrected_task_sha256"),
            "evaluators": [
                {"evaluator": item["evaluator_name"], "expected": item["expected"]}
                for item in evaluation.get("evaluators_results") or []
                if item.get("expected") is not None
            ],
        }
        if not row["corrected_task_sha256"] or not row["evaluators"]:
            raise ValueError(f"task {task_id}: corrected evaluation lacks acceptance evidence")
        if task_id in declarations:
            method = attempt["participant_id"]
            identity = {key: row[key] for key in ("correction_id", "corrected_task_sha256")}
            if declarations[task_id].get(method) != identity:
                raise ValueError(f"task {task_id}: declared acceptance revision differs for {method}")
            groups = versions.setdefault(task_id, [])
            group = next((item for item in groups if {key: item[key] for key in row} == row), None)
            if group is None:
                groups.append({**row, "methods": [method]})
            else:
                group["methods"].append(method)
            continue
        if task_id in corrected and corrected[task_id] != row:
            raise ValueError(f"task {task_id}: corrected acceptance differs across methods")
        corrected[task_id] = row
    if set(declarations) != set(versions):
        raise ValueError("declared acceptance revisions lack evaluator evidence")
    corrected.update({task: {"versions": rows} for task, rows in versions.items()})
    return corrected


def _format_ms(value: float | None, lang: str) -> str:
    if value is None:
        return "—"
    return report_text(lang)("unit.seconds", "{seconds:.1f} s", seconds=value / 1000)


def _task_type_label(task_type: str, lang: str) -> str:
    t = report_text(lang)
    return {
        "RETRIEVE": t("task_type.retrieve", "RETRIEVE"),
        "NAVIGATE": t("task_type.navigate", "NAVIGATE"),
        "MUTATE": t("task_type.mutate", "MUTATE"),
    }.get(task_type, task_type)


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
    t = report_text(lang)
    labels = (("mean", t("metric.mean", "Mean")), ("p50", "P50"), ("p95", "P95"))
    text = "<br>".join(
        f'<span class="metric-label">{label}</span> '
        + _winner(formatter(metric.get(key)), method_id in winners[key], lang=lang)
        for key, label in labels
    )
    if "n" in metric:
        coverage = f'n={metric["n"]}'
        if "population" in metric:
            coverage += f'/{metric["population"]}; ' + report_text(lang)("metric.coverage_missing", "missing {count}", count=metric["missing"])
        text += f"<br><small>{coverage}</small>"
    return text


def _unique_winners(
    rows: list[dict[str, Any]], key: str, *, higher: bool
) -> set[str]:
    numeric = [(row["id"], row.get(key)) for row in rows
               if type(row.get(key)) in (int, float) and math.isfinite(row[key])]
    if len(rows) < 2 or len(numeric) != len(rows):
        return set()
    best = (max if higher else min)(value for _, value in numeric)
    winners = {name for name, value in numeric if value == best}
    return winners if len(winners) == 1 else set()


def _add_interpretation_navigation(language_root: Path, lang: str) -> None:
    """Expose the optional article only after its page has been generated."""
    label = report_text(lang)("nav.interpretation", "Interpretation")
    for page in language_root.rglob("*.html"):
        route = page.relative_to(language_root)
        if route.parts[0] == "en" or page.name == "article.html":
            continue
        prefix = "../" * (len(route.parts) - 1)
        current = ' aria-current="page"' if route.as_posix() == "interpretation.html" else ""
        link = f'<a data-report-interpretation href="{prefix}interpretation.html"{current}>{label}</a>'
        content = page.read_text(encoding="utf-8")
        page.write_text(content.replace("<nav>", "<nav>" + link, 1), encoding="utf-8")


def _nav(lang: str, active: str, *, depth: int, current_route: str = "index.html") -> str:
    t = report_text(lang)
    labels = (
        ("index", t("nav.summary", "Summary")),
        ("dataset", t("nav.dataset", "Dataset")),
        ("results", t("nav.results", "Results analysis")),
        ("environment", t("nav.environment", "Environment & method")),
        ("review", t("nav.review", "Review history")),
    )
    local_prefix = "../" * depth
    switch = ("../../" if lang == "en" and depth else "../" if lang == "en" else "../en/" if depth else "en/") + current_route
    switch_label = translate(MESSAGES, "nav.other_edition", lang)
    brand = t("nav.brand", "Trajectory Replay")
    theme_label = t("nav.toggle_theme", "Toggle theme")
    links = "".join(
        f'<a href="{local_prefix}{slug}.html"'
        + (' aria-current="page"' if slug == active else "")
        + f">{label}</a>"
        for slug, label in labels
    )
    return f'<header class="top"><a class="brand" href="{local_prefix}index.html">{brand}</a><nav>{links}</nav><a class="language" href="{switch}">{switch_label}</a><button class="theme" type="button" aria-label="{theme_label}">◐</button></header>'


def _shell(lang: str, active: str, title: str, body: str, *, depth: int = 0, current_route: str = "index.html") -> str:
    prefix = "../" * depth
    asset_prefix = prefix if lang == "zh" else "../" + prefix
    footer = report_text(lang)("footer.track", "WebArena-Verified 1.2.3 · Browser Trajectory Replay")
    preview_bootstrap = r'''<script>(function(){
const base=document.querySelector('base[href]');
if(base){
  const parts=location.pathname.split('/');
  const at=parts.indexOf('preview');
  if(at>0&&parts[at-1]==='api'){
    const folders=parts.slice(at+2,-1);
    if(folders.length)base.href=new URL(folders.join('/')+'/',base.href).href;
  }
}
const css=document.querySelector('link[data-report-css]');
css.href=css.dataset.src;
})();</script>'''
    body = body.replace('<img src=', '<img data-src=')
    return f'''<!doctype html><html lang="{lang}"><head><meta name="browser-eval-report" content="1"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)}</title><link rel="stylesheet" data-report-css data-src="{asset_prefix}assets/report.css">{preview_bootstrap}</head><body>{_nav(lang, active, depth=depth, current_route=current_route)}<main>{body}</main><footer>{footer}</footer><script type="application/x-report-app" data-report-app data-src="{asset_prefix}assets/app.js"></script><script>(function(){{document.querySelectorAll('img[data-src]').forEach(img=>img.src=img.dataset.src);const app=document.querySelector('script[data-report-app]');const loader=document.createElement('script');loader.src=app.dataset.src;app.replaceWith(loader)}})();</script></body></html>'''


def _winner(value: str, won: bool, *, lang: str) -> str:
    badge = report_text(lang)("metric.best_badge", "Best")
    return f'<strong class="winner">{value}<span>{badge}</span></strong>' if won else value


def _verdict_label(status: str, semantic_status: str | None, lang: str) -> str:
    t = report_text(lang)
    if semantic_status == "unverified":
        if status == "pass":
            return t("verdict.pass_unverifiable", "Official pass; answer unverifiable")
        return t("verdict.fail_unverifiable", "Official fail; answer unverifiable")
    if status == "pass":
        return t("verdict.pass", "Pass")
    return t("verdict.not_passed", "{status}", status=status.title())


def _metrics_table(
    methods: list[dict[str, Any]], lang: str, *, show_valid_reference: bool = True
) -> str:
    t = report_text(lang)
    pass_winners = _unique_winners(methods, "pass_rate", higher=True)
    valid_winners = _unique_winners(
        [{"id": row["id"], "value": row["valid_reference_passed"]} for row in methods],
        "value", higher=True,
    )
    metric_keys = ["duration", "action_latency"]
    if any(row["cpu"]["n"] for row in methods):
        metric_keys.append("cpu")
    if any(row["memory"]["n"] for row in methods):
        metric_keys.append("memory")
    metric_winners = {
        metric: {
            stat: _unique_winners(
                [{"id": row["id"], "value": row[metric][stat]} for row in methods],
                "value",
                higher=False,
            ) if metric == "action_latency" else set()
            for stat in ("mean", "p50", "p95")
        }
        for metric in metric_keys
    }
    metric_headers = {
        "duration": t("metric.all_tasks_time", "End-to-end time, all tasks"),
        "action_latency": t("metric.action_latency", "Per-action latency"),
        "cpu": t("metric.cpu_time", "CPU time"),
        "memory": t("metric.mean_memory", "Mean memory"),
    }
    headers = [
        t("common.method", "Method"),
        t("metrics.official_passes", "Official passes"),
        t("metrics.official_pass_rate", "Official pass rate"),
        t("metrics.verifiable_passes", "Passes on verifiable tasks"),
        *(metric_headers[key] for key in metric_keys),
    ]
    if not show_valid_reference:
        headers = headers[:3] + headers[4:]
    body = []

    time_value = partial(_format_ms, lang=lang)

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
            _winner(
                f'{row["valid_reference_passed"]}/{row["valid_reference_total"]}',
                key in valid_winners, lang=lang,
            ),
        ]
        for metric in metric_keys:
            formatter = _format_mib if metric == "memory" else time_value
            cells.append(
                _metric_lines(
                    row[metric], formatter, key, metric_winners[metric], lang=lang
                )
            )
        body.append(
            "<tr>"
            + f'<td>{_e(cells[0])}</td>'
            + f'<td data-metric="core-pass" data-metric-direction="higher" data-sort="{row["passed"]}">{cells[1]}</td>'
            + f'<td>{cells[2]}</td>'
            + (
                f'<td data-metric="verifiable-pass" data-metric-direction="higher" data-sort="{row["valid_reference_passed"]}">{cells[3]}</td>'
                if show_valid_reference
                else ""
            )
            + "".join(f"<td>{cell}</td>" for cell in cells[4:])
            + "</tr>"
        )
    return f'<div class="table-scroll"><table class="metrics"><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in headers)}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _resource_table(methods: list[dict[str, Any]], lang: str) -> str:
    t = report_text(lang)
    metrics = (
        ("memory_peak", t("metric.peak_memory", "Peak memory"), _format_mib),
        ("network_rx", t("metric.network_received", "Network received"), _format_mib),
        ("network_tx", t("metric.network_sent", "Network sent"), _format_mib),
        ("cpu_utilization", t("metric.cpu_utilization", "Mean CPU utilization"), lambda value: "—" if value is None else f"{value:.1f}%"),
    )
    rows = []
    for key, label, formatter in metrics:
        if not any(method[key]["n"] for method in methods):
            continue
        cells = [_e(label)]
        for method in methods:
            cells.append(_metric_lines(method[key], formatter, method["id"], {stat: set() for stat in ("mean", "p50", "p95")}, lang=lang))
        rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    if not rows:
        return ""
    headers = (t("metric.resource_column", "Resource metric"), *(method["label"] for method in methods))
    return f'<div class="table-scroll"><table><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in headers)}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def _successful_distribution_table(methods: list[dict[str, Any]], lang: str) -> str:
    """Method-specific successful populations are descriptive, never ranked."""
    t = report_text(lang)
    time_value = partial(_format_ms, lang=lang)
    metrics = (("duration", t("metric.end_to_end_time", "End-to-end time"), time_value),
               ("cpu", t("metric.cpu_time", "CPU time"), time_value),
               ("memory", t("metric.mean_memory", "Mean memory"), _format_mib),
               ("memory_peak", t("metric.peak_memory", "Peak memory"), _format_mib),
               ("network_rx", t("metric.network_received", "Network received"), _format_mib),
               ("network_tx", t("metric.network_sent", "Network sent"), _format_mib),
               ("cpu_utilization", t("metric.cpu_utilization", "Mean CPU utilization"), lambda v: "—" if v is None else f"{v:.1f}%"))
    rows = []
    for name, label, formatter in metrics:
        if name != "duration" and not any(method["successful"][name]["n"] for method in methods):
            continue
        cells = [_e(label)] + [
            _metric_lines(method["successful"][name], formatter, method["id"],
                          {stat: set() for stat in ("mean", "p50", "p95")}, lang=lang)
            for method in methods]
        rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    headers = [t("metric.column", "Metric")] + [
        f'{method["label"]} · n={method["passed"]}/{method["total"]}' for method in methods]
    return '<div class="table-scroll successful-distributions"><table><thead><tr>' + ''.join(f'<th>{_e(h)}</th>' for h in headers) + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'


def _copy_failure_screenshot(attempt: dict[str, Any], output: Path, key: str) -> str | None:
    ref = (attempt.get("evidence") or {}).get("failure_screenshot")
    if not ref or not ref.get("path"):
        return None
    source = Path(str(ref["path"]))
    if (
        not source.is_absolute()
        or any(part.is_symlink() for part in (source, *source.parents))
        or not source.is_file()
        or file_sha256(source) != ref.get("sha256")
    ):
        raise ValueError(f"failure screenshot is missing or changed: {key}")
    target = output / "assets/screenshots" / f"{key}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return f"assets/screenshots/{key}.png"


def _public_evidence(
    attempt: dict[str, Any], review: dict[str, Any] | None, output: Path
) -> dict[str, Any]:
    key = f'{attempt["participant_id"]}-{attempt["task_id"]}'
    trace = _load_artifact((attempt.get("evidence") or {}).get("trace")) or []
    evaluation = _load_artifact((attempt.get("evidence") or {}).get("evaluation"))
    site_reset = _load_artifact((attempt.get("evidence") or {}).get("site_reset"))
    readiness = _load_artifact((attempt.get("evidence") or {}).get("readiness"))
    failed = programmatic_failure(attempt)
    if failed["stage"] == "browser_tool_execution" and failed.get("step") is None:
        failed_trace_step = next(
            (
                step.get("index")
                for step in reversed(trace)
                if step.get("status") in {"fail", "error"}
                and isinstance(step.get("index"), int)
            ),
            None,
        )
        if failed_trace_step is not None:
            failed = {**failed, "step": failed_trace_step}
    if failed["stage"] == "official_evaluation" and evaluation:
        missing_events = [
            result for result in evaluation.get("evaluators_results") or []
            if result.get("evaluator_name") == "NetworkEventEvaluator"
            and any(
                assertion.get("assertion_name") == "missing_navigation_event"
                for assertion in result.get("assertions") or []
            )
        ]
        if missing_events:
            assertion_messages = [
                str(message)
                for result in missing_events
                for assertion in result.get("assertions") or []
                if assertion.get("assertion_name") == "missing_navigation_event"
                for message in assertion.get("assertion_msgs") or []
            ]
            trigger = next(
                (
                    step for step in reversed(trace)
                    if (step.get("arguments") or {}).get("action") in {"click", "press", "evaluate"}
                ),
                None,
            )
            failed = {
                "stage": "side_effect_not_observed",
                "step": None,
                "trigger_step": trigger.get("index") if trigger else None,
                "message": "NetworkEventEvaluator: missing_navigation_event" + (" — " + " | ".join(assertion_messages) if assertion_messages else ""),
                "expected_network": [result.get("expected") for result in missing_events],
            }
    screenshot = _copy_failure_screenshot(attempt, output, key)
    public = {
        "result": {
            "participant_id": attempt["participant_id"],
            "engine": attempt["engine"],
            "task_id": attempt["task_id"],
            "status": attempt["status"],
            "action_count": attempt["action_count"],
            "planned_actions_sha256": attempt.get("planned_actions_sha256"),
            "task_contract_sha256": attempt.get("task_contract_sha256"),
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
                **{key: review["cause_group"][key] for key in (
                    "remediation_owner_zh", "remediation_owner_en",
                    "remediation_status_zh", "remediation_status_en",
                ) if key in review["cause_group"]},
                **({"revision_evidence": review["revision_evidence"]}
                   if review.get("revision_evidence") else {}),
            }
            if review
            else None
        ),
        "trajectory": trace,
        "official_evaluation": evaluation,
        "site_reset": site_reset,
        "readiness": readiness,
        "reference_validity": attempt.get("reference_validity"),
        "supplemental_diagnostic": _supplemental_diagnostic(review, int(attempt["task_id"])),
        "screenshot": screenshot,
    }
    public = _clean(public)
    _write_clean_json(output / "data/evidence" / f"{key}.json", public)
    return public


def _detail_page(
    lang: str,
    attempt: dict[str, Any],
    task: dict[str, Any],
    method: dict[str, Any],
    evidence: dict[str, Any],
    peers: list[dict[str, Any]],
    validity_notice: str = "",
) -> str:
    failed = evidence["programmatic_failure"]
    review = evidence.get("review")
    zh = lang == "zh"
    t = report_text(lang)
    semantic_status = (
        "unverified" if (attempt.get("reference_validity") or {}).get("status") == "unverifiable" else "valid"
    )
    status = _verdict_label(attempt["status"], semantic_status, lang)
    reason = (review.get("reason_zh") if zh else review.get("reason_en")) if review else ""
    label = (review.get("label_zh") if zh else review.get("label_en")) if review else ""
    key = f'{attempt["participant_id"]}-{attempt["task_id"]}'
    report_root = "../" if zh else "../../"
    stage_labels = {
        "browser_session_setup": t("stage.browser_session_setup", "Browser session setup"),
        "browser_tool_execution": t("stage.browser_action_execution", "Browser action execution"),
        "evidence_finalization": t("stage.evidence_finalization", "Evidence finalization"),
        "official_evaluator_execution": t("stage.official_evaluator_execution", "Official evaluator execution"),
        "side_effect_not_observed": t("stage.side_effect_not_observed", "Network acceptance not satisfied"),
        "official_evaluation": t("stage.official_evaluation", "Official evaluation"),
        "pass": t("stage.none", "None"),
    }
    stage = stage_labels.get(failed["stage"], failed["stage"])
    failed_index = failed.get("step")
    trigger_index = failed.get("trigger_step")
    failed_action = next((str((step.get("arguments") or {}).get("action") or "") for step in evidence["trajectory"] if step.get("index") == failed_index), "")
    peer_cells = []
    for peer in peers:
        peer_key = f'{peer["participant_id"]}-{peer["task_id"]}'
        peer_status = _verdict_label(peer["status"], peer.get("semantic_status"), lang)
        peer_href = "../" + peer["detail_url"] if peer.get("detail_url") else f"{peer_key}.html"
        peer_cells.append(
            f'<a class="peer {peer["status"]}" href="{_e(peer_href)}"'
            + (' aria-current="page"' if peer_key == key else '')
            + f'><span>{_e(peer["browser"])}</span><strong>{peer_status}</strong></a>'
        )
    # A watchdog can preserve a task-level timeout when an older runner did not
    # checkpoint the active action. Keep that evidence without pretending it
    # belongs to a numbered trajectory step.
    actual_by_index = {
        index: step
        for step in evidence["trajectory"]
        if isinstance((index := step.get("index")), int) and not isinstance(index, bool)
    }
    steps = []
    for number, planned in enumerate(attempt["planned_actions"], 1):
        actual = actual_by_index.get(number)
        planned_target = planned.get("selector") or planned.get("url") or planned.get("key") or ""
        actual_arguments = (actual or {}).get("arguments") or {}
        actual_target = actual_arguments.get("selector") or actual_arguments.get("url") or actual_arguments.get("key") or ""
        actual_result = str((actual or {}).get("result") or "")
        preview = actual_result.splitlines()[0][:160] if actual_result else ""
        failed_step = failed_index == number
        actual_status = str((actual or {}).get("status") or "")
        status_text = (
            t("result.step.not_executed", "Not executed") if actual is None
            else t("result.step.error", "Error") if actual_status == "error"
            else t("result.step.completed", "Completed")
        )
        status_class = "status-muted" if actual is None else "status-fail" if actual_status == "error" else "status-pass"
        duration = f'{float(actual.get("duration_ms") or 0) / 1000:.2f} s' if actual else "—"
        steps.append(
            f'<article id="step-{number}" class="aligned-step {"failed-step" if failed_step else ""}">'
            f'<div class="aligned-index"><span class="step-number">{number}</span></div>'
            f'<div class="aligned-standard"><span class="mobile-step-label">{t("result.step.fixed_input", "Fixed input")}</span><strong>{_e(planned.get("action") or "")}</strong>'
            f'<code>{_e(planned_target)}</code><button type="button" data-open-step="{number}">'
            f'{t("result.step.fixed_arguments", "Fixed arguments")}</button></div>'
            f'<div class="aligned-actual"><span class="mobile-step-label">{t("result.actual_execution", "Actual execution")}</span><div class="aligned-actual-head"><strong>{_e(actual_arguments.get("action") or "—")}</strong>'
            f'<span class="{status_class}">{status_text}</span><span>{_e(duration)}</span></div>'
            f'<code>{_e(actual_target) if actual else "—"}</code>'
            f'<p>{_e(preview) if preview else (t("result.step.no_tool_response", "No tool response"))}</p>'
            f'<button type="button" data-open-step="{number}">'
            f'{t("result.step.actual_arguments", "Actual arguments, logs and error")}</button></div></article>'
        )
    screenshot = (
        f'<figure><a href="{report_root}{evidence["screenshot"]}"><img src="{report_root}{evidence["screenshot"]}" alt="{t("result.screenshot.alt", "Last page after failure")}"></a><figcaption>{t("result.screenshot.caption", "Last page saved after failure, not necessarily the error instant. Open the full image.")}</figcaption></figure>'
        if evidence.get("screenshot")
        else ""
    )
    failure_panel = ""
    if attempt["status"] != "pass":
        unknown_active_action = (
            failed_index is None
            and trigger_index is None
            and int(attempt.get("completed_action_count") or 0)
            < int(attempt.get("action_count") or 0)
        )
        step_link = f'<a href="#step-{failed_index}">{t("result.diagnosis.go_to_failed_action", "Go to failed action")} #{failed_index}</a>' if failed_index else f'<a href="#step-{trigger_index}">{t("result.diagnosis.inspect_trigger_action", "Inspect trigger action")} #{trigger_index}</a>' if trigger_index else f'<a href="#trajectory">{t("result.diagnosis.view_trajectory", "View saved trajectory evidence")}</a>'
        action_locator = f'{failed_action} #{failed_index}' if failed_index else ((f'{(t("result.diagnosis.trigger_action", "Trigger action"))} #{trigger_index}; {(t("result.diagnosis.request_unmet", "request does not meet acceptance"))}') if trigger_index else ((t("result.diagnosis.active_action_unrecorded", "Active action not recorded")) if unknown_active_action else (t("result.diagnosis.after_all_actions", "After all actions"))))
        expected_network = f'<h3>{t("result.diagnosis.required_network_match", "Required network match")}</h3><pre>{_e(json.dumps(failed["expected_network"], ensure_ascii=False, indent=2))}</pre>' if failed.get("expected_network") else ""
        failure_panel = f'''<section id="diagnosis"><h2>{t("result.diagnosis.heading", "Failure diagnosis and treatment")}</h2><div class="diagnosis-panel"><div class="diagnosis-facts"><div><span>{t("common.failure_stage", "Failure stage")}</span><strong>{_e(stage)}</strong></div><div><span>{t("result.diagnosis.failure_location", "Failure location")}</span><strong>{_e(action_locator)}</strong></div><div><span>{t("common.reviewed_category", "Reviewed category")}</span><strong>{_e(label)}</strong></div></div><h3>{t("result.diagnosis.programmatic_finding", "Exact programmatic finding")}</h3><pre>{_e(failed["message"])}</pre>{expected_network}<h3>{t("result.diagnosis.reviewed_cause", "Reviewed cause and basis")}</h3><p>{_e(reason)}</p><p>{t("result.diagnosis.owner", "Owner")}: {_e(review.get(f"remediation_owner_{lang}", review.get("remediation_owner")) if review else "—")} · {t("result.diagnosis.status", "Status")}: {_e(review.get(f"remediation_status_{lang}", review.get("remediation_status")) if review else "—")}</p>{step_link}</div></section>'''
    failure_panel += _supplemental_diagnostic_html(evidence.get("supplemental_diagnostic"), lang)
    # Chinese shows an absent duration or CPU sample as a dash; English shows zero.
    duration_ms = attempt.get("duration_ms") if zh else attempt.get("duration_ms", 0)
    cpu_ms = (attempt.get("resource") or {}).get("cpu_ms")
    facts = [
        (t("result.facts.actions_completed", "Actions completed"), f'{attempt["completed_action_count"]}/{attempt["action_count"]}'),
        (t("metric.end_to_end_time", "End-to-end time"), _format_ms(duration_ms, lang)),
        (t("metric.cpu_time", "CPU time"), _format_ms(cpu_ms if zh else cpu_ms or 0, lang)),
        (t("metric.mean_memory", "Mean memory"), _format_mib((attempt.get("resource") or {}).get("memory_mean_bytes"))),
    ]
    fact_html = "".join(f'<div><span>{_e(label)}</span><strong>{_e(value)}</strong></div>' for label, value in facts)
    validity = attempt.get("reference_validity") or {}
    validity_reason = validity.get("reason_zh" if zh else "reason_en") or validity.get("reason") or ""
    validity_panel = (
        f'<section class="callout"><h2>{t("result.validity.heading", "Reference answer validity")}</h2>'
        f'<p>{_e(_clean(validity_reason)) if validity_reason else (t("result.validity.default_reason", "Site evidence cannot uniquely establish the answer; an official pass does not prove task correctness."))}</p></section>'
        if semantic_status == "unverified" else ""
    )
    viewer_file = ("data/evidence/" if zh else "../data/evidence/") + key + ".json"
    body = f'''
    <section class="detail-hero"><p class="eyebrow">{_e(key)}</p><h1>{_e(method["label"])} · {t("common.task", "Task")} #{attempt["task_id"]}</h1><p class="task-intent">{_e(task["task"]["intent"])}</p><div class="detail-links"><a href="../task/{attempt["task_id"]}.html">{t("result.link.task_contract", "Task contract and fixed trajectory")}</a><a href="../results.html">{t("result.link.back_to_results", "Back to results")}</a></div></section>
    <section class="compact-section"><h2>{t("result.peers.heading", "All method results for this task")}</h2><div class="peer-list">{''.join(peer_cells)}</div></section>
    <section class="compact-section"><div class="verdict-line {attempt["status"]}"><strong>{status}</strong><span>{_e(stage) if attempt["status"] != "pass" else (t("result.verdict.passed_note", "Fixed trajectory and official evaluation passed"))}</span></div><div class="fact-strip">{fact_html}</div></section>
    {validity_panel}
    {failure_panel}
    <section id="trajectory"><h2>{t("result.trajectory.heading", "This method's trajectory and execution")}</h2><p>{t("result.trajectory.lead", "The left column is this method's frozen input and the right column its execution. Qualified paths can differ between methods. Open a step for exact arguments, tool response, logs and errors.")}</p><div class="trajectory-columns"><span>#</span><strong>{t("result.trajectory.method_input", "Method input")}</strong><strong>{t("result.actual_execution", "Actual execution")}</strong></div><div class="trace-list">{''.join(steps)}</div></section>
    <dialog id="step-dialog" data-evidence-url="{report_root}data/evidence/{key}.json" data-standard-url="{report_root}data/trajectory/{key}.json"><div class="dialog-head"><strong data-step-title></strong><button type="button" data-close-step aria-label="{t('result.dialog.close', 'Close step evidence')}">×</button></div><div class="dialog-body"><p data-step-status></p><div class="step-dialog-grid"><div><h3>{t("result.dialog.method_input_arguments", "Method input arguments")}</h3><pre data-step-planned></pre></div><div><h3>{t("result.dialog.actual_arguments", "Actual action arguments")}</h3><pre data-step-actual></pre></div></div><h3>{t("result.dialog.tool_response", "Tool response, logs and error")}</h3><pre data-step-result></pre><div data-step-finding hidden><h3>{t("result.dialog.programmatic_failure", "Exact programmatic failure")}</h3><pre data-step-error></pre></div><p data-step-image-wrap hidden><a data-step-image target="_blank" rel="noopener">{t("result.dialog.last_observed_page", "Open last observed page (not a screenshot of this step)")}</a></p><p><a href="../evidence-viewer.html?file={viewer_file}">{t("result.dialog.complete_evidence", "Inspect complete execution evidence")}</a></p></div></dialog>
    <section id="evidence"><h2>{t("result.evidence.heading", "Evaluation, resources, and page evidence")}</h2><div class="evidence-actions"><a href="../evidence-viewer.html?file={viewer_file}">{t("result.evidence.inspect_in_browser", "Inspect complete evidence in the browser")}</a><a href="{report_root}data/evidence/{key}.json">{t("common.download_sanitized_json", "Download sanitized JSON")}</a></div>{screenshot}</section>'''
    title = (
        t("result.title", "{method} · Task #{task}", method=method['label'], task=attempt['task_id'])
    )
    return _shell(lang, "results", title, validity_notice + body, depth=1, current_route=f"result/{key}.html")


def _task_page(
    lang: str, task: dict[str, Any], peers: list[dict[str, Any]], validity_notice: str = "",
    reference_validity: dict[str, Any] | None = None,
    corrected_acceptance: dict[str, Any] | None = None,
) -> str:
    t = report_text(lang)
    task_id = int(task["task_id"])
    source_kind = str((task.get("source") or {}).get("kind") or "")
    source_label = (
        t("task.source.autonomous", "From a successful autonomous browser run")
        if source_kind == "authoritative_successful_browser_use_execution"
        else t("task.source.repaired", "Repaired and checked on the reference browser under the recorded protocol")
    )
    peer_rows = "".join(
        f'<tr><td><a href="../{_e(peer.get("detail_url") or "result/" + peer["participant_id"] + "-" + str(task_id) + ".html")}">{_e(peer["browser"])}</a></td>'
        f'<td class="status-{peer["status"]}">{_verdict_label(peer["status"], peer.get("semantic_status"), lang)}</td>'
        f'<td>{peer["completed_actions"]}/{peer["action_count"]}</td>'
        f'<td><a href="../{_e(peer.get("detail_url") or "result/" + peer["participant_id"] + "-" + str(task_id) + ".html")}{("#diagnosis" if peer["status"] != "pass" else "#trajectory")}">{t("task.peers.evidence_link", "Diagnosis and executed actions")}</a></td></tr>'
        for peer in peers
    )
    planned_rows = []
    for index, call in enumerate(task.get("tool_calls") or [], 1):
        action = str(call.get("action") or "")
        target = str(call.get("selector") or call.get("url") or call.get("key") or "")
        planned_rows.append(
            f'<tr><td>{index}</td><td><code>{_e(action)}</code></td><td><code>{_e(target)}</code></td>'
            f'<td><details><summary>{t("task.planned.full_arguments", "Full arguments")}</summary><pre>{_e(json.dumps(_clean(call), ensure_ascii=False, indent=2))}</pre></details></td></tr>'
        )
    contract = task.get("evaluation_input") or {}
    correction_panel = ""
    if corrected_acceptance:
        correction_panel = (
            '<section data-corrected-acceptance><h2>'
            + (t("task.correction.heading", "Acceptance used in this comparison"))
            + '</h2><p>'
            + (t("task.correction.lead", "These corrected acceptance fields come from the saved evaluator evidence, normalized by the evaluator. The historical dataset input is retained below for comparison."))
            + '</p><pre>'
            + _e(json.dumps(_clean(corrected_acceptance), ensure_ascii=False, indent=2))
            + '</pre></section>'
        )
    contract_type = str(contract.get("task_type") or "")
    contract_summary = _task_type_label(contract_type, lang) or "—"
    validity_panel = (
        f'<section class="callout"><h2>{t("task.validity.heading", "Answer cannot be independently verified")}</h2>'
        f'<p>{t("task.validity.body", "Site evidence does not uniquely establish this task’s reference answer. Official evaluator scores are separate from answer validity.")}</p></section>'
        if (reference_validity or {}).get("status") == "unverifiable" else ""
    )
    body = f'''<section class="detail-hero"><p class="eyebrow">WebArena-Verified · #{task_id}</p><h1>{t("task.heading", "Task contract and fixed trajectory")}</h1><p class="task-intent">{_e(task["task"]["intent"])}</p><div class="detail-links"><a href="../dataset.html">{t("task.link.back_to_dataset", "Back to dataset")}</a><a href="../results.html">{t("task.link.measured_results", "Explore measured results")}</a></div></section>
        {validity_panel}
        {correction_panel}
        <section class="compact-section"><div class="fact-strip"><div><span>{t("task.facts.sites", "Sites")}</span><strong>{_e(", ".join(task["task"].get("sites") or []))}</strong></div><div><span>{t("common.trajectory_source", "Trajectory source")}</span><strong>{source_label}</strong></div><div><span>{t("task.facts.planned_actions", "Planned actions")}</span><strong>{len(task.get("tool_calls") or [])}</strong></div><div><span>{t("common.task_type", "Task type")}</span><strong>{contract_summary}</strong></div></div></section>
    <section><h2>{t("task.peers.heading", "All method outcomes on this task")}</h2><div class="table-scroll"><table class="task-peer-table"><thead><tr><th>{t("common.method", "Method")}</th><th>{t("common.verdict", "Verdict")}</th><th>{t("task.peers.completed_actions", "Completed actions")}</th><th>{t("common.evidence", "Evidence")}</th></tr></thead><tbody>{peer_rows}</tbody></table></div></section>
    <section><h2>{t("task.reference.heading", "Dataset reference path")}</h2><p>{t("task.reference.lead", "This is the dataset reference path. Each method's frozen input and actual execution appear on its result page.")}</p><div class="table-scroll"><table class="planned-table"><thead><tr><th>#</th><th>{t("common.action", "Action")}</th><th>{t("common.target", "Target")}</th><th>{t("task.reference.arguments", "Arguments")}</th></tr></thead><tbody>{''.join(planned_rows)}</tbody></table></div></section>
    <section><h2>{(t("task.evaluation.historical_heading", "Historical dataset evaluation input")) if corrected_acceptance else (t("task.evaluation.heading", "Evaluation input"))}</h2><p>{(t("task.evaluation.historical_lead", "Retained fields before correction; this comparison uses the corrected acceptance above.")) if corrected_acceptance else (t("task.evaluation.lead", "Used by the official evaluator, not an Agent prompt or the only correct path."))}</p><details><summary>{t("task.evaluation.inspect", "Inspect answer and acceptance fields")}</summary><pre>{_e(json.dumps(_clean(contract), ensure_ascii=False, indent=2))}</pre></details></section>'''
    title = t("task.title", "Task #{task} · Dataset", task=task_id)
    return _shell(lang, "dataset", title, validity_notice + body, depth=1, current_route=f"task/{task_id}.html")


def _assets(output: Path) -> None:
    (output / "assets").mkdir(parents=True, exist_ok=True)
    (output / "assets/report.css").write_text(REPORT_CSS, encoding="utf-8")
    (output / "assets/app.js").write_text(REPORT_JS, encoding="utf-8")


def build_webarena_replay_report(
    *,
    results_path: str | Path,
    dataset_path: str | Path,
    review_path: str | Path,
    profile_path: str | Path,
    output_dir: str | Path,
    diagnostic_closure_path: str | Path | None = None,
    interpretation_path: str | Path | None = None,
    interpretation_audit_path: str | Path | None = None,
    include_english: bool = False,
    language: str | None = None,
) -> dict[str, Any]:
    include_english = includes_english(language, include_english)
    languages = ("zh", "en") if include_english else ("zh",)
    profile_source = Path(profile_path)
    profile = json.loads(profile_source.read_text(encoding="utf-8"))
    methods = participant_registry(profile["methods"])
    if len(methods) < 2 or profile.get("affected_task_methods"):
        raise ValueError("current report requires complete registered methods")
    execution_backend = profile.get("execution_backend")
    if execution_backend is not None and (
        execution_backend != "playwright"
        or any(method.get("backend") != execution_backend for method in profile["methods"])
    ):
        raise ValueError("replay report requires one Playwright backend for every method")
    execution = profile.get("execution") or {}
    if (
        execution.get("method_parallelism") != 1
        or execution.get("task_parallelism") != 1
        or execution.get("reset_before_every_task") is not False
        or execution.get("reset_before_every_method") is not True
        or execution.get("teardown_between_methods") is not True
        or execution.get("retry_failed_task") is not False
    ):
        raise ValueError("replay report requires the declared common execution protocol")
    if diagnostic_closure_path is not None:
        raise ValueError("diagnostic-only cohorts cannot enter the replay report")
    validated = load_validated_failure_review(results_path, review_path, methods=profile["methods"])
    results = validated["results"]
    reviews = validated["by_result"]
    results_source = Path(results_path)
    dataset_source = Path(dataset_path)
    dataset = json.loads(dataset_source.read_text(encoding="utf-8"))
    if results.get("dataset_sha256") != file_sha256(dataset_source):
        raise ValueError("aggregate is not bound to the selected corrected dataset")
    validity_identity = _measured_validity_identity(results, results_source, dataset_source)
    legacy_key = (
        validity_identity["results_sha256"], validity_identity["dataset_sha256"],
        validity_identity["profile_sha256"],
    )
    legacy_provenance = (
        _legacy_retrieval_provenance(results, dataset)
        if legacy_key in LEGACY_REPLAY_VALIDITY_INPUTS else None
    )
    output = Path(output_dir).resolve()
    tasks = {int(item["task_id"]): item for item in dataset["trajectories"]}
    attempts = [bind_participant(row, methods) for row in results["attempts"]]
    results = {**results, "attempts": attempts, "physical_executions": [
        bind_participant(row, methods) for row in results["physical_executions"]
    ]}
    if {(item["participant_id"], int(item["task_id"])) for item in attempts} != {
        (engine, task_id) for engine in methods for task_id in tasks
    } or len(attempts) != len(methods) * len(tasks):
        raise ValueError("report requires the complete participant-task matrix")
    for attempt in attempts:
        task_id = int(attempt["task_id"])
        task = tasks[task_id]
        contract_sha256 = canonical_json_sha256({
            "task": task["task"], "evaluation_input": task["evaluation_input"],
        })
        if attempt.get("task_contract_sha256") != contract_sha256:
            raise ValueError(
                f"task {task_id} result was scored under another task contract"
            )
    task_validity = _reference_validity_by_task(attempts)
    require_all_reference_validity(profile, results, dataset)
    validated_actions = {
        (attempt["participant_id"], int(attempt["task_id"])): _validated_planned_actions(attempt)
        for attempt in attempts
    }
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    _assets(output)
    if legacy_provenance is not None:
        _write_clean_json(output / "data/legacy-retrieval-provenance.json", legacy_provenance)
    corrected_acceptance = _corrected_acceptance_by_task(
        attempts, declared_revisions=profile.get("acceptance_revisions"),
    )
    for task_id, task in tasks.items():
        _write_clean_json(output / "data/trajectory" / f"{task_id}.json", {
            "task_id": task_id,
            "tool_calls": task.get("tool_calls") or [],
        })
    for attempt in attempts:
        actions = validated_actions[(attempt["participant_id"], int(attempt["task_id"]))]
        key = f'{attempt["participant_id"]}-{attempt["task_id"]}'
        _write_clean_json(output / "data/trajectory" / f"{key}.json", {
            "task_id": int(attempt["task_id"]),
            "participant_id": attempt["participant_id"],
            "planned_actions_sha256": attempt["planned_actions_sha256"],
            "tool_calls": actions,
        })
    comparison_methods = methods

    action_by_engine: dict[str, list[float]] = defaultdict(list)
    action_by_name: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for item in results["physical_executions"]:
        key = (str(item["participant_id"]), int(item["task_id"]))
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
                item for item in attempts if item["participant_id"] == engine and item["task_id"] == task_id
            )["status"]
            == "pass"
            for engine in methods
        )
    }
    method_rows = []
    for engine, method in methods.items():
        rows = [item for item in attempts if item["participant_id"] == engine]
        passes = sum(item["status"] == "pass" for item in rows)
        distributions = _outcome_metrics(rows)
        successful = _outcome_metrics([item for item in rows if item["status"] == "pass"])
        duration = distributions["duration"]
        action_latency = _stat(action_by_engine[engine])
        cpu = distributions["cpu"]
        memory = distributions["memory"]
        method_rows.append(
            {
                "id": method["id"],
                "participant_id": engine,
                "engine": method["engine"],
                "label": method["label"],
                "passed": passes,
                "valid_reference_passed": sum(
                    item["status"] == "pass" and task_validity[int(item["task_id"])] == "valid"
                    for item in rows
                ),
                "valid_reference_total": sum(
                    task_validity[int(item["task_id"])] == "valid" for item in rows
                ),
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
                "memory_peak": distributions["memory_peak"],
                "network_rx": distributions["network_rx"],
                "network_tx": distributions["network_tx"],
                "cpu_utilization": distributions["cpu_utilization"],
                "successful": successful,
                "physical_executions": len(
                    [item for item in results["physical_executions"] if item["participant_id"] == engine]
                ),
            }
        )

    public_rows = []
    public_evidence: dict[tuple[str, int], dict[str, Any]] = {}
    for attempt in attempts:
        key = (attempt["participant_id"], int(attempt["task_id"]))
        task = tasks[key[1]]
        review = reviews.get(key)
        evidence = _public_evidence(attempt, review, output)
        public_evidence[key] = evidence
        failure = evidence["programmatic_failure"]
        cause = review["cause_group"] if review else {}
        row = {
            "key": f"{key[0]}-{key[1]}",
            "participant_id": key[0],
            "engine": attempt["engine"],
            "browser": methods[key[0]]["label"],
            "task_id": key[1],
            "intent": task["task"]["intent"],
            "sites": task["task"].get("sites") or [],
            "status": attempt["status"],
            "task_contract_sha256": attempt["task_contract_sha256"],
            "semantic_status": (
                "unverified" if task_validity[key[1]] == "unverifiable" else "valid"
            ),
            "duration_ms": attempt["duration_ms"],
            "completed_actions": attempt["completed_action_count"],
            "action_count": attempt["action_count"],
            "failure_stage": failure["stage"],
            "program_error": failure["message"],
            "cause_zh": cause.get("label_zh", ""),
            "cause_en": cause.get("label_en", ""),
            "cause_code": cause.get("code", ""),
            "reason_zh": review.get("reason_zh", "") if review else "",
            "reason_en": review.get("reason_en", "") if review else "",
        }
        public_rows.append(row)
    rows_by_task = defaultdict(list)
    for row in public_rows:
        rows_by_task[int(row["task_id"])].append(row)
    for attempt in attempts:
        key = (str(attempt["participant_id"]), int(attempt["task_id"]))
        row = next(item for item in rows_by_task[key[1]] if item["participant_id"] == key[0])
        for lang in languages:
            target = output / ("result" if lang == "zh" else "en/result") / f"{row['key']}.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                _detail_page(lang, attempt, tasks[key[1]], methods[key[0]], public_evidence[key], rows_by_task[key[1]], _historical_replay_validity_notice(**validity_identity, lang=lang)), encoding="utf-8"
            )
    for task_id, task in tasks.items():
        for lang in languages:
            target = output / ("task" if lang == "zh" else "en/task") / f"{task_id}.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            matching_attempt = next(item for item in attempts if int(item["task_id"]) == task_id)
            target.write_text(_task_page(lang, task, rows_by_task[task_id], _historical_replay_validity_notice(**validity_identity, lang=lang), matching_attempt["reference_validity"], corrected_acceptance.get(task_id)), encoding="utf-8")

    _write_clean_json(output / "data/results.json", public_rows)
    _write_clean_json(output / "data/metrics.json", {
        "methods": method_rows,
        "distribution_scope": "all_declared_tasks",
        "successful_scope": "per_method_success",
        "passing_intersection": len(passing_tasks),
        "passing_intersection_scope": "diagnostic_only",
    })

    source_counts: dict[str, int] = defaultdict(int)
    for task in tasks.values():
        source_counts[str((task.get("source") or {}).get("kind") or "unknown")] += 1
    browser_use_source_count = source_counts["authoritative_successful_browser_use_execution"]
    repaired_source_count = source_counts["expert_repair_spec"]
    reference_engine = str((profile.get("qualification") or {}).get("reference_participant_id") or "")
    if not reference_engine:
        reference_engine = resolve_participant_id(
            {"engine": (profile.get("qualification") or {}).get("reference_engine")}, methods
        )
    reference_method = methods.get(reference_engine)
    reference_row = next(
        (item for item in method_rows if item["participant_id"] == reference_engine),
        None,
    )
    if reference_method is None or reference_row is None:
        raise ValueError("profile qualification reference participant must be registered")
    reference_label = _e(reference_method["label"])
    method_count = len(methods)
    browser_count = len({method["engine"] for method in methods.values()})
    reviewed_failure_count = len(reviews)
    unverifiable_ids = sorted(task_id for task_id, status in task_validity.items() if status == "unverifiable")
    valid_reference_count = len(tasks) - len(unverifiable_ids)

    for lang in languages:
        zh = lang == "zh"
        t = report_text(lang)
        time_value = partial(_format_ms, lang=lang)
        root = output if zh else output / "en"
        root.mkdir(exist_ok=True)
        action_headers = [t("common.action", "Action"), *[item["label"] for item in methods.values()]]
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
                    time_value,
                    methods[engine]["id"],
                    action_winners,
                    lang=lang,
                )
                calls_label = t("results.action_latency.calls", "calls")
                cells.append(f"<td>{text}<br><small>{metric['n']} {calls_label}</small></td>")
            action_rows.append(f'<tr><td><code>{_e(action)}</code></td>{"".join(cells)}</tr>')
        action_table = f'<div class="table-scroll"><table><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in action_headers)}</tr></thead><tbody>{"".join(action_rows)}</tbody></table></div>'
        task_count = len(tasks)
        eyebrow = (
            t("home.eyebrow", "WebArena-Verified · {tasks} tasks · model calls 0", tasks=task_count)
        )
        backend_note = (
            (t("home.backend.playwright", "All methods use the same Playwright replay adapter for frozen actions, with no Agent decisions or model calls."))
            if execution_backend == "playwright" else
            (t("home.backend.undeclared", "All methods replay frozen actions without Agent decisions or model calls. The historical profile does not declare an execution backend."))
        )
        failure_participants = tuple(
            engine
            for engine in methods
            if any(
                row["participant_id"] == engine and row["status"] != "pass"
                for row in public_rows
            )
        )
        failure_signatures = (
            failure_signature_cards(
                methods, public_rows, lang, participants=failure_participants
            )
            if failure_participants
            else ""
        )
        validity_notice = _historical_replay_validity_notice(
            **validity_identity, lang=lang
        )
        unverifiable_links = ", ".join(
            f'<a href="task/{task_id}.html">#{task_id}</a>' for task_id in unverifiable_ids
        ) or "—"
        if unverifiable_ids:
            core_metrics_note = (
                t("home.core_note.with_unverifiable", "All methods cover the same {tasks} tasks. Official passes use all {tasks}; verifiable-task passes use the {verifiable} tasks whose answers are supported by site evidence. Unverifiable tasks: {links}. ", tasks=task_count, verifiable=valid_reference_count, links=unverifiable_links)
            )
        else:
            core_metrics_note = (
                t("home.core_note.all_verifiable", "All methods cover the same {tasks} tasks. Task details identify acceptance rules and declared corrections; the review page states the independent review scope. ", tasks=task_count)
            )
        has_resource_samples = any(
            row[metric]["n"]
            for row in method_rows
            for metric in ("cpu", "memory", "memory_peak", "network_rx", "network_tx", "cpu_utilization")
        )
        core_metrics_note += (
            t("home.core_note.ranking_with_resources", "Early failures reduce work, so total time and resources are not ranked by lower values; per-action latency uses actual browser action calls.")
            if has_resource_samples
            else t("home.core_note.ranking_without_resources", "Early failures reduce work, so total time is not ranked by lower values; per-action latency uses actual browser action calls.")
        )
        core_metrics_table = _metrics_table(
            method_rows, lang, show_valid_reference=bool(unverifiable_ids)
        )
        measurement_note = (profile.get("measurement_notes") or {}).get(lang)
        if measurement_note:
            core_metrics_note += " " + _e(measurement_note)
        resource_table = _resource_table(method_rows, lang)
        resource_section = (
            f'<section><h2>{t("home.resources.heading", "Resources and network")}</h2><p>{t("home.resources.lead", "These per-task samples cover all {tasks} tasks. Each n counts observed values; missing samples are not zero, and measured zeroes are retained. Network bytes and CPU utilization are not ranked: lower values may reflect early failure, caching, or waiting.", tasks=task_count)}</p><details class="matrix-disclosure"><summary>{t("home.resources.open", "Open resource and network statistics")}</summary>{resource_table}</details></section>'
            if resource_table
            else f'<section><h2>{t("home.resources.unavailable_heading", "Resource data")}</h2><p class="callout">{t("home.resources.unavailable", "This run did not record process-level CPU, memory, or network samples. Those metrics are unavailable, so the report does not show empty tables or resource rankings. End-to-end and action latency come from the retained trajectories and require no rerun.")}</p></section>'
        )
        summary_body = f'''<section class="hero"><p class="eyebrow">{eyebrow}</p><h1>{t("home.heading", "Browser Trajectory Replay")}</h1><p>{t("home.lead", "{browsers} browsers form {methods} method configurations, each covering the same {tasks} frozen trajectory replays. {backend_note} Environment documents versions, paths and site preparation.", browsers=browser_count, methods=method_count, tasks=task_count, backend_note=backend_note)}</p><div class="detail-links"><a href="results.html">{t("home.link.outcomes", "Explore outcomes and exact errors")}</a><a href="dataset.html">{t("home.link.dataset", "Task contracts and fixed trajectories")}</a></div></section>
        {validity_notice}
        {f'<p class="evidence-actions"><a href="{"" if zh else "../"}data/legacy-retrieval-provenance.json">{t("home.link.legacy_provenance", "Inspect hashes for all 74 historical retrieval inputs")}</a></p>' if legacy_provenance is not None else ''}
        <section><h2>{t("home.core_metrics", "Core metrics")}</h2><p class="callout">{core_metrics_note}</p>{core_metrics_table}</section>
        {resource_section}
        {failure_signatures}'''
        (root / "index.html").write_text(_shell(lang, "index", t("home.title", "Browser Trajectory Replay"), summary_body, current_route="index.html"), encoding="utf-8")

        headers = [t("common.task", "Task"), t("common.site", "Site"), t("dataset.column.goal", "Task goal"), t("dataset.column.type", "Type"), t("common.trajectory_source", "Trajectory source"), t("dataset.column.fixed_actions", "Fixed actions"), t("dataset.column.drill_down", "Drill down")]
        matrix_rows = []
        for task_id, task in tasks.items():
            source = str((task.get("source") or {}).get("kind") or "")
            source_code = "autonomous" if source == "authoritative_successful_browser_use_execution" else "repaired"
            source_text = (t("dataset.source.autonomous", "Autonomous success")) if source_code == "autonomous" else (t("dataset.source.repaired", "Repaired and checked"))
            task_type = str((task.get("evaluation_input") or {}).get("task_type") or "")
            type_text = _task_type_label(task_type, lang) or "—"
            sites = task["task"].get("sites") or []
            intent = str(task["task"].get("intent") or "")
            action_count = len(task.get("tool_calls") or [])
            search = _e(f"{task_id} {', '.join(sites)} {intent} {task_type} {source_text}")
            matrix_rows.append(f'<tr data-task="{task_id}" data-sites="{_e(" ".join(sites))}" data-type="{_e(task_type)}" data-source="{source_code}" data-search="{search}"><td><a href="task/{task_id}.html">#{task_id}</a></td><td>{_e(", ".join(sites))}</td><td><div class="intent-preview">{_e(intent)}</div></td><td>{_e(type_text)}</td><td>{source_text}</td><td>{action_count}</td><td><div class="dataset-actions"><a href="task/{task_id}.html">{t("dataset.link.task", "Task & trajectory")}</a><a href="results.html?task={task_id}&status=">{t("dataset.link.results", "Measured results")}</a></div></td></tr>')
        dataset_sites = sorted({site for task in tasks.values() for site in (task["task"].get("sites") or [])})
        dataset_site_options = "".join(f'<option value="{_e(site)}">{_e(site)}</option>' for site in dataset_sites)
        dataset_type_options = "".join(f'<option value="{code}">{_e(label)}</option>' for code, label in (("RETRIEVE", t("dataset.type_filter.retrieve", "Retrieve")), ("NAVIGATE", t("dataset.type_filter.navigate", "Navigate")), ("MUTATE", t("dataset.type_filter.mutate", "Mutate"))))
        dataset_body = f'''<section class="hero"><p class="eyebrow">WebArena-Verified · {task_count}</p><h1>{t("dataset.title", "Dataset")}</h1><p>{t("dataset.lead", "{tasks} tasks share goals and acceptance rules. This page retains the dataset reference path; method-specific frozen inputs and measured verdicts appear on result pages.", tasks=task_count)}</p></section><section><div class="dataset-browser"><h2>{t("dataset.tasks_heading", "Tasks and reference paths")}</h2><p>{t("dataset.tasks_lead", "{autonomous} came from successful autonomous runs; {repaired} were repaired and checked under the recorded protocol on {reference}. Open a task for its acceptance input, full trajectory, and evidence from every browser.", autonomous=browser_use_source_count, repaired=repaired_source_count, reference=reference_label)}</p><div class="dataset-controls"><label>{t("dataset.filter.search", "Search tasks")}<input data-dataset-filter="q" type="search" placeholder="{t('dataset.filter.placeholder', 'ID, site, or goal')}"></label><label>{t("common.site", "Site")}<select data-dataset-filter="site"><option value="">{t("common.all", "All")}</option>{dataset_site_options}</select></label><label>{t("common.task_type", "Task type")}<select data-dataset-filter="type"><option value="">{t("common.all", "All")}</option>{dataset_type_options}</select></label><label>{t("common.trajectory_source", "Trajectory source")}<select data-dataset-filter="source"><option value="">{t("common.all", "All")}</option><option value="autonomous">{t("dataset.source.autonomous", "Autonomous success")}</option><option value="repaired">{t("dataset.source.repaired", "Repaired and checked")}</option></select></label></div><p class="dataset-count" data-dataset-count></p><div class="table-scroll dataset-table" data-dataset-table><table><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in headers)}</tr></thead><tbody>{"".join(matrix_rows)}</tbody></table></div><p class="dataset-empty" data-dataset-empty hidden>{t("dataset.empty", "No matching tasks. Change the filters.")}</p></div></section>'''
        (root / "dataset.html").write_text(_shell(lang, "dataset", t("dataset.title", "Dataset"), validity_notice + dataset_body, current_route="dataset.html"), encoding="utf-8")

        stage_labels = {
            "browser_session_setup": t("stage.browser_session_setup", "Browser session setup"),
            "browser_tool_execution": t("stage.browser_action", "Browser action"),
            "evidence_finalization": t("stage.evidence_finalization", "Evidence finalization"),
            "official_evaluator_execution": t("stage.official_evaluator_execution", "Official evaluator execution"),
            "side_effect_not_observed": t("stage.side_effect_not_observed", "Network acceptance not satisfied"),
            "official_evaluation": t("stage.official_evaluation", "Official evaluation"),
        }
        result_rows = []
        for row in public_rows:
            cause = row["cause_zh"] if zh else row["cause_en"]
            status = _verdict_label(row["status"], row.get("semantic_status"), lang)
            stage_label = "—" if row["failure_stage"] == "pass" else stage_labels.get(row["failure_stage"], row["failure_stage"])
            detail_url = row.get("detail_url") or f'result/{row["key"]}.html'
            result_rows.append(f'<tr data-key="{row["key"]}" data-task="{row["task_id"]}" data-participant="{_e(row["participant_id"])}" data-status="{row["status"]}" data-site="{_e(row["sites"][0] if row["sites"] else "")}" data-stage="{_e(row["failure_stage"])}" data-cause="{_e(row["cause_zh"] or row["cause_en"])}"><td><a href="task/{row["task_id"]}.html">#{row["task_id"]}</a><button type="button" data-open-diagnosis>{t("results.diagnose", "Diagnose")}</button></td><td><a href="{_e(detail_url)}">{_e(row["browser"])}</a></td><td>{_e(row["sites"][0] if row["sites"] else "")}</td><td class="status-{row["status"]}">{status}</td><td>{row["completed_actions"]}/{row["action_count"]}</td><td>{_e(stage_label)}</td><td>{_e(cause) if cause else "—"}</td><td><a href="{_e(detail_url)}">{t("results.evidence_page", "Evidence page")}</a></td></tr>')
        browser_options = "".join(f'<option value="{_e(key)}">{_e(item["label"])}</option>' for key, item in comparison_methods.items())
        site_options = "".join(f'<option value="{_e(site)}">{_e(site)}</option>' for site in sorted({s for row in public_rows for s in row["sites"]}))
        present_stages = {row["failure_stage"] for row in public_rows if row["status"] == "fail"}
        stage_options = "".join(f'<option value="{code}">{_e(stage_labels[code])}</option>' for code in stage_labels if code in present_stages)
        causes = {row["cause_zh"]: row["cause_zh"] if zh else row["cause_en"] for row in public_rows if row["cause_zh"]}
        cause_options = "".join(f'<option value="{_e(code)}">{_e(label)}</option>' for code, label in sorted(causes.items()))
        result_matrix_rows = []
        for task_id, task in tasks.items():
            pass_links = []
            fail_links = []
            passed_engines = []
            failed_engines = []
            for engine, _method in comparison_methods.items():
                row = next((item for item in rows_by_task[task_id] if item["participant_id"] == engine), None)
                if row is None:
                    raise ValueError(f"same-task method result is missing: {engine}/{task_id}")
                label = _verdict_label(row["status"], row.get("semantic_status"), lang)
                detail_url = row.get("detail_url") or f'result/{row["key"]}.html'
                link = f'<a class="outcome-pill {row["status"]}" href="{_e(detail_url)}" data-participant="{_e(engine)}">{_e(row["browser"])} · {label}</a>'
                if row["status"] == "pass":
                    pass_links.append(link)
                    passed_engines.append(engine)
                else:
                    fail_links.append(link)
                    failed_engines.append(engine)
            site = (task["task"].get("sites") or [""])[0]
            result_matrix_rows.append(f'<tr data-site="{_e(site)}" data-pass="{_e(" ".join(passed_engines))}" data-fail="{_e(" ".join(failed_engines))}"><td><a href="task/{task_id}.html">#{task_id}</a></td><td>{_e(", ".join(task["task"].get("sites") or []))}</td><td><div class="outcome-list">{"".join(pass_links) or "—"}</div></td><td><div class="outcome-list">{"".join(fail_links) or "—"}</div></td></tr>')
        matrix_sites = "".join(f'<option value="{_e(site)}">{_e(site)}</option>' for site in sorted({(task["task"].get("sites") or [""])[0] for task in tasks.values()}))
        matrix_baselines = "".join(f'<option value="{_e(engine)}">{_e(item["label"])}</option>' for engine, item in comparison_methods.items())
        matrix_checks = "".join(f'<label class="compare-chip"><input type="checkbox" data-matrix-method value="{_e(engine)}" checked>{_e(item["label"])}</label>' for engine, item in comparison_methods.items())
        result_matrix = f'<section class="method-comparison"><div class="comparison-heading"><div><h2>{t("comparison.heading", "Same-task method comparison")}</h2><p>{t("comparison.lead", "All methods are grouped by official pass and fail. Open any outcome for its method-specific path and execution evidence.")}</p></div><span class="comparison-count" data-matrix-count></span></div><div class="comparison-controls"><label>{t("comparison.baseline", "Baseline method")}<select data-matrix-baseline>{matrix_baselines}</select></label><label>{t("comparison.relation", "Outcome relation")}<select data-matrix-relation><option value="different">{t("comparison.relation.different", "Selected methods disagree")}</option><option value="baseline-pass-others-fail">{t("comparison.relation.baseline_pass", "Baseline passes; others fail")}</option><option value="baseline-fail-others-pass">{t("comparison.relation.baseline_fail", "Baseline fails; others pass")}</option><option value="all-pass">{t("comparison.relation.all_pass", "All selected pass")}</option><option value="all-fail">{t("comparison.relation.all_fail", "All selected fail")}</option><option value="exact-pass">{t("comparison.relation.exact_pass", "Only selected methods pass")}</option><option value="">{t("comparison.relation.all_tasks", "All tasks")}</option></select></label><label>{t("common.site", "Site")}<select data-matrix-site><option value="">{t("common.all", "All")}</option>{matrix_sites}</select></label></div><fieldset class="comparison-methods"><legend>{t("comparison.methods", "Comparison methods (select at least one)")}</legend><div class="method-checks">{matrix_checks}</div></fieldset><div class="table-scroll matrix-table" data-task-matrix><table><thead><tr><th>{t("common.task", "Task")}</th><th>{t("common.site", "Site")}</th><th>{t("comparison.official_pass", "Official pass")}</th><th>{t("verdict.fail", "Fail")}</th></tr></thead><tbody>{"".join(result_matrix_rows)}</tbody></table></div></section>'
        results_body = f'''<section class="hero"><p class="eyebrow">{len(public_rows)} {t("results.logical_outcomes", "logical outcomes")}</p><h1>{t("results.title", "Results analysis")}</h1><p>{t("results.lead", "Choose a baseline and multiple methods for same-task comparison. The inventory starts with failures; open Diagnose for the exact finding, reviewed cause, and execution evidence.")}</p></section><section class="compact-section">{result_matrix}</section><section><div class="filters"><label>{t("results.filter.search", "Search")}<input data-filter="q" placeholder="{t("results.filter.placeholder", "Task, site, or error")}"></label><label>{t("common.method", "Method")}<select data-filter="participant"><option value="">{t("common.all", "All")}</option>{browser_options}</select></label><label>{t("common.verdict", "Verdict")}<select data-filter="status"><option value="fail">{t("verdict.fail", "Fail")}</option><option value="pass">{t("verdict.pass", "Pass")}</option><option value="">{t("common.all", "All")}</option></select></label><label>{t("common.site", "Site")}<select data-filter="site"><option value="">{t("common.all", "All")}</option>{site_options}</select></label><label>{t("common.failure_stage", "Failure stage")}<select data-filter="stage"><option value="">{t("common.all", "All")}</option>{stage_options}</select></label><label>{t("common.reviewed_category", "Reviewed category")}<select data-filter="cause"><option value="">{t("common.all", "All")}</option>{cause_options}</select></label></div><p class="result-count" data-result-count></p><div class="table-scroll inventory" data-results><table><thead><tr><th>{t("common.task", "Task")}</th><th>{t("common.method", "Method")}</th><th>{t("common.site", "Site")}</th><th>{t("results.column.verdict", "Verdict")}</th><th>{t("results.column.actions", "Actions")}</th><th>{t("common.failure_stage", "Failure stage")}</th><th>{t("common.reviewed_category", "Reviewed category")}</th><th>{t("results.column.detail", "Detail")}</th></tr></thead><tbody>{''.join(result_rows)}</tbody></table></div></section><dialog id="diagnosis-dialog"><div class="dialog-head"><strong data-dialog-title></strong><button type="button" aria-label="{t("results.dialog.close", "Close")}">×</button></div><div class="dialog-body"><div class="diagnosis-facts"><div><span>{t("common.failure_stage", "Failure stage")}</span><strong data-dialog-stage></strong></div><div><span>{t("results.dialog.action_locator", "Action locator")}</span><strong data-dialog-step></strong></div><div><span>{t("common.reviewed_category", "Reviewed category")}</span><strong data-dialog-category></strong></div></div><h3>{t("results.dialog.finding", "Programmatic finding and evaluator rule")}</h3><pre data-dialog-error></pre><h3>{t("results.dialog.cause", "Reviewed cause and treatment")}</h3><p data-dialog-cause></p><p data-dialog-remediation></p><div class="evidence-actions"><a data-dialog-step-link>{t("results.dialog.open_action", "Open the relevant action")}</a><a data-dialog-link>{t("results.dialog.open_detail", "Open full detail")}</a></div></div></dialog>'''
        diagnostic_details = (
            failure_category_matrix(comparison_methods, public_rows, lang)
            + f'<section><h2>{t("results.action_latency.heading", "Browser action latency")}</h2>'
            + f'<p>{t("results.action_latency.lead", "Mean, P50, and P95 are per browser action call; call counts describe workload. Result details retain every action duration and response.")}</p>'
            + f'<details class="matrix-disclosure"><summary>{t("results.action_latency.open", "Open latency by action type")}</summary>{action_table}</details></section>'
            + f'<section><h2>{t("results.successful.heading", "Conditional distributions on each method’s successful tasks")}</h2>'
            + f'<p>{t("results.successful.lead", "Each column contains only that method’s successful tasks. Task sets and sample counts may differ, so these distributions are diagnostic and not ranked.")}</p>'
            + _successful_distribution_table(method_rows, lang)
            + "</section>"
        )
        results_body = results_body.replace(
            '<section class="compact-section">',
            diagnostic_details + '<section class="compact-section">',
            1,
        ).replace('<div class="filters">', '<div class="filters" id="result-inventory">', 1)
        (root / "results.html").write_text(_shell(lang, "results", t("results.title", "Results analysis"), validity_notice + results_body, current_route="results.html"), encoding="utf-8")

        flow_labels = (
            t("environment.flow.replay_runner", "Replay runner"), t("environment.flow.browser_container", "Browser container"),
            t("environment.flow.local_sites_short", "Local sites"), t("environment.flow.reset_per_method", "Reset per method"),
            t("environment.flow.evidence_short", "Evidence"), t("environment.flow.official_evaluator", "Official evaluator"),
            t("environment.flow.full_teardown", "Full teardown"),
        )
        fallback = '<div class="flow-fallback" data-topology-fallback>' + "".join(
            f'<div class="flow-node"><strong>{index}</strong>{_e(label)}</div>'
            + ('<div class="flow-arrow" aria-hidden="true">→</div>' if index < len(flow_labels) else "")
            for index, label in enumerate(flow_labels, 1)
        ) + "</div>"
        topology = f'''<pre class="mermaid" hidden>flowchart LR
  R[{t("environment.flow.replay_runner", "Replay runner")}] -->|{t("environment.flow.one_browser", "one browser at a time")}| B[{t("environment.flow.browser_container", "Browser container")}]
  B -->|{t("environment.flow.colima_gateway", "Colima gateway")}| S[{t("environment.flow.local_sites", "Local WebArena sites")}]
  S --> D[{t("environment.flow.reset_sites", "Reset site data before each method")}]
  B --> E[{t("environment.flow.evidence", "Trace · HAR · resource samples")}]
  E --> V[{t("environment.flow.official_evaluator", "Official evaluator")}]
  V --> X[{t("environment.flow.evidence_bound", "Evidence-bound result")}]
  X --> T[{t("environment.flow.teardown", "Teardown browser and sites")}]
  T --> R</pre>'''
        environment_body = f'''<section class="hero"><h1>{t("environment.title", "Environment and method")}</h1><p>{t("environment.gateway_note", "The site gateway is the host gateway inside the Colima VM. Browser containers use it to reach the WebArena sites running in the same Mac virtualization environment; it is not a public test website.")}</p></section><section><h2>{t("environment.topology", "Execution topology")}</h2>{fallback}{topology}</section><section><h2>{t("environment.isolation", "Isolation rules")}</h2><div class="summary-grid"><article><strong>1</strong><p>{t("environment.isolation.fixed_port", "One fixed-port site stack permits only one running method.")}</p></article><article><strong>{task_count}</strong><p>{t("environment.isolation.coverage", "Each method covers every task after one site reset, with no reset between tasks. One final logical outcome is retained per task; extra physical calls are counted separately, and an unchanged task is not run again after passing.")}</p></article><article><strong>0</strong><p>{_e(backend_note)}</p></article></div></section><script data-topology-script>{{const node=document.querySelector('.mermaid');const loader=document.createElement('script');{mermaid_script_attributes("loader")};loader.onload=async()=>{{try{{mermaid.initialize({{startOnLoad:false,theme:document.documentElement.dataset.theme==='dark'?'dark':'neutral'}});const rendered=await mermaid.render('replay-topology',node.textContent);node.innerHTML=rendered.svg;node.hidden=false;document.querySelector('[data-topology-fallback]').hidden=true}}catch(error){{node.hidden=true}}}};document.head.appendChild(loader)}}</script>'''
        if "Rainbond" in str((profile.get("execution") or {}).get("site_stack") or ""):
            diagram_script = environment_body[environment_body.index('<script data-topology-script>'):]
            remote_labels = (
                t("environment.remote.freeze_inputs", "Freeze inputs"), t("environment.remote.restore_sites", "Restore sites"),
                t("environment.remote.fresh_context", "Fresh browser context"), t("environment.remote.sequential_replay", "Sequential replay"),
                t("stage.official_evaluation", "Official evaluation"), t("environment.remote.retain_evidence", "Retain evidence"),
            )
            remote_fallback = '<div class="flow-fallback" data-topology-fallback>' + ''.join(
                f'<div class="flow-node"><strong>{i}</strong>{_e(label)}</div>'
                + ('<div class="flow-arrow" aria-hidden="true">→</div>' if i < len(remote_labels) else '')
                for i, label in enumerate(remote_labels, 1)
            ) + '</div>'
            remote_diagram = f'''<pre class="mermaid" hidden>flowchart TD
  A["{remote_labels[0]}"] --> B["{remote_labels[1]}"]
  B --> C["{remote_labels[2]}"]
  C --> D["{remote_labels[3]}"]
  D --> S["Rainbond WebArena"]
  S --> E["{remote_labels[4]}"]
  E --> F["{remote_labels[5]}"]
  F -->|{t("environment.remote.next_task", "next task, no site reset")}| C
</pre>'''
            reset_rows = []
            for participant, method in methods.items():
                entry = next((
                    (key, value) for key, value in public_evidence.items()
                    if key[0] == participant and value.get("site_reset")
                ), None)
                if entry is None:
                    continue
                key, value = entry
                evidence_file = ("" if zh else "../") + f"data/evidence/{key[0]}-{key[1]}.json"
                receipt_link = f'evidence-viewer.html?file={evidence_file}'
                for site, identity in sorted(value["site_reset"].get("sites", {}).items()):
                    reset_rows.append(
                        f'<tr><td>{_e(method["label"])}</td><td>{_e(site)}</td>'
                        f'<td><code>sha256:{_e(identity.get("image_digest") or "—")}</code></td>'
                        f'<td><a href="{_e(receipt_link)}">{t("environment.reset.evidence_link", "Reset and readiness evidence")}</a></td></tr>'
                    )
            reset_table = (
                f'<section><h2>{t("environment.reset.heading", "Site images and reset evidence")}</h2>'
                '<div class="table-scroll"><table><thead><tr>'
                + ''.join(f'<th>{label}</th>' for label in (
                    t("common.method", "Method"), t("common.site", "Site"),
                    t("environment.reset.image_identity", "Image identity"), t("common.evidence", "Evidence"),
                )) + '</tr></thead><tbody>' + ''.join(reset_rows) + '</tbody></table></div></section>'
            ) if reset_rows else ''
            environment_body = (
                f'<section class="hero"><h1>{t("environment.title", "Environment and method")}</h1>'
                f'<p>{t("environment.remote.lead", "All browsers use the same site map and task order; acceptance revisions are identified below and on task pages. The site baseline is restored before each method, not between tasks.")}</p></section>'
                f'<section><h2>{t("environment.remote.flow", "Execution flow")}</h2>{remote_fallback}{remote_diagram}'
                f'<p>{t("environment.remote.note", "Each method-level baseline receipt binds Pod replacement, image digest and site readiness. Per-task traces, network captures and scores remain separate. Methods run serially; extra physical calls cover only never-passing tasks or tasks whose frozen input changed.")}</p></section>'
                + f'<p>{_e((profile.get("resources") or {}).get("browser_host") or (t("environment.remote.browser_host_unrecorded", "Browser host not recorded")))} · Rainbond WebArena. {_e(backend_note)}</p>'
                + reset_table + diagram_script
            )
        identity_rows = []
        for participant, method in methods.items():
            hashes = Counter(
                row["binary_sha256"]
                for row in attempts
                if row["participant_id"] == participant
                and isinstance(row.get("binary_sha256"), str)
                and row["binary_sha256"]
            )
            declared_hashes = method.get("binary_sha256s") or (
                [method["binary_sha256"]]
                if isinstance(method.get("binary_sha256"), str) else []
            )
            for binary_hash in declared_hashes:
                hashes.setdefault(binary_hash, 0)
            revision = (
                method.get("release_tag")
                or method.get("release_revision")
                or method.get("version")
                or "—"
            )
            distribution = ", ".join(
                f'<code>{_e(binary_hash[:12])}</code> · {count} '
                + (t("environment.identity.tasks", "tasks"))
                for binary_hash, count in sorted(hashes.items(), key=lambda item: (-item[1], item[0]))
            ) or "—"
            identity_rows.append(
                f'<tr><td>{_e(method["label"])}</td><td>{_e(revision)}</td>'
                f'<td>{len(hashes) if hashes else "—"}</td><td>{distribution}</td></tr>'
            )
        identity_section = (
            f'<section><h2>{t("environment.identity.heading", "Method identity and repair sequence")}</h2>'
            f'<p>{t("environment.identity.lead", "A method may converge through several targeted browser builds. The table lists the builds bound to the retained logical results and their task counts. Latency is sampled across that repair sequence and does not establish the standalone performance of one build.")}</p>'
            '<div class="table-scroll"><table><thead><tr>'
            + ''.join(f'<th>{label}</th>' for label in (
                t("common.method", "Method"), t("environment.identity.revision", "Version or revision"),
                t("environment.identity.builds", "Builds"), t("environment.identity.distribution", "Retained result distribution"),
            ))
            + '</tr></thead><tbody>' + ''.join(identity_rows) + '</tbody></table></div></section>'
        )
        environment_body = environment_body.replace('</section>', '</section>' + identity_section, 1)
        if measurement_note:
            environment_body += f'<p class="callout">{_e(measurement_note)}</p>'
        (root / "environment.html").write_text(_shell(lang, "environment", t("environment.title", "Environment and method"), environment_body, current_route="environment.html"), encoding="utf-8")

        stage_counts = Counter(row["failure_stage"] for row in public_rows)
        review_rows = (
            (
                t("review.trajectory.title", "Trajectory qualification"),
                t("review.trajectory.scope", "{tasks} tasks: {autonomous} autonomous successes and {repaired} repaired trajectories.", tasks=task_count, autonomous=browser_use_source_count, repaired=repaired_source_count),
                t("review.trajectory.finding", "{repaired} candidate trajectories required repair before use as frozen inputs.", repaired=repaired_source_count),
                t("review.trajectory.correction", "Recorded per-task acceptance yielded {passed}/{total} passes on {reference}. Frozen trajectories contain no Agent or model decisions.", reference=reference_label, passed=reference_row['passed'], total=reference_row['total']),
                t("review.trajectory.open", "Method-specific frozen inputs and official evaluations are available on each result page."),
            ),
            (
                t("review.failures.title", "Failure evidence review"),
                t("review.failures.scope", "{failures} final failed outcomes.", failures=reviewed_failure_count),
                t("review.failures.finding", "{session} browser-session setup failures; {action} browser-action failures; {evidence} evidence-finalization failures; {evaluator} evaluator-execution failures; {network} network-acceptance failures.", session=stage_counts["browser_session_setup"], action=stage_counts["browser_tool_execution"], evidence=stage_counts["evidence_finalization"], evaluator=stage_counts["official_evaluator_execution"], network=stage_counts["side_effect_not_observed"]),
                t("review.failures.correction", "{failures}/{failures} reviews are bound to the current evidence hash. Program errors, action locators, reviewed causes and treatment states are separate; official scores did not change.", failures=reviewed_failure_count),
                t("review.failures.open", "Failure causes and treatment status appear on each failed result page."),
            ),
        )
        review_table_rows = "".join(
            "<tr>" + "".join(f"<td>{_e(cell)}</td>" for cell in (title, scope, finding, correction, remaining)) + "</tr>"
            for title, scope, finding, correction, remaining in review_rows
        )
        review_headers = (t("review.column.round", "Round"), t("review.column.scope", "Scope"), t("review.column.finding", "Finding"), t("review.column.correction", "Correction"), t("review.column.open", "Open"))
        review_status = (
            t("review.status", "The report contains {tasks} tasks with one result per each registered method; {failures}/{failures} final failures received evidence-bound review.", tasks=task_count, failures=reviewed_failure_count)
        )
        if unverifiable_ids:
            review_status += (
                t("review.status_unverifiable", " The {tasks} tasks with unverifiable references are excluded from the separate verifiable-task denominator.", tasks=len(unverifiable_ids))
            )
        review_body = f'''<section class="hero"><h1>{t("review.title", "Review history")}</h1><p>{t("review.lead", "Rounds that affected the current dataset or result interpretation. Full evidence and treatment status for each failure are on result pages.")}</p></section><section><h2>{t("review.current_status", "Current status")}</h2><p>{review_status}</p></section><section><h2>{t("review.rounds", "Review rounds")}</h2><div class="table-scroll review-table"><table><thead><tr>{''.join(f'<th>{_e(label)}</th>' for label in review_headers)}</tr></thead><tbody>{review_table_rows}</tbody></table></div></section>'''
        (root / "review.html").write_text(_shell(lang, "review", t("review.title", "Review history"), validity_notice + review_body, current_route="review.html"), encoding="utf-8")

        viewer_tabs = (("result", t("viewer.tab.result", "Result and resources")), ("site_reset", t("viewer.tab.site_reset", "Site reset")), ("readiness", t("viewer.tab.readiness", "Site readiness")), ("programmatic_failure", t("viewer.tab.programmatic_failure", "Programmatic finding")), ("review", t("viewer.tab.review", "Reviewed cause")), ("trajectory", t("viewer.tab.trajectory", "Actions and logs")), ("official_evaluation", t("stage.official_evaluation", "Official evaluation")))
        tab_buttons = "".join(f'<button type="button" data-evidence-tab="{key}" aria-selected="false">{label}</button>' for key, label in viewer_tabs)
        viewer_body = f'<section class="hero"><h1>{t("viewer.title", "Evidence viewer")}</h1><p>{t("viewer.lead", "Inspect full sanitized evidence by outcome, action, programmatic finding, and review. The raw JSON remains downloadable.")}</p></section><section class="viewer"><div class="viewer-tabs" role="tablist">{tab_buttons}</div><h2 data-evidence-heading></h2><div class="evidence-actions"><a data-evidence-raw href="#">{t("common.download_sanitized_json", "Download sanitized JSON")}</a><a data-evidence-image hidden></a></div><pre data-evidence-viewer></pre></section>'
        (root / "evidence-viewer.html").write_text(_shell(lang, "results", t("viewer.title", "Evidence viewer"), validity_notice + viewer_body, current_route="evidence-viewer.html"), encoding="utf-8")

    if interpretation_path is not None:
        source = Path(interpretation_path)
        illustrations = source.parent / "trajectory-replay-illustrations"
        asset_dir = output / "assets/interpretation"
        asset_dir.mkdir(parents=True, exist_ok=True)
        article = json.loads(source.read_text(encoding="utf-8"))
        for section in article["sections"]:
            if section.get("figure"):
                for lang in languages:
                    filename = f'{section["figure"]}-{lang}.png'
                    if Path(filename).name != filename:
                        raise ValueError("invalid article image name")
                    shutil.copyfile(illustrations / filename, asset_dir / filename)

        css_path = output / "assets/report.css"
        css = css_path.read_text(encoding="utf-8") + ARTICLE_CSS
        css_path.write_text(css, encoding="utf-8")
        for lang in languages:
            language_root = output if lang == "zh" else output / "en"
            t = report_text(lang)
            arguments = dict(source=source, contract_audit=Path(interpretation_audit_path) if interpretation_audit_path is not None else source.parent / "trajectory-replay-contract-audit.json",
                             results_sha256=file_sha256(results_source), methods=methods, rows=public_rows,
                             lang=lang, image_dir=illustrations,
                             image_prefix=("assets/" if lang == "zh" else "../assets/") + "interpretation/")
            title, body = render_interpretation(**arguments)
            download = t("interpretation.download", "Download standalone article")
            body = f'<p><a href="article.html" download>{download}</a></p>' + body
            (language_root / "interpretation.html").write_text(
                _shell(lang, "interpretation", title, body, current_route="interpretation.html"), encoding="utf-8")
            publication = profile["publication"]
            share = urlsplit(publication["share_url"])
            if not share.path.startswith("/v/") or not share.path.removeprefix("/v/"):
                raise ValueError("standalone article requires the bound report share")
            report_url = (
                publication["base_url"].rstrip("/") + "/api/preview/" + publication["site_slug"]
                + ("/en" if lang == "en" else "") + "/interpretation.html?"
                + urlencode({"__artifact_share": share.path.removeprefix("/v/")})
            )
            title, offline = render_interpretation(**arguments, standalone=True, report_url=report_url)
            (language_root / "article.html").write_text(
                standalone_document(title, offline, lang, css), encoding="utf-8")
            home = language_root / "index.html"
            label = t("interpretation.home_link", "Interpretation: failure mechanisms, repairs and evaluation limits")
            home.write_text(home.read_text(encoding="utf-8").replace("<main>", f'<main><p><a href="interpretation.html">{label}</a></p>', 1), encoding="utf-8")
            _add_interpretation_navigation(language_root, lang)

    if interpretation_path is None:
        for lang in languages:
            root = output if lang == "zh" else output / "en"
            t = report_text(lang)
            title = t("interpretation.title", "Result interpretation")
            failed_participants = [name for name in methods if any(
                row["participant_id"] == name and row["status"] != "pass" for row in public_rows)]
            failed_count = sum(row["status"] != "pass" for row in public_rows)
            overview = (t("interpretation.overview", "{tasks} tasks and {methods} methods produce {results} logical results, including {failures} failures.", tasks=len(tasks), methods=len(methods), results=len(public_rows), failures=failed_count))
            scoring = (t("interpretation.scoring_text", "Each method replays the frozen actions for the same task, then applies its bound acceptance rules. Task and result details retain original scores and contract revisions."))
            task_id = next(iter(tasks))
            body = (f'<article class="interpretation"><h1>{title}</h1>'
                    f'<section id="overview" class="presentation-slide"><h2>{t("interpretation.complete_results", "Complete results")}</h2><p>{overview}</p>'
                    + _metrics_table(method_rows, lang, show_valid_reference=bool(unverifiable_ids)) + '</section>'
                    f'<section id="scoring" class="presentation-slide"><h2>{t("interpretation.scoring", "Scoring")}</h2><p>{scoring}</p>'
                    f'<a href="task/{task_id}.html">Task {task_id}</a></section>'
                    f'<section id="browser-diagnoses" class="presentation-slide"><h2>{t("interpretation.failures", "Failures by method")}</h2>'
                    + ''.join(browser_failure_profile(methods, public_rows, lang, name) for name in failed_participants)
                    + '</section><section id="scope" class="presentation-slide"><h2>'
                    + (t("interpretation.scope", "Measurement scope")) + '</h2><p>'
                    + _e((profile.get("measurement_notes") or {}).get(lang) or scoring)
                    + '</p></section></article>')
            download = t("interpretation.download", "Download standalone article")
            (root / "interpretation.html").write_text(_shell(
                lang, "interpretation", title, f'<p><a href="article.html" download>{download}</a></p>' + body,
                current_route="interpretation.html"), encoding="utf-8")
            (root / "article.html").write_text(standalone_document(
                title, body, lang, (output / "assets/report.css").read_text(encoding="utf-8")), encoding="utf-8")
            _add_interpretation_navigation(root, lang)

    if not include_english:
        # Remove authored translations only; raw task and execution evidence is immutable.
        for evidence_path in (output / "data/evidence").glob("*.json"):
            document = json.loads(evidence_path.read_text(encoding="utf-8"))
            review = document.get("review")
            if isinstance(review, dict):
                document["review"] = {
                    key: value for key, value in review.items()
                    if not (key.endswith("_en") and key[:-3] + "_zh" in review)
                }
            _write_clean_json(evidence_path, document)
        result_path = output / "data/results.json"
        rows = json.loads(result_path.read_text(encoding="utf-8"))
        _write_clean_json(result_path, [
            {key: value for key, value in row.items() if key not in {"cause_en", "reason_en"}}
            for row in rows
        ])
        for page in output.rglob("*.html"):
            if page.name == "article.html":
                continue
            prefix = "../" * (len(page.relative_to(output).parts) - 1)
            content = page.read_text(encoding="utf-8")
            content = re.sub(
                r'(<a class="language" href=")[^"]+(">)',
                lambda match, prefix=prefix: match[1] + prefix + "en/index.html" + match[2],
                content,
            )
            page.write_text(content, encoding="utf-8")
        english_root = output / "en"
        english_root.mkdir()
        (english_root / "index.html").write_text(
            '<!doctype html><html lang="zh"><head><meta name="browser-eval-report" content="1"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<script>(function(){const b=document.querySelector("base[href]");'
            'if(b)b.href=new URL("en/",b.href).href;})();</script>'
            '<title>英文报告未生成</title></head><body>'
            '<main data-english-not-generated><h1>英文报告未生成</h1>'
            '<p>English report has not been generated.</p>'
            '<p>当前报告仅提供中文版。英文版需单独生成。</p>'
            '<a href="../index.html">返回中文报告</a></main></body></html>',
            encoding="utf-8",
        )
    manifest = {
        "schema": "lex_webarena_trajectory_replay_report/1",
        "profile_sha256": file_sha256(profile_source),
        "template": template_identity("trajectory-replay"),
        "languages": list(languages),
        "english_status": "generated" if include_english else "not_generated",
        "results_sha256": file_sha256(Path(results_path)),
        "dataset_sha256": file_sha256(Path(dataset_path)),
        "review_sha256": file_sha256(Path(review_path)),
        "task_count": len(tasks),
        "participant_count": len(comparison_methods),
        "browser_count": browser_count,
        "method_count": method_count,
        "execution_backend": execution_backend,
        "participants": list(comparison_methods),
        "logical_results": len(public_rows),
        "failed_results": sum(row["status"] != "pass" for row in public_rows),
        "reviewed_failures": len(reviews),
        "formal_reference_validity": (profile.get("scoring") or {}).get(
            "require_all_reference_validity"
        ) is True,
        "site_reset_between_tasks": (profile.get("execution") or {}).get(
            "reset_before_every_task"
        ) is True,
        "routes": len(list(output.rglob("*.html"))),
        "interpretation": True,
        "interpretation_kind": "authored" if interpretation_path is not None else "data-derived",
        "measurement_notes": {lang: profile["measurement_notes"][lang] for lang in languages}
        if profile.get("measurement_notes") else {},
    }
    manifest['browser_eval'] = bind_build_version(output)
    _write_clean_json(output / "report-manifest.json", manifest)
    if language is not None:
        entry = select_report_language(output, language)
        manifest.update(primary_language=language, entrypoint=entry.relative_to(output).as_posix())
    return manifest


CATALOG_REPORT_CSS = (ASSETS / "webarena-catalog.css").read_text(encoding="utf-8")

CATALOG_SITE_LABELS = {
    "gitlab": "GitLab",
    "map": "地图",
    "reddit": "Reddit",
    "shopping": "购物网站",
    "shopping_admin": "购物后台",
    "wikipedia": "维基百科",
}


def _catalog_shell(title: str, body: str, participant: str) -> str:
    nav = (
        '<nav><a href="index.html">结果</a>'
        '<a href="dataset.html">数据集</a>'
        '<a href="method.html">方法与边界</a></nav>'
    )
    return (
        '<!doctype html><html lang="zh-CN"><head><meta name="browser-eval-report" content="1"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"><base href="/">'
        f"<title>{html.escape(title)}</title><style>{CATALOG_REPORT_CSS}</style></head><body>{nav}{body}"
        f"<footer>WebArena-Verified 全量轨迹重放 · {html.escape(participant)}</footer>"
        "</body></html>"
    )


def _catalog_official_tasks() -> list[dict]:
    dataset = importlib.resources.files("webarena_verified").joinpath(
        "assets/dataset/webarena-verified.json"
    )
    tasks = json.loads(dataset.read_text(encoding="utf-8"))
    if not isinstance(tasks, list):
        raise ValueError("WebArena-Verified dataset is not a list")
    return tasks


def _catalog_progress_index(evidence_root: Path) -> tuple[dict[str, Path], dict[str, dict[int, dict]]]:
    directories: dict[str, Path] = {}
    rows_by_digest: dict[str, dict[int, dict]] = {}
    for progress in evidence_root.glob("*/progress.jsonl"):
        digest = file_sha256(progress)
        rows = load_jsonl(progress)
        directories[digest] = progress.parent
        rows_by_digest[digest] = {int(row["task_id"]): row for row in rows}
    return directories, rows_by_digest


def build_catalog_acceptance_report(
    *,
    evidence_root: Path,
    ledger_path: Path,
    scope_path: Path,
    catalog_path: Path,
    suite_path: Path,
    site_map_path: Path | None,
    output: Path,
    archive: Path,
) -> dict:
    """Build a Chinese-only report from a complete accepted catalog."""

    if output.exists() or archive.exists():
        raise FileExistsError("report output already exists")
    ledger = read_json(ledger_path)
    scope = read_json(scope_path)
    catalog = read_json(catalog_path)
    suite = read_json(suite_path)
    official = _catalog_official_tasks()
    task_ids = catalog.get("task_ids")
    expected_data_checksum = scope.get("official_dataset_sha256")
    if (
        ledger.get("counts", {}).get("pass") != len(official)
        or ledger.get("counts", {}).get("unresolved") != 0
        or task_ids != list(range(len(official)))
        or scope.get("task_ids") != task_ids
        or not isinstance(expected_data_checksum, str)
        or len(catalog.get("rows") or []) != len(official)
    ):
        raise ValueError("report inputs are not a complete accepted WebArena catalog")

    participant_data = ledger.get("participant") or {}
    task_count = len(official)
    engine = str(participant_data.get("engine") or "unknown")
    backend = str(participant_data.get("backend") or "unknown")
    request_bridge = request_bridge_identity(participant_data)
    participant = f"{engine.capitalize()} / {backend.capitalize()}"
    output.mkdir(parents=True)
    (output / "case").mkdir()
    (output / "evidence").mkdir()

    directories, rows_by_digest = _catalog_progress_index(evidence_root)
    benchmark = remote_benchmark(load_remote_sites(site_map_path)) if site_map_path else None
    corrections = load_corrections() if site_map_path else None
    accepted: dict[int, tuple[dict, dict, dict, Path]] = {}
    for accepted_row in ledger["rows"]:
        task_id = int(accepted_row["task_id"])
        attempt = accepted_row["attempts"][-1]
        digest = attempt["progress_sha256"]
        try:
            accepted[task_id] = (
                accepted_row,
                attempt,
                rows_by_digest[digest][task_id],
                directories[digest],
            )
        except KeyError as error:
            raise ValueError(f"task {task_id} accepted evidence is missing") from error

    suite_by_id = {
        int(task["id"].removeprefix("webarena-")): task for task in suite["tasks"]
    }
    catalog_by_id = {int(row["task_id"]): row for row in catalog["rows"]}
    contracts = []
    durations = []
    site_counts = {site: 0 for site in CATALOG_SITE_LABELS}
    result_rows = []
    first_statuses: dict[str, int] = {}
    for accepted_row in ledger["rows"]:
        status = accepted_row["attempts"][0]["status"]
        first_statuses[status] = first_statuses.get(status, 0) + 1

    for task_id, task in enumerate(official):
        accepted_row, final_attempt, row, run_dir = accepted[task_id]
        physical_attempt_count = sum(
            item["status"] != "candidate_setup_failure" for item in accepted_row["attempts"]
        )
        seconds = float(row["duration_ms"]) / 1000
        durations.append(seconds)
        sites = [str(site) for site in task["sites"]]
        for site in sites:
            site_counts[site] += 1
        site_text = "、".join(CATALOG_SITE_LABELS[site] for site in sites)
        intent = str(task["intent"])
        result_rows.append(
            f'<tr data-search="{task_id} {html.escape(site_text)} {html.escape(intent)}">'
            f'<td><a href="case/{task_id:03}.html">{task_id}</a></td>'
            f"<td>{html.escape(site_text)}</td><td class=\"ok\">通过</td>"
            f'<td>{len(accepted_row["attempts"])}</td><td>{seconds:.2f}</td></tr>'
        )
        task_dir = run_dir / f"task-{task_id}"
        transition = final_attempt.get("evaluator_transition")
        if transition is not None:
            if benchmark is None or corrections is None:
                raise ValueError("transitioned report evidence requires the bound site map")
            evaluation, binding = reevaluate_pass(
                task_dir,
                task_id=task_id,
                expected_data_checksum=expected_data_checksum,
                benchmark=benchmark,
                corrections=corrections,
            )
            if binding != transition or canonical_json_sha256(evaluation) != transition.get(
                "current_evaluation_sha256"
            ):
                raise ValueError(f"task {task_id} transitioned evaluation changed")
        else:
            evaluation = read_json(task_dir / "evaluation.json")
        evidence = {
            "schema": "lex_webarena_catalog_public_evidence/1",
            "task_id": task_id,
            "participant": {
                "engine": engine,
                "backend": backend,
                **(
                    {"binary_sha256": final_attempt["binary_sha256"]}
                    if isinstance(final_attempt.get("binary_sha256"), str)
                    else {
                        "remote_browser": (ledger.get("participant") or {}).get(
                            "remote_browser"
                        ),
                        "runtime_metadata": final_attempt.get("runtime_metadata"),
                        **(
                            {"request_bridge": request_bridge}
                            if request_bridge is not None
                            else {}
                        ),
                    }
                ),
            },
            "result": {
                key: row.get(key)
                for key in (
                    "status",
                    "score",
                    "action_count",
                    "completed_actions",
                    "duration_ms",
                    "executed_actions_sha256",
                )
            },
            "attempt_count": len(accepted_row["attempts"]),
            "physical_attempt_count": physical_attempt_count,
            "attempt_statuses": [item["status"] for item in accepted_row["attempts"]],
            "evaluation": evaluation,
            "evaluator_transition": transition,
            "actual_response": read_json(task_dir / "actual_response.json"),
            "trace": read_json(task_dir / "trace.json"),
        }
        write_json(output / "evidence" / f"{task_id:03}.json", evidence)
        case_body = (
            f"<h1>第 {task_id} 题 · {html.escape(participant)} 通过</h1>"
            f'<p class="lead">{html.escape(intent)}</p>'
            '<section class="panel" data-report-detail="passed"><h2>官方判分</h2>'
            f'<p class="ok">通过 · score 1.0</p><p>网站：{html.escape(site_text)}；'
            f"最终执行耗时：{seconds:.2f} 秒；物理执行：{physical_attempt_count} 次。</p>"
            f'<p>最终动作哈希：<code>{html.escape(str(row["executed_actions_sha256"]))}</code></p>'
            '</section><section class="panel"><h2>执行证据</h2>'
            f'<p><a href="evidence.html?task={task_id:03}">查看轨迹与官方评测结果</a> · '
            f'<a href="evidence/{task_id:03}.json">下载 JSON</a></p></section>'
        )
        (output / "case" / f"{task_id:03}.html").write_text(
            _catalog_shell(f"第 {task_id} 题结果", case_body, participant),
            encoding="utf-8",
        )
        catalog_row = catalog_by_id[task_id]
        fixed_actions = (
            suite_by_id[task_id]["trajectory"]["actions"]
            if catalog_row.get("mode") == "portable"
            else catalog_row["source_actions"]
        )
        contracts.append(
            {
                "task_id": task_id,
                "intent": intent,
                "sites": sites,
                "official_evaluators": task["eval"],
                "trajectory_source": catalog_row.get("source_kind"),
                "fixed_actions": fixed_actions,
            }
        )

    site_html = "".join(
        f"<tr><td>{CATALOG_SITE_LABELS[site]}</td><td>{count}</td></tr>"
        for site, count in site_counts.items()
    )
    first_pass = first_statuses.get("pass", 0)
    first_fail = first_statuses.get("fail", 0)
    setup_failure = first_statuses.get("candidate_setup_failure", 0)
    counts = ledger["counts"]
    binary_count = len(ledger.get("binary_sha256s") or [])
    remote_count = len(ledger.get("remote_browsers") or [])
    runtime_count = binary_count + remote_count
    hosted_runtime = bool(remote_count)
    initial_physical = first_pass + first_fail
    index_body = f"""<h1>WebArena-Verified 全量轨迹重放</h1><p class="lead">{html.escape(participant)}：{task_count} / {task_count} 题至少一次获得官方 evaluator 通过（100%）。通过后不再执行该 case。</p>
<section class="panel"><h2>核心结果</h2><div class="scroll"><table><thead><tr><th>浏览器引擎 / 执行后端</th><th>题数</th><th>通过</th><th>Mean</th><th>P50</th><th>P95</th></tr></thead><tbody><tr><td>{html.escape(participant)}</td><td>{task_count}</td><td>{counts["pass"]}</td><td>{statistics.mean(durations):.2f} 秒</td><td>{statistics.median(durations):.2f} 秒</td><td>{percentile(durations, .95):.2f} 秒</td></tr></tbody></table></div><p class="muted">耗时统计使用每题最终通过调用。接受审计覆盖 {counts["physical_attempts"]:,} 次物理执行；通过后重复执行 {counts["excluded_duplicate_attempts"]} 次。</p></section>
<section class="panel"><h2>执行收敛</h2><p>初始顺序扫描完成 {initial_physical} 次物理执行：{first_pass} 题通过，{first_fail} 题未通过；其余 {setup_failure} 题因执行环境中断记录为准备失败。恢复环境后继续按官方题号处理未通过项，并逐类修复轨迹、执行器、站点状态和已确认的评测问题，直至 {task_count} 题全部通过。</p><p>接受链包含 {len(ledger["runs"])} 个执行段和 {runtime_count} 个浏览器运行时身份。浏览器引擎、执行后端和站点映射保持一致；每次修复只进入尚未通过的 case。</p></section>
<section class="panel"><h2>网站分布</h2><p>一题可涉及多个网站，计数之和可能大于 {task_count}。</p><div class="scroll"><table><thead><tr><th>网站</th><th>涉及题数</th></tr></thead><tbody>{site_html}</tbody></table></div></section>
<section class="panel"><h2>逐题结果</h2><label for="find">筛选题号、网站或任务内容</label><p><input id="find" type="search" aria-label="筛选逐题结果"></p><div class="scroll"><table id="results"><thead><tr><th>题号</th><th>网站</th><th>最终判分</th><th>接受链记录数</th><th>最终耗时（秒）</th></tr></thead><tbody>{''.join(result_rows)}</tbody></table></div></section><script>const q=document.getElementById("find");q.addEventListener("input",()=>{{for(const row of document.querySelectorAll("#results tbody tr"))row.hidden=!row.dataset.search.toLowerCase().includes(q.value.toLowerCase())}})</script>"""
    (output / "index.html").write_text(
        _catalog_shell("全量轨迹重放结果", index_body, participant), encoding="utf-8"
    )

    runtime_method = (
        "托管浏览器的服务、账户匿名哈希及 Browser.getVersion 版本字段"
        if hosted_runtime
        else "本地浏览器二进制哈希"
    )
    revision_method = (
        "托管浏览器运行时在开始前冻结版本身份，每个物理结果都必须与该身份一致。"
        if hosted_runtime
        else "轨迹修复期间使用多个二进制版本，但每个版本均在接受账本中单独绑定。"
    )
    bridge_method = (
        f"Kitesurf 远端浏览器无法直接访问私网 Rainbond 地址；本轮用 Playwright request bridge 从执行机访问账本绑定的 {len(request_bridge['origins'])} 个站点 origin，并以原 URL、请求方法、请求头和请求体回填响应。桥接身份随每个执行段和公开证据保存。"
        if request_bridge is not None
        else ""
    )
    method_body = f"""<h1>方法与边界</h1><section class="panel"><h2>执行方法</h2><p>评测集按官方题号 {task_ids[0]}–{task_ids[-1]} 排列。每题执行冻结轨迹，由 {html.escape(engine.capitalize())} 浏览器引擎通过 {html.escape(backend.capitalize())} 协议后端完成动作。接受标准是 WebArena-Verified 官方 evaluator 返回 score 1.0。</p><p>初始扫描中断后，后续执行只选择接受账本里尚未通过的题。最终接受账本绑定每个物理调用、{runtime_method}、站点映射、轨迹、实际响应、网络证据和 evaluator 结果。</p>{f'<p>{html.escape(bridge_method)}</p>' if bridge_method else ''}</section><section class="panel"><h2>代码与评测边界</h2><p>确认属于评测集或 evaluator 的通用修复维护在 lexmount/webarena-verified 的 lex-main 分支；轨迹、执行器、站点绑定、证据聚合和报告维护在 browser-eval。评测答案和通过条件没有为浏览器适配而放宽。</p><p>旧 evaluator 版本下的通过证据通过固定实际响应和 HAR 离线重评，浏览器动作没有重跑。{revision_method}</p></section><section class="panel"><h2>结果范围</h2><p>该结果证明当前 {html.escape(participant)} 配置下 {task_count} 题各自至少成功一次。812-case 全量 split 与既有 90-case 抽样 split 数据独立；两者共享轨迹执行和 evaluator 基础模块，并分别绑定自己的证据与发布目标。</p></section>"""
    (output / "method.html").write_text(
        _catalog_shell("方法与边界", method_body, participant), encoding="utf-8"
    )

    write_json(
        output / "dataset-contracts.json",
        {"schema": LEGACY_CATALOG_CONTRACTS_SCHEMA, "tasks": contracts},
    )
    dataset_rows = "".join(
        f'<tr><td><a href="case/{task["task_id"]:03}.html">{task["task_id"]}</a></td>'
        f'<td>{html.escape("、".join(CATALOG_SITE_LABELS[site] for site in task["sites"]))}</td>'
        f'<td>{html.escape(task["intent"])}</td></tr>'
        for task in contracts
    )
    dataset_body = f"""<h1>数据集与轨迹合同</h1><p class="lead">{task_count} 题按 WebArena-Verified 官方顺序冻结。此页只展示任务定义；判分和执行证据在逐题结果页。</p><section class="panel"><div class="scroll"><table><thead><tr><th>题号</th><th>网站</th><th>任务</th></tr></thead><tbody>{dataset_rows}</tbody></table></div><p><a href="dataset-contracts.json">下载完整任务、evaluator 与固定动作合同</a></p></section>"""
    (output / "dataset.html").write_text(
        _catalog_shell("数据集与轨迹合同", dataset_body, participant), encoding="utf-8"
    )
    (output / "contract.html").write_text(
        _catalog_shell("评测合同", dataset_body, participant), encoding="utf-8"
    )
    viewer = """<h1>证据查看器</h1><p><a id="back" href="index.html">返回结果</a></p><pre id="payload" role="region" aria-label="完整执行证据">加载中…</pre><script>const id=new URLSearchParams(location.search).get("task");const ok=/^\\d{3}$/.test(id||"");if(ok){document.getElementById("back").href="case/"+id+".html";fetch("evidence/"+id+".json").then(r=>{if(!r.ok)throw Error("证据不存在");return r.json()}).then(x=>document.getElementById("payload").textContent=JSON.stringify(x,null,2)).catch(e=>document.getElementById("payload").textContent=String(e))}else document.getElementById("payload").textContent="无效的题号";</script>"""
    (output / "evidence.html").write_text(
        _catalog_shell("证据查看器", viewer, participant), encoding="utf-8"
    )
    notice = '<h1>English edition not generated</h1><p>This release is Chinese only.</p><p><a href="index.html">返回中文版</a></p>'
    (output / "en").mkdir()
    (output / "en" / "index.html").write_text(
        _catalog_shell("English edition not generated", notice, participant),
        encoding="utf-8",
    )

    manifest = {
        "schema": LEGACY_CATALOG_REPORT_SCHEMA,
        "generated_languages": ["zh"],
        "participant": {
            "engine": engine,
            "backend": backend,
            **(
                {"request_bridge": request_bridge}
                if request_bridge is not None
                else {}
            ),
        },
        "ledger_sha256": file_sha256(ledger_path),
        "scope_sha256": file_sha256(scope_path),
        "catalog_sha256": file_sha256(catalog_path),
        "portable_suite_sha256": file_sha256(suite_path),
        "task_count": len(official),
        "pass": counts["pass"],
        "initial_physical_attempts": initial_physical,
        "initial_pass": first_pass,
        "initial_setup_failures": setup_failure,
        "physical_attempts": counts["physical_attempts"],
        "passed_case_reruns": counts["excluded_duplicate_attempts"],
        "binary_count": binary_count,
        "site_counts": site_counts,
        "route_counts": {"case": len(official), "evidence_json": len(official)},
    }
    write_json(output / "manifest.json", manifest)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(output))
    validate_catalog_acceptance_report(output, archive)
    return {
        "output": str(output),
        "archive": str(archive),
        "manifest_sha256": file_sha256(output / "manifest.json"),
        "archive_sha256": file_sha256(archive),
        "archive_bytes": archive.stat().st_size,
    }


def validate_catalog_acceptance_report(
    output: Path, archive: Path, *, profile: dict[str, Any] | None = None
) -> dict:
    """Validate the public boundary of a catalog report package."""

    manifest = read_json(output / "manifest.json")
    task_count = int(manifest.get("task_count") or 0)
    if (
        manifest.get("schema") != LEGACY_CATALOG_REPORT_SCHEMA
        or manifest.get("generated_languages") != ["zh"]
        or manifest.get("pass") != task_count
        or task_count <= 0
        or len(list((output / "case").glob("*.html"))) != task_count
        or len(list((output / "evidence").glob("*.json"))) != task_count
    ):
        raise ValueError("catalog report manifest or route inventory is invalid")
    expected_participant = (
        {
            "engine": profile.get("browser"),
            "backend": profile.get("execution_backend"),
            **(
                {"request_bridge": profile["request_bridge"]}
                if profile.get("request_bridge") is not None
                else {}
            ),
        }
        if profile is not None
        else None
    )
    if profile is not None and (
        manifest.get("participant") != expected_participant
        or manifest.get("ledger_sha256") != profile.get("acceptance_ledger_sha256")
        or manifest.get("catalog_sha256")
        != (profile.get("accepted_catalog") or {}).get("sha256")
        or manifest.get("portable_suite_sha256")
        != (profile.get("accepted_portable_suite") or {}).get("sha256")
        or manifest.get("task_count") != (profile.get("accepted_counts") or {}).get("total")
        or manifest.get("physical_attempts")
        != (profile.get("accepted_counts") or {}).get("physical_attempts")
        or manifest.get("passed_case_reruns")
        != (profile.get("accepted_counts") or {}).get("excluded_duplicate_attempts")
    ):
        raise ValueError("catalog report presentation changed its release binding")
    missing = []
    for path in output.rglob("*"):
        if not path.is_file() or path.suffix not in {".html", ".json", ".css", ".js"}:
            continue
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in ("/Users/", "file://", "/private/tmp")):
            raise ValueError(f"catalog report leaks a private path: {path}")
        if path.suffix != ".html":
            continue

        for value in re.findall(
            r'<(?:a|script|img|link)\b[^>]*\b(?:href|src)="([^"]+)"', text
        ):
            if value.startswith(("http:", "https:", "#", "data:", "javascript:")):
                continue
            relative = value.split("?", 1)[0].split("#", 1)[0]
            if relative.startswith("/") or ".." in Path(relative).parts:
                raise ValueError(f"catalog report contains an unsafe asset path: {value}")
            if relative and not (output / relative).resolve().is_file():
                missing.append((str(path.relative_to(output)), value))
    if missing:
        raise ValueError(f"catalog report has missing local links: {missing[:5]}")
    index = (output / "index.html").read_text(encoding="utf-8")
    method = (output / "method.html").read_text(encoding="utf-8")
    if (
        f'{task_count} / {task_count}' not in index
        or f'{manifest["physical_attempts"]:,} 次物理执行' not in index
        or "通过后不再执行" not in index
        or "lexmount/webarena-verified" not in method
        or "评测答案和通过条件没有为浏览器适配而放宽" not in method
    ):
        raise ValueError("catalog report presentation contract is incomplete")
    with zipfile.ZipFile(archive) as bundle:
        corrupt = bundle.testzip()
        if corrupt is not None:
            raise ValueError(f"catalog report archive is corrupt: {corrupt}")
    return manifest


def build_main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", default="reports/trajectory-replay")
    parser.add_argument(
        "--content",
        type=Path,
        default=None,
        help="Git-external frozen article and illustrations",
    )
    parser.add_argument(
        "--content-bindings",
        type=Path,
        default=None,
    )
    parser.add_argument("--interpretation-audit", type=Path)
    parser.add_argument(
        "--include-english",
        action="store_true",
        help="Explicitly generate the English report and its assets",
    )
    add_language_argument(parser)
    args = parser.parse_args(argv)
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    dataset_path = args.dataset or bound_dataset_path(profile)
    if args.dataset is not None:
        expected = (profile.get("dataset") or {}).get("sha256")
        if not isinstance(expected, str):
            raise ValueError("explicit report dataset requires a profile hash binding")
        dataset_path = verified_dataset_path(dataset_path, expected)
    review_path = args.review or Path("research/trajectory-replay-failure-review.json")
    def build(interpretation_path=None):
        return build_webarena_replay_report(
            results_path=args.results,
            dataset_path=dataset_path,
            review_path=review_path,
            profile_path=args.profile,
            output_dir=args.output,
            include_english=args.include_english,
            language=args.language or primary_report_language(Path(args.output)),
            interpretation_path=interpretation_path,
            interpretation_audit_path=args.interpretation_audit,
        )

    if args.content is not None or args.content_bindings is not None:
        content_source = args.content or data_home() / "report-assets/trajectory-replay"
        bindings_path = args.content_bindings or Path("research/trajectory-replay-content.json")
        bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="replay-content-") as directory:
            content = Path(directory).resolve()
            copy_bound_assets(content_source, content, bindings)
            manifest = build(content / "trajectory-replay-interpretation.json")
    else:
        manifest = build()
    print(json.dumps(manifest, ensure_ascii=False))
