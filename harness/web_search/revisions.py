"""Re-evaluate saved evidence with explicit external correction authority."""
from copy import deepcopy
import json
from lexbench_websearch.scoring import evaluate
from lexbench_websearch.contracts import validate_cases
from lexbench_websearch.inputs import digest
from lexbench_websearch.answer_rules import POLICY_NAME
from lexbench_websearch.qualification import validate_regressions, _qualified, _equivalent


def _rescore(profile, rows):
    cases = {case["id"]: case for case in profile["cases"]}
    updated, changes = [], []
    for row in rows:
        new = {**row, "metrics": evaluate(cases[row["case_id"]], row["results"])}
        if new["metrics"] != row["metrics"]:
            changes.append({"id": row["id"], "before": row["metrics"], "after": new["metrics"]})
        updated.append(new)
    return profile, updated, changes


def apply_report_revision(profile, rows, revision):
    """Add independently observed reference annotations to immutable saved calls."""
    if revision.get("schema") != "web-search-assessment-revision/1":
        raise ValueError("Unsupported assessment revision")
    validate_regressions(revision)
    if revision.get("provider_calls") != 0:
        raise ValueError("An annotation revision cannot introduce provider calls")
    revised = deepcopy(profile)
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
    validate_cases(revised['cases'], reference_checks=revised.get('reference_checks'), reference_scope=revised.get('protocol', {}).get('reference_scope'))
    return _rescore(revised, rows)

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

def validate_revision_binding(revision, *, authority=None):
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
    validate_regressions(revision)
