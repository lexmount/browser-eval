from __future__ import annotations

from ..models import EvalCase, ProviderResult
from .base import Provider
from .http_client import inner_timeout_budget, request


class LexmountProvider(Provider):
    def validate(self) -> list[str]:
        missing = []
        if not self.config.get("api_key"):
            missing.append("api_key")
        if not self.config.get("project_id"):
            missing.append("project_id")
        return missing

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        base_url = str(self.config.get("base_url", "https://api.lexmount.com")).rstrip("/")
        extract: dict[str, object] = {"url": case.url}
        if self.config.get("engine"):
            extract["engine"] = self.config["engine"]
        response = request(
            f"{base_url}/v1/extract",
            method="POST",
            timeout=inner_timeout_budget(timeout),
            max_bytes=int(self.config.get("max_bytes", 20_000_000)),
            headers={
                "X-API-Key": str(self.config.get("api_key", "")),
                "X-Project-Id": str(self.config.get("project_id", "")),
            },
            json_body={"extract": extract},
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        result = payload.get("result") or {}
        metadata = payload.get("metadata") or {}
        ok = 200 <= response.status < 300 and bool(result)
        content = result.get("main_text") or response.text()
        error = payload.get("error") or {}
        message = error.get("message") or payload.get("message")
        return ProviderResult(
            ok=ok,
            content=content,
            status_code=response.status,
            final_url=result.get("final_url") or result.get("url"),
            title=result.get("title"),
            error_type=None if ok else ("authentication" if response.status == 401 else "api_error"),
            error_message=None if ok else (message or f"HTTP {response.status}"),
            metadata={
                **({"raw_response": response.text()} if case.task_kind == "file" else {}),
                "request_id": payload.get("request_id"),
                "engine": result.get("engine"),
                "upstream_status": result.get("status_code") or error.get("upstream_status_code"),
                "fetch_error_code": error.get("code"),
                "fetch_error_stage": error.get("stage"),
                "server_elapsed_ms": metadata.get("server_elapsed_ms"),
                "dom_id": result.get("dom_id") or metadata.get("dom_id"),
                "links_count": len(result.get("links") or []),
                "images_count": len(result.get("images") or []),
                "links": result.get("links") or [],
                "images": result.get("images") or [],
            },
        )
