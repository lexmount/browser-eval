"""Public projection and minimal export for browser-use reports."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

PRIVATE_IPV4 = re.compile(
    r"(?<![0-9])(?:10(?:\.[0-9]{1,3}){3}|192\.168(?:\.[0-9]{1,3}){2}|"
    r"172\.(?:1[6-9]|2[0-9]|3[01])(?:\.[0-9]{1,3}){2})(?![0-9])"
)
LOCAL_USER_PATH = re.compile(r"/(?:Users|home|data)/[^/\s<>'\"]+")


def public_projection(value: Any) -> Any:
    """Remove host-specific identities while retaining report semantics and hashes."""
    if isinstance(value, dict):
        return {key: public_projection(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public_projection(item) for item in value]
    if isinstance(value, tuple):
        return tuple(public_projection(item) for item in value)
    if isinstance(value, str):
        return LOCAL_USER_PATH.sub("/[local-user]", PRIVATE_IPV4.sub("webarena.local", value))
    return value


def export_public_report_bundle(formal_root: Path, output_root: Path) -> Path:
    """Export reports plus only the binding fields needed by the site builder."""
    manifest = json.loads((formal_root / "run/manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((formal_root / "run/summary.json").read_text(encoding="utf-8"))
    target = output_root / "formal/webarena-public-trajectories"
    reports_target = target / "reports"
    reports_target.mkdir(parents=True, exist_ok=False)
    for source in sorted((formal_root / "reports").glob("webarena-trajectory-replay-latest*")):
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"unsafe report file: {source}")
        shutil.copy2(source, reports_target / source.name)
    run_target = target / "run"
    run_target.mkdir()
    public_manifest = public_projection(
        {
            "suite_sha256": manifest.get("suite_sha256"),
            "artifact_provenance": manifest.get("artifact_provenance") or {},
        }
    )
    public_summary = public_projection(
        {
            "functional": summary.get("functional") or {},
            "resource_comparison": summary.get("resource_comparison") or {},
        }
    )
    (run_target / "manifest.json").write_text(
        json.dumps(public_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (run_target / "summary.json").write_text(
        json.dumps(public_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target
