from __future__ import annotations

import pytest

from scripts.build_webarena_qualification_subset import build_subset


def _task(task_id: str, task_type: str, site: str) -> dict:
    return {
        "id": task_id,
        "tags": [task_type],
        "environment": {"sites": [site]},
        "ground_truth": {"qualification": {"status": "skipped"}},
    }


def test_qualification_subset_selects_disposition_type_and_site():
    suite = {
        "benchmark": {"id": "source"},
        "tasks": [
            _task("a", "mutate", "shopping"),
            _task("b", "retrieve", "shopping"),
            _task("c", "mutate", "gitlab"),
        ],
    }
    qualification = {
        "tasks": [
            {"task_id": "a", "disposition": "needs_trace_repair"},
            {"task_id": "b", "disposition": "needs_trace_repair"},
            {"task_id": "c", "disposition": "needs_response_annotation"},
        ]
    }

    result = build_subset(
        suite,
        qualification,
        dispositions={"needs_trace_repair"},
        task_types={"mutate"},
        site="shopping",
    )

    assert [task["id"] for task in result["tasks"]] == ["a"]
    assert result["tasks"][0]["ground_truth"]["qualification"] == {
        "status": "pending"
    }
    assert result["benchmark"]["qualification_subset"]["task_count"] == 1


def test_qualification_subset_rejects_empty_result():
    with pytest.raises(ValueError, match="empty"):
        build_subset(
            {"tasks": [_task("a", "retrieve", "map")]},
            {"tasks": [{"task_id": "a", "disposition": "eligible"}]},
            dispositions={"needs_trace_repair"},
            task_types={"mutate"},
        )


def test_qualification_subset_rejects_unknown_filter_values():
    with pytest.raises(ValueError, match="unsupported disposition"):
        build_subset(
            {"tasks": []},
            {"tasks": []},
            dispositions={"unknown"},
            task_types={"mutate"},
        )


def test_qualification_subset_can_match_an_exact_multi_site_group():
    multi = _task("a", "mutate", "gitlab")
    multi["environment"]["sites"] = ["gitlab", "reddit"]
    single = _task("b", "mutate", "gitlab")
    qualification = {
        "tasks": [
            {"task_id": "a", "disposition": "needs_trace_repair"},
            {"task_id": "b", "disposition": "needs_trace_repair"},
        ]
    }

    result = build_subset(
        {"tasks": [multi, single]},
        qualification,
        dispositions={"needs_trace_repair"},
        task_types={"mutate"},
        sites_exact={"gitlab", "reddit"},
    )

    assert [task["id"] for task in result["tasks"]] == ["a"]
