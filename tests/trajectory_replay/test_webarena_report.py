from harness.trajectory_replay.webarena_report import _metrics_table


def _method(name: str, pass_rate: float, base: float) -> dict:
    metric = {"mean": base, "p50": base + 1, "p95": base + 2}
    return {
        "id": name,
        "label": name,
        "passed": int(pass_rate * 90),
        "total": 90,
        "pass_rate": pass_rate,
        "duration": metric,
        "action_latency": metric,
        "cpu": metric,
        "memory": metric,
    }


def test_core_metrics_are_registry_driven_and_mark_only_unique_winners() -> None:
    rendered = _metrics_table(
        [
            _method("Browser A", 1.0, 3.0),
            _method("Browser B", 0.8, 2.0),
            _method("Browser C", 0.8, 1.0),
        ],
        "en",
    )
    assert all(name in rendered for name in ("Browser A", "Browser B", "Browser C"))
    assert rendered.count(">Best</span>") == 14
    assert "median" not in rendered.lower()
