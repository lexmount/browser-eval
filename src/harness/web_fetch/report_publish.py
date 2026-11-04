"""Submit a validated Web Fetch package through its explicit publication profile."""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from harness.record_io import json_bytes
from harness.reporting.publication import PublicationClient, atomic_write
from harness.reporting.publication_target import validate_service_base
from harness.reporting.publish import ReceiptRelease, publish_receipt_release
from harness.web_fetch.publication_check import check
from harness.web_fetch.report_package import part_archives, validate_release
from harness.web_fetch.safety import unlinked_path

CLIENT = PublicationClient("WebFetch-Report-Publisher/1.0", put_timeout=90)
RENAME_CLIENT = PublicationClient(None, timeout=30)


@dataclass(frozen=True)
class PublishConfig:
    profile: Path
    archive: Path
    source: Path
    receipt: Path
    journal: Path
    mode: str = "reuse"
    title: str = "Web Fetch 评测"
    base_url: str | None = None
    expected_version: str | None = None
    presentation_binding: Path | None = None
    visibility: str | None = None
    parts_manifest: Path | None = None
    snapshot_audit: bool = False


def publish(config: PublishConfig, *, transport=None, package_check=None):
    """Validate the Web Fetch release, then submit it through the receipt lifecycle."""
    validate_release(config.source, presentation_binding=config.presentation_binding, snapshot_audit=config.snapshot_audit)
    validation_inputs = () if config.presentation_binding is None else (config.presentation_binding,)
    attachments, check_target = {}, None
    if config.parts_manifest is not None:
        attachments = part_archives(config.parts_manifest)
        parts_plan = json.loads(config.parts_manifest.read_text(encoding="utf-8"))
        validation_inputs = (*validation_inputs, config.parts_manifest, *attachments.values())

        def check_target(profile, base):
            if parts_plan.get('publications') != profile.get('data_publications'):
                raise ValueError('Data attachment targets differ from the publication authority')
            if any(target.get('base_url') != base for target in parts_plan['publications'].values()):
                raise ValueError('Data attachments must use the profile-bound publication service')

    bound_check = config.presentation_binding is not None or config.parts_manifest is not None or config.snapshot_audit
    if package_check is None and bound_check:
        package_check = partial(check, presentation_binding=config.presentation_binding,
                                external_archives=attachments, snapshot_audit=config.snapshot_audit)
    return publish_receipt_release(
        ReceiptRelease(
            config.profile, config.archive, config.source, config.receipt, config.journal,
            namespace="webfetch", upload_name="webfetch", client=CLIENT, package_check=package_check or check,
            title=config.title, mode=config.mode, base_url=config.base_url,
            expected_version=config.expected_version, visibility=config.visibility,
            inputs=validation_inputs, check_reuse=package_check is not None,
            check_target=check_target,
        ),
        transport=transport,
    )


def main(*, root: Path | None = None):
    root = root or Path.cwd()
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--create", action="store_true")
    mode.add_argument("--update", action="store_true")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, default=root / "publish/artifact-site-receipt.json")
    parser.add_argument("--archive", type=Path, default=root / "publish/webfetch-sites.zip")
    parser.add_argument("--source", type=Path, default=root / "reports/webfetch")
    parser.add_argument(
        "--operation-journal", type=Path, default=root / "publish/webfetch-operation-pending.json"
    )
    parser.add_argument("--expected-version")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("ARTIFACT_SITE_BASE_URL"),
    )
    parser.add_argument("--title", default="Web Fetch 评测")
    parser.add_argument("--presentation-binding", type=Path)
    parser.add_argument("--parts-manifest", type=Path)
    parser.add_argument("--visibility", choices=("private", "unlisted", "public"))
    parser.add_argument("--snapshot-audit",action="store_true",help="Publish a validated saved-output score audit")
    args = parser.parse_args()
    result = publish(
        PublishConfig(
            args.profile,
            args.archive,
            args.source,
            args.receipt,
            args.operation_journal,
            "create" if args.create else "update" if args.update else "reuse",
            args.title,
            args.base_url,
            args.expected_version,
            presentation_binding=args.presentation_binding, visibility=args.visibility, parts_manifest=args.parts_manifest,snapshot_audit=args.snapshot_audit,
        )
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)

def rename_main(argv=None, *, default_root=None) -> None:
    root = default_root or Path.cwd()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "receipt",
        type=Path,
        nargs="?",
        default=root / "publish/artifact-site-receipt.json",
    )
    parser.add_argument("--title", required=True)
    args = parser.parse_args(argv)

    path = unlinked_path(args.receipt)
    before = path.read_bytes()
    receipt = json.loads(before)
    base = validate_service_base(receipt.get("base_url"))
    configured = os.environ.get("ARTIFACT_SITE_BASE_URL")
    if configured and validate_service_base(configured) != base:
        raise ValueError("Requested publication service differs from the bound receipt")
    slug = receipt.get("slug")
    if not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", slug):
        raise ValueError("Publication receipt requires a valid bound site")
    if not args.title.strip():
        raise ValueError("Report title must be nonempty")
    response = RENAME_CLIENT.request(
        base,
        "/api/sites/" + receipt["slug"],
        json.dumps({"title": args.title}).encode(),
        "application/json",
        method="PATCH",
    )
    data = json.loads(response.body)
    if data.get("slug", slug) != slug:
        raise ValueError("Rename response differs from the bound site")
    if unlinked_path(path).read_bytes() != before:
        raise ValueError("Publication receipt changed during rename")
    receipt["title"] = data.get("title", args.title)
    atomic_write(path, json_bytes(receipt))
    print(
        json.dumps(
            {"status": response.status, "title": data.get("title")},
            ensure_ascii=False,
        )
    )
