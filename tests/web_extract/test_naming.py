"""Current track names and preserved source provenance have separate boundaries."""

import json


def test_current_extract_names_cover_code_configuration_and_example_paths():
    from harness.benchmarks.data import source_name_migrations
    from scripts.check_track_naming import extract_name_findings

    names = source_name_migrations()["schemas"]
    original = next(k for k, v in names.items() if v == "web-extract-public-snapshots/2")
    retired = original.removesuffix("-public-snapshots/2")
    for name, text in [
        ("src/harness/web_extract/report.py", retired.replace("-", " ").title()),
        ("src/harness/web_extract/report.py", 'LABEL = "' + retired + '"'),
        ("configs/report-templates.json", '{"tracks":{"' + retired + '":{}}}'),
        ("src/harness/" + retired.replace("-", "_") + "/cli.py", ""),
        ("configs/report-templates.json", '"/tmp/' + retired.rsplit("-", 1)[-1] + "-example" + '"'),
    ]:
        assert extract_name_findings(name, text)
    assert not extract_name_findings("src/harness/web_extract/report.py", 'LABEL = "Web Extract"')
    assert not extract_name_findings(
        "src/harness/web_fetch/workers/example.py", "PDFContentScrapingStrategy"
    )


def test_original_names_are_limited_to_explicit_migration_and_historical_sources():
    from harness.benchmarks.data import source_name_migrations
    from scripts.check_track_naming import extract_name_findings

    names = source_name_migrations()["schemas"]
    original = next(k for k, v in names.items() if v == "web-extract-public-snapshots/2")
    retired = original.removesuffix("-public-snapshots/2")
    ledger = {
        "legacy_names": {
            "schemas": {retired + "-public-snapshots/2": "web-extract-public-snapshots/2"}
        }
    }
    assert not extract_name_findings(
        "src/harness/benchmarks/code-migration.json", json.dumps(ledger)
    )
    ledger["current_track"] = retired
    assert extract_name_findings("src/harness/benchmarks/code-migration.json", json.dumps(ledger))
    lock = {
        "datasets": {
            "web-extract": {
                "repository": "owner/current",
                "history": [{"repository": "owner/" + retired}],
            }
        }
    }
    assert not extract_name_findings("src/harness/benchmarks/data-lock.json", json.dumps(lock))
    lock["datasets"][retired] = lock["datasets"].pop("web-extract")
    assert extract_name_findings("src/harness/benchmarks/data-lock.json", json.dumps(lock))
