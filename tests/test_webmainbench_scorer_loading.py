"""A pinned scorer must execute that checkout, even after another scorer was loaded."""

import subprocess
import sys

import pytest

from harness.web_fetch.webmainbench_scoring import official_calculator


@pytest.fixture(autouse=True)
def restore_scorer_namespace():
    previous = {
        name: module
        for name, module in sys.modules.items()
        if name == "webmainbench" or name.startswith("webmainbench.")
    }
    yield
    for name in list(sys.modules):
        if name == "webmainbench" or name.startswith("webmainbench."):
            del sys.modules[name]
    sys.modules.update(previous)


def repository(root, marker):
    root.mkdir()
    package = root / "webmainbench" / "metrics"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "helper.py").write_text(f"MARKER = {marker!r}\n")
    (package / "calculator.py").write_text(
        "from webmainbench.metrics.helper import MARKER\n"
        "class MetricCalculator:\n"
        "    def __init__(self, config):\n"
        "        self.marker = MARKER\n"
        "        self.config = config\n"
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=fixture@example.test",
            "-c",
            "user.name=Fixture",
            "commit",
            "-qm",
            "Frozen fixture",
        ],
        check=True,
    )
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    return head


def test_loading_two_pinned_checkouts_does_not_reuse_the_first_modules(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first_head = repository(first, "first")
    second_head = repository(second, "second")
    a = official_calculator(first, tmp_path / "cache-a", first_head)
    b = official_calculator(second, tmp_path / "cache-b", second_head)
    assert (a.marker, b.marker) == ("first", "second")
    assert b.config["use_llm"] is False


def test_dirty_checkout_is_rejected_before_import(tmp_path):
    root = tmp_path / "scorer"
    head = repository(root, "pinned")
    (root / "webmainbench/metrics/helper.py").write_text(
        "raise AssertionError('must not execute')\n"
    )
    with pytest.raises(ValueError, match="clean"):
        official_calculator(root, tmp_path / "cache", head)


def test_failed_import_restores_the_previous_scorer_namespace(tmp_path):
    valid, broken = tmp_path / "valid", tmp_path / "broken"
    valid_head = repository(valid, "valid")
    official_calculator(valid, tmp_path / "cache-valid", valid_head)
    previous = {
        name: module
        for name, module in sys.modules.items()
        if name == "webmainbench" or name.startswith("webmainbench.")
    }
    broken_head = repository(broken, "broken")
    (broken / "webmainbench/metrics/calculator.py").write_text(
        "raise RuntimeError('fixture import failure')\n"
    )
    subprocess.run(["git", "-C", str(broken), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(broken),
            "-c",
            "user.email=fixture@example.test",
            "-c",
            "user.name=Fixture",
            "commit",
            "-qm",
            "Frozen failing fixture",
        ],
        check=True,
    )
    broken_head = subprocess.check_output(
        ["git", "-C", str(broken), "rev-parse", "HEAD"], text=True
    ).strip()
    with pytest.raises(RuntimeError, match="fixture import failure"):
        official_calculator(broken, tmp_path / "cache-broken", broken_head)
    current = {
        name: module
        for name, module in sys.modules.items()
        if name == "webmainbench" or name.startswith("webmainbench.")
    }
    assert current == previous
