"""Explicit, evidence-backed identity corrections without repeating provider calls."""

from copy import deepcopy

from .core import canonical_url, evaluate, validate_profile


def apply_report_revision(profile, rows, revision):
    """Add independently observed reference annotations to immutable saved calls."""
    if revision.get("schema") != "web-search-assessment-revision/1":
        raise ValueError("Unsupported assessment revision")
    if not revision.get("root_cause") or not revision.get("regression_ids"):
        raise ValueError("Assessment revision needs cause and regression provenance")
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
        anchors, sources = item.get("anchors", []), item.get("sources", [])
        if not anchors or not sources or not item.get("qualification"):
            raise ValueError("An annotation requires independent source qualification")
        observed = set()
        for source in sources:
            if source.get("title") and source.get("excerpt") and source.get("verified_at"):
                observed.add(source["url"])
                if source.get("observation_kind") == "direct_official_source_http" and source.get("http_status") == 200:
                    observed.add(source.get("final_url"))
                if source.get("observation_kind") == "official_publisher_canonical_link":
                    observed.add(source.get("qualifying_link"))
        if any(not a.startswith("https://") or a not in observed for a in anchors):
            raise ValueError("Unqualified source anchor")
        cases[cid]["anchors"] = anchors
        cases[cid]["reference_annotation"] = item
    if revision.get("answer_match_rule"):
        if revision["answer_match_rule"] != "published-answers/2":
            raise ValueError("Unsupported answer matching rule")
        for case in cases.values():
            if case.get("reference_answer"):
                case["answer_match_rule"] = revision["answer_match_rule"]
    updated, changes = [], []
    for row in rows:
        new = {**row, "metrics": evaluate(cases[row["case_id"]], row["results"])}
        if new["metrics"] != row["metrics"]:
            changes.append({"id": row["id"], "before": row["metrics"], "after": new["metrics"]})
        updated.append(new)
    return revised, updated, changes


def apply_identity_correction(profile, rows, correction):
    if not correction.get("root_cause") or not correction.get("regression_ids"):
        raise ValueError("A scoring correction requires cause and regression provenance")
    observations = {x["url"]: x for x in correction["observations"]}
    revised = deepcopy(profile)
    cases = {c["id"]: c for c in revised["cases"]}
    for cid, aliases in correction["aliases"].items():
        case = cases[cid]
        for alias, target in aliases.items():
            a, b = observations[alias], observations[target]
            if a.get("error") or b.get("error"):
                raise ValueError("Failed source identity observation")
            same_destination = any(
                a.get(k) and b.get(k) and canonical_url(a[k]) == canonical_url(b[k])
                for k in ("final", "canonical")
            )
            same_text = a.get("text_sha256") and a["text_sha256"] == b.get("text_sha256")
            if not (same_destination or same_text):
                raise ValueError("Unproven equivalent page")
            if target not in case.get("anchors", []):
                raise ValueError("Correction must retain the original known target")
        case["identity_aliases"] = dict(aliases)
    validate_profile(revised)
    updated, changes = [], []
    for row in rows:
        new = dict(row)
        new["metrics"] = evaluate(cases[row["case_id"]], row["results"])
        if new["metrics"] != row["metrics"]:
            changes.append({"id": row["id"], "before": row["metrics"], "after": new["metrics"]})
        updated.append(new)
    return revised, updated, changes
