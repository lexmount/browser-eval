"""Publish an existing Web Scraper report through the shared staged-update owner."""

from pathlib import Path

from harness.record_io import json_bytes
from harness.reporting.publication import PublicationClient, atomic_write
from harness.reporting.publish import StagedUpdate, publish_staged_update
from harness.web_scraper.pipeline import digest, read_json, require_external_output
from harness.web_scraper.report import check, package


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
    work.mkdir(parents=True, exist_ok=True)
    archive = work / "report.zip"
    if archive.exists():
        check(source, release=True, visual_receipt=visual_receipt)
        proof = read_json(archive.with_suffix(".receipt.json"))
        if proof["archive_sha256"] != digest(archive.read_bytes()) or proof[
            "report_manifest_sha256"
        ] != digest((source / "report-manifest.json").read_bytes()):
            raise ValueError("Retained publication package differs from this report")
    else:
        proof = package(source, archive, visual_receipt=visual_receipt)

    def unchanged_target():
        if target.read_bytes() != original:
            raise ValueError("Web Scraper publication target changed")

    def bind(version):
        manifest = read_json(source / "report-manifest.json")
        builder = read_json(source / "browser-eval-version.json")
        updated = {
            **bound,
            "expected_version": version,
            "renderer_source_revision": builder["revision"],
            "profile_sha256": manifest["profile_sha256"],
            "profile": {**bound.get("profile", {}), "sha256": manifest["profile_sha256"]},
            "renderer_sha256": digest(Path(__file__).with_name("report.py").read_bytes()),
            "report_path": str(source),
            "archive_sha256": proof["archive_sha256"],
            "report_manifest_sha256": proof["report_manifest_sha256"],
        }
        atomic_write(target, json_bytes(updated))

    version = publish_staged_update(
        StagedUpdate(
            label="Web Scraper",
            prefix="web-scraper",
            client=PublicationClient("Browser-Eval-Web-Scraper/1.0"),
            base=bound["host"],
            slug=bound["slug"],
            expected_version=bound["expected_version"],
            share_path="/s/" + bound["slug"],
            title=bound["title"],
            archive=archive,
            archive_sha256=proof["archive_sha256"],
            inputs=(source, target, Path(visual_receipt)),
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
