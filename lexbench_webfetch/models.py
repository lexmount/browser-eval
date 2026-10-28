from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from .paths import safe_identifier, validate_http_url

TASK_KINDS = ('body', 'structure', 'resource', 'file')


TRACKS = {
    'web': '普通网页正文', 'dynamic': '动态网站正文', 'challenge': '热门网站正文',
    'risk_control': '风控网页正文', 'articles': '文章内容',
    'document': '文档正文', 'image': '图片获取', 'video': '视频获取',
}


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
    diagnostic_any: list[str] = field(default_factory=list, kw_only=True)
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
    composite_acceptance: dict[str, Any] = field(default_factory=dict)
    mutable_body_policy: dict[str, Any] = field(default_factory=dict)
    body_requirements: dict[str, Any] = field(default_factory=dict)
    document_structure: dict[str, Any] = field(default_factory=dict)
    retrieval_hints: dict[str, Any] = field(default_factory=dict)
    source_review_url: str = ""
    evaluation_url: str = ""
    page_identity_required: bool = field(default=False, kw_only=True)
    expected_final_url: str = ""
    page_identity_required: bool = False
    expected_document_language: str = ""
    document_language_match: str = "primary"
    expected_document_language_present: bool = True
    target_language: str = ""
    target_region: str = ""
    rule_review: dict[str, Any] = field(default_factory=dict)


def validate_case(case: EvalCase) -> None:
    """Validate required fields, routing and identity before acquisition."""
    if not isinstance(case.page_identity_required, bool):
        raise ValueError("page_identity_required must be boolean")
    safe_identifier(case.id)
    validate_http_url(case.url)
    if not isinstance(case.name, str) or not case.name.strip():
        raise ValueError(f"Task name must be nonempty text: {case.id}")
    if not isinstance(case.category, str):
        raise ValueError(f"Task category must be text: {case.id}")
    if not isinstance(case.expected_any, list) or any(
        not isinstance(marker, str) or not marker.strip() for marker in case.expected_any
    ):
        raise ValueError(f"expected_any must be a list of nonempty text markers: {case.id}")
    if case.task_kind not in TASK_KINDS:
        raise ValueError(f"Unknown task kind for {case.id}: {case.task_kind}")
    if not isinstance(case.track, str) or case.track not in TRACKS:
        raise ValueError(f"Unknown task track for {case.id}: {case.track}")

