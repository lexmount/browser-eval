from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _module():
    script = Path(__file__).resolve().parents[2] / "examples/browser_use/rebuild_report.py"
    spec = importlib.util.spec_from_file_location("rebuild_browser_report", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rebuild_accepts_a_manifest_selected_subset_of_the_bound_suite():
    _module().validate_task_binding(
        suite_task_ids={"a", "b", "c"},
        manifest_task_ids={"a", "c"},
        attempt_task_ids={"a", "c"},
    )


@pytest.mark.parametrize(
    ("manifest", "attempts", "message"),
    [
        (set(), set(), "manifest task set"),
        ({"outside"}, {"outside"}, "manifest task set"),
        ({"a"}, {"a", "b"}, "attempt task set"),
    ],
)
def test_rebuild_fails_closed_on_unbound_or_mismatched_tasks(manifest, attempts, message):
    with pytest.raises(ValueError, match=message):
        _module().validate_task_binding(
            suite_task_ids={"a", "b"},
            manifest_task_ids=manifest,
            attempt_task_ids=attempts,
        )
