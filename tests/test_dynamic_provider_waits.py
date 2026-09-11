import json
from pathlib import Path
from unittest.mock import patch

from harness.web_fetch.models import EvalCase
from harness.web_fetch.providers.cloudflare import CloudflareMarkdownProvider
from harness.web_fetch.providers.firecrawl import FirecrawlProvider
from harness.web_fetch.providers.http_client import HttpResponse


def dynamic_case(**completion_overrides):
    completion = {
        "selector": ".row",
        "minimum_count": 10,
        "timeout_ms": 30_000,
        "delay_ms": 250,
    }
    completion.update(completion_overrides)
    return EvalCase(
        id="dynamic-case",
        name="Dynamic case",
        url="https://example.com/dynamic",
        category="dynamic",
        expected_any=["Example"],
        track="dynamic",
        retrieval_hints={
            "version": 1,
            "wait_until": "domcontentloaded",
            "completion": completion,
        },
    )


def test_release_configs_select_firecrawl_wait_for_route():
    for filename in ("configs/providers.example.json", "configs/providers.resources.json"):
        providers = json.loads(Path(filename).read_text())["providers"]
        assert providers["firecrawl"]["dynamic_wait_strategy"] == "wait_for"
        assert providers["firecrawl-resources"]["dynamic_wait_strategy"] == "wait_for"
        assert providers["firecrawl"]["proxy"] == "auto"
        assert providers["firecrawl-resources"]["proxy"] == "auto"
        assert providers["firecrawl"]["capacity_credits_per_call"] == 1
        assert providers["firecrawl-resources"]["capacity_credits_per_call"] == 1


@patch("harness.web_fetch.providers.cloudflare.request")
def test_cloudflare_uses_native_selector_without_racing_it_against_delay(mock_request):
    mock_request.return_value = HttpResponse(200, "u", {}, b'{"success":true,"result":"Example"}')
    result = CloudflareMarkdownProvider(
        "cloudflare-markdown", {"api_token": "x", "account_id": "a"}
    ).fetch(dynamic_case(), 45)

    body = mock_request.call_args.kwargs["json_body"]
    assert body["gotoOptions"] == {
        "waitUntil": "domcontentloaded",
        "timeout": 30_000,
    }
    assert body["actionTimeout"] == 30_000
    assert body["waitForSelector"] == {"selector": ".row", "timeout": 30_000}
    assert "waitForTimeout" not in body
    assert "Example" not in str(body)
    assert result.metadata["route"] == "browser_rendering_markdown_dynamic"
    assert result.metadata["wait"]["strategy"] == "selector"
    assert result.metadata["wait"]["minimum_count"] == 10
    assert result.metadata["wait"]["minimum_count_enforced"] is False
    assert result.metadata["wait"]["completion_support"] == "approximate"
    assert result.metadata["wait"]["unsupported"] == [
        "minimum_count",
        "post_selector_delay",
    ]


@patch("harness.web_fetch.providers.cloudflare.request")
def test_cloudflare_maps_networkidle_and_caps_provider_timeout(mock_request):
    mock_request.return_value = HttpResponse(200, "u", {}, b'{"success":true,"result":"Example"}')
    case = dynamic_case(timeout_ms=60_000)
    case = EvalCase(
        **{
            **case.__dict__,
            "retrieval_hints": {**case.retrieval_hints, "wait_until": "networkidle"},
        }
    )
    CloudflareMarkdownProvider("cloudflare-markdown", {"api_token": "x", "account_id": "a"}).fetch(
        case, 12.5
    )
    body = mock_request.call_args.kwargs["json_body"]
    assert body["gotoOptions"] == {"waitUntil": "networkidle2", "timeout": 11_250}
    assert body["actionTimeout"] == 11_250


@patch("harness.web_fetch.providers.cloudflare.request")
def test_cloudflare_caps_declared_completion_at_actual_inner_http_cutoff(mock_request):
    mock_request.return_value = HttpResponse(200, "u", {}, b'{"success":true,"result":"Example"}')
    result = CloudflareMarkdownProvider(
        "cloudflare-markdown", {"api_token": "x", "account_id": "a"}
    ).fetch(dynamic_case(timeout_ms=60_000), 45)

    body = mock_request.call_args.kwargs["json_body"]
    assert mock_request.call_args.kwargs["timeout"] == 40.5
    assert body["gotoOptions"]["timeout"] == 40_500
    assert body["actionTimeout"] == 40_500
    assert body["waitForSelector"]["timeout"] == 40_500
    assert result.metadata["wait"]["timeout_ms"] == 40_500
    assert result.metadata["wait"]["client_http_timeout_ms"] == 40_500


@patch("harness.web_fetch.providers.firecrawl.request")
def test_firecrawl_uses_selector_action_then_declared_settle_delay(mock_request):
    mock_request.return_value = HttpResponse(
        200, "u", {}, b'{"success":true,"data":{"markdown":"Example"}}'
    )
    result = FirecrawlProvider("firecrawl", {"api_key": "x"}).fetch(dynamic_case(), 45)

    body = mock_request.call_args.kwargs["json_body"]
    assert body["actions"] == [
        {"type": "wait", "selector": ".row"},
        {"type": "wait", "milliseconds": 250},
    ]
    assert body["timeout"] == 30_000
    assert "Example" not in str(body)
    assert result.metadata["route"] == "v2_scrape_dynamic"
    assert result.metadata["requested_formats"] == ["markdown"]
    assert result.metadata["requested_proxy"] == "auto"
    assert result.metadata["requested_dynamic_wait_strategy"] == "actions"
    assert result.metadata["requested_cache_max_age_ms"] == 0
    assert result.metadata["requested_parsers"] is None
    assert result.metadata["wait"] == {
        "strategy": "selector_action_then_settle_delay",
        "selector": ".row",
        "frame_selector": None,
        "minimum_count": 10,
        "selector_enforced": True,
        "minimum_count_enforced": False,
        "completion_support": "approximate",
        "unsupported": ["wait_until", "minimum_count"],
        "delay_ms": 250,
        "timeout_ms": 30_000,
        "wait_until": "domcontentloaded",
        "smart_wait": True,
    }


@patch("harness.web_fetch.providers.firecrawl.request")
def test_firecrawl_request_is_unchanged_by_unsupported_text_fragment_diagnostic(mock_request):
    mock_request.return_value = HttpResponse(
        200, "u", {}, b'{"success":true,"data":{"markdown":"Example"}}'
    )
    provider = FirecrawlProvider(
        "firecrawl", {"api_key": "x", "dynamic_wait_strategy": "wait_for"}
    )
    provider.fetch(dynamic_case(), 45)
    before = mock_request.call_args.kwargs["json_body"]
    provider.fetch(dynamic_case(minimum_text_fragments=2), 45)
    after = mock_request.call_args.kwargs["json_body"]

    assert before == after


@patch("harness.web_fetch.providers.firecrawl.request")
def test_firecrawl_selector_wait_can_enforce_single_top_level_match(mock_request):
    mock_request.return_value = HttpResponse(
        200, "u", {}, b'{"success":true,"data":{"markdown":"Example"}}'
    )
    result = FirecrawlProvider("firecrawl", {"api_key": "x"}).fetch(
        dynamic_case(minimum_count=1, delay_ms=0), 45
    )

    body = mock_request.call_args.kwargs["json_body"]
    assert body["actions"] == [{"type": "wait", "selector": ".row"}]
    assert result.metadata["wait"]["minimum_count_enforced"] is True
    assert result.metadata["wait"]["unsupported"] == ["wait_until"]


@patch("harness.web_fetch.providers.firecrawl.request")
def test_firecrawl_wait_for_route_avoids_region_restricted_actions(mock_request):
    mock_request.return_value = HttpResponse(
        200, "u", {}, b'{"success":true,"data":{"markdown":"Example"}}'
    )
    result = FirecrawlProvider(
        "firecrawl", {"api_key": "x", "dynamic_wait_strategy": "wait_for"}
    ).fetch(dynamic_case(), 45)

    body = mock_request.call_args.kwargs["json_body"]
    assert body["waitFor"] == 250
    assert "actions" not in body
    assert body["timeout"] == 30_000
    assert result.metadata["route"] == "v2_scrape_dynamic_wait_for"
    assert result.metadata["requested_formats"] == ["markdown"]
    assert result.metadata["requested_proxy"] == "auto"
    assert result.metadata["requested_dynamic_wait_strategy"] == "wait_for"
    assert result.metadata["requested_cache_max_age_ms"] == 0
    assert result.metadata["requested_parsers"] is None
    assert result.metadata["wait"] == {
        "strategy": "predeclared_settle_delay",
        "selector": ".row",
        "frame_selector": None,
        "minimum_count": 10,
        "selector_enforced": False,
        "minimum_count_enforced": False,
        "completion_support": "approximate",
        "unsupported": ["wait_until", "selector", "minimum_count"],
        "delay_ms": 250,
        "requested_delay_ms": 250,
        "timeout_ms": 30_000,
        "wait_until": "domcontentloaded",
        "smart_wait": True,
    }


@patch("harness.web_fetch.providers.firecrawl.request")
def test_firecrawl_wait_for_delay_is_bounded_by_inner_deadline(mock_request):
    mock_request.return_value = HttpResponse(
        200, "u", {}, b'{"success":true,"data":{"markdown":"Example"}}'
    )
    result = FirecrawlProvider(
        "firecrawl", {"api_key": "x", "dynamic_wait_strategy": "wait_for"}
    ).fetch(dynamic_case(timeout_ms=30_000, delay_ms=10_000), 2)

    body = mock_request.call_args.kwargs["json_body"]
    assert mock_request.call_args.kwargs["timeout"] == 1.0
    assert body["timeout"] == 1000
    assert body["waitFor"] == 999
    assert result.metadata["wait"]["delay_ms"] == 999
    assert result.metadata["wait"]["requested_delay_ms"] == 10_000


@patch("harness.web_fetch.providers.firecrawl.request")
def test_firecrawl_rejects_unknown_dynamic_wait_strategy(mock_request):
    result = FirecrawlProvider(
        "firecrawl", {"api_key": "x", "dynamic_wait_strategy": "selector"}
    ).fetch(dynamic_case(), 45)

    assert result.error_type == "configuration"
    assert result.error_message == (
        "Firecrawl dynamic_wait_strategy must be actions or wait_for"
    )
    mock_request.assert_not_called()


@patch("harness.web_fetch.providers.firecrawl.request")
def test_firecrawl_iframe_condition_discloses_unenforced_selector_and_count(mock_request):
    mock_request.return_value = HttpResponse(
        200, "u", {}, b'{"success":true,"data":{"markdown":"Example"}}'
    )
    result = FirecrawlProvider("firecrawl", {"api_key": "x"}).fetch(
        dynamic_case(
            frame_selector="iframe#iframe",
            selector="h3.family-name",
            minimum_count=14,
            delay_ms=3000,
        ),
        45,
    )

    body = mock_request.call_args.kwargs["json_body"]
    assert body["actions"] == [{"type": "wait", "milliseconds": 3000}]
    assert "h3.family-name" not in str(body)
    wait = result.metadata["wait"]
    assert wait["selector_enforced"] is False
    assert wait["minimum_count_enforced"] is False
    assert wait["completion_support"] == "approximate"
    assert wait["unsupported"] == [
        "wait_until",
        "child_frame_selector",
        "selector",
        "minimum_count",
    ]


@patch("harness.web_fetch.providers.firecrawl.request")
def test_firecrawl_server_timeout_never_exceeds_reserved_http_deadline(mock_request):
    mock_request.return_value = HttpResponse(
        200, "u", {}, b'{"success":true,"data":{"markdown":"Example"}}'
    )
    FirecrawlProvider("firecrawl", {"api_key": "x"}).fetch(
        dynamic_case(timeout_ms=60_000), 45
    )

    assert mock_request.call_args.kwargs["timeout"] == 40.5
    assert mock_request.call_args.kwargs["json_body"]["timeout"] == 40_500


@patch("harness.web_fetch.providers.firecrawl.request")
@patch("harness.web_fetch.providers.cloudflare.request")
def test_dynamic_providers_fail_closed_without_hints(cloudflare_request, firecrawl_request):
    case = EvalCase(
        id="missing-hints",
        name="Missing hints",
        url="https://example.com/dynamic",
        category="dynamic",
        expected_any=["Example"],
        track="dynamic",
    )
    cloudflare = CloudflareMarkdownProvider(
        "cloudflare-markdown", {"api_token": "x", "account_id": "a"}
    ).fetch(case, 45)
    firecrawl = FirecrawlProvider("firecrawl", {"api_key": "x"}).fetch(case, 45)

    assert cloudflare.error_type == "configuration"
    assert firecrawl.error_type == "configuration"
    cloudflare_request.assert_not_called()
    firecrawl_request.assert_not_called()


@patch("harness.web_fetch.providers.cloudflare.request")
def test_cloudflare_iframe_wait_is_explicitly_approximate(mock_request):
    mock_request.return_value = HttpResponse(200, "u", {}, b'{"success":true,"result":"Example"}')
    case = dynamic_case(
        frame_selector="iframe#iframe",
        selector="h3.family-name",
        minimum_count=14,
        delay_ms=3000,
    )
    result = CloudflareMarkdownProvider(
        "cloudflare-markdown", {"api_token": "x", "account_id": "a"}
    ).fetch(case, 45)

    body = mock_request.call_args.kwargs["json_body"]
    assert "waitForSelector" not in body
    assert body["waitForTimeout"] == 3000
    assert "h3.family-name" not in str(body)
    assert result.metadata["wait"]["completion_support"] == "approximate"
    assert result.metadata["wait"]["unsupported"] == [
        "child_frame_selector",
        "selector",
        "minimum_count",
    ]


@patch("harness.web_fetch.providers.firecrawl.request")
@patch("harness.web_fetch.providers.cloudflare.request")
def test_dynamic_providers_reject_answer_bearing_hint_fields(cloudflare_request, firecrawl_request):
    case = dynamic_case()
    case = EvalCase(
        **{
            **case.__dict__,
            "retrieval_hints": {**case.retrieval_hints, "expected_answer": "Example"},
        }
    )

    cloudflare = CloudflareMarkdownProvider(
        "cloudflare-markdown", {"api_token": "x", "account_id": "a"}
    ).fetch(case, 45)
    firecrawl = FirecrawlProvider("firecrawl", {"api_key": "x"}).fetch(case, 45)

    assert cloudflare.error_type == "configuration"
    assert firecrawl.error_type == "configuration"
    cloudflare_request.assert_not_called()
    firecrawl_request.assert_not_called()
