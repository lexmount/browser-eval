from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from harness.environments.webarena.cli import main


def test_batch_failure_retains_completed_site_receipts(monkeypatch, tmp_path) -> None:
    receipt = tmp_path / "batch.json"

    class Manager:
        def verify(self, name: str):
            if name == "reddit":
                raise RuntimeError("second site unavailable")
            return {"site": name, "status": "running"}

    monkeypatch.setattr(
        "harness.environments.webarena.cli.RuntimeConfig.load",
        lambda _path: SimpleNamespace(sites={"map": object(), "reddit": object()}),
    )
    monkeypatch.setattr(
        "harness.environments.webarena.cli.EnvironmentManager",
        lambda *_args, **_kwargs: Manager(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "webarena-env",
            "verify",
            "--config",
            str(tmp_path / "config.json"),
            "--site",
            "map",
            "--site",
            "reddit",
            "--receipt",
            str(receipt),
        ],
    )

    with pytest.raises(RuntimeError, match="second site unavailable"):
        main()

    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["status"] == "partial_failure"
    assert payload["failed_site"] == "reddit"
    assert payload["sites"] == [{"site": "map", "status": "running"}]
