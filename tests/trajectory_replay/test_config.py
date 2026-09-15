from __future__ import annotations

import os

import pytest

from harness.trajectory_replay.config import configure_browser_docker_host, load_runtime_env


def test_runtime_env_defaults_do_not_override_exported_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nWEBARENA_REDDIT_USERNAME='fixture-user'\nINVALID KEY=nope\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("WEBARENA_REDDIT_USERNAME", "exported-user")

    load_runtime_env(env_file)

    assert os.environ["WEBARENA_REDDIT_USERNAME"] == "exported-user"
    assert "INVALID KEY" not in os.environ


def test_explicit_runtime_env_must_exist(tmp_path):
    with pytest.raises(ValueError, match="environment file is missing"):
        load_runtime_env(tmp_path / "missing.env")


def test_browser_docker_host_requires_absolute_unix_socket(monkeypatch):
    configure_browser_docker_host("unix:///var/run/docker.sock")
    assert os.environ["DOCKER_HOST"] == "unix:///var/run/docker.sock"

    for value in ("tcp://localhost:2375", "unix://relative.sock"):
        with pytest.raises(ValueError, match="absolute unix"):
            configure_browser_docker_host(value)
