"""The same measured resource window has the same units across browser tracks."""
import pytest

from harness.environments.webarena.runtime import resource_summary
from harness.resource_accounting import CounterSnapshot, ResourceSample, ResourceSampler
from harness.trajectory_replay.public_webarena.runner import _resource_result as public_resources
from harness.trajectory_replay.runner import _resource_result as replay_resources


@pytest.mark.parametrize("summarize", [resource_summary, public_resources, replay_resources])
def test_resource_window_reports_cpu_memory_and_network_in_consistent_units(summarize):
    sampler = ResourceSampler(object())
    sampler.samples = [
        ResourceSample(0, 100, 1, 20_000, 100, 200),
        ResourceSample(100, 300, 3, 70_000, 1100, 700),
    ]
    result = summarize(
        object(), sampler, CounterSnapshot(20_000, 100, 200),
        CounterSnapshot(70_000, 1100, 700), 100,
    )
    assert result["cpu_ms"] == 50
    assert result["cpu_average_utilization_pct"] == 50
    assert result["memory_mean_bytes"] == 200
    assert result["network_rx_bytes"] == 1000
    assert result["network_tx_bytes"] == 500
