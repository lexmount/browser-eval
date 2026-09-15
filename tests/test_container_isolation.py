from unittest.mock import MagicMock

import pytest

from harness.container_isolation import ContainerLimits, require_linux_cgroup_v2


def test_shared_container_limits_are_explicit_and_bounded():
    values = ContainerLimits(cpus=1.5, memory="1g", pids=64).docker_kwargs(
        drop_all_capabilities=True
    )
    assert values["nano_cpus"] == 1_500_000_000
    assert values["mem_limit"] == "1g"
    assert values["pids_limit"] == 64
    assert values["security_opt"] == ["no-new-privileges"]
    assert values["cap_drop"] == ["ALL"]


def test_shared_isolation_gate_rejects_non_linux_or_cgroup_v1():
    client = MagicMock()
    client.info.return_value = {"OSType": "linux", "CgroupVersion": "1"}
    with pytest.raises(RuntimeError, match="cgroup v2"):
        require_linux_cgroup_v2(client)
