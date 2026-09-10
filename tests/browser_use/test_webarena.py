from __future__ import annotations

import json
import zipfile

import pytest

from harness.browser_use.suite import load_suite
from harness.browser_use.webarena import (
    build_trace_catalog,
    import_playwright_trace,
    normalize_playwright_selector,
    parse_drive_folder_html,
)


def test_canonical_public_suite_contains_every_recorded_candidate():
    suite = load_suite("evaluation/browser-use-public/webarena-human-suite.json")

    assert len(suite.tasks) == 179
    assert sum(task.step_count for task in suite.tasks) == 3400
    assert [task.task_id for task in suite.tasks if not task.tool_calls] == [
        "webarena-300",
        "webarena-431",
    ]
    qualification_counts = {
        status: sum(
            task.ground_truth["qualification"]["status"] == status
            for task in suite.tasks
        )
        for status in ("eligible", "skipped")
    }
    assert qualification_counts == {"eligible": 19, "skipped": 160}
    response_builders = [task.ground_truth.get("response_builder") for task in suite.tasks]
    assert sum(builder is None for builder in response_builders) == 70
    assert sum(
        isinstance(builder, dict) and builder.get("kind") == "null_after_verified_path"
        for builder in response_builders
    ) == 98
    assert sum(
        isinstance(builder, dict) and builder.get("task_type") == "mutate"
        for builder in response_builders
    ) == 70
    assert sum(
        isinstance(builder, dict) and builder.get("task_type") == "navigate"
        for builder in response_builders
    ) == 28
    payload = json.loads(
        open("evaluation/browser-use-public/webarena-human-suite.json", encoding="utf-8").read()
    )
    assert payload["benchmark"]["qualification"] == {
        "schema": "lex_browser_use_qualification/1",
        "qualified_count": 19,
        "skipped_count": 160,
    }
    assert payload["benchmark"]["structural_import"] == {
        "action_count": 3394,
        "action_counts": {"check": 14, "click": 1980, "fill": 411, "press": 874, "select": 115},
        "recovered_input_counts": {"clipboard_paste": 3, "manual_typing": 408},
        "unmapped_api_counts": {},
        "zero_action_task_ids": [300, 431],
        "replay_qualified_count": 0,
    }


def test_official_folder_parser_requires_all_179_unique_traces():
    html = "".join(
        f'<a href="https://drive.google.com/file/d/{"x" * 25}{index}/view">{index}.trace.zip</a>'
        for index in range(179)
    )

    files = parse_drive_folder_html(html)

    assert len(files) == 179
    assert files[0].task_id == 0
    assert files[-1].task_id == 178


def test_official_folder_parser_fails_closed_when_listing_is_partial():
    html = f'<a href="https://drive.google.com/file/d/{"x" * 25}/view">4.trace.zip</a>'

    with pytest.raises(ValueError, match="must contain 179"):
        parse_drive_folder_html(html)


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        (
            'internal:role=link[name="Orders"i]',
            {"by": "role", "role": "link", "name": "Orders", "exact": False},
        ),
        (
            'internal:label="Period"s',
            {"by": "label", "value": "Period", "exact": True},
        ),
        (
            'internal:role=button[name="First line\nSecond line"s]',
            {
                "by": "role",
                "role": "button",
                "name": "First line\nSecond line",
                "exact": True,
            },
        ),
        (
            "#form >> internal:role=button",
            {"by": "playwright", "value": "#form >> internal:role=button"},
        ),
        ("#submit", {"by": "css", "value": "#submit"}),
    ],
)
def test_playwright_selectors_are_normalized_without_losing_compound_selectors(selector, expected):
    assert normalize_playwright_selector(selector) == expected


def test_trace_import_preserves_actions_tabs_context_and_recorded_failure(tmp_path):
    trace = tmp_path / "4.trace.zip"
    rows = [
        {
            "type": "context-options",
            "browserName": "chromium",
            "options": {"viewport": {"width": 1280, "height": 720}, "deviceScaleFactor": 2},
        },
        {
            "type": "event",
            "class": "Page",
            "method": "__create__",
            "guid": "page-one",
        },
        {
            "type": "before",
            "callId": "one",
            "apiName": "frame.click",
            "pageId": "page-one",
            "params": {"selector": 'internal:role=button[name="Save"i]'},
        },
        {
            "type": "frame-snapshot",
            "snapshot": {
                "callId": "one",
                "snapshotName": "before@one",
                "pageId": "page-one",
                "frameUrl": "http://old.example:8023/project",
                "html": ["HTML", {}, []],
            },
        },
        {"type": "after", "callId": "one"},
        {
            "type": "before",
            "callId": "two",
            "apiName": "frame.fill",
            "pageId": "page-two",
            "params": {"selector": "#query", "value": "hello"},
        },
        {
            "type": "frame-snapshot",
            "snapshot": {
                "callId": "two",
                "snapshotName": "before@two",
                "pageId": "page-two",
                "frameUrl": "http://old.example:9999/f/technology",
                "html": ["HTML", {}, []],
            },
        },
        {"type": "after", "callId": "two", "error": {"message": "detached"}},
    ]
    with zipfile.ZipFile(trace, "w") as archive:
        archive.writestr("trace.trace", "".join(json.dumps(row) + "\n" for row in rows))

    result = import_playwright_trace(trace)

    assert result["recorded_context"] == {
        "browser_name": "chromium",
        "viewport": {"width": 1280, "height": 720},
        "device_scale_factor": 2,
        "tab_count": 2,
    }
    assert [item["action"] for item in result["actions"]] == ["click", "fill"]
    assert [item["tab_index"] for item in result["actions"]] == [0, 1]
    assert result["recorded_tab_urls"] == [
        "http://old.example:8023/project",
        "http://old.example:9999/f/technology",
    ]
    assert result["actions"][1]["source"]["recorded_status"] == "fail"


def test_trace_import_preserves_multi_select_values(tmp_path):
    trace = tmp_path / "4.trace.zip"
    rows = [
        {
            "type": "before",
            "callId": "one",
            "apiName": "frame.selectOption",
            "params": {
                "selector": "#status",
                "values": [{"value": "closed"}, {"value": "complete"}],
            },
        },
        {"type": "after", "callId": "one"},
    ]
    with zipfile.ZipFile(trace, "w") as archive:
        archive.writestr("trace.trace", "".join(json.dumps(row) + "\n" for row in rows))

    result = import_playwright_trace(trace)

    assert result["actions"][0]["value"] == ["closed", "complete"]


def test_trace_import_keeps_empty_recording_as_repair_candidate(tmp_path):
    trace = tmp_path / "300.trace.zip"
    with zipfile.ZipFile(trace, "w") as archive:
        archive.writestr(
            "trace.trace",
            json.dumps(
                {
                    "type": "context-options",
                    "browserName": "chromium",
                    "options": {"viewport": {"width": 1280, "height": 720}},
                }
            )
            + "\n",
        )

    result = import_playwright_trace(trace)

    assert result["actions"] == []


def test_trace_import_recovers_clipboard_paste_from_input_snapshot(tmp_path):
    trace = tmp_path / "454.trace.zip"
    rows = [
        {
            "type": "before",
            "callId": "paste",
            "apiName": "frame.press",
            "params": {"selector": "#search", "key": "Control+v"},
        },
        {
            "type": "frame-snapshot",
            "snapshot": {
                "callId": "paste",
                "snapshotName": "input@paste",
                "html": [
                    "INPUT",
                    {
                        "id": "search",
                        "__playwright_target__": "paste",
                        "__playwright_value_": "Cronus yoga",
                    },
                ],
            },
        },
        {"type": "after", "callId": "paste"},
    ]
    with zipfile.ZipFile(trace, "w") as archive:
        archive.writestr("trace.trace", "".join(json.dumps(row) + "\n" for row in rows))

    result = import_playwright_trace(trace)

    assert result["actions"][0]["action"] == "fill"
    assert result["actions"][0]["text"] == "Cronus yoga"
    assert result["actions"][0]["source"]["recovered_from_input_snapshot"] is True


def test_trace_import_recovers_manual_typing_before_press_without_resetting_cursor(tmp_path):
    trace = tmp_path / "125.trace.zip"
    rows = [
        {
            "type": "before",
            "callId": "left-one",
            "apiName": "frame.press",
            "pageId": "page-one",
            "beforeSnapshot": "before@left-one",
            "params": {"selector": "#search", "key": "ArrowLeft"},
        },
        {
            "type": "frame-snapshot",
            "snapshot": {
                "callId": "left-one",
                "snapshotName": "before@left-one",
                "pageId": "page-one",
                "frameUrl": "http://shopping/",
                "html": [
                    "INPUT",
                    {
                        "id": "search",
                        "__playwright_target__": "left-one",
                        "__playwright_value_": "teeth grinding mouth guard",
                    },
                ],
            },
        },
        {"type": "after", "callId": "left-one"},
        {
            "type": "before",
            "callId": "left-two",
            "apiName": "frame.press",
            "pageId": "page-one",
            "beforeSnapshot": "before@left-two",
            "params": {"selector": "#search", "key": "ArrowLeft"},
        },
        {
            "type": "frame-snapshot",
            "snapshot": {
                "callId": "left-two",
                "snapshotName": "before@left-two",
                "pageId": "page-one",
                "frameUrl": "http://shopping/",
                "html": [
                    "INPUT",
                    {
                        "id": "search",
                        "__playwright_target__": "left-two",
                        "__playwright_value_": "teeth grinding mouth guard",
                    },
                ],
            },
        },
        {"type": "after", "callId": "left-two"},
    ]
    with zipfile.ZipFile(trace, "w") as archive:
        archive.writestr("trace.trace", "".join(json.dumps(row) + "\n" for row in rows))

    result = import_playwright_trace(trace)

    assert [action["action"] for action in result["actions"]] == ["fill", "press", "press"]
    assert result["actions"][0]["text"] == "teeth grinding mouth guard"
    assert result["actions"][0]["source"]["recovered_manual_input_from_snapshot"] is True


def test_trace_import_recovers_manual_typing_before_submit_click(tmp_path):
    trace = tmp_path / "search.trace.zip"
    rows = [
        {
            "type": "before",
            "callId": "focus-search",
            "apiName": "frame.click",
            "pageId": "page-one",
            "params": {"selector": "#search"},
        },
        {"type": "after", "callId": "focus-search"},
        {
            "type": "before",
            "callId": "submit",
            "apiName": "frame.click",
            "pageId": "page-one",
            "beforeSnapshot": "before@submit",
            "params": {"selector": "button[type=submit]"},
        },
        {
            "type": "frame-snapshot",
            "snapshot": {
                "callId": "submit",
                "snapshotName": "before@submit",
                "pageId": "page-one",
                "frameUrl": "http://shopping/",
                "html": [
                    "FORM",
                    {},
                    [
                        "INPUT",
                        {
                            "id": "search",
                            "type": "text",
                            "__playwright_target__": "focus-search",
                            "__playwright_value_": "trail shoes",
                        },
                    ],
                ],
            },
        },
        {"type": "after", "callId": "submit"},
    ]
    with zipfile.ZipFile(trace, "w") as archive:
        archive.writestr("trace.trace", "".join(json.dumps(row) + "\n" for row in rows))

    result = import_playwright_trace(trace)

    assert [action["action"] for action in result["actions"]] == ["click", "fill", "click"]
    assert result["actions"][1]["target"] == {"by": "css", "value": "#search"}
    assert result["actions"][1]["text"] == "trail shoes"


def test_catalog_joins_every_trace_to_both_upstream_definitions():
    html = "".join(
        f'<a href="https://drive.google.com/file/d/{"x" * 25}{index}/view">{index}.trace.zip</a>'
        for index in range(179)
    )
    files = parse_drive_folder_html(html)
    original = [{"task_id": index, "intent": f"old {index}"} for index in range(179)]
    verified = [
        {
            "task_id": index,
            "intent": f"new {index}",
            "intent_template_id": index,
            "sites": ["reddit"],
            "start_urls": ["__REDDIT__"],
            "revision": 2,
            "eval": [],
        }
        for index in range(179)
    ]

    result = build_trace_catalog(
        files,
        original_tasks=original,
        verified_tasks=verified,
        original_commit="a" * 40,
        verified_commit="b" * 40,
    )

    assert result["count"] == 179
    assert result["traces"][42]["start_urls"] == ["__REDDIT__"]
    assert result["scope"]["recorded_does_not_imply_successful_gold_path"] is True
