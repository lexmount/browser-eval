"""Explicit, evidence-backed identity corrections without repeating provider calls."""

from copy import deepcopy
import json
from pathlib import Path

from .core import canonical_url, evaluate, validate_profile, normalize
from .answer_rules import match_reference_answer


def validate_regressions(record):
    """Execute paired controls before accepting correction provenance."""
    registry = json.loads((Path(__file__).resolve().parents[2] / "evaluation/web-search/scoring-regressions.json").read_text())
    ids = record.get("regression_ids")
    if not record.get("root_cause") or not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("Correction requires cause and unique regression provenance")
    for identity in ids:
        if identity not in registry:
            raise ValueError("Unknown scoring regression: " + str(identity))
        controls = registry[identity]
        if {c["expected"] for c in controls} != {True, False}:
            raise ValueError("Scoring regression requires paired positive and negative controls")
        for control in controls:
            if control["kind"] == "answer":
                actual = match_reference_answer(control["answer"], control["text"])
            elif control["kind"] == "anchor":
                actual = bool(evaluate(control["case"], normalize({"results": [{"url": control["url"]}]}))["anchor_hit_5"])
            elif control["kind"] == "benchmark_page":
                from .evidence_quality import is_benchmark_page
                actual = is_benchmark_page(control["result"], case=control["case"])
            elif control["kind"] == "source":
                from .relevance import candidate_packet
                case = control["case"]
                packet, _ = candidate_packet(
                    {"cases": [case], "methods": [{"id": "control"}], "protocol": {"top_k": 5}},
                    [{"case_id": case["id"], "method": "control", "results": [{"url": control["url"]}]}],
                    {case["id"]: control.get("requirements", {})},
                )
                actual = packet["blind_candidate_packets"][0]["candidates"][0]["source_eligible"]
            elif control["kind"] == "qualification":
                actual = _qualified(control["item"])
            elif control["kind"] == "equivalence":
                actual = _equivalent(control["left"], control["right"])
            else:
                raise ValueError("Unsupported scoring regression control")
            if actual != control["expected"]:
                raise ValueError("Scoring regression failed: " + identity)


def _qualified(item):
    anchors, sources = item.get("anchors", []), item.get("sources", [])
    observed = set()
    for source in sources:
        if source.get("title") and source.get("excerpt") and source.get("verified_at"):
            observed.add(source["url"])
            if source.get("observation_kind") == "direct_official_source_http" and source.get("http_status") == 200:
                observed.add(source.get("final_url"))
            if source.get("observation_kind") == "official_publisher_canonical_link":
                observed.add(source.get("qualifying_link"))
    return bool(anchors and sources and item.get("qualification")) and all(a.startswith("https://") and a in observed for a in anchors)


def _equivalent(a, b):
    if a.get("error") or b.get("error"):
        return False
    return bool(any(a.get(k) and b.get(k) and canonical_url(a[k]) == canonical_url(b[k]) for k in ("final", "canonical")) or (a.get("text_sha256") and a["text_sha256"] == b.get("text_sha256")))


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
    validate_profile(revised)
    updated, changes = [], []
    for row in rows:
        new = dict(row)
        new["metrics"] = evaluate(cases[row["case_id"]], row["results"])
        if new["metrics"] != row["metrics"]:
            changes.append({"id": row["id"], "before": row["metrics"], "after": new["metrics"]})
        updated.append(new)
    return revised, updated, changes


def validate_revision_binding(revision):
    """Reader prose cannot introduce or modify an approved scoring revision."""
    from .core import digest
    authority = Path(__file__).resolve().parents[2] / "evaluation/web-search/assessment-revisions.json"
    registered = json.loads(authority.read_text())
    identity = digest(json.dumps(revision, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    if identity not in registered["approved_sha256"]:
        raise ValueError("Assessment revision is not bound by the versioned scoring authority")
    validate_regressions(revision)
