"""Append and project complete provider cohorts while preserving physical calls."""

import argparse
import hashlib
import json
import shutil
import tempfile
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lexbench_webfetch.paths import contained_path, safe_identifier

from harness.record_io import file_sha256, load_jsonl, parse_jsonl, read_completed_records
from harness.web_fetch.artifacts import (
    materialize_bound_artifacts,
    materialize_reference_checks,
    materialize_task_references,
    public_call_log_binding,
    verify_public_call_log,
    verify_record_artifacts,
)
from harness.web_fetch.reference_inputs import input_tree
from harness.web_fetch.reference_lifecycle import (
    completed_case_records,
    expected_providers,
    finalize_reference_checks,
    reference_cases,
    require_verified_reference_checks,
    requires_fresh_reference,
)
from harness.web_fetch.safety import (
    commit_staged_evidence,
    reject_evidence_symlinks,
    validate_report_ids,
)
from harness.web_fetch.storage import current_evidence_dir


def recover_completed_batch(source: Path, manifest: dict, records: list[dict]) -> dict:
    """Bind all flushed calls from an interrupted fixed-answer physical batch.

    The source manifest stays unchanged. The returned boundary describes only
    saved completed calls, and explicitly retains the original unfinished state.
    It never supplies missing live-reference certification or chooses scores.
    """

    if (manifest.get("schema_version") != 1 or manifest.get("tool") != "web_fetch"
            or manifest.get("completed_at") or manifest.get("attempt_ids") is not None
            or not records):
        raise ValueError("Recovery requires an interrupted versioned physical batch")
    parameters = manifest.get("parameters", {})
    if parameters.get("runs") != 1 or parameters.get("max_failure_retries") != 0:
        raise ValueError("Interrupted recovery requires one call per logical result")

    def verified_input(path, digest):
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError("Recovery input is missing or unsafe")
        raw = path.read_bytes()
        if not digest or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Recovery input hash changed")
        return raw

    input_raw = verified_input(manifest["dataset"]["source"], manifest.get("dataset_sha256"))
    verified_input(manifest["provider_config_source"], manifest.get("provider_config_sha256"))
    dataset = json.loads((source / "dataset.json").read_bytes())
    original_cases = {case["id"]: case for case in json.loads(input_raw)["cases"]}
    cases = {case["id"]: case for case in dataset["cases"]}
    if (len(cases) != len(dataset["cases"])
            or any(original_cases.get(identifier) != case for identifier, case in cases.items())):
        raise ValueError("Recovery task differs from frozen pre-call input")
    providers = {provider["name"]: provider for provider in manifest["providers"]}
    lower = datetime.fromisoformat(manifest["started_at"])
    ends, identities = [], set()
    for row in records:
        key = (row["provider"], row["case_id"])
        if (key in identities or key[0] not in providers or key[1] not in cases
                or row["run"] != 1 or row["provider_type"] != providers[key[0]]["type"]):
            raise ValueError("Interrupted result has duplicate or undeclared identity")
        identities.add(key)
        if requires_fresh_reference(cases[key[1]]):
            raise ValueError("Interrupted live-reference calls require their full certificate")
        started, ended = (datetime.fromisoformat(row[field])
                          for field in ("started_at", "completed_at"))
        binding = public_call_log_binding(row.get("provider_metadata", {}).get("call_log"))
        if not binding:
            raise ValueError("Interrupted result has no completed physical call log")
        log = json.loads(verify_public_call_log(source, binding).read_bytes())
        if any(log["identity"].get(field) != row[field]
               for field in ("attempt_id", "provider", "provider_type", "case_id", "run")):
            raise ValueError("Interrupted result does not match its physical call identity")
        timing = log["timing"]
        if (log["identity"]["physical_attempt"] != 1
                or not lower <= started <= datetime.fromisoformat(timing["started_at"])
                <= datetime.fromisoformat(timing["completed_at"]) <= ended
                or timing["elapsed_ms"] != row["elapsed_ms"]):
            raise ValueError("Interrupted result does not match its completed call timing")
        artifact = contained_path(source, row["artifact_path"])
        digest = row["metrics"]["content_sha256"]
        if (artifact.is_symlink() or not artifact.is_file()
                or file_sha256(artifact) != digest
                or log["output"]["path"] != row["artifact_path"]
                or log["output"]["sha256"] != digest):
            raise ValueError("Interrupted result output differs from completed physical call")
        verify_record_artifacts(source, row, native_cases=cases)
        ends.append(ended)
    batch = deepcopy(manifest)
    batch.update(completed_at=max(ends).isoformat(), attempt_count=len(records),
                 attempt_ids=[row["attempt_id"] for row in records])
    batch["recovery"] = {
        "kind": "all-saved-completed-calls-from-interrupted-batch",
        "original_batch_completed": False,
        "source_manifest_sha256": file_sha256(source / "manifest.json"),
        "source_results_sha256": file_sha256(source / "results.jsonl"),
        "source_dataset_sha256": file_sha256(source / "dataset.json"),
        "pending_logical_results": len(cases) * len(providers) - len(records),
        "selection": "every persisted completed call, regardless of pass or fail",
        "boundary": "last saved call completion; original full batch did not complete",
    }
    return batch


def add_provider_run(canonical: Path, incoming: Path, provider: str, implementation=None, *, track=None):
    for root in (canonical, incoming):
        reject_evidence_symlinks(root)
    canonical, incoming = canonical.resolve(), incoming.resolve()
    safe_identifier(provider)
    dataset = json.loads((canonical / "dataset.json").read_text())
    incoming_dataset = json.loads((incoming / "dataset.json").read_text())
    selected = [c for c in dataset['cases'] if track is None or c.get('track', 'web') == track]
    if not selected:
        raise ValueError('Requested track has no canonical cases')
    if track is None:
        matches = incoming_dataset == dataset
    else:
        matches = (incoming_dataset.get('tool') == dataset.get('tool')
                   and incoming_dataset.get('cases') == selected)
    if not matches:
        raise ValueError("Incoming dataset/reference differs from the canonical dataset")
    # Validate and publish the same byte snapshots, even if incoming files are
    # replaced during staging. Never re-read unvalidated JSONL for publication.
    original = (canonical / "results.jsonl").read_bytes()
    incoming_bytes = (incoming / "results.jsonl").read_bytes()
    if original and not original.endswith(b"\n"):
        raise ValueError("Canonical JSONL must end with newline")
    existing, records = parse_jsonl(original), parse_jsonl(incoming_bytes)
    validate_report_ids(dataset, existing + records)
    cases = {c["id"] for c in selected}
    if any(r["provider"] == provider and r['case_id'] in cases for r in existing):
        raise ValueError("Provider already has canonical records in this scope; refusing replacement")
    if not records or any(r["provider"] != provider for r in records):
        raise ValueError("Incoming records must belong only to the requested provider")
    if Counter(r["case_id"] for r in records) != Counter({c: 1 for c in cases}):
        raise ValueError("Incoming run must cover every case exactly once")
    identifiers = [r["attempt_id"] for r in existing + records]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Duplicate attempt IDs")
    paths = [contained_path(canonical, r["artifact_path"]) for r in existing + records]
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate artifact paths")
    for root, group in [(canonical, existing), (incoming, records)]:
        native_cases = {c['id']: c for c in (dataset if root == canonical else incoming_dataset)['cases']}
        for r in group:
            verify_record_artifacts(root, r, native_cases=native_cases)
            if Path(r['artifact_path']).parts[0] != 'artifacts':
                raise ValueError('Output paths must be inside artifacts/')
        require_verified_reference_checks(
            json.loads((root / 'execution.json').read_text())['batches'],
            cases=dataset if root == canonical else incoming_dataset,
            records=group, source=root)
    if implementation and implementation.get("sha256"):
        if any(
            r["provider_metadata"].get("binary_sha256") != implementation["sha256"] for r in records
        ):
            raise ValueError("Executable fingerprint mismatch")
    batches = deepcopy(json.loads((incoming / "execution.json").read_text())["batches"])
    assigned = []
    for batch in batches:
        if not batch.get("completed_at"):
            raise ValueError("Incoming batch is unfinished")
        names = {p["name"] for p in batch["providers"]}
        selected = [
            r
            for r in records
            if r["provider"] in names
            and batch["started_at"] <= r["started_at"] <= batch["completed_at"]
        ]
        if len(selected) != batch["attempt_count"]:
            raise ValueError("Batch attempt count does not match measured records")
        batch["attempt_ids"] = [r["attempt_id"] for r in selected]
        assigned.extend(batch["attempt_ids"])
        if implementation:
            batch["implementation"] = deepcopy(implementation)
    if Counter(assigned) != Counter(r["attempt_id"] for r in records):
        raise ValueError("Each new attempt must belong to exactly one execution batch")
    execution = json.loads((canonical / "execution.json").read_text())
    execution["batches"].extend(batches)
    plan = dataset.get('coverage_plan')
    if track is not None and (not isinstance(plan, dict) or track not in plan):
        raise ValueError('Track supplement requires an explicit canonical coverage plan')
    if isinstance(plan, dict):
        tracks = {case.get('track', 'web') for case in dataset['cases'] if case['id'] in cases}
        if not tracks <= set(plan):
            raise ValueError('Provider supplement requires an explicit task-group coverage plan')
        for name in tracks:
            plan[name] = sorted(set(plan[name]) | {provider})
    staging = Path(tempfile.mkdtemp(prefix=".evidence-add-", dir=canonical.parent))
    swap = None
    try:
        shutil.copytree(canonical, staging, dirs_exist_ok=True)
        materialize_bound_artifacts(staging, incoming, records)
        materialize_reference_checks(staging, incoming, batches)
        # Existing lines are copied byte-for-byte, not reserialized or rescored.
        (staging / "results.jsonl").write_bytes(
            original + incoming_bytes
        )
        (staging / "execution.json").write_text(
            json.dumps(execution, ensure_ascii=False, indent=2) + "\n"
        )
        if isinstance(plan, dict):
            (staging / 'dataset.json').write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + '\n')
        native_cases = {c['id']: c for c in dataset['cases']}
        for row in existing + records:
            verify_record_artifacts(staging, row, native_cases=native_cases)
        require_verified_reference_checks(execution['batches'], cases=dataset,
                                          records=existing + records, source=staging)
        swap = Path(tempfile.mkdtemp(prefix=".evidence-swap-", dir=canonical.parent))
        commit_staged_evidence(canonical, staging, swap / "original")
        shutil.rmtree(swap)
        return len(records)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if swap and swap.exists() and not (swap / "original").exists():
            shutil.rmtree(swap)


def _records(path: Path) -> list[dict]:
    return load_jsonl(path / "results.jsonl")


def _source_batches(source: Path) -> tuple[list[dict], str, bytes]:
    """Read the authoritative execution boundary without flattening its batches."""
    execution = source / "execution.json"
    if execution.is_file():
        raw = execution.read_bytes()
        payload = json.loads(raw)
        batches = payload.get("batches") if isinstance(payload, dict) else None
        if not isinstance(batches, list) or not batches:
            raise ValueError("Source execution has no batches")
        return batches, "execution", raw
    manifest = source / "manifest.json"
    if manifest.is_file():
        raw = manifest.read_bytes()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Source manifest is invalid")
        return [payload], "manifest", raw
    raise ValueError("Projection source has neither execution.json nor manifest.json")


def _inside_batch(row: dict, batch: dict) -> bool:
    started = datetime.fromisoformat(row["started_at"])
    completed = max(
        datetime.fromisoformat(row.get("completed_at", row["started_at"])),
        started + timedelta(milliseconds=row["elapsed_ms"]),
    )
    lower = datetime.fromisoformat(batch["started_at"])
    upper = datetime.fromisoformat(batch["completed_at"]) + timedelta(milliseconds=.01)
    return lower <= started <= completed <= upper


def _project_batches(
    batches: list[dict], selected: list[dict], provider: str, cases: dict[str, dict],
    *, subset: bool, source_kind: str, source_sha256: str, source_result_count: int,
) -> list[dict]:
    """Crop attempt identities and certificates while retaining physical bounds."""
    rows_by_id = {row["attempt_id"]: row for row in selected}
    ownership = {attempt_id: 0 for attempt_id in rows_by_id}
    identities: dict[str, dict] = {}
    projected = []
    for original in batches:
        attempt_ids = original.get("attempt_ids")
        if not isinstance(attempt_ids, list):
            raise ValueError("Source batch has no attempt ID inventory")
        kept = []
        for attempt_id in attempt_ids:
            row = rows_by_id.get(attempt_id)
            if row is not None and _inside_batch(row, original):
                kept.append(attempt_id)
                ownership[attempt_id] += 1
        if not kept:
            continue
        provider_rows = [row for row in original.get("providers", [])
                         if isinstance(row, dict) and row.get("name") == provider]
        if len(provider_rows) != 1:
            raise ValueError(f"{provider}: implementation identity is missing or ambiguous")
        identity = provider_rows[0]
        identities[json.dumps(identity, ensure_ascii=False, sort_keys=True)] = identity
        batch = deepcopy(original)
        batch["providers"] = [deepcopy(identity)]
        batch["attempt_ids"] = kept
        batch["attempt_count"] = len(kept)
        batch["unexecuted"] = []
        retained_cases = {rows_by_id[attempt_id]["case_id"] for attempt_id in kept}
        batch.setdefault("dataset", {})["case_count"] = len(retained_cases)
        checks = batch.get("reference_checks")
        if checks is not None:
            checks = deepcopy(checks)
            checks["cases"] = {
                case_id: binding for case_id, binding in checks.get("cases", {}).items()
                if case_id in retained_cases
            }
            postcheck = checks.get("postcheck")
            if isinstance(postcheck, dict):
                postcheck["cases"] = {
                    case_id: binding for case_id, binding in postcheck.get("cases", {}).items()
                    if case_id in retained_cases
                }
            if checks["cases"]:
                batch["reference_checks"] = checks
            else:
                batch.pop("reference_checks", None)
        batch["projection"] = {
            "kind": ("complete-provider-case-subset-from-retained-run" if subset else
                     "complete-provider-from-retained-incomplete-matrix"),
            "provider": provider,
            f"source_{source_kind}_sha256": source_sha256,
            "source_result_count": source_result_count,
            "selected_result_count": len(selected),
            "selection": (
                "explicit predeclared provider/case repair scope; no score-based selection"
                if subset else "all frozen cases for one provider; no score-based selection"
            ),
        }
        projected.append(batch)
    if any(count != 1 for count in ownership.values()):
        raise ValueError(
            f"{provider}: selected attempts are missing from, or ambiguous across, execution batches"
        )
    if len(identities) != 1:
        raise ValueError(f"{provider}: implementation identity differs across execution batches")
    return projected


def _project_provider_run(source: Path, output: Path, provider: str, reference_root: Path | None = None,
            case_ids: set[str] | None = None, *, retain_completed: bool = False) -> None:
    source, output = source.resolve(), output.resolve()
    reference_root = (reference_root or source).resolve()
    if output.exists():
        raise ValueError("Projection destination exists: " + str(output))
    dataset = json.loads((source / "dataset.json").read_text())
    all_records = _records(source)
    if retain_completed:
        if case_ids is not None:
            raise ValueError("Interrupted recovery cannot select a case subset")
        case_ids = {row["case_id"] for row in all_records if row["provider"] == provider}
    if case_ids is not None:
        known = {case["id"] for case in dataset["cases"]}
        if not case_ids or case_ids - known:
            raise ValueError("Projection case IDs are empty or unknown")
        dataset["cases"] = [case for case in dataset["cases"] if case["id"] in case_ids]
    cases = {case["id"]: case for case in dataset["cases"]}
    selected = [row for row in all_records
                if row["provider"] == provider and row["case_id"] in cases]
    expected = set(cases)
    if len(selected) != len(expected) or {row["case_id"] for row in selected} != expected:
        raise ValueError(f"{provider}: provider cohort is incomplete")
    if len({row["attempt_id"] for row in selected}) != len(selected):
        raise ValueError(f"{provider}: duplicate attempt IDs")
    validate_report_ids(dataset, selected)

    source_batches, source_kind, source_document_bytes = _source_batches(source)
    source_document_sha256 = hashlib.sha256(source_document_bytes).hexdigest()
    if retain_completed:
        if source_kind != "manifest" or len(source_batches) != 1:
            raise ValueError("Interrupted recovery requires one physical source manifest")
        source_batches = [recover_completed_batch(source, source_batches[0], all_records)]
    dataset["coverage_plan"] = {
        track: [provider] for track, providers in dataset["coverage_plan"].items()
        if provider in providers
    }
    if not dataset["coverage_plan"]:
        raise ValueError(f"{provider}: absent from the declared coverage plan")
    projected_batches = _project_batches(
        source_batches, selected, provider, cases, subset=case_ids is not None,
        source_kind=source_kind, source_sha256=source_document_sha256,
        source_result_count=len(all_records),
    )

    certified = any(batch.get("reference_checks", {}).get("status") == "verified"
                    for batch in projected_batches)
    if certified:
        require_verified_reference_checks(
            projected_batches, cases=dataset, records=selected, source=source,
            equivalent_capture_bindings=(
                dataset.get("comparison", {}).get("reference_binding_mode") ==
                "equivalent_reference_bytes_with_independent_batch_captures"
            ),
        )
    else:
        for batch in projected_batches:
            checks = batch.get("reference_checks", {})
            if checks and (checks.get("status") != "awaiting_postcheck" or checks.get("postcheck")):
                raise ValueError("Projection source has incomplete or mixed reference certification")

    output.mkdir(parents=True)
    (output / f"source-{source_kind}.json").write_bytes(source_document_bytes)
    materialize_task_references(output, {name: input_tree(reference_root/'dataset.json',name)
        for name in ('sources','list-references')}, cases)
    materialize_reference_checks(output, source, projected_batches)
    materialize_bound_artifacts(output, source, selected)
    (output / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n")
    if len(projected_batches) == 1:
        (output / "manifest.json").write_text(
            json.dumps(projected_batches[0], ensure_ascii=False, indent=2) + "\n"
        )
    (output / "execution.json").write_text(
        json.dumps({"batches": projected_batches}, ensure_ascii=False, indent=2) + "\n"
    )
    (output / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in selected))
    if certified:
        require_verified_reference_checks(
            projected_batches, cases=dataset, records=selected, source=output,
            equivalent_capture_bindings=(
                dataset.get("comparison", {}).get("reference_binding_mode") ==
                "equivalent_reference_bytes_with_independent_batch_captures"
            ),
        )


def project_provider_run(source: Path, output: Path, provider: str,
                         reference_root: Path | None = None,
                         case_ids: set[str] | None = None, *, retain_completed: bool = False) -> None:
    """Expose a new projection only after its complete evidence passes validation."""
    for root in (source, reference_root or source):
        reject_evidence_symlinks(root)
    safe_identifier(provider)
    if output.exists() or output.is_symlink():
        raise ValueError("Projection destination exists: " + str(output))
    source, output = source.resolve(), output.resolve()
    if output == source or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("Projection output must be separate from its source")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.' + output.name + '-', dir=output.parent) as work:
        staging = Path(work) / 'evidence'
        _project_provider_run(source, staging, provider, reference_root, case_ids,
                              retain_completed=retain_completed)
        staging.replace(output)


def projection_main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--retain-completed", action="store_true",
                        help="Retain every saved completed call from an interrupted fixed-answer batch")
    parser.add_argument("--case-ids", help="Optional comma-separated explicit subset")
    parser.add_argument("--reference-root", type=Path,
                        help="Root containing frozen sources/list-references when the live run omits them")
    args = parser.parse_args(argv)
    case_ids = None
    if args.case_ids is not None:
        values = [value.strip() for value in args.case_ids.split(',')]
        if any(not value for value in values) or len(set(values)) != len(values):
            parser.error('Case IDs must be nonempty and unique')
        case_ids = set(values)
    project_provider_run(args.source, args.output, args.provider, args.reference_root, case_ids,
                         retain_completed=args.retain_completed)
    print(f"Projected {args.provider}: {args.output}")


def addition_main(argv=None) -> None:
    parser = argparse.ArgumentParser(description='Validate and add a complete provider run.')
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--provider', required=True)
    parser.add_argument('--track', help='Add a complete, previously unmeasured task group')
    parser.add_argument('--implementation', type=Path,
                        help='Verified implementation version/source/hash JSON')
    args = parser.parse_args(argv)
    implementation = json.loads(args.implementation.read_text()) if args.implementation else None
    count = add_provider_run(current_evidence_dir(), args.run_dir, args.provider,
                             implementation, track=args.track)
    print('Added', count, 'records')


def certify_case(run, output, case, records, observation):
    """Create a reference certificate sidecar; artifacts remain in the physical run."""
    source_raw=contained_path(run,'manifest.json',reject_symlinks=True).read_bytes()
    source=json.loads(source_raw)
    cid=safe_identifier(case['id'])
    if source.get('reference_checks',{}).get('status')!='awaiting_postcheck' or 'postcheck' in source.get('reference_checks',{}):
        raise ValueError('Only an unfinished live preflight can be partitioned')
    binding=source.get('reference_checks',{}).get('cases',{}).get(cid)
    if not binding:
        raise ValueError('No original pre-call reference binding: '+cid)
    if any(r['case_id']!=cid for r in records):
        raise ValueError('Mixed case records')
    dataset=json.loads(contained_path(run,'dataset.json',reject_symlinks=True).read_bytes())
    if [row for row in dataset['cases'] if row['id']==cid] != [case]:
        raise ValueError('Case differs from the physical run dataset')
    expected=expected_providers(dataset,case)
    if completed_case_records(dataset,case,records) is None:
        raise ValueError('Incomplete matched-provider cohort')
    raw=contained_path(run,'results.jsonl',reject_symlinks=True).read_bytes()
    persisted=completed_case_records(dataset,case,parse_jsonl(raw,allow_incomplete_tail=True))
    if persisted is None or sorted(persisted,key=lambda row:row['provider']) != sorted(records,key=lambda row:row['provider']):
        raise ValueError('Certificate records differ from the physical run')
    destination=contained_path(output,cid,reject_symlinks=True)
    if destination.exists():
        raise FileExistsError(destination)
    manifest=deepcopy(source)
    # End of this assembly operation, not a newly invented call timestamp.
    # perf_counter starts before the wall-clock call timestamp in the runner;
    # start + elapsed can therefore slightly exceed recorded completed_at.
    manifest.update(attempt_count=len(records),attempt_ids=[r['attempt_id'] for r in records],
        completed_at=datetime.now(timezone.utc).isoformat())
    manifest['reference_checks']={k:deepcopy(v) for k,v in source['reference_checks'].items()
        if k not in ('cases','postcheck','callcheck_error')}
    # Preserve the original preflight status; never clear a failed lifecycle.
    manifest['reference_checks']['cases']={cid:deepcopy(binding)}
    manifest['projection']={'kind':'completed-case-from-live-preflight',
        'source_manifest_sha256':hashlib.sha256(source_raw).hexdigest(),
        'source_manifest_path':'source-manifest.json',
        'created_at':datetime.now(timezone.utc).isoformat(),
        'original_attempt_ids_preserved':True}
    dataset['cases']=[case]
    dataset['coverage_plan']={case.get('track','web'):sorted(expected)}
    manifest.setdefault('dataset',{})['case_count']=1
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.'+cid+'-',dir=output) as work:
        staging=Path(work)/'certificate'
        staging.mkdir()
        (staging/'source-manifest.json').write_bytes(source_raw)
        for name,payload in [('manifest.json',manifest),('dataset.json',dataset),('observations.json',[observation])]:
            (staging/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
        (staging/'results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
        result=finalize_reference_checks(staging,staging/'observations.json')
        staging.replace(destination)
    print(cid,result['status'],flush=True)
    return result


def certification_main(argv=None):
    parser=argparse.ArgumentParser(
        description='Certify completed matched cases from their original live preflight.')
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--observations',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=int,default=2700)
    args=parser.parse_args(argv)
    dataset=json.loads((args.run_dir/'dataset.json').read_text())
    if args.timeout < 0:
        parser.error('--timeout must be nonnegative')
    cases=reference_cases(dataset)
    if not cases:
        return
    deadline=time.monotonic()+args.timeout
    while True:
        try:
            observations=json.loads(args.observations.read_text())
        except (FileNotFoundError,json.JSONDecodeError):
            if time.monotonic()>=deadline:
                raise TimeoutError('Case certification deadline')
            time.sleep(1);continue
        records=read_completed_records(args.run_dir/'results.jsonl')
        for observation in observations:
            cid=safe_identifier(observation['case_id'])
            if cid not in cases:
                if cid not in {case['id'] for case in dataset['cases']}:
                    raise ValueError('Unknown observation case: '+cid)
                continue
            relevant=completed_case_records(dataset,cases[cid],records)
            if relevant is None:
                continue
            destination=contained_path(args.output,cid,reject_symlinks=True)
            if destination.exists():
                manifest=json.loads((destination/'manifest.json').read_bytes())
                original=(destination/'source-manifest.json').read_bytes()
                previous=json.loads(original)
                current=json.loads((args.run_dir/'manifest.json').read_bytes())
                # A live run legitimately adds completion metadata after a
                # case certificate is created. Its preflight, configuration
                # and calls must still match the retained source snapshot.
                for payload in (previous,current):
                    for key in ('attempt_count','attempt_ids','completed_at'):
                        payload.pop(key,None)
                if (manifest.get('projection',{}).get('source_manifest_sha256') !=
                        hashlib.sha256(original).hexdigest() or previous != current
                        or json.loads((destination/'dataset.json').read_bytes())['cases'] != [cases[cid]]
                        or sorted(load_jsonl(destination/'results.jsonl'),key=lambda row:row['provider']) !=
                           sorted(relevant,key=lambda row:row['provider'])):
                    raise ValueError('Existing certificate belongs to different evidence: '+cid)
                continue
            certify_case(args.run_dir,args.output,cases[cid],relevant,observation)
        if all((args.output/cid/'manifest.json').exists() for cid in cases):return
        if time.monotonic()>=deadline:
            raise TimeoutError('Case certification deadline')
        time.sleep(1)


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _attempt_key(batch: dict) -> tuple[str, ...]:
    if not isinstance(batch, dict):
        raise ValueError("Execution batch must be an object")
    attempts = batch.get("attempt_ids")
    if (not isinstance(attempts, list) or not attempts
            or any(not isinstance(value, str) or not value for value in attempts)
            or len(attempts) != len(set(attempts))):
        raise ValueError("Execution batch has invalid attempt IDs")
    return tuple(sorted(attempts))


def _index_batches(batches: list[dict]) -> dict[tuple[str, ...], dict]:
    indexed, attempts = {}, set()
    for batch in batches:
        key = _attempt_key(batch)
        if attempts.intersection(key):
            raise ValueError("Execution batches have duplicate attempt IDs")
        attempts.update(key)
        indexed[key] = batch
    return indexed


def _batch_identity(batch: dict) -> dict:
    """Only the reference-check outcome may change while finalizing a worker."""
    identity = deepcopy(batch)
    checks = identity.get("reference_checks")
    if isinstance(checks, dict):
        checks.pop("status", None)
        checks.pop("postcheck", None)
    return identity


def _is_reference_finalization(original: dict, current: dict) -> bool:
    if _batch_identity(original) != _batch_identity(current):
        return False
    checks = original.get("reference_checks")
    if isinstance(checks, dict) and (
            checks.get("status") != "awaiting_postcheck" or "postcheck" in checks):
        return checks == current.get("reference_checks")
    return True


def _verify_existing_postcheck(worker: Path, manifest: dict,
                               observations: Path) -> bool:
    """Accept only the exact verified postcheck left by an interrupted finalize.

    ``finalize_reference_checks`` intentionally refuses to rewrite an existing
    certificate.  The aggregate step must therefore be resumable after the
    worker manifest was committed but before the top-level execution file was
    rebuilt.  Reuse is safe only when the retained receipt still binds the
    supplied observations and this worker's immutable dataset/results bytes.
    """
    checks = manifest.get("reference_checks")
    postcheck = checks.get("postcheck") if isinstance(checks, dict) else None
    if not isinstance(postcheck, dict):
        return False
    if checks.get("status") != "verified" or postcheck.get("status") != "verified":
        raise ValueError(f"Retained reference postcheck is not verified: {worker}")
    expected = {
        "evidence_sha256": file_sha256(observations),
        "results_sha256": file_sha256(worker / "results.jsonl"),
        "dataset_sha256": file_sha256(worker / "dataset.json"),
    }
    for field, value in expected.items():
        if postcheck.get(field) != value:
            raise ValueError(
                f"Retained reference postcheck has stale {field}: {worker}")
    return True


def finalize_aggregate(run_dir: Path, observations: Path) -> None:
    """Finalize workers, merge certificates, and atomically replace execution.json."""
    reject_evidence_symlinks(run_dir)
    if observations.is_symlink():
        raise ValueError("Symlinked postcheck observations are forbidden")
    run_dir = run_dir.resolve()
    execution_path = run_dir / "execution.json"
    execution = _read(execution_path)
    original = execution.get("batches")
    if not isinstance(original, list) or not original:
        raise ValueError("Aggregated run has no execution batches")
    original_by_key = _index_batches(original)

    worker_root = run_dir / "workers"
    manifests = sorted({
        *worker_root.glob("*/manifest.json"),
        *worker_root.glob("*/*/manifest.json"),
    })
    if not manifests:
        raise ValueError("Aggregated run has no worker manifests")
    before = [_read(path) for path in manifests]
    before_by_key = _index_batches(before)
    if set(before_by_key) != set(original_by_key):
        raise ValueError("Worker manifests differ from aggregated execution")
    if any(not _is_reference_finalization(original_by_key[key], batch)
           for key, batch in before_by_key.items()):
        raise ValueError("Worker metadata differs from aggregated execution")

    for path, manifest in zip(manifests, before, strict=True):
        if "reference_checks" in manifest:
            if _verify_existing_postcheck(path.parent, manifest, observations):
                continue
            result = finalize_reference_checks(path.parent, observations)
            if result.get("status") != "verified":
                raise ValueError(f"Reference postcheck failed: {path.parent}")

    updated = [_read(path) for path in manifests]
    updated_by_key = _index_batches(updated)
    if set(updated_by_key) != set(original_by_key):
        raise ValueError("Finalized worker manifests changed attempt inventory")
    if any(not _is_reference_finalization(original_by_key[key], batch)
           for key, batch in updated_by_key.items()):
        raise ValueError("Finalized worker metadata changed")

    for path, manifest in zip(manifests, updated, strict=True):
        materialize_reference_checks(run_dir, path.parent, [manifest])

    rebuilt = {**execution, "batches": [updated_by_key[_attempt_key(batch)] for batch in original]}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=run_dir,
                                         prefix=".execution-", suffix=".json", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(rebuilt, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(execution_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"Finalized aggregate reference checks: {len(updated)} batches")


def finalize_main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=finalize_aggregate.__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    args = parser.parse_args(argv)
    finalize_aggregate(args.run_dir, args.observations)
