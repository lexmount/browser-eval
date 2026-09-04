"""One end-to-end answer deadline for every adapter, including local processing."""
import hashlib
import queue
import threading
import time

from .models import ProviderResult
from .response_input import answer_input


def prepare_answer(result, case):
    # This required representation conversion is inside the measured call.
    result.prepared_answer = answer_input(result.content.encode('utf-8', errors='replace'), result.metadata,
                 ok=result.ok, status_code=result.status_code)
    if case.task_kind == 'file':
        files = []
        for item in result.files:
            raw = item.get('data')
            if not isinstance(raw, bytes):
                raise ValueError('Provider file data must be bytes')
            mime = ('image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else
                    'video/webm' if raw.startswith(b'\x1aE\xdf\xa3') else
                    'video/mp4' if len(raw) >= 12 and raw[4:8] == b'ftyp' else None)
            expected = case.source_verification.get('mime', '').split(';')[0]
            digest = hashlib.sha256(raw).hexdigest()
            expected_hash = case.source_verification.get('file_sha256')
            files.append({'bytes': len(raw), 'sha256': digest,
                          'mime': mime, 'pass': bool(len(raw) >= 32 and mime and (not expected or mime == expected)
                                                   and (not expected_hash or digest == expected_hash))})
        result.metadata['file_evaluation'] = {'files': files, 'pass': any(f['pass'] for f in files),
            'scope': 'returned_bytes_signature_length_sha256; not semantic media validation'}
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
            value = prepare_answer(provider.fetch(case, timeout), case)
        except Exception as error:
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
        provider.cancel()
        return ProviderResult(False, '', error_type='timeout', error_message=str(value)), worker
    if isinstance(value, Exception):
        raise value
    return value, worker
