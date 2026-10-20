"""Read-only verification of retained schema-1 resumed execution evidence.

The original/resumed/admitted plan names and worker-path migration describe
historical calls. New cohorts use the ordinary execution contract instead;
this module neither creates plans nor changes stored records.
"""
import hashlib
import json

from lexbench_webfetch.paths import contained_path


def require_legacy_execution_segments(
    source, contract, batches, records, *, cases, timeout_seconds, validate_record_correction
):
    """Verify resumed measurements against retained pre-call plans and physical files."""
    correction_path = contained_path(source, "local-evidence/scoring-correction.json")
    correction = json.loads(correction_path.read_text()) if correction_path.is_file() else None
    def read(binding, *, json_lines=False):
        path = contained_path(source, binding["path"], reject_symlinks=True)
        if hashlib.sha256(path.read_bytes()).hexdigest() != binding["sha256"]:
            raise ValueError("Local execution evidence binding changed")
        if json_lines:
            return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        return json.loads(path.read_text())

    plans = contract["execution_plans"]
    if set(plans) not in ({"original", "resumed"}, {"original", "resumed", "admitted"}):
        raise ValueError("Local execution plans are incomplete")
    original, resumed = (read(plans[key]) for key in ("original", "resumed"))
    if (original.get("kind") != "uniform_native_local_measurement"
            or resumed.get("kind") != "post_merge_resume_and_runtime_correction"
            or original.get("providers") != contract["providers"]
            or original.get("dataset_sources") != contract.get("dataset_sources")
            or original.get("scoring_source_sha256") != contract["scoring_source_sha256"]
            or original.get("runs") != 1
            or original.get("timeout_seconds") != timeout_seconds
            or original.get("max_failure_retries") != 0):
        raise ValueError("Local execution changed the frozen protocol")
    expected_policy = {"runs": 1, "timeout": timeout_seconds, "delay": original["delay"],
                       "provider_concurrency": 1, "max_failure_retries": 0}
    if resumed.get("parameters") != expected_policy:
        raise ValueError("Local resumed execution parameters differ")
    configs = {key: read(plans[key]["configuration"]) for key in plans}
    admitted = read(plans["admitted"]) if "admitted" in plans else None
    if admitted and (admitted.get("kind") != "bounded_admission_continuation"
                     or admitted.get("predecessor_plan_sha256") != plans["resumed"]["sha256"]
                     or admitted.get("parameters") != expected_policy
                     or admitted.get("providers") != contract["providers"]
                     or admitted.get("dataset_sha256") != resumed.get("dataset_sha256")
                     or configs["admitted"] != configs["resumed"]):
        raise ValueError("Local admission changed the frozen protocol or configuration")
    plan_values = {"original": original, "resumed": resumed}
    if admitted:
        plan_values["admitted"] = admitted
    for key, plan in plan_values.items():
        if plans[key]["configuration"]["sha256"] != plan["provider_config_sha256"]:
            raise ValueError("Local execution configuration differs from its pre-call plan")
    from copy import deepcopy
    normalized = deepcopy(configs["resumed"])
    for provider in normalized.get("providers", {}).values():
        for field in ("command", "required_paths"):
            if field in provider:
                provider[field] = [value.replace(
                    "{harness_root}/harness/web_fetch/workers/", "{harness_root}/scripts/oss/")
                    if isinstance(value, str) else value for value in provider[field]]
    if normalized != configs["original"]:
        raise ValueError("Local resumed configuration changed beyond worker paths")
    names = set(contract["providers"])
    scopes = resumed.get("case_ids_by_provider", {})
    cases = [case["id"] for case in cases]
    if set(scopes) != names or any(
        len(scope) != len(set(scope)) or not set(scope) <= set(cases)
        for scope in scopes.values()
    ):
        raise ValueError("Local resumed execution has an invalid task scope")
    admitted_scopes = admitted.get("case_ids_by_provider", {}) if admitted else {}
    if admitted and (set(admitted_scopes) != names or any(
            len(scope) != len(set(scope)) or not set(scope) <= set(scopes[name])
            for name, scope in admitted_scopes.items())):
        raise ValueError("Local admission has an invalid remaining task scope")
    by_attempt = {record["attempt_id"]: record for record in records}
    seen_names, seen_attempts = set(), set()
    for batch in batches:
        providers = batch.get("providers", [])
        if len(providers) != 1:
            raise ValueError("Local execution batch must identify one method")
        name = providers[0]["name"]
        if name not in names or name in seen_names:
            raise ValueError("Local execution repeats or omits a method")
        seen_names.add(name)
        segments = batch.get("segments", [])
        if not segments:
            raise ValueError("Local resumed execution has no physical segments")
        expected_scopes = {"resumed": scopes[name],
                           "original": [case for case in cases if case not in set(scopes[name])]}
        if admitted:
            expected_scopes["resumed"] = [case for case in scopes[name]
                                          if case not in set(admitted_scopes[name])]
            expected_scopes["admitted"] = admitted_scopes[name]
        declared_scopes = {"original": expected_scopes["original"],
                           "resumed": scopes[name]}
        if admitted:
            declared_scopes["admitted"] = admitted_scopes[name]
        seen_plans, ids, manifest_paths = set(), [], set()
        offsets = {key: 0 for key in plans}
        for segment in segments:
            key = segment.get("plan_id")
            if key not in plans or (key == "original" and key in seen_plans):
                raise ValueError("Local execution repeats or substitutes a physical segment")
            seen_plans.add(key)
            plan = plan_values[key]
            manifest_path = segment["manifest"]["path"]
            if manifest_path in manifest_paths:
                raise ValueError("Local execution repeats a physical manifest")
            manifest_paths.add(manifest_path)
            manifest = read(segment["manifest"])
            physical = read(segment["records"], json_lines=True)
            full_ids = [f"{name}__{case}__r001" for case in expected_scopes[key]]
            expected_ids = full_ids[offsets[key]:offsets[key] + len(physical)]
            if not physical:
                raise ValueError("Local execution has an empty physical segment")
            offsets[key] += len(physical)
            if ([row.get("attempt_id") for row in physical] != expected_ids
                    or segment.get("attempt_ids") != expected_ids
                    or segment.get("attempt_count") != len(expected_ids)):
                raise ValueError("Local execution segment task selection differs")
            if (manifest.get("runtime_source_sha256") != plan["runtime_source_sha256"]
                    or manifest.get("provider_config_sha256") != plan["provider_config_sha256"]
                    or manifest.get("providers") != segment.get("providers")
                    or manifest.get("parameters") != segment.get("parameters")):
                raise ValueError("Local execution segment differs from its physical manifest")
            if (len(manifest["providers"]) != 1 or manifest["providers"][0]["name"] != name
                    or any(manifest["parameters"].get(field) != value
                           for field, value in expected_policy.items())):
                raise ValueError("Local execution segment parameters differ")
            if key != "original" and (
                not manifest.get("completed_at")
                or manifest.get("attempt_ids") != expected_ids
                or manifest.get("attempt_count") != len(expected_ids)
            ):
                raise ValueError("Local resumed execution is incomplete")
            if key != "original" and manifest.get("unexecuted"):
                if (physical[-1].get("error_type") != "timeout"
                        or any(item.get("reason") !=
                               "non_cancellable_call_still_running; remaining provider cases not executed"
                               or item.get("after_attempt_id") != physical[-1]["attempt_id"]
                               for item in manifest["unexecuted"])):
                    raise ValueError("Local execution stopped for an unaccounted reason")
            if key == "admitted" and manifest.get("scheduling") != admitted.get("scheduling"):
                raise ValueError("Local admission manifest changed the scheduling contract")
            continuation = segment.get("continuation_plan")
            if continuation:
                continuation = read(continuation)
                expected_kind = {"resumed": "resume_unexecuted_after_process_exit",
                                 "admitted": "resume_unexecuted_with_admission"}.get(key)
                parent_field = "admission_plan_sha256" if key == "admitted" else "resumed_plan_sha256"
                if (key == "original" or continuation.get("kind") != expected_kind
                        or continuation.get("method") != name
                        or continuation.get(parent_field) != plans[key]["sha256"]
                        or continuation.get("parameters") != expected_policy
                        or continuation.get("provider_config_sha256") != plan["provider_config_sha256"]
                        or continuation.get("runtime_source_sha256") != plan["runtime_source_sha256"]
                        or continuation.get("dataset_sha256") != plan.get("dataset_sha256")
                        or continuation.get("case_ids") != declared_scopes[key][offsets[key] - len(physical):]):
                    raise ValueError("Local continuation differs from its pre-call scope")
            for row in physical:
                identifier = row["attempt_id"]
                if identifier in seen_attempts or identifier not in by_attempt:
                    raise ValueError("Local execution altered or repeated a measured result")
                if row != by_attempt[identifier]:
                    validate_record_correction(source, row, by_attempt[identifier], correction)
                seen_attempts.add(identifier)
            ids.extend(expected_ids)
        if (any(offsets[key] != len(scope) for key, scope in expected_scopes.items())
                or seen_plans != {key for key, scope in expected_scopes.items() if scope}
                or len(ids) != batch.get("attempt_count")
                or set(ids) != set(batch.get("attempt_ids", []))):
            raise ValueError("Local execution segments repeat or omit a call")
    if seen_names != names or seen_attempts != set(by_attempt):
        raise ValueError("Local execution lineage is incomplete")

