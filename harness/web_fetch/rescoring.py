"""Recompute shared task metrics from immutable evidence; never call providers."""

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
from harness.record_io import parse_jsonl
from harness.web_fetch.artifacts import verify_record_artifacts, file_scoring_metadata
from harness.web_fetch.failure_diagnosis import diagnose
from lexbench_webfetch.metrics import summarize
from harness.web_fetch.models import execution_id
from harness.web_fetch.reference_lifecycle import (
    ReferenceLifecycleError,
    check_case_equivalent_reference_binding,
    check_case_reference_binding,
    check_case_reference_revision_binding,
    check_case_scoring_revision_binding,
    requires_fresh_reference,
)
from harness.web_fetch.report import failure_category, succeeded
from lexbench_webfetch.response_input import apply_request_status
from lexbench_webfetch.paths import contained_path
from harness.web_fetch.safety import validate_report_ids
from lexbench_webfetch.scoring import SCORING_SOURCE_SHA256, load_list_reference, score_attempt


def _rescore_one(arguments):
    r,c,source,audit,validate_only=arguments
    original = deepcopy(r) if validate_only else None
    raw = contained_path(source, r["artifact_path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != r["metrics"]["content_sha256"]:
        raise ValueError("Output integrity failure: " + r["attempt_id"])
    before = succeeded(r, c)
    # Status and artifact normalization are shared with live execution.
    r["metrics"] = score_attempt(
        c,
        raw,
        ok=bool(r["ok"]),
        status_code=r.get("status_code"),
        title=r.get("title"),
        final_url=r.get("final_url"),
        metadata=file_scoring_metadata(c, r.get("provider_metadata", {}), source),
        previous_metrics=r["metrics"],
        list_reference=load_list_reference(c, source),
    )
    apply_request_status(r)
    if validate_only:
        if r != original:
            raise ValueError('Scoring validation would change a retained attempt: ' + r['attempt_id'])
        return r, None
    if "run_id" not in r:
        r["run_id"] = execution_id(r)
    r["diagnosis"] = diagnose(r, c, raw.decode("utf-8", errors="replace"))
    r["failure_category"] = failure_category(r, c)
    r["scoring"] = {**r.get("scoring", {}), **audit}
    after = succeeded(r, c)
    change={"attempt_id":r["attempt_id"],"before":before,"after":after} if before!=after else None
    return r,change


def rescore(source, case_ids=None, *, allow_rule_revision=False, reference_revision_hashes=None,
            validate_only=False, jobs=1):
    if type(jobs) is not int or not 1<=jobs<=8:
        raise ValueError('Offline scoring jobs must be between 1 and 8')
    dataset_path = contained_path(source, "dataset.json")
    dataset_bytes = dataset_path.read_bytes()
    dataset = json.loads(dataset_bytes)
    path = contained_path(source, "results.jsonl")
    original_results = path.read_bytes()
    records = parse_jsonl(original_results)
    validate_report_ids(dataset, records)
    cases = {c["id"]: c for c in dataset["cases"]}
    execution = contained_path(source, "execution.json")
    batches = (
        json.loads(execution.read_text())["batches"]
        if execution.exists()
        else [json.loads(contained_path(source, "manifest.json").read_text())]
    )
    for record in records:
        reference_hash = record.get("metrics", {}).get("reference_sha256")
        owners = [b for b in batches if record["attempt_id"] in b.get("attempt_ids", [])]
        bound = any(
            record["case_id"] in b.get("reference_checks", {}).get("cases", {}) for b in owners
        )
        if not bound:
            if reference_hash is None or not requires_fresh_reference(cases[record["case_id"]]):
                continue
            raise ReferenceLifecycleError(
                record["attempt_id"] + ": fresh reference record has no original binding"
            )
        if len(owners) != 1:
            raise ReferenceLifecycleError(
                record["attempt_id"] + ": bound record requires one original execution batch"
            )
        binding = owners[0].get("reference_checks", {}).get("cases", {}).get(record["case_id"])
        revised_hashes = (reference_revision_hashes or {}).get(record["case_id"])
        revision_applies = bool(
            revised_hashes and binding and binding.get("reference_sha256") in revised_hashes
        )
        if not binding or (
            not revision_applies and binding.get("reference_sha256") != reference_hash
        ):
            raise ReferenceLifecycleError(
                record["attempt_id"] + ": bound record has no matching original reference"
            )
        checker = (
            (
                lambda case, binding: check_case_reference_revision_binding(
                    case, binding, revised_hashes
                )
            )
            if revision_applies
            else check_case_scoring_revision_binding
            if allow_rule_revision
            else check_case_equivalent_reference_binding
            if dataset.get("comparison", {}).get("reference_binding_mode")
            == "equivalent_reference_bytes_with_independent_batch_captures"
            else check_case_reference_binding
        )
        checker(cases[record["case_id"]], binding)
    if not any(case_ids is None or r["case_id"] in case_ids for r in records):
        return []
    for record in records:
        verify_record_artifacts(source, record, native_cases=cases)
    # One offline scoring batch, never a new provider invocation. Include this
    # script and diagnosis/report dispatch as well as the shared scorer sources.
    audit = {
        "mode": "offline_rescore",
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": hashlib.sha256(
            b"\0".join(
                [
                    Path(__file__).read_bytes(),
                    SCORING_SOURCE_SHA256.encode("ascii"),
                    (ROOT / "harness/web_fetch/failure_diagnosis.py").read_bytes(),
                    (ROOT / "harness/web_fetch/report.py").read_bytes(),
                ]
            )
        ).hexdigest(),
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "input_results_sha256": hashlib.sha256(original_results).hexdigest(),
        "request_status_basis": "shared_normalization_with_original_adapter_status_retained",
        "rule_revision": bool(allow_rule_revision),
        "reference_revision": bool(reference_revision_hashes),
    }
    changes = []
    selected=[index for index,row in enumerate(records) if case_ids is None or row['case_id'] in case_ids]
    def arguments(index):
        row=records[index]
        return row,cases[row['case_id']],source,audit,validate_only
    def accept(index,computed):
        row,change=computed
        records[index]=row
        if change is not None:changes.append(change)
    if jobs==1:
        for index in selected:
            accept(index,_rescore_one(arguments(index)))
    else:
        from collections import deque
        from concurrent.futures import ProcessPoolExecutor
        # Bound queued records as well as workers: saved answers and diagnostics
        # can be large, so eager map would retain many duplicated payloads.
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            remaining=iter(selected)
            pending=deque((index,pool.submit(_rescore_one,arguments(index)))
                          for index in [next(remaining,None) for _ in range(jobs)] if index is not None)
            while pending:
                index,future=pending.popleft()
                accept(index,future.result())
                following=next(remaining,None)
                if following is not None:
                    pending.append((following,pool.submit(_rescore_one,arguments(following))))
    temporary = contained_path(source, "results.jsonl.tmp")
    if path.read_bytes() != original_results or dataset_path.read_bytes() != dataset_bytes:
        raise ValueError("Scoring inputs changed during rescoring")
    if validate_only:
        return []
    # JSON Lines readers split on physical line boundaries. Escaping Unicode
    # line/paragraph separators keeps one attempt on exactly one line too.
    temporary.write_text("".join(json.dumps(r, ensure_ascii=True) + "\n" for r in records))
    temporary.replace(path)
    contained_path(source, "summary.json").write_text(
        json.dumps(summarize(records), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return changes


def main(argv=None):
    import argparse

    from harness.web_fetch.storage import current_evidence_dir

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, nargs="?", default=None)
    args = parser.parse_args(argv)
    print(json.dumps(rescore(args.source or current_evidence_dir()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


def _score_published_row(arguments):
    """Score verified saved bytes without changing a physical call's facts."""
    record, case, path, digest, view = arguments
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('Published scoring input changed: ' + record['attempt_id'])
    result = deepcopy(record)
    result['metrics'] = score_attempt(case, raw, ok=record['ok'],
        metadata=record.get('provider_metadata'), previous_metrics=record['metrics'],
        status_code=record.get('status_code'), title=record.get('title'),
        final_url=record.get('final_url'))
    result.pop('diagnosis', None)
    result.pop('failure_category', None)
    result['scoring'] = {'mode': 'published_snapshot_rescore',
        'source_sha256': SCORING_SOURCE_SHA256, 'provider_calls_added': 0,
        'artifact_view': view, 'original_content_sha256': record['metrics']['content_sha256'],
        'scored_content_sha256': digest}
    return result


def score_published_snapshot(source, dataset, *, jobs=1, recovered=None, progress=None):
    """Recompute a published matrix, disclosing privacy views per result.

    This explicit diagnostic boundary cannot certify a cohort or replace private
    evidence. Every changed artifact must match the saved publication receipt;
    an exact recovered original takes precedence over its public view.
    """
    from concurrent.futures import ProcessPoolExecutor
    from collections import deque
    from harness.record_io import file_sha256
    from lexbench_webfetch.metrics import task_passed
    if type(jobs) is not int or not 1 <= jobs <= 8:
        raise ValueError('Offline scoring jobs must be between 1 and 8')
    source = Path(source).resolve()
    records = parse_jsonl(contained_path(source, 'results.jsonl').read_bytes())
    original = json.loads(contained_path(source, 'dataset.json').read_text())
    validate_report_ids(original, records)
    cases = {case['id']: case for case in dataset['cases']}
    old_cases = {case['id']: case for case in original['cases']}
    from .scoring_revision import ALLOWED_FIELDS
    if cases.keys() != old_cases.keys() or dataset.get('coverage_plan') != original.get('coverage_plan'):
        raise ValueError('Published rescoring must retain the complete matrix')
    for key, prior in old_cases.items():
        changes = {field for field in prior.keys() | cases[key].keys()
                   if prior.get(field) != cases[key].get(field)}
        if changes - ALLOWED_FIELDS:
            raise ValueError('Published rescoring changed collection inputs: ' + key)
    privacy_path = contained_path(source, 'publication-privacy.json')
    privacy = json.loads(privacy_path.read_text()) if privacy_path.is_file() else {'files': []}
    views = {item['path']: item for item in privacy['files'] if item.get('scored_output')}
    recovered = recovered or {}
    unknown = set(recovered) - {row['artifact_path'] for row in records}
    if unknown:
        raise ValueError('Recovered output is outside the matrix')
    requests, inputs = [], []
    for row in records:
        relative = row['artifact_path']
        path = contained_path(source, relative, reject_symlinks=True)
        published_hash = file_sha256(path)
        original_hash = row['metrics']['content_sha256']
        if published_hash != original_hash:
            proof = views.get(relative, {})
            if (proof.get('original_sha256') != original_hash
                    or proof.get('published_sha256') != published_hash
                    or proof.get('published_bytes') != path.stat().st_size):
                raise ValueError('Output lacks an exact privacy binding: ' + row['attempt_id'])
        view = 'published_projection' if published_hash != original_hash else 'original'
        if relative in recovered:
            path = Path(recovered[relative])
            if path.is_symlink() or file_sha256(path) != original_hash:
                raise ValueError('Recovered original hash differs: ' + row['attempt_id'])
            digest, view = original_hash, 'recovered_original'
        else:
            digest = published_hash
        inputs.append({'attempt_id': row['attempt_id'], 'path': relative,
            'original_sha256': original_hash, 'published_sha256': published_hash,
            'scored_sha256': digest, 'view': view})
        requests.append((row, cases[row['case_id']], str(path), digest, view))
    results = []
    def accept(result):
        results.append(result)
        if progress:
            progress(len(results), len(records))
    if jobs == 1:
        for request in requests:
            accept(_score_published_row(request))
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            remaining = iter(requests)
            pending = deque(pool.submit(_score_published_row, request)
                            for request in [next(remaining, None) for _ in range(jobs)]
                            if request is not None)
            while pending:
                accept(pending.popleft().result())
                following = next(remaining, None)
                if following is not None:
                    pending.append(pool.submit(_score_published_row, following))
    from harness.record_io import canonical_json_sha256
    receipt = {'kind': 'published_snapshot_rescore', 'provider_calls_added': 0,
        'corrected_dataset_sha256': canonical_json_sha256(dataset),
        'input_dataset_sha256': file_sha256(source / 'dataset.json'),
        'input_results_sha256': file_sha256(source / 'results.jsonl'),
        'scoring_source_sha256': SCORING_SOURCE_SHA256, 'inputs': inputs,
        'result_count': len(results), 'case_count': len(cases),
        'projected_scoring_inputs': sum(item['view'] == 'published_projection' for item in inputs),
        'recovered_originals': sum(item['view'] == 'recovered_original' for item in inputs),
        'task_passes': sum(task_passed(row['ok'], row['metrics'], cases[row['case_id']].get('task_kind','body'))
                           for row in results)}
    return results, receipt
