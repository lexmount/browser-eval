import json
from pathlib import Path
from unittest.mock import patch

from harness.web_fetch.models import EvalCase
from harness.web_fetch.providers.cloudflare import CloudflareMarkdownProvider
from harness.web_fetch.providers.firecrawl import FirecrawlProvider
from harness.web_fetch.providers.http_client import HttpResponse
from harness.web_fetch.providers.lexmount import LexmountProvider


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


def test_release_configs_select_firecrawl_actions_route():
    for filename in ("configs/providers.example.json", "configs/providers.resources.json"):
        providers = json.loads(Path(filename).read_text())["providers"]
        assert providers["firecrawl"]["dynamic_wait_strategy"] == "actions"
        assert providers["firecrawl-resources"]["dynamic_wait_strategy"] == "actions"
        assert providers["firecrawl"]["proxy"] == "auto"
        assert providers["firecrawl-resources"]["proxy"] == "auto"
        assert providers["firecrawl"]["capacity_credits_per_call"] == 1
        assert providers["firecrawl-resources"]["capacity_credits_per_call"] == 1


@patch("harness.web_fetch.providers.lexmount.request")
def test_lexmount_dynamic_uses_all_in_one_extract_with_domstable_engine(mock_request):
    mock_request.return_value = HttpResponse(
        200,
        "u",
        {},
        json.dumps({
            "request_id": "req-1",
            "result": {
                "main_text": "Example rendered rows",
                "final_url": "https://example.com/dynamic",
                "engine": "lightmount_domstable",
                "dom_id": "dom-1",
            },
            "metadata": {"server_elapsed_ms": 1234},
        }).encode(),
    )

    result = LexmountProvider(
        "lexmount-global", {"api_key": "x", "project_id": "p"}
    ).fetch(dynamic_case(), 45)

    assert mock_request.call_args.args[0] == "https://api.lexmount.com/v1/extract"
    assert mock_request.call_args.kwargs["json_body"] == {
        "extract": {
            "url": "https://example.com/dynamic",
            "options": {
                "engine_preference": "lightmount_domstable",
                "timeout_ms": 30_000,
            },
        }
    }
    assert mock_request.call_args.kwargs["timeout"] == 40.5
    assert result.ok is True
    assert result.content == "Example rendered rows"
    assert result.metadata["route"] == "v1_extract_dynamic"
    assert result.metadata["requested_engine"] == "lightmount_domstable"
    assert result.metadata["wait"] == {
        "strategy": "dom_stability",
        "engine_preference": "lightmount_domstable",
        "selector": ".row",
        "frame_selector": None,
        "minimum_count": 10,
        "selector_enforced": False,
        "minimum_count_enforced": False,
        "completion_support": "approximate",
        "unsupported": ["selector", "minimum_count", "post_completion_delay"],
        "delay_ms": 250,
        "timeout_ms": 30_000,
        "wait_until": "domcontentloaded",
    }


@patch("harness.web_fetch.providers.lexmount.request")
def test_lexmount_dynamic_timeout_is_capped_by_client_deadline(mock_request):
    mock_request.return_value = HttpResponse(
        200, "u", {}, b'{"result":{"main_text":"Example","engine":"lightmount_domstable"}}'
    )

    result = LexmountProvider(
        "lexmount-global", {"api_key": "x", "project_id": "p"}
    ).fetch(dynamic_case(timeout_ms=60_000), 12.5)

    extract = mock_request.call_args.kwargs["json_body"]["extract"]
    assert extract["options"]["timeout_ms"] == 11_250
    assert result.metadata["wait"]["timeout_ms"] == 11_250


@patch("harness.web_fetch.providers.lexmount.request")
def test_lexmount_rejects_non_browser_dynamic_engine(mock_request):
    result = LexmountProvider(
        "lexmount-global",
        {"api_key": "x", "project_id": "p", "dynamic_engine": "http"},
    ).fetch(dynamic_case(), 45)

    assert result.error_type == "configuration"
    assert result.error_message == (
        "Lexmount dynamic_engine must be chrome, chrome_cdp, "
        "lightmount_dcl or lightmount_domstable"
    )
    mock_request.assert_not_called()


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
        "selector_requested": True,
        "selector_enforced": True,
        "minimum_count_enforced": False,
        "actions_accepted": True,
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
    assert result.metadata["wait"]["actions_accepted"] is True
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
    assert wait["actions_accepted"] is True
    assert wait["completion_support"] == "approximate"
    assert wait["unsupported"] == [
        "wait_until",
        "child_frame_selector",
        "selector",
        "minimum_count",
    ]


@patch("harness.web_fetch.providers.firecrawl.request")
def test_firecrawl_does_not_claim_rejected_actions_were_enforced(mock_request):
    mock_request.return_value = HttpResponse(
        400, "u", {}, b'{"success":false,"error":"invalid action"}'
    )

    result = FirecrawlProvider("firecrawl", {"api_key": "x"}).fetch(
        dynamic_case(minimum_count=1), 45
    )

    wait = result.metadata["wait"]
    assert wait["selector_requested"] is True
    assert wait["actions_accepted"] is False
    assert wait["selector_enforced"] is False
    assert wait["minimum_count_enforced"] is False


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


@patch("harness.web_fetch.providers.cloudflare.request")
def test_kitesurf_native_dynamic_completion_uses_ordinary_url_route(mock_request):
    from dataclasses import replace
    mock_request.return_value = HttpResponse(200, "u", {}, b'{"success":true,"result":"Example"}')
    case = replace(dynamic_case(), retrieval_hints={"version": 3, "mode": "native"})
    result = CloudflareMarkdownProvider("kitesurf", {
        "api_token": "x", "account_id": "a", "browser": "kitesurf"
    }).fetch(case, 45)
    assert result.ok
    body = mock_request.call_args.kwargs["json_body"]
    assert body["url"] == case.url
    assert body["gotoOptions"]["waitUntil"] == "networkidle2"
    assert "waitForSelector" not in body
    assert "waitForTimeout" not in body
    assert result.metadata["route"] == "browser_rendering_markdown"


@patch("harness.web_fetch.providers.cloudflare.request")
def test_kitesurf_maps_shared_completion_but_discloses_missing_frame_output(mock_request):
    from dataclasses import replace
    mock_request.return_value = HttpResponse(200, "u", {}, b'{"success":true,"result":"outer page"}')
    case = replace(dynamic_case(), retrieval_hints={
        "version": 3, "mode": "native", "include_subdocuments": True,
        "completion": {"frame_selector": "iframe", "selector": ".record",
                       "minimum_count": 2},
    })
    result = CloudflareMarkdownProvider("kitesurf", {
        "api_token": "x", "account_id": "a", "browser": "kitesurf"
    }).fetch(case, 45)
    assert result.ok
    body = mock_request.call_args.kwargs["json_body"]
    assert "waitForTimeout" not in body
    assert "waitForSelector" not in body
    assert result.metadata["wait"]["completion_support"] == "approximate"
    assert result.metadata["wait"]["subdocuments"] == {
        "requested": True, "support": "unsupported", "harness_content_assembly": False}


@patch("harness.web_fetch.providers.cloudflare.request")
def test_kitesurf_shared_completion_keeps_product_default_internal_timers(mock_request):
    from dataclasses import replace
    mock_request.return_value = HttpResponse(200, "u", {}, b'{"success":true,"result":"records"}')
    case = replace(dynamic_case(), retrieval_hints={
        "version": 3, "mode": "native",
        "completion": {"selector": ".record", "minimum_count": 10},
    })
    result = CloudflareMarkdownProvider("kitesurf", {
        "api_token": "x", "account_id": "a", "browser": "kitesurf",
        "uniform_native_parameters": True,
    }).fetch(case, 45)
    assert result.ok
    body = mock_request.call_args.kwargs["json_body"]
    assert body["waitForSelector"] == {"selector": ".record"}
    assert "timeout" not in body["gotoOptions"]
    assert "actionTimeout" not in body
    assert result.metadata["wait"]["timeout_ms"] is None
