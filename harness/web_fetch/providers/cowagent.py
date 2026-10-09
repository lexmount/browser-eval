from __future__ import annotations

import importlib
import base64
import json
import hashlib
import math
import re
import sys
from pathlib import Path

from ..models import EvalCase, ProviderResult
from .base import Provider
from ..diagnostics import diagnostic_context
from .runtime_identity import validate_runtime_identity


def _sanitize_public_content(content: str) -> str:
    """Remove CowAgent workspace destinations while retaining fetched text."""
    return re.sub(
        r"\s*\|\s*Saved to:\s*(?:/Users/|/home/|/private/|/tmp/)[^\]\r\n]+",
        "",
        content,
    )




class CowAgentBuiltinProvider(Provider):
    """Invoke native WebFetch with the shared caller timeout budget."""

    _REQUIRED_PATHS = {
        "{repo_path}/agent/tools/web_fetch/web_fetch.py",
        "{repo_path}/agent/tools/utils/url_safety.py",
    }

    def validate(self) -> list[str]:
        if hasattr(self, "_implementation_identity"):
            del self._implementation_identity
        root = Path(str(self.config.get("repo_path", "."))).expanduser().resolve()
        def resolve(value):
            return str(value).replace("{repo_path}", str(root)).replace("{harness_root}", str(Path(__file__).resolve().parents[3]))

        from ..network_dns import validate_dns_mode
        try:
            mode = validate_dns_mode(self.config.get("dns_mode", "system"))
        except ValueError as error:
            return [str(error)]
        missing, identity = validate_runtime_identity(
            self.config, resolve, required_fields=("expected_repo_revision",),
            required_paths=self._REQUIRED_PATHS)
        if mode == "public_doh" and "{harness_root}/harness/web_fetch/network_dns.py" not in self.config.get("required_paths", []):
            missing.append("locked public DNS helper paths")
        if identity is not None and not missing:
            self._implementation_identity = identity
        return missing

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        self.check_native_call(case)
        from ..network_dns import public_dns_context, validate_dns_mode
        dns_mode = validate_dns_mode(self.config.get("dns_mode", "system"))
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
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        native_timeout = module.DEFAULT_TIMEOUT
        tool = module.WebFetch({"cwd": str(workspace)})
        with diagnostic_context(getattr(self, "_call_diagnostics", None), names=("log",)), public_dns_context(dns_mode, timeout) as dns_trace:
            result = tool.execute({"url": case.url})
        raw = json.dumps({"status": result.status, "result": result.result,
                          "ext_data": result.ext_data, "display": result.display},
                         ensure_ascii=False).encode()
        ok = result.status == "success"
        content = _sanitize_public_content(str(result.result or ""))
        return ProviderResult(
            ok=ok,
            content=content,
            final_url=case.url,
            error_type=None if ok else "tool_error",
            error_message=None if ok else content,
            metadata={
                "tool_status": result.status,
                "dns_mode": dns_mode,
                "dns_resolutions": dns_trace,
                "request_summary": {"caller_timeout_seconds": timeout, "caller_dns_mode": dns_mode},
                "source_file": str(root / "agent/tools/web_fetch/web_fetch.py"),
                "cowagent_default_timeout_seconds": native_timeout,
                "effective_timeout_seconds": native_timeout,
                "timeout_adapter": "native_default",
                "_raw_response_b64": base64.b64encode(raw).decode(),
                "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
                "raw_response_bytes": len(raw),
                "harness_timeout_seconds": timeout,
            },
        )
