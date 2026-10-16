from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any


# The frozen dataset retains image/video as source tracks. Public comparisons
# group those cases by the deliverable a caller must receive.
REPORT_FAMILIES = {
    'web': '静态网站正文',
    'dynamic': '动态网站正文',
    'challenge': '热门网站正文',
    'risk_control': '风控网页正文',
    'document': '文档正文',
    'web_media': '网页内嵌媒体',
    'media_file': '直接媒体文件',
}


def report_family(case: dict[str, Any]) -> str:
    track = case.get('track', 'web')
    if track in {'image', 'video'}:
        return 'media_file' if case.get('task_kind', 'body') == 'file' else 'web_media'
    return track


def execution_id(record: dict[str, Any]) -> str:
    """Stable local invocation ID, distinct from the supplier's request ID."""
    identity = record['attempt_id'] + '\0' + record['started_at']
    return 'run-' + hashlib.sha256(identity.encode()).hexdigest()[:20]


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
