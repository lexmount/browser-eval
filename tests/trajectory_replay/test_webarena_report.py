from pathlib import Path

import pytest

from harness.trajectory_replay.webarena_report import _clean, _metrics_table, _shell
from scripts.check_webarena_replay_report import _check_english_localization


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


def test_english_release_gate_rejects_reader_facing_chinese(tmp_path: Path) -> None:
    page = tmp_path / "index.html"
    page.write_text(
        '<html><head><title>Replay</title></head><body><a title="Open">中文</a>'
        '<pre>{"evidence":"原样保留"}</pre></body></html>',
        encoding="utf-8",
    )
    _check_english_localization([page])

    page.write_text(
        '<html><head><title>Replay</title></head><body><p>未翻译正文</p></body></html>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Chinese prose"):
        _check_english_localization([page])

    page.write_text(
        '<html><head><title>Replay</title></head><body><button aria-label="打开">Open</button>'
        '</body></html>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="accessibility text"):
        _check_english_localization([page])


def test_report_shell_localizes_chinese_title_and_footer() -> None:
    rendered = _shell("zh", "index", "浏览器轨迹重放", "<p>正文</p>")
    assert "<title>浏览器轨迹重放</title>" in rendered
    assert "WebArena-Verified 1.2.3 · 浏览器轨迹重放" in rendered
    assert "Browser Trajectory Replay</footer>" not in rendered


def test_report_removes_macos_and_linux_local_paths() -> None:
    for path in (
        "/Users/example/run/trace.json",
        "/home/runner/work/trace.json",
        "/private/tmp/run/trace.json",
        "/var/lib/docker/trace.json",
        "/opt/hostedtoolcache/trace.json",
    ):
        assert _clean(f"failure at {path}") == "failure at [local path removed]"
