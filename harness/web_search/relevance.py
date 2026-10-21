"""Blind, pooled top-k preview relevance scoring for Web Search results."""

import hashlib
import math

from .core import canonical_url


def _gain(grade, rank):
    return (2**grade - 1) / math.log2(rank + 2)


def score(profile, rows, plan, labels):
    """Return per-query/provider nDCG and precision after exact coverage checks."""
    cutoff = plan["cutoff"]
    if cutoff != profile["protocol"]["top_k"] or cutoff < 1:
        raise ValueError("Relevance cutoff differs from the measured result count")
    cases = {case["id"]: case for case in profile["cases"]}
    methods = [method["id"] for method in profile["methods"]]
    indexed = {(row["case_id"], row["method"]): row for row in rows}
    if len(set(plan["case_ids"])) != len(plan["case_ids"]) or set(labels) != set(plan["case_ids"]):
        raise ValueError("Relevance labels do not cover the declared sample")
    scored = []
    for case_id in plan["case_ids"]:
        case = cases[case_id]
        rankings = {}
        candidate_ids = set()
        for method in methods:
            result = indexed[case_id, method]
            ids = []
            for item in result["results"][:cutoff]:
                canonical = canonical_url(item["url"])
                candidate_id = hashlib.sha256((case_id + "\0" + canonical).encode()).hexdigest()[:12]
                ids.append(candidate_id)
                candidate_ids.add(candidate_id)
            rankings[method] = ids
        grades = labels[case_id]
        if set(grades) != candidate_ids or any(type(v) is not int or v not in range(4) for v in grades.values()):
            raise ValueError("Relevance grades do not match the pooled candidates")
        ideal = sum(_gain(g, i) for i, g in enumerate(sorted(grades.values(), reverse=True)[:cutoff]))
        if ideal == 0:
            raise ValueError("No relevant candidate in a declared sample case")
        for method in methods:
            ids = rankings[method]
            seen = set()
            ranked_grades = []
            for candidate_id in ids:
                ranked_grades.append(0 if candidate_id in seen else grades[candidate_id])
                seen.add(candidate_id)
            dcg = sum(_gain(grade, rank) for rank, grade in enumerate(ranked_grades))
            scored.append({"case_id": case_id, "family": case["family"], "method": method,
                           "ndcg": dcg / ideal, "precision": sum(g >= 2 for g in ranked_grades) / cutoff,
                           "returned": len(ids)})
    return scored


def summary(scored, methods):
    return {method: {metric: sum(row[metric] for row in scoped) / len(scoped)
                     for metric in ("ndcg", "precision")}
            | {"n": len(scoped)}
            for method in methods if (scoped := [row for row in scored if row["method"] == method])}


def verify_provenance(profile, rows, evidence):
    """Make public candidate packets and rank mappings agree with measured outputs."""
    plan = evidence["plan"]
    packets = evidence["blind_candidate_packets"]
    rankings = evidence["provider_rankings"]
    labels = evidence["labels"]
    if [packet["case_id"] for packet in packets] != plan["case_ids"]:
        raise ValueError("Blind packets do not cover the declared sample")
    indexed = {(row["case_id"], row["method"]): row for row in rows}
    for packet in packets:
        case_id = packet["case_id"]
        ids = {candidate["candidate_id"] for candidate in packet["candidates"]}
        if ids != set(labels[case_id]) or len(ids) != len(packet["candidates"]):
            raise ValueError("Blind packet candidates disagree with grades")
        for candidate in packet["candidates"]:
            expected = hashlib.sha256((case_id + "\0" + canonical_url(candidate["url"])).encode()).hexdigest()[:12]
            if candidate["candidate_id"] != expected:
                raise ValueError("Blind packet URL identity mismatch")
        for method in (m["id"] for m in profile["methods"]):
            actual = [hashlib.sha256((case_id + "\0" + canonical_url(item["url"])).encode()).hexdigest()[:12]
                      for item in indexed[case_id, method]["results"][:plan["cutoff"]]]
            if rankings[case_id][method] != actual:
                raise ValueError("Published rank mapping disagrees with provider results")
    crosscheck = evidence.get("crosscheck_labels", {})
    if not set(crosscheck).issubset(labels) or any(
        not set(grades).issubset(labels[case_id]) or any(type(v) is not int or v not in range(4) for v in grades.values())
        for case_id, grades in crosscheck.items()
    ):
        raise ValueError("Cross-check grades are invalid")
