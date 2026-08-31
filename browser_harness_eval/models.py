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
