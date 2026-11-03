"""Public certified entry-point contracts and native-interface scope."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.reporting.certified_cli import main
from harness.web_scraper.certification import create_plan
from harness.web_scraper.example import create
from harness.web_scraper.pipeline import read_json


def test_certified_default_keeps_webfetch_arguments():
    with patch("harness.web_fetch.certification.main", return_value="legacy") as run:
        assert main(["--profile", "external.json"]) == "legacy"
    run.assert_called_once_with(["--profile", "external.json"])


def test_certified_scraper_routes_to_native_track():
    with patch("harness.web_scraper.certification.main", return_value="native") as run:
        assert main(["--track", "web-scraper", "--profile", "external.json"]) == "native"
    run.assert_called_once_with(["--profile", "external.json"])


def test_site_specific_scraper_is_not_an_archived_html_participant():
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(ValueError, match="complete extraction interfaces"):
            create_plan(Path(d), ["brightdata-scrapers"], Path(d) / "plan.json")
        assert not (Path(d) / "plan.json").exists()


def test_default_example_generates_chinese_and_english_notice():
    with tempfile.TemporaryDirectory() as d:
        output = Path(d) / "report"
        create(output)
        manifest = read_json(output / "report-manifest.json")
        assert manifest["languages"] == ["zh"]
        assert manifest["primary_language"] == "zh"
        assert "data-language-notice" in (output / "en/index.html").read_text()
        assert not (output / "en/results.html").exists()


@pytest.mark.parametrize(
    ("native", "expected"),
    [("available", "InStock"), ("sold_out", "OutOfStock"), ("unavailable", "OutOfStock")],
    ids=["scrapfly-available-is-in-stock", "scrapfly-sold-out-is-out-of-stock", "unavailable"],
)
def test_native_stock_labels_preserve_both_stock_directions(native, expected):
    from harness.web_scraper.pipeline import normalize

    result = normalize("scrapfly-product", {"data": {"offers": [{"availability": native}]}})
    assert result["availability"] == expected
    assert result[expected] == expected
    assert ({"InStock", "OutOfStock"} - {expected}).isdisjoint(result)


def test_scrapfly_unknown_stock_stays_unknown():
    from harness.web_scraper.pipeline import normalize

    result = normalize("scrapfly-product", {"data": {"offers": [{"availability": "preorder"}]}})
    assert not {"InStock", "OutOfStock"} & result.keys()


def test_transport_failure_retains_elapsed_time(tmp_path):
    from harness.web_scraper.pipeline import load_run, probe

    home = create(tmp_path / "example")
    clock = [0.0]

    def timeout(*args, **kwargs):
        clock[0] += 5
        raise TimeoutError("Synthetic transport timeout")

    with (
        patch("harness.web_scraper.pipeline.validate_dataset"),
        patch("harness.web_scraper.pipeline.load_runtime_env"),
        patch("harness.web_scraper.pipeline.time.monotonic", side_effect=lambda: clock[0]),
        patch("harness.web_scraper.pipeline.provider_call", side_effect=timeout),
        patch.dict(
            "os.environ",
            {"CLOUDFLARE_ACCOUNT_ID": "synthetic", "CLOUDFLARE_API_TOKEN": "synthetic"},
        ),
    ):
        run = probe(home, ["cloudflare-json"], full=True)
    _, rows = load_run(run)
    assert len(rows) == 3
    assert all(r["state"] == "call_error" and r["elapsed_seconds"] == 5 for r in rows)
