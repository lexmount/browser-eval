from __future__ import annotations

from lexbench_webfetch.models import EvalCase

from harness.web_fetch.models import ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.providers.http_client import inner_timeout_budget, request
from harness.web_fetch.retrieval_hints import plan_for_case

_ENGINES = {
    "auto",
    "http",
    "chrome",
    "chrome_cdp",
    "lightmount_lite",
    "lightmount_dcl",
    "lightmount_domstable",
}


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
        route = "v1_extract"
        wait_metadata = None
        configured_engine = self.config.get("engine")
        if case.track == "dynamic":
            try:
                plan = plan_for_case(case)
                assert plan is not None
                http_timeout = inner_timeout_budget(timeout)
            except (AssertionError, KeyError, TypeError, ValueError):
                return ProviderResult(
                    False,
                    "",
                    error_type="configuration",
                    error_message="invalid dynamic retrieval_hints v1",
                )
            engine = str(
                self.config.get(
                    "dynamic_engine", configured_engine or "lightmount_domstable"
                )
            ).strip()
            if engine not in _ENGINES - {"auto", "http", "lightmount_lite"}:
                return ProviderResult(
                    False,
                    "",
                    error_type="configuration",
                    error_message=(
                        "Lexmount dynamic_engine must be chrome, chrome_cdp, "
                        "lightmount_dcl or lightmount_domstable"
                    ),
                )
            effective_timeout_ms = min(plan.timeout_ms, max(1, int(http_timeout * 1000)))
            extract["options"] = {
                "engine_preference": engine,
                "timeout_ms": effective_timeout_ms,
            }
            unsupported = ["selector", "minimum_count"]
            if plan.frame_selector:
                unsupported.insert(0, "child_frame_selector")
            if plan.delay_ms:
                unsupported.append("post_completion_delay")
            if plan.minimum_text_fragments:
                unsupported.append("minimum_text_fragments")
            if plan.wait_until != "domcontentloaded":
                unsupported.append("wait_until")
            route = "v1_extract_dynamic"
            wait_metadata = {
                "strategy": {
                    "lightmount_domstable": "dom_stability",
                    "lightmount_dcl": "domcontentloaded",
                }.get(engine, "browser_render"),
                "engine_preference": engine,
                "selector": plan.selector,
                "frame_selector": plan.frame_selector,
                "minimum_count": plan.minimum_count,
                "selector_enforced": False,
                "minimum_count_enforced": False,
                "completion_support": "approximate",
                "unsupported": unsupported,
                "delay_ms": plan.delay_ms,
                "timeout_ms": effective_timeout_ms,
                "wait_until": plan.wait_until,
            }
        elif configured_engine:
            engine = str(configured_engine).strip()
            if engine not in _ENGINES:
                return ProviderResult(
                    False,
                    "",
                    error_type="configuration",
                    error_message="Lexmount engine is unsupported",
                )
            # ``options`` is part of the nested extract request.  ``extract.engine``
            # is not a server field and silently forfeits explicit route selection.
            extract["options"] = {"engine_preference": engine}
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
        if not isinstance(payload, dict):
            payload = {}
        result = payload.get("result")
        result = result if isinstance(result, dict) else {}
        metadata = payload.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        ok = 200 <= response.status < 300 and bool(result)
        main_text = result.get("main_text")
        # The API envelope is call evidence, not page content.  Falling back to
        # response JSON here can turn an empty extraction or an error message
        # into apparent answer text in the shared scorer.
        content = main_text if isinstance(main_text, str) else ""
        error = payload.get("error")
        error = error if isinstance(error, dict) else {}
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
                "route": route,
                "wait": wait_metadata,
                "requested_engine": (
                    extract.get("options", {}).get("engine_preference")
                    if isinstance(extract.get("options"), dict)
                    else None
                ),
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
