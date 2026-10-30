"""Reference qualification and paired scoring controls."""

import json
from .inputs import definition_path
from .scoring import (
    canonical_url,
    evaluate,
    normalize_results as normalize,
    task_acceptance,
)
from .answer_rules import match_reference_answer


def validate_regressions(record):
    """Execute paired controls before accepting correction provenance."""
    registry = json.loads(definition_path("scoring-regressions.json").read_text())
    ids = record.get("regression_ids")
    if (
        not record.get("root_cause")
        or not isinstance(ids, list)
        or not ids
        or any(not isinstance(i, str) for i in ids)
        or len(ids) != len(set(ids))
    ):
        raise ValueError("Correction requires cause and unique regression provenance")
    for identity in ids:
        if identity not in registry:
            raise ValueError("Unknown scoring regression: " + str(identity))
        controls = registry[identity]
        if {c["expected"] for c in controls} != {True, False}:
            raise ValueError(
                "Scoring regression requires paired positive and negative controls"
            )
        for control in controls:
            if control["kind"] == "answer":
                actual = match_reference_answer(
                    control["answer"],
                    control["text"],
                    derivation=control.get("derivation"),
                )
            elif control["kind"] == "anchor":
                actual = bool(
                    evaluate(
                        control["case"],
                        normalize({"results": [{"url": control["url"]}]}),
                    )["anchor_hit_5"]
                )
            elif control["kind"] == "benchmark_page":
                from .evidence_quality import is_benchmark_page

                actual = is_benchmark_page(control["result"], case=control["case"])
            elif control["kind"] == "relevance_source":
                from .relevance import source_rule_control

                actual = source_rule_control(control)
            elif control["kind"] == "source":
                from .relevance import candidate_packet

                case = control["case"]
                packet, _ = candidate_packet(
                    {
                        "cases": [case],
                        "methods": [{"id": "control"}],
                        "protocol": {"top_k": 5},
                    },
                    [
                        {
                            "case_id": case["id"],
                            "method": "control",
                            "results": [{"url": control["url"]}],
                        }
                    ],
                    {case["id"]: control.get("requirements", {})},
                )
                actual = packet["blind_candidate_packets"][0]["candidates"][0][
                    "source_eligible"
                ]
            elif control["kind"] == "annotation":
                actual = _qualified(control["item"])
            elif control["kind"] == "qualification":
                actual = _qualified(control["item"])
            elif control["kind"] == "equivalence":
                actual = _equivalent(control["left"], control["right"])
            else:
                raise ValueError("Unsupported scoring regression control")
            if control["kind"] in ("answer", "anchor", "benchmark_page"):
                if control["kind"] == "answer":
                    case = {
                        "reference_answer": control["answer"],
                        "answer_match_rule": "published-answers/2",
                        "answer_derivation": control.get("derivation"),
                    }
                    results = [
                        {"url": "https://docs.test/fact", "snippet": control["text"]}
                    ]
                    expected = control["expected"]
                elif control["kind"] == "anchor":
                    case, results = control["case"], [{"url": control["url"]}]
                    expected = control["expected"]
                else:
                    case = dict(control["case"], reference_answer="controlanswer")
                    results = [
                        dict(
                            control["result"],
                            snippet=control["result"].get("snippet", "")
                            + " controlanswer",
                        )
                    ]
                    expected = not control["expected"]
                row = {"results": results, "metrics": evaluate(case, results)}
                if task_acceptance(case, row, cutoff=5) != expected:
                    raise ValueError("Task acceptance regression failed: " + identity)
            if actual != control["expected"]:
                raise ValueError("Scoring regression failed: " + identity)


def _qualified(item):
    anchors, sources = item.get("anchors", []), item.get("sources", [])
    observed = set()
    for source in sources:
        if source.get("title") and source.get("excerpt") and source.get("verified_at"):
            observed.add(source["url"])
            if (
                source.get("observation_kind") == "direct_official_source_http"
                and source.get("http_status") == 200
            ):
                observed.add(source.get("final_url"))
            if source.get("observation_kind") == "official_publisher_canonical_link":
                observed.add(source.get("qualifying_link"))
    return bool(anchors and sources and item.get("qualification")) and all(
        a.startswith("https://") and a in observed for a in anchors
    )


def _equivalent(a, b):
    if a.get("error") or b.get("error"):
        return False
    return bool(
        any(
            a.get(k) and b.get(k) and canonical_url(a[k]) == canonical_url(b[k])
            for k in ("final", "canonical")
        )
        or (a.get("text_sha256") and a["text_sha256"] == b.get("text_sha256"))
    )
