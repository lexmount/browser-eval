"""Parallel API calls must retain the same evidence and restart guarantees."""

import multiprocessing
import os
import time
from unittest.mock import patch

import pytest

from harness.web_extract.example import create
from harness.web_extract.pipeline import digest, load_run, probe, read_json
from tests.web_extract.snapshot_inputs import serve_input
from tests.web_extract.test_execution_boundaries import collection_context


def policy():
    return {
        "purpose": "quality",
        "max_in_flight": 2,
        "resources": {"cloudflare-json": {"capacity": 2}},
        "backend": "process",
    }


def test_parallel_calls_overlap_and_completed_results_are_never_replayed(tmp_path):
    home = create(tmp_path / "example")
    target = home / "runs/concurrent"
    with multiprocessing.Manager() as shared:
        barrier, count, lock = shared.Barrier(2), shared.Value("i", 0), shared.Lock()
        observed = shared.list()

        def response(*args, **kwargs):
            with lock:
                count.value += 1
                ordinal = count.value
            observed.append((os.getpid(), time.monotonic()))
            if ordinal <= 2:
                barrier.wait(timeout=3)
            return {"http_status": 200, "response": {"result": {"sku": "ordinary value"}}}

        with (
            collection_context(),
            patch("harness.web_extract.pipeline.provider_call", side_effect=response),
        ):
            probe(home, ["cloudflare-json"], full=True, run_dir=target, execution_policy=policy())
        contract, rows = load_run(target)
        assert contract["concurrency"] == 2
        assert len(rows) == count.value == 3
        assert len({pid for pid, _ in observed}) == 2
        assert all(row["http_status"] == 200 for row in rows)
        with collection_context(), patch("harness.web_extract.pipeline.provider_call") as call:
            probe(home, ["cloudflare-json"], full=True, run_dir=target, execution_policy=policy())
            call.assert_not_called()
        changed = {**policy(), "max_in_flight": 3}
        with collection_context(), pytest.raises(ValueError, match="concurrency"):
            probe(home, ["cloudflare-json"], full=True, run_dir=target, execution_policy=changed)
        (target / "execution/benchmark_execution.py").write_text(
            "changed scheduler", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="source changed"):
            load_run(target)


def test_parallel_calls_obey_the_declared_start_interval(tmp_path):
    home = create(tmp_path / "example")
    with multiprocessing.Manager() as shared:
        starts = shared.list()

        def response(*args, **kwargs):
            starts.append(time.monotonic())
            time.sleep(0.08)
            return {"http_status": 200, "response": {"result": {"sku": "value"}}}

        with (
            collection_context(),
            patch("harness.web_extract.pipeline.provider_call", side_effect=response),
        ):
            probe(
                home,
                ["cloudflare-json"],
                full=True,
                run_dir=home / "runs/paced",
                min_call_interval=0.05,
                execution_policy=policy(),
            )
        ordered = sorted(starts)
        assert len(ordered) == 3
        assert all(b - a >= 0.045 for a, b in zip(ordered, ordered[1:], strict=False))


@pytest.mark.parametrize("assigned", [False, True])
def test_multi_method_collection_never_ignores_a_shared_account(tmp_path, assigned):
    home = create(tmp_path / "example")
    methods = ["cloudflare-json", "firecrawl-json"]
    execution = {
        "purpose": "quality",
        "max_in_flight": 2,
        "backend": "process",
        "resources": {method: {"capacity": 2} for method in methods},
    }
    execution["resources"]["shared-account"] = {"capacity": 1, "reason": "One account limit"}
    if assigned:
        execution["assignments"] = {method: [method, "shared-account"] for method in methods}
    with multiprocessing.Manager() as shared:
        active, maximum, calls, lock = (
            shared.Value("i", 0),
            shared.Value("i", 0),
            shared.Value("i", 0),
            shared.Lock(),
        )

        def response(*args, **kwargs):
            with lock:
                active.value += 1
                maximum.value = max(maximum.value, active.value)
                calls.value += 1
            time.sleep(0.04)
            with lock:
                active.value -= 1
            return {
                "http_status": 200,
                "response": {"result": {"sku": "value"}, "data": {"json": {"sku": "value"}}},
            }

        with (
            collection_context(),
            patch.dict(os.environ, {"FIRECRAWL_API_KEY": "synthetic"}),
            patch("harness.web_extract.pipeline.qualify_fixture", side_effect=serve_input),
            patch("harness.web_extract.pipeline.provider_call", side_effect=response),
        ):
            if assigned:
                probe(
                    home,
                    methods,
                    full=True,
                    fixture_base="https://snapshots.example",
                    run_dir=home / "runs/shared",
                    execution_policy=execution,
                )
                assert calls.value == 6 and maximum.value == 1
            else:
                with pytest.raises(ValueError, match="assignments"):
                    probe(
                        home,
                        methods,
                        full=True,
                        fixture_base="https://snapshots.example",
                        run_dir=home / "runs/shared",
                        execution_policy=execution,
                    )
                assert calls.value == 0


def test_failure_repair_preserves_parent_concurrency_and_binds_its_scheduler(tmp_path):
    from harness.web_extract.repair import load_selection, run
    from tests.web_extract.test_repair_review import parent_inputs

    home, source, profile, _ = parent_inputs(tmp_path, success_count=0, execution_policy=policy())
    assert read_json(profile)["participants"]["cloudflare-json"]["execution_policy"] == policy()
    target = home / "runs/repair"
    with multiprocessing.Manager() as shared:
        barrier, counter, lock = shared.Barrier(2), shared.Value("i", 0), shared.Lock()

        def response(*args, **kwargs):
            with lock:
                counter.value += 1
                ordinal = counter.value
            if ordinal <= 2:
                barrier.wait(timeout=3)
            return {"http_status": 200, "response": {"result": {"sku": "fixed"}}}

        with (
            collection_context(),
            patch("harness.web_extract.repair.validate_dataset"),
            patch("harness.web_extract.pipeline.provider_call", side_effect=response),
        ):
            run(home, profile, "cloudflare-json", target)
        assert counter.value == 3
    contract = read_json(target / "contract.json")
    assert contract["execution_policy"] == policy()
    binding = {
        "run": str(source),
        "repair": [
            {"path": str(target), "sha256": digest((target / "certificate.json").read_bytes())}
        ],
    }
    load_selection(home, binding)
    (target / "execution/benchmark_execution.py").write_bytes(b"changed scheduler")
    with pytest.raises(ValueError, match="execution source"):
        load_selection(home, binding)


def test_interrupted_parallel_dispatch_preserves_other_responses_and_blocks_replay(tmp_path):
    home = create(tmp_path / "example")
    target = home / "runs/interrupted"
    with multiprocessing.Manager() as shared:
        counter, lock, barrier = shared.Value("i", 0), shared.Lock(), shared.Barrier(2)

        def response(*args, **kwargs):
            with lock:
                counter.value += 1
                ordinal = counter.value
            if ordinal <= 2:
                barrier.wait(timeout=3)
            if ordinal == 1:
                raise KeyboardInterrupt()
            return {"http_status": 200, "response": {"result": {"sku": "retained"}}}

        with (
            collection_context(),
            patch("harness.web_extract.pipeline.provider_call", side_effect=response),
        ):
            with pytest.raises(KeyboardInterrupt):
                probe(
                    home, ["cloudflare-json"], full=True, run_dir=target, execution_policy=policy()
                )
        journal = (target / "calls.journal.jsonl").read_text(encoding="utf-8")
        assert len(journal.splitlines()) == counter.value - 1
        assert len(read_json(target / "inflight.json")["attempts"]) == 1
        with collection_context(), patch("harness.web_extract.pipeline.provider_call") as call:
            with pytest.raises(ValueError, match="Ambiguous"):
                probe(
                    home, ["cloudflare-json"], full=True, run_dir=target, execution_policy=policy()
                )
            call.assert_not_called()
