"""Bound provider calls and prepare answers outside fixed-native response timing."""
import queue
import threading
import time
import traceback

from lexbench_webfetch.file_acceptance import evaluate_files
from lexbench_webfetch.response_input import answer_input

from harness.web_fetch.diagnostics import diagnostic_context, emit
from harness.web_fetch.models import ProviderResult
from harness.web_fetch.providers.base import ProcessCleanupError


def prepare_answer(result, case):
    # A successful service request is not proof that its target page succeeded.
    # Adapters disclose origin status separately; never infer it from API status.
    origin = result.metadata.get('upstream_status')
    origin = origin if type(origin) is int and 100 <= origin <= 599 else None
    result.metadata['upstream_status'] = origin
    result.metadata['upstream_http_status_available'] = origin is not None
    if result.ok and origin is not None and not 200 <= origin < 300:
        result.ok = False
        result.error_type = 'http_error'
        result.error_message = f'Target page returned HTTP {origin}'
    # Prepare scorer input; fixed-native calls have already stopped their timer.
    result.prepared_answer = answer_input(result.content.encode('utf-8', errors='replace'), result.metadata,
                 ok=result.ok, status_code=result.status_code)
    # Persist and fingerprint every returned binary, including incidental
    # downloads produced while a body task is being fetched.  Only ``file``
    # tasks use this evidence for acceptance; body tasks still require text.
    if case.task_kind == 'file' or result.files:
        result.metadata['file_evaluation'] = evaluate_files(
            vars(case), [item.get('data') for item in result.files])
    return result


def worker_unresolved(worker, *, timed_out):
    """Whether a call may still be doing physical work after it returned.

    A timed-out call is unresolved while its thread runs. Any call is
    unresolved once its adapter reported a worker that survived its kill,
    even if that report arrived after the answer deadline. Liveness is read
    before the flag: a finished thread has already recorded its outcome.
    """
    if worker is None:
        return False
    return (timed_out and worker.is_alive()) or worker.process_cleanup_failed


def fetch_with_deadline(provider, case, timeout, *, native_route_ledger=None):
    """Discard late answers; never overlap calls on a non-cancellable adapter.

    A timed-out daemon cannot mutate persisted output. If its adapter cannot
    stop, the runner quarantines that provider and declares remaining cases
    unexecuted. It does not silently run concurrent abandoned paid requests.
    """
    response_only = bool(provider.config.get("uniform_native_parameters"))
    output = queue.Queue(maxsize=1)
    provider.begin_call()
    begin = time.perf_counter()
    def work():
        try:
            with diagnostic_context(getattr(provider, "_call_diagnostics", None)):
                emit("provider_invoked", url=case.url, timeout_seconds=timeout)
                fetch_options = ({'native_route_ledger': native_route_ledger}
                                 if native_route_ledger is not None else {})
                fetched = provider.fetch(case, timeout, **fetch_options)
                if response_only:
                    output.put((time.perf_counter(), fetched))
                    return
                emit("provider_returned", ok=fetched.ok, content_chars=len(fetched.content))
                value = prepare_answer(fetched, case)
                emit("answer_prepared", content_chars=len(value.prepared_answer[0]))
        except Exception as error:
            if isinstance(error, ProcessCleanupError):
                # Recorded before the thread ends, so a caller that sees the
                # thread finished also sees the surviving process.
                worker.process_cleanup_failed = True
            sink=getattr(provider, "_call_diagnostics", None)
            if sink: sink.emit("caller", "provider_exception", type=type(error).__name__, message=str(error),
                               traceback=traceback.format_exc())
            value = error
        output.put((time.perf_counter(), value))
    worker = threading.Thread(target=work, daemon=True, name='webfetch-answer')
    worker.process_cleanup_failed = False
    worker.start()
    caller_deadline_exceeded = False
    try:
        completed, value = output.get(timeout=max(0, timeout - (time.perf_counter() - begin)))
    except queue.Empty:
        caller_deadline_exceeded = True
        completed, value = time.perf_counter(), TimeoutError('End-to-end answer deadline exceeded')
    if completed - begin > timeout:
        caller_deadline_exceeded = True
        value = TimeoutError('End-to-end answer deadline exceeded')
    if caller_deadline_exceeded:
        sink = getattr(provider, "_call_diagnostics", None)
        if sink:
            sink.emit("caller", "answer_timeout", type=type(value).__name__, message=str(value),
                      timeout_seconds=timeout, worker_still_running=worker.is_alive())
        # Completion/cancellation cleanup is not additional answer budget.
        answer_elapsed_ms = round((completed - begin) * 1000, 2)
        provider.cancel()
        cleanup_seconds = max(0.0, float(provider.cancellation_cleanup_seconds))
        if cleanup_seconds:
            worker.join(cleanup_seconds)
        metadata={}
        # Cancellation may finish the adapter's diagnostic collection. Retain
        # that evidence, but never resurrect its late body/status or timing.
        if not worker.is_alive():
            try:
                _, late=output.get_nowait()
            except queue.Empty:
                late=None
            if isinstance(late,ProviderResult):
                metadata.update(late.metadata)
        for key in ('links', 'images', 'videos', 'media', 'extras', 'file_evaluation'):
            metadata.pop(key, None)
        if case.task_kind == 'file':
            metadata['file_evaluation'] = evaluate_files(vars(case), [])
        metadata['_answer_elapsed_ms']=answer_elapsed_ms
        metadata['caller_deadline_exceeded'] = True
        return ProviderResult(
            False,
            '',
            error_type='timeout',
            error_message=str(value),
            metadata=metadata,
        ), worker
    if isinstance(value, Exception):
        raise value
    value.metadata['caller_deadline_exceeded'] = False
    if response_only:
        # Stop the user-observed timer before scorer input preparation/storage.
        value.metadata['_answer_elapsed_ms'] = round((completed - begin) * 1000, 2)
        value.metadata['latency_scope'] = 'native_response_received'
        with diagnostic_context(getattr(provider, "_call_diagnostics", None)):
            emit("provider_returned", ok=value.ok, content_chars=len(value.content))
            value = prepare_answer(value, case)
            emit("answer_prepared", content_chars=len(value.prepared_answer[0]))
    return value, worker
