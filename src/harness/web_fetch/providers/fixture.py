from __future__ import annotations

import base64
import hashlib
import json

from lexbench_webfetch.models import EvalCase

from harness.web_fetch.models import ProviderResult
from harness.web_fetch.native_routes import NativeRoute
from harness.web_fetch.providers.base import Provider


class FixtureProvider(Provider):
    """Deterministic provider used to verify a fresh installation offline."""
    def native_route(self, case) -> NativeRoute:
        return NativeRoute('fixture', 'synthetic_fixture', ('content', 'title', 'final_url',))


    def _result(self, **kwargs):
        result = ProviderResult(**kwargs)
        if self.config.get('uniform_native_parameters'):
            raw = json.dumps({'content': result.content, 'title': result.title, 'final_url': result.final_url}).encode()
            result.metadata.update(_raw_response_b64=base64.b64encode(raw).decode(),
                raw_response_sha256=hashlib.sha256(raw).hexdigest(), raw_response_bytes=len(raw),
                native_answer_projection={'schema_version': 2, 'content_pointer': ['content'], 'content_format': 'text',
                    'result_fields': {'title': {'pointer': ['title']}, 'final_url': {'pointer': ['final_url']}}})
        return result

    def fetch(self, case: EvalCase, timeout: float, *, native_route_ledger=None) -> ProviderResult:
        fail_cases = set(self.config.get("fail_cases", []))
        if case.id in fail_cases:
            return self._result(
                ok=False,
                content="Fixture failure: configured test failure.",
                status_code=502,
                final_url=case.url,
                error_type="fixture_failure",
                error_message="Configured fixture failure",
            )
        markers = "\n".join(case.expected_any)
        return self._result(
            ok=True,
            content=f"# {case.name}\n\n{markers}\n\nFixture content for {case.url}",
            status_code=200,
            final_url=case.url,
            title=case.name,
            metadata={"fixture": True},
        )
