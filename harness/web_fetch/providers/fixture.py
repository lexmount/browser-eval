from __future__ import annotations

from ..models import EvalCase, ProviderResult
from .base import Provider


class FixtureProvider(Provider):
    """Deterministic provider used to verify a fresh installation offline."""

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        fail_cases = set(self.config.get("fail_cases", []))
        if case.id in fail_cases:
            return ProviderResult(
                ok=False,
                content="Fixture failure: configured test failure.",
                status_code=502,
                final_url=case.url,
                error_type="fixture_failure",
                error_message="Configured fixture failure",
            )
        markers = "\n".join(case.expected_any)
        return ProviderResult(
            ok=True,
            content=f"# {case.name}\n\n{markers}\n\nFixture content for {case.url}",
            status_code=200,
            final_url=case.url,
            title=case.name,
            metadata={"fixture": True},
        )
