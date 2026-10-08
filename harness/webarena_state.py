"""Opt-in, hash-bound final form-state evaluation shared by both browser tracks.

No synthetic request is emitted or inserted into a HAR. An explicitly selected
synthetic-state evaluator is replaced; all other official evaluators, including
the forbidden-submit check, still run unchanged. Existing profiles do not opt in.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from webarena_verified.core.evaluation.evaluators import EVALUATOR_REGISTRY
from webarena_verified.core.evaluation.value_comparator import ValueComparator
from webarena_verified.core.evaluation.value_normalizer import ValueNormalizer
from webarena_verified.core.utils.checksum import (
    compute_data_file_checksum,
    compute_evaluator_checksum,
)
from webarena_verified.types.eval import (
    EvalAssertion,
    EvalStatus,
    EvaluatorResult,
    TaskEvalContext,
    TaskEvalResult,
)

PROTOCOL = "webarena_observed_form_state/1"


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def rule_identity() -> dict[str, str]:
    return {
        "protocol": PROTOCOL,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "official_package_version": version("webarena-verified"),
        "official_evaluator_checksum": compute_evaluator_checksum(),
    }


@dataclass(frozen=True)
class BoundFormContract:
    definition: dict[str, Any]
    definition_sha256: str
    catalog_sha256: str
    rules: dict[str, str]
    official_data_checksum: str
    page_url: str


def _validate_definition(task: Any, definition: dict[str, Any]) -> None:
    required = {
        "task_id",
        "official_task_sha256",
        "replace_evaluator_index",
        "replace_evaluator_sha256",
        "forbidden_submit_evaluator_index",
        "page_url",
        "form_selector",
        "field_selectors",
        "allow_trailing_slash",
    }
    if (
        not required <= set(definition)
        or set(definition) - required - {"supersedes_replay_correction"}
        or definition["task_id"] != task.task_id
        or definition["official_task_sha256"] != digest(task.model_dump(mode="json"))
    ):
        raise ValueError("form contract does not bind the exact official task")
    index, forbidden_index = (
        definition["replace_evaluator_index"],
        definition["forbidden_submit_evaluator_index"],
    )
    if (
        type(index) is not int
        or type(forbidden_index) is not int
        or index == forbidden_index
        or not 0 <= index < len(task.eval)
        or not 0 <= forbidden_index < len(task.eval)
    ):
        raise ValueError("invalid form evaluator positions")
    selected, forbidden = task.eval[index], task.eval[forbidden_index]
    if (
        selected.evaluator != "NetworkEventEvaluator"
        or digest(selected.model_dump(mode="json")) != definition["replace_evaluator_sha256"]
    ):
        raise ValueError("synthetic state evaluator binding differs")
    if (
        selected.expected.response_status != -1
        or selected.expected.http_method != "POST"
        or selected.expected.url != "^http://.*/dummy_bin$"
        or selected.should_not_exist
        or not selected.expected.post_data
        or not isinstance(selected.expected.post_data.get("form_id"), str)
    ):
        raise ValueError("replacement requires an explicitly bound synthetic form-state rule")
    if (
        forbidden.evaluator != "NetworkEventEvaluator"
        or not forbidden.should_not_exist
        or forbidden.expected.http_method != "POST"
    ):
        raise ValueError("form contract must retain official forbidden submission check")
    fields = definition["field_selectors"]
    if (
        not isinstance(fields, dict)
        or set(fields) != set(selected.expected.post_data) - {"form_id"}
        or not fields
        or any(not isinstance(v, str) or not v for v in fields.values())
        or len(set(fields.values())) != len(fields)
    ):
        raise ValueError("form fields must map every official expected value exactly once")
    if (
        any(
            not isinstance(definition[k], str) or not definition[k]
            for k in ("page_url", "form_selector")
        )
        or type(definition["allow_trailing_slash"]) is not bool
    ):
        raise ValueError("invalid form capture target")


def load_contracts(
    binding: dict[str, Any] | None, root: Path, benchmark: Any
) -> dict[int, BoundFormContract]:
    """No opt-in means no state correction; changed rules fail before a run."""
    if binding is None:
        return {}
    if (
        set(binding) != {"path", "sha256", "rules", "official_data_checksum", "site_urls"}
        or binding["rules"] != rule_identity()
    ):
        raise ValueError("form-state profile must explicitly bind current rule identity")
    if not isinstance(binding["site_urls"], dict):
        raise ValueError("invalid frozen profile site bindings")
    checksum = compute_data_file_checksum(benchmark.config.test_data_file)
    if binding["official_data_checksum"] != checksum:
        raise ValueError("form-state profile official dataset differs")
    raw = root / binding["path"]
    if (
        any(p.is_symlink() for p in (raw, *raw.parents))
        or not raw.resolve().is_relative_to(root.resolve())
        or not raw.is_file()
    ):
        raise ValueError("unsafe form contract catalog")
    data = raw.read_bytes()
    if hashlib.sha256(data).hexdigest() != binding["sha256"]:
        raise ValueError("form contract catalog hash differs")
    payload = json.loads(data)
    if set(payload) != {"schema", "contracts"} or payload["schema"] != PROTOCOL:
        raise ValueError("invalid form contract catalog")
    contracts = {}
    for definition in payload["contracts"]:
        task_id = definition["task_id"]
        if type(task_id) is not int or task_id in contracts:
            raise ValueError("duplicate or invalid form task")
        task = benchmark.get_task(task_id)
        _validate_definition(task, definition)
        page_url = benchmark.config.render_url(definition["page_url"], task.sites)
        if not isinstance(page_url, str) or binding["site_urls"].get(str(task_id)) != page_url:
            raise ValueError("runtime form origin must match the frozen profile site binding")
        contracts[task_id] = BoundFormContract(
            definition,
            digest(definition),
            binding["sha256"],
            dict(binding["rules"]),
            checksum,
            page_url,
        )
    if set(binding["site_urls"]) != {str(task_id) for task_id in contracts}:
        raise ValueError("frozen profile site bindings differ from form contract scope")
    return contracts


CAPTURE_SCRIPT = """target => {
  const visible = node => {
    if (!node || !node.isConnected ||
        ![...node.getClientRects()].some(r => r.width > 0 && r.height > 0)) return false;
    for (let parent = node; parent; parent = parent.parentElement) {
      const style = getComputedStyle(parent);
      if (style.display === 'none' || ['hidden','collapse'].includes(style.visibility) ||
          Number(style.opacity) === 0 || style.contentVisibility === 'hidden') return false;
    }
    return true;
  };
  const forms = [...document.querySelectorAll(target.form_selector)];
  const form = forms.length === 1 ? forms[0] : null;
  const fields = {};
  for (const [name, selector] of Object.entries(target.field_selectors)) {
    const matches = form?.tagName === 'FORM'
      ? [...form.elements].filter(node => node.form === form && node.matches(selector)) : [];
    const node = matches.length === 1 ? matches[0] : null;
    fields[name] = {count: matches.length, associated: !!node && node.form === form,
      visible: visible(node),
      connected: !!node && node.isConnected, complete: !!node && typeof node.value === 'string',
      value: node && typeof node.value === 'string' ? node.value : null};
  }
  return {page_url: location.href, form_count: forms.length, form_id: form?.id ?? null,
    is_form: form?.tagName === 'FORM', connected: !!form && form.isConnected,
    visible: visible(form), fields};
}"""


def capture_form(
    page: Any, contract: BoundFormContract, *, run_id: str, attempt_id: str, engine: str
) -> dict[str, Any]:
    if (
        not run_id
        or not engine
        or not attempt_id
        or digest(contract.definition) != contract.definition_sha256
        or contract.rules != rule_identity()
    ):
        raise ValueError("invalid current-run form capture binding")
    result = {
        "schema": PROTOCOL,
        "task_id": contract.definition["task_id"],
        "contract_sha256": contract.definition_sha256,
        "runtime_site_sha256": digest(contract.page_url),
        "run_id": run_id,
        "engine": engine,
        "attempt_id": attempt_id,
        "capture_status": "error",
        "state": None,
    }
    try:
        result["state"] = page.evaluate(CAPTURE_SCRIPT, contract.definition)
        result["capture_status"] = "complete"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _same_url(actual: str, expected: str, trailing_slash: bool) -> bool:
    left, right = urlsplit(actual), urlsplit(expected)
    if any(
        u.username or u.password or u.scheme not in {"http", "https"} or not u.hostname
        for u in (left, right)
    ):
        return False

    def origin(u):
        return (
            u.scheme,
            u.hostname,
            u.port if u.port is not None else (443 if u.scheme == "https" else 80),
        )

    def path(u):
        return u.path.rstrip("/") if trailing_slash else u.path

    return origin(left) == origin(right) and path(left) == path(right) and left.query == right.query


def evaluate_form_state(
    context: TaskEvalContext,
    contract: BoundFormContract,
    observation: dict[str, Any] | None,
    *,
    run_id: str,
    attempt_id: str,
    engine: str,
) -> EvaluatorResult:
    """Compare complete DOM values using the installed official value semantics."""
    definition = contract.definition
    selected = context.task.eval[definition["replace_evaluator_index"]]
    errors = []
    actual = None
    try:
        if (
            not run_id
            or not engine
            or not attempt_id
            or not isinstance(observation, dict)
            or any(
                observation.get(key) != expected
                for key, expected in {
                    "schema": PROTOCOL,
                    "task_id": context.task.task_id,
                    "contract_sha256": contract.definition_sha256,
                    "runtime_site_sha256": digest(contract.page_url),
                    "run_id": run_id,
                    "engine": engine,
                    "attempt_id": attempt_id,
                    "capture_status": "complete",
                }.items()
            )
        ):
            raise ValueError("missing, incomplete or cross-run form observation")
        state = observation["state"]
        expected_url = contract.page_url
        if not isinstance(expected_url, str) or not _same_url(
            state["page_url"], expected_url, definition["allow_trailing_slash"]
        ):
            raise ValueError("form observation has wrong origin or page path")
        if (
            type(state["form_count"]) is not int
            or state["form_count"] != 1
            or any(state.get(k) is not True for k in ("is_form", "connected", "visible"))
            or state["form_id"] != selected.expected.post_data["form_id"]
        ):
            raise ValueError("expected unique connected visible form was not observed")
        if set(state["fields"]) != set(definition["field_selectors"]):
            raise ValueError("incomplete form fields")
        actual = {"form_id": state["form_id"]}
        for name, field in state["fields"].items():
            if (
                type(field.get("count")) is not int
                or field["count"] != 1
                or any(
                    field.get(k) is not True
                    for k in ("associated", "connected", "complete", "visible")
                )
                or not isinstance(field.get("value"), str)
            ):
                raise ValueError("missing, ambiguous, detached or truncated form field")
            actual[name] = field["value"]
        normalizer, comparator = ValueNormalizer(), ValueComparator()
        schema = selected.post_data_schema or {}
        kwargs = {
            "derender_url_fct": lambda value: context.config.derender_url(
                value, sites=context.task.sites
            )
        }
        expected_normalized = normalizer.normalize(
            selected.expected.post_data, schema, strict=True, **kwargs
        )
        actual_normalized = normalizer.normalize(actual, schema, strict=False, **kwargs)
        errors.extend(
            comparator.compare(
                actual=actual_normalized,
                expected=expected_normalized,
                value_name="form_state",
                ignore_extra_keys=False,
            )
        )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append(
            EvalAssertion(
                assertion_name="observable_form_state",
                status=EvalStatus.FAILURE,
                assertion_msgs=(str(exc),),
            )
        )
    return EvaluatorResult.create(
        evaluator_name="ObservableFormStateEvaluator",
        actual=actual,
        expected=dict(selected.expected.post_data),
        assertions=errors,
    )


def evaluate_task(
    benchmark: Any,
    contract: BoundFormContract,
    *,
    agent_response: Any,
    network_trace: Any,
    observation: dict[str, Any] | None,
    run_id: str,
    attempt_id: str,
    engine: str,
) -> dict[str, Any]:
    """New opt-in scoring only; official task and supplied HAR remain immutable."""
    if (
        contract.rules != rule_identity()
        or digest(contract.definition) != contract.definition_sha256
        or contract.official_data_checksum
        != compute_data_file_checksum(benchmark.config.test_data_file)
    ):
        raise ValueError("form evaluation rules or frozen inputs changed")
    task = benchmark.get_task(contract.definition["task_id"])
    _validate_definition(task, contract.definition)
    if (
        benchmark.config.render_url(contract.definition["page_url"], task.sites)
        != contract.page_url
    ):
        raise ValueError("runtime form origin changed after contract binding")
    if not observation or observation.get("capture_status") != "complete":
        return {
            "task_id": task.task_id,
            "status": "unsupported",
            "score": None,
            "evaluators_results": [],
            "error_msg": "final form observation unavailable",
        }
    response = benchmark._evaluator._parse_agent_response(agent_response)
    context = TaskEvalContext(
        task=task,
        agent_response_raw=response,
        network_trace=benchmark._evaluator._parse_network_trace(network_trace),
        config=benchmark.config,
    )
    results = []
    for index, config in enumerate(task.eval):
        if index == contract.definition["replace_evaluator_index"]:
            results.append(
                evaluate_form_state(
                    context,
                    contract,
                    observation,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    engine=engine,
                )
            )
        else:
            results.append(
                EVALUATOR_REGISTRY[config.evaluator]().evaluate(context=context, config=config)
            )
    result = TaskEvalResult.create(
        task_id=task.task_id,
        intent_template_id=task.intent_template_id,
        sites=task.sites,
        task_revision=task.revision,
        evaluators_results=results,
        data_checksum=contract.official_data_checksum,
    ).model_dump(mode="json")
    result["observed_state_protocol"] = {
        "rules": contract.rules,
        "catalog_sha256": contract.catalog_sha256,
        "runtime_site_sha256": digest(contract.page_url),
        "contract_sha256": contract.definition_sha256,
        "observation_sha256": digest(observation),
        "run_id": run_id,
        "engine": engine,
        "attempt_id": attempt_id,
    }
    return result


def load_profile_contracts(
    binding: dict[str, Any] | None, root: Path
) -> dict[int, BoundFormContract]:
    """Resolve only the hostname already declared in a frozen opt-in profile."""
    if binding is None:
        return {}
    from harness.environments.webarena.runtime import (
        configure_benchmark,
    )

    hosts = {urlsplit(url).hostname for url in binding.get("site_urls", {}).values()}
    if len(hosts) != 1 or None in hosts:
        raise ValueError("state profile must declare one exact site access hostname")
    return load_contracts(binding, root, configure_benchmark(hostname=hosts.pop()))


def require_supersession(
    contract: BoundFormContract, correction_id: str, correction_sha256: str
) -> None:
    if contract.definition.get("supersedes_replay_correction") != {
        "correction_id": correction_id,
        "record_sha256": correction_sha256,
    }:
        raise ValueError("state contract must explicitly supersede the legacy replay correction")


def verify_attempt(attempt: dict[str, Any], contract: BoundFormContract) -> None:
    """Recompute the score from hash-bound state, actual response and full HAR."""
    from harness.environments.webarena.runtime import (
        configure_benchmark,
    )

    if attempt.get("contract_correction_id"):
        raise ValueError("shared observed-state cannot also apply legacy replay correction")
    if attempt.get("status") == "pass" and not (
        attempt.get("process_status") == attempt.get("evaluator_status") == "pass"
    ):
        raise ValueError("claimed state pass contradicts process or evaluator status")
    loaded, paths = {}, {}
    for name in ("final_state", "actual_response", "evaluation", "network_har"):
        ref = (attempt.get("evidence") or {}).get(name) or {}
        path = Path(str(ref.get("path") or ""))
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise ValueError("missing or unsafe observed-state evidence")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != ref.get("sha256"):
            raise ValueError("observed-state evidence hash mismatch")
        paths[name] = path
        if name != "network_har":
            loaded[name] = json.loads(data)
    if (
        attempt.get("task_id") != contract.definition["task_id"]
        or attempt.get("state_contract_sha256") != contract.definition_sha256
    ):
        raise ValueError("attempt differs from current observed-state contract")
    benchmark = configure_benchmark(hostname=urlsplit(contract.page_url).hostname)
    current = evaluate_task(
        benchmark,
        contract,
        agent_response=loaded["actual_response"],
        network_trace=paths["network_har"],
        observation=loaded["final_state"],
        run_id=attempt.get("run_id", ""),
        attempt_id=attempt.get("attempt_id", ""),
        engine=attempt.get("engine", ""),
    )
    if current.get("status") == "unsupported" or digest(current) != digest(loaded["evaluation"]):
        raise ValueError("observed-state evaluation cannot be reproduced")
    if attempt.get("score") is not None and attempt["score"] != current["score"]:
        raise ValueError("observed-state score differs from evaluated evidence")
    if "evaluator_status" in attempt and attempt["evaluator_status"] != (
        "pass" if current["score"] == 1 else "fail"
    ):
        raise ValueError("observed-state verdict differs from evaluated evidence")


def validate_attempt_identities(attempts: list[dict[str, Any]], task_ids: set[int]) -> None:
    if not task_ids:
        return
    seen = {}
    for row in attempts:
        if "task_id" not in row:
            raise ValueError("observed-state attempt omitted task_id")
        if int(row["task_id"]) not in task_ids:
            continue
        key = (row.get("run_id"), row.get("attempt_id"))
        owner = (row.get("engine"), int(row["task_id"]))
        if not all(key) or not owner[0] or (key in seen and seen[key] != owner):
            raise ValueError("observed-state attempt identity reused across browser or task")
        seen[key] = owner


def capture_service_form(
    service: Any, contract: BoundFormContract, **identity: str
) -> dict[str, Any]:
    """Dispatch failure is unavailable evidence; never repeat the trajectory action."""
    try:
        return service._submit(lambda: capture_form(service._page, contract, **identity))
    except Exception as exc:
        return {
            "schema": PROTOCOL,
            "task_id": contract.definition["task_id"],
            "contract_sha256": contract.definition_sha256,
            "runtime_site_sha256": digest(contract.page_url),
            **identity,
            "capture_status": "error",
            "state": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
