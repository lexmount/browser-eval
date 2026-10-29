"""Submit a validated Web Fetch package through its explicit publication profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from harness.reporting.publication import (
    PublicationOperation,
    atomic_write,
    json_bytes,
    public_share_context,
    snapshot_publication,
)
from harness.web_fetch.artifact_auth import load_token
from harness.web_fetch.publication import updated_receipt, upload_large_archive
from harness.web_fetch.publication_check import check
from harness.web_fetch.publication_target import (
    publication_base,
    publication_document,
    require_publication_site,
    require_publication_version,
    validate_service_base,
)
from harness.web_fetch.report_package import part_archives, validate_release
from harness.web_fetch.safety import reject_evidence_symlinks, unlinked_path

STAGED_UPLOAD_THRESHOLD = 23 * 1024 * 1024


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
    operation_namespace: str = "webfetch"
    presentation_binding: Path | None = None
    visibility: str | None = None
    parts_manifest: Path | None = None
    snapshot_audit: bool = False


def response_json(status, raw, *, operation):
    if not 200 <= status < 300:
        raise RuntimeError(f"{operation} failed with HTTP {status}")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError(f"{operation} returned a non-object response")
    return result


def activated_version(result):
    version = result.get("versionId") or result.get("version")
    if isinstance(version, dict):
        version = version.get("id")
    if not isinstance(version, str) or not version.startswith("ver_"):
        raise ValueError("Publication receipt omitted an activated version")
    return version


def multipart(archive, title, digest):
    boundary = "----webfetch" + hashlib.sha256((digest + "|" + title).encode()).hexdigest()[:32]
    fields = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        for key, value in [("mode", "zip"), ("title", title)]
    ]
    fields.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        'filename="webfetch.zip"\r\nContent-Type: application/zip\r\n\r\n'.encode()
        + archive.read_bytes()
        + b"\r\n"
    )
    fields.append(f"--{boundary}--\r\n".encode())
    return b"".join(fields), "multipart/form-data; boundary=" + boundary


def _receipt_identity(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Publication receipt must be a regular file")
    return path.read_bytes() if path.exists() else None


def _safe_receipt(value):
    if isinstance(value, dict):
        return {
            key: _safe_receipt(item)
            for key, item in value.items()
            if key not in {"editToken", "claimToken", "token"}
        }
    if isinstance(value, list):
        return [_safe_receipt(item) for item in value]
    return value


def publish(config: PublishConfig, *, transport=None, package_check=None, validation_inputs=()):
    """Upload or finish a receipt; remote byte verification is a separate command."""
    if config.mode not in {"create", "update", "reuse"}:
        raise ValueError("Unknown publication mode")
    if config.operation_namespace == 'webfetch':
        validate_release(config.source, presentation_binding=config.presentation_binding,snapshot_audit=config.snapshot_audit)
    if config.visibility not in {None, "private", "unlisted", "public"}:
        raise ValueError("Unknown publication visibility")
    if config.presentation_binding is not None:
        validation_inputs = (*validation_inputs, config.presentation_binding)
    attachments, parts_plan = {}, None
    if config.parts_manifest is not None:
        attachments = part_archives(config.parts_manifest)
        parts_plan = json.loads(config.parts_manifest.read_text())
        validation_inputs = (*validation_inputs,config.parts_manifest,*attachments.values())
    if package_check is None and (config.presentation_binding is not None or config.parts_manifest is not None or config.snapshot_audit):
        package_check = partial(check,presentation_binding=config.presentation_binding,external_archives=attachments,snapshot_audit=config.snapshot_audit)
    protected = {config.profile.resolve(), config.archive.resolve(),
                 *(Path(path).resolve() for path in validation_inputs)}
    if config.receipt.resolve() in protected or config.journal.resolve() in protected:
        raise ValueError("Publication receipts and journals must not replace package inputs")
    reject_evidence_symlinks(config.profile)
    profile_bytes = config.profile.read_bytes()
    profile = publication_document(json.loads(profile_bytes))
    base = publication_base(config.profile, config.base_url, document=profile)
    if parts_plan is not None:
        if parts_plan.get('publications') != profile.get('data_publications'):
            raise ValueError('Data attachment targets differ from the publication authority')
        if any(target.get('base_url') != base for target in parts_plan['publications'].values()):
            raise ValueError('Data attachments must use the profile-bound publication service')
    previous_receipt = _receipt_identity(config.receipt)
    result = json.loads(previous_receipt) if previous_receipt is not None else {}
    if not isinstance(result, dict):
        raise ValueError("Publication receipt must be a JSON object")
    if config.receipt.resolve() in {config.profile.resolve(), config.journal.resolve()}:
        raise ValueError("Publication authority, receipt and operation journal must be distinct")
    if config.journal.resolve() == config.profile.resolve():
        raise ValueError("Publication authority, receipt and operation journal must be distinct")
    if config.mode == "create":
        if profile.get("artifact_hub_site") or profile.get("artifact_hub_expected_version"):
            raise ValueError("--create requires an unbound publication profile")
    else:
        if previous_receipt is None:
            raise ValueError("Current report requires its existing site receipt")
        require_publication_site(result, config.profile, base, document=profile)
    expected = (
        require_publication_version(config.profile, config.expected_version, document=profile)
        if config.mode == "update"
        else None
    )

    def release_guard():
        pass

    def local_guard():
        release_guard()
        reject_evidence_symlinks(config.profile)
        if config.profile.read_bytes() != profile_bytes:
            raise ValueError("Publication profile changed during submission")
        if _receipt_identity(config.receipt) != previous_receipt:
            raise ValueError("Publication receipt changed during submission")

    def send(path, data=None, content_type=None, *, method=None, extra_headers=None):
        local_guard()
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Publication request must stay on the configured service")
        if transport is not None:
            return transport(path, data, content_type, method=method, extra_headers=extra_headers)
        headers = {
            "Authorization": "Bearer " + load_token(base_url=base),
            "User-Agent": "WebFetch-Report-Publisher/1.0",
        }
        if content_type:
            headers["Content-Type"] = content_type
        headers.update(extra_headers or {})
        request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=90 if method == "PUT" else 240) as response:
            return response.status, response.read()

    def save(value):
        nonlocal previous_receipt
        local_guard()
        payload = json_bytes(_safe_receipt(value))
        atomic_write(config.receipt, payload)
        os.chmod(config.receipt, 0o600)
        previous_receipt = payload

    def finish(value):
        version = activated_version(value)
        if config.visibility is not None:
            slug = urllib.parse.quote(value["slug"], safe="")
            path = "/api/sites/" + slug + "/sharing"
            status, raw = send(path)
            sharing = response_json(status, raw, operation="sharing inspection")
            if sharing.get("visibility") != config.visibility:
                status, raw = send(path, json_bytes({"visibility": config.visibility}),
                                   "application/json", method="PUT")
                sharing = response_json(status, raw, operation="sharing update")
            if sharing.get("visibility") != config.visibility or sharing.get("moderation"):
                raise ValueError("Requested sharing is not active; inspect the saved publication")
            value.update(share_url=base + "/s/" + slug, visibility=config.visibility,
                         publication_pending=False)
            save(value)
            return {"slug": value["slug"], "versionId": version,
                    "url": value["share_url"], "visibility": config.visibility}
        had_share = bool(value.get("share_url"))
        if not value.get("share_url"):
            slug = urllib.parse.quote(value["slug"], safe="")
            status, raw = send(
                "/api/sites/" + slug + "/shares", b'{"policy":"public"}', "application/json"
            )
            value["share_url"] = response_json(status, raw, operation="share creation")["url"]
        public_share_context(base, value["share_url"])
        if config.mode != "reuse" or not had_share:
            value["publication_pending"] = False
            save(value)
        return {"slug": value["slug"], "versionId": version, "url": value["share_url"]}

    if config.mode == "reuse":
        if package_check is None:
            return finish(result)
        with snapshot_publication(
            config.archive, inputs=[config.profile, config.source, *validation_inputs]
        ) as release:
            release_guard = release.check
            package_check(release.archive, config.source)
            release.check()
            summary = finish(result)
            release.check()
            return summary
    for path in (config.archive, config.receipt, config.journal):
        if path.resolve().is_relative_to(config.source.resolve()):
            raise ValueError("Publication outputs must stay outside the source report")
    with snapshot_publication(
        config.archive, inputs=[config.profile, config.source, *validation_inputs]
    ) as release:
        release_guard = release.check
        (package_check or check)(release.archive, config.source)
        release.check()
        original_send = send

        def guarded_send(*args, **kwargs):
            if '/commit' in args[0] or '/versions' in args[0]:
                release.verify()
            return original_send(*args, **kwargs)

        send = guarded_send
        identity = {
            "schema": config.operation_namespace + "_publication_operation/1",
            "base_url": base,
            "archive_sha256": release.sha256,
            "title": config.title,
            "kind": config.mode,
            "slug": None if config.mode == "create" else result["slug"],
            "expected_version": expected,
        }
        operations = PublicationOperation(config.journal, config.operation_namespace + "-")
        pending, resumed = operations.pending(
            identity, allow_create=config.mode != "create" or previous_receipt is None
        )
        already_submitted = (
            result.get("operation_key") == pending["key"]
            and result.get("archive_sha256") == release.sha256
            and result.get("base_url") == base
        )
        if config.mode == "create" and previous_receipt is not None and not already_submitted:
            raise ValueError("--create requires a new receipt or its matching pending operation")
        if not already_submitted:
            recovered = (
                operations.completed(pending["key"], request=send, decode=response_json)
                if resumed
                else None
            )
            if recovered is not None:
                status, raw = recovered
            elif release.archive.stat().st_size > STAGED_UPLOAD_THRESHOLD:

                def staged_send(path, data=None, content_type=None, *, method=None):
                    headers = None
                    if data is not None and method is None:
                        key = pending["key"] if "/commit" in path else pending["key"] + ":start"
                        headers = {"Idempotency-Key": key}
                    return send(path, data, content_type, method=method, extra_headers=headers)

                raw = upload_large_archive(
                    staged_send, release.archive, identity["slug"], expected, title=config.title, max_workers=4
                )
                status = 200
            else:
                body, content_type = multipart(release.archive, config.title, release.sha256)
                endpoint = (
                    "/api/sites"
                    if config.mode == "create"
                    else "/api/sites/"
                    + urllib.parse.quote(result["slug"], safe="")
                    + "/versions?"
                    + urllib.parse.urlencode({"expected_version": expected})
                )
                status, raw = send(
                    endpoint, body, content_type, extra_headers={"Idempotency-Key": pending["key"]}
                )
            response = response_json(status, raw, operation="package submission")
            if config.mode == "update":
                if response.get("slug", result["slug"]) != result["slug"]:
                    raise ValueError("Publication returned an unexpected site")
                result = updated_receipt(result, response)
            else:
                result = response
                if not isinstance(result.get("slug"), str) or not result["slug"]:
                    raise ValueError("Publication did not return a site slug")
            activated_version(result)
            for key in ("verified", "verification", "verified_at"):
                result.pop(key, None)
            result.update(
                base_url=base,
                archive_sha256=release.sha256,
                publication_pending=True,
                operation_key=pending["key"],
                submission_state="submitted_unverified",
            )
            save(result)
        summary = finish(result)
        release.verify()
        operations.clear(identity)
        return summary


def main(*, root: Path | None = None):
    root = root or Path.cwd()
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--create", action="store_true")
    mode.add_argument("--update", action="store_true")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, default=root / "publish/artifact-hub-receipt.json")
    parser.add_argument("--archive", type=Path, default=root / "publish/webfetch-sites.zip")
    parser.add_argument("--source", type=Path, default=root / "reports/webfetch")
    parser.add_argument(
        "--operation-journal", type=Path, default=root / "publish/webfetch-operation-pending.json"
    )
    parser.add_argument("--expected-version")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("ARTIFACT_SITE_BASE_URL", os.environ.get("ARTIFACT_HUB_BASE_URL")),
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
        default=root / "publish/artifact-hub-receipt.json",
    )
    parser.add_argument("--title", required=True)
    args = parser.parse_args(argv)

    path = unlinked_path(args.receipt)
    before = path.read_bytes()
    receipt = json.loads(before)
    base = validate_service_base(receipt.get("base_url"))
    configured = os.environ.get("ARTIFACT_SITE_BASE_URL") or os.environ.get("ARTIFACT_HUB_BASE_URL")
    if configured and validate_service_base(configured) != base:
        raise ValueError("Requested publication service differs from the bound receipt")
    slug = receipt.get("slug")
    if not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", slug):
        raise ValueError("Publication receipt requires a valid bound site")
    if not args.title.strip():
        raise ValueError("Report title must be nonempty")
    token = load_token(base_url=base)
    request = urllib.request.Request(
        base + "/api/sites/" + receipt["slug"],
        data=json.dumps({"title": args.title}).encode(),
        method="PATCH",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
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
