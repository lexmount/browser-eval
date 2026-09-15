"""Shared cgroup-v2 and network-namespace resource accounting."""

from __future__ import annotations

import statistics
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CounterSnapshot:
    cpu_usage_usec: int
    network_rx_bytes: int
    network_tx_bytes: int


@dataclass(frozen=True)
class ResourceSample:
    elapsed_ms: float
    memory_current_bytes: int
    pids_current: int
    cpu_usage_usec: int
    network_rx_bytes: int
    network_tx_bytes: int


class LinuxContainerResources:
    """Read one container cgroup and network namespace from an external sampler."""

    def __init__(self, root_pid: int, *, proc_root: str | Path = "/host/proc",
                 cgroup_root: str | Path = "/host/cgroup") -> None:
        self.root_pid = int(root_pid)
        self.proc_root = Path(proc_root)
        self.cgroup_root = Path(cgroup_root)
        cgroup_text = (self.proc_root / str(self.root_pid) / "cgroup").read_text(
            encoding="utf-8"
        )
        relative = next((line[3:].lstrip("/") for line in cgroup_text.splitlines()
                         if line.startswith("0::")), None)
        if relative is None:
            raise RuntimeError("target container is not using cgroup v2")
        candidate = (self.cgroup_root / relative).resolve()
        root = self.cgroup_root.resolve()
        if candidate != root and root not in candidate.parents:
            raise RuntimeError("target cgroup escapes configured cgroup root")
        self.cgroup_path = candidate

    def counters(self) -> CounterSnapshot:
        cpu = parse_cpu_stat((self.cgroup_path / "cpu.stat").read_text(encoding="utf-8"))
        rx, tx = parse_network_dev(
            (self.proc_root / str(self.root_pid) / "net" / "dev").read_text(
                encoding="utf-8"
            )
        )
        return CounterSnapshot(cpu, rx, tx)

    def sample(self, *, elapsed_ms: float) -> ResourceSample:
        memory = int((self.cgroup_path / "memory.current").read_text(encoding="ascii"))
        pids = {int(value) for value in (self.cgroup_path / "cgroup.procs").read_text(
            encoding="ascii"
        ).split()}
        counters = self.counters()
        return ResourceSample(
            elapsed_ms=elapsed_ms,
            memory_current_bytes=memory,
            pids_current=len(pids),
            cpu_usage_usec=counters.cpu_usage_usec,
            network_rx_bytes=counters.network_rx_bytes,
            network_tx_bytes=counters.network_tx_bytes,
        )


class DockerStatsResources:
    """Read one container through the Docker Engine stats API."""

    def __init__(self, container: Any) -> None:
        self.container = container

    def _stats(self) -> dict[str, Any]:
        return self.container.stats(stream=False, one_shot=True)

    @staticmethod
    def _network(stats: dict[str, Any]) -> tuple[int, int]:
        rx = tx = 0
        for name, values in (stats.get("networks") or {}).items():
            if name == "lo":
                continue
            rx += int(values.get("rx_bytes") or 0)
            tx += int(values.get("tx_bytes") or 0)
        return rx, tx

    def counters(self) -> CounterSnapshot:
        stats = self._stats()
        cpu_ns = int((((stats.get("cpu_stats") or {}).get("cpu_usage") or {})
                      .get("total_usage")) or 0)
        rx, tx = self._network(stats)
        return CounterSnapshot(cpu_ns // 1000, rx, tx)

    def sample(self, *, elapsed_ms: float) -> ResourceSample:
        stats = self._stats()
        cpu_ns = int((((stats.get("cpu_stats") or {}).get("cpu_usage") or {})
                      .get("total_usage")) or 0)
        rx, tx = self._network(stats)
        return ResourceSample(
            elapsed_ms=elapsed_ms,
            memory_current_bytes=int((stats.get("memory_stats") or {}).get("usage") or 0),
            pids_current=int((stats.get("pids_stats") or {}).get("current") or 0),
            cpu_usage_usec=cpu_ns // 1000,
            network_rx_bytes=rx,
            network_tx_bytes=tx,
        )


class ResourceSampler:
    """Sample memory, process count and counter rates outside the target cgroup."""

    def __init__(self, source: Any, interval_ms: int = 50) -> None:
        self.source = source
        self.interval_ms = max(10, int(interval_ms))
        self.samples: list[ResourceSample] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0

    def start(self) -> None:
        self._started = time.monotonic()
        self.samples.append(self.source.sample(elapsed_ms=0.0))

        def loop() -> None:
            while not self._stop.wait(self.interval_ms / 1000):
                elapsed = (time.monotonic() - self._started) * 1000
                try:
                    self.samples.append(self.source.sample(elapsed_ms=elapsed))
                except (FileNotFoundError, ProcessLookupError):
                    return

        self._thread = threading.Thread(target=loop, name="resource-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        elapsed = (time.monotonic() - self._started) * 1000
        try:
            self.samples.append(self.source.sample(elapsed_ms=elapsed))
        except (FileNotFoundError, ProcessLookupError):
            pass

    def summary(self) -> dict[str, int | float]:
        if not self.samples:
            raise RuntimeError("resource sampler has no samples")
        baseline = self.samples[0].memory_current_bytes
        end = self.samples[-1].memory_current_bytes
        peak = max(sample.memory_current_bytes for sample in self.samples)
        memories = [sample.memory_current_bytes for sample in self.samples]
        pids = [sample.pids_current for sample in self.samples]
        elapsed_seconds = max(0.0, self.samples[-1].elapsed_ms / 1000)
        memory_mean = _time_weighted_mean(
            [(sample.elapsed_ms, sample.memory_current_bytes) for sample in self.samples]
        )
        cpu_rates = _counter_rates(self.samples, "cpu_usage_usec", scale=0.1)
        rx_rates = _counter_rates(self.samples, "network_rx_bytes", scale=1000.0)
        tx_rates = _counter_rates(self.samples, "network_tx_bytes", scale=1000.0)
        return {
            "memory_baseline_bytes": baseline,
            "memory_mean_bytes": memory_mean,
            "memory_p50_bytes": _nearest_rank(memories, 0.50),
            "memory_p95_bytes": _nearest_rank(memories, 0.95),
            "memory_peak_bytes": peak,
            "memory_end_bytes": end,
            "memory_end_delta_bytes": end - baseline,
            "memory_retained_bytes": max(0, end - baseline),
            "memory_peak_delta_bytes": max(0, peak - baseline),
            "memory_mib_seconds": memory_mean * elapsed_seconds / (1024 * 1024),
            "pids_current_mean": statistics.fmean(pids),
            "pids_current_p95": _nearest_rank(pids, 0.95),
            "pids_current_peak": max(pids),
            "cpu_sampled_utilization_p95_pct": _nearest_rank(cpu_rates, 0.95),
            "cpu_sampled_utilization_peak_pct": max(cpu_rates, default=0.0),
            "network_rx_rate_p95_bytes_per_second": _nearest_rank(rx_rates, 0.95),
            "network_rx_rate_peak_bytes_per_second": max(rx_rates, default=0.0),
            "network_tx_rate_p95_bytes_per_second": _nearest_rank(tx_rates, 0.95),
            "network_tx_rate_peak_bytes_per_second": max(tx_rates, default=0.0),
            "sample_count": len(self.samples),
        }


def _nearest_rank(values: Iterable[int | float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(len(ordered) * quantile + 0.999) - 1))
    return ordered[index]


def _time_weighted_mean(points: list[tuple[float, int | float]]) -> float:
    if len(points) < 2 or points[-1][0] <= points[0][0]:
        return float(points[0][1]) if points else 0.0
    area = 0.0
    for (left_ms, left), (right_ms, right) in zip(points, points[1:], strict=False):
        area += (right_ms - left_ms) * (float(left) + float(right)) / 2
    return area / (points[-1][0] - points[0][0])


def _counter_rates(samples: list[ResourceSample], attribute: str, *, scale: float) -> list[float]:
    rates: list[float] = []
    for left, right in zip(samples, samples[1:], strict=False):
        elapsed_ms = right.elapsed_ms - left.elapsed_ms
        if elapsed_ms <= 0:
            continue
        delta = max(0, getattr(right, attribute) - getattr(left, attribute))
        rates.append(delta / elapsed_ms * scale)
    return rates


def parse_cpu_stat(text: str) -> int:
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "usage_usec":
            return int(parts[1])
    raise ValueError("usage_usec is missing from cpu.stat")


def parse_network_dev(text: str) -> tuple[int, int]:
    rx = tx = 0
    for line in text.splitlines():
        if ":" not in line:
            continue
        interface, values_text = line.split(":", 1)
        if interface.strip() == "lo":
            continue
        values = values_text.split()
        if len(values) >= 16:
            rx += int(values[0])
            tx += int(values[8])
    return (rx, tx)


def delta_counters(before: CounterSnapshot, after: CounterSnapshot) -> dict[str, int | float]:
    return {
        "cpu_ms": max(0, after.cpu_usage_usec - before.cpu_usage_usec) / 1000,
        "network_rx_bytes": max(0, after.network_rx_bytes - before.network_rx_bytes),
        "network_tx_bytes": max(0, after.network_tx_bytes - before.network_tx_bytes),
    }


__all__ = [
    "CounterSnapshot", "DockerStatsResources", "LinuxContainerResources",
    "ResourceSample", "ResourceSampler", "delta_counters", "parse_cpu_stat",
    "parse_network_dev",
]
