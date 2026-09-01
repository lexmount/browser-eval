from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


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
    annotation_notes: str = ""
    task_prompt: str = ""
    reference_answer: str = ""
    human_review_checklist: list[str] = field(default_factory=list)
    source_benchmark: dict[str, Any] = field(default_factory=dict)
    ground_truth_main_content: str = ""


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
