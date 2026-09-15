from __future__ import annotations

import json
from types import SimpleNamespace

from scripts.manage_webarena_environment import _manager


def test_manager_applies_explicit_host_ports(tmp_path, monkeypatch):
    pinned = "am1n3e/webarena-verified-shopping_admin@sha256:" + "a" * 64
    lock = tmp_path / "images.json"
    lock.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_environment_images/1",
                "images": {
                    "shopping_admin": {
                        "pinned": pinned,
                        "architecture": "amd64",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.manage_webarena_environment.docker.from_env",
        lambda: SimpleNamespace(info=lambda: {"Architecture": "x86_64"}),
    )

    manager = _manager(
        "shopping_admin",
        lock,
        port=17780,
        control_port=17781,
    )

    assert manager.config.docker_img == pinned
    assert manager.config.host_port == 17780
    assert manager.config.host_env_ctrl_port == 17781
    assert manager.config.container_port == 80


def test_manager_keeps_gitlab_internal_health_port(tmp_path, monkeypatch):
    pinned = "am1n3e/webarena-verified-gitlab@sha256:" + "b" * 64
    lock = tmp_path / "images.json"
    lock.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_environment_images/1",
                "images": {
                    "gitlab": {
                        "pinned": pinned,
                        "architecture": "amd64",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.manage_webarena_environment.docker.from_env",
        lambda: SimpleNamespace(info=lambda: {"Architecture": "amd64"}),
    )

    manager = _manager("gitlab", lock, port=18023, control_port=18024)

    assert manager.config.host_port == 18023
    assert manager.config.container_port == 8023
