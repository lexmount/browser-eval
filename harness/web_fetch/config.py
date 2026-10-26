from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from lexbench_webfetch.models import EvalCase, validate_case

from harness.web_fetch.reference_inputs import read_dataset_bytes
from harness.web_fetch.retrieval_hints import (
    native_default_case,
    parse_retrieval_hints,
    task_definition_sha256,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FAILURE_RETRIES = 1
DEFAULT_CALL_TIMEOUT_SECONDS = 45


def nonnegative_int(value: str) -> int:
    """Parse a nonnegative command-line count."""
    number = int(value)
    if number < 0:
        raise ValueError('must be a nonnegative integer')
    return number


def add_failure_retry_argument(parser) -> None:
    parser.add_argument(
        '--max-failure-retries', type=nonnegative_int, default=DEFAULT_FAILURE_RETRIES,
        help=f'Retry a failed task up to N times (default: {DEFAULT_FAILURE_RETRIES}; 0 disables retries); use the final call',
    )


def _validate_retrieval_hints(item: dict[str, Any]) -> None:
    if native_default_case(item):
        return
    parse_retrieval_hints(
        str(item.get("track", "web")),
        item.get("retrieval_hints") if "retrieval_hints" in item else None,
        identifier=str(item.get("id", "<unknown>")),
        current_task_definition_sha256=task_definition_sha256(item),
    )


def _validate_ground_truth_evidence(item: dict[str, Any]) -> None:
    """Reject copied provenance links for references captured from the case URL."""
    case_host = (urlsplit(str(item.get("url", ""))).hostname or "").removeprefix("www.")
    for evidence in item.get("ground_truth_evidence", []):
        if evidence.get("supports") != "本版独立获取的完整参考正文":
            continue
        source_host = (urlsplit(str(evidence.get("source", ""))).hostname or "").removeprefix("www.")
        if not case_host or source_host != case_host:
            raise ValueError(
                f"{item.get('id', '<unknown>')} full-reference evidence must use the case URL host"
            )


def _evaluation_credential(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Z0-9_]+_(?:API_KEY|API_TOKEN|ACCOUNT_ID)", name))


def load_runtime_env(path: Path | None = None) -> None:
    """Use the repository .env by default; explicit missing files are errors."""
    selected = path if path is not None else ROOT / ".env"
    if path is not None and not selected.exists():
        raise FileNotFoundError(selected)
    # Credentials may never leak into a run from the parent shell.
    for key in list(os.environ):
        if _evaluation_credential(key):
            os.environ.pop(key)
    if path is not None or selected.exists():
        load_env_file(selected, override_credentials=True,
                      credentials_allowed=selected.resolve() == (ROOT / ".env").resolve())
    if path is not None:
        os.environ.setdefault("BROWSER_EVAL_REPORT_LANGUAGE", "en")


def load_env_file(path: Path, *, override_credentials: bool = False, credentials_allowed: bool = True) -> None:
    """Load simple dotenv assignments without evaluating shell code."""
    for raw_line in path.expanduser().read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if _evaluation_credential(key) and not credentials_allowed:
            raise ValueError("Evaluation credentials must come from the repository .env")
        if override_credentials and _evaluation_credential(key):
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?}", value)
        if match:
            return os.getenv(match.group(1)) or match.group(2) or ""
        return value
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env(item) for key, item in value.items()}
    return value


def load_json(path: Path) -> Any:
    return json.loads(read_dataset_bytes(path))


def load_dataset(path: Path) -> tuple[dict[str, Any], list[EvalCase]]:
    payload = load_json(path)
    if not isinstance(payload, dict) or payload.get("tool") != "web_fetch":
        raise ValueError("dataset.tool must be 'web_fetch'")
    raw_cases = payload.get("cases", [])
    if not isinstance(raw_cases, list) or any(not isinstance(item, dict) for item in raw_cases):
        raise ValueError("dataset.cases must be a list of task objects")
    for item in raw_cases:
        _validate_retrieval_hints(item)
        _validate_ground_truth_evidence(item)
    try:
        cases = [EvalCase(**item) for item in raw_cases]
    except TypeError as error:
        raise ValueError(f"Invalid task definition: {error}") from error
    for case in cases:
        validate_case(case)
    if not cases:
        raise ValueError("dataset must contain at least one case")
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("dataset case ids must be unique")
    return payload, cases


def derive_comparison(dataset: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """Change execution controls without discarding inherited evidence contracts."""
    return {**deepcopy(dataset.get('comparison', {})), **deepcopy(changes)}


def select_diagnostic_scope(
    dataset: dict[str, Any], cases: list[EvalCase], providers: list[str], *,
    case_ids: str = "", task_kind: str | None = None,
) -> tuple[dict[str, Any], list[EvalCase]]:
    """Declare the generic runner's complete matrix before any provider call."""
    if not providers or len(providers) != len(set(providers)):
        raise ValueError("Select at least one provider; provider names must be unique")
    requested = [item.strip() for item in case_ids.split(",") if item.strip()]
    if case_ids and (not requested or len(requested) != len(set(requested))):
        raise ValueError("Case IDs must be nonempty and unique")
    unknown = set(requested) - {case.id for case in cases}
    if unknown:
        raise ValueError(f"Unknown cases: {', '.join(sorted(unknown))}")
    selected = [case for case in cases if (not requested or case.id in requested)
                and (task_kind is None or case.task_kind == task_kind)]
    if not selected:
        raise ValueError("Task selection is empty")
    selected_ids = {case.id for case in selected}
    scoped = deepcopy(dataset)
    scoped['cases'] = [item for item in scoped['cases'] if item['id'] in selected_ids]
    scoped['coverage_plan'] = {track: list(providers) for track in
                               sorted({case.track for case in selected})}
    # A new diagnostic run cannot inherit a historical cohort's certification.
    scoped['comparison'] = derive_comparison(dataset, {'kind': 'web_fetch_diagnostic',
                            'evidence_state': 'diagnostic',
                            'publication_state': 'diagnostic_not_for_release'})
    scoped['comparison'].pop('require_independent_result_review', None)
    return scoped, selected


def load_provider_settings(path: Path) -> dict[str, Any]:
    payload = expand_env(load_json(path))
    providers = payload.get("providers", {})
    if not isinstance(providers, dict) or not providers:
        raise ValueError("provider config must define providers")
    return payload


def load_provider_config(path: Path) -> dict[str, dict[str, Any]]:
    return load_provider_settings(path)["providers"]
