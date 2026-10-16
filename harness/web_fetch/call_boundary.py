"""One end-to-end answer deadline for every adapter, including local processing."""
import queue
import threading
import time

from .models import ProviderResult
from .diagnostics import diagnostic_context, emit
from lexbench_webfetch.response_input import answer_input
from lexbench_webfetch.file_acceptance import evaluate_files


def prepare_answer(result, case):
    # This required representation conversion is inside the measured call.
    result.prepared_answer = answer_input(result.content.encode('utf-8', errors='replace'), result.metadata,
                 ok=result.ok, status_code=result.status_code)
    # Persist and fingerprint every returned binary, including incidental
    # downloads produced while a body task is being fetched.  Only ``file``
    # tasks use this evidence for acceptance; body tasks still require text.
    if case.task_kind == 'file' or result.files:
        result.metadata['file_evaluation'] = evaluate_files(
            vars(case), [item.get('data') for item in result.files])
    return result


def fetch_with_deadline(provider, case, timeout):
    """Discard late answers; never overlap calls on a non-cancellable adapter.

    A timed-out daemon cannot mutate persisted output. If its adapter cannot
    stop, the runner quarantines that provider and declares remaining cases
    unexecuted. It does not silently run concurrent abandoned paid requests.
    """
    output = queue.Queue(maxsize=1)
    provider.begin_call()
    begin = time.perf_counter()
    def work():
        try:
            with diagnostic_context(getattr(provider, "_call_diagnostics", None)):
                emit("provider_invoked", url=case.url, timeout_seconds=timeout)
                fetched = provider.fetch(case, timeout)
                emit("provider_returned", ok=fetched.ok, content_chars=len(fetched.content))
                value = prepare_answer(fetched, case)
                emit("answer_prepared", content_chars=len(value.prepared_answer[0]))
        except Exception as error:
            sink=getattr(provider, "_call_diagnostics", None)
            if sink: sink.emit("caller", "provider_exception", type=type(error).__name__, message=str(error))
            value = error
        output.put((time.perf_counter(), value))
    worker = threading.Thread(target=work, daemon=True, name='webfetch-answer')
    worker.start()
    try:
        completed, value = output.get(timeout=max(0, timeout - (time.perf_counter() - begin)))
    except queue.Empty:
        completed, value = time.perf_counter(), TimeoutError('End-to-end answer deadline exceeded')
    if completed - begin > timeout:
        value = TimeoutError('End-to-end answer deadline exceeded')
    if isinstance(value, TimeoutError):
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
        return ProviderResult(
            False,
            '',
            error_type='timeout',
            error_message=str(value),
            metadata=metadata,
        ), worker
    if isinstance(value, Exception):
        raise value
    return value, worker
