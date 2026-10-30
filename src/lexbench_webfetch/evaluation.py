"""Public standalone scoring boundary for saved responses and actual files."""
from .file_acceptance import evaluate_files
from .metrics import task_passed, task_measured
from .scoring import score_attempt


def evaluate_response(case: dict, response: bytes, *, files=(), ok=True, metadata=None,
                      list_reference=None,
                      status_code=None, title=None, final_url=None) -> dict:
    metadata = dict(metadata or {})
    # Acceptance of delivered files can only be derived from actual bytes.
    metadata.pop('file_evaluation', None)
    if case.get('task_kind') == 'file' or files:
        metadata['file_evaluation'] = evaluate_files(case, list(files))
    metrics = score_attempt(case, response, ok=ok, metadata=metadata,
                            list_reference=list_reference,
                            status_code=status_code,
                            title=title, final_url=final_url)
    return {'passed': task_passed(metrics.get('request_ok', ok), metrics, case.get('task_kind', 'body')),
            'measured': task_measured(metrics, case.get('task_kind', 'body')),
            'metrics': metrics}
