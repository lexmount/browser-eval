"""Current-run retrieval answers, never filled from a frozen expected answer.

Version one deliberately accepts only strict JSON observations and structural
projections. Version two additionally supports one built-in, deterministic
reviewed-span projection whose complete ordered annotation is part of the
contract digest. It never executes annotation-provided code or answer strings.
Version three adds opt-in exact string-value sets and accurately attributed,
materialized review evidence. Reviewer declarations are not identity authentication.
Unstructured or incomplete observations remain unsupported, not a browser
failure. Every protocol revision requires fresh qualification.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

PROTOCOL_V1 = "lex_replay_current_run_retrieval/1"
PROTOCOL_V2 = "lex_replay_current_run_retrieval/2"
PROTOCOL_V3 = "lex_replay_current_run_retrieval/3"
# Keep the original public alias stable for existing v1 catalogs and callers.
PROTOCOL = PROTOCOL_V1
SUPPORTED_PROTOCOLS = frozenset({PROTOCOL_V1, PROTOCOL_V2, PROTOCOL_V3})
REVIEWED_SPAN_KIND = "reviewed_ordered_spans/1"
REVIEWED_SPAN_ANNOTATION = "lex_replay_reviewed_span_annotation/1"


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def text_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def strict_json(text: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid(value: str) -> None:
        raise ValueError(f"nonfinite JSON value: {value}")

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("nonfinite JSON number")
        return number

    return json.loads(
        text, object_pairs_hook=pairs, parse_constant=invalid, parse_float=finite_float
    )


def pointer(value: Any, path: str) -> Any:
    if not isinstance(path, str) or (path and not path.startswith("/")):
        raise ValueError("invalid JSON Pointer")
    for token in path.split("/")[1:] if path else []:
        if re.search(r"~(?![01])", token):
            raise ValueError("invalid JSON Pointer escape")
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not token.isascii() or not token.isdigit() or (len(token) > 1 and token[0] == "0"):
                raise ValueError("invalid array index")
            value = value[int(token)]
        elif isinstance(value, dict):
            value = value[token]
        else:
            raise ValueError("JSON Pointer traverses a scalar")
    return value


def project(expression: dict[str, Any], sources: dict[str, Any], depth: int = 0) -> Any:
    if depth > 32 or not isinstance(expression, dict):
        raise ValueError("invalid projection")
    if set(expression) == {"source", "pointer"}:
        return pointer(sources[expression["source"]], expression["pointer"])
    if set(expression) == {"array"} and isinstance(expression["array"], list):
        return [project(item, sources, depth + 1) for item in expression["array"]]
    if set(expression) == {"object"} and isinstance(expression["object"], dict):
        return {
            key: project(item, sources, depth + 1) for key, item in expression["object"].items()
        }
    if set(expression) == {"map"}:
        spec = expression["map"]
        if not isinstance(spec, dict) or set(spec) != {"input", "projection"}:
            raise ValueError("invalid map")
        _validate_projection(spec["projection"], {"$item"})
        values = project(spec["input"], sources, depth + 1)
        if not isinstance(values, list):
            raise ValueError("map input must be an array")
        return [
            project(spec["projection"], {**sources, "$item": value}, depth + 1) for value in values
        ]
    raise ValueError("unsupported projection; constants and executable code are forbidden")


def validate_contract(contract: dict[str, Any], calls: list[dict[str, Any]]) -> None:
    schema = contract.get("schema")
    if schema not in SUPPORTED_PROTOCOLS:
        raise ValueError("unsupported retrieval protocol")
    common = {"schema", "task_id", "canonical_calls_sha256", "sources"}
    version_fields = (
        {"projection"}
        if schema == PROTOCOL_V1
        else {"reviewed_span_projection", "semantic_reference"}
    )
    if schema == PROTOCOL_V3:
        version_fields = {"semantic_reference", "review_evidence"} | (
            {"reviewed_span_projection"}
            if "reviewed_span_projection" in contract
            else {"projection"}
        )
    required = common | version_fields
    optional = {"requirements", "empty_result"} if schema != PROTOCOL_V2 else {"requirements"}
    if schema == PROTOCOL_V3:
        optional.add("reviewed_scope")
    if not required <= set(contract) or set(contract) - required - optional:
        raise ValueError("invalid retrieval observation contract fields")
    if contract["canonical_calls_sha256"] != digest(calls):
        raise ValueError("retrieval contract differs from canonical actions")
    sources = contract["sources"]
    if not isinstance(sources, list) or not sources:
        raise ValueError("retrieval requires observation sources")
    names: set[str] = set()
    for source in sources:
        if set(source) != {"name", "step", "action_sha256", "decode"}:
            raise ValueError("invalid observation source")
        name, step = source["name"], source["step"]
        if not isinstance(name, str) or not name or name.startswith("$") or name in names:
            raise ValueError("invalid or duplicate source name")
        names.add(name)
        if type(step) is not int or not 1 <= step <= len(calls):
            raise ValueError("invalid observation step")
        action = calls[step - 1]
        if action.get("action") not in {"get_text", "evaluate"} or source["decode"] != "json":
            raise ValueError("retrieval requires structured JSON read observations")
        if digest(action) != source["action_sha256"]:
            raise ValueError("observation action binding mismatch")
    if schema == PROTOCOL_V3:
        _validate_review_evidence(contract)
        if "reviewed_span_projection" in contract:
            _validate_reviewed_span_projection(contract, names)
        else:
            expression = contract["projection"]
            if not isinstance(expression, dict) or set(expression) != {"distinct_exact_strings"}:
                raise ValueError("v3 value sets require an explicit distinct projection")
            _validate_projection(expression["distinct_exact_strings"], names)
    elif schema == PROTOCOL_V1:
        _validate_projection(contract["projection"], names)
    else:
        _validate_reviewed_span_projection(contract, names)
    if len({source["step"] for source in contract["sources"]}) != len(contract["sources"]):
        raise ValueError("one observation step cannot have multiple source aliases")
    requirements = contract.get("requirements", {})
    if not isinstance(requirements, dict) or set(requirements) - {"scope", "completeness"}:
        raise ValueError("invalid observation requirements")
    for requirement in requirements.values():
        if not isinstance(requirement, dict) or set(requirement) != {"source", "pointer"}:
            raise ValueError("requirements must directly read observed booleans")
        _validate_projection(requirement, names)
    if "reviewed_scope" in contract:
        _validate_reviewed_scope_contract(contract, names)
    if schema in {PROTOCOL_V2, PROTOCOL_V3} and (
        set(requirements) != {"scope", "completeness"}
        or digest(requirements["scope"]) == digest(requirements["completeness"])
    ):
        raise ValueError("reviewed spans require independent scope and completeness observations")
    if "empty_result" in contract:
        if (
            contract["empty_result"] != "NOT_FOUND_ERROR"
            or set(requirements) != {"scope", "completeness"}
            or digest(requirements["scope"]) == digest(requirements["completeness"])
        ):
            raise ValueError(
                "absence needs independently observed scope and completeness requirements"
            )


def _scope_ref(spec: Any, names: set[str]) -> None:
    if not isinstance(spec, dict) or set(spec) != {"source", "pointer"}:
        raise ValueError("reviewed scope requires a direct observation reference")
    _validate_projection(spec, names)


def _validate_reviewed_scope_contract(contract: dict[str, Any], names: set[str]) -> None:
    spec = contract["reviewed_scope"]
    if (
        contract["schema"] != PROTOCOL_V3
        or not isinstance(spec, dict)
        or set(spec) != {"review_pointer", "observed_requirements", "populations", "identity_joins"}
    ):
        raise ValueError("invalid reviewed scope contract")
    if not _valid_pointer_path(spec["review_pointer"]):
        raise ValueError("invalid scope review pointer")
    checks = spec["observed_requirements"]
    if not isinstance(checks, list) or not checks:
        raise ValueError("reviewed scope needs independent observed requirements")
    for check in checks:
        _scope_ref(check, names)
    if not isinstance(spec["populations"], list) or not isinstance(spec["identity_joins"], list):
        raise ValueError("invalid reviewed scope constraints")
    for population in spec["populations"]:
        if not isinstance(population, dict) or set(population) != {
            "records",
            "pages",
            "id_pointer",
            "required_fields",
        }:
            raise ValueError("invalid reviewed population constraint")
        _scope_ref(population["records"], names)
        _scope_ref(population["pages"], names)
        if (
            not _valid_pointer_path(population["id_pointer"])
            or not isinstance(population["required_fields"], list)
            or not population["required_fields"]
            or any(not _valid_pointer_path(p) for p in population["required_fields"])
        ):
            raise ValueError("invalid reviewed population fields")
    for join in spec["identity_joins"]:
        if not isinstance(join, dict) or set(join) != {
            "selected",
            "candidates",
            "result",
            "selected_fields",
            "candidate_fields",
            "allow_unique_exact_name_inference",
        }:
            raise ValueError("invalid reviewed identity join")
        for key in ("selected", "candidates", "result"):
            _scope_ref(join[key], names)
        if type(join["allow_unique_exact_name_inference"]) is not bool:
            raise ValueError("identity inference policy must be explicit")
        for key, fields in (
            ("selected_fields", {"id", "name", "linked_name", "linked_email"}),
            ("candidate_fields", {"id", "name", "email"}),
        ):
            mapping = join[key]
            if (
                not isinstance(mapping, dict)
                or set(mapping) != fields
                or any(not _valid_pointer_path(p) for p in mapping.values())
            ):
                raise ValueError("invalid reviewed identity field mapping")
    proof = pointer(contract["review_evidence"]["source_review"], spec["review_pointer"])
    if not isinstance(proof, dict) or set(proof) != {
        "decision",
        "rationale",
        "scope_contract_sha256",
        "sources",
    }:
        raise ValueError("invalid materialized scope review")
    if (
        proof["decision"] not in {"accept", "reject"}
        or not isinstance(proof["rationale"], str)
        or not proof["rationale"].strip()
    ):
        raise ValueError("scope review requires explicit decision and rationale")
    if proof["scope_contract_sha256"] != digest(
        {"reviewed_scope": spec, "requirements": contract["requirements"]}
    ):
        raise ValueError("reviewed scope constraints changed")
    if not isinstance(proof["sources"], list) or len(proof["sources"]) != len(names):
        raise ValueError("scope review must bind every source")
    found = set()
    for binding in proof["sources"]:
        if (
            not isinstance(binding, dict)
            or set(binding) != {"source", "output_sha256", "value_sha256"}
            or binding["source"] not in names
            or binding["source"] in found
        ):
            raise ValueError("invalid or duplicate scope review source")
        found.add(binding["source"])
        if any(
            not isinstance(binding[k], str) or not re.fullmatch(r"[0-9a-f]{64}", binding[k])
            for k in ("output_sha256", "value_sha256")
        ):
            raise ValueError("invalid scope observation digest")


def _check_scope_population(spec: dict[str, Any], values: dict[str, Any]) -> None:
    records, pages = project(spec["records"], values), project(spec["pages"], values)
    if not isinstance(records, list) or not records or not isinstance(pages, list) or not pages:
        raise ValueError("reviewed population is unavailable")
    count = 0
    for index, page in enumerate(pages, 1):
        if (
            not isinstance(page, dict)
            or type(page.get("page")) is not int
            or page["page"] != index
            or type(page.get("rows")) is not int
            or page["rows"] < 0
        ):
            raise ValueError("reviewed population page sequence is incomplete")
        if "total" in page and (type(page["total"]) is not int or page["total"] != len(records)):
            raise ValueError("reviewed population total differs")
        count += page["rows"]
    if count != len(records):
        raise ValueError("reviewed population count differs")
    ids = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("reviewed population record unavailable")
        key = pointer(record, spec["id_pointer"])
        if type(key) not in {int, str} or str(key).strip() == "":
            raise ValueError("reviewed population ID missing")
        ids.append(str(key))
        for field in spec["required_fields"]:
            value = pointer(record, field)
            if (
                value is None
                or value == []
                or value == {}
                or (isinstance(value, str) and not value.strip())
            ):
                raise ValueError("reviewed population required field missing")
    if len(set(ids)) != len(ids):
        raise ValueError("reviewed population IDs are not unique")


def _check_scope_identity_join(spec: dict[str, Any], values: dict[str, Any]) -> str:
    selected, candidates, result = (
        project(spec[key], values) for key in ("selected", "candidates", "result")
    )
    if (
        not isinstance(selected, dict)
        or not isinstance(result, dict)
        or not isinstance(candidates, list)
        or not candidates
    ):
        raise ValueError("reviewed identity records unavailable")

    def normalized(value: Any) -> str:
        return " ".join(value.split()) if isinstance(value, str) else ""

    fields, target = spec["selected_fields"], spec["candidate_fields"]
    ids = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("reviewed customer row unavailable")
        cid, name, email = (pointer(candidate, target[key]) for key in ("id", "name", "email"))
        if (
            type(cid) not in {str, int}
            or not str(cid).strip()
            or not normalized(name)
            or not isinstance(email, str)
            or not re.fullmatch(r"[^\s@]+@[^\s@]+", email)
        ):
            raise ValueError("reviewed customer identity incomplete")
        ids.append(str(cid))
    if len(set(ids)) != len(ids):
        raise ValueError("reviewed customer IDs are not unique")
    explicit = pointer(selected, fields["id"])
    if explicit is not None:
        if type(explicit) not in {str, int}:
            raise ValueError("invalid explicit customer identity")
        matches = [c for c in candidates if str(pointer(c, target["id"])) == str(explicit)]
        basis = "explicit_customer_id"
    else:
        name = normalized(pointer(selected, fields["name"]))
        if not spec["allow_unique_exact_name_inference"] or len(name.split()) < 2:
            raise ValueError("unpermitted or incomplete name inference")
        matches = [c for c in candidates if normalized(pointer(c, target["name"])) == name]
        basis = "unique_exact_name_inference"
    if len(matches) != 1 or matches[0] != result:
        raise ValueError("reviewed identity is missing, ambiguous or mismatched")
    for key, candidate_key in (("linked_name", "name"), ("linked_email", "email")):
        linked = pointer(selected, fields[key])
        expected = pointer(result, target[candidate_key])
        if linked is not None and (normalized(linked) if key == "linked_name" else linked) != (
            normalized(expected) if key == "linked_name" else expected
        ):
            raise ValueError("explicit customer identity conflict")
    return basis


def _apply_reviewed_scope(
    contract: dict[str, Any], values: dict[str, Any], sources: list[dict[str, Any]]
) -> dict[str, Any]:
    spec = contract["reviewed_scope"]
    proof = pointer(contract["review_evidence"]["source_review"], spec["review_pointer"])
    if proof["decision"] != "accept":
        raise ValueError("scope review did not accept")
    observed = {source["name"]: source for source in sources}
    for binding in proof["sources"]:
        name = binding["source"]
        if binding["output_sha256"] != observed[name]["output_sha256"] or binding[
            "value_sha256"
        ] != digest(values[name]):
            raise ValueError("scope review does not match current observation")
    for requirement in spec["observed_requirements"]:
        if project(requirement, values) is not True:
            raise ValueError("scope review cannot override observed hard requirement")
    for population in spec["populations"]:
        _check_scope_population(population, values)
    bases = [_check_scope_identity_join(join, values) for join in spec["identity_joins"]]
    return {
        "scope_review_sha256": digest(proof),
        "raw_observations_unchanged": True,
        "identity_bases": bases,
    }


def _valid_pointer_path(path: Any) -> bool:
    return (
        isinstance(path, str)
        and (not path or path.startswith("/"))
        and not re.search(r"~(?![01])", path)
    )


def _validate_review_evidence(contract: dict[str, Any]) -> None:
    """Check materialized provenance declarations, not a human's real-world identity."""
    reference, evidence = contract["semantic_reference"], contract["review_evidence"]
    kind = (
        "reviewed_span_annotation"
        if "reviewed_span_projection" in contract
        else "reviewed_value_set"
    )
    if not isinstance(reference, dict) or set(reference) != {
        "kind",
        "browser_semantic_inference",
        "reviewer",
        "scope",
        "evidence_sha256",
    }:
        raise ValueError("invalid v3 semantic reference")
    reviewer = reference["reviewer"]
    if (
        reference["kind"] != kind
        or reference["browser_semantic_inference"] is not False
        or not isinstance(reviewer, dict)
        or set(reviewer) != {"kind", "identity"}
        or reviewer["kind"] not in {"agent", "human"}
        or not isinstance(reviewer["identity"], str)
        or not reviewer["identity"].strip()
        or not isinstance(reference["scope"], str)
        or not reference["scope"].strip()
    ):
        raise ValueError("explicit reviewer identity, kind and scope required")
    if not isinstance(evidence, dict) or set(evidence) != {
        "schema",
        "reviewer",
        "scope",
        "task_id",
        "sources_sha256",
        "projection_sha256",
        "source_review",
        "source_review_sha256",
        "source_review_artifact",
    }:
        raise ValueError("invalid materialized review evidence")
    projection = contract.get("reviewed_span_projection", contract.get("projection"))
    source_review = evidence["source_review"]
    if not isinstance(source_review, dict):
        raise ValueError("review source must be a materialized object")
    _validate_review_artifact(evidence["source_review_artifact"], source_review)
    for artifact in source_review.get("predecessor_artifacts", []):
        _validate_review_artifact(
            artifact, artifact.get("content") if isinstance(artifact, dict) else None
        )
        for binding in artifact.get("bindings", []):
            if not isinstance(binding, dict) or set(binding) != {
                "artifact_pointer",
                "contract_pointer",
            }:
                raise ValueError("invalid predecessor artifact binding")
            if pointer(artifact["content"], binding["artifact_pointer"]) != pointer(
                contract, binding["contract_pointer"]
            ):
                raise ValueError("predecessor artifact binding mismatch")
    if (
        evidence["schema"] != "lex_replay_review_evidence/1"
        or digest(evidence) != reference["evidence_sha256"]
        or evidence["reviewer"] != reviewer
        or evidence["scope"] != reference["scope"]
        or evidence["task_id"] != contract["task_id"]
        or evidence["sources_sha256"] != digest(contract["sources"])
        or evidence["projection_sha256"] != digest(projection)
        or not isinstance(source_review, dict)
        or evidence["source_review_sha256"] != digest(source_review)
        or source_review.get("reviewer") != reviewer
        or source_review.get("scope") != reference["scope"]
        or source_review.get("task_id") != contract["task_id"]
        or source_review.get("sources_sha256") != evidence["sources_sha256"]
        or source_review.get("projection_sha256") != evidence["projection_sha256"]
    ):
        raise ValueError("review evidence reviewer, scope, source or projection binding mismatch")


def _validate_review_artifact(artifact: Any, content: Any) -> None:
    """Verify original UTF-8 JSON bytes, including whitespace, before decoding."""
    if (
        not isinstance(artifact, dict)
        or not {"utf8", "sha256", "content"} <= set(artifact)
        or set(artifact) - {"utf8", "sha256", "content", "bindings"}
    ):
        raise ValueError("invalid materialized review artifact")
    text = artifact["utf8"]
    if not isinstance(text, str) or text_digest(text) != artifact["sha256"]:
        raise ValueError("review artifact byte hash mismatch")
    if strict_json(text) != artifact["content"] or artifact["content"] != content:
        raise ValueError("review artifact decoded content mismatch")


def _validate_reviewed_span_projection(contract: dict[str, Any], names: set[str]) -> None:
    reference = contract["semantic_reference"]
    if contract["schema"] == PROTOCOL_V2 and reference != {
        "kind": "human_reviewed_span_annotation",
        "browser_semantic_inference": False,
    }:
        raise ValueError("v2 requires an explicit human semantic reference disclosure")
    spec = contract["reviewed_span_projection"]
    expected = {
        "kind",
        "source",
        "collection_pointer",
        "identity_pointer",
        "semantic_text_pointer",
        "output_text_pointer",
        "output",
        "span_unit",
        "annotation",
    }
    if not isinstance(spec, dict) or set(spec) != expected:
        raise ValueError("invalid reviewed span projection fields")
    if (
        spec["kind"] != REVIEWED_SPAN_KIND
        or spec["source"] not in names
        or spec["output"] not in {"selected_item_text", "selected_spans"}
        or spec["span_unit"] != "unicode_code_points"
        or not all(
            _valid_pointer_path(spec[field])
            for field in (
                "collection_pointer",
                "identity_pointer",
                "semantic_text_pointer",
                "output_text_pointer",
            )
        )
    ):
        raise ValueError("invalid reviewed span projection")
    annotation = spec["annotation"]
    if not isinstance(annotation, dict) or set(annotation) != {"schema", "semantic", "items"}:
        raise ValueError("invalid reviewed span annotation fields")
    if (
        annotation["schema"] != REVIEWED_SPAN_ANNOTATION
        or annotation["semantic"] != "exactly_one_book_recommendation"
        or not isinstance(annotation["items"], list)
        or not annotation["items"]
    ):
        raise ValueError("invalid reviewed span annotation")
    hash_pattern = re.compile(r"[0-9a-f]{64}")
    for item in annotation["items"]:
        if not isinstance(item, dict) or set(item) != {
            "identity_sha256",
            "semantic_text_sha256",
            "output_text_sha256",
            "recommendation_count",
            "spans",
        }:
            raise ValueError("invalid reviewed span annotation item fields")
        if (
            not isinstance(item["identity_sha256"], str)
            or not hash_pattern.fullmatch(item["identity_sha256"])
            or not isinstance(item["semantic_text_sha256"], str)
            or not hash_pattern.fullmatch(item["semantic_text_sha256"])
            or not isinstance(item["output_text_sha256"], str)
            or not hash_pattern.fullmatch(item["output_text_sha256"])
            or type(item["recommendation_count"]) is not int
            or item["recommendation_count"] < 0
            or not isinstance(item["spans"], list)
        ):
            raise ValueError("invalid reviewed span annotation item")
        expected_span_count = 1 if item["recommendation_count"] == 1 else 0
        if len(item["spans"]) != expected_span_count:
            raise ValueError("semantic count and reviewed spans disagree")
        for span in item["spans"]:
            if (
                not isinstance(span, dict)
                or set(span) != {"start", "end"}
                or type(span["start"]) is not int
                or type(span["end"]) is not int
                or span["start"] < 0
                or span["end"] <= span["start"]
            ):
                raise ValueError("invalid reviewed span boundary")


def _project_reviewed_spans(contract: dict[str, Any], sources: dict[str, Any]) -> list[str]:
    spec = contract["reviewed_span_projection"]
    values = pointer(sources[spec["source"]], spec["collection_pointer"])
    annotations = spec["annotation"]["items"]
    if not isinstance(values, list) or len(values) != len(annotations):
        raise ValueError("reviewed annotation does not cover the complete observed collection")
    answer: list[str] = []
    for index, (value, annotation) in enumerate(zip(values, annotations, strict=True)):
        identity = pointer(value, spec["identity_pointer"])
        semantic_text = pointer(value, spec["semantic_text_pointer"])
        output_text = pointer(value, spec["output_text_pointer"])
        if not isinstance(semantic_text, str) or not isinstance(output_text, str):
            raise ValueError("reviewed semantic and output source text must be strings")
        if (
            digest(identity) != annotation["identity_sha256"]
            or text_digest(semantic_text) != annotation["semantic_text_sha256"]
            or text_digest(output_text) != annotation["output_text_sha256"]
        ):
            raise ValueError(f"reviewed source identity or text changed at index {index}")
        if annotation["recommendation_count"] != 1:
            continue
        if spec["output"] == "selected_item_text":
            answer.append(output_text)
            continue
        span = annotation["spans"][0]
        if span["end"] > len(output_text):
            raise ValueError(f"reviewed span exceeds current text at index {index}")
        selected = output_text[span["start"] : span["end"]]
        if not selected:
            raise ValueError(f"reviewed span is empty at index {index}")
        answer.append(selected)
    return answer


def _validate_projection(expression: Any, names: set[str], depth: int = 0) -> None:
    if depth > 32 or not isinstance(expression, dict):
        raise ValueError("invalid projection")
    if set(expression) == {"source", "pointer"}:
        if expression["source"] not in names or not isinstance(expression["pointer"], str):
            raise ValueError("unknown projection source or pointer")
        path = expression["pointer"]
        if (path and not path.startswith("/")) or re.search(r"~(?![01])", path):
            raise ValueError("invalid projection pointer")
        return
    if (
        set(expression) == {"array"}
        and isinstance(expression["array"], list)
        and expression["array"]
    ):
        for child in expression["array"]:
            _validate_projection(child, names, depth + 1)
        return
    if (
        set(expression) == {"object"}
        and isinstance(expression["object"], dict)
        and expression["object"]
    ):
        for child in expression["object"].values():
            _validate_projection(child, names, depth + 1)
        return
    if (
        set(expression) == {"map"}
        and isinstance(expression["map"], dict)
        and set(expression["map"]) == {"input", "projection"}
    ):
        _validate_projection(expression["map"]["input"], names, depth + 1)
        _validate_projection(expression["map"]["projection"], {"$item"}, depth + 1)
        return
    raise ValueError("unsupported projection; constants and executable code are forbidden")


def _validate_answer(value: Any) -> None:
    if value is None or value == "" or (isinstance(value, (dict, list)) and not value):
        raise ValueError("answer contains a missing or empty value")
    if isinstance(value, dict):
        for child in value.values():
            _validate_answer(child)
    elif isinstance(value, list):
        for child in value:
            _validate_answer(child)


def build_actual_response(
    *,
    contract: dict[str, Any] | None,
    task_id: int,
    calls: list[dict[str, Any]],
    records: list[dict[str, Any]],
    run_id: str,
    attempt_id: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": contract.get("schema", PROTOCOL) if contract is not None else PROTOCOL,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "task_id": task_id,
        "status": "unsupported",
        "actual_response": None,
        "sources": [],
    }
    if contract is None:
        return {**result, "reason": "missing_frozen_observation_contract"}
    try:
        if not run_id or not attempt_id or contract.get("task_id") != task_id:
            raise ValueError("missing or mismatched execution identity")
        validate_contract(contract, calls)
        result["contract_sha256"] = digest(contract)
        values = {}
        for source in contract["sources"]:
            step = source["step"]
            row = records[step - 1]
            if (
                row.get("index") != step
                or row.get("status") != "success"
                or row.get("tool_status", "success") != "success"
                or row.get("observation_status") == "error"
            ):
                raise ValueError("observation action did not succeed")
            if row.get("arguments") != calls[step - 1] or not isinstance(row.get("result"), str):
                raise ValueError("observation trace binding mismatch")
            values[source["name"]] = strict_json(row["result"])
            result["sources"].append(
                {
                    "name": source["name"],
                    "step": step,
                    "action_sha256": digest(row["arguments"]),
                    "output_sha256": digest(row["result"]),
                }
            )
        reviewed_scope = None
        if "reviewed_scope" in contract:
            reviewed_scope = _apply_reviewed_scope(contract, values, result["sources"])
            result["reviewed_scope"] = reviewed_scope
        for name, requirement in contract.get("requirements", {}).items():
            observed = project(requirement, values)
            if observed is not True and not (
                name == "scope" and observed is False and reviewed_scope is not None
            ):
                raise ValueError("observation scope or completeness requirement is not true")
        if "reviewed_span_projection" in contract:
            data = _project_reviewed_spans(contract, values)
            result["semantic_reference"] = {
                **contract["semantic_reference"],
                "annotation_sha256": digest(contract["reviewed_span_projection"]["annotation"]),
            }
        elif contract["schema"] == PROTOCOL_V3:
            data = project(contract["projection"]["distinct_exact_strings"], values)
            if not isinstance(data, list) or any(type(value) is not str for value in data):
                raise ValueError("distinct projection requires an array of exact strings")
            data = list(dict.fromkeys(data))
            result["semantic_reference"] = contract["semantic_reference"]
        else:
            data = project(contract["projection"], values)
        # Absence requires a separate, qualified completeness protocol. In v1
        # null/empty results cannot silently become NOT_FOUND or success.
        proven_absence = (
            isinstance(data, list)
            and not data
            and contract.get("empty_result") == "NOT_FOUND_ERROR"
        )
        if proven_absence and any(
            project(contract["requirements"][name], values) is not True
            for name in ("scope", "completeness")
        ):
            raise ValueError("empty retrieval requires observed complete task scope")
        if not proven_absence and (data is None or data == [] or data == {} or data == ""):
            raise ValueError("empty retrieval requires a completeness contract")
        if not proven_absence:
            _validate_answer(data)
        result.update(
            status="supported",
            actual_response={
                "task_type": "RETRIEVE",
                "status": "NOT_FOUND_ERROR" if proven_absence else "SUCCESS",
                "retrieved_data": None if proven_absence else data,
                "error_details": None,
            },
        )
        result["actual_response_sha256"] = digest(result["actual_response"])
    except (ValueError, TypeError, KeyError, IndexError) as error:
        result.update(status="insufficient", reason=str(error), actual_response=None)
    return result


def load_contracts(
    binding: dict[str, Any] | None, root: Path, candidates: dict[int, dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    if binding is None:
        return {}
    if binding.get("protocol") not in SUPPORTED_PROTOCOLS:
        raise ValueError("unsupported retrieval protocol")
    raw = root / str(binding.get("path") or "")
    if (
        any(path.is_symlink() for path in [raw, *raw.parents])
        or root.resolve() not in raw.resolve().parents
    ):
        raise ValueError("unsafe retrieval contract path")
    data = raw.read_bytes()
    if hashlib.sha256(data).hexdigest() != binding.get("sha256"):
        raise ValueError("retrieval contract file hash mismatch")
    catalog = strict_json(data.decode())
    if (
        set(catalog) != {"schema", "contracts"}
        or catalog["schema"] != binding["protocol"]
        or catalog["schema"] not in SUPPORTED_PROTOCOLS
    ):
        raise ValueError("invalid retrieval contract catalog")
    result = {}
    for contract in catalog["contracts"]:
        task_id = contract["task_id"]
        if type(task_id) is not int or task_id in result or task_id not in candidates:
            raise ValueError("duplicate or unknown retrieval task")
        validate_contract(contract, candidates[task_id]["tool_calls"])
        result[task_id] = contract
    return result


def require_current_retrieval(plan: dict[str, Any]) -> None:
    missing = set(plan.get("retrieval_task_ids", [])) - set(plan.get("retrieval_contracts", {}))
    if missing:
        raise ValueError(
            "current-run retrieval contracts required before qualification/promotion: "
            f"{sorted(missing)}"
        )


def verify_attempt(
    attempt: dict[str, Any],
    contract: dict[str, Any],
    calls: list[dict[str, Any]],
    *,
    expected_data_checksum: str,
) -> None:
    if attempt.get("retrieval_protocol") != contract.get("schema"):
        raise ValueError("retrieval attempt uses a different contract protocol")
    evidence = attempt.get("evidence") or {}
    loaded = {}
    for name in ("trace", "retrieval_acceptance", "actual_response", "evaluation"):
        ref = evidence.get(name) or {}
        raw = Path(str(ref.get("path") or ""))
        if (
            not raw.is_absolute()
            or any(path.is_symlink() for path in [raw, *raw.parents])
            or not raw.is_file()
        ):
            raise ValueError("missing or unsafe current-run retrieval evidence")
        data = raw.read_bytes()
        if hashlib.sha256(data).hexdigest() != ref.get("sha256"):
            raise ValueError("current-run retrieval evidence hash mismatch")
        loaded[name] = strict_json(data.decode())
    receipt = loaded["retrieval_acceptance"]
    recomputed = build_actual_response(
        contract=contract,
        task_id=int(attempt["task_id"]),
        calls=calls,
        records=loaded["trace"],
        run_id=attempt.get("run_id", ""),
        attempt_id=attempt.get("attempt_id", ""),
    )
    if (
        digest(recomputed) != digest(receipt)
        or receipt["status"] != "supported"
        or digest(loaded["actual_response"]) != receipt["actual_response_sha256"]
    ):
        raise ValueError("current-run retrieval cannot be reproduced")
    if attempt.get("evaluation_input_sha256") != receipt["actual_response_sha256"]:
        raise ValueError("evaluator did not consume current-run retrieval response")
    answer_rows = [
        row
        for row in loaded["evaluation"].get("evaluators_results", [])
        if row.get("evaluator_name") == "AgentResponseEvaluator"
    ]
    if (
        len(answer_rows) != 1
        or digest(answer_rows[0].get("actual")) != receipt["actual_response_sha256"]
    ):
        raise ValueError("official evaluator actual differs from current-run retrieval")
    evaluation = loaded["evaluation"]
    if evaluation.get("task_id") != attempt["task_id"]:
        raise ValueError("official evaluator task differs from retrieval attempt")
    from .webarena_dataset import _validate_evaluation_provenance

    _validate_evaluation_provenance(evaluation, attempt, expected_data_checksum)
    accepted = (
        evaluation.get("score") == 1
        and evaluation.get("status") in {"pass", "success"}
        and answer_rows[0].get("score") == 1
        and answer_rows[0].get("status") in {"pass", "success"}
    )
    if attempt.get("evaluator_status") != ("pass" if accepted else "fail"):
        raise ValueError("claimed evaluator status contradicts actual evaluation")
    if attempt.get("status") != (
        "pass" if accepted and attempt.get("process_status") == "pass" else "fail"
    ):
        raise ValueError("claimed verdict contradicts actual evaluation")
