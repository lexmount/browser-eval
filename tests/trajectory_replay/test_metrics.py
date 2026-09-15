from __future__ import annotations

from harness.trajectory_replay.metrics import (
    CounterSnapshot,
    DockerStatsResources,
    LinuxContainerResources,
    ResourceSample,
    ResourceSampler,
    delta_counters,
    parse_cpu_stat,
    parse_network_dev,
    summarize_attempts,
)


def test_resource_summary_reports_time_series_percentiles_and_sane_cpu_percent():
    sampler = ResourceSampler(object())
    sampler.samples = [
        ResourceSample(0, 100, 2, 0, 0, 0),
        ResourceSample(100, 200, 4, 50_000, 1_000, 500),
        ResourceSample(200, 300, 3, 100_000, 3_000, 1_500),
    ]

    result = sampler.summary()

    assert result["memory_mean_bytes"] == 200
    assert result["memory_p95_bytes"] == 300
    assert result["cpu_sampled_utilization_p95_pct"] == 50
    assert result["network_rx_rate_p95_bytes_per_second"] == 20_000


def test_parse_cpu_and_network_counters():
    assert parse_cpu_stat("usage_usec 1234\nuser_usec 1000\nsystem_usec 234\n") == 1234
    assert parse_network_dev(
        "Inter-| Receive | Transmit\n"
        " eth0: 100 1 0 0 0 0 0 0 250 2 0 0 0 0 0 0\n"
        " lo: 10 1 0 0 0 0 0 0 10 1 0 0 0 0 0 0\n"
    ) == (100, 250)


def test_delta_counters_uses_cumulative_kernel_values():
    before = CounterSnapshot(cpu_usage_usec=100, network_rx_bytes=200, network_tx_bytes=300)
    after = CounterSnapshot(cpu_usage_usec=340, network_rx_bytes=900, network_tx_bytes=1100)

    assert delta_counters(before, after) == {
        "cpu_ms": 0.24,
        "network_rx_bytes": 700,
        "network_tx_bytes": 800,
    }


def test_linux_container_resources_reads_target_cgroup_and_network_namespace(tmp_path):
    proc = tmp_path / "proc"
    cgroup = tmp_path / "cgroup"
    (proc / "42" / "net").mkdir(parents=True)
    (proc / "42" / "cgroup").write_text("0::/docker/browser\n", encoding="utf-8")
    (proc / "42" / "net" / "dev").write_text(
        "eth0: 100 1 0 0 0 0 0 0 200 1 0 0 0 0 0 0\n", encoding="utf-8"
    )
    target = cgroup / "docker" / "browser"
    target.mkdir(parents=True)
    (target / "cpu.stat").write_text("usage_usec 1234\n", encoding="utf-8")
    (target / "memory.current").write_text("4096\n", encoding="utf-8")
    (target / "cgroup.procs").write_text("42\n43\n", encoding="utf-8")

    resources = LinuxContainerResources(42, proc_root=proc, cgroup_root=cgroup)

    assert resources.counters() == CounterSnapshot(1234, 100, 200)
    sample = resources.sample(elapsed_ms=12.5)
    assert sample.memory_current_bytes == 4096
    assert sample.pids_current == 2
    assert (sample.cpu_usage_usec, sample.network_rx_bytes, sample.network_tx_bytes) == (
        1234, 100, 200
    )


def test_resource_summary_uses_elapsed_time_for_memory_mean():
    sampler = ResourceSampler(object())
    sampler.samples = [
        ResourceSample(0, 0, 1, 0, 0, 0),
        ResourceSample(100, 100, 1, 100_000, 0, 0),
        ResourceSample(400, 100, 1, 400_000, 0, 0),
    ]
    assert sampler.summary()["memory_mean_bytes"] == 87.5


def test_docker_stats_resources_uses_per_container_cumulative_counters():
    class Container:
        def stats(self, **kwargs):
            assert kwargs == {"stream": False, "one_shot": True}
            return {
                "cpu_stats": {"cpu_usage": {"total_usage": 2_500_000}},
                "memory_stats": {"usage": 8192},
                "pids_stats": {"current": 4},
                "networks": {
                    "eth0": {"rx_bytes": 300, "tx_bytes": 500},
                    "lo": {"rx_bytes": 100, "tx_bytes": 100},
                },
            }

    resources = DockerStatsResources(Container())

    assert resources.counters() == CounterSnapshot(2500, 300, 500)
    assert resources.sample(elapsed_ms=1).memory_current_bytes == 8192
    assert resources.sample(elapsed_ms=1).pids_current == 4


def test_summary_keeps_functional_scores_separate_and_uses_pass_intersection():
    rows = [
        {
            "engine": "chromium",
            "task_id": "a",
            "complexity": "step_01_05",
            "attempt": 1,
            "status": "pass",
            "duration_ms": 10,
            "resource": {"cpu_ms": 5, "memory_peak_bytes": 100, "network_rx_bytes": 20},
        },
        {
            "engine": "moli",
            "task_id": "a",
            "complexity": "step_01_05",
            "attempt": 1,
            "status": "pass",
            "duration_ms": 8,
            "resource": {"cpu_ms": 2, "memory_peak_bytes": 50, "network_rx_bytes": 18},
        },
        {
            "engine": "chromium",
            "task_id": "b",
            "complexity": "step_06_10",
            "attempt": 1,
            "status": "pass",
            "duration_ms": 7,
            "resource": {"cpu_ms": 3, "memory_peak_bytes": 90, "network_rx_bytes": 10},
        },
        {
            "engine": "moli",
            "task_id": "b",
            "complexity": "step_06_10",
            "attempt": 1,
            "status": "fail",
            "duration_ms": 6,
            "error": "timeout",
            "resource": {"cpu_ms": 1, "memory_peak_bytes": 40, "network_rx_bytes": 9},
        },
    ]

    summary = summarize_attempts(rows, engines=("chromium", "moli"))

    assert summary["functional"]["chromium"]["passes"] == 2
    assert summary["functional"]["moli"]["passes"] == 1
    assert summary["resource_comparison"]["intersection_attempts"] == 1
    assert summary["resource_comparison"]["by_engine"]["chromium"]["cpu_ms"]["median"] == 5
    assert summary["resource_comparison"]["by_engine"]["moli"]["cpu_ms"]["median"] == 2
    assert summary["resource_comparison"]["by_engine"]["chromium"]["duration_ms"]["median"] == 10
    assert summary["resource_comparison"]["by_engine"]["moli"]["duration_ms"]["median"] == 8
    assert summary["resource_comparison"]["by_engine"]["moli"]["duration_ms"]["p50"] == 8
    assert summary["resource_comparison"]["by_engine"]["moli"]["duration_ms"]["mean"] == 8
    assert summary["resource_comparison"]["by_engine"]["moli"]["duration_ms"]["p95"] == 8
    assert summary["resource_comparison"]["paired_ratios"]["metrics"]["duration_ms"]["p50"] == 0.8
    assert summary["resource_comparison"]["paired_ratios"]["metrics"]["duration_ms"]["values"] == [
        {"task_id": "a", "denominator": 10.0, "numerator": 8.0, "ratio": 0.8}
    ]
    assert summary["resource_comparison"]["paired_ratios"]["p95_method"] == (
        "nearest-rank: sorted_values[ceil(0.95*n)-1]"
    )
    assert summary["by_complexity"]["step_01_05"]["functional"]["moli"]["passes"] == 1
    assert (
        summary["by_complexity"]["step_06_10"]["resource_comparison"]["intersection_attempts"] == 0
    )
    assert summary["by_task"]["a"]["functional"]["chromium"]["passes"] == 1
    assert summary["by_task"]["b"]["representative_errors"]["moli"] == ["timeout"]


def test_summary_separates_first_pass_retry_recovery_and_final_outcome():
    rows = [
        {
            "engine": "chromium",
            "task_id": "stable",
            "complexity": "simple",
            "attempt": 1,
            "is_retry": False,
            "status": "pass",
            "duration_ms": 10,
            "resource": {},
        },
        {
            "engine": "moli",
            "task_id": "stable",
            "complexity": "simple",
            "attempt": 1,
            "is_retry": False,
            "status": "fail",
            "duration_ms": 9,
            "resource": {},
        },
        {
            "engine": "moli",
            "task_id": "stable",
            "complexity": "simple",
            "attempt": 2,
            "is_retry": True,
            "status": "pass",
            "duration_ms": 11,
            "resource": {},
        },
    ]

    summary = summarize_attempts(rows, engines=("chromium", "moli"))

    assert summary["functional"]["chromium"]["first_passes"] == 1
    assert summary["functional"]["moli"]["first_passes"] == 0
    assert summary["functional"]["moli"]["recovered"] == 1
    assert summary["functional"]["moli"]["final_passes"] == 1
    assert summary["functional"]["moli"]["physical_attempts"] == 2
    assert summary["resource_comparison"]["intersection_attempts"] == 0


def test_summary_reports_result_and_combined_correctness_independently():
    rows = [
        {
            "engine": engine,
            "task_id": "multi-path",
            "complexity": "medium",
            "attempt": 1,
            "status": "pass",
            "result_status": "pass",
            "process_status": "fail" if engine == "moli" else "pass",
            "combined_status": "fail" if engine == "moli" else "pass",
            "duration_ms": 10,
            "resource": {"cpu_ms": 1},
        }
        for engine in ("chromium", "moli")
    ]

    summary = summarize_attempts(rows, engines=("chromium", "moli"))

    assert summary["functional"]["moli"]["result_first_passes"] == 1
    assert summary["functional"]["moli"]["combined_first_passes"] == 0
    assert summary["resource_comparison"]["intersection_attempts"] == 1
