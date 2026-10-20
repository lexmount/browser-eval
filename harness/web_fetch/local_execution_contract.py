"""Verify frozen local execution inputs and physical calls, independently of review."""
import hashlib
import json
from pathlib import Path

from harness.record_io import canonical_json_sha256
from lexbench_webfetch.paths import contained_path
from .reference_inputs import read_dataset_bytes

ROOT = Path(__file__).resolve().parents[2]


def _require_local_batch_segments(batch):
    """A resumed batch must retain distinct physical manifests and the same policy."""
    segments = batch.get("segments")
    if segments is None:
        return
    if not isinstance(segments, list) or not segments:
        raise ValueError("Local measurement has no physical segments")
    attempts = []
    for segment in segments:
        for field in ("provider_config_sha256", "runtime_source_sha256", "parameters", "providers"):
            if segment.get(field) != batch.get(field):
                raise ValueError("Local measurement segment parameters differ")
        ids = segment.get("attempt_ids", [])
        if len(ids) != segment.get("attempt_count"):
            raise ValueError("Local measurement segment count differs")
        attempts.extend(ids)
    if (len(attempts) != len(set(attempts))
            or set(attempts) != set(batch.get("attempt_ids", []))
            or len(attempts) != batch.get("attempt_count")):
        raise ValueError("Local measurement segments repeat or omit a call")


def local_measurement_call_sha256(record):
    """Bind measured call facts independently of later scores/report metadata."""
    derived = {"metrics", "diagnosis", "failure_category", "scoring", "provider_metadata"}
    facts = {key: value for key, value in record.items() if key not in derived}
    facts["content_sha256"] = record.get("metrics", {}).get("content_sha256")
    return canonical_json_sha256(facts)


def _require_local_record_correction(source, original, corrected, receipt=None):
    """Permit derived corrections while binding every physical call fact."""
    if receipt is None:
        path = contained_path(source, "local-evidence/scoring-correction.json")
        if not path.is_file():
            raise ValueError("Changed local record lacks a correction receipt")
        receipt = json.loads(path.read_text())
    identifier = original["attempt_id"]
    if (receipt.get("original_call_identities", {}).get(identifier)
            != local_measurement_call_sha256(original)
            or receipt.get("call_identities", {}).get(identifier)
            != local_measurement_call_sha256(corrected)):
        raise ValueError("Local correction lost its physical call binding")
    derived = {"metrics", "scoring", "diagnosis", "failure_category"}
    changed = {key for key in original.keys() | corrected.keys()
               if original.get(key) != corrected.get(key)} - derived
    if changed - {"final_url"}:
        raise ValueError("Local correction changed measured call facts")
    if "final_url" in changed:
        from .native_projection import native_field_value
        from .publication_privacy import read_evidence_binding
        proof = receipt.get("field_projections", {}).get(identifier, {}).get("final_url", {})
        if not proof.get('path') or not proof.get('sha256'):
            raise ValueError('Local URL correction lost its native response')
        path = read_evidence_binding(source, proof)
        pointer = proof.get('json_pointer')
        # The legacy receipt selects object keys only; sharing extraction must
        # not silently add array selection to this historical contract.
        if not isinstance(pointer, list) or not pointer or any(not isinstance(key, str) for key in pointer):
            raise ValueError('Local URL correction requires object field names')
        value = native_field_value(json.loads(path.read_text()), pointer)
        if (value != corrected.get("final_url")
                or proof.get("original_value") != original.get("final_url")):
            raise ValueError("Local URL correction differs from native response")


def _require_local_method_bindings(contract, batches, field):
    """Join each method's execution to its declared runtime or configuration."""
    variants = contract.get(field + "_by_provider")
    expected = set(contract["providers"])
    label = "runtime" if field == "runtime_source_sha256" else "configuration"
    if variants is not None:
        if (not isinstance(variants, dict) or set(variants) != expected
                or any(not isinstance(value, str) or len(value) != 64
                       or any(char not in "0123456789abcdef" for char in value)
                       for value in variants.values())):
            raise ValueError(f"Invalid local {label} variant bindings")
    seen = set()
    for batch in batches:
        providers = batch.get("providers", [])
        if len(providers) != 1:
            raise ValueError(f"Local {label} batch must identify one method")
        name = providers[0].get("name")
        if name not in expected or name in seen:
            raise ValueError(f"Local {label} batches repeat or omit a method")
        seen.add(name)
        digest = variants[name] if variants is not None else contract[field]
        if batch.get(field) != digest:
            raise ValueError(f"Local measurement execution {label} differs")
    if seen != expected:
        raise ValueError(f"Local {label} batches repeat or omit a method")


def local_measurement_cases(contract: dict, source=None) -> list[dict]:
    """Read an ordered selection from the original hash-bound input trees."""
    bindings = contract.get("dataset_sources")
    if bindings is None:
        bindings = [{"path": contract["dataset_path"],
                     "sha256": contract["dataset_sha256"]}]
    if not isinstance(bindings, list) or not bindings:
        raise ValueError("Local measurement dataset sources are missing")
    cases, seen_paths, seen_ids = [], set(), set()
    for binding in bindings:
        relative = binding["path"]
        root = source if contract.get("dataset_storage") == "evidence" else ROOT
        if root is None:
            raise ValueError("Evidence-relative inputs require their evidence root")
        path = contained_path(root, relative, reject_symlinks=True)
        raw = read_dataset_bytes(path)
        if relative in seen_paths or hashlib.sha256(raw).hexdigest() != binding["sha256"]:
            raise ValueError("Local measurement dataset source changed")
        seen_paths.add(relative)
        available = json.loads(raw)["cases"]
        selected = binding.get("case_ids", [case["id"] for case in available])
        if not selected or len(selected) != len(set(selected)):
            raise ValueError("Local measurement source selection differs")
        rows = [case for case in available if case["id"] in set(selected)]
        if [case["id"] for case in rows] != selected or seen_ids.intersection(selected):
            raise ValueError("Local measurement source selection differs")
        seen_ids.update(selected)
        cases.extend(rows)
    return cases


def _local_timeout_seconds(contract):
    import math
    value = contract.get('timeout_seconds')
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError('Local measurement requires a positive finite caller timeout')
    return value


def require_local_execution_contract(source, dataset, records, contract, execution):
    """Shared physical-call boundary for complete cohorts and task replacements."""
    from .artifacts import verify_record_public_call_logs, record_public_call_log_bindings
    from datetime import datetime
    timeout = _local_timeout_seconds(contract)
    # This mode binds one authoritative physical call per method/task. Multiple
    # rounds and retries require the certified cohort's separate lineage model.
    if (contract.get("schema_version") != 1
            or contract.get("kind") != "uniform_native_local_measurement"
            or contract.get("runs") != 1
            or contract.get("max_failure_retries") != 0):
        raise ValueError("Invalid uniform native local measurement contract")
    canonical = {"cases": (local_measurement_cases(contract, source)
                              if contract.get("dataset_storage") == "evidence"
                              else local_measurement_cases(contract))}
    if dataset["cases"] != canonical["cases"]:
        raise ValueError("Local measurement changed the committed cases")
    expected = {(p, c["id"]) for p in contract["providers"] for c in canonical["cases"]}
    if (len(records) != len(expected)
            or {(r["provider"], r["case_id"]) for r in records} != expected
            or any(r["run"] != 1 or r.get("provider_metadata", {}).get("retry") for r in records)):
        raise ValueError("Local measurement matrix is incomplete or has retries")
    if len(execution["batches"]) != len(contract["providers"]):
        raise ValueError("Local measurement execution scope differs")
    def validate_current_batches():
        for field in ("runtime_source_sha256", "provider_config_sha256"):
            _require_local_method_bindings(contract, execution["batches"], field)
        for batch in execution["batches"]:
            _require_local_batch_segments(batch)
    from .local_execution_legacy import require_execution_lineage
    require_execution_lineage(
        source, contract, execution["batches"], records,
        cases=canonical["cases"], timeout_seconds=timeout,
        validate_record_correction=_require_local_record_correction,
        validate_current_batches=validate_current_batches)
    for batch in execution["batches"]:
        ids = batch.get("attempt_ids", [])
        measured_ids = {r["attempt_id"] for r in records
                        if r["provider"] == batch["providers"][0]["name"]}
        if (len(ids) != len(set(ids)) or set(ids) != measured_ids
                or batch.get("attempt_count") != len(measured_ids)):
            raise ValueError("Local measurement execution calls differ from results")
        if (batch.get("parameters", {}).get("timeout") != timeout
                or batch.get("parameters", {}).get("runs") != 1
                or batch.get("parameters", {}).get("provider_concurrency") != 1
                or batch.get("parameters", {}).get("max_failure_retries") != 0):
            raise ValueError("Local measurement execution parameters differ")
    if contract.get("runner_inputs"):
        _require_local_runner_inputs(source, contract, execution, records)
    canonical_by_id = {case['id']: case for case in canonical['cases']}
    for record in records:
        if record.get('url') != canonical_by_id[record['case_id']]['url']:
            raise ValueError('Local measurement request URL differs from frozen task')
        if len(record_public_call_log_bindings(record)) != 1:
            raise ValueError("Local measurement requires one complete call log per result")
        lexical = source
        for part in Path(record["artifact_path"]).parts:
            lexical = lexical / part
            if lexical.is_symlink():
                raise ValueError("Local measurement output is unsafe")
        artifact = contained_path(source, record["artifact_path"])
        if artifact.is_symlink() or hashlib.sha256((artifact).read_bytes()).hexdigest() != record["metrics"]["content_sha256"]:
            raise ValueError("Local measurement output changed")
        verify_record_public_call_logs(source, record)
        binding = record_public_call_log_bindings(record)[0]
        log = json.loads(contained_path(source, binding["path"]).read_text())
        if (any(log["identity"][key] != record[key]
                for key in ("attempt_id", "provider", "case_id", "run"))
                or log["output"]["path"] != record["artifact_path"]
                or log["output"]["sha256"] != record["metrics"]["content_sha256"]
                or log['request']['url'] != record['url']
                or log['timing'].get('elapsed_ms') != record.get('elapsed_ms')
                or not (datetime.fromisoformat(record['started_at'])
                        <= datetime.fromisoformat(log['timing']['started_at'])
                        <= datetime.fromisoformat(log['timing']['completed_at'])
                        <= datetime.fromisoformat(record['completed_at']))
                or log['timing'].get('deadline_budget_ms') != timeout * 1000):
            raise ValueError("Local measurement call log belongs to a different result")


def _require_local_runner_inputs(source, contract, execution, records):
    """Verify retained runner inputs and source observations, without certifying them."""
    from datetime import datetime
    from .publication_privacy import read_evidence_binding
    inputs = contract['runner_inputs']
    def read(key):
        return json.loads(read_evidence_binding(source, inputs[key]).read_text())
    frozen, configuration, network = (read(key) for key in ('dataset', 'providers', 'network'))
    canonical = local_measurement_cases(contract, source)
    if frozen['cases'] != canonical or inputs['dataset'] != contract['dataset_sources'][0]:
        raise ValueError('Local runner dataset differs from frozen task inputs')
    if inputs['providers']['sha256'] != contract['provider_config_sha256']:
        raise ValueError('Local runner configuration binding differs')
    if not set(contract['providers']) <= set(configuration['providers']):
        raise ValueError('Local runner configuration omits a method')
    dns_mode = _require_bound_dns_mode(configuration, contract['providers'], network)
    if network.get('usable_for_guarded_local_comparison') is not True:
        raise ValueError('Local runner lacks usable public DNS evidence')
    from .network_preflight import GUARDED_NATIVE_TYPES, require_usable_public_dns
    require_usable_public_dns(network)
    from urllib.parse import urlsplit
    method_types = {name: configuration['providers'][name].get('method_type',
                    configuration['providers'][name]['type']) for name in contract['providers']}
    if any(value in GUARDED_NATIVE_TYPES for value in method_types.values()):
        hosts = network.get('hosts', [])
        expected_hosts = {urlsplit(case['url']).hostname for case in canonical}
        if (len(hosts) != len(expected_hosts) or {host.get('host') for host in hosts} != expected_hosts
                or any(not host.get('addresses') or host.get('dns_error') for host in hosts)):
            raise ValueError('Local runner public DNS scope is incomplete')
    for batch in execution['batches']:
        name = batch['providers'][0]['name']
        guarded = method_types[name] in GUARDED_NATIVE_TYPES
        if (batch['providers'][0].get('type') != method_types[name]
                or (guarded and not batch['providers'][0].get('implementation_identity'))
                or any(row.get('provider_type') != method_types[name]
                       for row in records if row['provider'] == name)):
            raise ValueError('Local runner method identity differs from frozen configuration')
        expected_network = ({'dns_mode': dns_mode, 'sha256': inputs['network']['sha256']}
                            if guarded else None)
        if (batch.get('dataset_sha256') != inputs['dataset']['sha256']
                or batch.get('environment') != contract['environment']
                or batch.get('parameters') != contract['parameters']
                or batch.get('network_preflight') != expected_network
                or not batch.get('completed_at') or batch.get('unexecuted')):
            raise ValueError('Local runner execution differs from bound inputs or environment')
    if any(not batch.get('environment', {}).get(key) for batch in execution['batches']
           for key in ('python', 'platform')):
        raise ValueError('Local runner execution lacks environment identity')
    observations = inputs['source_observations']
    if set(observations) != {case['id'] for case in canonical}:
        raise ValueError('Local runner source observation scope differs')
    for case in canonical:
        times = [datetime.fromisoformat(row['started_at']) for row in records if row['case_id'] == case['id']]
        ends = [datetime.fromisoformat(row['completed_at']) for row in records if row['case_id'] == case['id']]
        for stage in ('pre', 'post'):
            capture = observations[case['id']][stage]
            evidence = json.loads(read_evidence_binding(source, capture['evidence']).read_text())
            for key, field in [('source', 'sha256'),
                               ('reference', 'reference_sha256'), ('extracted', 'extracted_sha256')]:
                read_evidence_binding(source, capture[key])
                if capture[key]['sha256'] != evidence.get(field):
                    raise ValueError('Local runner source bytes differ from observation')
            observed = datetime.fromisoformat(evidence['checked_at'])
            if (evidence.get('case_id') != case['id'] or evidence.get('url') != case['url']
                    or evidence.get('independent') is not True or evidence.get('status') != 200
                    or (stage == 'pre' and observed > min(times))
                    or (stage == 'post' and observed < max(ends))):
                raise ValueError('Local runner source observation does not bracket the calls')


def _require_bound_dns_mode(configuration, providers, network):
    """Match observed DNS to guarded adapters' frozen configuration."""
    from .network_dns import validate_dns_mode
    from .network_preflight import GUARDED_NATIVE_TYPES
    observed = validate_dns_mode(network.get('dns_mode'))
    modes = {
        validate_dns_mode(config.get('dns_mode', 'system'))
        for name in providers
        for config in [configuration['providers'][name]]
        if config.get('method_type', config['type']) in GUARDED_NATIVE_TYPES
    }
    if modes and modes != {observed}:
        raise ValueError('Local runner DNS differs from frozen provider configuration')
    return observed
