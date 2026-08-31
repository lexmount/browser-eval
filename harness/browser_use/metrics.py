"""Cgroup-v2 and network-namespace resource accounting."""

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


class LinuxContainerResources:
    """Read one container cgroup and network namespace from an external sampler."""

    def __init__(
        self,
        root_pid: int,
        *,
        proc_root: str | Path = "/host/proc",
        cgroup_root: str | Path = "/host/cgroup",
    ) -> None:
        self.root_pid = int(root_pid)
        self.proc_root = Path(proc_root)
        self.cgroup_root = Path(cgroup_root)
        cgroup_text = (self.proc_root / str(self.root_pid) / "cgroup").read_text(encoding="utf-8")
        relative = None
        for line in cgroup_text.splitlines():
            if line.startswith("0::"):
                relative = line[3:].lstrip("/")
                break
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
            (self.proc_root / str(self.root_pid) / "net" / "dev").read_text(encoding="utf-8")
        )
        return CounterSnapshot(cpu, rx, tx)

    def sample(self, *, elapsed_ms: float) -> ResourceSample:
        memory = int((self.cgroup_path / "memory.current").read_text(encoding="ascii"))
        pids = {
            int(value)
            for value in (self.cgroup_path / "cgroup.procs").read_text(encoding="ascii").split()
        }
        return ResourceSample(elapsed_ms, memory, len(pids))


class DockerStatsResources:
    """Read one browser container through the Docker Engine stats API."""

    def __init__(self, container: Any) -> None:
        self.container = container

    def _stats(self) -> dict[str, Any]:
        return self.container.stats(stream=False, one_shot=True)

    @staticmethod
    def _network(stats: dict[str, Any]) -> tuple[int, int]:
        rx = 0
        tx = 0
        for name, values in (stats.get("networks") or {}).items():
            if name == "lo":
                continue
            rx += int(values.get("rx_bytes") or 0)
            tx += int(values.get("tx_bytes") or 0)
        return rx, tx

    def counters(self) -> CounterSnapshot:
        stats = self._stats()
        cpu_ns = int(
            (((stats.get("cpu_stats") or {}).get("cpu_usage") or {}).get("total_usage")) or 0
        )
        rx, tx = self._network(stats)
        return CounterSnapshot(cpu_ns // 1000, rx, tx)

    def sample(self, *, elapsed_ms: float) -> ResourceSample:
        stats = self._stats()
        return ResourceSample(
            elapsed_ms=elapsed_ms,
            memory_current_bytes=int((stats.get("memory_stats") or {}).get("usage") or 0),
            pids_current=int((stats.get("pids_stats") or {}).get("current") or 0),
        )


class ResourceSampler:
    """Sample current memory/PID count outside the target browser cgroup."""

    def __init__(self, source: LinuxContainerResources, interval_ms: int = 50) -> None:
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

        self._thread = threading.Thread(target=loop, name="browser-resource-sampler", daemon=True)
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

    def summary(self) -> dict[str, int]:
        if not self.samples:
            raise RuntimeError("resource sampler has no samples")
        baseline = self.samples[0].memory_current_bytes
        end = self.samples[-1].memory_current_bytes
        peak = max(sample.memory_current_bytes for sample in self.samples)
        return {
            "memory_baseline_bytes": baseline,
            "memory_peak_bytes": peak,
            "memory_end_bytes": end,
            "memory_peak_delta_bytes": max(0, peak - baseline),
            "pids_current_peak": max(sample.pids_current for sample in self.samples),
            "sample_count": len(self.samples),
        }


def parse_cpu_stat(text: str) -> int:
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "usage_usec":
            return int(parts[1])
    raise ValueError("usage_usec is missing from cpu.stat")


def parse_network_dev(text: str) -> tuple[int, int]:
    rx = 0
    tx = 0
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


def _metric(values: Iterable[int | float]) -> dict[str, int | float | None]:
    numbers = [float(value) for value in values]
    if not numbers:
        return {"n": 0, "p50": None, "median": None, "mean": None, "p95": None}
    ordered = sorted(numbers)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95 + 0.999) - 1))
    p50 = statistics.median(numbers)
    return {
        "n": len(numbers),
        "p50": p50,
        "median": p50,
        "mean": statistics.fmean(numbers),
        "p95": ordered[index],
    }


def _summarize_core(rows: list[dict[str, Any]], *, engines: tuple[str, ...]) -> dict[str, Any]:
    functional: dict[str, Any] = {}
    for engine in engines:
        engine_rows = [row for row in rows if row.get("engine") == engine]
        passes = sum(row.get("status") == "pass" for row in engine_rows)
        functional[engine] = {
            "attempts": len(engine_rows),
            "passes": passes,
            "pass_rate": passes / len(engine_rows) if engine_rows else None,
            "duration_ms": _metric(
                row["duration_ms"] for row in engine_rows if row.get("status") == "pass"
            ),
            "tool_output_tokens": _metric(
                row["tool_output_tokens"]
                for row in engine_rows
                if row.get("status") == "pass"
                and isinstance(row.get("tool_output_tokens"), (int, float))
            ),
        }
    grouped: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((str(row["task_id"]), int(row["attempt"])), {})[str(row["engine"])] = row
    intersection = [
        bucket
        for bucket in grouped.values()
        if all(engine in bucket and bucket[engine].get("status") == "pass" for engine in engines)
    ]
    metric_names = (
        "cpu_ms",
        "memory_baseline_bytes",
        "memory_peak_bytes",
        "memory_end_bytes",
        "memory_peak_delta_bytes",
        "network_rx_bytes",
        "network_tx_bytes",
        "pids_current_peak",
    )
    by_engine: dict[str, Any] = {}
    for engine in engines:
        engine_rows = [bucket[engine] for bucket in intersection]
        values = {
            name: _metric(
                resource[name]
                for row in engine_rows
                if isinstance((resource := row.get("resource") or {}).get(name), (int, float))
            )
            for name in metric_names
        }
        values["duration_ms"] = _metric(row["duration_ms"] for row in engine_rows)
        values["tool_output_tokens"] = _metric(
            row["tool_output_tokens"]
            for row in engine_rows
            if isinstance(row.get("tool_output_tokens"), (int, float))
        )
        by_engine[engine] = values
    paired_ratios: dict[str, Any] = {}
    if len(engines) == 2:
        denominator_engine, numerator_engine = engines
        ratio_metric_names = (*metric_names, "duration_ms", "tool_output_tokens")
        for name in ratio_metric_names:
            ratios: list[float] = []
            for bucket in intersection:
                denominator_row = bucket[denominator_engine]
                numerator_row = bucket[numerator_engine]
                if name in {"duration_ms", "tool_output_tokens"}:
                    denominator = denominator_row.get(name)
                    numerator = numerator_row.get(name)
                else:
                    denominator = (denominator_row.get("resource") or {}).get(name)
                    numerator = (numerator_row.get("resource") or {}).get(name)
                if (
                    isinstance(denominator, (int, float))
                    and denominator > 0
                    and isinstance(numerator, (int, float))
                ):
                    ratios.append(float(numerator) / float(denominator))
            paired_ratios[name] = _metric(ratios)
    return {
        "functional": functional,
        "resource_comparison": {
            "scope": "warm browser container only",
            "functional_score_combined": False,
            "intersection_attempts": len(intersection),
            "by_engine": by_engine,
            "paired_ratios": {
                "numerator_engine": engines[1] if len(engines) == 2 else None,
                "denominator_engine": engines[0] if len(engines) == 2 else None,
                "metrics": paired_ratios,
            },
        },
    }


def summarize_attempts(rows: list[dict[str, Any]], *, engines: tuple[str, ...]) -> dict[str, Any]:
    summary = _summarize_core(rows, engines=engines)
    complexities = sorted({str(row["complexity"]) for row in rows if row.get("complexity")})
    summary["by_complexity"] = {
        complexity: _summarize_core(
            [row for row in rows if row.get("complexity") == complexity],
            engines=engines,
        )
        for complexity in complexities
    }
    task_ids = list(dict.fromkeys(str(row["task_id"]) for row in rows))
    summary["by_task"] = {}
    for task_id in task_ids:
        task_rows = [row for row in rows if str(row.get("task_id")) == task_id]
        first = task_rows[0]
        task_summary = _summarize_core(task_rows, engines=engines)
        task_summary.update(
            {
                "title": first.get("title", task_id),
                "complexity": first.get("complexity"),
                "step_count": first.get("step_count"),
                "representative_errors": {
                    engine: list(
                        dict.fromkeys(
                            str(row.get("error"))
                            for row in task_rows
                            if row.get("engine") == engine and row.get("error")
                        )
                    )[:3]
                    for engine in engines
                },
            }
        )
        summary["by_task"][task_id] = task_summary

    max_attempt = max((int(row.get("attempt") or 0) for row in rows), default=0)
    repeat_drift: dict[str, Any] = {}
    for engine in engines:
        baseline_ratios: list[float] = []
        duration_ratios: list[float] = []
        for task_id in task_ids:
            buckets = {
                int(row["attempt"]): row
                for row in rows
                if row.get("engine") == engine and str(row.get("task_id")) == task_id
            }
            first = buckets.get(1)
            last = buckets.get(max_attempt)
            if max_attempt <= 1 or first is None or last is None:
                continue
            first_baseline = (first.get("resource") or {}).get("memory_baseline_bytes")
            last_baseline = (last.get("resource") or {}).get("memory_baseline_bytes")
            if (
                isinstance(first_baseline, (int, float))
                and first_baseline > 0
                and isinstance(last_baseline, (int, float))
            ):
                baseline_ratios.append(float(last_baseline) / float(first_baseline))
            if first.get("status") == "pass" and last.get("status") == "pass":
                first_duration = first.get("duration_ms")
                last_duration = last.get("duration_ms")
                if (
                    isinstance(first_duration, (int, float))
                    and first_duration > 0
                    and isinstance(last_duration, (int, float))
                ):
                    duration_ratios.append(float(last_duration) / float(first_duration))
        repeat_drift[engine] = {
            "first_attempt": 1 if max_attempt else None,
            "last_attempt": max_attempt or None,
            "paired_tasks": len(baseline_ratios),
            "memory_baseline_ratio": (
                statistics.median(baseline_ratios) if baseline_ratios else None
            ),
            "duration_ratio": statistics.median(duration_ratios) if duration_ratios else None,
        }
    summary["repeat_drift"] = repeat_drift
    return summary
