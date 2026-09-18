"""Traceable WebArena contract corrections used only by trajectory replay.

The official task objects are immutable and remain the scoring authority for
historical Browser Use runs.  This module copies a task, applies a small JSON
Patch subset, and returns replay-only assertions that the official evaluator
model cannot express (currently final DOM state).
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from webarena_verified.core.evaluation.evaluators import EVALUATOR_REGISTRY
from webarena_verified.core.utils.checksum import compute_data_file_checksum
from webarena_verified.types.eval import TaskEvalContext, TaskEvalResult
from webarena_verified.types.task import WebArenaVerifiedTask

SCHEMA = "lex_webarena_replay_task_corrections/1"
DEFAULT_CORRECTIONS_PATH = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "trajectory-replay"
    / "webarena-verified"
    / "task-corrections.json"
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _decode_pointer_token(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


def _pointer_parts(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise ValueError(f"invalid JSON pointer: {pointer!r}")
    return [_decode_pointer_token(item) for item in pointer[1:].split("/")]


def _resolve(document: Any, pointer: str) -> Any:
    current = document
    for part in _pointer_parts(pointer):
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise ValueError(f"JSON pointer crosses a scalar: {pointer!r}")
    return current


def _parent(document: Any, pointer: str) -> tuple[Any, str]:
    parts = _pointer_parts(pointer)
    if not parts:
        raise ValueError("root replacement is not supported")
    parent_pointer = "/" + "/".join(
        item.replace("~", "~0").replace("/", "~1") for item in parts[:-1]
    )
    return (_resolve(document, parent_pointer) if parts[:-1] else document, parts[-1])


def _apply_operation(document: dict[str, Any], operation: Mapping[str, Any]) -> None:
    kind = operation.get("op")
    pointer = operation.get("path")
    if kind not in {"add", "remove", "replace"} or not isinstance(pointer, str):
        raise ValueError(f"unsupported correction operation: {operation!r}")
    parent, key = _parent(document, pointer)
    if isinstance(parent, list):
        index = int(key)
        if kind == "add":
            parent.insert(index, copy.deepcopy(operation.get("value")))
        elif kind == "remove":
            parent.pop(index)
        else:
            parent[index] = copy.deepcopy(operation.get("value"))
        return
    if not isinstance(parent, dict):
        raise ValueError(f"operation parent is not a container: {pointer!r}")
    if kind == "remove":
        del parent[key]
    elif kind == "replace":
        if key not in parent:
            raise ValueError(f"replace target does not exist: {pointer!r}")
        parent[key] = copy.deepcopy(operation.get("value"))
    else:
        parent[key] = copy.deepcopy(operation.get("value"))


@dataclass(frozen=True)
class CorrectionCatalog:
    """Validated correction records and their independent identities."""

    records: Mapping[int, Mapping[str, Any]]
    record_sha256s: Mapping[int, str]
    upstream: Mapping[str, Any]
    source_path: Path
    source_sha256: str


@dataclass(frozen=True)
class CorrectedReplayContract:
    """A corrected task and replay-only checks, with before/after identities."""

    task: WebArenaVerifiedTask
    correction_id: str
    correction_source_sha256: str
    original_task_sha256: str
    corrected_task_sha256: str
    rationale: str
    replay_assertions: tuple[Mapping[str, Any], ...]
    network_alternatives: Mapping[int, tuple[WebArenaVerifiedTask, ...]]


def load_corrections(path: str | Path = DEFAULT_CORRECTIONS_PATH) -> CorrectionCatalog:
    """Load and validate the replay-only correction catalog."""

    source_path = Path(path).expanduser().resolve()
    raw_bytes = source_path.read_bytes()
    payload = json.loads(raw_bytes)
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"unsupported WebArena correction schema: {payload.get('schema')!r}")
    upstream = payload.get("upstream")
    if not isinstance(upstream, dict) or not all(
        upstream.get(key) for key in ("benchmark", "package_version", "dataset_sha256")
    ):
        raise ValueError("correction catalog must identify its upstream benchmark and dataset")
    records: dict[int, Mapping[str, Any]] = {}
    record_sha256s: dict[int, str] = {}
    for record in payload.get("corrections") or []:
        task_id = int(record["task_id"])
        if task_id in records:
            raise ValueError(f"duplicate WebArena correction for task {task_id}")
        if not record.get("correction_id") or not record.get("rationale"):
            raise ValueError(f"task {task_id}: correction id and rationale are required")
        if int(record.get("official_revision") or 0) < 1:
            raise ValueError(f"task {task_id}: official_revision must be positive")
        if not record.get("preconditions") or not record.get("operations"):
            raise ValueError(f"task {task_id}: preconditions and operations are required")
        if record.get("network_event_alternatives") or record.get("reference_receipt"):
            regression_ids = record.get("regression_ids")
            if (
                not record.get("root_cause")
                or not isinstance(regression_ids, list)
                or len(regression_ids) < 2
                or not all(isinstance(item, str) and item for item in regression_ids)
                or len(regression_ids) != len(set(regression_ids))
            ):
                raise ValueError(
                    f"task {task_id}: reference or network correction needs root cause "
                    "and paired regressions"
                )
        records[task_id] = MappingProxyType(copy.deepcopy(record))
        record_sha256s[task_id] = _digest(record)
    return CorrectionCatalog(
        records=MappingProxyType(records),
        record_sha256s=MappingProxyType(record_sha256s),
        upstream=MappingProxyType(copy.deepcopy(upstream)),
        source_path=source_path,
        source_sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


def apply_correction(
    task: WebArenaVerifiedTask,
    catalog: CorrectionCatalog,
) -> CorrectedReplayContract:
    """Return a corrected copy of ``task`` without mutating the official object."""

    record = catalog.records.get(task.task_id)
    if record is None:
        raise KeyError(f"no replay correction registered for task {task.task_id}")
    if task.revision != int(record["official_revision"]):
        raise ValueError(
            f"task {task.task_id}: expected official revision "
            f"{record['official_revision']}, got {task.revision}"
        )

    original = task.model_dump(mode="json")
    corrected = copy.deepcopy(original)
    for precondition in record["preconditions"]:
        actual = _resolve(original, str(precondition["path"]))
        expected = precondition.get("equals")
        if actual != expected:
            raise ValueError(
                f"task {task.task_id}: correction precondition failed at "
                f"{precondition['path']}: expected {expected!r}, got {actual!r}"
            )
    for operation in record["operations"]:
        _apply_operation(corrected, operation)

    reference = record.get("reference_receipt")
    if reference is not None:
        project_root = catalog.source_path.parents[3]
        reference_path = (project_root / str(reference["path"])).resolve()
        try:
            reference_path.relative_to(project_root)
        except ValueError as exc:
            raise ValueError(f"task {task.task_id}: reference receipt escapes repository") from exc
        if reference_path.is_symlink() or not reference_path.is_file():
            raise ValueError(f"task {task.task_id}: reference receipt missing or symlinked")
        raw_reference = reference_path.read_bytes()
        if hashlib.sha256(raw_reference).hexdigest() != reference.get("sha256"):
            raise ValueError(f"task {task.task_id}: reference receipt changed")
        observation = json.loads(raw_reference)
        if observation.get("task_id") != task.task_id:
            raise ValueError(f"task {task.task_id}: reference receipt task changed")
        if task.task_id == 225:
            graphql = observation.get("independent_source") or {}
            products = graphql.get("title_matched_items") or []
            check = observation.get("database_cross_check") or {}
            expected_response = observation.get("expected_response") or {}
            binding = observation.get("candidate_binding") or {}
            candidate_path = (project_root / str(binding.get("path") or "")).resolve()
            try:
                candidate_path.relative_to(project_root)
            except ValueError as exc:
                raise ValueError("task 225: candidate path escapes repository") from exc
            if (
                observation.get("schema") != "trajectory_replay_revised_reference/1"
                or candidate_path.is_symlink()
                or not candidate_path.is_file()
                or hashlib.sha256(candidate_path.read_bytes()).hexdigest() != binding.get("sha256")
                or graphql.get("kind") != "public_storefront_graphql"
                or graphql.get("total_count") != graphql.get("returned_items")
                or graphql.get("total_count") != 6
                or graphql.get("other_sephora_title_items") != 4
                or len(products) != 2
                or check.get("catalog_match_count") != len(products)
                or check.get("review_row_count") != 0
                or check.get("manufacturer_attribute_rows") != 0
                or any(
                    not all(word in item.get("name", "").lower() for word in ("sephora", "brush"))
                    or item.get("review_count") != 0
                    or (item.get("reviews") or {}).get("items") != []
                    or (item.get("reviews") or {}).get("page_info", {}).get("total_pages") != 0
                    for item in products
                )
                or expected_response.get("task_type") != "RETRIEVE"
                or expected_response.get("status") != "NOT_FOUND_ERROR"
                or expected_response.get("retrieved_data") is not None
            ):
                raise ValueError(f"task {task.task_id}: incomplete independent reference")
            corrected["eval"][0]["expected"] = copy.deepcopy(expected_response)
        elif observation.get("derivation", {}).get("kind") == "qualified_catalog_price_range/1":
            source = observation.get("independent_source") or {}
            products = source.get("qualified_products") or []
            if (
                observation.get("schema") != "trajectory_replay_revised_reference/1"
                or source.get("total_count") != source.get("returned_items")
                or type(source.get("total_count")) is not int
                or source["total_count"] < len(products)
                or not products
                or len({row.get("sku") for row in products}) != len(products)
                or len({row.get("name") for row in products}) != len(products)
                or any(
                    not row.get("sku")
                    or not row.get("name")
                    or type(row.get("price_cents")) is not int
                    or row["price_cents"] < 0
                    or row.get("currency") != "USD"
                    or not row.get("qualification_evidence")
                    for row in products
                )
            ):
                raise ValueError(f"task {task.task_id}: incomplete qualified catalog reference")
            prices = [row["price_cents"] for row in products]
            expected = {
                "task_type": "RETRIEVE",
                "status": "SUCCESS",
                "retrieved_data": [
                    {
                        "names": [row["name"] for row in products],
                        "min": min(prices) / 100,
                        "max": max(prices) / 100,
                    }
                ],
                "error_details": None,
            }
            if observation.get("expected_response") != expected:
                raise ValueError(f"task {task.task_id}: catalog reference derivation differs")
            corrected["eval"][0]["expected"] = expected
        else:
            raise ValueError(f"task {task.task_id}: unsupported independent reference")

    corrected_task = type(task).model_validate(corrected)
    network_alternatives: dict[int, list[WebArenaVerifiedTask]] = {}
    for alternative in record.get("network_event_alternatives") or []:
        index = alternative.get("evaluator_index")
        operations = alternative.get("operations")
        if (
            not isinstance(index, int)
            or not 0 <= index < len(corrected["eval"])
            or corrected["eval"][index]["evaluator"] != "NetworkEventEvaluator"
            or not isinstance(operations, list)
            or not operations
            or not all(
                operation.get("path")
                in {
                    f"/eval/{index}/expected/response_status",
                    f"/eval/{index}/expected/response_content",
                }
                for operation in operations
            )
        ):
            raise ValueError(f"task {task.task_id}: invalid network event alternative")
        alternative_data = copy.deepcopy(corrected)
        for operation in operations:
            _apply_operation(alternative_data, operation)
        alternative_task = type(task).model_validate(alternative_data)
        if (
            alternative_task.eval[index].expected.url != corrected_task.eval[index].expected.url
            or alternative_task.eval[index].expected.http_method
            != corrected_task.eval[index].expected.http_method
            or alternative_task.eval[index].expected.post_data
            != corrected_task.eval[index].expected.post_data
        ):
            raise ValueError(f"task {task.task_id}: network alternative changed the mutation")
        network_alternatives.setdefault(index, []).append(alternative_task)
    assertions = tuple(
        MappingProxyType(copy.deepcopy(item)) for item in record.get("replay_assertions") or []
    )
    return CorrectedReplayContract(
        task=corrected_task,
        correction_id=str(record["correction_id"]),
        correction_source_sha256=catalog.record_sha256s[task.task_id],
        original_task_sha256=_digest(original),
        corrected_task_sha256=_digest(corrected_task.model_dump(mode="json")),
        rationale=str(record["rationale"]),
        replay_assertions=assertions,
        network_alternatives=MappingProxyType(
            {index: tuple(tasks) for index, tasks in network_alternatives.items()}
        ),
    )


def apply_registered_correction(
    task: WebArenaVerifiedTask,
    *,
    path: str | Path = DEFAULT_CORRECTIONS_PATH,
) -> CorrectedReplayContract:
    """Convenience wrapper for applying the repository correction catalog."""

    return apply_correction(task, load_corrections(path))


def corrected_task_ids(catalog: CorrectionCatalog) -> tuple[int, ...]:
    """Return the stable sorted correction scope."""

    return tuple(sorted(catalog.records))


def normalize_agent_response_for_evaluator(parsed_response: Any) -> Any:
    """Return the exact response shape accepted by the 1.2.3 evaluator."""
    if not isinstance(parsed_response, dict):
        return parsed_response
    return {
        key: ("" if value is None and key != "retrieved_data" else value)
        for key, value in parsed_response.items()
        if key in {"task_type", "status", "retrieved_data"}
    }


def evaluate_corrected_contract(
    benchmark: Any,
    contract: CorrectedReplayContract,
    *,
    agent_response: Any,
    network_trace: str | Path,
) -> TaskEvalResult:
    """Evaluate a replay-only corrected task without mutating the benchmark reader.

    Task 676 exposes an upstream evaluator bug: navigate tasks always select the
    last document navigation before considering the expected XHR URL. A replay
    assertion explicitly opts into ordinary URL/method/query filtering; only
    that network evaluator receives a non-navigate view of the same task.
    """
    task = contract.task
    parsed_response = benchmark._evaluator._parse_agent_response(agent_response)
    # webarena-verified 1.2.3 calls ``.strip()`` on scalar response fields.
    # Its documented dict format commonly serializes absent fields as JSON null,
    # so normalize only those top-level scalars while preserving retrieved_data.
    parsed_response = normalize_agent_response_for_evaluator(parsed_response)
    trace = benchmark._evaluator._parse_network_trace(network_trace)
    context = TaskEvalContext(
        task=task,
        agent_response_raw=parsed_response,
        network_trace=trace,
        config=benchmark.config,
    )
    ordinary_network_filtering = any(
        item.get("kind") == "network_event_selection"
        and item.get("policy") == "match_expected_url_method_and_query_before_recency"
        for item in contract.replay_assertions
    )
    evaluator_results = []
    for index, config in enumerate(task.eval):
        evaluator_context = context
        if ordinary_network_filtering and config.evaluator == "NetworkEventEvaluator":
            task_data = task.model_dump(mode="json")
            evaluations = task_data.get("eval") or []
            if not evaluations or not isinstance(evaluations[0].get("expected"), dict):
                raise ValueError("ordinary network filtering requires eval[0].expected")
            # webarena-verified 1.2.3 reads the task type used by
            # NetworkEventEvaluator from eval[0].expected, regardless of the
            # network evaluator's own position in the task configuration.
            evaluations[0]["expected"]["task_type"] = "MUTATE"
            filtering_task = type(task).model_validate(task_data)
            evaluator_context = context.model_copy(update={"task": filtering_task})
        result = EVALUATOR_REGISTRY[config.evaluator]().evaluate(
            context=evaluator_context,
            config=config,
        )
        if float(result.score) != 1.0:
            for alternative_task in contract.network_alternatives.get(index, ()):
                alternative_result = EVALUATOR_REGISTRY[config.evaluator]().evaluate(
                    context=context.model_copy(update={"task": alternative_task}),
                    config=alternative_task.eval[index],
                )
                if float(alternative_result.score) == 1.0:
                    result = alternative_result
                    break
        evaluator_results.append(result)
    return TaskEvalResult.create(
        task_id=task.task_id,
        intent_template_id=task.intent_template_id,
        sites=task.sites,
        task_revision=task.revision,
        evaluators_results=evaluator_results,
        data_checksum=compute_data_file_checksum(benchmark.config.test_data_file),
    )


def check_replay_assertions(
    contract: CorrectedReplayContract,
    *,
    page_url: str,
    page_text: str,
    field_values: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Check replay-only final-state assertions captured from the browser page."""
    results: list[dict[str, Any]] = []
    for assertion in contract.replay_assertions:
        kind = str(assertion.get("kind") or "")
        if kind == "network_event_selection":
            results.append({"kind": kind, "status": "pass", "detail": assertion["target"]})
            continue
        if kind == "final_dom_form_state":
            expected = dict(assertion.get("fields") or {})
            actual = {selector: field_values.get(selector) for selector in expected}
            passed = actual == expected and "/contact" in page_url
            results.append(
                {
                    "kind": kind,
                    "status": "pass" if passed else "fail",
                    "expected": expected,
                    "actual": actual,
                }
            )
            continue
        if kind == "gitlab_project_state":
            project_path = str(assertion["project_path"])
            passed = project_path in page_url and (
                "repository for this project is empty" in page_text.lower()
                or "empty repository" in page_text.lower()
            )
            results.append(
                {"kind": kind, "status": "pass" if passed else "fail", "project_path": project_path}
            )
            continue
        if kind == "magento_configurable_variation":
            required = assertion.get("required_attributes") or {}
            values = (required.get("size"), required.get("color"))
            passed = all(str(value).lower() in page_text.lower() for value in values)
            results.append(
                {
                    "kind": kind,
                    "status": "pass" if passed else "fail",
                    "required_attributes": dict(required),
                }
            )
            continue
        raise ValueError(f"unsupported replay assertion kind: {kind!r}")
    return tuple(results)
