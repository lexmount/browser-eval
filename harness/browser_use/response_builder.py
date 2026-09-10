"""Build evaluator responses only from values observed during trajectory replay."""

from __future__ import annotations

import json
import re
from typing import Any

from .suite import BrowserTask


def _resolve(value: Any, outputs: dict[str, str]) -> Any:
    if isinstance(value, list):
        return [_resolve(item, outputs) for item in value]
    if isinstance(value, dict) and "$output" in value:
        allowed = {"$output", "regex", "group", "case", "coerce"}
        if not set(value) <= allowed:
            raise ValueError("output transform contains unsupported keys")
        name = str(value["$output"])
        if name not in outputs:
            raise ValueError(f"response builder output is missing: {name}")
        resolved: str = outputs[name]
        pattern = value.get("regex")
        if pattern is not None:
            if not isinstance(pattern, str) or not pattern:
                raise ValueError("output regex must be a non-empty string")
            match = re.search(pattern, resolved)
            if match is None:
                raise ValueError(f"response builder regex did not match output: {name}")
            group = int(value.get("group", 1))
            try:
                resolved = match.group(group)
            except IndexError as exc:
                raise ValueError("response builder regex group does not exist") from exc
        case = str(value.get("case") or "preserve")
        if case == "lower":
            resolved = resolved.lower()
        elif case == "upper":
            resolved = resolved.upper()
        elif case != "preserve":
            raise ValueError(f"unsupported output case transform: {case!r}")
        coerce = str(value.get("coerce") or "string")
        if coerce == "string":
            return resolved
        if coerce == "integer":
            return int(resolved)
        if coerce == "number":
            number = float(resolved)
            return int(number) if number.is_integer() else number
        if coerce == "closed_boolean":
            normalized = resolved.strip().lower()
            if normalized == "closed":
                return True
            if normalized in {"open", "opened"}:
                return False
            raise ValueError("closed_boolean requires observed Open, Opened, or Closed text")
        raise ValueError(f"unsupported output coercion: {coerce!r}")
    if isinstance(value, dict):
        return {key: _resolve(item, outputs) for key, item in value.items()}
    raise ValueError("response builder may contain only output references, lists, and objects")


def build_agent_response(task: BrowserTask, outputs: dict[str, str]) -> dict[str, Any] | None:
    if (task.evaluator or {}).get("kind") != "webarena_verified":
        return None
    config = task.ground_truth.get("response_builder")
    if not isinstance(config, dict):
        return None
    task_type = str(config.get("task_type") or "")
    if task_type not in {"retrieve", "navigate", "mutate"}:
        raise ValueError("response builder requires retrieve, navigate, or mutate task_type")
    kind = config.get("kind")
    response_status = "SUCCESS"
    if kind == "null_after_verified_path":
        if task_type == "retrieve":
            raise ValueError("retrieve responses must be built from observed page output")
        retrieved_data = None
    elif kind == "observed_shape":
        retrieved_data = _resolve(config.get("shape"), outputs)
    elif kind == "observed_json":
        output_name = str(config.get("output") or "")
        if output_name not in outputs:
            raise ValueError(f"response builder output is missing: {output_name}")
        retrieved_data = json.loads(outputs[output_name])
    elif kind == "observed_json_concat":
        output_names = config.get("outputs")
        if not isinstance(output_names, list) or not output_names:
            raise ValueError("observed_json_concat requires a non-empty outputs list")
        retrieved_data = []
        for raw_name in output_names:
            output_name = str(raw_name)
            if output_name not in outputs:
                raise ValueError(f"response builder output is missing: {output_name}")
            value = json.loads(outputs[output_name])
            if not isinstance(value, list):
                raise ValueError("observed_json_concat outputs must contain JSON arrays")
            retrieved_data.extend(value)
    elif kind == "observed_json_zip":
        fields = config.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise ValueError("observed_json_zip requires a non-empty fields object")
        columns: dict[str, list[Any]] = {}
        for field, raw_name in fields.items():
            output_name = str(raw_name)
            if output_name not in outputs:
                raise ValueError(f"response builder output is missing: {output_name}")
            value = json.loads(outputs[output_name])
            if not isinstance(value, list):
                raise ValueError("observed_json_zip outputs must contain JSON arrays")
            columns[str(field)] = value
        lengths = {len(value) for value in columns.values()}
        if len(lengths) != 1:
            raise ValueError("observed_json_zip columns must have equal lengths")
        retrieved_data = [
            {field: values[index] for field, values in columns.items()}
            for index in range(next(iter(lengths)))
        ]
    elif kind == "observed_review_authors_matching":
        raw_output_names = config.get("outputs")
        if raw_output_names is None:
            raw_output_names = [config.get("output")]
        if not isinstance(raw_output_names, list) or not raw_output_names:
            raise ValueError("observed_review_authors_matching requires outputs")
        output_names = [str(value or "") for value in raw_output_names]
        if any(name not in outputs for name in output_names):
            missing = next(name for name in output_names if name not in outputs)
            raise ValueError(f"response builder output is missing: {missing}")
        match_regex = config.get("match_regex")
        author_regex = config.get("author_regex")
        if (
            not isinstance(match_regex, str)
            or not match_regex
            or not isinstance(author_regex, str)
            or not author_regex
        ):
            raise ValueError(
                "observed_review_authors_matching requires a JSON array and two regexes"
            )
        authors: list[str] = []
        for output_name in output_names:
            reviews = json.loads(outputs[output_name])
            if not isinstance(reviews, list):
                raise ValueError(
                    "observed_review_authors_matching outputs must contain JSON arrays"
                )
            for review in reviews:
                text = str(review)
                if re.search(match_regex, text, flags=re.IGNORECASE) is None:
                    continue
                author = re.search(author_regex, text, flags=re.IGNORECASE)
                if author is None or author.lastindex is None:
                    raise ValueError("matching review does not expose an author capture group")
                value = author.group(1).strip()
                if value and value not in authors:
                    authors.append(value)
        if not authors:
            raise ValueError("no observed reviews matched the requested evidence regex")
        retrieved_data = authors
    elif kind == "observed_currency_range":
        output_names = config.get("outputs")
        if not isinstance(output_names, list) or not output_names:
            raise ValueError("observed_currency_range requires a non-empty outputs list")
        amounts: list[float] = []
        for raw_name in output_names:
            output_name = str(raw_name)
            if output_name not in outputs:
                raise ValueError(f"response builder output is missing: {output_name}")
            value = json.loads(outputs[output_name])
            if not isinstance(value, list):
                raise ValueError("observed_currency_range outputs must contain JSON arrays")
            for item in value:
                match = re.search(r"-?[0-9][0-9,]*(?:\.[0-9]+)?", str(item))
                if match is None:
                    raise ValueError(f"currency value is not parseable in output: {output_name}")
                amounts.append(float(match.group(0).replace(",", "")))
        if not amounts:
            raise ValueError("observed_currency_range requires at least one observed amount")
        minimum = min(amounts)
        maximum = max(amounts)
        retrieved_data = [
            {
                "min": int(minimum) if minimum.is_integer() else minimum,
                "max": int(maximum) if maximum.is_integer() else maximum,
            }
        ]
    elif kind == "observed_not_found":
        output_name = str(config.get("output") or "")
        if output_name not in outputs:
            raise ValueError(f"response builder output is missing: {output_name}")
        evidence_regex = config.get("evidence_regex")
        if not isinstance(evidence_regex, str) or not evidence_regex:
            raise ValueError("observed_not_found requires a non-empty evidence_regex")
        if re.search(evidence_regex, outputs[output_name], flags=re.IGNORECASE) is None:
            raise ValueError("observed_not_found evidence regex did not match observed output")
        retrieved_data = None
        response_status = "NOT_FOUND_ERROR"
    else:
        raise ValueError(f"unsupported response builder kind: {kind!r}")
    return {
        "task_type": task_type,
        "status": response_status,
        "retrieved_data": retrieved_data,
    }
