"""Compile article contracts against independently reviewed, external full bodies.

The authoring inventory retains unresolved cases. Compilation is fail-closed:
every approved case needs its own complete, hash-bound reference and contract.
No provider, browser adapter or capture orchestration belongs in this module.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from .body_coverage import body_requirements, compact_ngrams
from .models import EvalCase, validate_case
from .paths import contained_path
from .stable_tasks import validate_acceptance_contract

from harness.benchmarks.data import snapshot

DEFINITIONS = snapshot("webfetch") / "definitions/evaluation/articles"


def _acceptance(rule: dict) -> dict:
    acceptance = rule["acceptance_contract"]
    validate_acceptance_contract(acceptance)
    if acceptance["schema"] != "body_text" or not acceptance.get("page_patterns"):
        raise ValueError("Article rules require body text and target identity")
    if any(re.compile(pattern).search("") for pattern in acceptance["page_patterns"]):
        raise ValueError("Article identity patterns cannot match empty text")
    if acceptance.get("stable_text_role", "required") != "required":
        raise ValueError("Declared article stable text must be required")
    reviews = rule.get("stable_text_review")
    if not isinstance(reviews, list) or not rule.get("excluded_regions"):
        raise ValueError("Article rules need stable-region review and exclusions")
    defaults = rule.get("stable_text_review_defaults", {})
    if (not isinstance(defaults, dict) or set(defaults) - {"basis", "observed_on"}
            or any(not isinstance(v, str) or not v.strip() for v in defaults.values())):
        raise ValueError("Invalid stable review defaults")
    reviews = [{**defaults, **review} if isinstance(review, dict) else review
               for review in reviews]
    anchor_ids = [anchor["id"] for anchor in acceptance.get("stable_text_anchors", [])]
    if (any(not isinstance(review, dict) for review in reviews)
            or len(reviews) != len(anchor_ids)
            or {review.get("id") for review in reviews} != set(anchor_ids)
            or any(not isinstance(review.get(key), str) or not review[key].strip()
                   for review in reviews for key in ("region", "basis", "observed_on"))):
        raise ValueError("Every declared stable group requires its own source-region review")
    return acceptance


def _base_case(case: dict, rule: dict, acceptance: dict) -> dict:
    task = {
        "id": case["id"], "name": case["name"], "url": case["url"],
        "category": "article", "track": "articles", "task_kind": "body",
        "task_prompt": case["task_prompt"], "expected_any": [], "min_content_chars": 1,
        "acceptance_contract": acceptance,
        "rule_review": {"status": "reviewed", "reason": rule["reason"]},
    }
    components = {}
    if "title_contract" in rule:
        from .article_title import validate_title_contract
        validate_title_contract(rule["title_contract"])
        components["title_contract"] = rule["title_contract"]
    if "comment_contract" in rule:
        from .article_comments import validate_comment_contract
        validate_comment_contract(rule["comment_contract"])
        components["comment_contract"] = rule["comment_contract"]
    if components:
        task["source_benchmark"] = components
    validate_case(EvalCase(**task))
    return task



def _qualify_components(case: dict, rule: dict, task: dict, evidence_home: Path) -> dict:
    """Validate historical qualification without certifying a future live run."""
    review = rule.get("comment_coverage_review")
    if "comment_contract" not in rule and review is None:
        return task
    from .article_qualification import qualify_article_comments
    contract = rule.get("comment_contract")
    if review is not None:
        if (not isinstance(review, dict) or review.get("status") not in (
                "visible_text", "observed_absent", "visible_placeholders_only")
                or not isinstance(review.get("basis"), str) or not review["basis"].strip()):
            raise ValueError("Invalid comment presence review")
        if ((review["status"] == "visible_text") != (contract is not None)
                or contract is not None and contract.get("version") != 2):
            raise ValueError("Comment presence review and coverage contract disagree")
        if "historical_contract" in review and (
                review["status"] != "visible_placeholders_only"
                or not isinstance(review["historical_contract"], dict)
                or review["historical_contract"].get("version") != 1):
            raise ValueError("Only placeholders may retain a legacy authoring contract")
    elif contract is not None and contract.get("version") == 2:
        raise ValueError("Comment coverage requires a presence review")
    if contract is None:
        if not isinstance(review, dict) or review.get("status") not in (
                "observed_absent", "visible_placeholders_only"):
            raise ValueError("Omitted comment rules require reviewed absence evidence")

    proof = rule.get("comment_qualification_reference", {})
    if not isinstance(proof.get("path"), str) or not proof.get("sha256"):
        raise ValueError("Comment contracts require independent qualification evidence")
    raw = contained_path(evidence_home, proof["path"], reject_symlinks=True).read_bytes()
    if hashlib.sha256(raw).hexdigest() != proof["sha256"]:
        raise ValueError("Changed article comment qualification reference")
    reference = json.loads(raw)
    checked = qualify_article_comments(
        contract, reference, case_id=case["id"], url=case["url"])
    if checked["status"] != "qualified":
        raise ValueError("Article comments require a complete independent observation")
    if review is not None:
        if not isinstance(review, dict) or not reference.get("source_evidence"):
            raise ValueError("Comment presence review requires source evidence")
        if review.get("status") == "visible_placeholders_only":
            placeholders = review.get("placeholder_texts", [])
            records = reference.get("records", [])
            if ("comment_contract" in rule or not records
                    or not isinstance(placeholders, list) or not placeholders
                    or any(not isinstance(text, str) or not text.strip() for text in placeholders)
                    or any(row["text"].strip() not in placeholders for row in records)):
                raise ValueError("Placeholder-only review must explain every observed record")
        elif review.get("status") == "observed_absent" and reference.get("records"):
            raise ValueError("Absent comment review contains observed records")
        elif review.get("status") == "visible_text" and "comment_contract" not in rule:
            raise ValueError("Visible comment text requires a coverage contract")
    for source in reference.get("source_evidence", []):
        data = contained_path(evidence_home, source["path"], reject_symlinks=True).read_bytes()
        if hashlib.sha256(data).hexdigest() != source["sha256"]:
            raise ValueError("Changed article comment source evidence")
    task.setdefault("source_benchmark", {})["comment_qualification_proof"] = proof
    if review is not None:
        task["source_benchmark"]["comment_presence_review"] = {
            "status": review["status"], "basis": review["basis"],
            "observed_record_count": len(reference["records"]),
            "qualification_only": True,
        }
    return task


def _compile_post_collection(case: dict, rule: dict, evidence_home: Path) -> dict:
    from .article_posts import validate_post_contract
    from .article_qualification import qualify_article_posts
    if case.get("page_type") != "collection":
        raise ValueError("Post collection rules require an explicitly reviewed collection")
    contract = rule["post_contract"]
    validate_post_contract(contract)
    proof = rule["qualification_reference"]
    raw = contained_path(evidence_home, proof["path"], reject_symlinks=True).read_bytes()
    if hashlib.sha256(raw).hexdigest() != proof["sha256"]:
        raise ValueError("Changed article collection qualification reference")
    reference = json.loads(raw)
    records = reference.get("records", [])
    counts = reference.get("source_observation_counts", [])
    if (reference.get("version") != 1 or reference.get("case_id") != case["id"]
            or reference.get("url") != case["url"]
            or reference.get("scope") != "initial_load_only"
            or not isinstance(counts, list) or len(counts) < 2
            or any(type(count) is not int or count < 1 for count in counts)
            or not isinstance(records, list) or not records or len(records) != counts[-1]):
        raise ValueError("Article collections require independent initial-load record review")
    sources = rule.get("post_coverage_review", {}).get("source_evidence", [])
    if not sources:
        raise ValueError("Static post coverage requires source evidence")
    for source in sources:
        captured = contained_path(evidence_home, source["path"], reject_symlinks=True).read_bytes()
        if hashlib.sha256(captured).hexdigest() != source["sha256"]:
            raise ValueError("Changed article collection source evidence")
    qualification = qualify_article_posts(
        contract, reference, case_id=case["id"], url=case["url"])
    if qualification["status"] != "qualified":
        raise ValueError("Every source record needs complete owned post and quote text")
    task = _base_case(case, rule, _acceptance(rule))
    task.update(ground_truth_main_content="", source_benchmark={
        **task.get("source_benchmark", {}),
        "mode": "article_post_collection", "post_contract": contract,
        "qualification_proof": proof,
    })
    return _qualify_components(case, rule, task, evidence_home)


def compile_case(case: dict, rule: dict, evidence_home: Path) -> dict:
    """Require a reviewed complete body; never turn a missing reference into a pass."""
    if rule.get("id") != case["id"] or rule.get("status") != "verified":
        raise ValueError(f"Unresolved article rule: {case['id']}")
    if rule.get("target_kind") == "post_collection":
        return _compile_post_collection(case, rule, evidence_home)
    if rule.get("target_kind") != "fixed_article" or case.get("page_type") == "collection":
        raise ValueError("Collection/post-feed contracts require separate record review")
    proof = rule["body_reference"]
    path = contained_path(evidence_home, proof["path"], reject_symlinks=True)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != proof["sha256"]:
        raise ValueError(f"Changed article reference: {case['id']}")
    body = raw.decode("utf-8")
    if len(body) != proof["chars"] or not body.strip():
        raise ValueError("Empty or incorrectly bound article body")
    scope = rule["reference_scope"]
    if (scope.get("complete_initial_body") is not True
            or not scope.get("start") or not scope.get("end")
            or not body.lstrip().startswith(scope["start"])
            or not body.rstrip().endswith(scope["end"])):
        raise ValueError("Article scope must bind the complete opening and ending")
    acceptance = _acceptance(rule)
    lines = body.splitlines()
    ranges = rule.get("body_regions", [])
    if not isinstance(ranges, list) or not ranges:
        raise ValueError("Article scope requires reviewed contiguous semantic regions")
    contract = body_requirements(body)
    segmentation_version = rule.get("body_segmentation", {}).get("version")
    if segmentation_version == 3 and len(ranges) > 5:
        raise ValueError("Current article scope allows at most five body regions")
    semantic = segmentation_version in (2, 3)
    reviews = rule.get("body_region_review", [])
    if semantic and (len(reviews) != len(ranges)
                     or rule["body_segmentation"].get("reference_sha256") != proof["sha256"]
                     or any(row.get("lines") != bounds
                            or not isinstance(row.get("label"), str) or not row["label"].strip()
                            for row, bounds in zip(reviews, ranges))):
        raise ValueError("Every semantic region needs a source-bound readable label")
    regions, next_line = [], 1
    for index, bounds in enumerate(ranges, 1):
        if (not isinstance(bounds, list) or len(bounds) != 2
                or any(type(value) is not int for value in bounds)
                or bounds[0] != next_line or not bounds[0] <= bounds[1] <= len(lines)):
            raise ValueError("Article regions must cover every source line once in order")
        source = "\n".join(lines[bounds[0] - 1:bounds[1]]).strip()
        part = body_requirements(source, structure_hint="whole_declared_component")
        if not part["regions"] or not compact_ngrams(source, 6):
            raise ValueError("Article body cannot yield an empty coverage region")
        region = part["regions"][0]
        region.update(id=f"body-region-{index}", segmentation_method=(
            "reviewed_source_semantics" if semantic else "reviewed_source_lines"))
        if semantic:
            region.update(label=reviews[index - 1]["label"], source_line_range=bounds)
        regions.append(region)
        next_line = bounds[1] + 1
    if next_line != len(lines) + 1:
        raise ValueError("Article regions must include the complete source ending")
    contract.update(version=4, ending={"text": scope["end"]},
                    regions=regions, segmentation_method=(
                        "reviewed_source_semantics" if semantic else "reviewed_source_lines"),
                    minimum_region_passes=len(regions),
                    minimum_floor=contract["minimum_coverage"])
    task = {
        **_base_case(case, rule, acceptance),
        "ground_truth_main_content": body,
        "body_requirements": contract,
    }
    return _qualify_components(case, rule, task, evidence_home)


def compile_dataset(evidence_home: Path, definition_dir: Path = DEFINITIONS) -> dict:
    """Compile the whole approved population, refusing unresolved or omitted cases."""
    sources = {name: (definition_dir / name).read_bytes()
               for name in ("cohort.json", "rules.json")}
    cohort = json.loads(sources["cohort.json"])
    document = json.loads(sources["rules.json"])
    cases, rules = cohort["cases"], document["rules"]
    ids = [case["id"] for case in cases]
    if (cohort.get("schema_version") != 1 or document.get("schema_version") != 1
            or cohort.get("case_count") != len(cases)
            or not ids or len(ids) != len(set(ids))
            or ids != [rule["id"] for rule in rules]):
        raise ValueError("Article cohort and rule inventory differ")
    compiled = [
        compile_case(case, rule, evidence_home) for case, rule in zip(cases, rules)
    ]
    return {
        "schema_version": 1, "id": cohort.get("id", "articles-v1"), "name": "Article retrieval",
        "tool": "web_fetch", "case_count": len(compiled),
        "site_count": len({urlsplit(case["url"]).hostname for case in cases}),
        "runs_per_case": 1, "timeout_ms": 45000, "workers": 1,
        "retry_policy": {"declared_retries": 0,
                         "authoritative_response": "final_physical_call"},
        "interaction_policy": "initial_load_only_no_click_input_scroll_pagination_login_or_ocr",
        "source_bindings": {
            name.replace(".json", "_sha256"): hashlib.sha256(raw).hexdigest()
            for name, raw in sources.items()
        },
        "cases": compiled,
    }
