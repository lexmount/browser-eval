"""Print the source-reviewed task 67 retrieval contract for its current candidate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from harness.trajectory_replay.retrieval_acceptance import digest, validate_contract

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = (
    ROOT / "evaluation/trajectory-replay/webarena-verified/retrieval-candidates/task-67.json"
)
AUDIT = ROOT / "research/trajectory-replay-task67-reference-audit.json"


def main() -> None:
    candidate = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    if audit["reference_under_review"]["decision_status"] != "confirmed_after_baseline_restoration":
        raise ValueError("task 67 source review is not confirmed")
    expected_ids = audit["source"]["current_top_ten_ids"]
    posts = audit["reviewed_posts"]
    if [row["post_id"] for row in posts] != expected_ids or len(posts) != 10:
        raise ValueError("task 67 review does not cover the ordered top ten")
    calls = candidate["tool_calls"]
    sources = [{"name": "books", "step": 4, "action_sha256": digest(calls[3]), "decode": "json"}]
    items = []
    for row in posts:
        spans = row["title_spans_in_description"]
        selected = row["post_id"] == "17445"
        if selected != bool(spans) or (selected and spans[0]["title"] != "The Hobbit"):
            raise ValueError("task 67 reviewed title spans changed")
        items.append(
            {
                "identity_sha256": digest(row["post_id"]),
                "semantic_text_sha256": row["body_sha256"],
                "output_text_sha256": row["body_sha256"],
                "recommendation_count": int(selected),
                "spans": (
                    [{"start": spans[0]["start"], "end": spans[0]["end"]}]
                    if selected else []
                ),
            }
        )
    projection = {
        "kind": "reviewed_ordered_spans/1",
        "source": "books",
        "collection_pointer": "/posts",
        "identity_pointer": "/post_id",
        "semantic_text_pointer": "/description",
        "output_text_pointer": "/description",
        "output": "selected_spans",
        "span_unit": "unicode_code_points",
        "annotation": {
            "schema": "lex_replay_reviewed_span_annotation/1",
            "semantic": "exactly_one_book_recommendation",
            "items": items,
        },
    }
    reviewer = {"kind": "agent", "identity": "parent agent, independent source review"}
    scope = (
        "Ordered Books Hot top ten: exactly one recommended book "
        "with its title in the post description"
    )
    review = {
        "reviewer": reviewer,
        "scope": scope,
        "task_id": 67,
        "sources_sha256": digest(sources),
        "projection_sha256": digest(projection),
        "decision": (
            "Only the body of post 17445 yields an exact book title; "
            "post 59396 names its book only in the header."
        ),
        "audit_sha256": hashlib.sha256(AUDIT.read_bytes()).hexdigest(),
        "predecessor_artifacts": [],
    }
    review_text = json.dumps(review, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    evidence = {
        "schema": "lex_replay_review_evidence/1",
        "reviewer": reviewer,
        "scope": scope,
        "task_id": 67,
        "sources_sha256": digest(sources),
        "projection_sha256": digest(projection),
        "source_review": review,
        "source_review_sha256": digest(review),
        "source_review_artifact": {
            "utf8": review_text,
            "sha256": hashlib.sha256(review_text.encode()).hexdigest(),
            "content": review,
        },
    }
    contract = {
        "schema": "lex_replay_current_run_retrieval/3",
        "task_id": 67,
        "canonical_calls_sha256": digest(calls),
        "sources": sources,
        "semantic_reference": {
            "kind": "reviewed_span_annotation",
            "browser_semantic_inference": False,
            "reviewer": reviewer,
            "scope": scope,
            "evidence_sha256": digest(evidence),
        },
        "reviewed_span_projection": projection,
        "requirements": {
            "scope": {"source": "books", "pointer": "/scope"},
            "completeness": {"source": "books", "pointer": "/complete"},
        },
        "review_evidence": evidence,
    }
    validate_contract(contract, calls)
    print(json.dumps(contract, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
