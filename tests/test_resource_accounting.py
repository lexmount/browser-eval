"""The same measured resource window has the same units across browser tracks."""
import pytest

from harness.environments.webarena.runtime import resource_summary
from harness.resource_accounting import CounterSnapshot, ResourceSample, ResourceSampler
from harness.trajectory_replay.core.docker_runner import resource_result


@pytest.mark.parametrize(
    "summarize", [lambda *window: resource_summary(object(), *window), resource_result]
)
def test_resource_window_reports_cpu_memory_and_network_in_consistent_units(summarize):
    sampler = ResourceSampler(object())
    sampler.samples = [
        ResourceSample(0, 100, 1, 20_000, 100, 200),
        ResourceSample(100, 300, 3, 70_000, 1100, 700),
    ]
    result = summarize(
        sampler, CounterSnapshot(20_000, 100, 200),
        CounterSnapshot(70_000, 1100, 700), 100,
    )
    assert result["cpu_ms"] == 50
    assert result["cpu_average_utilization_pct"] == 50
    assert result["memory_mean_bytes"] == 200
    assert result["network_rx_bytes"] == 1000
    assert result["network_tx_bytes"] == 500


@pytest.mark.parametrize(
    ("duration_ms", "expected_rates"),
    [
        (
            100,
            {
                "cpu_average_utilization_pct": 50,
                "network_rx_bytes_per_second": 10_000,
                "network_tx_bytes_per_second": 5_000,
            },
        ),
        (0, {}),
    ],
)
def test_replay_resource_result_derives_each_rate_from_its_own_counter(
    duration_ms, expected_rates
):
    sampler = ResourceSampler(object())
    sampler.samples = [ResourceSample(0, 100, 1, 20_000, 100, 200)]

    result = resource_result(
        sampler, CounterSnapshot(20_000, 100, 200),
        CounterSnapshot(70_000, 1100, 700), duration_ms,
    )

    rate_fields = (
        "cpu_average_utilization_pct",
        "network_rx_bytes_per_second",
        "network_tx_bytes_per_second",
    )
    assert {key: result[key] for key in rate_fields if key in result} == expected_rates
