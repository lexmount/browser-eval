from __future__ import annotations

import importlib
import sys
from pathlib import Path

from ..models import EvalCase, ProviderResult
from .base import Provider
from .runtime_identity import validate_runtime_identity


class CowAgentBuiltinProvider(Provider):
    """Invoke CowAgent's repository-local WebFetch implementation unchanged."""

    _LOCKED_REVISION = "ed5bb344cfe42cee443c8266ba47ee666d8d7471"
    _REQUIRED_PATHS = {
        "{repo_path}/agent/tools/web_fetch/web_fetch.py",
        "{repo_path}/agent/tools/utils/url_safety.py",
    }

    def validate(self) -> list[str]:
        root = Path(str(self.config.get("repo_path", "."))).expanduser().resolve()
        def resolve(value):
            return str(value).replace("{repo_path}", str(root))

        missing, identity = validate_runtime_identity(self.config, resolve)
        if self.config.get("expected_repo_revision") != self._LOCKED_REVISION:
            missing.append(f"repo revision {self._LOCKED_REVISION}")
        if not self._REQUIRED_PATHS.issubset(set(self.config.get("required_paths", []))):
            missing.append("locked required paths")
        if identity is not None:
            self._implementation_identity = identity
        return missing

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        root = Path(str(self.config["repo_path"])).expanduser().resolve()
        from ..storage import data_home
        workspace = Path(str(self.config.get(
            "workspace", data_home() / "runs/cowagent-workspace"
        ))).expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        root_text = str(root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
        module = importlib.import_module("agent.tools.web_fetch.web_fetch")
        tool = module.WebFetch({"cwd": str(workspace)})
        result = tool.execute({"url": case.url})
        ok = result.status == "success"
        content = str(result.result or "")
        return ProviderResult(
            ok=ok,
            content=content,
            final_url=case.url,
            error_type=None if ok else "tool_error",
            error_message=None if ok else content,
            metadata={
                "tool_status": result.status,
                "source_file": str(root / "agent/tools/web_fetch/web_fetch.py"),
                "cowagent_default_timeout_seconds": getattr(module, "DEFAULT_TIMEOUT", None),
                "harness_timeout_seconds": timeout,
            },
        )
