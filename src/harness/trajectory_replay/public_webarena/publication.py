"""Projection, validation and packaging for public WebArena replay reports."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import posixpath
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from harness.reporting.publication import archive_inventory
from harness.trajectory_replay.public_webarena.result_audit import audit_result_set
from harness.web_fetch.publication_target import publication_document, require_publication_version
from harness.web_fetch.report_publish import PublishConfig, publish
from harness.web_fetch.safety import unlinked_path
from lexbench_webfetch.paths import contained_path

REPORT_STEM = "webarena-trajectory-replay-latest"

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
    manifest = _read_json(contained_path(formal_root, "run/manifest.json", reject_symlinks=True))
    summary = _read_json(contained_path(formal_root, "run/summary.json", reject_symlinks=True))
    files = _report_files(formal_root, summary)
    target = output_root / "formal/webarena-public-trajectories"
    reports_target = target / "reports"
    reports_target.mkdir(parents=True, exist_ok=False)
    for source in files:
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


FORBIDDEN_PUBLIC_RESULT_TERMS = (
    "首次成功",
    "最终成功",
    "最终尝试",
    "重试",
    "final_passes",
    "final_failures",
    "两个浏览器",
    "双浏览器",
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
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"required JSON object is missing: {path}")
    return payload


def _report_files(formal_root: Path, summary: dict) -> list[Path]:
    from harness.trajectory_replay.public_webarena.report import report_file_names

    files = []
    for name in sorted(report_file_names(summary)):
        source = contained_path(formal_root, "reports/" + name, reject_symlinks=True)
        if not source.is_file():
            raise ValueError(f"standalone report is missing required file: {name}")
        files.append(source)
    return files


def _validate_binding(formal_root: Path, authority: Path) -> tuple[dict, dict, dict]:
    manifest_path = contained_path(formal_root, "run/manifest.json", reject_symlinks=True)
    summary_path = contained_path(formal_root, "run/summary.json", reject_symlinks=True)
    audit_path = contained_path(
        formal_root, f"reports/{REPORT_STEM}-data.json", reject_symlinks=True
    )
    manifest = _read_json(manifest_path)
    summary = _read_json(summary_path)
    audit = _read_json(audit_path)
    if audit.get("suite_sha256") != manifest.get("suite_sha256"):
        raise ValueError("report suite binding mismatch")
    if audit.get("artifact_provenance") != public_projection(
        manifest.get("artifact_provenance") or {}
    ):
        raise ValueError("report artifact binding mismatch")
    expected_public_functional = {
        engine: {
            "tasks": int(values.get("tasks") or 0),
            "passes": int(values.get("result_final_passes", values.get("passes", 0)) or 0),
            "pass_rate": values.get("result_final_pass_rate", values.get("pass_rate")),
        }
        for engine, values in (summary.get("functional") or {}).items()
    }
    comparison = summary.get("resource_comparison") or {}
    expected_public_resources = {
        "scope": comparison.get("scope"),
        "intersection_results": comparison.get(
            "intersection_results", comparison.get("intersection_attempts", 0)
        ),
        "by_engine": comparison.get("by_engine") or {},
    }
    if audit.get("functional") != public_projection(expected_public_functional):
        raise ValueError("report functional binding mismatch")
    if audit.get("resource_comparison") != public_projection(expected_public_resources):
        raise ValueError("report resource_comparison binding mismatch")
    functional = summary.get("functional") or {}
    engines = tuple(str(engine) for engine in (manifest.get("protocol") or {}).get("engines") or ())
    if not engines or tuple(functional) != engines:
        raise ValueError("formal result engine order must match the manifest")
    if any(manifest.get(key) is not None for key in ("agent", "model", "reasoning_effort")):
        raise ValueError("fixed-trajectory formal run must not bind an agent or model")
    current = _read_json(authority)
    expected_results = {
        engine: {
            "passes": row["result_final_passes"],
        }
        for engine, row in functional.items()
    }
    task_count = len(manifest.get("suite") or [])
    checks = {
        "evaluation kind": current.get("evaluation_kind") == "fixed_trajectory_replay",
        "agentic flag": current.get("agentic_browser_use") is False,
        "task count": current.get("task_count_per_engine") == task_count
        and all(int(row.get("tasks") or 0) == task_count for row in functional.values()),
        "engines": current.get("engines") == list(engines),
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
    names = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    for path in root.rglob("*.html"):
        page = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        for raw in re.findall(r"""(?:href|src)=["']([^"']+)["']""", text):
            value = html.unescape(raw)
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            target = posixpath.normpath(
                posixpath.join(posixpath.dirname(page), unquote(parsed.path))
            )
            if target not in names:
                raise ValueError(f"broken local link: {page} -> {value}")


def build(formal_root: Path, archive: Path, attestation: Path, *, authority: Path) -> dict:
    manifest, summary, _audit = _validate_binding(formal_root, authority)
    files = _report_files(formal_root, summary)
    inputs = {
        path.resolve()
        for path in [
            *files,
            authority,
            formal_root / "run/manifest.json",
            formal_root / "run/summary.json",
        ]
    }
    outputs = {archive.resolve(), attestation.resolve()}
    if len(outputs) != 2 or inputs.intersection(outputs):
        raise ValueError("publication outputs must be separate from each other and consumed inputs")
    if archive.is_symlink() or attestation.is_symlink():
        raise ValueError("symlinked publication outputs are forbidden")

    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="webarena-trajectory-") as temporary:
        staging = Path(temporary)
        for source in files:
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"unsafe report file: {source}")
            shutil.copy2(source, staging / source.name)
        shutil.copy2(staging / f"{REPORT_STEM}.html", staging / "index.html")
        _validate_internal_links(staging)
        for path in staging.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if PRIVATE_IPV4.search(text) or LOCAL_USER_PATH.search(text):
                raise ValueError(f"private host or local user path in public report: {path.name}")
            found = next((term for term in FORBIDDEN_PUBLIC_RESULT_TERMS if term in text), None)
            if found:
                raise ValueError(
                    f"public report exposes internal result terminology {found!r}: {path.name}"
                )
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
        "profile_sha256": _sha256(authority),
        "archive_bytes": archive.stat().st_size,
        "files": len(files) + 1,
        "results": {
            engine: {
                "tasks": row["tasks"],
                "passes": row["result_final_passes"],
            }
            for engine, row in functional.items()
        },
    }
    attestation.parent.mkdir(parents=True, exist_ok=True)
    attestation.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def check_report_bundle(formal_root: Path) -> dict[str, int]:
    from harness.trajectory_replay.public_webarena.report import METRICS, PAGES

    reports = formal_root / "reports"
    pages_checked = 0
    for index, (_slug, label, filename) in enumerate(PAGES, 1):
        path = reports / filename
        if not path.is_file():
            raise ValueError(f"missing report page: {filename}")
        text = path.read_text(encoding="utf-8")
        nav = re.search(r'<nav aria-label="报告分页">(.*?)</nav>', text, re.DOTALL)
        if not nav:
            raise ValueError(f"missing report navigation: {filename}")
        for expected_index, (_, expected_label, expected_filename) in enumerate(PAGES, 1):
            expected = f'href="{expected_filename}"'
            label_markup = f">{expected_index}. {expected_label}</a>"
            if expected not in nav.group(1) or label_markup not in nav.group(1):
                raise ValueError(f"incomplete report navigation: {filename}")
        active = re.findall(r"<a [^>]*aria-current=page[^>]*>(.*?)</a>", nav.group(1))
        if active != [f"{index}. {label}"]:
            raise ValueError(f"wrong active report navigation: {filename}")
        pages_checked += 1

    homepage = (reports / f"{REPORT_STEM}.html").read_text(encoding="utf-8")
    specs = [spec for _, metrics in METRICS for spec in metrics]
    if homepage.count('data-metric="') != len(specs):
        raise ValueError("resource table metric inventory does not match the declared contract")
    for spec in specs:
        pattern = (
            rf'<tr data-metric="{re.escape(spec.key)}" '
            rf'data-preference="{re.escape(spec.preference)}">(.*?)</tr>'
        )
        rows = re.findall(pattern, homepage, re.DOTALL)
        if len(rows) != 1:
            raise ValueError(f"missing or duplicated resource metric row: {spec.key}")
        row = rows[0]
        if row.count('class="metric-policy ') != 1:
            raise ValueError(f"missing metric interpretation badge: {spec.key}")
        if spec.preference == "neutral" and ('class="better"' in row or 'class="worse"' in row):
            raise ValueError(f"neutral metric must not declare a winner: {spec.key}")

    summary = json.loads((formal_root / "run/summary.json").read_text(encoding="utf-8"))
    comparison = summary.get("resource_comparison") or {}
    relative = comparison.get("relative_ratios") or {}
    comparison_engines = relative.get("by_engine") or {}
    if not comparison_engines:
        paired = comparison.get("paired_ratios") or {}
        if paired.get("numerator_engine") and paired.get("denominator_engine"):
            comparison_engines = {str(paired["numerator_engine"]): paired.get("metrics") or {}}
    ratio_modules = homepage.count('class="ratio-module" data-formula="per-task-ratio"')
    expected_ratio_modules = len(METRICS) * len(comparison_engines)
    if ratio_modules != expected_ratio_modules:
        raise ValueError("ratio modules are incomplete across metric groups")
    if expected_ratio_modules and "先逐任务相除再汇总" not in homepage:
        raise ValueError("per-task ratio aggregation is not explained")

    return {
        "pages": pages_checked,
        "metrics": len(specs),
        "ratio_modules": ratio_modules,
    }


def stage_report(formal_root: Path, destination: Path) -> None:
    from harness.trajectory_replay.public_webarena.report import write_latest_report

    if destination.exists() or destination.is_symlink():
        raise ValueError("report staging destination already exists")
    if destination.resolve().is_relative_to(formal_root.resolve()):
        raise ValueError("report staging destination must be outside source evidence")
    paths = {
        name: contained_path(formal_root, "run/" + name, reject_symlinks=True)
        for name in ("manifest.json", "summary.json")
    }
    raw = {name: path.read_bytes() for name, path in paths.items()}
    manifest, summary = (json.loads(raw[name]) for name in ("manifest.json", "summary.json"))
    if not isinstance(manifest, dict) or not isinstance(summary, dict):
        raise ValueError("formal report inputs must be JSON objects")
    audit_result_set(manifest, summary)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".report-stage-", dir=destination.parent) as work:
        staged = Path(work) / "bundle"
        (staged / "run").mkdir(parents=True)
        for name, content in raw.items():
            (staged / "run" / name).write_bytes(content)
        write_latest_report(staged / "reports", manifest=manifest, summary=summary)
        for name, content in raw.items():
            current = contained_path(formal_root, "run/" + name, reject_symlinks=True)
            if current.read_bytes() != content:
                raise ValueError("source evidence changed during report staging: " + name)
        if destination.exists() or destination.is_symlink():
            raise ValueError("report staging destination appeared during rendering")
        staged.rename(destination)


def publish_current(
    profile: Path,
    formal_root: Path,
    archive: Path,
    attestation: Path,
    receipt: Path,
    journal: Path,
    *,
    expected_version=None,
    base_url=None,
    transport=None,
) -> dict:
    """Submit only the profile-bound public replay package through the shared publisher."""

    paths = [
        unlinked_path(path)
        for path in (profile, formal_root, archive, attestation, receipt, journal)
    ]
    if len(set(paths)) != len(paths):
        raise ValueError("Publication paths must be distinct")
    profile, formal_root, archive, attestation, receipt, journal = paths
    require_publication_version(
        profile, expected_version, document=publication_document(_read_json(profile))
    )

    def package_check(frozen_archive, source):
        proof = _read_json(attestation)
        if proof.get("profile_sha256") != _sha256(profile):
            raise ValueError("publication attestation does not bind the current profile")
        if proof.get("archive_sha256") != _sha256(frozen_archive):
            raise ValueError("publication archive does not match its attestation")
        check_report_bundle(source)
        with tempfile.TemporaryDirectory(prefix="public-replay-package-check-") as temporary:
            expected_archive = Path(temporary) / "expected.zip"
            expected_proof = build(
                source, expected_archive, Path(temporary) / "proof.json", authority=profile
            )
            if archive_inventory(frozen_archive) != archive_inventory(expected_archive):
                raise ValueError("publication archive differs from the bound report")
            for key in (
                "schema",
                "evaluation_kind",
                "agentic_browser_use",
                "run_id",
                "suite_sha256",
                "manifest_sha256",
                "summary_sha256",
                "files",
                "results",
            ):
                if proof.get(key) != expected_proof[key]:
                    raise ValueError(
                        "publication attestation disagrees with the bound report: " + key
                    )

    # A saved submission can finish byte verification without activating another version.
    prior = _read_json(receipt) if receipt.exists() else {}
    expected_share = (_read_json(profile).get('publication') or {}).get('share_url')
    if prior.get('share_url') and expected_share and prior['share_url'] != expected_share:
        raise ValueError('Publication receipt share URL differs from the bound profile')
    resume = prior.get("submission_state") == "submitted_unverified" and prior.get(
        "archive_sha256"
    ) == _sha256(archive)
    config = PublishConfig(
        profile,
        archive,
        formal_root,
        receipt,
        journal,
        mode="reuse" if resume and not journal.exists() else "update",
        title=str(_read_json(profile).get("title") or "WebArena 轨迹重放"),
        base_url=base_url,
        expected_version=expected_version,
        operation_namespace="public-webarena",
    )
    result = publish(
        config, transport=transport, package_check=package_check, validation_inputs=[attestation]
    )
    return {"slug": result["slug"], "version": result["versionId"], "url": result["url"]}


def publish_main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Submit the bound public WebArena replay package.")
    for name in ("profile", "formal-root", "archive", "attestation", "receipt", "journal"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--expected-version")
    parser.add_argument("--base-url")
    args = parser.parse_args(argv)
    print(
        json.dumps(
            publish_current(
                args.profile,
                args.formal_root,
                args.archive,
                args.attestation,
                args.receipt,
                args.journal,
                expected_version=args.expected_version,
                base_url=args.base_url,
            ),
            ensure_ascii=False,
        )
    )
