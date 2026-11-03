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
        assert 'data-language-notice' in (output / "en/index.html").read_text()
        assert not (output / "en/results.html").exists()
