"""Publish an existing Web Extract report through the shared staged-update owner."""

import time
from pathlib import Path

from harness.record_io import json_bytes
from harness.reporting.publication import PublicationClient, archive_inventory, atomic_write
from harness.reporting.publish import StagedUpdate, publish_staged_update
from harness.web_extract.pipeline import digest, read_json, require_external_output
from harness.web_extract.report import check, package, verify_completion_audit


def publish(source, target, work, visual_receipt, *, transport=None):
    source, target, work = Path(source), Path(target), Path(work)
    require_external_output(target)
    require_external_output(work)
    original = target.read_bytes()
    bound = read_json(target)
    if (
        not bound.get("host", "").startswith("https://")
        or not bound.get("slug")
        or not bound.get("expected_version", "").startswith("ver_")
    ):
        raise ValueError("Publication requires this report's complete existing target binding")
    manifest = read_json(source / "report-manifest.json")
    previous = bound.get("profile_sha256") or bound.get("profile", {}).get("sha256")
    current = manifest.get("profile_sha256")
    successor = bound.get("approved_profile_successor")
    if not previous or not current:
        raise ValueError(
            "Report profile differs from its publication target; approve its successor"
        )
    next_profile = bound.get("profile", {}).get("path")
    if current != previous:
        if not isinstance(successor, dict) or (
            successor.get("from_sha256") != previous
            or successor.get("to_sha256") != current
            or not successor.get("profile_path")
        ):
            raise ValueError(
                "Report profile differs from its publication target; approve its successor"
            )
        selected = Path(successor["profile_path"])
        require_external_output(selected)
        read_json(selected)
        if digest(selected.read_bytes()) != current:
            raise ValueError("Publication successor profile bytes differ from their approved hash")
        next_profile = str(selected)

    def unchanged_profile():
        if current != previous and digest(Path(next_profile).read_bytes()) != current:
            raise ValueError("Publication successor profile changed")
        verify_completion_audit(source, next_profile)

    unchanged_profile()
    work.mkdir(parents=True, exist_ok=True)
    archive = work / "report.zip"
    started = time.perf_counter()
    print("Preparing Web Extract publication package...", flush=True)
    if archive.exists():
        check(source, release=True, visual_receipt=visual_receipt, profile=next_profile)
        proof = read_json(archive.with_suffix(".receipt.json"))
        if proof["archive_sha256"] != digest(archive.read_bytes()) or proof[
            "report_manifest_sha256"
        ] != digest((source / "report-manifest.json").read_bytes()):
            raise ValueError("Retained publication package differs from this report")
        manifest = read_json(source / "report-manifest.json")
        expected = {
            **manifest["files"],
            "report-manifest.json": digest((source / "report-manifest.json").read_bytes()),
        }
        if archive_inventory(archive) != expected:
            raise ValueError("Retained publication ZIP inventory differs from checked source")
    else:
        proof = package(source, archive, visual_receipt=visual_receipt, profile=next_profile)
    print(f"Web Extract package ready in {time.perf_counter() - started:.1f}s", flush=True)

    unchanged_profile()

    def unchanged_target():
        unchanged_profile()
        if target.read_bytes() != original:
            raise ValueError("Web Extract publication target changed")

    def bind(version):
        unchanged_target()
        manifest = read_json(source / "report-manifest.json")
        builder = read_json(source / "browser-eval-version.json")
        updated = {
            **bound,
            "expected_version": version,
            "renderer_source_revision": builder["revision"],
            "profile_sha256": manifest["profile_sha256"],
            "profile": {"path": next_profile, "sha256": manifest["profile_sha256"]},
            "renderer_sha256": digest(Path(__file__).with_name("report.py").read_bytes()),
            "report_path": str(source),
            "archive_sha256": proof["archive_sha256"],
            "report_manifest_sha256": proof["report_manifest_sha256"],
        }
        updated.pop("approved_profile_successor", None)
        atomic_write(target, json_bytes(updated))

    version = publish_staged_update(
        StagedUpdate(
            allow_inline=True,
            inline_limit_bytes=bound.get("inline_limit_bytes"),
            label="Web Extract",
            prefix="web-extract",
            client=PublicationClient("Browser-Eval-Web-Extract/1.0"),
            base=bound["host"],
            slug=bound["slug"],
            expected_version=bound["expected_version"],
            share_path="/s/" + bound["slug"],
            title=bound["title"],
            archive=archive,
            archive_sha256=proof["archive_sha256"],
            inputs=(
                source,
                target,
                Path(visual_receipt),
                *((Path(next_profile),) if current != previous else ()),
            ),
            work=work,
            guard=unchanged_target,
            bind=bind,
        ),
        transport=transport,
    )
    return {
        "slug": bound["slug"],
        "versionId": version,
        "url": bound["host"] + "/s/" + bound["slug"],
    }
