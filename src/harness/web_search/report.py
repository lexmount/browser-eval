"""Small bilingual Web Search report built from frozen calls, never invented scores."""

import argparse
import base64
import copy
import gzip
import json
import math
import posixpath
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from statistics import median
from typing import Any
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from harness.record_io import bytes_sha256, same_json_record
from harness.report_languages import (
    REPORT_PAGE_MARKER,
    check_language_contract,
    generated_languages,
    resolve_report_language,
    select_report_language,
    write_language_contract,
)
from harness.reporting.mermaid import mermaid_script_attributes
from harness.reporting.presentation import check_presentation
from harness.reporting.presentation import esc as escape_text
from harness.reporting.publication import atomic_write
from harness.reporting.versions import (
    FILENAME,
    bind_build_version,
    check_build_version,
    software_identity,
)
from harness.web_fetch.publication_privacy import SENSITIVE_KEY, project_file
from harness.web_search.comparison import render_comparison
from harness.web_search.core import (
    aggregate,
    execution_resources,
    normalize_response,
    normalized_answer,
    request_body,
    request_cost,
    validate_profile,
    verify_bundle,
    write_json,
)
from harness.web_search.diagnostics import error_evidence, operational
from harness.web_search.messages import report_text, zh_text
from harness.web_search.pages import reader_pages
from harness.web_search.profiles import _bound, load_profile
from harness.web_search.relevance import (
    export_annotations,
    import_annotations,
    report_authority,
    verify_protocol,
    verify_provenance,
)
from harness.web_search.review import (
    coverage_percent,
    factual_quality_exclusions,
    render_detail,
    render_summary,
    validate_public_review,
)
from harness.web_search.revisions import (
    apply_identity_correction,
    apply_report_revision,
    collected_projection,
    collection_protocol,
    validate_revision_binding,
)
from harness.web_search.runner import reference_available, reference_changes
from lexbench_webfetch.paths import contained_path
from lexbench_websearch import answer_rules
from lexbench_websearch.evidence_quality import CONTAMINATION_RULE, factual_sensitivity
from lexbench_websearch.inputs import definition_path, implementation_sources
from lexbench_websearch.relevance import paired_bootstrap
from lexbench_websearch.relevance import score as benchmark_relevance_score
from lexbench_websearch.relevance import summary as relevance_summary
from lexbench_websearch.scoring import (
    answer_evidence_key,
    canonical_url,
    classify,
    evaluate,
    factual_evidence,
    in_domain,
)

CHINESE_SEPARATORS = re.compile("[\u3001\uff1a\uff1b]")
ASSETS = Path(__file__).with_name("assets")
STYLE = ASSETS.joinpath("report.css").read_text(encoding="utf-8")
EVIDENCE_SCRIPT = ASSETS.joinpath("native-evidence-result.js").read_text(encoding="utf-8")
SCRIPT = EVIDENCE_SCRIPT + ASSETS.joinpath("report.js").read_text(encoding="utf-8").replace(
    "/*mermaid-script-attributes*/", mermaid_script_attributes("s")
)


# Search pages keep their established literal rendering of an absent value.
esc = partial(escape_text, none="None")


def fmt(value):
    return "—" if value is None else f"{value:,.2f}"


def table(headers, rows, sortable=True, *, bounded=False, primary_column=None, significance=False, query_count=None):
    head = "".join(
        f'<th aria-sort="{"descending" if i == primary_column else "none"}"' + (' data-primary-metric' if i == primary_column else '') + f'><button type="button" aria-label="{esc(h)}" data-sort-column="{i}">{esc(h)}</button></th>'
        if sortable
        else f"<th>{esc(h)}</th>"
        for i, h in enumerate(headers)
    )
    return (
        ('<div class="scroll inventory" tabindex="0"><table' if bounded else '<div class="scroll" tabindex="0"><table')
        + (' data-sort-controls="buttons"' if sortable else '')
        + (f' data-significance="paired-query-bootstrap/1" data-query-count="{query_count}"' if significance else '')
        + (' class="compact"' if len(headers) == 2 else "")
        + "><thead><tr>"
        + head
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def cells(values):
    return "<tr>" + "".join("<td>" + v + "</td>" for v in values) + "</tr>"


def _redact_url_userinfo(raw):
    """Redact only HTTP URL authority credentials, preserving every other byte."""
    def replace(match):
        token = match.group(0)
        candidate = re.split(rb"\]\(|[,;](?:mailto:|https?://)", token, maxsplit=1, flags=re.I)[0]
        authority = candidate.split(b"://", 1)[1].split(b"/", 1)[0].split(b"?", 1)[0].split(b"#", 1)[0]
        for separator in (b"'", b")"):
            if separator in authority and b":" not in authority.split(separator, 1)[0]:
                candidate = candidate.split(separator, 1)[0]
        try:
            parsed = urlsplit(candidate.decode("utf-8"))
            if not parsed.hostname or parsed.username is None:
                return token
        except (ValueError, UnicodeError):
            return token
        start = candidate.index(b"://") + 3
        end = start + len(parsed.netloc.encode("utf-8"))
        netloc = candidate[start:end]
        redacted = candidate[:start] + b"REDACTED:REDACTED@" + netloc.rsplit(b"@", 1)[1] + candidate[end:]
        return redacted + token[len(candidate):]
    return re.sub(rb"https?://[^\s\"<>\\]+", replace, raw, flags=re.I)


def public_calls(profile, rows, source, sources):
    """Keep measured outcomes; redact URL credentials and audit billing corrections."""
    rows = copy.deepcopy(rows)
    methods = {m["id"]: m for m in profile["methods"]}
    files, redactions, corrections = {}, [], []
    for row in rows:
        attempts = row.get("attempts", [])
        final_path = row["evidence"]["path"]
        if attempts and attempts[-1]["evidence"]["path"] != final_path:
            raise ValueError("Final response evidence differs from the final physical attempt")
        for attempt in attempts or [row]:
            binding = attempt["evidence"]
            raw = (sources.get(row.get("collection", row.get("cohort")), source) / binding["path"]).read_bytes()
            public = _redact_url_userinfo(raw)
            try:
                envelope = json.loads(public)
            except ValueError:
                envelope = None
            headers = envelope.get("headers") if isinstance(envelope, dict) else None
            if isinstance(headers, dict):
                private_headers = {
                    key: value for key, value in headers.items()
                    if re.fullmatch(SENSITIVE_KEY, key.encode(), re.I)
                }
                projected, proof = project_file("headers.json", json.dumps(private_headers).encode())
                if proof:
                    headers.update(json.loads(projected))
                    public = json.dumps(envelope, ensure_ascii=False).encode()
            if public != raw:
                redactions.append({"path": binding["path"], "original_sha256": bytes_sha256(raw),
                                   "public_sha256": bytes_sha256(public), "rule": "URL userinfo and credential response headers redacted; ranked results and outcome unchanged"})
                binding["sha256"] = bytes_sha256(public)
            files[binding["path"]] = public
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = None
            method = methods[row["method"]]
            cost = attempt["cost"]
            if method.get("billing") == "http-200-nonempty":
                cost = request_cost(method, attempt["http_status"], attempt.get("transport_exit", 0), payload)
            if cost != attempt["cost"]:
                corrections.append({"path": binding["path"], "original_cost": attempt["cost"], "cost": cost,
                                    "rule": "Provider nonempty billing does not depend on local URL validation"})
                attempt["cost"] = cost
        if attempts:
            row["evidence"] = copy.deepcopy(attempts[-1]["evidence"])
            row["cost"] = sum(a["cost"] for a in attempts) if all(a["cost"] is not None for a in attempts) else None
    return rows, files, redactions, corrections


def validate_native_answer(row, payload, method):
    """Reject substituted or fabricated answer text at every public input boundary."""
    expected = normalized_answer(payload, method) if row['http_status'] == 200 and not row.get('error') else None
    if (expected is not None and row.get('answer') != expected
            or expected is None and row.get('answer')):
        raise ValueError('Generated answer differs from the native response')


def load_run(source):
    profile = json.loads((source / "profile.json").read_text(encoding="utf-8"))
    validate_profile(profile)
    receipt = json.loads((source / "receipt.json").read_text(encoding="utf-8"))
    synthetic = receipt.get("synthetic") is True and receipt.get("certified") is False
    if receipt.get("synthetic") is True and not synthetic:
        raise ValueError("Synthetic evidence cannot be certified")
    if bytes_sha256((source / "profile.json").read_bytes()) != receipt["profile_sha256"]:
        raise ValueError("Profile hash mismatch")
    raw = (source / "results.jsonl").read_bytes()
    if bytes_sha256(raw) != receipt["results_sha256"]:
        raise ValueError("Results hash mismatch")
    rows = [json.loads(line) for line in raw.splitlines()]
    expected = {
        (c["id"], m["id"], r)
        for c in profile["cases"]
        for m in profile["methods"]
        for r in range(profile["protocol"]["rounds"])
    }
    actual = [(r["case_id"], r["method"], r["round"]) for r in rows]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("Incomplete or duplicate call matrix")
    if not receipt.get("complete") or receipt["call_count"] != len(rows):
        raise ValueError("Incomplete receipt")
    if "physical_call_count" in receipt and receipt["physical_call_count"] != sum(
        len(r.get("attempts", [r["evidence"]])) for r in rows
    ):
        raise ValueError("Physical call count mismatch")
    verify_bundle(
        source,
        [r["evidence"] for r in rows]
        + [a["evidence"] for r in rows for a in r.get("attempts", [])]
        + receipt.get("bindings", []),
    )
    # Preserve the frozen receipt, but expose incomplete historical reference transfers.
    receipt = dict(receipt)
    receipt["reference_capture_issues"] = []
    for phase in ("before", "after"):
        reference_file = source / f"references-{phase}.json"
        if reference_file.exists():
            for url, capture in json.loads(reference_file.read_text(encoding="utf-8")).items():
                if profile["protocol"].get("reference_scope") == "identity":
                    actual_available = reference_available(
                        profile, url, capture, (source / capture["path"]).read_bytes()
                    )
                    if actual_available != capture["available"]:
                        raise ValueError("Reference identity qualification mismatch")
                if capture.get("transport_exit") or capture.get("http_status") != 200:
                    receipt["reference_capture_issues"].append(
                        {
                            "phase": phase,
                            "url": url,
                            "transport_exit": capture.get("transport_exit"),
                        }
                    )
    if receipt.get("certified") and receipt["reference_capture_issues"]:
        raise ValueError("Certified source capture is incomplete")
    if receipt.get("certified") and profile["protocol"].get("reference_scope") == "identity":
        before = json.loads((source / "references-before.json").read_text(encoding="utf-8"))
        after = json.loads((source / "references-after.json").read_text(encoding="utf-8"))
        expected_refs = {u for c in profile["cases"] for u in c.get("anchors", [])}
        expected_refs.update(profile.get("reference_artifacts", {}))
        if (
            set(before) != expected_refs
            or set(after) != expected_refs
            or reference_changes(profile, before, after)
        ):
            raise ValueError("Incomplete or failed identity reference scope")
    bycase = {c["id"]: c for c in profile["cases"]}
    bymethod = {m["id"]: m for m in profile["methods"]}
    for r in rows:
        method = bymethod[r["method"]]
        expected_request = request_body(method["adapter"], bycase[r["case_id"]], profile["protocol"], method.get("output_options"))
        if "request" not in r:
            if not synthetic:
                raise ValueError("Measured result lacks its frozen request contract")
        elif not same_json_record(r["request"], expected_request):
            raise ValueError("Measured request differs from the frozen profile contract")
        for attempt in r.get("attempts", []):
            if "request" in attempt and not same_json_record(attempt["request"], expected_request):
                raise ValueError("Physical attempt request differs from the frozen profile contract")
        if r["id"] != f"{r['case_id']}-{r['method']}-r{r['round']}":
            raise ValueError("Unsafe call identity")
        attempts = r.get("attempts")
        if attempts is not None:
            if not 1 <= len(attempts) <= 1 + int(bool(profile["protocol"].get("retry"))):
                raise ValueError("Invalid retry count")
            if attempts[-1]["evidence"] != r["evidence"]:
                raise ValueError("Final response must determine the result")
            if r["elapsed_ms"] + 1 < sum(a["elapsed_ms"] for a in attempts):
                raise ValueError("User-visible latency excludes a physical call")
            if all(a["cost"] is not None for a in attempts):
                if abs(r["cost"] - sum(a["cost"] for a in attempts)) > 1e-8:
                    raise ValueError("Retry cost mismatch")
        if not r["error"] and r["http_status"] == 200:
            payload = json.loads((source / r["evidence"]["path"]).read_bytes())
            normalized = normalize_response(payload, bymethod[r["method"]], profile["protocol"], r.get("request"))
            if normalized != r["results"]:
                raise ValueError("Normalized response mismatch")
            validate_native_answer(r, payload, bymethod[r['method']])
        elif r.get('answer'):
            raise ValueError('Failed calls cannot contain a generated answer')
        if evaluate(bycase[r["case_id"]], r["results"]) != r["metrics"]:
            raise ValueError("Stored score mismatch")
    return profile, rows, receipt


def _execution_contract(protocol):
    """Fields that must match for same-budget cross-cohort comparisons."""
    return {key: protocol.get(key) for key in (
        "top_k", "rounds", "score_round", "concurrency", "server_timeout_ms",
        "client_timeout_s", "interval_s", "retry", "order", "as_of", "locale_policy",
    )}


def scoring_rows(profile, rows):
    """Select the declared quality round; timing and evidence retain every round."""
    return [row for row in rows if row["round"] == profile["protocol"]["score_round"]]


def _check_collection_provenance(receipt, previous):
    if previous and bool(receipt.get("synthetic")) != bool(previous[0]["collection_receipt"].get("synthetic")):
        raise ValueError("Cannot combine synthetic and measured collections")


def load_report_runs(source, cohorts, labels, method_ids=None, *, composition="cases", source_methods=None, source_cases=None):
    """Compose separately verified runs; never pretend they were one collection."""
    inputs = [("primary", source), *cohorts]
    if len({name for name, _ in inputs}) != len(inputs):
        raise ValueError("Duplicate cohort name")
    if composition not in {"cases", "methods", "matrix"}:
        raise ValueError("Unsupported collection composition")
    if (source_methods is not None or source_cases is not None) and composition != 'matrix':
        raise ValueError('Per-source method selection requires matrix composition')
    if composition in {'methods','matrix'}:
        if composition == 'matrix':
            if (not isinstance(source_methods,dict) or set(source_methods) != {name for name,_ in inputs}
                    or any(not isinstance(ids,list) or not ids or len(ids) != len(set(ids)) for ids in source_methods.values())):
                raise ValueError('Matrix composition requires explicit methods for every source')
        if source_cases is not None and (
                not isinstance(source_cases, dict) or set(source_cases) != {name for name, _ in inputs}
                or any(not isinstance(ids, list) or not ids or len(ids) != len(set(ids))
                       for ids in source_cases.values())):
            raise ValueError("Matrix composition requires explicit cases for every source")
        return _load_method_runs(inputs, labels, method_ids, source_methods=source_methods,
                                 source_cases=source_cases)
    profile = None
    rows, receipts, groups, paths = [], [], [], {}
    seen_cases, seen_evidence = set(), set()
    for name, path in inputs:
        if not name.replace("-", "").isalnum():
            raise ValueError("Unsafe cohort name")
        current, calls, receipt = load_run(path)
        _check_collection_provenance(receipt, receipts)
        if method_ids is not None:
            methods = {method["id"]: method for method in current["methods"]}
            if not method_ids or not set(method_ids).issubset(methods):
                raise ValueError("Cohort does not contain every comparison participant")
            current = copy.deepcopy(current)
            if current.get("execution_policy"):
                current["collection_methods"] = current["methods"]
            current["methods"] = [methods[method_id] for method_id in method_ids]
            calls = [row for row in calls if row["method"] in method_ids]
        if profile is None:
            profile = copy.deepcopy(current)
            profile["cases"] = []
            profile["factual_quality_exclusions"] = []
            profile["reference_adjudications"] = []
        elif current["methods"] != profile["methods"] or _execution_contract(current["protocol"]) != _execution_contract(profile["protocol"]) or current.get("execution_policy") != profile.get("execution_policy"):
            raise ValueError("Cohort participants or execution protocols differ")
        case_ids = {c["id"] for c in current["cases"]}
        evidence = {r["evidence"]["path"] for r in calls}
        if seen_cases & case_ids or seen_evidence & evidence:
            raise ValueError("Overlapping cohort case or evidence identities")
        seen_cases.update(case_ids)
        seen_evidence.update(evidence)
        profile["cases"].extend(dict(c, cohort=name) for c in current["cases"])
        profile["factual_quality_exclusions"].extend(current.get("factual_quality_exclusions", []))
        profile["reference_adjudications"].extend(current.get("reference_adjudications", []))
        profile.setdefault("reference_checks", {}).update(current.get("reference_checks", {}))
        profile.setdefault("reference_artifacts", {}).update(current.get("reference_artifacts", {}))
        rows.extend(dict(r, cohort=name) for r in calls)
        groups.append(
            {
                "id": name,
                "label": labels.get(name, {"en": name, "zh": name}),
                "case_ids": sorted(case_ids),
                "protocol": current["protocol"],
            }
        )
        receipts.append(
            {
                "cohort": name,
                "profile_sha256": bytes_sha256((path / "profile.json").read_bytes()),
                "results_sha256": bytes_sha256((path / "results.jsonl").read_bytes()),
                "receipt_sha256": bytes_sha256((path / "receipt.json").read_bytes()),
                "collection_receipt": receipt,
            }
        )
        paths[name] = path
    profile["report_cohorts"] = groups
    if any(group["protocol"].get("preserve_publisher_queries") for group in groups):
        profile["protocol"]["preserve_publisher_queries"] = True
    # A batch-specific prose description is not the combined report's population.
    # Frozen source profiles remain bound separately by their original hashes.
    profile["protocol"].pop("comparison", None)
    validate_profile(profile, execution_methods=profile.get("collection_methods"))
    return (
        profile,
        rows,
        {
            "certified": all(r["collection_receipt"].get("certified") for r in receipts),
            "synthetic": all(r["collection_receipt"].get("synthetic") for r in receipts),
            "complete": True,
            "call_count": len(rows),
            "separate_collections": receipts,
        },
        paths,
    )


def _merge_matrix_references(profile, current):
    """Keep all source assessments, rejecting contradictory reference contracts."""
    for key in ("reference_checks", "reference_artifacts"):
        merged = profile.setdefault(key, {})
        for identity, value in current.get(key, {}).items():
            if identity in merged and merged[identity] != value:
                raise ValueError("Matrix collections contain conflicting reference contracts: " + key)
            merged[identity] = copy.deepcopy(value)
    exclusions = set(profile.get("factual_quality_exclusions", []))
    exclusions.update(current.get("factual_quality_exclusions", []))
    profile["factual_quality_exclusions"] = sorted(exclusions)
    decisions = {item["case_id"]: item for item in profile.get("reference_adjudications", [])}
    for item in current.get("reference_adjudications", []):
        cid = item["case_id"]
        if cid in decisions and decisions[cid] != item:
            raise ValueError("Matrix collections contain conflicting reference adjudications")
        decisions[cid] = copy.deepcopy(item)
    profile["reference_adjudications"] = [decisions[cid] for cid in sorted(decisions)]


def _load_method_runs(inputs, labels, method_ids, *, source_methods=None, source_cases=None):
    """Add disjoint providers on identical frozen tasks without repeating calls."""
    profile = None
    rows, receipts, paths, batches = [], [], {}, []
    evidence_paths, method_names = set(), set()
    matrix = source_methods is not None
    case_definitions, method_definitions, selected_cells = {}, {}, set()
    for name, path in inputs:
        if not name.replace("-", "").isalnum():
            raise ValueError("Unsafe cohort name")
        current, calls, receipt = load_run(path)
        _check_collection_provenance(receipt, receipts)
        execution_methods = current['methods']
        if matrix:
            available = {m['id']:m for m in current['methods']}
            chosen = source_methods[name]
            if not set(chosen).issubset(available):
                raise ValueError('Selected methods are unavailable in source: ' + name)
            current = copy.deepcopy(current)
            current['methods'] = [available[mid] for mid in chosen]
            calls = [r for r in calls if r['method'] in chosen]
            if source_cases is not None:
                selected_cases = set(source_cases[name])
                if not selected_cases.issubset({case['id'] for case in current['cases']}):
                    raise ValueError("Selected cases are unavailable in source: " + name)
                current['cases'] = [case for case in current['cases'] if case['id'] in selected_cases]
                calls = [row for row in calls if row['case_id'] in selected_cases]
                current['factual_quality_exclusions'] = [
                    cid for cid in current.get('factual_quality_exclusions', []) if cid in selected_cases]
                current['reference_adjudications'] = [
                    item for item in current.get('reference_adjudications', [])
                    if item['case_id'] in selected_cases]
        elif current.get("execution_policy") or current["protocol"]["concurrency"] != 1:
            raise ValueError("Method composition requires serial collections")
        ids = {m["id"] for m in current["methods"]}
        if not matrix and method_names & ids:
            raise ValueError("Overlapping method collections")
        method_names.update(ids)
        if profile is None:
            profile = copy.deepcopy(current)
            profile["methods"] = []
        elif not matrix:
            def controls(p):
                return {**{k: v for k, v in _execution_contract(p).items() if k not in {"as_of", "order"}},
                        "normalize_top_k": p.get("normalize_top_k"),
                        "result_url_resolution": p.get("result_url_resolution")}
            if current["cases"] != profile["cases"] or controls(current["protocol"]) != controls(profile["protocol"]):
                raise ValueError("Method collections require identical tasks and measurement controls")
            for key in ("reference_checks", "reference_artifacts", "factual_quality_exclusions", "reference_adjudications"):
                if current.get(key) != profile.get(key):
                    raise ValueError("Method collection reference contracts differ")
        if matrix:
            _merge_matrix_references(profile, current)
            if any(current['protocol'][k] != profile['protocol'][k] for k in ('top_k','rounds','score_round')):
                raise ValueError('Matrix collections require the same cutoff and score rounds')
            for case in current['cases']:
                if case['id'] in case_definitions and case_definitions[case['id']] != case:
                    raise ValueError('Matrix collections contain conflicting task definitions')
                case_definitions.setdefault(case['id'],copy.deepcopy(case))
            for method in current['methods']:
                if method['id'] in method_definitions and method_definitions[method['id']] != method:
                    raise ValueError('Matrix collections contain conflicting method definitions')
                method_definitions.setdefault(method['id'],copy.deepcopy(method))
            cells = {(r['case_id'],r['method'],r['round']) for r in calls}
            if selected_cells & cells:
                raise ValueError('Overlapping method-by-task cells')
            selected_cells.update(cells)
        native_evidence = {e["path"] for r in calls for e in [r["evidence"], *[a["evidence"] for a in r.get("attempts", [])]]}
        if native_evidence & evidence_paths:
            raise ValueError("Overlapping method evidence paths")
        evidence_paths.update(native_evidence)
        profile["methods"].extend(copy.deepcopy(current["methods"]))
        rows.extend(dict(r, cohort="primary", collection=name) for r in calls)
        receipts.append({"cohort": name, "profile_sha256": bytes_sha256((path / "profile.json").read_bytes()),
                         "results_sha256": bytes_sha256((path / "results.jsonl").read_bytes()),
                         "receipt_sha256": bytes_sha256((path / "receipt.json").read_bytes()), "collection_receipt": receipt})
        batches.append({"id": name, "methods": sorted(ids), "as_of": current["protocol"].get("as_of"),
                        "order": current["protocol"].get("order", "complete provider batches"),
                        "started_at": min((r["timestamp"] for r in calls if r.get("timestamp")), default=receipt.get("started_at")),
                        "ended_at": max((r["timestamp"] for r in calls if r.get("timestamp")), default=receipt.get("ended_at"))})
        if matrix:
            receipts[-1]['selected_methods'] = list(source_methods[name])
            if source_cases is not None:
                receipts[-1]['selected_cases'] = list(source_cases[name])
            batches[-1].update(protocol=copy.deepcopy(current['protocol']),
                               execution_policy=copy.deepcopy(current.get('execution_policy')),
                               execution_methods=copy.deepcopy(execution_methods))
        paths[name] = path
    if matrix:
        profile['cases'] = list(case_definitions.values())
        profile['methods'] = list(method_definitions.values())
        profile['collection_composition'] = 'matrix'
        # This is a read-only scoring view; source batches retain their admission contracts.
        profile.pop('execution_policy',None)
        profile.pop('collection_methods',None)
        profile['protocol']['concurrency'] = 1
        profile['protocol']['order'] = 'complete provider batches'
    if method_ids is not None:
        available = {m["id"]: m for m in profile["methods"]}
        if not method_ids or not set(method_ids).issubset(available):
            raise ValueError("Collections do not contain every comparison participant")
        profile["methods"] = [available[mid] for mid in method_ids]
        rows = [r for r in rows if r["method"] in method_ids]
    if matrix:
        expected = {(c['id'],m['id'],round_id) for c in profile['cases'] for m in profile['methods']
                    for round_id in range(profile['protocol']['rounds'])}
        actual = {(r['case_id'],r['method'],r['round']) for r in rows}
        if actual != expected or len(actual) != len(rows):
            raise ValueError('Matrix composition requires the complete method-by-task population')
    profile["cases"] = [dict(c, cohort="primary") for c in profile["cases"]]
    profile["method_collections"] = batches
    profile["report_cohorts"] = [{"id": "primary", "label": labels.get("primary", {"en": "Shared tasks", "zh": zh_text("Shared tasks")}),
                                  "case_ids": [c["id"] for c in profile["cases"]], "protocol": copy.deepcopy(profile["protocol"])}]
    profile["protocol"].pop("comparison", None)
    validate_profile(profile)
    return profile, rows, {"certified": all(r["collection_receipt"].get("certified") for r in receipts),
                           "synthetic": all(r["collection_receipt"].get("synthetic") for r in receipts),
                           "complete": True, "call_count": len(rows), "separate_collections": receipts}, paths


def _language_notice(output, languages):
    if languages == ["zh"] and (output / "en").exists():
        for directory in sorted((output / "en").rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
    write_language_contract(output, include_english="en" in languages)
    return ["en/index.html"] if languages == ["zh"] else []


def _check_report_languages(output, manifest):
    check_language_contract(output)
    if list(generated_languages(output)) != manifest.get("languages"):
        raise ValueError("Report language metadata differs from the report manifest")


def assessment_profile(profile, relevance, review):
    """Describe current assessment without rewriting historical collection metadata."""
    protocol = dict(profile["protocol"])
    exclusions = sorted(set(profile.get("factual_quality_exclusions", [])) | set(factual_quality_exclusions(review)))
    if relevance:
        protocol["quality"] = "Frozen pooled title/snippet relevance grades (0..3); deterministic aggregation. See report_assessment for judge provenance."
    if review:
        protocol["review"] = "Complete result audit with per-output opinions and finding adjudications." if review["scope"] == "all" else "Failure-only result audit; successful outputs outside the review are counted explicitly."
    return dict(profile, protocol=protocol, **({"independent_result_review_scope": review["scope"], "factual_quality_exclusions": exclusions} if review else {}))


@dataclass(frozen=True)
class ReportInputs:
    """Loaded and validated evidence that every bundle and page stage reads."""

    output: Path
    languages: list[str]
    site_prefix: str
    content: Path | None
    content_bytes: bytes | None
    authored: dict[str, Any]
    reading_sections: list[dict[str, Any]]
    authority: dict[str, Any]
    profile: dict[str, Any]
    collection_profile: dict[str, Any]
    rows: list[dict[str, Any]]
    selected: list[dict[str, Any]]
    receipt: dict[str, Any]
    public_files: dict[str, bytes]
    redactions: list[dict[str, Any]]
    billing_corrections: list[dict[str, Any]]
    scoring_revision: dict[str, Any] | None
    assessment_revision: dict[str, Any] | None
    review_data: dict[str, Any] | None
    review_percent: int | float | None
    relevance_data: dict[str, Any] | None
    relevance_scores: list[dict[str, Any]] | None
    overview_methods: list[dict[str, Any]]
    overview_rows: list[dict[str, Any]]
    overview_summary: dict[str, Any]
    all_call_summary: dict[str, Any]
    payloads: dict[str, Any]
    diagnostics: Any
    level_views: dict[str, Any] | None = None
    level_files: dict[str, bytes] | None = None
    level_binding: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReportEdition:
    """One language edition: its route prefix, navigation, notice and the shared route list."""

    lang: str
    zh: bool
    prefix: str
    t: Callable[..., str]
    nav: list[tuple[str, str]]
    state: str
    routes: list[str]


def _validate_build_options(language: str, site_prefix: str) -> None:
    if language not in ("en", "zh"):
        raise ValueError("Unsupported primary report language; choose en or zh")
    if site_prefix and ("/" in site_prefix or not site_prefix.replace("-", "").isalnum()):
        raise ValueError("Site prefix must be one safe directory name")


def _read_authored_content(content: Path | None) -> tuple[bytes | None, dict[str, Any], list[dict[str, Any]]]:
    content_bytes = content.read_bytes() if content else None
    authored = json.loads(content_bytes) if content_bytes is not None else {"sections": []}
    reading_sections = authored.get("reading_sections", [])
    if len({p["key"] for p in reading_sections}) != len(reading_sections) or any(
        not p["key"].replace("-", "").isalnum() for p in reading_sections
    ):
        raise ValueError("Unsafe or overlapping reading section")
    return content_bytes, authored, reading_sections


def _load_assessment_authority(profile_path: str | Path | None) -> dict[str, Any]:
    authority = {}
    if profile_path is not None:
        selector = json.loads(Path(profile_path).read_bytes())
        if selector.get("assessment_authority"):
            authority = _bound(Path(profile_path).resolve().parent, selector["assessment_authority"])
    return authority


def _load_collection(
    profile_path: str | Path | None, source: Path | None, cohorts: Sequence[tuple[str, Path]], authored: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    sources = {}
    if cohorts:
        if not source:
            raise ValueError("Additional cohorts require a primary source")
        profile, rows, receipt, sources = load_report_runs(
            source, cohorts, authored.get("cohort_labels", {}), authored.get("comparison_methods"),
            composition=authored.get("collection_composition", "cases"),
            source_methods=authored.get('collection_methods'),
            source_cases=authored.get('collection_cases')
        )
    elif source:
        profile, rows, receipt = load_run(source)
    else:
        profile = load_profile(profile_path)
        validate_profile(profile)
        rows = []
        receipt = {"certified": False}
    return profile, rows, receipt, sources


def _apply_revisions(
    correction: Path | None,
    authored: dict[str, Any],
    authority: dict[str, Any],
    profile: dict[str, Any],
    rows: list[dict[str, Any]],
    public_files: dict[str, bytes],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None]:
    scoring_revision = None
    if correction:
        revision = json.loads(correction.read_text(encoding="utf-8"))
        profile, rows, changes = apply_identity_correction(profile, rows, revision)
        scoring_revision = {
            "contract": revision,
            "changes": changes,
            "sha256": bytes_sha256(correction.read_bytes()),
        }
    assessment_revision = None
    if authored.get("assessment_revision"):
        validate_revision_binding(authored["assessment_revision"], normalizer=normalize_response, authority=authority)
        profile, rows, changes = apply_report_revision(profile, rows, authored["assessment_revision"], artifacts=public_files, normalizer=normalize_response)
        assessment_revision = {"contract": authored["assessment_revision"], "changes": changes, "implementation_sha256": {name: bytes_sha256(path.read_bytes()) for name, path in implementation_sources().items()}}
    return profile, rows, scoring_revision, assessment_revision


def relevance_scope(profile, rows):
    """Keep historical judgments on their declared configurations only."""
    ids = profile.get("relevance_methods")
    if ids is None:
        return profile, rows
    available = {m["id"]: m for m in profile["methods"]}
    if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)) or not set(ids).issubset(available):
        raise ValueError("Invalid relevance method scope")
    return dict(profile, methods=[available[mid] for mid in ids]), [r for r in rows if r["method"] in ids]


def score_relevance(profile, rows, plan, labels):
    scoped, selected = relevance_scope(profile, rows)
    return benchmark_relevance_score(scoped, selected, plan, labels)


def relevance_scope_notice(profile, zh):
    if not profile.get("relevance_methods"):
        return ""
    labels = {m["id"]: m["label"] for m in profile["methods"]}
    return ('<p data-relevance-method-scope>' + report_text(
        "Relevance labels cover {methods}. Other configurations remain unscored; labels are not transferred between provider responses.",
        zh, methods=", ".join(labels[mid] for mid in profile["relevance_methods"])) + '</p>')


def _load_assessment(
    output: Path,
    review: Path | None,
    relevance: Path | None,
    authored: dict[str, Any],
    authority: dict[str, Any],
    profile: dict[str, Any],
    selected: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, int | float | None, dict[str, Any] | None]:
    """Validate the review and relevance inputs; a validated review is written at once."""
    review_data = json.loads(review.read_text(encoding="utf-8")) if review else None
    relevance_data = json.loads(relevance.read_text(encoding="utf-8")) if relevance else None
    review_percent = None
    if review is not None:
        validate_public_review(profile, selected, review_data, authority=authority, relevance=relevance_data)
        review_percent = coverage_percent(review_data)
        write_json(output / "independent-review.json", review_data)
    if relevance_data:
        relevance_profile, relevance_rows = relevance_scope(profile, selected)
        if "plan" not in relevance_data:
            relevance_data = import_annotations(relevance_profile, relevance_rows, relevance_data)
        verify_provenance(relevance_profile, relevance_rows, relevance_data, authority=authority)
        verify_protocol(relevance_profile, relevance_data, authored.get("relevance_revision"), authority=authority)
    return review_data, review_percent, relevance_data


def _response_payloads(rows: list[dict[str, Any]], public_files: dict[str, bytes]) -> dict[str, Any]:
    payloads = {}
    for r in rows:
        try:
            payloads[r["id"]] = json.loads(
                public_files[r["evidence"]["path"]]
            )
        except ValueError:
            payloads[r["id"]] = {}
    return payloads


def _load_report_inputs(
    output: str | Path,
    *,
    profile_path: str | Path | None,
    source: Path | None,
    content: Path | None,
    language: str,
    include_english: bool,
    correction: Path | None,
    relevance: Path | None,
    review: Path | None,
    site_prefix: str,
    cohorts: Sequence[tuple[str, Path]],
) -> ReportInputs:
    content_bytes, authored, reading_sections = _read_authored_content(content)
    authority = _load_assessment_authority(profile_path)
    profile, rows, receipt, sources = _load_collection(profile_path, source, cohorts, authored)
    if "relevance_methods" in authored:
        profile = dict(profile, relevance_methods=authored["relevance_methods"])
        relevance_scope(profile, rows)
    collection_profile = profile
    rows, public_files, redactions, billing_corrections = public_calls(profile, rows, source, sources)
    profile, rows, scoring_revision, assessment_revision = _apply_revisions(correction, authored, authority, profile, rows, public_files)
    profile, rows = project_comparison(profile, rows, authored)
    materialized_paths = {e["path"] for r in rows for e in [r["evidence"], *[a["evidence"] for a in r.get("attempts", [])]]}
    redactions = [r for r in redactions if r["path"] in materialized_paths]
    billing_corrections = [r for r in billing_corrections if r["path"] in materialized_paths]
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    languages = ["zh", "en"] if include_english or language == "en" else ["zh"]
    selected = scoring_rows(profile, rows)
    review_data, review_percent, relevance_data = _load_assessment(output, review, relevance, authored, authority, profile, selected)
    profile = assessment_profile(profile, relevance_data, review_data)
    relevance_scores = (
        score_relevance(profile, selected, relevance_data["plan"], relevance_data["labels"])
        if relevance_data else None
    )
    level_views, level_files, level_binding = None, {}, None
    if authored.get('output_level_assessments'):
        from harness.web_search.output_levels import load_assessed_levels

        if relevance or review or authored.get('presentation') != 'comparison-report':
            raise ValueError('Layered reports require comparison presentation and per-level assessments')
        binding = authored['output_level_assessments']
        verify_bundle(content.parent, [binding])
        path = content.parent/binding['path']
        level_views, files = load_assessed_levels(path, profile, selected, authority=authority)
        level_files = {'level-assessments/' + name: raw for name, raw in files.items()}
        level_binding = dict(path='level-assessments/' + path.name, sha256=bytes_sha256(path.read_bytes()))
    overview_methods = scoped_overview_methods(profile, authored)
    overview_rows = scoped_overview_rows(profile, rows, authored)
    overview_summary = aggregate(scoring_rows(profile, overview_rows))
    all_call_summary = aggregate(rows)
    payloads = _response_payloads(rows, public_files)
    diagnostics = operational(rows, payloads, profile["methods"])
    return ReportInputs(
        output=output,
        languages=languages,
        site_prefix=site_prefix,
        content=content,
        content_bytes=content_bytes,
        authored=authored,
        reading_sections=reading_sections,
        authority=authority,
        profile=profile,
        collection_profile=collection_profile,
        rows=rows,
        selected=selected,
        receipt=receipt,
        public_files=public_files,
        redactions=redactions,
        billing_corrections=billing_corrections,
        scoring_revision=scoring_revision,
        assessment_revision=assessment_revision,
        review_data=review_data,
        review_percent=review_percent,
        relevance_data=relevance_data,
        relevance_scores=relevance_scores,
        overview_methods=overview_methods,
        overview_rows=overview_rows,
        overview_summary=overview_summary,
        all_call_summary=all_call_summary,
        payloads=payloads,
        diagnostics=diagnostics, level_views=level_views, level_files=level_files, level_binding=level_binding,
    )


def _write_assessment_files(inputs: ReportInputs, bundle: list[dict[str, Any]]) -> None:
    output, authority, review_data, relevance_data = inputs.output, inputs.authority, inputs.review_data, inputs.relevance_data
    profile, selected = inputs.profile, inputs.selected
    for name, raw in (inputs.level_files or {}).items():
        path = output/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        bundle.append(dict(path=name, sha256=bytes_sha256(raw)))
    if authority:
        write_json(output / "assessment-authority.json", authority)
        bundle.append({"path": "assessment-authority.json", "sha256": bytes_sha256((output / "assessment-authority.json").read_bytes())})
    if review_data:
        bundle.append({"path": "independent-review.json", "sha256": bytes_sha256((output / "independent-review.json").read_bytes())})
    factual_cases = {c["id"]: c for c in profile["cases"] if "reference_answer" in c}
    if factual_cases:
        rules = {c.get('factual_contamination_rule', CONTAMINATION_RULE) for c in factual_cases.values()}
        if len(rules) != 1:
            raise ValueError('Factual comparisons require one contamination rule')
        rule = next(iter(rules))
        sensitivity = {r["id"]: factual_sensitivity(factual_cases[r["case_id"]], r["results"], rule=rule, cutoff=profile["protocol"]["top_k"]) for r in selected if r["case_id"] in factual_cases}
        write_json(output / "factual-sensitivity.json", {"schema": "factual-sensitivity/1", "timing": "post-hoc diagnostic; no recollection or rank compaction", "rule": rule, "results": sensitivity})
        bundle.append({"path": "factual-sensitivity.json", "sha256": bytes_sha256((output / "factual-sensitivity.json").read_bytes())})
    if relevance_data:
        target = output / "relevance-judgments.json"
        target.write_text(json.dumps(relevance_data, ensure_ascii=False, indent=2), encoding="utf-8")
        bundle.append({"path": target.name, "sha256": bytes_sha256(target.read_bytes())})


def _write_license_and_assets(inputs: ReportInputs, bundle: list[dict[str, Any]]) -> None:
    output, authored, content = inputs.output, inputs.authored, inputs.content
    notice = definition_path("NOTICE.txt")
    notice_target = output / "query-catalog-license.txt"
    notice_target.write_bytes(notice.read_bytes())
    bundle.append({"path": notice_target.name, "sha256": bytes_sha256(notice.read_bytes())})
    for asset in authored.get("assets", []):
        verify_bundle(content.parent, [{"path": asset["source"], "sha256": asset["sha256"]}])
        target = output / asset["path"]
        if not target.resolve().is_relative_to(output):
            raise ValueError("Unsafe report asset destination")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((content.parent / asset["source"]).read_bytes())
        bundle.append({"path": asset["path"], "sha256": asset["sha256"]})


def _remove_previous_routes(output: Path) -> None:
    if (output / "manifest.json").exists():
        old_manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        for route in [*old_manifest["routes"], *old_manifest.get("language_notices", [])]:
            old = (output / route).resolve()
            if old.is_relative_to(output) and old.is_file():
                old.unlink()


def _write_public_calls(inputs: ReportInputs, bundle: list[dict[str, Any]]) -> None:
    output, rows, public_files = inputs.output, inputs.rows, inputs.public_files
    for r in rows:
        for evidence in [r["evidence"], *[a["evidence"] for a in r.get("attempts", [])]]:
            if evidence in bundle:
                continue
            target = output / evidence["path"]
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(public_files[evidence["path"]])
            bundle.append(evidence)


def _write_public_bundle(inputs: ReportInputs) -> list[dict[str, Any]]:
    bundle: list[dict[str, Any]] = []
    _write_assessment_files(inputs, bundle)
    _write_license_and_assets(inputs, bundle)
    _remove_previous_routes(inputs.output)
    _write_public_calls(inputs, bundle)
    return bundle


def _report_edition(inputs: ReportInputs, lang: str, routes: list[str]) -> ReportEdition:
    authored, reading_sections, review_data, relevance_data = inputs.authored, inputs.reading_sections, inputs.review_data, inputs.relevance_data
    profile, rows, receipt = inputs.profile, inputs.rows, inputs.receipt
    zh = lang == "zh"
    prefix = "" if lang == "zh" else lang + "/"

    t = partial(report_text, zh=zh)

    page_registry = reader_pages(authored.get("presentation"), reviewed=bool(review_data))
    nav = [(p["route"], p[lang]) for p in page_registry if p["nav"]]
    if reading_sections and authored.get("presentation") != "comparison-report":
        nav = (
            [("index.html", t("Overview"))]
            + [("index.html#" + p["key"], p["nav"][lang]) for p in reading_sections]
            + [("interpretation.html", t("Report interpretation"))]
            + [entry for entry in nav[1:] if entry[0] != "interpretation.html"]
        )
    state = (
        t("Measured API evaluation · frozen model relevance labels and deterministic metrics.") if relevance_data and relevance_data["judge"]["kind"] == "model" else t("Measured API evaluation · human relevance labels and deterministic metrics.") if relevance_data else t("Measured API evaluation · automated target checks, no relevance labels.")
        if rows
        else t("Evaluation design preview. No API performance ranking.")
    )
    if inputs.level_views:
        state = t('Measured API evaluation with isolated output levels and frozen judgments.')
    if rows and len(profile["methods"]) == 1 and not inputs.level_views:
        state = t("Single-provider preflight; no cross-provider comparison.")
    if receipt.get("synthetic"):
        state = t("Synthetic fixture — not API measurements.")
    if rows and not receipt["certified"]:
        state += t(" Diagnostic evidence.")
    if authored.get("reading_model") and rows and receipt.get("certified"):
        state = t("Measured Search API responses and documented capabilities · each metric states its evidence scope.")
    return ReportEdition(lang=lang, zh=zh, prefix=prefix, t=t, nav=nav, state=state, routes=routes)


def _resolve_owned_links(document, route, site_prefix, root_paths):
    """Use one path rule for rendered pages and their expected fragments."""
    for node in document.select("[href],[data-evidence],[data-detail],img[src]"):
        if node.name == "base":
            continue
        for attr in ("href", "data-evidence", "data-detail", "src"):
            value = node.get(attr)
            if value and not value.startswith(("https://", "http://", "#")):
                node[attr] = posixpath.normpath(posixpath.join(
                    site_prefix, "" if value in root_paths else posixpath.dirname(route), value))


def _write_page(inputs: ReportInputs, edition: ReportEdition, route: str, title: str, body: str) -> None:
    output, languages, site_prefix, authored = inputs.output, inputs.languages, inputs.site_prefix, inputs.authored
    receipt, review_data, review_percent, relevance_data = inputs.receipt, inputs.review_data, inputs.review_percent, inputs.relevance_data
    lang, prefix, t, nav, state, routes = edition.lang, edition.prefix, edition.t, edition.nav, edition.state, edition.routes
    path = output / prefix / route
    path.parent.mkdir(parents=True, exist_ok=True)
    back = "../" if route.startswith("details/") else ""
    depth = "../" * (len(Path(prefix + route).parts) - 1 + bool(site_prefix))
    navigation = "".join(f'<a href="{back}{p}"' + (' aria-current="page"' if p == route else '') + f'>{label}</a>' for p, label in nav)
    if len(languages) > 1:
        other = "zh" if lang == "en" else "en"
        otherprefix = "" if other == "zh" else other + "/"
        language_depth = "../" * (len(Path(prefix + route).parts) - 1)
        navigation += f'''<a href="{language_depth}{otherprefix}{route}">{(zh_text("Chinese") if other == 'zh' else 'English')}</a>'''
    elif lang == "zh" and authored.get("presentation") != "comparison-report":
        language_depth = "../" * (len(Path(prefix + route).parts) - 1)
        navigation += f'<a href="{language_depth}en/index.html">English</a>'
    navigation += f"<button data-theme>{t('Theme')}</button>"
    if site_prefix:
        navigation += f'''<a href="{depth}index.html">{t("Main comparison")}</a>'''
    notice_html = f'<p class="notice" data-state>{state}</p>'
    if authored.get("presentation") == "comparison-report" and receipt.get("certified") and not receipt.get("synthetic"):
        notice_html = ''
    document = BeautifulSoup(
        f'''<!doctype html><html lang="{lang}"><head><base href="{depth or './'}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · {t("Web Search")}</title><style>{STYLE}</style></head><body><nav>{navigation}</nav><main><p class="muted">{t("Webstone / Web Search")}</p><h1>{esc(title)}</h1>{notice_html}{body}</main><dialog aria-label="{t("Response evidence")}"><button data-close>{t("Close")}</button><pre></pre><a hidden>{t("Full result")}</a></dialog><script>{SCRIPT}</script></body></html>''',
        "html.parser",
    )
    # Artifact Site injects a root base on every nested route. Local pages use
    # the matching base above; every artifact-owned link names the root path.
    asset_paths = {a["path"] for a in authored.get("assets", [])}
    if relevance_data:
        asset_paths.add("relevance-judgments.json")
    if review_data:
        asset_paths.add("independent-review.json")
    _resolve_owned_links(document, prefix + route, site_prefix, asset_paths | set(inputs.public_files))
    if authored.get("presentation") == "comparison-report":
        footer = document.new_tag("footer")
        for target, label in [("research.html", t("Method and FAQ")), ("review.html", t("Review evidence"))]:
            if target == "review.html" and not review_data:
                continue
            link = document.new_tag("a", href=posixpath.join(site_prefix, prefix, target))
            link.string = label
            if target == "review.html":
                link.string = label + f" · {review_percent}% · {review_data['case_reviewed']}/{review_data['case_total']}" + t(" queries · ") + f"{review_data['output_reviewed']}/{review_data['output_total']}" + t(" outputs")
                link['data-review-coverage'] = ''
                link['data-review-percent'] = str(review_percent)
                link['data-reviewed-queries'] = f"{review_data['case_reviewed']}/{review_data['case_total']}"
                link['data-reviewed-outputs'] = f"{review_data['output_reviewed']}/{review_data['output_total']}"
            footer.append(link)
        document.main.append(footer)
    path.write_text(str(document).replace("</head>", REPORT_PAGE_MARKER + "</head>"), encoding="utf-8")
    routes.append(prefix + route)


def _known_page_metric(profile: dict[str, Any]) -> str:
    return "anchor_hit_5" if profile["protocol"]["top_k"] == 5 else "anchor_hit_10"


def _core_metrics_table(inputs: ReportInputs, edition: ReportEdition) -> tuple[list[str], list[str]]:
    """Return the headers and rows of the core comparison over the home page's overview scope."""
    profile, overview_methods, summary = inputs.profile, inputs.overview_methods, inputs.overview_summary
    t = edition.t
    hit_key = _known_page_metric(profile)
    cutoff = profile["protocol"]["top_k"]
    headers = [
        t("Method"),
        t("Nonempty / queries"),
        t("Mean ms"),
        "P50 ms",
        "P95 ms",
        t("Known page@{cutoff}", context="target rank", cutoff=cutoff),
        t("Price / 1k"),
    ]
    entries = []
    for m in overview_methods:
        s = summary.get(m["id"])
        a = s["all_latency"] if s else {}
        hit = s["metrics"][hit_key] if s else {"mean": None, "n": 0}
        entries.append(
            [
                m["label"],
                s["nonempty"] if s else None,
                a.get("mean"),
                a.get("p50"),
                a.get("p95"),
                hit["mean"],
                m["price_per_1000"],
            ]
        )
    directions = {1: "max", 2: "min", 3: "min", 4: "min", 5: "max", 6: "min" if len({m["currency"] for m in overview_methods}) == 1 else "neutral"}
    winners = numeric_winners(entries, directions)
    core = []
    for entry, m in zip(entries, overview_methods, strict=True):
        td = []
        for i, value in enumerate(entry):
            label = esc(value) if i == 0 else fmt(value)
            if i == 1 and value is not None:
                label = f"{value}/{summary[m['id']]['n']}"
            if i == 6:
                label += " " + m["currency"]
            if i == 5 and value is not None:
                label = t("{value:.1f}% ({n} queries)", value=value * 100, n=summary[m['id']]['metrics'][hit_key]['n'])
            win = i in winners and value == winners[i]
            td.append('<td>' + label + '</td>' if i == 0 else numeric_cell(label, value, ("method", "nonempty", "mean-ms", "p50-ms", "p95-ms", "target-hit", "price-per-1000")[i], directions.get(i, "neutral"), win))
        core.append("<tr>" + "".join(td) + "</tr>")
    return headers, core


def _default_home_body(inputs: ReportInputs, edition: ReportEdition, headers: list[str], core: list[str]) -> str:
    profile, rows, selected, receipt, authored = inputs.profile, inputs.rows, inputs.selected, inputs.receipt, inputs.authored
    physical = inputs.all_call_summary
    lang, zh, t = edition.lang, edition.zh, edition.t
    cutoff = profile["protocol"]["top_k"]
    body = table(headers, core, True)

    body += cohort_summary(profile, rows, zh)
    body += authored_sections(authored, "summary", lang)
    if receipt.get("reference_capture_issues"):
        body += (
            '<p class="notice">'
            + t("{reference_capture_issues} reference captures have HTTP or transfer failures. This cohort remains diagnostic and cannot certify source-text quality.", reference_capture_issues=len(receipt['reference_capture_issues']))
            + "</p>"
        )

    body += (
        "<p>"
        + t("All-call latency includes failures and every declared round. P50 is the median; P95 uses nearest rank. Quality uses only the preselected first round. A known-page miss is not proof of irrelevant results.")
        + "</p>"
    )
    body += (
        "<p>"
        + t("Charges use the prices and billing rules frozen in each method configuration. Unset prices remain unavailable. API charges exclude downstream models and additional fetches.")
        + "</p>"
    )
    body += comparison_tables(profile, selected, rows, zh)
    body += (
        "<p>"
        + t("Collected methods: {methods}. Declared rounds: {rounds}.", methods=len(profile['methods']), rounds=profile['protocol']['rounds'])
        + "</p>"
    )
    body += (
        "<h2>"
        + t("What is compared")
        + '</h2><div class="flow">'
        + "".join(
            "<span>" + v + "</span>"
            for v in [
                t("{cases} frozen queries", cases=len(profile['cases'])),
                t("Same top-{cutoff} budget", cutoff=cutoff),
                (t("Serial API calls") if profile["protocol"]["concurrency"] == 1
                 else t("Up to {concurrency} concurrent API calls",
                        concurrency=profile["protocol"]["concurrency"])),
                t("Saved responses & metrics"),
            ]
        )
        + "</div>"
    )
    body += (
        "<p>"
        + t("Web, site-restricted and vertical queries remain separate populations. Saved requests show each method’s native controls; the comparison uses delivered search text without an answering model.")
        + "</p>"
    )
    if rows:
        body += latency_rounds(profile, rows, zh)
        body += "<h2>" + t("Successful-call latency") + "</h2>"
        body += table(
            [t("Method"), "N", t("Mean ms", context="duration"), "P50 ms", "P95 ms"],
            [
                cells(
                    [
                        esc(m["label"]),
                        str(physical[m["id"]]["success_latency"]["n"]),
                        *[
                            fmt(physical[m["id"]]["success_latency"][k])
                            for k in ("mean", "p50", "p95")
                        ],
                    ]
                )
                for m in profile["methods"]
            ],
        )
    return body


def _reading_model_home_body(inputs: ReportInputs, edition: ReportEdition, headers: list[str], core: list[str]) -> str:
    profile, selected, authored, reading_sections = inputs.profile, inputs.selected, inputs.authored, inputs.reading_sections
    overview_rows, overview_methods = inputs.overview_rows, inputs.overview_methods
    relevance_scores, relevance_data = inputs.relevance_scores, inputs.relevance_data
    lang, zh, t = edition.lang, edition.zh, edition.t
    cutoff = profile["protocol"]["top_k"]
    body = authored_sections(authored, "lead", lang, rows=selected)
    body += (
        "<h2>" + t("Measured baseline") + "</h2>"
        + "<p>" + esc(cohort_label(profile, authored.get("overview_cohort"), zh)) + "</p>"
    )
    overview_case_ids = {r["case_id"] for r in overview_rows}
    target_count = sum(bool(c.get("anchors") or c.get("paper_ids") or c.get("repositories"))
                       for c in profile["cases"] if c["id"] in overview_case_ids)
    body += '<p class="muted">' + t("Nonempty counts queries with at least one result. Mean, P50 (median) and P95 cover all {overview_cases} queries in this table; 95% finish within the P95 time. Known page@{cutoff} checks whether a target URL appears in the first {cutoff} results, across {target_count} predeclared target-page queries.", overview_cases=len(overview_case_ids), cutoff=cutoff, target_count=target_count) + '</p>'
    body += table(headers, core, True)
    if profile["protocol"].get("retry"):
        body += '<p class="lede">' + t("The same {cases} queries went to every provider. Each query has one final result; transport, HTTP or invalid-response failures receive one retry. Latency covers the complete user-visible request, including any retry. Empty valid results are not retried.", cases=len(profile['cases'])) + '</p>'
    body += authored_sections(authored, "summary", lang, rows=selected)
    for chapter in reading_sections:
        body += (
            '<section id="'
            + esc(chapter["key"])
            + '" data-reading="'
            + esc(chapter["key"])
            + '"><h2>'
            + esc(chapter["title"][lang])
            + "</h2>"
            + authored_sections(authored, chapter["key"] + "-lead", lang, rows=selected, heading_level=3)
            + (capability_map(dict(profile, methods=overview_methods), selected, zh) if chapter["key"] == "capabilities" else "")
            + (relevance_panel(profile, relevance_scores, relevance_data, zh) if chapter["key"] == "quality" and relevance_scores else "")
            + (known_page_chart(dict(profile, methods=overview_methods), selected, zh) if chapter["key"] == "quality" else "")
            + (factual_panel(profile, selected, zh) if chapter["key"] == "quality" else "")
            + (mode_panel(profile, selected, zh) if chapter["key"] == "speed" else "")
            + authored_sections(
                authored, chapter["key"], lang, rows=selected, heading_level=3
            )
            + "</section>"
        )
    return body


def _comparison_home_body(inputs: ReportInputs, edition: ReportEdition) -> str:
    profile, selected, authored, relevance_scores = inputs.profile, inputs.selected, inputs.authored, inputs.relevance_scores
    lang, zh = edition.lang, edition.zh
    body = authored_sections(authored, "lead", lang, rows=selected)
    if inputs.level_views:
        from harness.web_search.comparison import render_level_summary, render_level_tabs

        body += render_level_tabs(inputs.level_views, zh)
        body += ''.join(render_level_summary(view, zh, content=authored) for view in inputs.level_views.values())
        return body + authored_sections(authored, 'summary', lang, rows=selected)
    body += executive_table(profile, selected, authored, relevance_scores, zh)
    body += discovery_comparison(profile, selected, authored, zh)
    body += authored_sections(authored, "summary", lang, rows=selected)
    return body


def _write_comparison_chapters(inputs: ReportInputs, edition: ReportEdition) -> None:
    profile, selected, authored = inputs.profile, inputs.selected, inputs.authored
    relevance_scores, relevance_data = inputs.relevance_scores, inputs.relevance_data
    lang, zh, t = edition.lang, edition.zh, edition.t
    page = partial(_write_page, inputs, edition)
    for chapter_key, chapter_title in [("capabilities", t("API outputs")), ("quality", t("Retrieval quality"))]:
        chapter_body = authored_sections(authored, chapter_key + "-lead", lang, rows=selected)
        if inputs.level_views:
            from harness.web_search.comparison import (
                render_level_capabilities,
                render_level_findings,
                render_level_tabs,
            )

            if chapter_key == 'quality':
                chapter_body += render_level_tabs(inputs.level_views, zh)
            chapter_body += (render_level_capabilities(inputs.level_views, zh) if chapter_key == 'capabilities'
                             else ''.join(render_level_findings(view, zh) for view in inputs.level_views.values()))
            chapter_body += authored_sections(authored, chapter_key, lang, rows=selected)
        elif chapter_key == "capabilities":
            chapter_body += capability_map(profile, selected, zh)
            chapter_body += authored_sections(authored, chapter_key, lang, rows=selected)
            if authored.get("interface_example_case_id"):
                chapter_body += interface_example(profile, selected, authored["interface_example_case_id"], zh)
        else:
            chapter_body += authored_sections(authored, "quality-definitions", lang, rows=selected)
            chapter_body += relevance_panel(profile, relevance_scores, relevance_data, zh) if relevance_scores else ""
            chapter_body += factual_panel(profile, selected, zh, compact=True)
            chapter_body += authored_sections(authored, chapter_key, lang, rows=selected)
        page(chapter_key + ".html", chapter_title, chapter_body)


def _write_home_page(inputs: ReportInputs, edition: ReportEdition) -> None:
    profile, selected, authored, reading_sections = inputs.profile, inputs.selected, inputs.authored, inputs.reading_sections
    review_data, review_percent = inputs.review_data, inputs.review_percent
    lang, zh, t = edition.lang, edition.zh, edition.t
    page = partial(_write_page, inputs, edition)
    headers, core = ([], []) if inputs.level_views else _core_metrics_table(inputs, edition)
    body = '' if inputs.level_views else _default_home_body(inputs, edition, headers, core)
    if authored.get("reading_model") and not inputs.level_views:
        body = _reading_model_home_body(inputs, edition, headers, core)
    if not any(c["key"] == "speed" for c in reading_sections) and authored.get("presentation") != "comparison-report":
        body += mode_panel(profile, selected, zh)
    if authored.get("presentation") == "comparison-report":
        body = _comparison_home_body(inputs, edition)
        _write_comparison_chapters(inputs, edition)
    if review_data and authored.get("presentation") != "comparison-report":
        body += f'<p data-review-coverage data-review-percent="{review_percent}" data-reviewed-queries="{review_data["case_reviewed"]}/{review_data["case_total"]}" data-reviewed-outputs="{review_data["output_reviewed"]}/{review_data["output_total"]}"><a href="review.html">{review_percent}% · ' + t("Reviewed {case_reviewed}/{case_total} queries and {output_reviewed}/{output_total} outputs", case_reviewed=review_data['case_reviewed'], case_total=review_data['case_total'], output_reviewed=review_data['output_reviewed'], output_total=review_data['output_total']) + '</a></p>'
    if profile.get("method_collections"):
        body = method_collection_notice(zh) + body
    page(
        "index.html",
        authored.get("title", {}).get(lang)
        or (
            t("Web Search: {cases}-query comparison", cases=len(profile['cases']))
            if profile.get("report_cohorts")
            else t("Search quality, speed and cost")
        ),
        body,
    )


def _dataset_row(profile: dict[str, Any], c: dict[str, Any], edition: ReportEdition) -> str:
    zh, t = edition.zh, edition.t
    anchors = "<br>".join(
        f'''<a href="{esc(u)}" title="{esc(u)}">{t("Target page")} · {esc(u.split('://', 1)[-1].split('/', 1)[0])}</a>'''
        for u in c.get("anchors", [])
    ) or t("No target URL declared")
    if "reference_answer" in c:
        anchors = esc(c["reference_answer"]) + "<br>" + "<br>".join('<a href="' + esc(u) + '">' + t("Labeled source") + "</a>" for u in c.get("reference_urls", []))
    if c.get("paper_ids") or c.get("repositories"):
        anchors += "<br>" + esc(
            ", ".join(c.get("paper_ids", []) + c.get("repositories", []))
        )
    if c.get("identity_aliases"):
        anchors += "<br>" + t("Verified equivalent URLs: ")
        anchors += "<br>".join(esc(u) for u in c["identity_aliases"])
    return (
        cells(
            [
                esc(c["id"]),
                esc(
                    cohort_label(profile, c.get("cohort"), zh)
                    if profile.get("report_cohorts")
                    else search_category(c["family"], zh)
                ),
                esc({"zh": t("Chinese"), "en": t("English")}.get(c["language"], c["language"])),
                esc(search_category(c.get("category", c["family"]), zh)),
                esc(provenance_label(c.get("provenance_kind", "curated"), zh)),
                esc(c["query"]),
                anchors,
                esc(source_note(c, zh))
                + "<br>"
                + (
                    f'<a href="{esc(c["origin"])}">' + t("Source") + "</a>"
                    if c["origin"].startswith("https://")
                    else ""
                ),
            ]
        )
    )


def _write_dataset_page(inputs: ReportInputs, edition: ReportEdition) -> None:
    profile, authored = inputs.profile, inputs.authored
    lang, zh, prefix, t = edition.lang, edition.zh, edition.prefix, edition.t
    page = partial(_write_page, inputs, edition)
    dataset_profile, dataset_content = quality_dataset(profile, authored)
    ds = [_dataset_row(profile, c, edition) for c in dataset_profile["cases"]]
    page(
        "dataset.html",
        t("Tests and sources"),
        dataset_context(dataset_profile, dataset_content, zh)
        + '<p><a href="https://huggingface.co/datasets/Lexmount/LexBench-Web-Search">'
        + t("LexBench Web Search: dataset and sources")
        + '</a> · <a href="https://github.com/lexmount/webstone/tree/main/src/lexbench_websearch">'
        + t("Scoring code")
        + "</a></p>"
        + provenance_table(dataset_profile, zh)
        + authored_sections(authored, "dataset", lang)
        + f'<p><a href="{"../" if prefix else ""}query-catalog-license.txt">'
        + t("Public query catalog attribution and license")
        + "</a></p>"
        + '<div class="dataset-controls">' + origin_filter(dataset_profile, zh)
        + cohort_filter(dataset_profile, zh)
        + population_filter(dataset_profile, dataset_content, zh)
        + "<label>"
        + t("Search queries, categories or sources")
        + " <input data-query></label></div>"
        + table(
            [
                "ID",
                t("Benchmark"),
                t("Language"),
                t("Category"),
                t("Provenance", context="source type"),
                t("Query"),
                t("Reference page / answer"),
                t("Provenance"),
            ],
            [
                s.replace(
                    "<tr>",
                    f'<tr data-row data-state="all" data-cohort="{esc(c.get("cohort", ""))}" data-origin="{esc(origin_group(c))}" data-case-id="{esc(c["id"])}">',
                    1,
                )
                for s, c in zip(ds, dataset_profile["cases"], strict=True)
            ],
            bounded=True,
        ),
    )


def _reason_label(reason: str, cutoff: int, t: Callable[..., str]) -> str:
    return {
        "relevance_hit": t("Useful result found"),
        "relevance_miss": t("No useful result found"),
        "quality_excluded": t("Reference qualification excluded"),
        "api_error": t("API or transport error"),
        "empty": t("Empty result list"),
        "domain_mismatch": t("Results outside the requested domain"),
        "target_miss": t("Reference target absent from top {cutoff}", cutoff=cutoff),
        "target_hit": t("Reference target found"),
        "source_hit": t("Published supporting source found"),
        "answer_hit": t("Reference answer text found"),
        "answer_miss": t("Reference answer text absent"),
        "domain_hit": t("Requested site found"),
        "domain_miss": t("Requested site absent"),
        "source_miss": t("Published supporting source not found"),
        "probe": t("Unjudged query"),
    }[reason]


def _case_reference_detail(c: dict[str, Any], query_relevance: dict[str, Any] | None, cutoff: int, edition: ReportEdition) -> str:
    zh, t = edition.zh, edition.t
    detail = ""
    if query_relevance:
        detail += relevance_detail(query_relevance, zh)
    if c.get('anchors'):
        detail += '<h2>' + t("Webpage discovery references") + '</h2><ul>' + ''.join('<li><a href="' + esc(url) + '">' + esc(url) + '</a></li>' for url in c['anchors']) + '</ul>'
    elif c.get('domains'):
        detail += '<h2>' + t("Requested sites") + '</h2><p>' + esc(t(", ").join(c['domains'])) + '</p>'
    if c.get("reference_annotation"):
        detail += annotation_detail(c["reference_annotation"], zh)
    elif c.get("anchors"):
        detail += (
            "<p>"
            + t("These URLs define the known-page check; relevance is scored separately.")
            + "</p>"
        )
    if "reference_answer" in c:
        detail += "<h3>" + t("Published answer and sources") + "</h3>"
        detail += "<p>" + esc(c["reference_answer"]) + "</p>"
        detail += "<ul>" + "".join(
            '<li><a href="' + esc(u) + '">' + esc(u) + "</a></li>"
            for u in c.get("reference_urls", [])
        ) + "</ul>"
        detail += "<p>" + t("Checks compare the first {cutoff} URLs with the published sources and look for the normalized answer phrase in individual returned fields. A phrase match is not a factual correctness or entailment judgment.", cutoff=cutoff) + "</p>"
    if c.get("paper_ids") or c.get("repositories"):
        detail += (
            "<p>"
            + esc(", ".join(c.get("paper_ids", []) + c.get("repositories", [])))
            + "</p>"
        )
    if c.get("identity_aliases"):
        detail += "<p>" + t("Verified equivalent URLs: ")
        detail += esc(", ".join(c["identity_aliases"])) + "</p>"
    return detail


def _result_metrics_detail(r: dict[str, Any], c: dict[str, Any], hit_key: str, cutoff: int, t: Callable[..., str]) -> str:
    detail = ""
    metric_labels = [
        (hit_key, t("Known target@{cutoff}", cutoff=cutoff)),
        ("reciprocal_rank", t("Reciprocal rank")),
        ("domain_precision", t("Requested-domain fraction")),
        ("duplicate_rate", t("Duplicate-document fraction")),
        ("content_coverage", t("Results containing content")),
    ]
    detail_metrics = r["metrics"]
    if "reference_answer" in c:
        detail_metrics = {**r["metrics"], **factual_evidence(c, r["results"], cutoff=cutoff)}
        metric_labels += [
            (f"labeled_source_hit_{cutoff}", t("Published source in top {cutoff}", cutoff=cutoff)),
            (f"labeled_source_rr_{cutoff}", t("Published-source reciprocal rank in top {cutoff}", cutoff=cutoff)),
            (f"answer_in_preview_{cutoff}", t("Answer phrase in title/snippet")),
            (f"answer_in_delivered_text_{cutoff}", t("Answer phrase in delivered text")),
        ]
    if "reference_answer" in c:
        sensitivity = factual_sensitivity(c, r["results"], cutoff=cutoff)
        detail += '<p>' + t("Post-hoc benchmark-mirror exclusion; blanked ranks: ") + esc(str(sensitivity["excluded_ranks"])) + '</p>'
        detail += table([t("After excluding benchmark copies and evaluation records"), t("Value")], [cells([t("Answer in preview"), str(sensitivity["metrics"][f"answer_in_preview_{cutoff}"])]), cells([t("Answer in all text"), str(sensitivity["metrics"][f"answer_in_delivered_text_{cutoff}"])])])
    detail += table(
        [t("Metric"), t("Value")],
        [cells([label, fmt(detail_metrics[key])]) for key, label in metric_labels],
    )
    return detail


def _physical_calls_detail(repeats: list[dict[str, Any]], edition: ReportEdition) -> str:
    prefix, t = edition.prefix, edition.t
    if len(repeats) <= 1:
        return ""
    repeat_rows = []
    for call in repeats:
        call_path = ("../../" if prefix else "../") + call["evidence"]["path"]
        repeat_rows.append(
            cells(
                [
                    str(call["round"] + 1),
                    str(call["http_status"]),
                    fmt(call["elapsed_ms"]),
                    str(len(call["results"])),
                    f'''<button data-evidence="{call_path}">{t("Open response")}</button> <a href="{call_path}">JSON</a>''',
                ]
            )
        )
    return (
        "<h2>"
        + t("All declared physical calls")
        + "</h2>"
        + table(
            [
                t("Round"),
                "HTTP",
                "ms",
                t("Results"),
                t("Evidence"),
            ],
            repeat_rows,
        )
    )


def _wrap_result_detail(detail: str, status: str, t: Callable[..., str]) -> str:
    detail_doc = BeautifulSoup(detail, "html.parser")
    detail_doc.select_one("[data-summary]")["data-result-summary"] = ""
    for pre in list(detail_doc.select("pre")):
        if pre.find_parent("details") is None:
            disclosure = detail_doc.new_tag("details")
            label = detail_doc.new_tag("summary")
            label.string = t("Inspect evidence")
            pre.wrap(disclosure)
            disclosure.insert(0, label)
    diagnosis_node = detail_doc.new_tag("section", id="result-logs")
    diagnosis_node["data-failure-diagnosis"] = ""
    error_node = detail_doc.select_one("[data-error]")
    error_node["data-kind"] = "error"
    error_node.parent.wrap(diagnosis_node)
    detail = '<section data-report-detail="' + ("failed" if status == "fail" else "passed") + '">' + str(detail_doc) + '</section>'
    return detail


def _result_detail(
    inputs: ReportInputs,
    edition: ReportEdition,
    r: dict[str, Any],
    c: dict[str, Any],
    query_relevance: dict[str, Any] | None,
    status: str,
    reason_label: str,
) -> str:
    profile, rows, selected, review_data, payloads = inputs.profile, inputs.rows, inputs.selected, inputs.review_data, inputs.payloads
    zh, prefix, t = edition.zh, edition.prefix, edition.t
    cid = c["id"]
    cutoff = profile["protocol"]["top_k"]
    # Relative path from details/ to shared evidence at output root.
    ev = ("../../" if prefix else "../") + r["evidence"]["path"]
    detail = f"<section data-summary><p>{esc(c['query'])}</p><p>HTTP {r['http_status']} · {fmt(r['elapsed_ms'])} ms · {len(r['results'])} {t('results')}</p></section>"
    detail += _case_reference_detail(c, query_relevance, cutoff, edition)
    if query_relevance:
        reason_label = t("Useful results: ") + str(query_relevance["useful"]) + f"/{cutoff}"
    if review_data:
        detail += render_detail(review_data, r["id"], zh)
    diagnosis = reason_label
    detail += "<p>" + diagnosis + "</p>"
    detail += (
        "<h2>"
        + t("Actual request parameters")
        + "</h2><pre data-request>"
        + esc(json.dumps(r.get("request", {}), ensure_ascii=False, indent=2))
        + "</pre>"
    )
    if inputs.level_views:
        from harness.web_search.comparison import render_level_detail

        detail += render_level_detail(inputs.level_views, r, zh)
    else:
        detail += _result_metrics_detail(r, c, _known_page_metric(profile), cutoff, t)
    detail += (
        "<h2>"
        + t("Exact error")
        + "</h2><pre data-error>"
        + esc(
            error_evidence(r, payloads.get(r["id"], {}))
            or t("No API error reported; inspect the returned response for retrieval diagnostics.")
        )
        + "</pre>"
    )
    detail += f'''<button data-evidence="{ev}">{t("Open response")}</button> <a href="{ev}">JSON</a>'''
    repeats = [x for x in rows if x["case_id"] == cid and x["method"] == r["method"]]
    detail += _physical_calls_detail(repeats, edition)
    peers = [x for x in selected if x["case_id"] == cid]
    detail += same_task(peers, profile, zh)
    detail += "<h2>" + t("Returned results") + "</h2>"
    for item_index, item in enumerate(r["results"]):
        if item.get('invalid_slot'):
            detail += '<article class="specimen"><p>' + str(item_index + 1) + '. ' + t('Invalid result slot') + '</p></article>'
            continue
        detail += f'''<article class="specimen"><a href="{esc(item['url'])}">{esc(item['title'])}</a><p>{esc(item['snippet'])}</p><button data-evidence="{ev}" data-result="{item_index}">{t("Summary and content")}</button></article>'''
    return _wrap_result_detail(detail, status, t)


def _write_result_pages(inputs: ReportInputs, edition: ReportEdition) -> list[str]:
    """Write one detail page per scored result and return the matching inventory rows."""
    profile, selected, relevance_scores, payloads = inputs.profile, inputs.selected, inputs.relevance_scores, inputs.payloads
    zh, t = edition.zh, edition.t
    page = partial(_write_page, inputs, edition)
    cutoff = profile["protocol"]["top_k"]
    inventory = []
    bycase = {c["id"]: c for c in profile["cases"]}
    method_labels = {method["id"]: method["label"] for method in profile["methods"]}
    for r in selected:
        cid = r["case_id"]
        c = bycase[cid]
        query_relevance = next((x for x in (relevance_scores or []) if x["case_id"] == cid and x["method"] == r["method"]), None)
        status, reason = classify(c, r, cutoff=cutoff, quality_excluded=cid in profile.get("factual_quality_exclusions", []), relevance_grades=query_relevance["ranked_grades"] if query_relevance else None)
        reason_label = _reason_label(reason, cutoff, t)
        target = "details/" + r["id"] + ".html"
        state_label = {
            "fail": t("Miss / error"),
            "pass": t("Target hit"),
            "probe": t("Unjudged query"),
        }[status]
        diagnostic_text = "\n".join(
            [
                c["query"],
                reason_label,
                f"HTTP {r['http_status']}; {len(r['results'])} " + t("results"),
                error_evidence(r, payloads.get(r["id"], {}))
                or (
                    t("Empty results array")
                    if not r["results"]
                    else t("Compare returned URLs with the independent reference target in the full result.")
                ),
            ]
        )
        inventory.append(
            f'''<tr data-row data-state="{status}" data-method="{esc(r['method'])}" data-cohort="{esc(c.get('cohort', ''))}" data-origin="{esc(origin_group(c))}"><td>{esc(cid)}<br>{esc(cohort_label(profile, c.get('cohort'), zh))}</td><td>{esc(c['query'])}</td><td>{esc(method_labels[r['method']])}</td><td>{esc(state_label)}</td><td>{fmt(r['elapsed_ms'])}</td><td><button data-diagnosis="{esc(diagnostic_text)}" data-detail="{target}">{t("Diagnosis")}</button> <a href="{target}">{t("Details")}</a></td></tr>'''
        )
        detail = _result_detail(inputs, edition, r, c, query_relevance, status, reason_label)
        page(target, cid + " / " + method_labels[r["method"]], detail)
    return inventory


def _write_results_page(inputs: ReportInputs, edition: ReportEdition, inventory: list[str]) -> None:
    profile, rows, selected, authored, relevance_data, site_prefix = inputs.profile, inputs.rows, inputs.selected, inputs.authored, inputs.relevance_data, inputs.site_prefix
    zh, prefix, t = edition.zh, edition.prefix, edition.t
    page = partial(_write_page, inputs, edition)
    if inputs.level_views:
        from harness.web_search.comparison import render_level_results

        body = render_level_results(inputs.level_views, zh, detail_link=lambda row: posixpath.join(site_prefix, prefix, 'details', row['id'] + '.html'))
        page('results.html', t('Query comparison'), body)
        return
    options = [
        ("fail", t("Target miss / error")),
        ("pass", t("Declared evidence found")),
        ("probe", t("Unjudged queries")),
        ("all", t("All")),
    ]
    default = "fail" if any('data-state="fail"' in r for r in inventory) else "all"
    controls = (
        "<label>"
        + t("Filter")
        + " <select data-filter>"
        + "".join(
            f'<option value="{v}"'
            + (" selected" if v == default else "")
            + ">"
            + label
            + "</option>"
            for v, label in options
        )
        + "</select></label> <label>"
        + t("Provider")
        + ' <select data-method-filter><option value="all">'
        + t("All")
        + "</option>"
        + "".join(
            f'<option value="{esc(method["id"])}">{esc(method["label"])}</option>'
            for method in profile["methods"]
        )
        + "</select></label> <label>"
        + t("Find")
        + " <input data-query></label>"
    )
    page(
        "results.html",
        t("Query comparison"),
        (render_result_comparison(profile, selected, zh, prefix=prefix, site_prefix=site_prefix, content=authored, relevance=relevance_data) if authored.get("presentation") == "comparison-report" else "")
        + ("" if authored.get("presentation") == "comparison-report" else (
        origin_filter(profile, zh)
        + cohort_filter(profile, zh)
        + controls
        + table(
            [
                "ID",
                t("Query"),
                t("Method"),
                t("Outcome", context="status"),
                "ms",
                t("Evidence"),
            ],
            inventory,
        ).replace('class="scroll"', 'class="scroll inventory"')
        + (
            ""
            if rows
            else "<p>"
            + t("No authenticated cohort has been collected. Missing credentials are not provider failures.")
            + "</p>"
        )
        + (
            comparison_tables(profile, selected, rows, zh)
            if authored.get("reading_model")
            else ""
        )
        ))
    )


def _write_method_pages(inputs: ReportInputs, edition: ReportEdition) -> None:
    profile, rows, selected, authored, relevance_scores = inputs.profile, inputs.rows, inputs.selected, inputs.authored, inputs.relevance_scores
    lang, zh, t = edition.lang, edition.zh, edition.t
    page = partial(_write_page, inputs, edition)
    research = (
        authored_sections(authored, "method", lang, rows=selected)
        + (cohort_summary(profile, rows, zh) if authored.get("reading_model") and authored.get("presentation") != "comparison-report" else "")
        + fairness_table(profile, rows, zh)
        + execution_settings(profile, zh)
    )
    page(
        "research.html", t("Evaluation method"), research
    )
    if authored.get("presentation") != "comparison-report":
        page("interpretation.html", t("Findings"),
             authored_sections(authored, "interpretation-lead", lang, rows=selected)
             + interpretation_summary_table(profile, selected, zh, authored, relevance_scores)
             + factual_panel(profile, selected, zh)
             + authored_sections(authored, "interpretation", lang, rows=selected)
             + outcome_table(profile, selected, zh, relevance_scores) + domain_discovery_table(profile, selected, zh))


def _write_performance_page(inputs: ReportInputs, edition: ReportEdition) -> None:
    profile, rows, selected, authored, payloads, diagnostics = inputs.profile, inputs.rows, inputs.selected, inputs.authored, inputs.payloads, inputs.diagnostics
    lang, zh, prefix, t = edition.lang, edition.zh, edition.prefix, edition.t
    page = partial(_write_page, inputs, edition)
    if authored.get("presentation") == "comparison-report":
        primary_ids = {c["id"] for c in profile["cases"] if "reference_answer" not in c}
        primary_rows = [r for r in rows if r["case_id"] in primary_ids]
        primary_profile = dict(profile, cases=[c for c in profile["cases"] if c["id"] in primary_ids])
        primary_diagnostics = operational(primary_rows, payloads, profile["methods"])
        timing = benchmark_operations(profile, selected, authored, zh)
        timing += authored_sections(authored, "performance", lang, rows=selected)
        timing += '<details class="technical-details"><summary>' + t("Server timing and upstream evidence") + '</summary>' + performance_view(primary_profile, primary_diagnostics, zh, evidence_prefix="../" if prefix else "") + '</details>'
        timing += '<details><summary>' + t("Pricing terms") + '</summary>' + cost_panel(profile, rows, zh, authored) + '</details>'
        timing += authored_sections(authored, "cost", lang, rows=selected)
        page("performance.html", t("Speed and cost"), timing)
    else:
        page("performance.html", t("Latency diagnostics"), authored_sections(authored, "performance", lang, rows=selected) + performance_view(profile, diagnostics, zh, evidence_prefix="../" if prefix else ""))


def _write_review_page(inputs: ReportInputs, edition: ReportEdition) -> None:
    selected, authored, review_data = inputs.selected, inputs.authored, inputs.review_data
    lang, zh, t = edition.lang, edition.zh, edition.t
    page = partial(_write_page, inputs, edition)
    page("review.html", t("Independent result review"), render_summary(review_data, zh) + authored_sections(authored, "review", lang, rows=selected))


def _write_edition(inputs: ReportInputs, lang: str, routes: list[str]) -> None:
    edition = _report_edition(inputs, lang, routes)
    _write_home_page(inputs, edition)
    _write_dataset_page(inputs, edition)
    inventory = _write_result_pages(inputs, edition)
    _write_results_page(inputs, edition, inventory)
    _write_method_pages(inputs, edition)
    _write_performance_page(inputs, edition)
    if inputs.review_data:
        _write_review_page(inputs, edition)


def _write_manifest(
    inputs: ReportInputs,
    language: str,
    routes: list[str],
    bundle: list[dict[str, Any]],
    source: Path | None,
    cohorts: Sequence[tuple[str, Path]],
) -> None:
    output, languages, site_prefix, content_bytes = inputs.output, inputs.languages, inputs.site_prefix, inputs.content_bytes
    profile, collection_profile, rows, receipt = inputs.profile, inputs.collection_profile, inputs.rows, inputs.receipt
    scoring_revision, assessment_revision = inputs.scoring_revision, inputs.assessment_revision
    redactions, billing_corrections, diagnostics = inputs.redactions, inputs.billing_corrections, inputs.diagnostics
    review_data, relevance_data = inputs.review_data, inputs.relevance_data
    authored, reading_sections = inputs.authored, inputs.reading_sections
    build_version = bind_build_version(output)
    bundle.append({"path": FILENAME, "sha256": build_version["sha256"]})
    write_json(
        output / "manifest.json",
        dict(
            schema="web-search-report/1",
            **({'level_assessments': inputs.level_binding} if inputs.level_binding else {}),
            template="web-search/1",
            webstone=build_version,
            languages=languages,
            language_notices=_language_notice(output, languages),
            primary_language=language,
            site_prefix=site_prefix,
            routes=routes,
            profile=profile,
            collection_profile=collection_profile,
            scoring_revision=scoring_revision,
            assessment_revision=assessment_revision,
            evidence_redactions=redactions,
            billing_corrections=billing_corrections,
            independent_review_sha256=bytes_sha256((output / "independent-review.json").read_bytes()) if review_data else None,
            report_assessment={"relevance": {key: relevance_data["judge"][key] for key in ("kind", "name")} if relevance_data else None,
                               "independent_review_scope": review_data["scope"] if review_data else None,
                               "collection_protocol_sha256": bytes_sha256(json.dumps(collection_profile["protocol"], sort_keys=True).encode())},
            relevance_sha256=bytes_sha256((output / "relevance-judgments.json").read_bytes()) if relevance_data else None,
            result_storage="raw-response",
            rows=[
                {
                    **{k: v for k, v in r.items() if k != "results"},
                    "result_count": len(r["results"]),
                    "normalized_results_sha256": bytes_sha256(
                        json.dumps(r["results"], ensure_ascii=False, sort_keys=True).encode()
                    ),
                }
                for r in rows
            ],
            receipt=receipt,
            collection_receipt_sha256=bytes_sha256((source / "receipt.json").read_bytes()) if source and not cohorts else None,
            bundle=bundle,
            content_sha256=bytes_sha256(content_bytes) if content_bytes is not None else None,
            diagnostics=diagnostics,
            authored=authored if content_bytes is not None else None,
            reading_sections=reading_sections,
            renderer_sha256=bytes_sha256(b"\0".join(
                path.read_bytes() for path in (*sorted(Path(__file__).parent.glob("*.py")), Path(__file__).with_name("messages.json"), *sorted(ASSETS.glob("*.*")))
            )),
        ),
    )


def build(
    output: str | Path,
    *,
    profile_path: str | Path | None = None,
    source: Path | None = None,
    content: Path | None = None,
    language: str = "zh",
    include_english: bool = False,
    correction: Path | None = None,
    relevance: Path | None = None,
    review: Path | None = None,
    site_prefix: str = "",
    cohorts: Sequence[tuple[str, Path]] = (),
) -> Path:
    _validate_build_options(language, site_prefix)
    inputs = _load_report_inputs(
        output,
        profile_path=profile_path,
        source=source,
        content=content,
        language=language,
        include_english=include_english,
        correction=correction,
        relevance=relevance,
        review=review,
        site_prefix=site_prefix,
        cohorts=cohorts,
    )
    bundle = _write_public_bundle(inputs)
    routes: list[str] = []
    for lang in inputs.languages:
        _write_edition(inputs, lang, routes)
    _write_manifest(inputs, language, routes, bundle, source, cohorts)
    entry = select_report_language(inputs.output, language)
    check(inputs.output, evidence_only=True)
    return entry


def _check_english_separators(output, manifest):
    """Report-authored separators stay English; measured queries and responses keep their own text."""
    for route in manifest["routes"]:
        if not route.startswith("en/") or not route.endswith(".html"):
            continue
        html = (output / route).read_text(encoding="utf-8")
        if not CHINESE_SEPARATORS.search(html):
            continue
        soup = BeautifulSoup(html, "html.parser")
        authored = soup.select("[data-independent-review] details > ul > li, [data-populations] > .population-note")
        authored += [
            heading.find_next_sibling("p")
            for heading in soup.find_all("h2")
            if heading.get_text() == "Requested sites"
        ]
        if any(CHINESE_SEPARATORS.search(element.get_text()) for element in authored if element):
            raise ValueError(f"English page renders Chinese punctuation in report-authored text: {route}")


def check(output, *, return_data=False, evidence_only=False):
    authority = report_authority(output)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    validate_content_revisions(manifest)
    if not evidence_only:
        check_build_version(output, manifest)
    if manifest.get("assessment_revision"):
        validate_revision_binding(manifest["assessment_revision"]["contract"], normalizer=normalize_response, authority=authority)
    _check_report_languages(output, manifest)
    if not evidence_only:
        _check_english_separators(output, manifest)
    top_routes = reader_pages((manifest.get("authored") or {}).get("presentation"), reviewed=bool(manifest.get("independent_review_sha256")))
    for lang in manifest["languages"]:
        prefix = "" if lang == "zh" else lang + "/"
        if not {prefix + p["route"] for p in top_routes}.issubset(manifest["routes"]):
            raise ValueError("Report is missing a registered reader page")
    editions = [
        {r.removeprefix(lang + "/") for r in manifest["routes"] if r.startswith(lang + "/")}
        if lang != "zh"
        else {r for r in manifest["routes"] if not r.startswith(("en/", "zh/"))}
        for lang in manifest["languages"]
    ]
    if not editions or any(e != editions[0] for e in editions):
        raise ValueError("Language route inventories disagree")
    verify_bundle(output, manifest["bundle"])
    for item in manifest["bundle"]:
        if item["path"].endswith((".json", ".txt", ".md")):
            raw = (output / item["path"]).read_bytes()
            if _redact_url_userinfo(raw) != raw:
                raise ValueError("Public evidence contains URL credentials")
    for item in manifest.get("evidence_redactions", []):
        if bytes_sha256((output / item["path"]).read_bytes()) != item["public_sha256"]:
            raise ValueError("Public redaction binding changed")
    relevance_data = None
    if manifest.get("relevance_sha256"):
        relevance_path = output / "relevance-judgments.json"
        if bytes_sha256(relevance_path.read_bytes()) != manifest["relevance_sha256"]:
            raise ValueError("Relevance judgments hash mismatch")
        relevance_data = json.loads(relevance_path.read_text(encoding="utf-8"))
    review_data = json.loads((output / "independent-review.json").read_text(encoding="utf-8")) if manifest.get("independent_review_sha256") else None
    if manifest.get("report_assessment") is not None:
        expected_assessment = {
            "relevance": {key: relevance_data["judge"][key] for key in ("kind", "name")} if relevance_data else None,
            "independent_review_scope": review_data["scope"] if review_data else None,
            "collection_protocol_sha256": bytes_sha256(json.dumps(manifest["collection_profile"]["protocol"], sort_keys=True).encode()),
        }
        if (manifest["report_assessment"] != expected_assessment
                or assessment_profile(manifest["profile"], relevance_data, review_data) != manifest["profile"]):
            raise ValueError("Current assessment metadata contradicts its evidence")
    payloads = {}
    for row in manifest["rows"]:
        try:
            payloads[row["id"]] = json.loads((output / row["evidence"]["path"]).read_bytes())
        except ValueError:
            payloads[row["id"]] = {}
        if manifest.get("result_storage") == "raw-response":
            results = (
                normalize_response(payloads[row["id"]], next(m for m in manifest["profile"]["methods"] if m["id"] == row["method"]), (manifest["profile"]["protocol"] if manifest.get("assessment_revision") else collection_protocol(manifest["profile"], row)), row.get("request"))
                if row["http_status"] == 200 and not row["error"]
                else []
            )
            if (
                len(results) != row["result_count"]
                or bytes_sha256(json.dumps(results, ensure_ascii=False, sort_keys=True).encode())
                != row["normalized_results_sha256"]
            ):
                raise ValueError("Result receipt disagrees with raw response")
            row["results"] = results
    level_views = None
    if manifest.get('level_assessments'):
        from harness.web_search.output_levels import load_assessed_levels

        verify_bundle(output, [manifest['level_assessments']])
        level_views, _ = load_assessed_levels(output/manifest['level_assessments']['path'],
                                             manifest['profile'], scoring_rows(manifest['profile'], manifest['rows']),
                                             authority=authority)
    if manifest.get("independent_review_sha256"):
        review_path = output / "independent-review.json"
        if bytes_sha256(review_path.read_bytes()) != manifest["independent_review_sha256"]:
            raise ValueError("Independent review evidence hash mismatch")
        review_data = json.loads(review_path.read_bytes())
        validate_public_review(manifest["profile"], scoring_rows(manifest["profile"], manifest["rows"]), review_data, authority=authority, relevance=json.loads((output / "relevance-judgments.json").read_bytes()) if manifest.get("relevance_sha256") else None)
        for lang in (() if evidence_only else manifest["languages"]):
            prefix = "" if lang == "zh" else lang + "/"
            if prefix + "review.html" not in manifest["routes"]:
                raise ValueError("Independent review page is missing")
            soup = BeautifulSoup((output / (prefix + "index.html")).read_text(encoding="utf-8"), "html.parser")
            coverage = soup.select_one("[data-review-coverage]")
            query_scope = f"{review_data['case_reviewed']}/{review_data['case_total']}"
            output_scope = f"{review_data['output_reviewed']}/{review_data['output_total']}"
            percent = coverage_percent(review_data)
            if coverage is None or any(value not in re.findall(r"(?<!\d)\d+/\d+(?!\d)", coverage.get_text()) for value in (query_scope, output_scope)) or not re.search(rf"(?<![\d.]){percent}%(?![\d])", coverage.get_text()):
                raise ValueError("Independent review coverage differs from the accepted evidence")
            if coverage.get("data-review-percent") != str(percent):
                raise ValueError("Independent review coverage differs from the accepted evidence")
            for attr, value in (("data-reviewed-queries", query_scope), ("data-reviewed-outputs", output_scope)):
                if coverage.get(attr) != value:
                    raise ValueError("Independent review coverage differs from the accepted evidence")
            review_page = BeautifulSoup((output / (prefix + "review.html")).read_text(encoding="utf-8"), "html.parser")
            expected = BeautifulSoup(render_summary(review_data, lang == "zh"), "html.parser")
            actual = review_page.select_one("[data-independent-review]")
            if actual is None or actual.get_text() != expected.get_text():
                raise ValueError("Independent review summary differs from the accepted evidence")
            for row in scoring_rows(manifest["profile"], manifest["rows"]):
                detail = BeautifulSoup((output / (prefix + "details/" + row["id"] + ".html")).read_text(encoding="utf-8"), "html.parser")
                expected = BeautifulSoup(render_detail(review_data, row["id"], lang == "zh"), "html.parser")
                actual = detail.select("[data-independent-opinion], [data-review-not-selected]")
                if len(actual) != 1 or actual[0].get_text() != expected.get_text():
                    raise ValueError("Independent review detail differs from accepted evidence")
    factual_cases = {
        case["id"]: case for case in manifest["profile"]["cases"]
        if case.get("reference_answer")
    }
    if factual_cases:
        sensitivity_path = output / "factual-sensitivity.json"
        sensitivity = json.loads(sensitivity_path.read_text(encoding="utf-8"))
        expected_sensitivity = {
            row["id"]: factual_sensitivity(factual_cases[row["case_id"]], row["results"], rule=sensitivity.get("rule"), cutoff=manifest["profile"]["protocol"]["top_k"])
            for row in scoring_rows(manifest["profile"], manifest["rows"]) if row["case_id"] in factual_cases
        }
        if sensitivity.get("results") != expected_sensitivity:
            raise ValueError("Factual sensitivity disagrees with preserved responses")
    groups = manifest["profile"].get("report_cohorts", [])
    if groups:
        declared = [(g["id"], cid) for g in groups for cid in g["case_ids"]]
        actual_cases = [(c.get("cohort"), c["id"]) for c in manifest["profile"]["cases"]]
        if len(declared) != len(set(cid for _, cid in declared)) or set(declared) != set(
            actual_cases
        ):
            raise ValueError("Cohort population does not match the combined query set")
        by_id = dict((cid, group) for group, cid in declared)
        if any(r.get("cohort") != by_id.get(r["case_id"]) for r in manifest["rows"]):
            raise ValueError("Result belongs to the wrong cohort")
    for lang in (() if evidence_only else manifest["languages"]):
        prefix = "" if lang == "zh" else lang + "/"
        if manifest["profile"].get("method_collections"):
            home = BeautifulSoup((output / (prefix + "index.html")).read_text(encoding="utf-8"), "html.parser")
            notice = home.select_one("[data-method-collection-notice]")
            expected_notice = BeautifulSoup(method_collection_notice(lang == "zh"), "html.parser").get_text()
            if notice is None or notice.get_text() != expected_notice:
                raise ValueError("Method collection window disclosure is missing or changed")
        soup = BeautifulSoup((output / (prefix + "research.html")).read_text(encoding="utf-8"), "html.parser")
        expected = BeautifulSoup(
            fairness_table(manifest["profile"], manifest["rows"], lang == "zh"), "html.parser"
        )
        actual = soup.select_one("[data-fairness]")
        if actual is None or actual.get_text() != expected.select_one("[data-fairness]").get_text():
            raise ValueError("Fairness table disagrees with physical calls")
        expected_execution = BeautifulSoup(execution_settings(manifest["profile"], lang == "zh"),
                                           "html.parser")
        actual_execution = soup.select("[data-execution-settings]")
        if len(actual_execution) != 1 or actual_execution[0].get_text() != expected_execution.get_text():
            raise ValueError("Execution settings disagree with the frozen collection policy")
        if groups:
            if (manifest.get("authored") or {}).get("presentation") != "comparison-report":
                summary_route = (
                    "research.html"
                    if (manifest.get("authored") or {}).get("reading_model")
                    else "index.html"
                )
                home = BeautifulSoup((output / (prefix + summary_route)).read_text(encoding="utf-8"), "html.parser")
                section = home.select_one("[data-cohort-summary]")
                expected_section = BeautifulSoup(
                    cohort_summary(manifest["profile"], manifest["rows"], lang == "zh"), "html.parser"
                )
                if section is None or section.get_text() != expected_section.get_text():
                    raise ValueError("Missing or incorrect cohort summary")
            for route in (("dataset.html",) if (manifest.get("authored") or {}).get("presentation") == "comparison-report" else ("dataset.html", "results.html")):
                inventory = BeautifulSoup((output / (prefix + route)).read_text(encoding="utf-8"), "html.parser")
                options = inventory.select("[data-cohort-filter] option")
                if {o["value"] for o in options} != {"all", *(g["id"] for g in groups)}:
                    raise ValueError("Incomplete cohort filter")
        for route in (("dataset.html",) if (manifest.get("authored") or {}).get("presentation") == "comparison-report" else ("dataset.html", "results.html")):
            inventory = BeautifulSoup((output / (prefix + route)).read_text(encoding="utf-8"), "html.parser")
            inventory_profile = quality_dataset(manifest["profile"], manifest.get("authored") or {})[0] if route == "dataset.html" else manifest["profile"]
            if route == "dataset.html" and Counter(r.get("data-case-id") for r in inventory.select("[data-row]")) != Counter(c["id"] for c in inventory_profile["cases"]):
                raise ValueError("Active dataset differs from reviewed quality population")
            expected_groups = {origin_group(c) for c in inventory_profile["cases"]}
            if {o["value"] for o in inventory.select("[data-origin-filter] option")} != {
                "all",
                *expected_groups,
            }:
                raise ValueError("Incomplete query-source filter")
            expected_origins = [
                origin_group(c)
                for c in inventory_profile["cases"]
                for _ in range(
                    1 if route == "dataset.html" else (
                        len(manifest["profile"]["methods"]) if manifest["rows"] else 0
                    )
                )
            ]
            if Counter(r.get("data-origin") for r in inventory.select("[data-row]")) != Counter(
                expected_origins
            ):
                raise ValueError("Query-source population mismatch")
            if route == "results.html":
                method_ids = {method["id"] for method in manifest["profile"]["methods"]}
                if {option["value"] for option in inventory.select("[data-method-filter] option")} != {"all", *method_ids}:
                    raise ValueError("Incomplete provider filter")
                if Counter(row.get("data-method") for row in inventory.select("[data-row]")) != Counter(
                    result["method"] for result in scoring_rows(manifest["profile"], manifest["rows"])
                ):
                    raise ValueError("Provider filter population mismatch")
    relevance_scores = (score_relevance(manifest["profile"], scoring_rows(manifest["profile"], manifest["rows"]), relevance_data["plan"], relevance_data["labels"]) if relevance_data else [])
    expected_diagnostics = operational(manifest["rows"], payloads, manifest["profile"]["methods"])
    cases = {c["id"]: c for c in manifest["profile"]["cases"]}
    for row in manifest["rows"]:
        method = next(m for m in manifest['profile']['methods'] if m['id'] == row['method'])
        validate_native_answer(row, payloads.get(row['id'], {}), method)
        if row["http_status"] == 200 and not row["error"]:
            if normalize_response(payloads[row["id"]], next(m for m in manifest["profile"]["methods"] if m["id"] == row["method"]), (manifest["profile"]["protocol"] if manifest.get("assessment_revision") else collection_protocol(manifest["profile"], row)), row.get("request")) != row["results"]:
                raise ValueError("Published results disagree with raw response")
        if row["metrics"] != evaluate(cases[row["case_id"]], row["results"]):
            raise ValueError("Published scores disagree with the declared rules")
    if relevance_data:
        selected = scoring_rows(manifest["profile"], manifest["rows"])
        if manifest["profile"].get("relevance_methods") != manifest.get("authored", {}).get("relevance_methods"):
            raise ValueError("Relevance method scope differs from authored content")
        relevance_profile, relevance_rows = relevance_scope(manifest["profile"], selected)
        verify_provenance(relevance_profile, relevance_rows, relevance_data, authority=authority)
        verify_protocol(relevance_profile, relevance_data, (manifest.get("authored") or {}).get("relevance_revision"), authority=authority)
    if manifest.get("scoring_revision"):
        revision = manifest["scoring_revision"]
        collected = [
            dict(
                r,
                metrics=evaluate(
                    next(
                        c
                        for c in manifest["collection_profile"]["cases"]
                        if c["id"] == r["case_id"]
                    ),
                    r["results"],
                ),
            )
            for r in manifest["rows"]
        ]
        revised, _, changes = apply_identity_correction(
            manifest["collection_profile"], collected, revision["contract"]
        )
        expected_changes = [c for c in revision["changes"] if c["id"] in {r["id"] for r in collected}]
        if changes != expected_changes:
            raise ValueError("Scoring revision provenance mismatch")
        if manifest.get("assessment_revision"):
            revised, _, _ = apply_report_revision(revised, [], manifest["assessment_revision"]["contract"], normalizer=normalize_response)
        revised, _ = project_comparison(revised, [], manifest.get("authored") or {})
        if manifest.get("report_assessment"):
            revised = assessment_profile(revised, relevance_data, review_data)
        if revised != manifest["profile"]:
            raise ValueError("Scoring revision profile mismatch")
    elif manifest.get("assessment_revision"):
        revision = manifest["assessment_revision"]
        if revision["contract"].get("normalization"):
            artifacts = {r["evidence"]["path"]: (output / r["evidence"]["path"]).read_bytes() for r in manifest["rows"]}
            collected = collected_projection(manifest["collection_profile"], manifest["rows"], artifacts, normalize_response)
            _, corrected, changes = apply_report_revision(manifest["collection_profile"], collected, revision["contract"], artifacts=artifacts, normalizer=normalize_response)
            if changes != revision["changes"] or any(
                any(a.get(k) != b.get(k) for k in ("results", "metrics", "error"))
                for a, b in zip(corrected, manifest["rows"], strict=True)
            ):
                raise ValueError("Native normalization correction provenance mismatch")
        revised, _, _ = apply_report_revision(manifest["collection_profile"], [], manifest["assessment_revision"]["contract"], normalizer=normalize_response)
        revised, _ = project_comparison(revised, [], manifest.get("authored") or {})
        if manifest.get("report_assessment"):
            revised = assessment_profile(revised, relevance_data, review_data)
        if revised != manifest["profile"]:
            raise ValueError("Assessment revision profile mismatch")
    if manifest.get("diagnostics") != expected_diagnostics:
        raise ValueError("Performance explanation disagrees with raw evidence")
    if evidence_only:
        if return_data:
            return manifest["profile"], scoring_rows(manifest["profile"], manifest["rows"])
        return True
    for route in manifest["routes"]:
        path = output / route
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        check_presentation(soup)
        authored = manifest.get("authored") or {}
        if "details/" not in route:
            lang = soup.html["lang"]
            name = Path(route).name
            key = {
                "index.html": "summary",
                "research.html": "method",
                "interpretation.html": "interpretation",
                "performance.html": "performance",
                "capabilities.html": "capabilities",
                "quality.html": "quality",
                "cost.html": "cost",
                "review.html": "review",
                "dataset.html": "dataset",
            }.get(name)
            if key:
                score_rows = scoring_rows(manifest["profile"], manifest["rows"])
                if authored.get("presentation") == "comparison-report":
                    pages = {"index.html": ["lead", "summary"], "quality.html": ["quality-lead", "quality-definitions", "quality"], "capabilities.html": ["capabilities-lead", "capabilities"], "performance.html": ["performance", "cost"]}.get(name, [key])
                    expected = ''.join(authored_sections(authored, section, lang, rows=score_rows) for section in pages)
                else:
                    expected = (authored_sections(authored, "lead", lang, rows=score_rows) if name == "index.html" and authored.get("reading_model") else "") + (authored_sections(authored, "interpretation-lead", lang, rows=score_rows) if name == "interpretation.html" else "") + authored_sections(authored, key, lang, rows=score_rows)
                if name == "index.html" and authored.get("reading_model") and authored.get("presentation") != "comparison-report":
                    chapters = manifest["reading_sections"]
                    if [s["data-reading"] for s in soup.select("[data-reading]")] != [
                        c["key"] for c in chapters
                    ]:
                        raise ValueError("Missing reader chapter")
                    expected += "".join(
                        authored_sections(authored, c["key"] + "-lead", lang, rows=score_rows, heading_level=3) + authored_sections(
                            authored, c["key"], lang, rows=score_rows, heading_level=3
                        )
                        for c in chapters
                    )
                expected_soup = BeautifulSoup(expected, "html.parser")
                for image in expected_soup.select("[data-authored-figure] img"):
                    image["src"] = posixpath.join(manifest.get("site_prefix", ""), image["src"])
                if [str(node) for node in soup.select("[data-authored-figure]")] != [
                    str(node) for node in expected_soup.select("[data-authored-figure]")
                ]:
                    raise ValueError("Reader figure differs from its bound content")
                if [s.get_text() for s in soup.select("[data-authored]")] != [
                    s.get_text() for s in expected_soup.select("[data-authored]")
                ]:
                    raise ValueError(
                        "Reader explanation or measured specimen differs from its bound content"
                    )
        quiet_certified = authored.get("presentation") == "comparison-report" and manifest["receipt"].get("certified") and not manifest["receipt"].get("synthetic")
        if len(soup.select("p[data-state]")) != (0 if quiet_certified else 1):
            raise ValueError("Missing evidence state")
        if soup.head is None:
            raise ValueError("Missing HTML head for artifact hosting")
        if route.endswith("performance.html") and manifest["rows"]:
            visible_diagnostics = expected_diagnostics
            if authored.get("presentation") == "comparison-report":
                primary_ids = {c["id"] for c in manifest["profile"]["cases"] if "reference_answer" not in c}
                primary_rows = [r for r in manifest["rows"] if r["case_id"] in primary_ids]
                visible_diagnostics = operational(primary_rows, payloads, manifest["profile"]["methods"])
                expected_operations = BeautifulSoup(benchmark_operations(manifest["profile"], scoring_rows(manifest["profile"], manifest["rows"]), authored, soup.html["lang"] == "zh"), 'html.parser')
                if str(soup.select_one('[data-benchmark-operations]')) != str(expected_operations.select_one('[data-benchmark-operations]')):
                    raise ValueError("Benchmark timing/cost summary differs from physical calls")
                expected_cost = BeautifulSoup(cost_panel(manifest["profile"], manifest["rows"], soup.html["lang"] == "zh", authored), 'html.parser')
                _resolve_owned_links(expected_cost, route, manifest.get("site_prefix", ""),
                                     {a["path"] for a in authored.get("assets", [])})
                if str(soup.select_one('[data-cost-budget]')) != str(expected_cost.select_one('[data-cost-budget]')):
                    raise ValueError("Cost budget differs from physical calls")
            trs = soup.select_one("[data-timing-decomposition]").select("table")[0].select("tbody tr")
            for tr, method in zip(trs, manifest["profile"]["methods"], strict=True):
                stats = visible_diagnostics["methods"][method["id"]]
                keys = (
                    "client_ms",
                    "server_ms",
                    "outside_server_ms",
                    "ttfb_ms",
                    "body_ms",
                    "bytes",
                )
                for cell, key in zip(tr.select("td")[1:], keys, strict=True):
                    expected = fmt(stats[key]["mean"]) + f" (n={stats[key]['n']})"
                    if cell.get_text() != expected:
                        raise ValueError("Visible performance decomposition mismatch")
            candidates = [
                c for c in visible_diagnostics["calls"] if c["after_first_ms"] is not None
            ]
            if candidates:
                chosen = max(candidates, key=lambda c: c["after_first_ms"])
                specimen = soup.select_one("[data-timing-specimen]")
                if specimen is None or specimen["data-timing-specimen"] != chosen["id"]:
                    raise ValueError("Missing or wrong timing specimen")
        if route.endswith("quality.html") and not level_views and (manifest.get("authored") or {}).get("presentation") == "comparison-report":
            score_rows = scoring_rows(manifest["profile"], manifest["rows"])
            for selector, expected in (("[data-factual]", factual_panel(manifest["profile"], score_rows, soup.html["lang"] == "zh", compact=True)),):
                expected_node = BeautifulSoup(expected, "html.parser").select_one(selector)
                if str(soup.select_one(selector)) != str(expected_node):
                    raise ValueError("Quality population differs from measured inputs")
        if route.endswith("results.html") and not level_views and authored.get("presentation") == "comparison-report":
            selected = scoring_rows(manifest["profile"], manifest["rows"])
            lang = soup.html["lang"]
            prefix = "" if lang == manifest["primary_language"] else lang + "/"
            expected = BeautifulSoup(render_result_comparison(manifest["profile"], selected, lang == "zh", prefix=prefix, site_prefix=manifest.get("site_prefix", ""), content=authored, relevance=relevance_data), "html.parser").select_one("[data-comparison-data]")
            actual = soup.select_one("[data-comparison-data]")
            if actual is None or json.loads(actual.string) != json.loads(expected.string):
                raise ValueError("Result comparison differs from measured inputs")
        if route.endswith("interpretation.html"):
            selected = scoring_rows(manifest["profile"], manifest["rows"])
            expected_table = BeautifulSoup(
                interpretation_summary_table(manifest["profile"], selected, soup.html["lang"] == "zh", manifest.get("authored") or {}, relevance_scores),
                "html.parser",
            ).select_one("table")
            actual_table = soup.select_one("table")
            if actual_table is None or str(actual_table) != str(expected_table):
                raise ValueError("Interpretation core comparison differs from evidence")
            expected_factual = BeautifulSoup(factual_panel(manifest["profile"], selected, soup.html["lang"] == "zh"), "html.parser").select_one("[data-factual]")
            if str(soup.select_one("[data-factual]")) != str(expected_factual):
                raise ValueError("Interpretation factual evidence differs from measured inputs")
        if route.endswith("cost.html") and (manifest.get("authored") or {}).get("presentation") == "comparison-report":
            expected_cost = BeautifulSoup(cost_panel(manifest["profile"], manifest["rows"], soup.html["lang"] == "zh", authored), "html.parser")
            _resolve_owned_links(expected_cost, route, manifest.get("site_prefix", ""),
                                 {a["path"] for a in authored.get("assets", [])})
            if str(soup.select_one("[data-cost-budget]")) != str(expected_cost.select_one("[data-cost-budget]")):
                raise ValueError("Cost budget differs from measured physical calls")
        if authored.get("presentation") == "comparison-report" and "details/" not in route:
            if Path(route).name in {"interpretation.html", "cost.html"}:
                raise ValueError("Retired duplicate reading route")
            nav_targets = {a.get('href') for a in soup.select('nav a')}
            if not any(target and target.endswith('quality.html') for target in nav_targets):
                raise ValueError("Quality page is missing from reader navigation")
        comparison_home = route.endswith("index.html") and (manifest.get("authored") or {}).get("presentation") == "comparison-report"
        if comparison_home and not level_views:
            expected = BeautifulSoup(executive_table(manifest["profile"], scoring_rows(manifest["profile"], manifest["rows"]), manifest["authored"], relevance_scores, soup.html["lang"] == "zh"), "html.parser")
            # Page assembly resolves edition-local links against the report root.
            for link in expected.select('a[href^="quality.html"]'):
                edition_prefix = '' if soup.html['lang'] == 'zh' else soup.html['lang']
                link['href'] = posixpath.join(manifest.get('site_prefix', ''), edition_prefix, link['href'])
            if str(soup.select_one('[data-executive]')) != str(expected.select_one('[data-executive]')):
                raise ValueError("Population summary differs from measured inputs")
            expected_discovery = BeautifulSoup(discovery_comparison(manifest['profile'], scoring_rows(manifest["profile"], manifest["rows"]), manifest['authored'], soup.html['lang'] == 'zh'), 'html.parser').select_one('[data-discovery]')
            if str(soup.select_one('[data-discovery]')) != str(expected_discovery):
                raise ValueError("Discovery comparison differs from measured inputs")
        if level_views and 'details/' in route:
            from harness.web_search.comparison import render_level_detail

            row_id = Path(route).stem
            row = next(r for r in manifest['rows'] if r['id'] == row_id)
            expected_detail = BeautifulSoup(render_level_detail(level_views, row, soup.html['lang'] == 'zh'), 'html.parser')
            if [node.get_text() for node in soup.select('[data-detail-level]')] != [node.get_text() for node in expected_detail.select('[data-detail-level]')]:
                raise ValueError('Per-level detail differs from scored fields and judgments')
        if level_views and route.endswith(('index.html', 'quality.html', 'capabilities.html', 'results.html')):
            from harness.web_search.comparison import (
                render_level_capabilities,
                render_level_findings,
                render_level_results,
                render_level_summary,
            )

            zh = soup.html['lang'] == 'zh'
            if route.endswith('index.html'):
                selector = '[data-output-level]'
                expected = ''.join(render_level_summary(view, zh, content=authored) for view in level_views.values())
            elif route.endswith('quality.html'):
                selector = '[data-level-findings]'
                expected = ''.join(render_level_findings(view, zh) for view in level_views.values())
            elif route.endswith('results.html'):
                selector = '[data-level-results]'
                prefix = '' if soup.html['lang'] == manifest['primary_language'] else soup.html['lang'] + '/'
                expected = render_level_results(level_views, zh, detail_link=lambda row, prefix=prefix: posixpath.join(manifest.get('site_prefix', ''), prefix, 'details', row['id'] + '.html'))
            else:
                selector = '[data-level-capabilities]'
                expected = render_level_capabilities(level_views, zh)
            expected_nodes = BeautifulSoup(expected, 'html.parser').select(selector)
            if [str(node) for node in soup.select(selector)] != [str(node) for node in expected_nodes]:
                raise ValueError('Output-level presentation differs from verified evidence')
        if route.endswith("index.html") and not comparison_home:
            for selector, renderer in (
                ("[data-modes]", mode_panel),
            ):
                score_rows = scoring_rows(manifest["profile"], manifest["rows"])
                expected_panel = BeautifulSoup(renderer(manifest["profile"], score_rows, soup.html["lang"] == "zh"), "html.parser").select_one(selector)
                if expected_panel and str(soup.select_one(selector)) != str(expected_panel):
                    raise ValueError("Deterministic evidence comparison differs from measured inputs")
            if relevance_data:
                selected = scoring_rows(manifest["profile"], manifest["rows"])
                scores = score_relevance(manifest["profile"], selected, relevance_data["plan"], relevance_data["labels"])
                expected_panel = BeautifulSoup(relevance_panel(manifest["profile"], scores, relevance_data, soup.html["lang"] == "zh"), "html.parser").select_one("[data-relevance]")
                actual_panel = soup.select_one("[data-relevance]")
                if actual_panel is None or str(actual_panel) != str(expected_panel):
                    raise ValueError("Preview relevance panel disagrees with judgments")
            if not soup.select("table"):
                raise ValueError("Missing core metrics")
            table_rows = soup.select("table")[0].select("tbody tr")
            overview_methods = scoped_overview_methods(manifest["profile"], manifest.get("authored") or {})
            if len(table_rows) != len(overview_methods):
                raise ValueError("Missing participant")
            overview_rows = scoped_overview_rows(manifest["profile"], manifest["rows"], manifest.get("authored") or {})
            physical = aggregate(overview_rows)
            cutoff = manifest["profile"]["protocol"]["top_k"]
            hit_key = "anchor_hit_5" if cutoff == 5 else "anchor_hit_10"
            quality = aggregate(
                scoring_rows(manifest["profile"], overview_rows)
            )
            for tr, method in zip(table_rows, overview_methods, strict=True):
                stats = physical.get(method["id"])
                if not stats:
                    continue
                for metric, key in (("mean-ms", "mean"), ("p50-ms", "p50"), ("p95-ms", "p95")):
                    cell = tr.select_one(f'td[data-metric="{metric}"]')
                    value = stats["all_latency"][key]
                    if float(cell["data-value"]) != value or cell.get_text().replace(
                        "★", ""
                    ).strip() != fmt(value):
                        raise ValueError("Visible latency disagrees with evidence")
                cell = tr.select_one('td[data-metric="target-hit"]')
                hit = quality[method["id"]]["metrics"][hit_key]
                expected = (
                    "—" if hit["mean"] is None else (
                        report_text("{value:.1f}% ({n} queries)", soup.html["lang"] == "zh", value=hit['mean'] * 100, n=hit['n'])
                    )
                )
                if cell.get_text().replace("★", "").strip() != expected:
                    raise ValueError("Visible target hit disagrees with evidence")
        if route.endswith("capabilities.html") and manifest.get("authored", {}).get("interface_example_case_id"):
            cid = manifest["authored"]["interface_example_case_id"]
            example = soup.select_one("[data-interface-case]")
            if example is None or example["data-interface-case"] != cid or [n["data-interface-method"] for n in example.select("[data-interface-method]")] != [m["id"] for m in manifest["profile"]["methods"]]:
                raise ValueError("Interface example must show the same query for every method")
        if "details/" in route and relevance_scores:
            result_id = Path(route).stem
            row = next((r for r in manifest["rows"] if r["id"] == result_id), None)
            expected_error = error_evidence(row, payloads.get(row["id"], {})) if row else ""
            if expected_error and soup.select_one("[data-error]").get_text() != expected_error:
                raise ValueError("Exact error details differ from native evidence")
            query_score = next((q for q in relevance_scores if row and q["case_id"] == row["case_id"] and q["method"] == row["method"]), None)
            if query_score:
                expected = BeautifulSoup(relevance_detail(query_score, soup.html["lang"] == "zh"), "html.parser").select_one('[data-case-relevance]')
                if str(soup.select_one('[data-case-relevance]')) != str(expected):
                    raise ValueError("Query quality calculation differs from frozen grades")
        if "details/" in route and (
            len(soup.select("[data-summary]")) != 1
            or not soup.select("[data-error]")
            or not soup.select("[data-evidence]")
        ):
            raise ValueError("Missing detail summary, error or evidence")
        if "details/" in route:
            row = next(r for r in manifest["rows"] if r["id"] == Path(route).stem)
            expected_error = error_evidence(row, payloads.get(row["id"], {}))
            if expected_error and soup.select_one("[data-error]").get_text() != expected_error:
                raise ValueError("Exact error details differ from native evidence")
        for a in soup.select("[href],[data-evidence],[data-detail]"):
            if a.name == "base":
                continue
            href = a.get("href", a.get("data-evidence", a.get("data-detail", "")))
            if href.startswith(("https://", "http://", "#")):
                continue
            href = urlsplit(href).path
            site_root = output.parent if manifest.get("site_prefix") else output
            target = (site_root / href).resolve()
            if not target.is_relative_to(site_root.resolve()) or not target.is_file():
                raise ValueError("Broken evidence/navigation target: " + href)
        if "/Users/" in str(soup):
            raise ValueError("Private data in report")
    if return_data:
        selected = scoring_rows(manifest["profile"], manifest["rows"])
        return manifest["profile"], selected
    return True


def validate_content_revisions(manifest):
    """Verify paragraph-only revisions against their retained predecessor bytes."""
    def require(condition):
        if not condition:
            raise ValueError('Invalid paragraph revision provenance')

    current = manifest
    while current.get('content_revisions'):
        receipt = current['content_revisions'][-1]
        try:
            require(receipt['schema'] in ('web-search-paragraph-revision/1', 'web-search-paragraph-revision/2'))
            producer = receipt['producer']
            require(bool(re.fullmatch(r'[0-9a-f]{64}', producer['source_sha256'])))
            require(bool(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', producer['version'])))
            require(producer['revision'] is None or bool(re.fullmatch(r'[0-9a-f]{40}', producer['revision'])))
            require(type(producer['modified']) is bool)
            raw = gzip.decompress(base64.b64decode(receipt['before_manifest_gzip_base64'], validate=True))
            previous = json.loads(raw)
            require(bytes_sha256(raw) == receipt['before_manifest_sha256'])
            require(previous.get('content_sha256') == receipt['before_content_sha256'])
            require(current.get('content_sha256') == receipt['content_sha256'])
            require(bytes_sha256(receipt['content_utf8'].encode('utf-8')) == receipt['content_sha256'])
            require(json.loads(receipt['content_utf8']) == current['authored'])
            require(type(receipt['provider_calls']) is int and receipt['provider_calls'] == 0)
            if receipt['schema'] == 'web-search-paragraph-revision/2':
                require(len(current['content_revisions']) == 1)
            else:
                require(current['content_revisions'][:-1] == previous.get('content_revisions', []))
            excluded = {'authored', 'content_sha256', 'content_revisions'}
            require({k: v for k, v in current.items() if k not in excluded} == {
                k: v for k, v in previous.items() if k not in excluded})
            old, new = previous['authored'], current['authored']
            require(dict(old, sections=[]) == dict(new, sections=[]))
            changed = []
            for index, (left, right) in enumerate(zip(old.get('sections', []), new.get('sections', []), strict=True)):
                require(dict(left, paragraphs=[]) == dict(right, paragraphs=[]))
                for a, b in zip(left.get('paragraphs', []), right.get('paragraphs', []), strict=True):
                    require(a.keys() == b.keys() and all(isinstance(v, str) for v in b.values()))
                if left.get('paragraphs') != right.get('paragraphs'):
                    changed.append(index)
            require(receipt['changed_sections'] == changed)
        except (KeyError, TypeError, ValueError, OSError, EOFError) as error:
            raise ValueError('Invalid paragraph revision provenance') from error
        current = previous


def revise_paragraphs(output, content):
    """Revise authored paragraphs only; retain evidence and require normal release checks."""
    output, content = Path(output), Path(content)
    path = contained_path(output, 'manifest.json', reject_symlinks=True)
    before = path.read_bytes()
    manifest = json.loads(before)
    validate_content_revisions(manifest)
    if (manifest.get('schema') != 'web-search-report/1' or not manifest.get('languages')
            or set(manifest['languages']) - {'en', 'zh'}):
        raise ValueError('Paragraph revisions require a supported Web Search report')
    previous = manifest.get('authored') or {}
    raw = content.read_bytes()
    revised = json.loads(raw)
    if (dict(previous, sections=[]) != dict(revised, sections=[])
            or len(previous.get('sections', [])) != len(revised.get('sections', []))):
        raise ValueError('Paragraph revisions cannot change report inputs or structure')
    changed = []
    for index, (old, new) in enumerate(zip(previous.get('sections', []), revised.get('sections', []), strict=True)):
        if (dict(old, paragraphs=[]) != dict(new, paragraphs=[])
                or len(old.get('paragraphs', [])) != len(new.get('paragraphs', []))):
            raise ValueError('Paragraph revisions cannot change report inputs or structure')
        for left, right in zip(old.get('paragraphs', []), new.get('paragraphs', []), strict=True):
            if left.keys() != right.keys() or any(not isinstance(value, str) for value in right.values()):
                raise ValueError('Paragraph revisions must retain language coverage')
        if old.get('paragraphs') != new.get('paragraphs'):
            changed.append(index)
    if not changed and bytes_sha256(raw) == manifest.get('content_sha256'):
        return output / 'index.html'
    seen = {(index, lang): 0 for index in changed for lang in manifest['languages']}
    updates = {}
    for lang in manifest['languages']:
        prefix = '' if lang == 'zh' else lang + '/'
        for page in reader_pages(previous.get('presentation'), reviewed=bool(manifest.get('independent_review_sha256'))):
            target = contained_path(output, prefix + page['route'], reject_symlinks=True)
            soup = BeautifulSoup(target.read_text(encoding='utf-8'), 'html.parser')
            touched = False
            for index in changed:
                old = previous['sections'][index]['paragraphs']
                new = revised['sections'][index]['paragraphs']
                for section in soup.select(f'[data-authored="{index}"]'):
                    paragraphs = section.find_all('p', recursive=False)[:len(old)]
                    current = [p.get_text() for p in paragraphs]
                    if current not in ([p[lang] for p in old], [p[lang] for p in new]):
                        raise ValueError('Existing authored paragraphs differ from their frozen content')
                    for node, paragraph in zip(paragraphs, new, strict=True):
                        node.string = paragraph[lang]
                    seen[index, lang] += 1
                    touched = True
            if touched:
                updates[target] = str(soup)
    if any(count == 0 for count in seen.values()):
        raise ValueError('Revised paragraph is missing from a generated language')
    receipt = {'schema': 'web-search-paragraph-revision/2',
               'before_manifest_sha256': bytes_sha256(before),
               'before_manifest_gzip_base64': base64.b64encode(gzip.compress(before, mtime=0)).decode('ascii'),
               'before_content_sha256': manifest.get('content_sha256'),
               'content_sha256': bytes_sha256(raw), 'content_utf8': raw.decode('utf-8'), 'provider_calls': 0,
               'changed_sections': changed, 'producer': software_identity()}
    manifest['authored'], manifest['content_sha256'] = revised, bytes_sha256(raw)
    manifest['content_revisions'] = [receipt]
    for target, text in updates.items():
        atomic_write(target, text.encode('utf-8'))
    write_json(path, manifest)
    return output / 'index.html'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", type=Path, help="External instance selector; required without --source"
    )
    parser.add_argument("--source", type=Path)
    parser.add_argument(
        "--cohort",
        action="append",
        default=[],
        metavar="NAME=RUN",
        help="Add a separately verified cohort to the same report",
    )
    parser.add_argument("--content", type=Path)
    parser.add_argument('--revise-paragraphs', type=Path,
                        help='Revise authored paragraphs in an existing report without rebuilding evidence pages')
    parser.add_argument("--identity-correction", type=Path)
    parser.add_argument("--review", type=Path, help="Complete independent review with adjudications")
    parser.add_argument("--relevance", type=Path, help="Complete, provenance-bound relevance labels for every in-house query")
    parser.add_argument("--export-annotations", type=Path, metavar="DESTINATION",
                        help="Prepare full in-house human annotations from an existing checked report")
    parser.add_argument("--site-prefix", default="")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--language", choices=["en", "zh"], default=None)
    parser.add_argument("--include-english", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--release-check", type=Path, metavar="VISUAL_RECEIPT")
    parser.add_argument("--publication-plan", type=Path)
    parser.add_argument("--export-delivery", type=Path, metavar="DESTINATION",
                        help="Export the checked report into lossless case shards for bounded-file hosts")
    args = parser.parse_args(argv)
    if args.revise_paragraphs:
        if any(value for key, value in vars(args).items() if key not in ('output', 'revise_paragraphs')):
            parser.error('Paragraph revisions accept only --output and --revise-paragraphs')
        print(revise_paragraphs(args.output, args.revise_paragraphs))
        return
    if args.export_annotations:
        if args.source or args.cohort:
            parser.error("Annotation export consumes an existing report")
        profile, selected = check(args.output, return_data=True)
        print(export_annotations(profile, selected, args.export_annotations))
        return
    if args.export_delivery:
        if args.source or args.cohort:
            parser.error("Delivery export consumes an existing report, not build inputs")
        if args.release_check:
            release_check(args.output, args.release_check, publication_plan=args.publication_plan)
        else:
            check(args.output)
        from harness.web_search.delivery import export, verify
        export(args.output, args.export_delivery)
        verify(args.output, args.export_delivery)
        print(args.export_delivery)
        return
    if args.release_check:
        release_check(args.output, args.release_check, publication_plan=args.publication_plan)
        print("Web Search full release checks passed")
        return
    if args.check:
        check(args.output)
        print("Web Search report checks passed")
        return
    language = resolve_report_language(args.output, args.language)
    print(
        build(
            args.output,
            profile_path=args.profile,
            source=args.source,
            content=args.content,
            correction=args.identity_correction,
            relevance=args.relevance,
            review=args.review,
            site_prefix=args.site_prefix,
            cohorts=[
                (value.split("=", 1)[0], Path(value.split("=", 1)[1])) for value in args.cohort
            ],
            language=language,
            include_english=args.include_english,
        )
    )


def release_check(output, visual_receipt, *, publication_plan=None):
    """Combine deterministic evidence checks with the separate operated-browser gate."""
    check(output)
    proof = json.loads(visual_receipt.read_text(encoding="utf-8"))
    manifest = output / "manifest.json"
    if proof["manifest_sha256"] != bytes_sha256(manifest.read_bytes()):
        raise ValueError("Visual check belongs to a different build")
    report_manifest = json.loads(manifest.read_text(encoding="utf-8"))
    if not report_manifest.get("receipt", {}).get("synthetic"):
        if not report_manifest.get("receipt", {}).get("certified"):
            raise ValueError("A formal release requires certified collection evidence")
        if publication_plan is not None:
            from harness.web_search.publication import verify_plan
            verify_plan(output, publication_plan.parent)
        else:
            raise ValueError("A measured release requires an explicit publication plan")
    from harness.web_search.visual import verify
    return verify(output, visual_receipt)


def cohort_label(profile, name, zh):
    return next(
        (
            g["label"]["zh" if zh else "en"]
            for g in profile.get("report_cohorts", [])
            if g["id"] == name
        ),
        "",
    )


def origin_group(case):
    kind = case.get("provenance_kind", "curated")
    return "originals" if kind in {"community_exact", "user_screenshot"} else kind


def origin_filter(profile, zh):
    counts = Counter(origin_group(c) for c in profile["cases"])
    labels = {
        "originals": report_text("Post and screenshot transcriptions", zh),
        "community_derived": report_text("Derived scenarios", zh),
        "public_catalog": report_text("Public query catalog", zh, context="supplement"),
        "curated": report_text("Curated probes", zh),
        "public_labeled": report_text("Public factual queries", zh),
    }
    return (
        "<label>"
        + report_text("Query source", zh)
        + ' <select data-origin-filter><option value="all">'
        + report_text("All", zh)
        + "</option>"
        + "".join(
            '<option value="'
            + esc(k)
            + '">'
            + esc(labels.get(k, k))
            + f" ({n})</option>"
            for k, n in counts.items()
        )
        + "</select></label> "
    )


def cohort_filter(profile, zh):
    groups = profile.get("report_cohorts", [])
    if not groups:
        return ""
    return (
        "<label>"
        + report_text("Benchmark", zh)
        + ' <select data-cohort-filter><option value="all">'
        + report_text("All queries", zh)
        + "</option>"
        + "".join(
            f'<option value="{esc(g["id"])}">{esc(cohort_label(profile, g["id"], zh))}</option>'
            for g in groups
        )
        + "</select></label> "
    )


def cohort_summary(profile, rows, zh):
    groups = profile.get("report_cohorts", [])
    if not groups:
        return ""

    t = partial(report_text, zh=zh)

    entries = []
    for group in groups:
        subset = [r for r in rows if r["cohort"] == group["id"]]
        stats = aggregate(subset)
        for m in profile["methods"]:
            calls = [r for r in subset if r["method"] == m["id"]]
            s = stats[m["id"]]
            entries.append(
                cells(
                    [
                        esc(cohort_label(profile, group["id"], zh)),
                        esc(m["label"]),
                        str(len(group["case_ids"])),
                        str(s["n"]),
                        f"{s['nonempty']}/{s['n']}",
                        fmt(sum(len(r["results"]) for r in calls) / len(calls)),
                        *[fmt(s["all_latency"][k]) for k in ("mean", "p50", "p95")],
                    ]
                )
            )
    return (
        "<section data-cohort-summary><h2>"
        + t("Independent benchmarks, with separate collection cohorts")
        + "</h2><p>"
        + t("{cases} queries and {rows} final logical results, from {physical_calls} physical requests including retries. The table preserves the selected source scopes; the combined count is not a same-window speed trial.", cases=len(profile['cases']), rows=len(rows), physical_calls=sum(len(r.get('attempts') or [r]) for r in rows), groups=len(profile.get("method_collections", groups)))
        + "</p>"
        + table(
            [
                t("Query group"),
                t("Method", context="provider"),
                t("Queries/provider"),
                t("Search tasks"),
                t("Nonempty/final results"),
                t("Mean returned count"),
                t("Mean ms"),
                "P50 ms",
                "P95 ms",
            ],
            entries,
        )
        + "</section>"
    )


def method_collection_notice(zh):
    return ('<p data-method-collection-notice>'
            + report_text("Methods were collected on different dates; saved latency is not a same-window speed trial.", zh)
            + ' <a href="research.html">' + report_text("Collection dates and call order", zh) + '</a></p>')


def execution_settings(profile, zh):
    """Disclose effective admission limits and the latency measurement scope."""
    t = partial(report_text, zh=zh)
    matrix = profile.get('collection_composition') == 'matrix'
    batches = profile.get('method_collections',[])
    maximum = (max(b['protocol']['concurrency'] for b in batches) if matrix
               else profile['protocol']['concurrency'])

    def admission(context, method):
        limit = context['protocol']['concurrency']
        policy = context.get('execution_policy')
        bindings = execution_resources(context) if policy else {}
        resources = bindings.get(method['id'],())
        capacity = min([limit] + [policy['resources'][r]['capacity'] for r in resources])
        details = '; '.join(f'{r}: {policy["resources"][r]["capacity"]}'
                            + (f' ({policy["resources"][r]["reason"]})'
                               if policy['resources'][r].get('reason') else '') for r in resources)
        return capacity, details

    entries = []
    for method in profile["methods"]:
        if matrix:
            settings = [admission(dict(protocol=b['protocol'],execution_policy=b.get('execution_policy'),
                                       methods=b['execution_methods']), method)
                        for b in batches if method['id'] in b['methods']]
            capacity = ' / '.join(str(v) for v in sorted({value for value,_ in settings}))
            details = '; '.join(dict.fromkeys(text for _,text in settings if text))
        else:
            capacity,details = admission({**profile,'methods':profile.get('collection_methods',profile['methods'])},method)
        entries.append(cells([esc(method['label']),str(capacity),esc(details or '—')]))
    scope = (t("Latency includes concurrent collection load; it is not an isolated speed comparison.")
             if maximum > 1 else t("Serial collection; latency covers each saved API call."))
    collections = ""
    if profile.get("method_collections"):
        def collected_dates(batch):
            start = (batch.get("started_at") or batch.get("ended_at") or batch.get("as_of") or "—")[:10]
            end = (batch.get("ended_at") or start)[:10]
            return start if start == end else start + " / " + end

        method_labels = {m["id"]: m["label"] for m in profile["methods"]}
        orders = {"complete provider batches": t("complete provider batches"),
                  "rotate methods by case": t("rotate methods by case"),
                  "rotate methods by case and round": t("rotate methods by case and round")}
        collections = ('<p>' + t("Methods were collected in separate batches. Dates and order are shown below; latency describes each saved batch, not a same-window speed trial.") + '</p>'
                       + table([t("Collection"), t("Methods"), t("Date"), t("Call order")]
                               + ([t('Concurrent calls'),t('Client timeout (s)'),t('Allowed retries')] if matrix else []),
                               [cells([esc(b["id"]), esc(", ".join(method_labels.get(mid, mid) for mid in b["methods"])), esc(collected_dates(b)), esc(orders.get(b["order"], b["order"]))]
                                      + ([str(b['protocol']['concurrency']),str(b['protocol']['client_timeout_s']),str(int(b['protocol'].get('retry',False)))] if matrix else []))
                                for b in profile["method_collections"]]))
    return ('<section data-execution-settings><h2>' + t("Collection concurrency") + '</h2><p>'
            + t("Maximum concurrent calls: {concurrency}.", concurrency=maximum)
            + ' ' + scope + '</p>'
            + table([t("Method"), t("Effective concurrent calls"), t("Shared resource limits")],
                    entries) + collections + '</section>')


def fairness_table(profile, rows, zh):
    """Expose requested budgets and actual delivery without equal-count filtering."""

    t = partial(report_text, zh=zh)

    def requested_limit(row, method):
        request = row.get("request", {})
        for key in ("limit", "count", "numResults", "num", "Cnt"):
            if key in request:
                return str(request[key])
        default = t("Default") if method.get("adapter") == "brightdata" else "—"
        return parse_qs(urlsplit(request.get("url", "")).query).get("num", [default])[0]

    entries = []
    first = {}
    for method in profile["methods"]:
        group = [r for r in rows if r["method"] == method["id"]]
        requested = Counter(
            requested_limit(r, method)
            for r in group
        )
        counts = Counter(len(r["results"]) for r in group)
        first[method["id"]] = {r["case_id"]: len(r["results"]) for r in group if r["round"] == 0}
        entries.append(
            cells(
                [
                    esc(method["label"]),
                    str(len(group)),
                    esc(", ".join(f"{k} × {v}" for k, v in sorted(requested.items()))),
                    esc(", ".join(f"{k} × {v}" for k, v in sorted(counts.items()))),
                    fmt(sum(len(r["results"]) for r in group) / len(group)) if group else "—",
                ]
            )
        )
    common = set.intersection(*(set(v) for v in first.values())) if first else set()
    equal = sum(len({v[c] for v in first.values()}) == 1 for c in common)
    return (
        "<section data-fairness><h2>"
        + t("Request limits and scored result counts")
        + "</h2>"
        + table(
            [
                t("Provider", context="api"),
                t("Search tasks"),
                t("Requested limit × tasks"),
                t("Returned items × tasks"),
                t("Mean items per task"),
            ],
            entries,
        )
        + "<p>"
        + t("First-round queries with equal result counts across all providers: {equal}/{common}. Empty and short responses remain in every declared denominator. Counts do not establish relevance.", equal=equal, common=len(common))
        + "</p></section>"
    )


def comparison_tables(profile, selected, rows, zh):
    t = partial(report_text, zh=zh)

    cutoff = profile["protocol"]["top_k"]
    hit_key = "anchor_hit_5" if cutoff == 5 else "anchor_hit_10"
    body = "<h2>" + t("Separate task populations") + "</h2>"
    bycase = {c["id"]: c for c in profile["cases"]}
    grouped = []
    for family in dict.fromkeys(c["family"] for c in profile["cases"]):
        stats = aggregate([r for r in selected if bycase[r["case_id"]]["family"] == family])
        for method in profile["methods"]:
            s = stats.get(method["id"])
            m = s["metrics"] if s else {}

            def metric(k, m=m):
                x = m.get(k, {"mean": None, "n": 0})
                return fmt(x["mean"]) + f" (n={x['n']})"

            grouped.append(
                cells(
                    [
                        esc(
                            {
                                "web": t("Web"),
                                "site": t("Site restriction"),
                                "vertical": t("Native source / site route"),
                            }[family]
                        ),
                        esc(method["label"]),
                        metric(hit_key),
                        metric("reciprocal_rank"),
                        metric("domain_precision"),
                        metric("duplicate_rate"),
                    ]
                )
            )
    body += table(
        [
            t("Population", context="scenario"),
            t("Method"),
            f"Hit@{cutoff}",
            "MRR",
            t("Domain fraction"),
            t("Duplicate fraction"),
        ],
        grouped,
    )
    body += (
        "<p>"
        + t("Each metric shows its eligible query count. Hit@{cutoff} and MRR use declared known pages; domain fraction uses returned top-{cutoff} URLs and is unavailable for empty responses. Native source and site operators implement the same intent through different routes.", cutoff=cutoff)
        + "</p>"
    )
    body += "<h2>" + t("Text delivery and estimated charges") + "</h2>"
    stats = aggregate(selected)
    physical = aggregate(rows)
    textrows = []
    for method in profile["methods"]:
        s = stats.get(method["id"])
        m = s["metrics"] if s else {}
        total_cost = physical.get(method["id"], {}).get("cost_total")
        nonempty = s["nonempty"] if s else 0
        textrows.append(
            cells(
                [
                    esc(method["label"]),
                    *[
                        fmt(m.get(k, {}).get("mean"))
                        for k in (
                            "result_count",
                            "snippet_chars",
                            "summary_chars",
                            "content_chars",
                            "content_coverage",
                        )
                    ],
                    (
                        "—"
                        if total_cost is None
                        else f"{total_cost:.3f}"
                    )
                    + " "
                    + method["currency"],
                    ("—" if total_cost is None or not nonempty else f"{total_cost / nonempty:.4f}")
                    + " " + method["currency"],
                ]
            )
        )
    body += table(
        [
            t("Method"),
            t("Results/query"),
            t("Snippet chars/query"),
            t("Summary chars/query"),
            t("Content chars/query"),
            t("Content coverage"),
            t("All-call estimate"),
            t("Cost/nonempty query"),
        ],
        textrows,
    )
    body += (
        "<p>"
        + t("Text sizes count Unicode characters, not tokens or correctness. Native fields are retained separately: text in a snippet is not automatically full content. No source-faithfulness or answer-accuracy score is assigned without independent judgments. Cost is an estimate from list prices; a transport timeout has unknown billing.")
        + "</p>"
    )
    return body


def same_task(peers, profile, zh):
    if len(peers) < 2:
        return ""

    t = partial(report_text, zh=zh)

    labels = {m["id"]: m["label"] for m in profile["methods"]}
    headings = [t("Rank")] + [labels[p["method"]] for p in peers]
    rows = []
    for rank in range(max((len(p["results"][:10]) for p in peers), default=0)):
        values = [str(rank + 1)]
        for peer in peers:
            if rank >= len(peer["results"]):
                values.append("—")
                continue
            item = peer["results"][rank]
            values.append(
                f'<a href="{esc(item["url"])}">{esc(item["title"])}</a><p>{esc(item["snippet"][:180])}</p>'
            )
        rows.append(cells(values))
    links = " · ".join(f'<a href="{p["id"]}.html">{esc(labels[p["method"]])}</a>' for p in peers)
    return (
        "<h2>"
        + t("Same-query results")
        + "</h2><p>"
        + links
        + "</p>"
        + table(headings, rows)
    )


def latency_rounds(profile, rows, zh):
    """Expose repeated-query and reported cache state without inferring cold starts."""

    t = partial(report_text, zh=zh)

    entries = []
    for round_id in range(profile["protocol"]["rounds"]):
        for method in profile["methods"]:
            subset = [r for r in rows if r["round"] == round_id and r["method"] == method["id"]]
            if not subset:
                continue
            stats = aggregate(subset)[method["id"]]["all_latency"]
            caches = Counter((r.get("cache") or {}).get("state", "unknown") for r in subset)
            cache_text = ", ".join(f"{state}: {n}" for state, n in sorted(caches.items()))
            entries.append(
                cells(
                    [
                        str(round_id + 1),
                        esc(method["label"]),
                        str(len(subset)),
                        *[fmt(stats[k]) for k in ("mean", "p50", "p95")],
                        esc(cache_text),
                    ]
                )
            )
    return (
        "<h2>"
        + t("Repeated-query latency and reported cache state")
        + "</h2>"
        + table(
            [
                t("Round"),
                t("Method"),
                "N",
                t("Mean ms", context="duration"),
                "P50 ms",
                "P95 ms",
                t("API cache state"),
            ],
            entries,
        )
        + "<p>"
        + t("All declared calls remain in the headline latency. Cache states come from the API response; unknown means the provider did not report one. The first round is not guaranteed to be a cold cache. Repeated queries describe reuse behavior, not sustained-load throughput.")
        + "</p>"
    )


def authored_sections(content, page, language, *, rows=(), heading_level=2):
    body = ""
    for section_index, s in enumerate(content.get("sections", [])):
        if s.get("page", "method") != page:
            continue
        tag = 'details' if s.get('collapsed') else 'section'
        heading = 'summary' if s.get('collapsed') else f'h{heading_level}'
        body += f'<{tag} class="chapter" data-authored="{section_index}"><{heading}>' + esc(s['title'][language]) + f'</{heading}>'
        if s.get("diagram"):
            body += (
                '<div class="process"><div class="flow-fallback">'
                + "".join("<span>" + esc(step[language]) + "</span>" for step in s["steps"])
                + '</div><pre class="mermaid" hidden>'
                + esc(s["diagram"][language])
                + "</pre></div>"
            )
        for p in s.get("paragraphs", []):
            body += "<p>" + esc(p[language]) + "</p>"
        for figure in s.get("figures", []):
            source = figure["path"]
            if not any(asset.get("path") == source and asset.get("sha256") for asset in content.get("assets", [])):
                raise ValueError("Reader figures require a hash-bound report asset")
            body += ('<figure data-authored-figure><div class="figure-scroll"><img src="'
                     + esc(source) + '" alt="' + esc(figure["alt"][language])
                     + '"></div><figcaption>' + esc(figure["caption"][language]) + '</figcaption></figure>')
        if s.get("rows"):
            body += table(
                s["headers"][language], [cells([esc(v) for v in r[language]]) for r in s["rows"]]
            )
        for sample in s.get("samples", []):
            body += (
                "<details><summary>"
                + esc(sample["label"][language])
                + "</summary><pre>"
                + esc(sample["code"])
                + "</pre></details>"
            )
        specimen_columns = s.get('specimens_layout') == 'columns' and bool(s.get('specimens'))
        if specimen_columns:
            body += '<div class="specimen-grid">'
        for specimen in s.get("specimens", []):
            row = next(
                (
                    r
                    for r in rows
                    if r["case_id"] == specimen["case_id"] and r["method"] == specimen["method"]
                ),
                None,
            )
            if row is None:
                raise ValueError("Missing measured specimen")
            item = row["results"][specimen.get("result_index", 0)]
            body += (
                f'<article class="specimen" data-specimen="{esc(row["id"])}"><h3>'
                + esc(specimen["label"][language])
                + "</h3>"
                + ("<p>" + esc(specimen["explanation"][language]) + "</p>" if specimen.get("explanation") else "")
                + "<p>" + esc(item["title"])
                + '</p><p><a href="'
                + esc(item["url"])
                + '">'
                + esc(report_text("Open source page · ", language == "zh")
                      + (urlsplit(item["url"]).hostname or ""))
                + "</a></p>"
            )
            for field in specimen["fields"]:
                value = item.get(field) or ""
                excerpt = (
                    value
                    if field in specimen.get("full_fields", [])
                    else value[: specimen.get("excerpt_chars", 450)]
                )
                body += (
                    "<p>"
                    + esc(field)
                    + " · "
                    + str(len(value))
                    + report_text(" characters", language == "zh")
                    + '</p><blockquote class="field-value">'
                    + esc(excerpt)
                    + "</blockquote>"
                )
            body += (
                '<p><a href="details/'
                + esc(row["id"])
                + '.html">'
                + report_text("Inspect the full measured response", language == "zh")
                + "</a></p></article>"
            )
        if specimen_columns:
            body += '</div>'
        for link in s.get("sources", []):
            label = link["label"]
            if isinstance(label, dict):
                label = label[language]
            url = link["url"]
            if isinstance(url, dict):
                url = url[language]
            if link.get("evidence"):
                body += (
                    '<p><button data-evidence="' + esc(url) + '">' + esc(label) + "</button></p>"
                )
                continue
            body += f'<p><a href="{esc(url)}">{esc(label)}</a></p>'
        body += f"</{tag}>"
    return body


def provenance_label(kind, zh):
    return {
        "community_exact": report_text("Published community query", zh),
        "community_derived": report_text("Community-derived scenario", zh),
        "public_catalog": report_text("Public query catalog", zh),
        "public_labeled": report_text("Public human-verified factual dataset", zh),
        "curated": report_text("Curated supplement", zh),
        "user_screenshot": report_text("Exact query from supplied screenshot", zh),
    }.get(kind, kind)


def search_category(value, zh):
    if not zh:
        return value.replace("_", " ")
    return {
        "web": zh_text("web"), "site": zh_text("site"), "vertical": zh_text("vertical"),
        "factual": zh_text("factual"),
        "known_item": zh_text("known item"), "primary_source": zh_text("primary source"),
        "technical_exact": zh_text("technical exact"), "semantic_long_tail": zh_text("semantic long tail"),
        "niche_small_web": zh_text("niche small web"), "multi_constraint": zh_text("multi constraint"),
        "exploratory_diversity": zh_text("exploratory diversity"), "multilingual_regional": zh_text("multilingual regional"),
        "robustness_noisy": zh_text("robustness noisy"), "freshness_rolling": zh_text("freshness rolling"),
        "creator_discovery": zh_text("creator discovery"), "chinese_service": zh_text("chinese service"),
        "community_original": zh_text("community original"), "wechat": zh_text("wechat"),
        "xiaohongshu": zh_text("xiaohongshu"), "zhihu": zh_text("zhihu"), "douyin": zh_text("douyin"),
        "weibo": zh_text("weibo"), "bilibili": zh_text("bilibili"), "douban": zh_text("douban"),
        "baijiahao": zh_text("baijiahao"), "toutiao": zh_text("toutiao"), "x": "X",
        "reddit": "Reddit", "instagram": "Instagram", "facebook": "Facebook",
        "linkedin": "LinkedIn", "medium": "Medium", "substack": "Substack",
    }.get(value, value)


def source_note(case, zh):
    if not zh:
        return case.get("derivation", case["origin"])
    return {
        "user_screenshot": zh_text("Transcribed verbatim from the supplied screenshot; a site match does not imply relevant content."),
        "community_exact": zh_text("Original query from public material; every API requested the same number of results this round, which may differ from the original author's setting."),
        "community_derived": zh_text("Query written from a public discussion; not the original author's query."),
        "public_catalog": zh_text("Original query from a public catalog; the declared URL only checks discovery of that page."),
        "curated": zh_text("Query added to cover this scenario."),
        "public_labeled": zh_text("Original public human-curated query, answer and supporting sources; preselected by shared input length."),
    }.get(case.get("provenance_kind", "curated"), "")


def provenance_table(profile, zh):
    counts = Counter(c.get("provenance_kind", "curated") for c in profile["cases"])
    return (
        "<h2>"
        + report_text("Where the queries come from", zh)
        + "</h2>"
        + table(
            [report_text("Source type", zh), report_text("Unique queries", zh)],
            [cells([provenance_label(k, zh), str(v)]) for k, v in counts.items()],
        )
        + "<p>"
        + (
            report_text("{cases} unique queries; repeated calls do not add queries.", zh, cases=len(profile['cases']))
        )
        + "</p>"
    )


def numeric_winners(rows, directions):
    """Return unique extrema from available raw values; ties remain neutral."""
    winners = {}
    for column, direction in directions.items():
        available = [row[column] for row in rows if row[column] is not None]
        if direction == "neutral" or not available:
            continue
        best = (min if direction == "min" else max)(available)
        if available.count(best) == 1:
            winners[column] = best
    return winners


def numeric_cell(label, value, metric, direction, winner=False, *, significant=None):
    attrs = f' data-metric="{esc(metric)}" data-metric-direction="{direction}"'
    if value is not None:
        attrs += f' data-sort="{value}" data-value="{value}"'
    else:
        attrs += ' data-value=""'
    if significant is not None:
        attrs += ' data-significant="' + str(bool(significant)).lower() + '"'
    if winner:
        attrs += ' class="win"'
        if significant is None:
            label = '<span class="comparison-winner">★ ' + label + '</span>'
        else:
            marker = '<span class="comparison-winner">★ </span>' if significant else ''
            label = '<span class="comparison-extremum">' + marker + label + '</span>'
    return '<td' + attrs + '>' + label + '</td>'


def interpretation_summary_table(profile, selected, zh, content=None, relevance_scores=None):
    content = content or {}
    if content.get("independent_benchmarks"):
        return population_summary(profile, selected, content, zh, relevance_scores=relevance_scores)
    selected = scoped_overview_rows(profile, selected, content)
    profile = dict(profile, methods=scoped_overview_methods(profile, content), cases=[c for c in profile["cases"] if c["id"] in {r["case_id"] for r in selected}])
    stats = aggregate(selected)
    cutoff = profile["protocol"]["top_k"]
    hit_key = "anchor_hit_5" if cutoff == 5 else "anchor_hit_10"
    measures = []
    for method in profile["methods"]:
        item = stats.get(method["id"])
        anchor = item["metrics"][hit_key] if item else {"mean": None, "n": 0}
        measures.append((
            method,
            item["nonempty"] if item else None,
            anchor["mean"],
            anchor["n"],
            item["all_latency"]["p50"] if item else None,
            item["all_latency"]["p95"] if item else None,
            item["cost_total"] if item else None,
        ))
    directions = {1: "max", 2: "max", 4: "min", 5: "min", 6: "min" if len({m["currency"] for m in profile["methods"]}) == 1 else "neutral"}
    winners = numeric_winners(measures, directions)
    rows = []
    for method, nonempty, hit, n, p50, p95, cost in measures:
        values = [
            esc(method["label"]),
            f"{nonempty}/{len(profile['cases'])}" if nonempty is not None else "—",
            f"{hit * 100:.1f}% (n={n})" if hit is not None else "—",
            fmt(p50),
            fmt(p95),
            (f"{cost:.3f}" if cost is not None else "—") + " " + method["currency"],
        ]
        raw = [None, nonempty, hit, None, p50, p95, cost]
        cells_html = []
        for column, label in enumerate(values):
            source_index = (0, 1, 2, 4, 5, 6)[column]
            win = source_index in winners and raw[source_index] == winners[source_index]
            cells_html.append('<td>' + label + '</td>' if source_index == 0 else numeric_cell(label, raw[source_index], f"interpretation-{source_index}", directions.get(source_index, "neutral"), win))
        rows.append('<tr>' + ''.join(cells_html) + '</tr>')
    return (
        '<h2>' + report_text("Core API comparison", zh) + '</h2>'
        + table(
            [report_text("Provider", zh, context="api"), report_text("Nonempty queries", zh), report_text("Known page@{cutoff}", zh, cutoff=cutoff), 'P50 ms', 'P95 ms', report_text("Estimated cohort cost", zh)],
            rows,
        )
        + '<p>' + (
            report_text("★ marks a unique favorable value within its column. Populations differ across columns, so these are not an overall ranking. Charges use frozen list prices; unknown prices remain unavailable.", zh)
        ) + '</p>'
    )


def capability_map(profile, selected, zh):
    """Show actual delivered layers; a separate AI-answer endpoint is not implied."""
    heads = (
        [report_text("Tested endpoint", zh), report_text("URL & title", zh), report_text("Snippet", zh), report_text("Page summary", zh), report_text("Page body", zh), report_text("Answer", zh)]
    )
    output = ['<div class="capmap" role="table" aria-label="' +
              report_text("Observed response fields", zh) + '">']
    output.extend('<div class="head" role="columnheader">' + esc(h) + '</div>' for h in heads)
    for method in profile["methods"]:
        fields = [item for row in selected if row["method"] == method["id"] for item in row["results"]]
        example_request = next((row["request"] for row in selected if row["method"] == method["id"] and row.get("request")), {})
        request_mode = ", ".join(
            f"{key}={str(example_request[key]).lower()}"
            for key in ("limit", "count", "need_content", "summary")
            if key in example_request
        )
        delivered = [
            any(item.get("url") and item.get("title") for item in fields),
            any(item.get("snippet") for item in fields),
            any(item.get("summary") for item in fields),
            any(item.get("content") for item in fields),
            False,
        ]
        output.append('<div class="name" role="rowheader">' + esc(method["label"]) + '<br><small>' + esc(request_mode) + '</small></div>')
        for present in delivered:
            output.append('<div role="cell" class="' + ('yes' if present else 'no') + '">' +
                          (report_text("Yes", zh) if present else report_text("No", zh, context="not delivered")) + '</div>')
    output.append('</div><p class="muted">' + (
        report_text("Based on the listed configurations: Yes means the field was delivered at least once, not in every result. Other endpoints are documented in the bound interface research.", zh)
    ) + '</p>')
    return ''.join(output)


def interface_example(profile, rows, case_id, zh):
    """Show the same query's complete native requests and response links for every method."""
    case = next((c for c in profile["cases"] if c["id"] == case_id), None)
    if case is None:
        raise ValueError("Interface example query is missing")
    indexed = {r["method"]: r for r in rows if r["case_id"] == case_id}
    body = '<section data-interface-case="' + esc(case_id) + '"><h2>' + (
        report_text("One query: complete requests and responses", zh)
    ) + '</h2><p>' + esc(case["query"]) + '</p>'
    for method in profile["methods"]:
        if method["id"] not in indexed:
            raise ValueError("Interface example must include every method")
        row = indexed[method["id"]]
        body += '<article class="specimen" data-interface-method="' + esc(method["id"]) + '"><h3>' + esc(method["label"]) + '</h3>'
        body += '<p>' + report_text("{results} results returned", zh, results=len(row["results"])) + '</p>'
        body += '<details><summary>' + report_text("Complete request parameters", zh) + '</summary><pre>' + esc(json.dumps(row.get("request", {}), ensure_ascii=False, indent=2)) + '</pre></details>'
        body += '<details><summary>' + report_text("All search results", zh) + '</summary><pre>' + esc(json.dumps(row["results"], ensure_ascii=False, indent=2)) + '</pre></details>'
        body += '<button data-evidence="' + esc(row["evidence"]["path"]) + '">' + report_text("Open native response", zh) + '</button></article>'
    return body + '</section>'


def relevance_detail(result, zh):
    t = partial(report_text, zh=zh)
    rows = []
    for i in range(result.get("cutoff", 5)):
        grade = result["ranked_grades"][i] if i < len(result["ranked_grades"]) else 0
        label = str(grade) if i < len(result["ranked_grades"]) else t("Not returned")
        rows.append(cells([str(i + 1), label, str(2**grade-1), f'{1 / math.log2(i+2):.4f}', f'{(result["contributions"][i] if i < len(result["contributions"]) else 0):.4f}']))
    return ('<section data-case-relevance><h2>' + t("Query ranking quality") + '</h2><strong>' + f'{result["ndcg"]*100:.1f} / 100' + '</strong><p>'
            + t("Useful results: ") + f'{result["useful"]}/{result.get("cutoff", 5)} · ' + t("Grade-3 result: ") + (t("Yes") if result["success"] else t("No"))
            + '</p><details><summary>' + t("Inspect the complete calculation") + '</summary>'
            + table([t("Rank"), t("Grade"), t("Gain"), t("Rank weight"), t("Contribution")], rows)
            + '<p>' + t("Ideal pooled grades: ") + esc(str(result["ideal_grades"])) + '</p><p>'
            + (f'{result["dcg"]:.4f} ÷ {result["idcg"]:.4f} × 100 = {result["ndcg"]*100:.1f}' if result['idcg'] else t("No relevant candidate in the pool: score 0.")) + '</p></details></section>')


def catalog_prices(profile, content):
    prices = (content or {}).get("catalog_pricing")
    if prices is None:
        return None
    if set(prices) != {m["id"] for m in profile["methods"]}:
        raise ValueError("Catalog prices must cover every compared method")
    for item in prices.values():
        amount = item.get("amount")
        source = item.get('source', '')
        bound_quote = item.get('source_kind') == 'owner-quote' and any(
            asset.get('path') == source and asset.get('sha256') for asset in content.get('assets', []))
        if (item.get("currency") != "USD" or item.get("unit") not in ("1000-successful-searches", "1000-requests")
                or amount is not None and (type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0)
                or amount is not None and (not (source.startswith("https://") or bound_quote) or not item.get("checked_at"))):
            raise ValueError("Invalid verified catalog price")
    return prices


def catalog_price_panel(profile, content, zh):
    prices = catalog_prices(profile, content)
    rendered = []
    direction = 'min' if comparable_catalog_prices(prices) else 'neutral'
    winners = numeric_winners([[prices[m['id']]['amount']] for m in profile['methods']], {0: direction})
    for method in profile['methods']:
        item = prices[method['id']]
        amount = item['amount']
        price = '' if amount is None else '<a href="' + esc(item['source']) + '">' + fmt(amount) + '</a>'
        row = '<td>' + esc(method['label']) + '</td>' + numeric_cell(price + catalog_billing_basis(item, zh), amount, 'catalog-unit-price', direction, amount is not None and winners.get(0) == amount)
        row += cells([esc(item.get('plan', '')), esc(item.get('terms', {}).get('zh' if zh else 'en', '')), esc(item.get('checked_at', ''))])[4:-5]
        rendered.append('<tr>' + row + '</tr>')
    return '<section data-cost-budget data-catalog-prices><h2>' + report_text("Published prices and purchase terms", zh) + '</h2><p>' + report_text("Usage prices per 1,000 calls. Each method’s billing rules, subscription and prepayment terms are listed below. These are quoted rates, not invoice charges.", zh) + '</p>' + table([report_text("Method", zh), report_text("USD / 1,000 calls", zh), report_text("Plan", zh), report_text("Purchase terms", zh), report_text("Verified", zh)], rendered) + '</section>'


def comparable_catalog_prices(prices):
    return len({item['unit'] for item in prices.values()}) == 1


def catalog_billing_basis(item, zh):
    label = (report_text('Requests', zh) if item['unit'] == '1000-requests'
             else report_text('Successful searches', zh))
    return '<small class="billing-basis" data-billing-basis="' + esc(item['unit']) + '">' + label + '</small>'


def cost_panel(profile, rows, zh, content=None):
    """Expose frozen unit prices and the actual billable physical-call budget."""
    if catalog_prices(profile, content) is not None:
        return catalog_price_panel(profile, content, zh)
    records = []
    for method in profile["methods"]:
        group = [r for r in rows if r["method"] == method["id"]]
        attempts = [attempt for row in group for attempt in row.get("attempts", [row])]
        costs = [attempt.get("cost") for attempt in attempts]
        total = sum(costs) if costs and all(c is not None for c in costs) else None
        records.append((method, len(group), len(attempts), sum(c is not None and c > 0 for c in costs), total))
    comparable = len({m.get("currency") for m in profile["methods"]}) == 1
    winners = numeric_winners([[r[-1]] for r in records], {0: "min" if comparable else "neutral"})
    rendered = []
    rules = {"http-200-nonempty": report_text("HTTP 200 with nonempty results", zh), "http-200": "HTTP 200", "unknown": report_text("Not specified", zh)}
    for method, logical, physical, billed, total in records:
        currency = zh_text("CNY") if zh and method.get("currency") == "CNY" else method.get("currency", "")
        price = method.get("price_per_1000")
        label = report_text("Not priced", zh) if price is None else fmt(price) + " " + currency
        source = method.get("price_source", "")
        if source.startswith("https://"):
            label = '<a href="' + esc(source) + '">' + label + '</a>'
        rule = rules.get(method.get("billing"), method.get("billing", ""))
        row = cells([esc(method["label"]), label, esc(method.get("price_checked", "")), esc(rule), str(logical), str(physical), str(billed) if price is not None else report_text("Unknown", zh)])
        label = fmt(total) + " " + currency if total is not None else (report_text("Not priced", zh) if price is None else report_text("Partial charges known; some billing remains unknown", zh, value=sum(c for c in [a.get('cost') for r in rows if r['method'] == method['id'] for a in r.get('attempts', [r])] if c is not None), currency=currency))
        rendered.append(row[:-5] + numeric_cell(label, total, "cohort-cost", "min" if comparable else "neutral", 0 in winners and total == winners[0]) + '</tr>')
    return ('<section data-cost-budget><h2>' + report_text("Unit prices, billable calls and estimated charges", zh) + '</h2>'
            + table([report_text("Method", zh), report_text("Price /1,000", zh), report_text("Price checked", zh), report_text("Billing condition", zh), report_text("Search tasks", zh), report_text("Physical calls", zh), report_text("Known billable calls", zh), report_text("Estimated total", zh)], rendered)
            + '<p>' + report_text("Retries increase physical calls, not search tasks. Charges sum physical requests; unknown billing is not zero. Price links identify the frozen sources; provider invoices determine actual charges.", zh) + '</p></section>')


def relevance_panel(profile, scored, evidence, zh):
    """Explain and visualize judged preview relevance without conflating it with recall."""
    cutoff = profile.get("protocol", {}).get("top_k", evidence.get("plan", {}).get("cutoff", scored[0].get("cutoff", 5) if scored else 5))
    if not scored:
        return ""
    methods = [m for m in profile["methods"] if m["id"] in {r["method"] for r in scored}]
    rows = []
    families = [family for family in ("web", "site", "vertical") if any(row["family"] == family for row in scored)]
    for family in families:
        scoped = [row for row in scored if row["family"] == family]
        summaries = relevance_summary(scoped, [m["id"] for m in methods])
        for method in methods:
            stats = summaries[method["id"]]
            rows.append(cells([esc(search_category(family, zh)), esc(method["label"]),
                               str(stats["n"]), f'{stats["ndcg"]*100:.1f}', f'{stats["precision"]:.1%}', f'{stats["success"]:.1%}']))
    category_rows = []
    for family in families:
        scoped = [r for r in scored if r['family'] == family]
        summaries = relevance_summary(scoped, [m['id'] for m in methods])
        values = [summaries[m['id']]['ndcg'] * 100 for m in methods]
        best = max(values)
        row = '<td>' + esc(search_category(family, zh)) + '</td><td>' + str(summaries[methods[0]['id']]['n']) + '</td>'
        for value in values:
            row += numeric_cell(f'{value:.1f}', value, 'category-quality-' + family, 'max', values.count(best) == 1 and value == best)
        category_rows.append('<tr>' + row + '</tr>')
    bars = table(([report_text("Category", zh, context="question type"), report_text("Queries", zh)]) + [m['label'] for m in methods], category_rows)
    audit = evidence["judge"]
    model = audit["kind"] == "model"
    count = len(evidence["plan"]["case_ids"])
    title = report_text("In-house relevance: {count} queries", zh, count=count)
    definitions = (
        report_text("Ranking quality is nDCG@{cutoff} × 100. Useful-result fraction counts grades 2 and 3 in {cutoff} slots; direct-match rate counts queries with at least one grade-3 result.", zh, cutoff=cutoff)
    )
    scope = (
        report_text("Model judge: {name}. This run evaluates relevance visible in titles and snippets, without verifying full pages or factual accuracy. Frozen labels make metric recalculation deterministic; a new labeling run may differ.", zh, name=audit['name'])
    ) if model else (
        report_text("Human annotator: {name}. Metric calculation uses fixed labels without model calls.", zh, name=audit['name'])
    )
    denominator = report_text("Empty responses, missing slots and all-zero candidate pools remain in the denominator.", zh, cutoff=cutoff)
    panel = ('<section class="specimen" id="relevance-scores" data-relevance><h3>' + esc(title) + '</h3>' + relevance_scope_notice(profile, zh) + bars
             + ('' if profile.get("comparison_selection") else '<p>' + esc(definitions) + '</p>')
             + '<details><summary>' + report_text("Useful results and direct matches", zh) + '</summary>' + table([report_text("Category", zh, context="question type"), report_text("Provider", zh), report_text("Queries", zh), f"nDCG@{cutoff} × 100", f"Precision@{cutoff}", f"Success@{cutoff}"], rows) + '</details>'
             + '<p class="muted">' + esc(scope + denominator) + '</p><a href="relevance-judgments.json">'
             + report_text("Scoring evidence", zh) + '</a></section>')
    first_case = evidence["plan"]["case_ids"][0]
    query = next(c["query"] for c in profile["cases"] if c["id"] == first_case)
    panel += '<details class="score-example"><summary>' + report_text("A real query: inspect each rank and its score", zh) + '</summary><section data-score-example="' + esc(first_case) + '"><h3>' + report_text("Actual calculation", zh) + '</h3><p>' + esc(query) + '</p>'
    for method in methods:
        result = next(r for r in scored if r["case_id"] == first_case and r["method"] == method["id"])
        panel += '<details><summary>' + esc(method["label"]) + ' · ' + f'{result["ndcg"]*100:.1f}/100' + '</summary>' + relevance_detail(result, zh) + '</details>'
    panel += '</section></details>'
    return panel


def factual_panel(profile, selected, zh, *, compact=False):
    cutoff = profile["protocol"]["top_k"]
    excluded = set(profile.get('factual_quality_exclusions', []))
    cases = {c['id']: c for c in profile['cases'] if 'reference_answer' in c and c['id'] not in excluded}
    if not cases:
        return ''
    projected = bool(profile.get("comparison_selection") or profile.get("current_selection"))
    baseline = profile['methods'] if projected else [m for m in profile['methods'] if not any(m.get('output_options', {}).values())]
    answer_key = f'answer_in_preview_{cutoff}' if all(c.get('answer_evidence_scope') == 'preview' for c in cases.values()) else f'answer_in_delivered_text_{cutoff}' if projected else f'answer_in_preview_{cutoff}'
    keys = (f'labeled_source_hit_{cutoff}', f'labeled_source_rr_{cutoff}', answer_key)
    values = []
    by_method = {}
    for method in profile['methods']:
        group = {r['case_id']: r for r in selected if r['method'] == method['id'] and r['case_id'] in cases}
        by_method[method['id']] = group
        if method in baseline:
            values.append((method['label'], len(group), [sum(factual_evidence(cases[r['case_id']], r['results'], cutoff=cutoff)[k] for r in group.values()) for k in keys] + [sum(factual_sensitivity(cases[r['case_id']], r['results'], cutoff=cutoff)['metrics'][answer_key] for r in group.values())]))
    answer_description = (report_text("Check top-{cutoff} given URLs and answer text in titles and standard snippets, excluding dataset mirrors and evaluation logs.", zh, cutoff=cutoff)
                          if answer_key == f"answer_in_preview_{cutoff}" else report_text("Check top-{cutoff} given URLs and answer text in all delivered fields, excluding dataset mirrors and evaluation logs. Text presence does not verify its context.", zh, cutoff=cutoff))
    if compact:
        headers = [report_text("Method", zh), report_text("Reference Hit@{cutoff}", zh, cutoff=cutoff), report_text("Reference MRR@{cutoff}", zh, cutoff=cutoff), report_text("Answer Match@{cutoff} (text)", zh, cutoff=cutoff)]
        rendered = []
        best = numeric_winners([[v[2][i]/v[1] if v[1] else None for i in (0,1,3)] for v in values], {0: "max", 1: "max", 2: "max"})
        for label, n, sums in values:
            row = '<td>' + esc(label) + '</td>'
            for i, total in enumerate((sums[0], sums[1], sums[3])):
                display = (f'{total/n:.3f}' if i == 1 else f'{int(total)}/{n} · {total/n:.1%}') if n else '—'
                row += numeric_cell(display, total/n if n else None, f'factual-current-{i}', 'max', n > 0 and best.get(i) == total/n)
            rendered.append('<tr>' + row + '</tr>')
        return '<section data-factual><h2>' + report_text("Factual evidence retrieval: {cases} queries", zh, cases=len(cases)) + '</h2><p>' + answer_description + '</p>' + table(headers, rendered) + report_text('<p class="muted">Target MRR is available through the per-query reciprocal-rank details.</p>', zh) + ('<p data-quality-exclusions>' + report_text("Reference review excludes {excluded} defective queries for every provider; quality uses {cases} queries. Original responses, timing and cost retain the full collection.", zh, excluded=len(excluded), cases=len(cases)) + '</p>' if excluded else '') + '</section>'
    rendered = []
    for label, n, sums in values:
        row = ['<td>' + esc(label) + '</td>', f'<td>{n}</td>']
        for i, total in enumerate(sums):
            direction = "neutral" if i == 2 else "max"
            fractions = [[v[2][i] / v[1] if v[1] else None] for v in values]
            value_raw = total / n if n else None
            best = value_raw is not None and numeric_winners(fractions, {0: direction}).get(0) == value_raw
            value = (f'{total/n:.3f}' if i == 1 else f'{int(total)}/{n} · {total/n:.1%}') if n else '—'
            row.append(numeric_cell(value, value_raw, f"factual-{i}", direction, best))
        rendered.append('<tr>' + ''.join(row) + '</tr>')
    body = '<section data-factual><h3>' + (
        report_text("Public factual questions: sources and answer cues", zh) if projected else report_text("Public factual questions: basic-API evidence discovery", zh)
    ) + '</h3><p>' + (
        report_text("A source hit means at least one published supporting URL appears in the top {cutoff}. MRR averages reciprocal first-hit rank: 1 for rank one, 0.5 for rank two and zero for a miss. Answer cues use only shared titles and snippets. Failures, empties and short lists remain in the population.", zh, cutoff=cutoff)
    ) + '</p>' + table(
        [report_text("API", zh, context="base"), report_text("Queries", zh), report_text("Given webpage retrieved", zh), report_text("First-source reciprocal rank", zh), report_text("Answer text: raw", zh), report_text("Answer text: dataset mentions excluded", zh)], rendered)
    if projected:
        body = body.replace(zh_text("Answer cues use only shared titles and snippets."), zh_text("Answer cues use every delivered title, snippet, summary and body in the top {cutoff}.", cutoff=cutoff)).replace('Answer cues use only shared titles and snippets.', f'Answer cues use every delivered title, snippet, summary and body in the top {cutoff}.').replace(zh_text("API", context="base") + '</th>', zh_text("Method") + '</th>').replace('API</th>', 'Method</th>').replace(zh_text("Answer text: raw"), zh_text("Delivered answer cue: raw")).replace('Answer text: raw', 'Delivered answer cue: raw')
    body += '<p>' + report_text("Contamination exclusion is a post-hoc deterministic diagnostic. Results naming SimpleQA in their URL/title, or containing the complete question together with evaluation fields, validation logs or dataset-viewer structure, are blanked without refilling or compacting ranks. Original values remain visible.", zh) + '</p>'
    if excluded:
        body += '<p data-quality-exclusions>' + report_text("Independent reference review excluded {excluded} defective or materially ambiguous questions from all methods’ quality metrics, leaving {cases} common queries. All queries remain in latency and cost; original inputs, responses and diagnostics remain inspectable.", zh, excluded=len(excluded), cases=len(cases)) + '</p>'
    extra = []
    for enabled in ([] if projected else profile['methods']):
        options = enabled.get('output_options', {})
        if not any(options.values()):
            continue
        disabled = next((m for m in baseline if m['adapter'] == enabled['adapter'] and m.get('output_options') == {k: False for k in options}), None)
        if disabled is None:
            continue
        a, b = by_method[disabled['id']], by_method[enabled['id']]
        if set(a) != set(b):
            raise ValueError('Incomplete factual content pair')
        a = {cid: dict(r, metrics=factual_sensitivity(cases[cid], r['results'], cutoff=cutoff)['metrics']) for cid,r in a.items()}
        b = {cid: dict(r, metrics=factual_sensitivity(cases[cid], r['results'], cutoff=cutoff)['metrics']) for cid,r in b.items()}
        key = f'answer_in_delivered_text_{cutoff}'
        extra.append(cells([esc(enabled['label']), str(len(a)),
            str(sum(r['metrics'][key] for r in a.values())), str(sum(r['metrics'][key] for r in b.values())),
            str(sum(not a[c]['metrics'][key] and b[c]['metrics'][key] for c in a)),
            str(sum(a[c]['metrics'][key] and not b[c]['metrics'][key] for c in a))]))
    if extra:
        body += '<h3>' + report_text("Option experiment: answer cues in all delivered text", zh) + '</h3><p>' + (
            report_text("This includes returned summaries and bodies after the same explicit-mirror exclusion. Added means absent when disabled and present when enabled; lost is the reverse. Search results can change, so this is the overall option effect rather than a fixed-page body-only gain.", zh)
        ) + '</p>' + table([report_text("Enabled mode", zh), report_text("Pairs", zh), report_text("Disabled hits", zh), report_text("Enabled hits", zh), report_text("Added", zh), report_text("Lost", zh)], extra)
    if any(c.get('answer_match_rule') == answer_rules.POLICY_NAME for c in cases.values()):
        return body + '<p class="muted">' + (
            report_text("Answer checks use the published answer, explicit aliases and allowed numeric ranges, preserving units and percentage modifiers. Matches stay within one returned field; fields are never joined. Unspecified synonyms are not inferred. Negated or unrelated mentions can still match, so this is lexical evidence rather than semantic accuracy. Details expose the reference answer, sources and actual output.", zh)
        ) + '</p></section>'
    return body + '<p class="muted">' + (
        report_text("Answer matching normalizes Unicode, case and punctuation and requires consecutive whole tokens within one field. Negated or unrelated mentions can match; paraphrases can miss. These are source and lexical checks, not semantic accuracy. Every detail exposes the answer, sources and output.", zh)
    ) + '</p></section>'


def mode_panel(profile, selected, zh):
    methods = profile["methods"]
    pairs = []
    for enabled in methods:
        options = enabled.get("output_options", {})
        if not options or not any(options.values()):
            continue
        disabled = next((m for m in methods if m["adapter"] == enabled["adapter"]
                         and m.get("output_options") == {k: False for k in options}), None)
        if disabled:
            pairs.append((disabled, enabled))
    if not pairs:
        return ""
    groups = list(dict.fromkeys(r.get("cohort", "primary") for r in selected))
    output = []
    for group in groups:
        for disabled, enabled in pairs:
            a = {r["case_id"]: r for r in selected if r["method"] == disabled["id"] and r.get("cohort", "primary") == group}
            b = {r["case_id"]: r for r in selected if r["method"] == enabled["id"] and r.get("cohort", "primary") == group}
            if set(a) != set(b) or not a:
                raise ValueError("Incomplete paired output-mode comparison")
            deltas = [b[k]["elapsed_ms"] - a[k]["elapsed_ms"] for k in a]
            changes = sum([canonical_url(x["url"]) for x in a[k]["results"]] != [canonical_url(x["url"]) for x in b[k]["results"]] for k in a)
            output.append(cells([
                esc(cohort_label(profile, group, zh)), esc(enabled["label"]), str(len(a)),
                fmt(sum(deltas) / len(deltas)),
                fmt(median(deltas)),
                f"{sum(d > 0 for d in deltas)}/{len(deltas)}",
                f"{changes}/{len(a)}",
            ]))
    return '<section data-modes><h3>' + (
        report_text("What does enabling extra text change?", zh)
    ) + "</h3><p>" + (
        report_text("Paired difference = enabled minus disabled latency; positive means slower with extra text. Each mode runs once per query in rotating order. Index or cache changes can alter results, so URL/order changes are shown too.", zh)
    ) + "</p>" + table(
        [report_text("Cohort", zh), report_text("Enabled mode", zh), report_text("Pairs", zh), report_text("Mean Δ ms", zh), report_text("Median Δ ms", zh), report_text("Enabled slower", zh), report_text("URL/order changed", zh)],
        output,
    ) + "</section>"


def known_page_chart(profile, selected, zh):
    """A scoped discovery check, deliberately separate from relevance quality."""
    key = "anchor_hit_5" if profile["protocol"]["top_k"] == 5 else "anchor_hit_10"
    stats = aggregate(selected)
    output = ['<h3>' + report_text("Known-page discovery", zh) + '</h3>']
    cutoff = profile["protocol"]["top_k"]
    output.append('<p>' + (
        report_text("Known-page tasks test discovery: a target URL in the first {cutoff} results counts as a hit. Each row shows hits and eligible tasks; questions without a declared target are excluded. Public factual questions have separate source and answer checks.", zh, cutoff=cutoff)
    ) + '</p>')
    output.append('<div class="metric-bars" data-known-page-chart>')
    for method in profile["methods"]:
        metric = stats[method["id"]]["metrics"][key]
        n, rate = metric["n"], metric["mean"]
        if not n or rate is None:
            continue
        hits = round(rate * n)
        label = f"{hits}/{n} · {rate:.1%}"
        output.append('<div class="item"><strong>' + esc(method["label"]) +
                      '</strong><div class="track"><span class="fill" style="width:' +
                      f'{rate*100:.1f}' + '%"></span></div><small>' + label + '</small></div>')
    output.append('</div><p class="muted">' + (
        report_text("These are purposively selected tasks, not random user traffic. Missing the target does not make other results irrelevant; this is not an overall relevance ranking.", zh)
    ) + '</p>')
    return ''.join(output)


def outcome_table(profile, selected, zh, relevance_scores=None):
    cases = {c["id"]: c for c in profile["cases"]}
    names = {
        "relevance_hit": report_text("Useful result found", zh),
        "relevance_miss": report_text("No useful result found", zh),
        "quality_excluded": report_text("Reference excluded from quality", zh),
        "api_error": report_text("API / transport error", zh),
        "empty": report_text("Empty response", zh, context="outcome"),
        "domain_mismatch": report_text("Off-domain URL returned", zh),
        "target_miss": report_text("Known target absent", zh),
        "target_hit": report_text("Known target found", zh),
        "source_hit": report_text("Published supporting source found", zh),
        "answer_hit": report_text("Reference answer text found", zh),
        "answer_miss": report_text("Reference answer text absent", zh),
        "domain_hit": report_text("Requested site found", zh),
        "domain_miss": report_text("Requested site absent", zh),
        "source_miss": report_text("Published supporting source not found", zh),
        "probe": report_text("Unjudged query response", zh),
    }
    rows = []
    for m in profile["methods"]:
        group = [r for r in selected if r["method"] == m["id"]]
        scores = {(x["case_id"], x["method"]): x for x in (relevance_scores or [])}
        counts = Counter(classify(cases[r["case_id"]], r, cutoff=profile["protocol"]["top_k"], quality_excluded=r["case_id"] in profile.get("factual_quality_exclusions", []), relevance_grades=(scores.get((r["case_id"], r["method"])) or {}).get("ranked_grades"))[1] for r in group)
        for kind, n in counts.items():
            rows.append(cells([esc(m["label"]), names[kind], str(n), str(len(group))]))
    return (
        "<h2>"
        + report_text("Primary outcome category", zh)
        + "</h2>"
        + table(
            [report_text("Method", zh), report_text("Machine category", zh), report_text("Queries", zh, context="query count"), report_text("Method total", zh)],
            rows,
        )
        + "<p>"
        + (
            report_text("Each query receives one primary category: API error, empty list, off-domain result, then known-page discovery. A result that finds its target but leaks another domain is counted as off-domain here; the separate known-page metric still counts it as a hit. Missing the target does not make other pages irrelevant.", zh)
        )
        + "</p>"
    )


def domain_discovery_table(profile, selected, zh):
    cases = [c for c in profile["cases"] if c.get("domains")]
    if not cases or not selected:
        return ""
    lookup = {(r["case_id"], r["method"]): r for r in selected}
    entries = []
    for case in cases:
        values = [esc(case["query"])]
        for method in profile["methods"]:
            row = lookup[(case["id"], method["id"])]
            ranks = [
                i
                for i, result in enumerate(row["results"][:10], 1)
                if any(in_domain(result["url"], domain) for domain in case["domains"])
            ]
            label = f"{len(ranks)}/{len(row['results'][:10])}; " + (
                f"#{ranks[0]}" if ranks else report_text("absent", zh)
            )
            values.append(f'<a href="details/{row["id"]}.html">{label}</a>')
        entries.append(cells(values))
    return (
        "<h2>"
        + report_text("Did the requested platform appear?", zh)
        + "</h2>"
        + "<p>"
        + (
            report_text("First round: requested-domain links / returned links, followed by first rank. Host matching does not validate the topic, account, date or article body. 0/0 denotes an empty response.", zh)
        )
        + "</p>"
        + table([report_text("Query", zh)] + [m["label"] for m in profile["methods"]], entries)
    )


def performance_view(profile, diagnostics, zh, evidence_prefix=""):
    t = partial(report_text, zh=zh)

    methods = diagnostics["methods"]
    body = '<section data-timing-decomposition><h2>' + t("Client and server timing: {cases} queries", cases=len(profile['cases'])) + "</h2>"
    rows = []
    for m in profile["methods"]:
        s = methods.get(m["id"])
        if not s:
            continue
        rows.append(
            cells(
                [esc(m["label"])]
                + [
                    fmt(s[k]["mean"]) + f" (n={s[k]['n']})"
                    for k in (
                        "client_ms",
                        "server_ms",
                        "outside_server_ms",
                        "ttfb_ms",
                        "body_ms",
                        "bytes",
                    )
                ]
            )
        )
    body += table(
        [
            t("Method"),
            t("Mean total ms"),
            t("Mean server ms"),
            t("Mean remainder ms"),
            t("Mean TTFB ms"),
            t("Mean body transfer ms"),
            t("Mean response bytes"),
        ],
        rows,
    )
    body += (
        "<p>"
        + t("All logical requests with available telemetry are included; n states the available population. Server time is provider-reported and unavailable where no comparable field exists. The remainder includes network transit and client setup; it is not pure network latency. Requests that received no first byte contribute only to total latency, not to TTFB or body-transfer means. TTFB and body transfer are sequential; DNS/connect/TLS clocks are cumulative and must not be added.")
        + "</p>"
    )
    body += "</section>"
    body += (
        "<h2>"
        + t("Work performed across the full call budget")
        + "</h2>"
    )
    rows = []
    for m in profile["methods"]:
        s = methods.get(m["id"])
        if s:
            rows.append(
                cells(
                    [
                        esc(m["label"]),
                        fmt(s["attempts"]["mean"]),
                        esc(
                            ", ".join(f"{k}: {v}" for k, v in s["outcomes"].items())
                            or t("Not exposed")
                        ),
                        esc(", ".join(f"{k}: {v}" for k, v in s["cache"].items())),
                        esc(
                            ", ".join(f"{k}: {v}" for k, v in s["usage"].items())
                            or t("Not exposed")
                        ),
                    ]
                )
            )
    body += table(
        [
            t("Method"),
            t("Mean recorded attempts/call"),
            t("Attempt outcomes"),
            t("API cache states"),
            t("Reported usage units"),
        ],
        rows,
    )
    body += (
        "<p>"
        + t("Attempt durations overlap. Their sum is work, not user waiting time. Usage units have no monetary conversion until internal prices are supplied. Missing upstream telemetry does not mean zero upstream work.")
        + "</p>"
    )
    candidates = [c for c in diagnostics["calls"] if c["after_first_ms"] is not None]
    if candidates:
        # Fixed selection rule: largest observed post-first-result wait, not a quality winner.
        specimen = max(candidates, key=lambda c: c["after_first_ms"])
        body += (
            "<h2>"
            + t("Longest wait after the first upstream result")
            + "</h2>"
        )
        body += (
            f'<section class="specimen" data-timing-specimen="{esc(specimen["id"])}"><p>{esc(specimen["case_id"])} · {esc(specimen["method"])} · '
            + t("round")
            + f" {specimen['round'] + 1}</p>"
        )
        body += (
            "<p>"
            + t("First upstream results / server completion / additional wait: ")
            + f"{fmt(specimen['first_results_ms'])} / {fmt(specimen['server_ms'])} / {fmt(specimen['after_first_ms'])} ms</p>"
        )
        maximum = max([a["start"] + a["duration"] for a in specimen["timeline"]] + [1])
        rows = []
        for a in specimen["timeline"]:
            bar = f'<div class="timeline"><i class="{"good" if a["outcome"] == "results" else ""}" style="left:{100 * a["start"] / maximum:.2f}%;width:{100 * a["duration"] / maximum:.2f}%"></i></div>'
            rows.append(
                cells(
                    [
                        esc(a["route"]),
                        str(a["start"]),
                        str(a["duration"]),
                        esc(a["outcome"]),
                        str(a["count"]),
                        bar,
                    ]
                )
            )
        body += table(
            [
                t("Route"),
                t("Start ms"),
                t("Duration ms"),
                t("Outcome"),
                t("Parsed items"),
                t("Parallel timeline"),
            ],
            rows,
        )
        body += (
            f'<button data-evidence="{evidence_prefix}{esc(specimen["evidence_path"])}">'
            + t("Open exact response")
            + "</button></section>"
        )
        body += (
            "<p>"
            + t("A first upstream result may contain too few or off-topic items. The extra wait is an observed scheduling interval, not a proven removable speedup. Any early-stop policy must be tested against the same quality requirements.")
            + "</p>"
        )
    return body


def scoped_overview_methods(profile, content):
    ids = content.get("overview_methods")
    if ids is None:
        return profile["methods"]
    if not ids or len(ids) != len(set(ids)) or not set(ids).issubset({m["id"] for m in profile["methods"]}):
        raise ValueError("Invalid overview participants")
    return [m for m in profile["methods"] if m["id"] in ids]


def scoped_overview_rows(profile, rows, content):
    cohort = content.get("overview_cohort")
    if cohort and cohort not in {g["id"] for g in profile.get("report_cohorts", [])}:
        raise ValueError("Overview cohort is not registered")
    ids = {m["id"] for m in scoped_overview_methods(profile, content)}
    return [r for r in rows if r["method"] in ids and (not cohort or r.get("cohort") == cohort)]


def project_comparison(profile, rows, content):
    """Project an already-validated collection without changing measured calls."""
    ids = content.get("comparison_methods")
    if ids is None:
        return profile, rows
    methods = {m["id"]: m for m in profile["methods"]}
    if not ids or len(ids) != len(set(ids)) or not set(ids).issubset(methods):
        raise ValueError("Invalid comparison participants")
    selected = [r for r in rows if r["method"] in ids]
    if rows:
        expected = {(m, c["id"], round_number) for m in ids for c in profile["cases"]
                    for round_number in range(profile["protocol"]["rounds"])}
        actual = [(r["method"], r["case_id"], r["round"]) for r in selected]
        if set(actual) != expected or len(actual) != len(expected):
            raise ValueError("Incomplete comparison participant matrix")
    projected = copy.deepcopy(profile)
    if profile.get("execution_policy"):
        projected.setdefault("collection_methods", copy.deepcopy(profile["methods"]))
    projected["methods"] = [dict(methods[mid], label=content.get("comparison_labels", {}).get(mid, methods[mid]["label"])) for mid in ids]
    projected["comparison_selection"] = {"method_ids": ids, "collection_method_ids": list(methods)}
    return projected, selected


def executive_table(profile, selected, content, scores, zh):
    """Keep every benchmark's primary quality measures visible on the home page."""
    summary_content = dict(content)
    body = population_summary(profile, selected, summary_content, zh, relevance_scores=scores)
    return '<section data-executive>' + body + '</section>'


def discovery_comparison(profile, selected, content, zh):
    """Compare every provider on the same explicit page/site checks."""
    cutoff = profile["protocol"]["top_k"]
    rows = []
    for population in metric_populations(profile, content):
        kind = population.get('metric_kind', population['id'])
        if kind not in ('scenario', 'site'):
            continue
        ids = set(population['case_ids'])
        counts = []
        for method in profile['methods']:
            group = [r for r in selected if r['method'] == method['id'] and r['case_id'] in ids]
            if len(group) != len(ids):
                raise ValueError('Incomplete discovery comparison')
            counts.append(sum(bool(evaluate(next(c for c in profile['cases'] if c['id'] == r['case_id']), r['results'][:profile['protocol']['top_k']])['anchor_hit_10']) if kind == 'scenario' else (r['metrics'].get('domain_precision') or 0) > 0 for r in group))
        best = max(counts)
        label = report_text("Find the given webpage", zh) if kind == 'scenario' else report_text("Find the requested site", zh)
        cells_html = '<td>' + label + '</td><td>' + str(len(ids)) + '</td>'
        for count in counts:
            cells_html += numeric_cell(f'{count}/{len(ids)}', count, 'discovery-' + kind, 'max', counts.count(best) == 1 and count == best)
        rows.append('<tr>' + cells_html + '</tr>')
    if not rows:
        return ''
    return '<section data-discovery><h2>' + report_text("Directed webpage discovery", zh) + '</h2><p>' + report_text("For scenario queries with explicit webpage/site targets, check whether the top {cutoff} contain the given URL or domain. These diagnose discovery separately from graded relevance.", zh, cutoff=cutoff) + '</p>' + table(([report_text("Check", zh), report_text("Queries", zh)]) + [m['label'] for m in profile['methods']], rows) + '</section>'


def benchmark_operations(profile, selected, content, zh):
    """Collection labels, populations, retries and currencies come from the inputs."""
    populations = [p for p in metric_populations(profile, content) if p.get("benchmark_root")]
    if not populations:
        cohorts = sorted({r.get("cohort", "primary") for r in selected})
        populations = [{"id": key, "label": {"zh": cohort_label(profile, key, True), "en": cohort_label(profile, key, False)}, "case_ids": sorted({r["case_id"] for r in selected if r.get("cohort", "primary") == key})} for key in cohorts]
    rendered = []
    successful = []
    prices = catalog_prices(profile, content)
    comparable = comparable_catalog_prices(prices) if prices is not None else len({m.get("currency", "CNY") for m in profile["methods"]}) == 1
    for population in populations:
        ids = set(population['case_ids'])
        rows = [r for r in selected if r['case_id'] in ids]
        stats = aggregate(rows)
        values = [(m, stats[m['id']]) for m in profile['methods']]
        raw = [[item['all_latency'][k] for k in ('mean', 'p50', 'p95')] + [prices[m['id']]['amount'] if prices is not None else item['cost_total']] for m, item in values]
        directions = {0:'min',1:'min',2:'min',3:'min' if comparable else 'neutral'}
        winners = numeric_winners(raw, directions)
        conditional = [[item['success_latency'][k] for k in ('mean', 'p50', 'p95')] for _, item in values]
        conditional_winners = numeric_winners(conditional, {0: 'min', 1: 'min', 2: 'min'})
        for idx, (method, item) in enumerate(values):
            label = esc(population['label']['zh' if zh else 'en'])
            row = '<td>' + label + '</td><td>' + esc(method['label']) + '</td>'
            for i, value in enumerate(raw[idx]):
                display = (fmt(value) + (' ' + method.get('currency', 'CNY') if i == 3 else '')) if value is not None else (report_text("Not priced", zh) if method.get('price_per_1000') is None else report_text("Some billing unknown", zh))
                if prices is not None and i == 3:
                    display = (fmt(value) if value is not None else '') + catalog_billing_basis(prices[method['id']], zh)
                row += numeric_cell(display, value, f'operation-{population["id"]}-{i}', directions[i], value is not None and winners.get(i) == value)
            rendered.append('<tr>' + row + '</tr>')
            success_row = '<td>' + label + '</td><td>' + esc(method['label']) + '</td><td>' + str(item['success_latency']['n']) + '</td>'
            for i, value in enumerate(conditional[idx]):
                success_row += numeric_cell(fmt(value), value, f'success-latency-{population["id"]}-{i}', 'min', value is not None and conditional_winners.get(i) == value)
            successful.append('<tr>' + success_row + '</tr>')
    cost_heading = report_text("USD / 1,000 calls", zh) if prices is not None else report_text("Estimated batch cost", zh)
    retried = any(len(row.get("attempts") or [row]) > 1 for row in selected)
    timing_note = report_text('One final result per query. Timing includes retries, the complete collection and reference-quarantined queries. P95 bounds 95% of request waits. The table states each cost unit; purchase or billing terms appear below.', zh) if retried else report_text('One final result per query. Timing covers successful, empty and failed requests, including reference-quarantined queries; this collection used no retries. P95 bounds 95% of request waits. The table states each cost unit; purchase or billing terms appear below.', zh)
    return '<section data-benchmark-operations><h2>' + report_text("Performance and cost by collection", zh) + '</h2><p>' + timing_note + '</p>' + table(([report_text("Collection", zh), report_text("API", zh), report_text("Mean ms", zh, context="duration"), "P50 ms", "P95 ms", cost_heading]), rendered) + '</section><section data-successful-latency><h2>' + report_text("Latency of successful result deliveries", zh) + '</h2><p>' + report_text("Only HTTP 200 calls without errors and with at least one result. Counts differ by provider; this conditional comparison does not replace all-call latency.", zh) + '</p>' + table([report_text("Population", zh), report_text("API", zh), report_text("Calls", zh), report_text("Mean ms", zh, context="duration"), 'P50 ms', 'P95 ms'], successful) + '</section>'


def quality_dataset(profile, content):
    """Active dataset excludes rejected references; archived calls stay in results."""
    excluded = set(profile.get("factual_quality_exclusions", []))
    if not excluded:
        return profile, content
    active = copy.deepcopy(profile)
    active["cases"] = [case for case in active["cases"] if case["id"] not in excluded]
    for cohort in active.get("report_cohorts", []):
        cohort["case_ids"] = [cid for cid in cohort["case_ids"] if cid not in excluded]
    authored = copy.deepcopy(content)
    for population in authored.get("metric_populations", []):
        population["case_ids"] = [cid for cid in population["case_ids"] if cid not in excluded]
    return active, authored


def metric_populations(profile, content):
    cases = {c["id"] for c in profile["cases"]}
    populations = content.get("metric_populations") or [
        {"id": "overall", "label": {"en": "All questions", "zh": zh_text("All questions")}, "case_ids": [c["id"] for c in profile["cases"]]}
    ]
    seen = set()
    for population in populations:
        ids = population.get("case_ids", [])
        if (not population.get("id") or population["id"] in seen or not ids
                or len(ids) != len(set(ids)) or not set(ids).issubset(cases)):
            raise ValueError("Invalid metric population")
        seen.add(population["id"])
        if population["id"] == "overall" and set(ids) != cases:
            raise ValueError("Overall population must include every query")
        if population["id"] == "factual" and set(ids) != {c["id"] for c in profile["cases"] if "reference_answer" in c}:
            raise ValueError("Factual population must include every labeled question")
    if content.get("independent_benchmarks"):
        roots = [set(p["case_ids"]) for p in populations if p.get("benchmark_root")]
        if not roots or set().union(*roots) != cases or sum(map(len, roots)) != len(cases):
            raise ValueError("Independent benchmark roots must partition every query")
    elif populations[0]["id"] != "overall":
        raise ValueError("Overall population must be first")
    return populations


def population_summary(profile, selected, content, zh, *, diagnostics=False, relevance_scores=None):
    cutoff = profile["protocol"]["top_k"]
    lang = "zh" if zh else "en"
    independent = content.get("independent_benchmarks")
    prices = catalog_prices(profile, content)
    roots = [p for p in metric_populations(profile, content) if p.get("benchmark_root")] if independent else []
    heading = report_text("Core results by benchmark", zh)
    scope = report_text("; ", zh).join(p["label"][lang] + " " + str(len(set(p["case_ids"]) - set(profile.get("factual_quality_exclusions", [])))) + report_text(" queries", zh) for p in roots)
    note = ((scope + report_text(". Benchmarks are reported separately. ", zh)) if scope else "") + (
        report_text("Mean, P50 and P95 cover all requests and retry waits; fees estimate the calls in each population.", zh))
    if prices is not None:
        retried = any(len(row.get('attempts') or [row]) > 1 for row in selected)
        note = scope + (report_text(". Latency includes every call and retry wait. Collection windows differ; recorded times are observational. Prices and billing terms appear on the performance page.", zh) if retried else report_text(". Latency includes successful, empty and failed requests; this collection used no retries. Prices and billing terms appear on the performance page.", zh))
    if diagnostics:
        heading = report_text("Reference URLs and requested sites: diagnostics", zh)
        note = report_text("These overlapping subsets diagnose URL and domain discovery. Uniform relevance scoring covers the complete in-house set.", zh)
    body = '<section data-populations><h2>' + heading + '</h2><p class="population-note">' + note + '</p>' + relevance_scope_notice(profile, zh)
    for population in metric_populations(profile, content):
        if independent and bool(population.get("benchmark_root")) == diagnostics:
            continue
        ids = set(population['case_ids'])
        quality_ids = ids - set(profile.get('factual_quality_exclusions', []))
        subset = [r for r in selected if r['case_id'] in ids]
        stats = aggregate(subset)
        kind = population.get('metric_kind', population['id'])
        ranked = bool(relevance_scores) and ids.issubset({r['case_id'] for r in relevance_scores}) and not diagnostics
        if kind == 'relevance' and not ranked:
            raise ValueError('Relevance comparison requires every method and query')
        if ranked:
            expected = {(cid, method['id']) for cid in ids for method in profile['methods']}
            actual = [(r['case_id'], r['method']) for r in relevance_scores if r['case_id'] in ids]
            if len(actual) != len(expected) or set(actual) != expected:
                raise ValueError('Relevance comparison requires every method and query')
        quality = ranked or kind in ('factual', 'scenario', 'site')
        quality_stats = relevance_summary([r for r in (relevance_scores or []) if r['case_id'] in ids], [m['id'] for m in profile['methods']]) if ranked else {}
        cases = {c['id']: c for c in profile['cases']}
        values = []
        for method in profile['methods']:
            s = stats.get(method['id'])
            if not s or s['n'] != len(ids):
                raise ValueError('Incomplete metric population')
            group = [r for r in subset if r['method'] == method['id']]
            if ranked:
                q = quality_stats.get(method['id'], {"n": 0})
                measures = [q['ndcg'] * 100, q['precision'], q['success']] if q['n'] else [None, None, None]
            elif kind == 'factual':
                quality_group = [r for r in group if r['case_id'] in quality_ids]
                measures = [sum(factual_evidence(cases[r['case_id']], r['results'], cutoff=cutoff)[f'labeled_source_hit_{cutoff}'] for r in quality_group), sum(factual_sensitivity(cases[r['case_id']], r['results'], cutoff=cutoff)['metrics'][answer_evidence_key(cases[r['case_id']], cutoff)] for r in quality_group)]
                if content.get("summary_metrics") == "retrieval":
                    measures.append(sum(factual_evidence(cases[r['case_id']], r['results'], cutoff=cutoff)[f'labeled_source_rr_{cutoff}'] for r in quality_group) / len(quality_group) if quality_group else None)
            elif kind == 'scenario':
                hit_key = 'anchor_hit_5' if profile['protocol']['top_k'] == 5 else 'anchor_hit_10'
                metrics = [evaluate(cases[r['case_id']], r['results'], cutoff=cutoff) for r in group]
                if any(m.get(hit_key) is None or m.get('reciprocal_rank') is None for m in metrics):
                    raise ValueError('Scenario population requires a reference for every query')
                measures = [sum(m[hit_key] for m in metrics), sum(m['reciprocal_rank'] for m in metrics) / len(ids)]
            elif kind == 'site':
                eligible = []
                for row in group:
                    value = row['metrics'].get('domain_precision')
                    if value is None and row['metrics'].get('result_count') == 0:
                        value = 0
                    if value is None:
                        raise ValueError('Site population has an invalid domain metric')
                    eligible.append(value)
                measures = [sum(value > 0 for value in eligible), sum(eligible) / len(eligible)]
            else:
                measures = [s['nonempty']]
            values.append([method['label'], *measures, s['all_latency']['mean'], s['all_latency']['p50'], s['all_latency']['p95'], prices[method['id']]['amount'] if prices is not None else s['cost_total']])
        max_column = 3 if ranked or kind == "factual" and content.get("summary_metrics") == "retrieval" else 2 if quality else 1
        directions = {i: "max" if i <= max_column else "min" for i in range(1, len(values[0]))}
        if (prices is not None and not comparable_catalog_prices(prices)
                or prices is None and len({m.get("currency", "CNY") for m in profile["methods"]}) != 1):
            directions[len(values[0]) - 1] = "neutral"
        winners = numeric_winners(values, directions)
        intervals = None
        if ranked and content.get('uncertainty') == 'paired-query-bootstrap/1':
            intervals = paired_bootstrap([r for r in relevance_scores if r['case_id'] in ids],
                                         [m['id'] for m in profile['methods']])
            if any(value['difference_to_best'][1] >= 0 for mid, value in intervals['methods'].items()
                   if mid != intervals['best_observed']):
                winners.pop(1, None)
                directions[1] = 'neutral'
        rendered = []
        for row, method in zip(values, profile['methods'], strict=True):
            html_cells = []
            for i, value in enumerate(row):
                if i == 0:
                    label = esc(value)
                elif value is None:
                    label = '—'
                elif ranked and i == 1:
                    label = f'{value:.1f}'
                    if intervals:
                        low, high = intervals['methods'][method['id']]['interval']
                        label += f'<br><small>95% CI [{low * 100:.1f}, {high * 100:.1f}]</small>'
                elif ranked and i in (2, 3):
                    label = f'{value:.1%}'
                elif i == 1 or (kind == 'factual' and i == 2):
                    denominator = len(quality_ids) if kind == 'factual' else len(ids)
                    label = (f'{int(value)}/{denominator}' + (f' · {value / denominator:.1%}' if quality else '')) if denominator else '—'
                elif kind == 'scenario' and i == 2 or kind == 'factual' and content.get('summary_metrics') == 'retrieval' and i == 3:
                    label = f'{value:.3f}' if value is not None else '—'
                else:
                    label = fmt(value)
                if i == len(row) - 1:
                    currency = method.get('currency', 'CNY')
                    label = ((report_text("Not priced", zh) if method.get('price_per_1000') is None else report_text("Some calls have unknown billing", zh)) if value is None else label + ' ' + (zh_text("CNY") if zh and currency == 'CNY' else esc(currency)))
                    if prices is not None:
                        label = (fmt(value) if value is not None else '') + catalog_billing_basis(prices[method['id']], zh)
                win = i in winners and winners[i] == value
                html_cells.append('<td>' + label + '</td>' if i == 0 else numeric_cell(label, value, f"population-{i}", directions.get(i, "neutral"), win))
            rendered.append('<tr>' + ''.join(html_cells) + '</tr>')
        body += '<article data-population="' + esc(population['id']) + '"><h3>' + esc(population['label'][lang]) + f' · {len(quality_ids) if kind == "factual" else len(ids)}' + report_text(" queries", zh) + '</h3>'
        if population.get('description'):
            body += '<p>' + esc(population['description'][lang]) + '</p>'
        headings = [report_text("Queries returning results", zh)]
        if ranked:
            headings = [f'nDCG@{cutoff} × 100', f'Precision@{cutoff}', f'Success@{cutoff}']
            body += '<details class="metric-guide"><summary>' + report_text("Metric definitions", zh) + '</summary><p>' + report_text("nDCG@{cutoff} measures graded ranking quality, displayed ×100. Precision@{cutoff} is the share of {cutoff} slots graded ≥2, with missing slots scoring zero. Success@{cutoff} is the share of queries with a grade-3 result. Every query remains in the denominator.", zh, cutoff=cutoff) + ' <a href="quality.html#relevance-scores">' + report_text("Inspect grades and calculation", zh) + '</a></p></details>'
        elif kind == 'factual':
            if quality_ids != ids:
                body += '<p data-quality-exclusions>' + (report_text("Quality uses {qualified} qualified queries; latency retains all {total} collected queries.", zh, qualified=len(quality_ids), total=len(ids)) if prices is not None else report_text("Quality uses {qualified} reviewed-reference queries for every method; {defective} defective references are quarantined. Latency and cost retain all {total} queries.", zh, qualified=len(quality_ids), defective=len(ids) - len(quality_ids), total=len(ids))) + '</p>'
            body += '<details class="metric-guide"><summary>' + report_text("Metric definitions", zh) + '</summary><p>' + report_text("Hit@{cutoff} checks for a published supporting URL in the first {cutoff} results. MRR@{cutoff} averages reciprocal first-hit rank, with misses scoring zero. Answer Match@{cutoff} checks delivered text for reference-answer phrases after excluding explicit dataset mirrors; it does not measure semantic accuracy.", zh, cutoff=cutoff) + '</p></details>'
            headings = [report_text("Reference Hit@{cutoff}", zh, cutoff=cutoff), report_text("Answer Match@{cutoff} (text)", zh, cutoff=cutoff)]
            if content.get('summary_metrics') == 'retrieval':
                headings.append(report_text("Reference MRR@{cutoff}", zh, cutoff=cutoff))
        elif kind == 'scenario':
            body += '<p class="population-note">' + report_text("Target found: a reference page appears in the top {cutoff}. MRR averages reciprocal first-target rank: 1 for first, 0.5 for second, and 0 for a miss. Higher means targets appear earlier.", zh, cutoff=cutoff) + '</p>'
            headings = [report_text("Reference target found", zh, context="target page"), report_text("Target MRR", zh)]
        elif kind == 'site':
            body += '<p class="population-note">' + report_text("Site satisfied: at least one top-{cutoff} result uses the requested domain. Domain fraction averages the share of top-{cutoff} results from that domain. Neither metric judges page relevance.", zh, cutoff=cutoff) + '</p>'
            headings = [report_text("Site constraint met", zh), report_text("Requested-domain fraction", zh)]
        headers = [report_text("Method", zh)] + headings + [report_text("Mean ms", zh, context="duration"), report_text("P50 ms", zh), report_text("P95 ms", zh), report_text("Population cost", zh)]
        if prices is not None:
            headers[-1] = report_text("USD / 1,000 calls", zh)
        primary_column = 1 if ranked else 3 if kind == 'factual' and content.get('summary_metrics') == 'retrieval' else None
        if primary_column is not None:
            body += '<p class="primary-guide" data-primary-guide>' + report_text("Primary metric: {metric}. Higher is better; the initial order is descending.", zh, metric=headers[primary_column]) + '</p>'
            rendered = [html for _, html in sorted(zip(values, rendered, strict=True), key=lambda pair: (pair[0][primary_column] is None, -(pair[0][primary_column] or 0)))]
        body += table(headers, rendered, primary_column=primary_column)
        if ranked:
            body += '<details><summary>' + report_text("API delivery", zh) + '</summary><p>' + ' · '.join(esc(m['label']) + ' ' + str(stats[m['id']]['nonempty']) + '/' + str(len(ids)) for m in profile['methods']) + report_text(" queries returned results.", zh) + '</p></details>'
        body += '</article>'
    return body + '</section>'


def render_result_comparison(profile, selected, zh, *, prefix="", site_prefix="", content=None, relevance=None):
    profile = dict(profile, comparison_populations=metric_populations(profile, content or {}))
    return render_comparison(profile, selected, zh, relevance=relevance, primary_factual_metric="source" if (content or {}).get("summary_metrics") == "retrieval" else "answer", detail_link=lambda row: posixpath.join(site_prefix, prefix, "details", row["id"] + ".html"))


def population_filter(profile, content, zh):
    if content.get("presentation") != "comparison-report":
        return ""
    groups = [p for p in metric_populations(profile, content) if p["id"] != "overall" and not p.get("benchmark_root")]
    if not groups:
        return ""
    return '<label>' + report_text("Diagnostic subset", zh) + '<select data-population-filter><option value="all">' + report_text("All", zh) + '</option>' + ''.join('<option value="' + esc(p["id"]) + '" data-case-ids="' + esc(json.dumps(p["case_ids"])) + '">' + esc(p["label"]["zh" if zh else "en"]) + f' ({len(p["case_ids"])})</option>' for p in groups) + '</select></label>'


def annotation_detail(annotation, zh):
    text = report_text("This reference was added independently from the original query after collection. Search APIs were not called again.", zh)
    body = '<section class="specimen"><h3>' + report_text("Additional reference evidence", zh) + '</h3><p>' + text + '</p>'
    for source in annotation['sources']:
        body += '<p><a href="' + esc(source['url']) + '">' + esc(source['title']) + '</a> · ' + esc(source['verified_at']) + '</p><blockquote>' + esc(source['excerpt']) + '</blockquote>'
    return body + '</section>'


def dataset_context(profile, content, zh):
    if content.get("presentation") != "comparison-report":
        return ''
    populations = [p for p in metric_populations(profile, content) if p.get("benchmark_root")]
    if not populations:
        populations = [{"label": {"zh": profile.get("title", zh_text("Benchmark")), "en": profile.get("title", "Benchmark")}, "case_ids": [c["id"] for c in profile["cases"]]}]
    rows = []
    for population in populations:
        cases = [c for c in profile["cases"] if c["id"] in population["case_ids"]]
        if not cases:
            continue
        factual = all("reference_answer" in c for c in cases)
        purpose = report_text("Given-source and reference-answer text checks", zh) if factual else report_text("Query-result relevance", zh)
        rows.append(cells([esc(population.get("label", {"zh": population.get("id", zh_text("Benchmark")), "en": population.get("id", "Benchmark")})["zh" if zh else "en"]), str(len(cases)), purpose]))
    exact = sum(c.get("provenance_kind") == "community_exact" for c in profile["cases"])
    screenshots = sum(c.get("provenance_kind") == "user_screenshot" for c in profile["cases"])
    parts = []
    if exact:
        parts.append(report_text("{exact} original queries have traceable public sources", zh, exact=exact))
    if screenshots:
        parts.append(report_text("{screenshots} queries were transcribed from user screenshots; their original authors and posts are unconfirmed", zh, screenshots=screenshots))
    note = report_text("; ", zh).join(parts)
    if note:
        note += report_text(". ", zh)
    note += report_text("Original queries, source URLs and derivations are listed below.", zh)
    return '<section><h2>' + report_text("Datasets and evaluation scope", zh) + '</h2>' + table([report_text("Dataset", zh), report_text("Queries", zh), report_text("Purpose", zh)], rows) + '<p>' + note + '</p></section>'
