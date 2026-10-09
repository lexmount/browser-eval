from __future__ import annotations

import re
import sys
from pathlib import Path

from ..models import EvalCase, ProviderResult
from .subprocess_jsonl import SubprocessJsonlProvider


def _sanitize_public_content(content: str) -> str:
    """Remove CowAgent workspace destinations while retaining fetched text."""
    return re.sub(
        r"\s*\|\s*Saved to:\s*(?:/Users/|/home/|/private/|/tmp/)[^\]\r\n]+",
        "",
        content,
    )


class CowAgentBuiltinProvider(SubprocessJsonlProvider):
    """Invoke CowAgent's repository-local WebFetch implementation unchanged."""

    def __init__(self, name, config):
        from ..storage import data_home

        worker = Path(__file__).parents[1] / 'workers/cowagent_worker.py'
        workspace = Path(str(config.get(
            "workspace", data_home() / "runs/cowagent-workspace"
        ))).expanduser().resolve()
        derived = {**config, 'method_type': 'cowagent_builtin',
                   'command': [sys.executable, '-u', str(worker), str(workspace)],
                   'expected_python_version': config.get(
                       'expected_python_version', 'Python ' + sys.version.split()[0])}
        paths = derived.get('required_paths', [])
        if isinstance(paths, list):
            derived['required_paths'] = [*paths,
                '{harness_root}/harness/web_fetch/workers/cowagent_worker.py']
        super().__init__(name, derived)

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        result = super().fetch(case, timeout)
        result.content = _sanitize_public_content(result.content)
        if result.error_message:
            result.error_message = _sanitize_public_content(result.error_message)
        return result
