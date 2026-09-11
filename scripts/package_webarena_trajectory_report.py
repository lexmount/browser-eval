"""Build the standalone Artifact Hub bundle for the fixed-trajectory report."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import posixpath
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.browser_use.publication import (  # noqa: E402
    LOCAL_USER_PATH,
    PRIVATE_IPV4,
    public_projection,
)

STEM = "webarena-trajectory-replay-latest"
CURRENT_REPORT = ROOT / "evaluation/browser-use-public/current-report.json"
DEFAULT_FORMAL_ROOT = (
    Path.home()
    / ".browser-eval/evidence/current/formal/webarena-public-trajectories"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"required regular file is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_binding(formal_root: Path) -> tuple[dict, dict, dict]:
    manifest_path = formal_root / "run/manifest.json"
    summary_path = formal_root / "run/summary.json"
    audit_path = formal_root / f"reports/{STEM}-data.json"
    manifest = _read_json(manifest_path)
    summary = _read_json(summary_path)
    audit = _read_json(audit_path)
    if audit.get("suite_sha256") != manifest.get("suite_sha256"):
        raise ValueError("report suite binding mismatch")
    if audit.get("artifact_provenance") != public_projection(
        manifest.get("artifact_provenance") or {}
    ):
        raise ValueError("report artifact binding mismatch")
    for key in ("functional", "resource_comparison"):
        if audit.get(key) != public_projection(summary.get(key) or {}):
            raise ValueError(f"report {key} binding mismatch")
    functional = summary.get("functional") or {}
    if set(functional) != {"chromium", "moli"}:
        raise ValueError("formal result must contain exactly Chromium and Moli")
    if any(int(row.get("tasks") or 0) != 30 for row in functional.values()):
        raise ValueError("formal result must contain 30 tasks per engine")
    if any(manifest.get(key) is not None for key in ("agent", "model", "reasoning_effort")):
        raise ValueError("fixed-trajectory formal run must not bind an agent or model")
    current = _read_json(CURRENT_REPORT)
    expected_results = {
        engine: {
            "first_passes": row["first_passes"],
            "recovered": row["recovered"],
            "final_passes": row["final_passes"],
        }
        for engine, row in functional.items()
    }
    checks = {
        "evaluation kind": current.get("evaluation_kind") == "fixed_trajectory_replay",
        "agentic flag": current.get("agentic_browser_use") is False,
        "task count": current.get("task_count_per_engine") == 30,
        "run id": current.get("run_id") == manifest.get("run_id"),
        "suite hash": current.get("suite_sha256") == manifest.get("suite_sha256"),
        "manifest hash": (current.get("evidence") or {}).get("manifest_sha256")
        == _sha256(manifest_path),
        "summary hash": (current.get("evidence") or {}).get("summary_sha256")
        == _sha256(summary_path),
        "results": current.get("results") == expected_results,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError("current report authority mismatch: " + ", ".join(failed))
    return manifest, summary, audit


def _validate_internal_links(root: Path) -> None:
    names = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    for path in root.rglob("*.html"):
        page = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        for raw in re.findall(r'''(?:href|src)=["']([^"']+)["']''', text):
            value = html.unescape(raw)
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            target = posixpath.normpath(
                posixpath.join(posixpath.dirname(page), unquote(parsed.path))
            )
            if target not in names:
                raise ValueError(f"broken local link: {page} -> {value}")


def build(formal_root: Path, archive: Path, attestation: Path) -> dict:
    formal_root = formal_root.resolve()
    manifest, summary, _audit = _validate_binding(formal_root)
    reports = formal_root / "reports"
    files = sorted(reports.glob(f"{STEM}*"))
    required = {f"{STEM}.html", f"{STEM}-research.html", f"{STEM}-data.json"}
    if not required.issubset({path.name for path in files}):
        raise ValueError("standalone report is missing required pages")

    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="webarena-trajectory-") as temporary:
        staging = Path(temporary)
        for source in files:
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"unsafe report file: {source}")
            shutil.copy2(source, staging / source.name)
        shutil.copy2(staging / f"{STEM}.html", staging / "index.html")
        _validate_internal_links(staging)
        for path in staging.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if PRIVATE_IPV4.search(text) or LOCAL_USER_PATH.search(text):
                raise ValueError(f"private host or local user path in public report: {path.name}")
        temporary_archive = archive.with_suffix(archive.suffix + ".tmp")
        temporary_archive.unlink(missing_ok=True)
        with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    output.write(path, path.relative_to(staging).as_posix())
        temporary_archive.replace(archive)

    functional = summary["functional"]
    payload = {
        "schema": "lexmount_webarena_fixed_trajectory_publication/1",
        "evaluation_kind": "fixed_trajectory_replay",
        "agentic_browser_use": False,
        "run_id": manifest.get("run_id"),
        "suite_sha256": manifest.get("suite_sha256"),
        "manifest_sha256": _sha256(formal_root / "run/manifest.json"),
        "summary_sha256": _sha256(formal_root / "run/summary.json"),
        "archive_sha256": _sha256(archive),
        "archive_bytes": archive.stat().st_size,
        "files": len(files) + 1,
        "results": {
            engine: {
                "tasks": row["tasks"],
                "first_passes": row["first_passes"],
                "recovered": row["recovered"],
                "final_passes": row["final_passes"],
            }
            for engine, row in functional.items()
        },
    }
    attestation.parent.mkdir(parents=True, exist_ok=True)
    attestation.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-root", type=Path, default=DEFAULT_FORMAL_ROOT)
    parser.add_argument(
        "--archive", type=Path, default=ROOT / "publish/webarena-fixed-trajectory.zip"
    )
    parser.add_argument(
        "--attestation",
        type=Path,
        default=ROOT / "publish/webarena-fixed-trajectory-attestation.json",
    )
    args = parser.parse_args()
    print(json.dumps(build(args.formal_root, args.archive, args.attestation), ensure_ascii=False))


if __name__ == "__main__":
    main()
