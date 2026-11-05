"""Frozen synthetic URL inputs for collection scenarios; no external network."""

from harness.web_extract import snapshots
from harness.web_extract.pipeline import digest, save_json


def bind_inputs(home, output, *, base="https://snapshot-a.example"):
    version, files = snapshots.inventory(home)
    save_json(
        output,
        {
            "hosting_provider": "external",
            "html_inventory_sha256": version,
            "cases": files,
            "fixture_base": base,
            "immutable_origin": base,
        },
    )
    return output


def serve_input(home, base, case, **kwargs):
    return {
        "case_id": case,
        "url": base + "/" + case,
        "sha256": digest((home / "dataset/html" / (case + ".html")).read_bytes()),
        "http_status": 200,
    }
