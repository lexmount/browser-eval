from __future__ import annotations

import sys

from harness.environments.webarena import rainbond


def test_reset_persists_runtime_config_atomically(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "pool.json"
    slot_dir = tmp_path / "slice-1"
    slot_dir.mkdir()
    calls = []

    class Pool:
        def __init__(self, config):
            assert config == config_path.resolve()

        def reset(self, receipt_path):
            assert receipt_path == slot_dir.resolve() / "slice-receipt.json"
            return {"app_id": 201}

        def persist_runtime_config(self, receipt, target):
            calls.append((receipt, target))

    monkeypatch.setattr(rainbond, "RainbondSlicePool", Pool)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "manage_webarena_rainbond_pool.py",
            "--config",
            str(config_path),
            "reset",
            "--slice-dir",
            str(slot_dir),
        ],
    )

    rainbond.pool_main()

    target = slot_dir.resolve() / "webarena.config.json"
    assert calls == [({"app_id": 201}, target)]
    assert capsys.readouterr().out.strip() == str(target)
