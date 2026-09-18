"""Task 67's source reader must keep the canonical replay and discover current Hot posts."""

from __future__ import annotations

import json
from pathlib import Path

from harness.trajectory_replay.retrieval_acceptance import digest


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "evaluation/trajectory-replay/webarena-verified/retrieval-candidates/task-67.json"
SEEDS = ROOT / "evaluation/trajectory-replay/webarena-verified/retrieval-candidates/recompiled-seed-bindings.json"
AUDIT = ROOT / "research/trajectory-replay-task67-reference-audit.json"


def _candidate() -> dict:
    return json.loads(CANDIDATE.read_text())


def test_current_reader_preserves_frozen_prefix_and_does_not_claim_qualification() -> None:
    candidate = _candidate()
    seed = next(row for row in json.loads(SEEDS.read_text())["rows"] if row["task_id"] == 67)
    calls = candidate["tool_calls"]

    assert candidate["task_id"] == 67
    assert len(calls) == seed["canonical_action_count"] + 1
    assert digest(calls[: seed["canonical_action_count"]]) == seed["canonical_calls_sha256"]
    assert calls[-1]["action"] == "evaluate"
    assert candidate["retrieval_contract"] is None
    assert candidate["qualification_status"] == "pending_post_reset_source_review"


def test_current_reader_is_scoped_complete_and_does_not_embed_old_answer() -> None:
    script = _candidate()["tool_calls"][-1]["script"]

    assert "sort by:" in script.lower() and "hot" in script.lower()
    assert "articles.slice(0, 10)" in script
    assert "posts.length===10" in script
    assert "new Set(entries.map(row => row.post_id)).size !== 10" in script
    assert "detailTitle !== entry.title" in script
    assert "description:article.querySelector('.submission__body')?.textContent ?? ''" in script
    assert "credentials:'same-origin',redirect:'error'" in script
    for forbidden in ("The Hobbit", "A Christmas Carol", "17445", "59396", "method:'POST'", "method:'PUT'", "method:'DELETE'", "method:'PATCH'"):
        assert forbidden not in script


def test_current_reader_has_no_browser_semantic_heuristic() -> None:
    script = _candidate()["tool_calls"][-1]["script"]

    assert "recommendation_count" not in script
    assert "answer:" not in script
    assert "reviewed_span_projection" not in script
    assert "article_text:" in script


def test_source_audit_keeps_live_pollution_separate_from_reference_correction() -> None:
    audit = json.loads(AUDIT.read_text())
    rows = {row["post_id"]: row for row in audit["reviewed_posts"]}

    assert audit["source"]["reset_receipt"] is None
    assert audit["source"]["status"] == "contaminated_shared_site_not_frozen_reference"
    assert audit["source"]["current_first_three_ids"] == ["4", "3", "124260"]
    assert len(rows) == 10
    assert [row["expected_baseline_rank_if_reset_restores_order"] for row in audit["reviewed_posts"]] == list(range(1, 11))
    assert rows["17445"]["title_spans_in_description"] == [
        {"title": "The Hobbit", "start": 463, "end": 473}
    ]
    assert rows["59396"]["recommendation_count"] == 1
    assert rows["59396"]["title_spans_in_description"] == []
    assert rows["59396"]["title_only_name"] == "A Christmas Carol"
    assert audit["reference_under_review"]["decision_status"] == "pending_post_reset_top_ten_verification"
