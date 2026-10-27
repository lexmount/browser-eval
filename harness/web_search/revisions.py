"""Re-evaluate saved evidence with explicit external correction authority."""

import json
from copy import deepcopy
from pathlib import Path

from lexbench_websearch.answer_rules import POLICY_NAME, RULE_VERSION, reference_rule
from lexbench_websearch.contracts import validate_cases
from lexbench_websearch.inputs import digest
from lexbench_websearch.qualification import _equivalent, _qualified, validate_regressions
from lexbench_websearch.scoring import evaluate


def _rescore(profile, rows):
    cases = {case["id"]: case for case in profile["cases"]}
    updated, changes = [], []
    for row in rows:
        new = {**row, "metrics": evaluate(cases[row["case_id"]], row["results"])}
        if new["metrics"] != row["metrics"]:
            changes.append({"id": row["id"], "before": row["metrics"], "after": new["metrics"]})
        updated.append(new)
    return profile, updated, changes


NATIVE_CONTROLS = Path(__file__).with_name("data") / "native-normalization-regressions.json"

def normalization_regressions(expected_sha256, normalizer):
    """Exercise frozen projection controls through the execution owner's adapter."""
    path = NATIVE_CONTROLS
    if digest(path.read_bytes()) != expected_sha256:
        raise ValueError("Normalization regression binding mismatch")
    if not callable(normalizer):
        raise ValueError("Native normalization requires the execution adapter")
    for group in json.loads(path.read_bytes())["regressions"]:
        for control in group["controls"]:
            try:
                results = normalizer(
                    control["payload"],
                    control["method"],
                    control["protocol"],
                    control["request"],
                )
            except ValueError:
                if control["error"]:
                    continue
                raise
            if (
                control["error"]
                or [r["url"] for r in results] != control["expected_urls"]
            ):
                raise ValueError("Normalization regression failed: " + control["id"])
            metrics = evaluate(control["case"], results)
            if any(metrics[k] != v for k, v in control["expected_metrics"].items()):
                raise ValueError("Task metric regression failed: " + control["id"])


def validate_revision_controls(revision, normalizer=None):
    if not revision.get("normalization"):
        validate_regressions(revision)
        return
    native = {"relative-serp-links", "unsafe-serp-links", "top-five-boundary"}
    ids = revision.get("regression_ids", [])
    if (
        revision["normalization"] != "request-url/1"
        or not native.issubset(ids)
        or len(ids) != len(set(ids))
        or not revision.get("root_cause")
    ):
        raise ValueError(
            "Native normalization revision needs paired URL and cutoff controls"
        )
    shared = [i for i in ids if i not in native]
    if shared:
        validate_regressions(dict(revision, regression_ids=shared))
    normalization_regressions(revision.get("regressions_sha256"), normalizer)


def apply_report_revision(profile, rows, revision, *, artifacts=None, normalizer=None):
    """Add independently observed reference annotations to immutable saved calls."""
    if revision.get("schema") != "web-search-assessment-revision/1":
        raise ValueError("Unsupported assessment revision")
    validate_revision_controls(revision, normalizer)
    if revision.get("provider_calls") != 0:
        raise ValueError("An annotation revision cannot introduce provider calls")
    revised = deepcopy(profile)
    normalization = revision.get("normalization")
    if normalization:
        revised["protocol"]["result_url_resolution"] = normalization
    cases = {c["id"]: c for c in revised["cases"]}
    seen = set()
    for item in revision.get("cases", []):
        cid = item["case_id"]
        if cid in seen or cid not in cases:
            raise ValueError("Duplicate or unknown annotated case")
        seen.add(cid)
        if cases[cid].get("anchors"):
            raise ValueError("Additional annotations cannot replace existing anchors")
        anchors = item.get("anchors", [])
        if not _qualified(item):
            raise ValueError("Unqualified source anchor")
        cases[cid]["anchors"] = anchors
        cases[cid]["reference_annotation"] = item
        if revised.get('protocol', {}).get('reference_scope') == 'identity':
            checks = revised.setdefault('reference_checks', {})
            for anchor in anchors:
                source = next(s for s in item['sources'] if anchor in (s['url'], s.get('final_url'), s.get('qualifying_link')))
                checks[anchor] = {'url': source['url'], 'contains': [source['title']]}
    if revision.get("answer_match_rule"):
        if revision["answer_match_rule"] != POLICY_NAME:
            raise ValueError("Unsupported answer matching rule")
        for case in cases.values():
            if case.get("reference_answer"):
                case["answer_match_rule"] = revision["answer_match_rule"]

    if revision.get("answer_annotation_rule") not in (None, RULE_VERSION):
        raise ValueError("Answer derivation revision uses an unsupported annotation rule")
    derivations = revision.get("answer_derivations", [])
    if derivations:

        if (revision.get("answer_annotation_rule") != RULE_VERSION
                or not {"published-answer-citation-metadata", "literal-bracketed-answer"}.issubset(revision["regression_ids"])):
            raise ValueError("Reference citation cleanup requires a bound scoring revision")
        seen = set()
        for item in derivations:
            case = cases.get(item["case_id"], {})
            metadata = {k: v for k, v in item.items() if k not in {"case_id", "query"}}
            if (item["case_id"] in seen or item.get("query") != case.get("query")
                    or item.get("reference") != case.get("reference_answer")
                    or case.get("answer_derivation", metadata) != metadata):
                raise ValueError("Answer derivation does not bind the unchanged case")
            reference_rule(case["reference_answer"], derivation=metadata)
            seen.add(item["case_id"])
            case["answer_derivation"] = deepcopy(metadata)
    exclusions = set(revised.get("factual_quality_exclusions", []))
    decisions = {}
    incoming = revision.get("reference_adjudications", [])
    for batch in (revised.get("reference_adjudications", []), incoming):
        seen = set()
        for item in batch:
            cid = item["case_id"]
            if cid in seen or cid in decisions and decisions[cid] != item:
                raise ValueError("Duplicate or conflicting reference adjudication")
            seen.add(cid)
            decisions[cid] = item
    for item in decisions.values():
        cid = item["case_id"]
        case = cases.get(cid, {})
        if (
            "reference_answer" not in case
            or item.get("query") != case.get("query")
            or item.get("reference_answer") != case["reference_answer"]
            or item.get("quality_excluded") is not True
            or not item.get("reason")
            or not item.get("evidence")
        ):
            raise ValueError(
                "Reference adjudication does not bind the unchanged factual case"
            )
        exclusions.add(cid)
    if exclusions:
        revised["factual_quality_exclusions"] = sorted(exclusions)
    if decisions:
        revised["reference_adjudications"] = [
            decisions[cid] for cid in sorted(decisions)
        ]
    validate_cases(revised["cases"], reference_checks=revised.get("reference_checks"), reference_scope=revised.get("protocol", {}).get("reference_scope"))
    updated, changes = [], []
    for row in rows:
        new = dict(row)
        if (
            normalization
            and row["http_status"] == 200
            and not row.get("transport_exit")
        ):
            path = row["evidence"]["path"]
            if (
                artifacts is None
                or path not in artifacts
                or digest(artifacts[path]) != row["evidence"]["sha256"]
            ):
                raise ValueError("Native artifact binding mismatch")
            try:
                method = next(m for m in revised["methods"] if m["id"] == row["method"])
                new["results"] = normalizer(
                    json.loads(artifacts[path]),
                    method,
                    revised["protocol"],
                    row.get("request"),
                )
                new["error"] = ""
            except (ValueError, TypeError, AttributeError, KeyError):
                # Preserve the original failed response and its unchanged evidence.
                new = dict(row)
        new["metrics"] = evaluate(cases[row["case_id"]], new["results"])
        if (
            new["metrics"] != row["metrics"]
            or new["results"] != row["results"]
            or new.get("error") != row.get("error")
        ):
            change = {
                "id": row["id"],
                "before": row["metrics"],
                "after": new["metrics"],
            }
            if normalization:
                change["normalization"] = {
                    "before_sha256": digest(
                        json.dumps(
                            row["results"], sort_keys=True, ensure_ascii=False
                        ).encode()
                    ),
                    "after_sha256": digest(
                        json.dumps(
                            new["results"], sort_keys=True, ensure_ascii=False
                        ).encode()
                    ),
                    "before_count": len(row["results"]),
                    "after_count": len(new["results"]),
                    "before_error": row.get("error"),
                    "after_error": new.get("error"),
                }
            changes.append(change)
        updated.append(new)
    return revised, updated, changes


def collected_projection(profile, rows, artifacts, normalizer):
    """Reconstruct collection projections through the frozen execution adapter."""
    cases = {c["id"]: c for c in profile["cases"]}
    methods = {m["id"]: m for m in profile["methods"]}
    restored = []
    for row in rows:
        original = dict(row, results=[])
        if row["http_status"] == 200 and not row.get("transport_exit"):
            path = row["evidence"]["path"]
            if (
                path not in artifacts
                or digest(artifacts[path]) != row["evidence"]["sha256"]
            ):
                raise ValueError("Native artifact binding mismatch")
            try:
                original["results"] = normalizer(
                    json.loads(artifacts[path]),
                    methods[row["method"]],
                    profile["protocol"],
                    row.get("request"),
                )
                original["error"] = ""
            except (ValueError, TypeError, AttributeError, KeyError) as exc:
                original["error"] = str(exc)
        original["metrics"] = evaluate(cases[row["case_id"]], original["results"])
        restored.append(original)
    return restored


def apply_identity_correction(profile, rows, correction):
    validate_regressions(correction)
    observations = {x["url"]: x for x in correction["observations"]}
    revised = deepcopy(profile)
    cases = {c["id"]: c for c in revised["cases"]}
    for cid, aliases in correction["aliases"].items():
        case = cases[cid]
        for alias, target in aliases.items():
            a, b = observations[alias], observations[target]
            if not _equivalent(a, b):
                raise ValueError("Unproven equivalent page")
            if target not in case.get("anchors", []):
                raise ValueError("Correction must retain the original known target")
        case["identity_aliases"] = dict(aliases)
    validate_cases(
        revised["cases"],
        reference_checks=revised.get("reference_checks"),
        reference_scope=revised.get("protocol", {}).get("reference_scope"),
    )
    return _rescore(revised, rows)

def validate_revision_binding(revision, *, authority=None, normalizer=None):
    registered = (authority or {}).get("assessment_revisions", {})
    if registered.get("schema") != "search-assessment-authority/1":
        raise ValueError("Assessment revision requires an explicit external scoring authority")
    identity = digest(json.dumps(revision, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    records = [p for p in registered.get("provenance", []) if p.get("sha256") == identity]
    if identity not in registered.get("approved_sha256", []) or len(records) != 1:
        raise ValueError("Assessment revision is not bound by the versioned scoring authority")
    for key in ("root_cause", "regression_ids", "provider_calls"):
        if records[0].get(key) != revision.get(key):
            raise ValueError("Assessment revision provenance mismatch")
    validate_revision_controls(revision, normalizer)
