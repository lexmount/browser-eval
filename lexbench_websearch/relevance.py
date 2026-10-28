"""Blind candidate pooling and graded top-k retrieval metrics."""

import hashlib
import json
import math
from .scoring import candidate_identity, canonical_url, in_domain
from .inputs import definition_path


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
    if len(set(plan["case_ids"])) != len(plan["case_ids"]) or set(labels) != set(
        plan["case_ids"]
    ):
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
                candidate_id = candidate_identity(case_id, canonical)
                ids.append(candidate_id)
                candidate_ids.add(candidate_id)
            rankings[method] = ids
        grades = labels[case_id]
        if set(grades) != candidate_ids or any(
            type(v) is not int or v not in range(4) for v in grades.values()
        ):
            raise ValueError("Relevance grades do not match the pooled candidates")
        for method in methods:
            ids = rankings[method]
            metrics = query_metrics(ids, grades, cutoff)
            scored.append(
                {
                    "case_id": case_id,
                    "family": case["family"],
                    "method": method,
                    **metrics,
                }
            )

    return scored


def query_metrics(ranking, grades, cutoff=5):
    """The single result-to-query calculation used by summaries and drilldowns."""
    if type(cutoff) is not int or cutoff < 1:
        raise ValueError("Cutoff must be a positive integer")
    if any(
        type(grade) is not int or grade not in range(4) for grade in grades.values()
    ):
        raise ValueError("Relevance grades must be integers 0..3")
    seen, ranked_grades, duplicates = set(), [], []
    for candidate_id in ranking[:cutoff]:
        duplicate = candidate_id in seen
        ranked_grades.append(0 if duplicate else grades[candidate_id])
        duplicates.append(duplicate)
        seen.add(candidate_id)
    ideal_grades = sorted(grades.values(), reverse=True)[:cutoff]
    contributions = [_gain(g, i) for i, g in enumerate(ranked_grades)]
    ideal_contributions = [_gain(g, i) for i, g in enumerate(ideal_grades)]
    dcg, ideal = sum(contributions), sum(ideal_contributions)
    useful = sum(g >= 2 for g in ranked_grades)
    return {
        "cutoff": cutoff,
        "ndcg": dcg / ideal if ideal else 0.0,
        "precision": useful / cutoff,
        "success": float(any(g == 3 for g in ranked_grades)),
        "returned": len(ranked_grades),
        "useful": useful,
        "ranked_grades": ranked_grades,
        "duplicates": duplicates,
        "ideal_grades": ideal_grades,
        "contributions": contributions,
        "ideal_contributions": ideal_contributions,
        "dcg": dcg,
        "idcg": ideal,
    }


def summary(scored, methods):
    return {
        method: {
            metric: sum(row[metric] for row in scoped) / len(scoped)
            for metric in ("ndcg", "precision", "success")
        }
        | {"n": len(scoped)}
        for method in methods
        if (scoped := [row for row in scored if row["method"] == method])
    }


def candidate_packet(
    profile,
    rows,
    source_requirements=None,
    *,
    source_policy="explicit-source/2",
    packet_format="search/1",
):
    """Pool every in-house query without method names, rankings or generated grades."""
    if source_policy not in ("legacy-domain-intersection/1", "explicit-source/2"):
        raise ValueError("Unsupported relevance source policy")
    if packet_format not in ("search/1", "serp/1"):
        raise ValueError("Unsupported frozen candidate packet format")
    case_ids = [c["id"] for c in profile["cases"] if "reference_answer" not in c]
    cutoff = profile["protocol"]["top_k"]
    indexed = {(r["case_id"], r["method"]): r for r in rows}
    if source_requirements is not None and not set(source_requirements).issubset(
        case_ids
    ):
        raise ValueError("Explicit source requirement names an unknown in-house query")
    if len(indexed) != len(rows):
        raise ValueError("Expected one final result per method and case")
    packets, rankings = [], {}
    for case in profile["cases"]:
        cid = case["id"]
        if cid not in case_ids:
            continue
        pool = {}
        rankings[cid] = {}
        for method in profile["methods"]:
            if (cid, method["id"]) not in indexed:
                raise ValueError("Incomplete method-case matrix")
            ranking = []
            for item in indexed[cid, method["id"]]["results"][:cutoff]:
                url = canonical_url(item["url"])
                key = hashlib.sha256((cid + "\0" + url).encode()).hexdigest()[:12]
                ranking.append(key)
                candidate = pool.setdefault(
                    key, {"candidate_id": key, "url": url, "previews": []}
                )
                preview = {f: item.get(f) or "" for f in ("title", "snippet")}
                if preview not in candidate["previews"]:
                    candidate["previews"].append(preview)
            rankings[cid][method["id"]] = ranking
        if source_requirements is not None:
            constraint = source_requirements.get(cid, {})
            for candidate in pool.values():
                url = candidate["url"]
                # Diagnostic target domains are not mandatory for ordinary web
                # queries. Explicit official-source alternatives must remain valid.
                restrict_domain = (
                    source_policy == "legacy-domain-intersection/1"
                    or case["family"] == "site"
                    or packet_format == "serp/1"
                    and bool(case.get("source"))
                )
                domain_ok = (
                    not restrict_domain
                    or not case.get("domains")
                    or any(in_domain(url, d) for d in case["domains"])
                )
                official_ok = (
                    not constraint
                    or any(in_domain(url, d) for d in constraint.get("domains", []))
                    or any(
                        url.startswith(prefix)
                        for prefix in constraint.get("url_prefixes", [])
                    )
                )
                candidate["source_eligible"] = domain_ok and official_ok
        for candidate in pool.values():
            candidate["previews"].sort(key=lambda x: (x["title"], x["snippet"]))
        packets.append(
            {
                "case_id": cid,
                "query": case["query"],
                "required_domains": case.get("domains", [])
                if packet_format != "serp/1" or case["family"] == "site"
                else [],
                "candidates": [pool[k] for k in sorted(pool)],
            }
        )
    if source_requirements is not None:
        for packet in packets:
            packet["source_checked"] = True
    blind = {
        "schema": "web-search-human-annotation/1",
        "plan": {"scope": "all_custom", "cutoff": cutoff, "case_ids": case_ids},
        "blind_candidate_packets": packets,
    }
    if (
        source_requirements is not None
        and source_policy != "legacy-domain-intersection/1"
    ):
        blind["source_policy"] = source_policy
    if packet_format == "serp/1" and source_requirements is not None:
        blind["source_requirements"] = source_requirements
    blind["packet_sha256"] = hashlib.sha256(
        json.dumps(blind, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return blind, rankings


SOURCE_RULE_REGRESSIONS = (
    "relevance-official-source-required",
    "relevance-official-source-alternatives",
    "relevance-explicit-platform-scope",
)


def validate_source_rule_regressions(ids):
    """Exercise paired source decisions through pooling and public relevance scores."""
    registry = json.loads(definition_path("scoring-regressions.json").read_text())
    if (
        not isinstance(ids, list)
        or len(ids) != len(set(ids))
        or not set(SOURCE_RULE_REGRESSIONS).issubset(ids)
    ):
        raise ValueError(
            "Source correction requires paired production regression provenance"
        )
    for identity in ids:
        controls = registry.get(identity, [])
        if not controls or {c.get("expected") for c in controls} != {True, False}:
            raise ValueError(
                "Source correction requires paired production regression controls"
            )
        for control in controls:
            if control.get("kind") != "relevance_source":
                raise ValueError("Unsupported source correction regression control")
            if source_rule_control(control) != control["expected"]:
                raise ValueError("Source correction regression failed: " + identity)


def source_rule_control(control):
    """Run one source control through the same pool and query metrics as a report."""
    case = control["case"]
    profile = {
        "protocol": {"top_k": 1},
        "methods": [{"id": "control"}],
        "cases": [case],
    }
    rows = [
        {
            "case_id": case["id"],
            "method": "control",
            "results": [{"url": control["url"], "snippet": "Directly useful content"}],
        }
    ]
    packet, _ = candidate_packet(
        profile, rows, {case["id"]: control["requirements"]}, packet_format="serp/1"
    )
    candidate = packet["blind_candidate_packets"][0]["candidates"][0]
    labels = {
        case["id"]: {
            candidate["candidate_id"]: 3 if candidate["source_eligible"] else 0
        }
    }
    metrics = score(profile, rows, packet["plan"], labels)[0]
    if any(
        metrics[key] != float(candidate["source_eligible"])
        for key in ("success", "precision", "ndcg")
    ):
        raise ValueError("Source eligibility and public relevance metrics disagree")
    return candidate["source_eligible"]
