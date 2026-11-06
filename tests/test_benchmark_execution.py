"""Execution must overlap independent work without mixing shared state."""

import threading
import time
from unittest.mock import patch

import pytest

from harness.benchmark_execution import ExecutionPolicy, Job, assess_execution, run_jobs


def policy(**updates):
    value = {
        "purpose": "quality",
        "max_in_flight": 4,
        "resources": {"api-a": {"capacity": 2}, "api-b": {"capacity": 2}},
    }
    value.update(updates)
    return ExecutionPolicy.from_dict(value)


def test_independent_services_overlap_and_each_service_obeys_its_limit():
    lock = threading.Lock()
    active = {"api-a": 0, "api-b": 0}
    peak = dict(active)
    simultaneous_services = []
    results = []

    def execute(item):
        with lock:
            active[item] += 1
            peak[item] = max(peak[item], active[item])
            simultaneous_services.append(all(active.values()))
        time.sleep(0.02)
        with lock:
            active[item] -= 1
        return item

    jobs = [Job(str(i), (group,), group) for group in active for i in range(6)]
    # IDs are global, not only unique within a provider.
    jobs = [Job(f"{j.payload}-{j.key}", j.resources, j.payload) for j in jobs]
    run_jobs(
        jobs, execute, lambda job, value, error: results.append((job.key, value, error)), policy()
    )
    assert len(results) == 12 and all(error is None for _, _, error in results)
    assert peak == {"api-a": 2, "api-b": 2}
    assert any(simultaneous_services)


def test_shared_mutable_environment_stays_exclusive_while_other_work_runs():
    barrier = threading.Barrier(2, timeout=2)
    observed = []

    def execute(value):
        if value in ("stateful-1", "independent"):
            barrier.wait()
        observed.append(value)
        return value

    p = policy(
        resources={
            "site": {"capacity": 1, "reason": "Shared database reset"},
            "other": {"capacity": 1, "reason": "Isolated latency"},
        }
    )
    jobs = [
        Job("one", ("site",), "stateful-1"),
        Job("two", ("site",), "stateful-2"),
        Job("other", ("other",), "independent"),
    ]
    run_jobs(jobs, execute, lambda *args: None, p)
    assert observed.index("stateful-1") < observed.index("stateful-2")


def test_stop_after_denial_drains_and_records_already_started_calls():
    started = threading.Barrier(2, timeout=2)
    retained = []
    stopped = False

    def execute(value):
        started.wait()
        return value

    def commit(job, value, error):
        nonlocal stopped
        retained.append(value)
        stopped = True

    p = policy(max_in_flight=2)
    run_jobs(
        [Job(str(i), ("api-a",), i) for i in range(8)],
        execute,
        commit,
        p,
        should_stop=lambda: stopped,
    )
    assert sorted(retained) == [0, 1]


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "4", 65])
def test_invalid_limits_fail_before_work_starts(bad):
    with pytest.raises(ValueError):
        policy(max_in_flight=bad)


def test_serial_quality_execution_requires_a_concrete_reason():
    with pytest.raises(ValueError, match="reason"):
        policy(max_in_flight=1)


def test_preparation_failure_drains_work_already_dispatched():
    retained = []

    def prepare(job):
        if job.key == "second":
            raise ValueError("No remaining physical call budget")
        return job.payload

    with pytest.raises(ValueError, match="budget"):
        run_jobs(
            [Job("first", ("api-a",), 1), Job("second", ("api-a",), 2)],
            lambda value: value,
            lambda job, value, error: retained.append(value),
            policy(),
            prepare=prepare,
        )
    assert retained == [1]


def test_operator_interruption_drains_results_before_propagating():
    from harness import benchmark_execution

    original_wait = benchmark_execution.wait
    interrupted = False
    retained = []

    def wait_once(futures, **kwargs):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt()
        return original_wait(futures, **kwargs)

    with patch("harness.benchmark_execution.wait", side_effect=wait_once):
        with pytest.raises(KeyboardInterrupt):
            run_jobs(
                [Job(str(i), ("api-a",), i) for i in range(8)],
                lambda value: value,
                lambda job, value, error: retained.append(value),
                policy(max_in_flight=2),
            )
    assert sorted(retained) == [0, 1]


def test_interrupt_during_preparation_does_not_dispatch_the_prepared_job():
    import signal
    from concurrent.futures import CancelledError

    executed, retained, cancelled = [], [], []

    def prepare(job):
        if job.key == "second":
            signal.raise_signal(signal.SIGINT)
        return job.payload

    def commit(job, value, error):
        if isinstance(error, CancelledError):
            cancelled.append(job.key)
        else:
            assert error is None
            retained.append(value)

    with pytest.raises(KeyboardInterrupt):
        run_jobs([Job("first", ("api-a",), 1), Job("second", ("api-a",), 2)],
                 lambda value: executed.append(value) or value, commit,
                 policy(max_in_flight=2), prepare=prepare)
    assert executed == retained == [1]
    assert cancelled == ["second"]


def test_interrupt_after_submission_retains_the_submitted_call():
    import signal

    from harness import benchmark_execution

    retained = []

    class InterruptedExecutor(benchmark_execution.ThreadPoolExecutor):
        def submit(self, *args, **kwargs):
            future = super().submit(*args, **kwargs)
            signal.raise_signal(signal.SIGINT)
            return future

    with patch.object(benchmark_execution, "ThreadPoolExecutor", InterruptedExecutor):
        with pytest.raises(KeyboardInterrupt):
            run_jobs(
                [Job("one", ("api-a",), 1)],
                lambda value: value,
                lambda job, value, error: retained.append(value),
                policy(),
            )
    assert retained == [1]


def test_interrupt_between_wait_and_commit_retains_every_submitted_call():
    import signal

    from harness import benchmark_execution

    original_wait = benchmark_execution.wait
    interrupted = False
    retained = []

    class InterruptingDone:
        def __init__(self, done):
            self.done = done

        def __iter__(self):
            signal.raise_signal(signal.SIGINT)
            return iter(self.done)

    def wait_once(futures, **kwargs):
        nonlocal interrupted
        done, pending = original_wait(futures, **kwargs)
        if not interrupted:
            interrupted = True
            return InterruptingDone(done), pending
        return done, pending

    with patch.object(benchmark_execution, "wait", side_effect=wait_once):
        with pytest.raises(KeyboardInterrupt):
            run_jobs(
                [Job(str(i), ("api-a",), i) for i in range(8)],
                lambda value: value,
                lambda job, value, error: retained.append(value),
                policy(max_in_flight=2),
            )
    assert sorted(retained) == [0, 1]


def test_stop_predicate_interruption_drains_another_running_call():
    release = threading.Event()
    ready = threading.Barrier(2, timeout=2)
    retained = []
    started = []

    def execute(value):
        started.append(value)
        ready.wait()
        if value == 2:
            assert release.wait(timeout=2)
        return value

    def stop():
        if len(started) == 2:
            release.set()
            raise KeyboardInterrupt()
        return False

    with pytest.raises(KeyboardInterrupt):
        run_jobs(
            [Job(str(i), ("api-a",), i) for i in (1, 2, 3)],
            execute,
            lambda job, value, error: retained.append(value),
            policy(max_in_flight=2),
            should_stop=stop,
        )
    assert sorted(retained) == [1, 2]


def test_serial_reason_cannot_be_a_boolean():
    with pytest.raises(ValueError, match="reason"):
        policy(max_in_flight=1, serial_reason=True)


def test_preflight_estimate_uses_measured_batch_time_and_checks_the_budget():
    plan = {
        "execution_policy": {
            "purpose": "quality",
            "max_in_flight": 4,
            "resources": {"api": {"capacity": 4}},
        },
        "planned_calls": 800,
        "setup_seconds": 10,
        "wall_time_budget_seconds": 3000,
        "preflight": {
            "completed_calls": 4,
            "wall_seconds": 20,
            "max_in_flight": 4,
            "evidence_sha256": "a" * 64,
        },
    }
    with pytest.raises(ValueError, match="budget"):
        assess_execution(plan)
    plan["wall_time_budget_seconds"] = 5000
    assert assess_execution(plan)["estimated_wall_seconds"] == 4010
    plan["preflight"]["max_in_flight"] = 1
    with pytest.raises(ValueError, match="concurrency"):
        assess_execution(plan)


def test_missing_preflight_is_rejected_before_an_execution_estimate():
    with pytest.raises(ValueError, match="preflight"):
        assess_execution(
            {
                "execution_policy": {
                    "purpose": "quality",
                    "max_in_flight": 2,
                    "resources": {"api": {"capacity": 2}},
                },
                "planned_calls": 800,
            }
        )


def test_latency_measurement_cannot_overlap_on_one_resource():
    with pytest.raises(ValueError, match="latency"):
        policy(purpose="latency")


@pytest.mark.parametrize(
    "jobs",
    [[Job("same", ("api-a",), 1), Job("same", ("api-b",), 2)], [Job("bad", ("unregistered",), 1)]],
)
def test_invalid_inventory_fails_before_any_dispatch(jobs):
    called = []
    with pytest.raises(ValueError):
        run_jobs(jobs, called.append, lambda *args: None, policy())
    assert not called
