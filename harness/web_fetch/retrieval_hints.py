"""One parser for the public, answer-free dynamic retrieval contract."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_WAIT_UNTIL = {"domcontentloaded", "load", "networkidle"}
_COMPLETION_KEYS = {"selector", "minimum_count", "timeout_ms", "delay_ms"}
_NATIVE_COMPLETION_KEYS = {"selector", "minimum_count"}
_EXTRACTION_KEYS = {"selector", "maximum_count", "exclude_selectors", "fallback"}
_EXTRACTION_FALLBACKS = {"body", "empty"}
_SAFE_SELECTOR = re.compile(
    r"^(?:[A-Za-z][A-Za-z0-9_-]*|[.#][A-Za-z_][A-Za-z0-9_-]*)"
    r"(?:(?:[.#][A-Za-z_][A-Za-z0-9_-]*)|"
    r"(?:\s*>\s*|\s+)(?:[A-Za-z][A-Za-z0-9_-]*|[.#][A-Za-z_][A-Za-z0-9_-]*))*$"
)
_CONTRACT_NAME = re.compile(r"^[a-z][a-z0-9-]{2,80}$")
_CONTRACT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[2]
    / "evaluation/native-retrieval-contracts.json"
)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_ANSWER_SELECTOR = re.compile(r"(?:^|[.#_-])(answer|correct-answer)(?:$|[.#_-])", re.I)


@lru_cache(maxsize=1)
def _native_contract_registry() -> dict[str, Any]:
    registry = json.loads(_CONTRACT_REGISTRY_PATH.read_text(encoding="utf-8"))
    if registry.get("schema_version") != 1 or not isinstance(registry.get("contracts"), dict):
        raise ValueError("Native retrieval contract registry is invalid")
    return registry


@lru_cache(maxsize=None)
def _verify_contract_source(path: str, expected_sha256: str) -> None:
    prefix = Path("evaluation/sources")
    authority = Path(path)
    try:
        relative = authority.relative_to(prefix)
    except ValueError as error:
        raise ValueError("Native retrieval contract source must belong to evaluation/sources") from error
    manifest = json.loads((_REPOSITORY_ROOT / "evaluation/source-inputs.json").read_text())
    if manifest.get("trees", {}).get("sources", {}).get(relative.as_posix()) != expected_sha256:
        raise ValueError("Native retrieval contract source hash is not in the source manifest")
    source = _verified_source_tree() / relative
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError("Native retrieval contract source bytes do not match the declared hash")


@lru_cache(maxsize=None)
def _verify_task_definition(identifier: str, expected_sha256: str) -> None:
    catalog = json.loads((_REPOSITORY_ROOT /
        "evaluation/dynamic-content-100-moli-webfetch/dataset-catalog.json").read_text())
    declared = next((case for case in catalog.get("cases", [])
                     if case.get("id") == identifier), None)
    if declared is None or declared.get("definition_sha256") != expected_sha256:
        raise ValueError("Native retrieval contract task definition hash is not in the dataset catalog")


def task_definition_sha256(case: Mapping[str, Any]) -> str:
    """Hash one task with the same canonical form as the frozen catalog."""
    return hashlib.sha256(
        json.dumps(
            case, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


@lru_cache(maxsize=1)
def _verified_source_tree() -> Path:
    from .reference_inputs import input_tree
    return input_tree(_REPOSITORY_ROOT / "evaluation/dataset.json", "sources")


def native_fetch_dataset(dataset: dict) -> dict:
    """Select default WebFetch execution without changing scenario acceptance.

    Keep the task family independent of completion strategy. An explicit
    version-3 native contract selects ordinary provider completion without
    selectors, delays, extraction hints or changes to acceptance rules.
    """
    result = deepcopy(dataset)
    for case in result['cases']:
        if case.get('retrieval_hints'):
            try:
                plan = parse_retrieval_hints(case.get('track', 'web'), case['retrieval_hints'], identifier=case['id'])
            except ValueError as error:
                raise ValueError('Native fetch cannot discard declared retrieval hints: ' + case['id']) from error
            if plan is not None:
                raise ValueError('Native fetch cannot discard declared retrieval hints: ' + case['id'])
        if case.get('track') == 'dynamic':
            case.setdefault('retrieval_hints', {'version': 3, 'mode': 'native'})
    result.setdefault('comparison', {})['retrieval_mode'] = 'native_default_completion'
    return result


@dataclass(frozen=True)
class RetrievalPlan:
    version: int
    wait_until: str
    selector: str
    minimum_count: int
    timeout_ms: int
    delay_ms: int
    frame_selector: str | None = None
    minimum_text_fragments: int = 0
    extraction_selector: str | None = None
    extraction_maximum_count: int = 0
    extraction_frame_selector: str | None = None
    extraction_exclude_selectors: tuple[str, ...] = ()
    extraction_fallback: str = "body"

    @property
    def wait_kind(self) -> str:
        return (
            "selector"
            if self.frame_selector is None
            and self.minimum_count == 1
            and self.minimum_text_fragments == 0
            else "script"
        )

    @property
    def wait_value(self) -> str:
        if self.wait_kind == "selector":
            return self.selector
        selector = json.dumps(self.selector, ensure_ascii=False)
        if self.frame_selector is None:
            if self.minimum_text_fragments:
                return complete_text_wait_value(self)
            return f"() => document.querySelectorAll({selector}).length >= {self.minimum_count}"
        frame = json.dumps(self.frame_selector, ensure_ascii=False)
        if self.minimum_text_fragments:
            return (
                "() => { const frame = document.querySelector("
                + frame
                + "); if (!frame || !frame.contentDocument) return false; const nodes = "
                + "[...frame.contentDocument.querySelectorAll("
                + selector
                + f")]; return nodes.length >= {self.minimum_count} && "
                + f"nodes.slice(0, {self.minimum_count}).every(node => "
                + "node.innerText.split(/\\n+/).map(value => value.trim()).filter(Boolean).length "
                + f">= {self.minimum_text_fragments}); }}"
            )
        return (
            "() => { const frame = document.querySelector("
            + frame
            + "); return !!frame && !!frame.contentDocument && "
            + "frame.contentDocument.querySelectorAll("
            + selector
            + f").length >= {self.minimum_count}; }}"
        )

    def worker_dict(self) -> dict[str, Any]:
        """Trusted normalized plan sent to repository-native subprocess workers."""
        result = {
            "version": self.version,
            "wait_until": self.wait_until,
            "selector": self.selector,
            "minimum_count": self.minimum_count,
            "timeout_ms": self.timeout_ms,
            "delay_ms": self.delay_ms,
            "frame_selector": self.frame_selector,
            "minimum_text_fragments": self.minimum_text_fragments,
            "wait_kind": self.wait_kind,
            "wait_value": self.wait_value,
        }
        if self.version == 2:
            result["extraction"] = {
                "selector": self.extraction_selector,
                "maximum_count": self.extraction_maximum_count,
                "frame_selector": self.extraction_frame_selector,
                "exclude_selectors": list(self.extraction_exclude_selectors),
                "fallback": self.extraction_fallback,
            }
        return result


@dataclass(frozen=True)
class NativeRetrievalContract:
    """Answer-free capabilities requested from each product's native interface.

    Unlike the retired guided routes, this contract never describes content to
    extract.  A provider may wait for the declared page shape or ask its own
    serializer to include child documents.  Unsupported capabilities remain
    visible in provider metadata and must not be emulated by the harness.
    """

    include_subdocuments: bool = False
    selector: str | None = None
    minimum_count: int = 0
    frame_selector: str | None = None
    minimum_text_fragments: int = 0

    def completion_plan(self, timeout_ms: int) -> RetrievalPlan | None:
        if self.selector is None:
            return None
        return RetrievalPlan(
            version=3,
            wait_until="load",
            selector=self.selector,
            minimum_count=self.minimum_count,
            timeout_ms=timeout_ms,
            delay_ms=0,
            frame_selector=self.frame_selector,
            minimum_text_fragments=self.minimum_text_fragments,
        )


def complete_text_wait_value(plan: RetrievalPlan) -> str:
    """Strengthen a count wait with answer-free record completeness.

    A rendered card can exist before its asynchronous text fields arrive.  The
    task contract may therefore require a minimum number of nonempty fields in
    every selected record; the field values themselves never enter this wait.
    """
    minimum_text_fragments = plan.minimum_text_fragments
    if minimum_text_fragments < 1:
        raise ValueError("plan.minimum_text_fragments must be positive")
    selector = json.dumps(plan.selector, ensure_ascii=False)
    return (
        "() => { const nodes = [...document.querySelectorAll("
        + selector
        + f")]; return nodes.length >= {plan.minimum_count} && "
        + f"nodes.slice(0, {plan.minimum_count}).every(node => "
        + "node.innerText.split(/\\n+/).map(value => value.trim()).filter(Boolean).length "
        + f">= {minimum_text_fragments}); }}"
    )


def _selector(identifier: str, name: str, value: Any) -> str:
    if not isinstance(value, str) or len(value) > 200 or not _SAFE_SELECTOR.fullmatch(value):
        raise ValueError(
            f"{identifier}: retrieval_hints.completion.{name} must be a simple CSS selector"
        )
    return value


def _integer(identifier: str, name: str, value: Any, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(
            f"{identifier}: retrieval_hints.completion.{name} must be an integer "
            f"from {minimum} to {maximum}"
        )
    return value


def parse_retrieval_hints(
    track: str,
    hints: Any,
    *,
    identifier: str = "<unknown>",
    current_task_definition_sha256: str | None = None,
) -> RetrievalPlan | None:
    """Validate once and produce the only plan consumed by provider adapters."""
    if hints is None:
        if track == "dynamic":
            raise ValueError(f"{identifier}: dynamic cases require retrieval_hints")
        return None
    if track != "dynamic":
        raise ValueError(f"{identifier}: retrieval_hints are only valid for dynamic cases")
    if not isinstance(hints, Mapping):
        raise ValueError(f"{identifier}: retrieval_hints must be an object")
    version = hints.get("version")
    if isinstance(version, int) and not isinstance(version, bool) and version == 3:
        if hints.get("mode") != "native":
            raise ValueError(
                f"{identifier}: native retrieval_hints must use version=3 and mode=native"
            )
        allowed = {"version", "mode", "contract"}
        if set(hints) - allowed:
            raise ValueError(
                f"{identifier}: native retrieval_hints contain unsupported fields"
            )
        _parse_native_contract(
            hints,
            identifier=identifier,
            current_task_definition_sha256=current_task_definition_sha256,
        )
        return None
    expected_keys = (
        {"version", "wait_until", "completion", "extraction"}
        if version == 2 and not isinstance(version, bool)
        else {"version", "wait_until", "completion"}
    )
    if set(hints) != expected_keys:
        raise ValueError(
            f"{identifier}: retrieval_hints version {version!r} must contain only "
            + (
                "version, wait_until, completion and extraction"
                if version == 2
                else "version, wait_until and completion"
            )
        )
    if isinstance(version, bool) or not isinstance(version, int) or version not in {1, 2}:
        raise ValueError(f"{identifier}: retrieval_hints.version must be 1 or 2")
    wait_until = hints["wait_until"]
    if not isinstance(wait_until, str) or wait_until not in _WAIT_UNTIL:
        raise ValueError(f"{identifier}: retrieval_hints.wait_until is unsupported")
    completion = hints["completion"]
    if not isinstance(completion, Mapping):
        raise ValueError(f"{identifier}: retrieval_hints.completion must be an object")
    keys = set(completion)
    optional_keys = {"frame_selector", "minimum_text_fragments"}
    if not _COMPLETION_KEYS <= keys or keys - _COMPLETION_KEYS - optional_keys:
        raise ValueError(
            f"{identifier}: retrieval_hints.completion must contain only selector, "
            "minimum_count, timeout_ms, delay_ms and optional frame_selector or "
            "minimum_text_fragments"
        )
    selector = _selector(identifier, "selector", completion["selector"])
    frame_selector = (
        _selector(identifier, "frame_selector", completion["frame_selector"])
        if "frame_selector" in completion
        else None
    )
    minimum_count = _integer(
        identifier, "minimum_count", completion["minimum_count"], minimum=1, maximum=1000
    )
    timeout_ms = _integer(
        identifier, "timeout_ms", completion["timeout_ms"], minimum=1000, maximum=60000
    )
    delay_ms = _integer(identifier, "delay_ms", completion["delay_ms"], minimum=0, maximum=10000)
    minimum_text_fragments = (
        _integer(
            identifier,
            "minimum_text_fragments",
            completion["minimum_text_fragments"],
            minimum=1,
            maximum=20,
        )
        if "minimum_text_fragments" in completion
        else 0
    )
    if delay_ms >= timeout_ms:
        raise ValueError(
            f"{identifier}: retrieval_hints.completion.delay_ms must be below timeout_ms"
        )
    extraction_selector = None
    extraction_maximum_count = 0
    extraction_frame_selector = None
    extraction_exclude_selectors: tuple[str, ...] = ()
    extraction_fallback = "body"
    if version == 2:
        extraction = hints["extraction"]
        if (
            not isinstance(extraction, Mapping)
            or not {"selector", "maximum_count", "fallback"} <= set(extraction)
            or set(extraction) - _EXTRACTION_KEYS - {"frame_selector"}
        ):
            raise ValueError(
                f"{identifier}: retrieval_hints.extraction must contain selector, "
                "maximum_count and fallback, with only optional frame_selector and "
                "exclude_selectors"
            )
        extraction_selector = _selector(identifier, "extraction.selector", extraction["selector"])
        extraction_maximum_count = _integer(
            identifier,
            "extraction.maximum_count",
            extraction["maximum_count"],
            minimum=1,
            maximum=1000,
        )
        extraction_frame_selector = (
            _selector(
                identifier,
                "extraction.frame_selector",
                extraction["frame_selector"],
            )
            if "frame_selector" in extraction
            else None
        )
        exclusions = extraction.get("exclude_selectors", [])
        if not isinstance(exclusions, list) or len(exclusions) > 20:
            raise ValueError(
                f"{identifier}: retrieval_hints.extraction.exclude_selectors must be a "
                "unique list of at most 20 selectors"
            )
        extraction_exclude_selectors = tuple(
            _selector(identifier, "extraction.exclude_selectors", value) for value in exclusions
        )
        if len(set(extraction_exclude_selectors)) != len(extraction_exclude_selectors):
            raise ValueError(
                f"{identifier}: retrieval_hints.extraction.exclude_selectors must be unique"
            )
        extraction_fallback = extraction["fallback"]
        if (
            not isinstance(extraction_fallback, str)
            or extraction_fallback not in _EXTRACTION_FALLBACKS
        ):
            raise ValueError(
                f"{identifier}: retrieval_hints.extraction.fallback must be body or empty"
            )
    return RetrievalPlan(
        version=version,
        wait_until=wait_until,
        selector=selector,
        minimum_count=minimum_count,
        timeout_ms=timeout_ms,
        delay_ms=delay_ms,
        frame_selector=frame_selector,
        minimum_text_fragments=minimum_text_fragments,
        extraction_selector=extraction_selector,
        extraction_maximum_count=extraction_maximum_count,
        extraction_frame_selector=extraction_frame_selector,
        extraction_exclude_selectors=extraction_exclude_selectors,
        extraction_fallback=extraction_fallback,
    )


def _parse_native_contract(
    hints: Mapping[str, Any], *, identifier: str,
    current_task_definition_sha256: str | None = None,
) -> NativeRetrievalContract:
    name = hints.get("contract")
    if name is None:
        return NativeRetrievalContract()
    if not isinstance(name, str) or not _CONTRACT_NAME.fullmatch(name):
        raise ValueError(f"{identifier}: retrieval_hints.contract is invalid")
    registry = _native_contract_registry()
    declared = registry["contracts"].get(name)
    if not isinstance(declared, Mapping):
        raise ValueError(f"{identifier}: unknown native retrieval contract {name!r}")
    if declared.get("case_id") != identifier:
        raise ValueError(f"{identifier}: native retrieval contract belongs to another case")
    definition_sha256 = declared.get("task_definition_sha256")
    if not isinstance(definition_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", definition_sha256):
        raise ValueError(f"{identifier}: native retrieval contract task binding is invalid")
    _verify_task_definition(identifier, definition_sha256)
    if (
        current_task_definition_sha256 is not None
        and current_task_definition_sha256 != definition_sha256
    ):
        raise ValueError(
            f"{identifier}: native retrieval contract does not match the current task definition"
        )
    authority = declared.get("source_authority")
    if (
        not isinstance(authority, Mapping)
        or not isinstance(authority.get("path"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", str(authority.get("sha256", "")))
        or authority.get("provider_output_used") is not False
    ):
        raise ValueError(f"{identifier}: native retrieval contract source binding is invalid")
    _verify_contract_source(authority["path"], authority["sha256"])
    capabilities = declared.get("capabilities")
    if not isinstance(capabilities, Mapping):
        raise ValueError(f"{identifier}: native retrieval contract capabilities are invalid")
    if set(declared) != {"case_id", "task_definition_sha256", "source_authority", "capabilities"}:
        raise ValueError(f"{identifier}: native retrieval contract contains unsupported fields")
    if set(capabilities) - {"include_subdocuments", "completion"}:
        raise ValueError(f"{identifier}: native retrieval contract capabilities are unsupported")
    include_subdocuments = capabilities.get("include_subdocuments", False)
    if not isinstance(include_subdocuments, bool):
        raise ValueError(
            f"{identifier}: retrieval_hints.include_subdocuments must be a boolean"
        )
    completion = capabilities.get("completion")
    if completion is None:
        return NativeRetrievalContract(include_subdocuments=include_subdocuments)
    if not isinstance(completion, Mapping):
        raise ValueError(f"{identifier}: retrieval_hints.completion must be an object")
    optional = {"frame_selector", "minimum_text_fragments"}
    completion_keys = set(completion)
    if (
        not _NATIVE_COMPLETION_KEYS <= completion_keys
        or completion_keys - _NATIVE_COMPLETION_KEYS - optional
    ):
        raise ValueError(
            f"{identifier}: native completion must contain only selector, minimum_count "
            "and optional frame_selector or minimum_text_fragments"
        )
    selector = _selector(identifier, "selector", completion["selector"])
    frame_selector = (
        _selector(identifier, "frame_selector", completion["frame_selector"])
        if "frame_selector" in completion
        else None
    )
    if _ANSWER_SELECTOR.search(selector) or (
        frame_selector is not None and _ANSWER_SELECTOR.search(frame_selector)
    ):
        raise ValueError(f"{identifier}: native retrieval contract selector is answer-derived")
    minimum_count = _integer(
        identifier, "minimum_count", completion["minimum_count"], minimum=1, maximum=1000
    )
    minimum_text_fragments = (
        _integer(
            identifier,
            "minimum_text_fragments",
            completion["minimum_text_fragments"],
            minimum=1,
            maximum=20,
        )
        if "minimum_text_fragments" in completion
        else 0
    )
    if frame_selector and not include_subdocuments:
        raise ValueError(
            f"{identifier}: frame completion requires include_subdocuments=true"
        )
    return NativeRetrievalContract(
        include_subdocuments=include_subdocuments,
        selector=selector,
        minimum_count=minimum_count,
        frame_selector=frame_selector,
        minimum_text_fragments=minimum_text_fragments,
    )


def native_contract_for_case(case: Any) -> NativeRetrievalContract | None:
    """Return a validated version-3 contract without changing legacy plans."""
    get = case.get if isinstance(case, dict) else lambda key, default=None: getattr(case, key, default)
    hints = get("retrieval_hints")
    if not isinstance(hints, Mapping) or hints.get("version") != 3:
        return None
    parse_retrieval_hints(
        get("track", "web"), hints, identifier=get("id", "<unknown>")
    )
    return _parse_native_contract(hints, identifier=get("id", "<unknown>"))


def native_default_case(case: Any) -> bool:
    """Stable acceptance contracts permit a URL-only provider-default route."""
    get = case.get if isinstance(case, dict) else lambda key, default=None: getattr(case, key, default)
    if get("track", "web") != "dynamic" or get("retrieval_hints"):
        return False
    contract = get("acceptance_contract")
    if not contract:
        return False
    from .stable_tasks import validate_acceptance_contract
    validate_acceptance_contract(contract)
    return True


def plan_for_case(case: Any) -> RetrievalPlan | None:
    if native_default_case(case):
        return None
    hints = getattr(case, "retrieval_hints", None)
    if not hints and getattr(case, "track", "web") != "dynamic":
        return None
    return parse_retrieval_hints(
        getattr(case, "track", "web"), hints, identifier=getattr(case, "id", "<unknown>")
    )
