from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import hashlib


def execution_id(record: dict[str, Any]) -> str:
    """Stable local invocation ID, distinct from the supplier's request ID."""
    identity = record['attempt_id'] + '\0' + record['started_at']
    return 'run-' + hashlib.sha256(identity.encode()).hexdigest()[:20]


@dataclass(frozen=True)
class EvalCase:
    id: str
    name: str
    url: str
    category: str
    expected_any: list[str]
    description: str = ""
    tags: list[str] = field(default_factory=list)
    required_facts: list[dict[str, Any]] = field(default_factory=list)
    forbidden_any: list[str] = field(default_factory=list)
    min_content_chars: int = 1
    fact_coverage_threshold: float = 1.0
    ground_truth_evidence: list[dict[str, str]] = field(default_factory=list)
    task_prompt: str = ""
    reference_answer: str = ""
    source_benchmark: dict[str, Any] = field(default_factory=dict)
    ground_truth_main_content: str = ""
    expected_resources: list[dict[str, Any]] = field(default_factory=list)
    resource_output_contract: dict[str, Any] = field(default_factory=dict)
    content_score_applicable: bool = True
    track: str = "web"
    required_patterns: list[dict[str, Any]] = field(default_factory=list)
    source_verification: dict[str, Any] = field(default_factory=dict)
    task_kind: str = "body"
    acceptance_contract: dict[str, Any] = field(default_factory=dict)
    mutable_body_policy: dict[str, Any] = field(default_factory=dict)
    body_requirements: dict[str, Any] = field(default_factory=dict)
    retrieval_hints: dict[str, Any] = field(default_factory=dict)
    source_review_url: str = ""
    evaluation_url: str = ""
    expected_final_url: str = ""
    expected_document_language: str = ""
    expected_document_language_present: bool = True
    target_language: str = ""
    target_region: str = ""
    rule_review: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderResult:
    ok: bool
    content: str
    status_code: int | None = None
    final_url: str | None = None
    title: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    files: list[dict[str, Any]] = field(default_factory=list)
    prepared_answer: tuple | None = None


@dataclass
class AttemptRecord:
    attempt_id: str
    provider: str
    provider_type: str
    case_id: str
    case_name: str
    category: str
    url: str
    run: int
    started_at: str
    elapsed_ms: float
    ok: bool
    status_code: int | None
    final_url: str | None
    title: str | None
    error_type: str | None
    error_message: str | None
    artifact_path: str
    metrics: dict[str, Any]
    provider_metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
