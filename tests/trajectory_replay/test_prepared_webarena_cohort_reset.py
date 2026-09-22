"""A prepared method reset must be fresh, exact, and copied unchanged."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from harness.trajectory_replay.remote_runtime import file_sha256
from scripts import use_prepared_webarena_cohort_reset as prepared
from tests.trajectory_replay.test_aggregate_webarena_three_method_cohorts import _reset_receipt


def _invoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, receipt: dict) -> Path:
    source = tmp_path / "prepared.json"
    source.write_text(json.dumps(receipt), encoding="utf-8")
    site_map = tmp_path / "sites.json"
    site_map.write_text("{}", encoding="utf-8")
    receipt["site_map_sha256"] = file_sha256(site_map)
    source.write_text(json.dumps(receipt), encoding="utf-8")
    output = tmp_path / "site-reset.json"
    monkeypatch.setenv("WEBARENA_PREPARED_COHORT_RESET_RECEIPT", str(source))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reset",
            "--method-id", "chromium",
            "--app-id", "195",
            "--site-map", str(site_map),
            "--receipt", str(output),
        ],
    )
    prepared.main()
    assert file_sha256(output) == file_sha256(source)
    return output


def test_prepared_reset_copies_fresh_method_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = _reset_receipt("chromium")
    receipt["completed_at_unix"] = time.time()
    assert _invoke(tmp_path, monkeypatch, receipt).is_file()


def test_prepared_reset_rejects_wrong_method(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = _reset_receipt("moli-1.1.3")
    receipt["completed_at_unix"] = time.time()
    with pytest.raises(ValueError, match="another method"):
        _invoke(tmp_path, monkeypatch, receipt)


def test_prepared_reset_rejects_stale_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = _reset_receipt("chromium")
    receipt["completed_at_unix"] = time.time() - 1900
    with pytest.raises(ValueError, match="stale"):
        _invoke(tmp_path, monkeypatch, receipt)
