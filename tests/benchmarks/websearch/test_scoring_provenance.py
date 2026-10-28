"""Scoring corrections preserve their baseline identities and revision provenance."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from importlib.resources import files

from lexbench_websearch.inputs import definition_path
from lexbench_websearch.qualification import validate_regressions


def verify_receipt(receipt, package):
    expected = dict(receipt["files"])
    for revision in receipt.get("revisions", []):
        validate_regressions(revision)
        for name, change in revision["files"].items():
            assert expected[name] == change["before_sha256"]
            expected[name] = change["after_sha256"]
    for name, digest in expected.items():
        # The recorded path is source-relative; installed distributions use the same modules.
        relative = Path(name).relative_to("src/lexbench_websearch")
        if relative.parts[0] == "definitions":
            actual = definition_path(Path(*relative.parts[1:]).as_posix())
        else:
            actual = package / relative
        migration = json.loads(files("harness.benchmarks").joinpath("code-migration.json").read_bytes())
        change = migration["sources"]["websearch"]["changes"].get(relative.as_posix())
        if change:
            assert change["before_sha256"] == digest
            digest = change["after_sha256"]
        assert hashlib.sha256(actual.read_bytes()).hexdigest() == digest


def receipt_path():
    # One packaged receipt binds both source and wheel installations.
    return definition_path("scoring-revisions.json")


def test_current_source_has_paired_revision_provenance():
    receipt = json.loads(receipt_path().read_text())
    import lexbench_websearch
    verify_receipt(receipt, Path(lexbench_websearch.__file__).parent)


@pytest.mark.parametrize("defect", ["cause", "regressions", "predecessor"])
def test_correction_receipt_cannot_erase_cause_controls_or_history(defect):
    receipt = deepcopy(json.loads(receipt_path().read_text()))
    revision = receipt["revisions"][0]
    if defect == "cause":
        revision.pop("root_cause")
    elif defect == "regressions":
        revision["regression_ids"] = ["unknown-control"]
    else:
        next(iter(revision["files"].values()))["before_sha256"] = "0" * 64
    with pytest.raises((ValueError, AssertionError)):
        verify_receipt(receipt, definition_path("registry.json").parent.parent)
