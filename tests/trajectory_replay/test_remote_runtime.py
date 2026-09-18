import json

import pytest
from webarena_verified.types.task import WebArenaSite

from harness.trajectory_replay.remote_runtime import (
    file_sha256,
    load_remote_sites,
    rebind_remote_calls,
    validate_remote_cohort_reset_receipt,
    validate_remote_reset_receipt,
)


def _site_map(tmp_path):
    path = tmp_path / "sites.json"
    path.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_remote_sites/1",
                "sites": {
                    "shopping": "http://shop.example/",
                    "shopping_admin": "http://admin.example/admin",
                    "reddit": "http://reddit.example/",
                    "gitlab": "http://gitlab.example/",
                },
            }
        )
    )
    return path


def test_rebind_remote_calls_preserves_path_query_and_non_site_urls(tmp_path):
    sites = load_remote_sites(_site_map(tmp_path))
    calls = [
        {"action": "navigate", "url": "http://host.docker.internal:7780/admin/catalog/edit?id=3"},
        {"action": "navigate", "url": "http://host.docker.internal:9999/f/books"},
        {"action": "navigate", "url": "http://other.example:7780/admin"},
        {
            "action": "click",
            "selector": 'a[href="http://host.docker.internal:7780/admin/sales/order/?p=2"]:visible',
        },
        {
            "action": "click",
            "selector": (
                ':nth-match(a[href="http://host.docker.internal:7770/review/product/'
                'listAjax/id/38805/?p=2"]:visible, 1)'
            ),
        },
        {"action": "click", "selector": 'a[href="http://other.example:7780/admin"]:visible'},
    ]
    result = rebind_remote_calls(calls, sites, {"host.docker.internal"})
    assert result[0]["url"] == "http://admin.example/admin/catalog/edit?id=3"
    assert result[1]["url"] == "http://reddit.example/f/books"
    assert result[2] == calls[2]
    assert result[3]["selector"] == (
        'a:is([href="http://admin.example/admin/sales/order/?p=2"],'
        '[href="https://admin.example/admin/sales/order/?p=2"]):visible'
    )
    assert result[4]["selector"] == (
        ':nth-match(a:is([href="http://shop.example/review/product/listAjax/id/38805/?p=2"],'
        '[href="https://shop.example/review/product/listAjax/id/38805/?p=2"]):visible, 1)'
    )
    assert result[5] == calls[5]
    assert calls[0]["url"].startswith("http://host.docker.internal")


def test_rebind_url_form_input_without_rewriting_prose_or_external_links(tmp_path):
    calls = [
        {"action": "fill", "text": "http://192.168.5.2:9999/submission_images/a.jpg?x=1#image"},
        {"action": "fill", "text": "See http://192.168.5.2:9999/f/books"},
        {"action": "fill", "text": "https://external.example/image.jpg"},
        {"action": "fill", "text": "plain-text"},
    ]
    result = rebind_remote_calls(calls, load_remote_sites(_site_map(tmp_path)), {"192.168.5.2"})
    assert result[0]["text"] == "http://reddit.example/submission_images/a.jpg?x=1#image"
    assert result[1:] == calls[1:]
    assert calls[0]["text"].startswith("http://192.168.5.2")


def test_remote_reset_requires_pod_replacement_and_readiness(tmp_path):
    site_path = _site_map(tmp_path)
    receipt_path = tmp_path / "reset.json"
    row = {
        "image_digest": "sha256:abc",
        "operation_event_id": "event-1",
        "old_pod": "old",
        "new_pod": "new",
        "operation_status": "success",
        "writable_layer_marker": "absent",
        "env_ctrl_ready": True,
        "public_http_status": 200,
        "elasticsearch_status": "yellow",
    }
    receipt = {
        "schema": "lex_webarena_remote_reset/1",
        "site_map_sha256": file_sha256(site_path),
        "task_id": 464,
        "app_id": 195,
        "sites": {"shopping_admin": row},
    }
    receipt_path.write_text(json.dumps(receipt))
    assert (
        validate_remote_reset_receipt(
            receipt_path,
            required_sites={WebArenaSite.SHOPPING_ADMIN},
            site_map_sha256=file_sha256(site_path),
        )
        == receipt
    )
    receipt["sites"]["shopping_admin"]["new_pod"] = "old"
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="not proven"):
        validate_remote_reset_receipt(
            receipt_path,
            required_sites={WebArenaSite.SHOPPING_ADMIN},
            site_map_sha256=file_sha256(site_path),
        )


def test_cohort_reset_requires_all_replaced_pods_and_clean_baseline(tmp_path):
    site_path = _site_map(tmp_path)
    receipt_path = tmp_path / "cohort-reset.json"
    row = {
        "service_id": "service",
        "image_digest": "sha256:abc",
        "event_id": "event",
        "event_status": "success",
        "old_pod": "old",
        "new_pod": "new",
        "data_volume_count": 0,
        "public_http_status": 200,
    }
    receipt = {
        "schema": "webarena_remote_cohort_reset/1",
        "app_id": 195,
        "site_map_sha256": file_sha256(site_path),
        "reset_scope": "once_before_original36_candidate_cohort",
        "site_reset_between_tasks": False,
        "sites": {
            site.value: dict(row)
            for site in (
                WebArenaSite.SHOPPING,
                WebArenaSite.SHOPPING_ADMIN,
                WebArenaSite.REDDIT,
                WebArenaSite.GITLAB,
            )
        },
    }
    receipt["sites"]["reddit"]["baseline_check"] = {"row_count": 0}
    receipt["sites"]["gitlab"]["baseline_check"] = {"row_count": 0}
    receipt_path.write_text(json.dumps(receipt))
    kwargs = {
        "site_map_sha256": file_sha256(site_path),
        "app_id": 195,
        "reset_scope": "once_before_original36_candidate_cohort",
    }
    assert validate_remote_cohort_reset_receipt(receipt_path, **kwargs) == receipt
    receipt["sites"]["reddit"]["baseline_check"]["row_count"] = 1
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="baseline is not proven"):
        validate_remote_cohort_reset_receipt(receipt_path, **kwargs)


def test_cohort_reset_requires_matching_https_admin_configuration(tmp_path):
    site_path = _site_map(tmp_path)
    receipt_path = tmp_path / "cohort-reset.json"
    row = {
        "service_id": "service",
        "image_digest": "sha256:abc",
        "event_id": "event",
        "event_status": "success",
        "old_pod": "old",
        "new_pod": "new",
        "data_volume_count": 0,
        "public_http_status": 200,
    }
    receipt = {
        "schema": "webarena_remote_cohort_reset/1",
        "app_id": 195,
        "site_map_sha256": file_sha256(site_path),
        "reset_scope": "once_before_original36_candidate_cohort",
        "site_reset_between_tasks": False,
        "sites": {
            site.value: dict(row)
            for site in (
                WebArenaSite.SHOPPING,
                WebArenaSite.SHOPPING_ADMIN,
                WebArenaSite.REDDIT,
                WebArenaSite.GITLAB,
            )
        },
    }
    receipt["sites"]["reddit"]["baseline_check"] = {"row_count": 0}
    receipt["sites"]["gitlab"]["baseline_check"] = {"row_count": 0}
    config = {
        "web_secure_base_url": "https://admin.example/",
        "web_secure_use_in_adminhtml": 1,
        "config_cache_cleaned": True,
        "public_static_resource_scheme": "https",
    }
    receipt["sites"]["shopping_admin"]["https_admin_config"] = config
    receipt_path.write_text(json.dumps(receipt))
    kwargs = {
        "site_map_sha256": file_sha256(site_path),
        "app_id": 195,
        "reset_scope": "once_before_original36_candidate_cohort",
        "admin_https_base_url": "https://admin.example/",
    }
    assert validate_remote_cohort_reset_receipt(receipt_path, **kwargs) == receipt
    for field, invalid in (
        ("web_secure_base_url", "http://admin.example/"),
        ("web_secure_use_in_adminhtml", 0),
        ("config_cache_cleaned", False),
        ("public_static_resource_scheme", "http"),
    ):
        changed = json.loads(json.dumps(receipt))
        changed["sites"]["shopping_admin"]["https_admin_config"][field] = invalid
        receipt_path.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match="HTTPS configuration is not proven"):
            validate_remote_cohort_reset_receipt(receipt_path, **kwargs)


def test_cohort_reset_requires_bound_fast_read_only_site_probes(tmp_path):
    site_path = _site_map(tmp_path)
    receipt_path = tmp_path / "cohort-reset.json"
    row = {
        "service_id": "service",
        "image_digest": "sha256:abc",
        "event_id": "event",
        "event_status": "success",
        "old_pod": "old",
        "new_pod": "new",
        "data_volume_count": 0,
        "public_http_status": 200,
    }
    url = "http://reddit.example/f/DIY/post"
    probe = {
        "site": "reddit",
        "path": "/f/DIY/post",
        "url": url,
        "method": "GET",
        "read_only": True,
        "cold_http_status": 200,
        "cold_ms": 26_000,
        "warm_http_status": 200,
        "warm_ms": 450,
        "stable_http_status": 200,
        "stable_ms": 400,
    }
    receipt = {
        "schema": "webarena_remote_cohort_reset/1",
        "app_id": 195,
        "site_map_sha256": file_sha256(site_path),
        "reset_scope": "once_before_original36_candidate_cohort",
        "site_reset_between_tasks": False,
        "sites": {site.value: dict(row) for site in (
            WebArenaSite.SHOPPING, WebArenaSite.SHOPPING_ADMIN,
            WebArenaSite.REDDIT, WebArenaSite.GITLAB,
        )},
        "site_readiness": [probe],
    }
    receipt["sites"]["reddit"]["baseline_check"] = {"row_count": 0}
    receipt["sites"]["gitlab"]["baseline_check"] = {"row_count": 0}
    receipt_path.write_text(json.dumps(receipt))
    kwargs = {
        "site_map_sha256": file_sha256(site_path),
        "app_id": 195,
        "reset_scope": "once_before_original36_candidate_cohort",
        "required_readiness_probes": [{
            "site": "reddit", "path": "/f/DIY/post", "url": url, "max_stable_ms": 5000,
        }],
    }
    assert validate_remote_cohort_reset_receipt(receipt_path, **kwargs) == receipt
    recovered = json.loads(json.dumps(receipt))
    recovered["site_readiness"][0].update({
        "cold_http_status": 500,
        "recovery_http_status": 200,
        "recovery_ms": 15_000,
    })
    receipt_path.write_text(json.dumps(recovered))
    assert validate_remote_cohort_reset_receipt(receipt_path, **kwargs) == recovered
    timed_out = json.loads(json.dumps(recovered))
    timed_out["site_readiness"][0].update({
        "cold_http_status": 0,
        "cold_error": "timeout",
    })
    receipt_path.write_text(json.dumps(timed_out))
    assert validate_remote_cohort_reset_receipt(receipt_path, **kwargs) == timed_out
    del timed_out["site_readiness"][0]["cold_error"]
    receipt_path.write_text(json.dumps(timed_out))
    with pytest.raises(ValueError, match="site readiness"):
        validate_remote_cohort_reset_receipt(receipt_path, **kwargs)
    del recovered["site_readiness"][0]["recovery_http_status"]
    receipt_path.write_text(json.dumps(recovered))
    with pytest.raises(ValueError, match="site readiness"):
        validate_remote_cohort_reset_receipt(receipt_path, **kwargs)
    for changed_field, invalid in (
        ("url", "http://reddit.example/other"),
        ("read_only", False),
        ("warm_http_status", 500),
        ("stable_ms", 6000),
    ):
        changed = json.loads(json.dumps(receipt))
        changed["site_readiness"][0][changed_field] = invalid
        receipt_path.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match="site readiness"):
            validate_remote_cohort_reset_receipt(receipt_path, **kwargs)
