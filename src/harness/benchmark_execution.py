"""Bound benchmark work by declared resource isolation, not a global serial loop.

Tracks own calls, evidence, retries and verdicts. This owner schedules ready work
and drains dispatched calls before stopping; it never retries a job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing
import signal
import threading
from concurrent.futures import (
    FIRST_COMPLETED,
    BrokenExecutor,
    CancelledError,
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
)
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def exclusive_policy(resource, reason, *, purpose="quality"):
    """Describe a stateful environment whose full lifecycle has one owner."""
    value = {
        "purpose": purpose,
        "max_in_flight": 1,
        "serial_reason": reason,
        "resources": {resource: {"capacity": 1, "reason": reason}},
    }
    ExecutionPolicy.from_dict(value)
    return value


def assess_execution(plan, *, evidence_root=None):
    """Check a measured preflight and project batch time before collection."""
    if not isinstance(plan, dict):
        raise ValueError("Execution assessment must be an object")
    policy = ExecutionPolicy.from_dict(plan.get("execution_policy"))
    calls = plan.get("planned_calls")
    if type(calls) is not int or calls < 1:
        raise ValueError("planned_calls must be a positive integer")
    sample = plan.get("preflight")
    if not isinstance(sample, dict):
        raise ValueError("Record a bounded measured preflight before collection")
    count, seconds = sample.get("completed_calls"), sample.get("wall_seconds")
    if type(count) is not int or not 1 <= count <= calls:
        raise ValueError("Invalid preflight call population")
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Preflight wall_seconds must be finite and positive")
    if (
        type(sample.get("max_in_flight")) is not int
        or sample["max_in_flight"] != policy.max_in_flight
    ):
        raise ValueError("Preflight concurrency differs from the execution plan")
    identity = sample.get("evidence_sha256")
    if (
        not isinstance(identity, str)
        or len(identity) != 64
        or any(c not in "0123456789abcdef" for c in identity)
    ):
        raise ValueError("Bind the preflight evidence hash")
    if evidence_root is not None:
        root = Path(evidence_root).resolve()
        if not isinstance(sample.get("evidence_path"), str):
            raise ValueError("Bind a preflight evidence_path")
        relative = Path(sample.get("evidence_path", ""))
        source = root / relative
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not source.is_file()
            or source.is_symlink()
            or source.resolve() != source
            or not source.resolve().is_relative_to(root)
        ):
            raise ValueError("Missing or unsafe preflight evidence")
        if hashlib.sha256(source.read_bytes()).hexdigest() != identity:
            raise ValueError("Preflight evidence changed")
        receipt = json.loads(source.read_text(encoding="utf-8"))
        for key in ("completed_calls", "wall_seconds", "max_in_flight"):
            if type(receipt.get(key)) is not type(sample[key]) or receipt[key] != sample[key]:
                raise ValueError("Preflight summary differs from bound evidence")
    setup = plan.get("setup_seconds", 0)
    budget = plan.get("wall_time_budget_seconds")
    if type(setup) not in (int, float) or not math.isfinite(setup) or setup < 0:
        raise ValueError("Invalid setup time")
    if type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0:
        raise ValueError("Declare a finite positive wall_time_budget_seconds")
    estimate = setup + calls * seconds / count
    reason = plan.get("over_budget_reason")
    if estimate > budget and (not isinstance(reason, str) or not reason.strip()):
        raise ValueError(
            "Estimated execution exceeds the budget; revise the plan or explain the constraint"
        )
    return {
        "planned_calls": calls,
        "sample_calls": count,
        "estimated_wall_seconds": estimate,
        "wall_time_budget_seconds": budget,
        "purpose": policy.purpose,
        "max_in_flight": policy.max_in_flight,
        "estimate_scope": "Projection from the bound preflight; not a latency ranking",
    }


def validate_capacity(value, label="capacity"):
    if type(value) is not int or not 1 <= value <= 64:
        raise ValueError(f"{label} must be an integer between 1 and 64")
    return value


@dataclass(frozen=True)
class ExecutionPolicy:
    purpose: str
    max_in_flight: int
    resources: dict[str, int]
    serial_reasons: dict[str, str]
    backend: str = "thread"

    @classmethod
    def from_dict(cls, value):
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("purpose"), str)
            or value["purpose"] not in {"quality", "latency", "throughput"}
        ):
            raise ValueError("Execution purpose must be quality, latency or throughput")
        maximum = validate_capacity(value.get("max_in_flight"), "max_in_flight")
        reason = value.get("serial_reason", "")
        if maximum == 1 and (not isinstance(reason, str) or not reason.strip()):
            raise ValueError("Serial execution requires a concrete serial_reason")
        resources = value.get("resources")
        if not isinstance(resources, dict) or not resources:
            raise ValueError("Declare the shared resources before execution")
        capacities, reasons = {}, {}
        for name, resource in resources.items():
            if not isinstance(name, str) or not name.strip() or not isinstance(resource, dict):
                raise ValueError("Invalid execution resource")
            capacities[name] = validate_capacity(resource.get("capacity"), "Resource capacity")
            reason = resource.get("reason", "")
            if capacities[name] == 1 and (not isinstance(reason, str) or not reason.strip()):
                raise ValueError("An exclusive resource requires a concrete reason")
            reasons[name] = reason
            if value["purpose"] == "latency" and capacities[name] != 1:
                raise ValueError("Isolated latency requires exclusive measurement resources")
        backend = value.get("backend", "thread")
        if backend not in {"thread", "process"}:
            raise ValueError("Execution backend must be thread or process")
        return cls(value["purpose"], maximum, capacities, reasons, backend)


@dataclass(frozen=True)
class Job:
    key: str
    resources: tuple[str, ...]
    payload: Any


@contextmanager
def _retain_call_ownership(on_interrupt=None):
    """Defer Ctrl-C until a future and its durable callback have a known owner."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGINT)
    interrupted = None

    def defer(number, frame):
        nonlocal interrupted
        interrupted = (number, frame)
        if on_interrupt is not None:
            on_interrupt()

    signal.signal(signal.SIGINT, defer)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)
        if interrupted is not None and previous != signal.SIG_IGN:
            if callable(previous):
                previous(*interrupted)
            else:
                raise KeyboardInterrupt()


class _InlineExecutor:
    """Keep a single native call on the main thread, including its signal deadline."""

    def submit(self, execute, payload):
        future = Future()
        try:
            future.set_result(execute(payload))
        except BaseException as error:
            future.set_exception(error)
        return future


def run_jobs(
    jobs,
    execute,
    commit,
    policy,
    *,
    prepare=None,
    should_stop=lambda: False,
    initialize=None,
    initargs=(),
    stop_event=None,
):
    """Dispatch a bounded set; commit every terminal call before propagating errors.

    Inventories contain small task descriptions. Expensive payload preparation
    happens only after resource admission. Only the parent calls prepare/commit,
    so each track can retain its existing durable evidence and budget boundary.
    """
    pending = list(jobs)
    keys = set()
    for job in pending:
        if (
            not isinstance(job, Job)
            or not isinstance(job.key, str)
            or not job.key
            or job.key in keys
        ):
            raise ValueError("Execution job IDs must be nonempty and globally unique")
        if (
            not job.resources
            or len(set(job.resources)) != len(job.resources)
            or any(r not in policy.resources for r in job.resources)
        ):
            raise ValueError("Every job must bind registered, distinct resources")
        keys.add(job.key)
    active = {name: 0 for name in policy.resources}
    inflight = {}
    failure = None
    stop_event = stop_event if stop_event is not None else threading.Event()
    options = {"max_workers": policy.max_in_flight}
    if initialize is not None:
        options.update(initializer=initialize, initargs=initargs)
    executor_class = ThreadPoolExecutor
    if policy.backend == "process":
        executor_class = ProcessPoolExecutor
        # Fresh interpreters avoid copying a parent that may already own threads.
        options["mp_context"] = multiprocessing.get_context("spawn")

    def stop(error):
        nonlocal failure
        if failure is None:
            failure = error
            stop_event.set()

    with (
        _retain_call_ownership(lambda: stop(KeyboardInterrupt())),
        (nullcontext(_InlineExecutor()) if policy.max_in_flight == 1
         else executor_class(**options)) as executor,
    ):
        if pending and policy.max_in_flight == 1 and initialize is not None:
            initialize(*initargs)
        while pending or inflight:
            try:
                if failure is None and not stop_event.is_set() and not should_stop():
                    for job in list(pending):
                        if (failure is not None or stop_event.is_set()
                                or len(inflight) >= policy.max_in_flight):
                            break
                        if any(active[r] >= policy.resources[r] for r in job.resources):
                            continue
                        payload = prepare(job) if prepare else job.payload
                        if failure is not None or stop_event.is_set() or should_stop():
                            if prepare is not None:
                                commit(job, None, CancelledError("Stopped before dispatch"))
                            break
                        try:
                            future = executor.submit(execute, payload)
                        except RuntimeError as error:
                            rejected = isinstance(error, BrokenExecutor) or str(error) in {
                                "cannot schedule new futures after shutdown",
                                "cannot schedule new futures after interpreter shutdown",
                            }
                            if rejected and prepare is not None:
                                try:
                                    commit(job, None, CancelledError("Submission rejected"))
                                finally:
                                    raise error
                            raise
                        inflight[future] = job
                        for resource in job.resources:
                            active[resource] += 1
                        pending.remove(job)
            except BaseException as error:
                stop(error)
            if not inflight:
                break
            try:
                done, _ = wait(inflight, return_when=FIRST_COMPLETED)
                for future in done:
                    try:
                        job = inflight[future]
                        value = error = None
                        try:
                            value = future.result()
                        except BaseException as caught:
                            error = caught
                        try:
                            commit(job, value, error)
                        finally:
                            inflight.pop(future)
                            for resource in job.resources:
                                active[resource] -= 1
                    except BaseException as caught:
                        stop(caught)
            except BaseException as error:
                # An operator interruption stops admission, while dispatched calls
                # still reach the track's durable commit boundary.
                stop(error)
                continue
    if failure is not None:
        raise failure


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            assess_execution(
                json.loads(args.plan.read_text(encoding="utf-8")), evidence_root=args.plan.parent
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
