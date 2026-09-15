from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from harness.web_fetch.container_resources import ContainerResourceRunner


@pytest.fixture(autouse=True)
def _available_docker_cli():
    with patch("harness.web_fetch.container_resources.shutil.which",
               return_value="/usr/bin/docker"):
        yield


def _client(*, os_type="linux", cgroup="2"):
    client = MagicMock()
    client.info.return_value = {
        "OSType": os_type,
        "CgroupVersion": cgroup,
        "OperatingSystem": "test-linux",
        "Architecture": "arm64",
    }
    client.version.return_value = {"Version": "29.5.2"}
    client.images.get.return_value = SimpleNamespace(
        id="sha256:image",
        attrs={"RepoDigests": ["fixture@sha256:digest"], "Architecture": "arm64"},
    )
    return client


def test_resource_backend_requires_linux_cgroup_v2():
    runner = ContainerResourceRunner("fixture:local")
    with patch("harness.web_fetch.container_resources.docker.from_env",
               return_value=_client(os_type="darwin")):
        with pytest.raises(RuntimeError, match="Linux Docker engine"):
            runner.validate()
    with patch("harness.web_fetch.container_resources.docker.from_env",
               return_value=_client(cgroup="1")):
        with pytest.raises(RuntimeError, match="cgroup v2"):
            runner.validate()


def test_resource_environment_binds_image_limits_and_network_scope():
    runner = ContainerResourceRunner(
        "fixture:local", interval_ms=25, cpus=1.5, memory="1g", pids_limit=128
    )
    with patch("harness.web_fetch.container_resources.docker.from_env",
               return_value=_client()):
        result = runner.validate()
    assert result["backend"] == "docker_cgroup_v2"
    assert result["image_id"] == "sha256:image"
    assert result["cpus"] == 1.5
    assert result["memory"] == "1g"
    assert result["sample_interval_ms"] == 25
    assert result["network_isolation"] == (
        "one dedicated bridge with no peer containers per call"
    )
    assert result["controller_excluded"] is True


def test_resource_backend_rejects_missing_docker_cli():
    runner = ContainerResourceRunner("fixture:local")
    with patch("harness.web_fetch.container_resources.docker.from_env", return_value=_client()), \
         patch("harness.web_fetch.container_resources.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="Docker CLI is required"):
            runner.validate()


def test_resource_identity_hashes_binary_inside_bound_image():
    client = _client()
    client.containers.run.side_effect = [
        b"a" * 64 + b"  /usr/local/bin/moli\n",
        b"moli 1.1.1\n",
    ]
    runner = ContainerResourceRunner("fixture:local")
    with patch("harness.web_fetch.container_resources.docker.from_env", return_value=client):
        identity = runner.implementation_identity("/usr/local/bin/moli")
    assert identity["binary_sha256"] == "a" * 64
    assert identity["reported_version"] == "moli 1.1.1"
    assert identity["image_id"] == "sha256:image"
