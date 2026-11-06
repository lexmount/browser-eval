"""Run one report release through its publication lifecycle; each track supplies an adapter.

Tracks differ in what they prove and where they keep their target, so the owner offers
three lifecycles rather than one with a branch per track:

- ``publish_receipt_release`` submits a package and records it in a private receipt. Byte
  verification is a separate command (Web Fetch, Web Search, public WebArena replay).
- ``publish_bound_release`` verifies the deployed bytes and public share, then advances the
  profile and attestation together (Browser Use, Browser Trajectory Replay).
- ``publish_staged_update`` resumes one staged update from a per-release journal, verifies it
  and lets the report bind its own flat profile (WCXB).

An adapter always names its own target, version, journals and client. Nothing here holds a
default site, and credentials stay behind ``PublicationClient``.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import urllib.parse
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from http.client import RemoteDisconnected
from io import BytesIO
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote

from harness.record_io import json_bytes
from harness.reporting.publication import (
    PublicationClient,
    PublicationOperation,
    PublicationSnapshot,
    archive_inventory,
    atomic_write,
    binding_receipt_matches,
    multipart_archive,
    public_share_context,
    response_json,
    snapshot_publication,
    write_receipt,
)
from harness.reporting.publication_target import (
    publication_base,
    publication_document,
    require_publication_site,
    require_publication_version,
)
from harness.web_fetch.safety import reject_evidence_symlinks

STAGED_UPLOAD_THRESHOLD = 23 * 1024 * 1024


def updated_receipt(receipt, response):
    """Keep the receipt's concurrency baseline aligned with the activated version."""
    version = response.get('versionId')
    if not isinstance(version, str) or not version.startswith('ver_'):
        raise ValueError('Publication response omitted activated versionId')
    # Older receipts used ``version``. Keeping it alongside ``versionId`` leaves
    # two apparently authoritative baselines, one of which becomes stale here.
    current = {key: value for key, value in receipt.items() if key != 'version'}
    return {**current, 'versionId': version, 'last_update': response}


def _rate_limit_delay(error):
    header = error.headers.get('Retry-After', '') if error.headers else ''
    delay = 30.0
    if header:
        try:
            delay = float(header)
        except ValueError:
            try:
                delay = parsedate_to_datetime(header).timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                pass
    if not math.isfinite(delay) or delay > 120:
        raise error
    return max(1.0, delay)


def put_file(request, path, data):
    """Retry only the identical idempotent staging PUT with bounded waits."""
    for attempt in range(4):
        try:
            return request(path, data, 'application/octet-stream', method='PUT')
        except HTTPError as error:
            if error.code >= 500:
                if attempt > 0:
                    raise
                delay=1.0
            elif error.code == 429:
                if attempt == 3:
                    raise
                delay = _rate_limit_delay(error)
            else:
                raise
            error.close()
            print(f'Upload failed with HTTP {error.code}; retrying the same file in {delay:.0f}s', flush=True)
            while delay > 0:
                interval = min(delay, 30)
                time.sleep(interval)
                delay -= interval
        except (TimeoutError, URLError, RemoteDisconnected):
            if attempt == 3:
                raise
            delay=min(2**attempt,4)
            print(f'Upload response unavailable; retrying the same file in {delay}s',flush=True)
            time.sleep(delay)


def _archive_entries(bundle):
    files = [info for info in bundle.infolist() if not info.is_dir()]
    names = [info.filename for info in files]
    if not files or len(set(names)) != len(names) or 'index.html' not in names:
        raise ValueError('Bundle must contain unique files and a root index.html')
    for name in names:
        if (name.startswith('/') or '\\' in name
                or any(part in ('', '..') or part.startswith('.')
                       or part.lower() == 'node_modules' for part in name.split('/'))
                or str(PurePosixPath(name)) != name):
            raise ValueError('Unsafe bundle path: ' + name)
    return files


def upload_large_archive(request, archive, slug=None, expected=None, title=None, *, max_workers=1, resume=False):
    """Upload unpacked bytes to staging and commit the full tree once."""
    if slug and not expected:
        raise ValueError('An existing site requires an exported version baseline')
    if not slug and not title:
        raise ValueError('A new site requires a title')

    def check_baseline():
        if slug:
            _, raw = request('/api/sites/' + quote(slug, safe=''))
            site = json.loads(raw)
            nested = site.get('site') if isinstance(site.get('site'), dict) else site
            observed = nested.get('currentVersionId') or (nested.get('version') or {}).get('id')
            if observed != expected:
                raise RuntimeError('Remote version changed; export and reconcile before publishing')

    check_baseline()
    with zipfile.ZipFile(archive) as bundle:
        files = _archive_entries(bundle)
        names = [info.filename for info in files]
        query = '?expected_version=' + quote(expected, safe='') if expected else ''
        payload = {'slug': slug} if slug else {'title': title}
        _, raw = request('/api/uploads' + query, json.dumps(payload).encode(), 'application/json')
        session = json.loads(raw)['versionId']
        endpoint = '/api/uploads/' + quote(session, safe='')
        # The current service may choose the first uploaded HTML as the
        # version entry. Stage the root page before concurrent file uploads.
        retained = set()
        if resume:
            _, staged = request(endpoint)
            receipts = json.loads(staged).get('files', [])
            if any(item['relpath'] not in names for item in receipts):
                raise ValueError('Retained upload contains files outside this release')
            by_path = {item['relpath']: item for item in receipts}
            for info in files:
                receipt = by_path.get(info.filename, {})
                if (receipt.get('bytes') == info.file_size
                        and receipt.get('sha256') == hashlib.sha256(bundle.read(info)).hexdigest()):
                    retained.add(info.filename)
            print(f'Reusing {len(retained)}/{len(files)} verified uploaded files', flush=True)
        entry = next(info for info in files if info.filename == 'index.html')
        if entry.filename not in retained:
            put_file(request, endpoint + '/files/index.html', bundle.read(entry))
        remaining = [info for info in files
                     if info.filename != 'index.html' and info.filename not in retained]
        if max_workers > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                pending = {
                    pool.submit(put_file, request,
                                endpoint + '/files/' + quote(info.filename, safe='/'),
                                bundle.read(info)): info.filename
                    for info in remaining
                }
                for index, future in enumerate(as_completed(pending), 2):
                    future.result()
                    if index % 100 == 0 or index == len(files):
                        print(f'Uploaded {index}/{len(files)} files', flush=True)
        else:
            for index, info in enumerate(remaining, 2):
                put_file(request, endpoint + '/files/' + quote(info.filename, safe='/'),
                         bundle.read(info))
                print(f'Uploaded {index}/{len(files)} files', flush=True)
        # The manual only guarantees atomic expected_version enforcement on
        # /versions and /edit. Also recheck before chunk commit; this read is
        # explicitly not claimed to be an atomic server-side lock.
        for attempt in range(4):
            check_baseline()
            try:
                _, raw = request(endpoint + '/commit' + query, b'{}', 'application/json')
                break
            except HTTPError as error:
                if error.code != 429 or attempt == 3:
                    raise
                delay = _rate_limit_delay(error)
                error.close()
                print(f'Commit rate limited; retrying the same operation in {delay:g}s', flush=True)
                while delay > 0:
                    interval = min(delay, 30)
                    time.sleep(interval)
                    delay -= interval
        if slug and json.loads(raw).get('slug', slug) != slug:
            raise RuntimeError('Publication returned an unexpected site')
        return raw


def site_version(site: dict) -> str:
    """Read the active version from either shape of the site metadata response."""
    nested = site.get("site") if isinstance(site.get("site"), dict) else site
    version = str(nested.get("currentVersionId") or (nested.get("version") or {}).get("id") or "")
    if not version.startswith("ver_"):
        raise RuntimeError("Artifact Site metadata omitted current version")
    return version


def verify_export(request: Callable, slug: str, archive: Path) -> None:
    """Prove the live site serves exactly the frozen package."""
    status, exported = request(f"/api/sites/{quote(slug)}/export")
    if status != 200:
        response_json(status, exported, operation="site export")
    if archive_inventory(exported) != archive_inventory(archive):
        raise RuntimeError("deployed Artifact Site file tree differs from package")


# Receipt lifecycle


@dataclass(frozen=True)
class ReceiptRelease:
    """A package submitted to the target its profile names, recorded in a private receipt."""

    profile: Path
    archive: Path
    source: Path
    receipt: Path
    journal: Path
    namespace: str
    upload_name: str
    client: PublicationClient
    package_check: Callable
    title: str
    mode: str = "reuse"
    base_url: str | None = None
    expected_version: str | None = None
    visibility: str | None = None
    inputs: tuple = ()
    check_reuse: bool = True
    check_target: Callable | None = None

    def __post_init__(self):
        if self.mode not in {"create", "update", "reuse"}:
            raise ValueError("Unknown publication mode")
        if self.visibility not in {None, "private", "unlisted", "public"}:
            raise ValueError("Unknown publication visibility")


def activated_version(result):
    version = result.get("versionId") or result.get("version")
    if isinstance(version, dict):
        version = version.get("id")
    if not isinstance(version, str) or not version.startswith("ver_"):
        raise ValueError("Publication receipt omitted an activated version")
    return version


def release_multipart(archive, title, digest, upload_name):
    return multipart_archive(
        archive,
        title,
        boundary_prefix="----" + upload_name,
        filename=upload_name + ".zip",
        boundary_seed=(digest + "|" + title).encode(),
    )


def _receipt_identity(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Publication receipt must be a regular file")
    return path.read_bytes() if path.exists() else None


def publish_receipt_release(config: ReceiptRelease, *, transport=None):
    """Upload or finish a receipt; remote byte verification is a separate command."""
    protected = {config.profile.resolve(), config.archive.resolve(),
                 *(Path(path).resolve() for path in config.inputs)}
    if config.receipt.resolve() in protected or config.journal.resolve() in protected:
        raise ValueError("Publication receipts and journals must not replace package inputs")
    reject_evidence_symlinks(config.profile)
    profile_bytes = config.profile.read_bytes()
    profile = publication_document(json.loads(profile_bytes))
    base = publication_base(config.profile, config.base_url, document=profile)
    if config.check_target is not None:
        config.check_target(profile, base)
    previous_receipt = _receipt_identity(config.receipt)
    result = json.loads(previous_receipt) if previous_receipt is not None else {}
    if not isinstance(result, dict):
        raise ValueError("Publication receipt must be a JSON object")
    if config.receipt.resolve() == config.journal.resolve():
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
        return config.client.request(
            base, path, data, content_type, method=method, extra_headers=extra_headers
        )[:2]

    def save(value):
        nonlocal previous_receipt
        local_guard()
        previous_receipt = write_receipt(config.receipt, value)

    def finish(value):
        version = activated_version(value)
        if config.visibility is not None:
            slug = quote(value["slug"], safe="")
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
            slug = quote(value["slug"], safe="")
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
        if not config.check_reuse:
            return finish(result)
        with snapshot_publication(
            config.archive, inputs=[config.profile, config.source, *config.inputs]
        ) as release:
            release_guard = release.check
            config.package_check(release.archive, config.source)
            release.check()
            summary = finish(result)
            release.check()
            return summary
    for path in (config.archive, config.receipt, config.journal):
        if path.resolve().is_relative_to(config.source.resolve()):
            raise ValueError("Publication outputs must stay outside the source report")
    with snapshot_publication(
        config.archive, inputs=[config.profile, config.source, *config.inputs]
    ) as release:
        release_guard = release.check
        config.package_check(release.archive, config.source)
        release.check()
        original_send = send

        def guarded_send(*args, **kwargs):
            if '/commit' in args[0] or '/versions' in args[0]:
                release.verify()
            return original_send(*args, **kwargs)

        send = guarded_send
        identity = {
            "schema": config.namespace + "_publication_operation/1",
            "base_url": base,
            "archive_sha256": release.sha256,
            "title": config.title,
            "kind": config.mode,
            "slug": None if config.mode == "create" else result["slug"],
            "expected_version": expected,
        }
        operations = PublicationOperation(config.journal, config.namespace + "-")
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
                body, content_type = release_multipart(
                    release.archive, config.title, release.sha256, config.upload_name
                )
                endpoint = (
                    "/api/sites"
                    if config.mode == "create"
                    else "/api/sites/"
                    + quote(result["slug"], safe="")
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


# Bound lifecycle


@dataclass(frozen=True)
class BoundTrack:
    """A report whose verified publication advances its profile and attestation."""

    label: str
    default_title: str
    archive: Path
    profile: Path
    receipt: Path
    operation_journal: Path
    inputs: tuple
    operation_prefix: str
    operation_schema: str
    version_field: str
    request: Callable  # (base, path, data, content_type, *, method, extra_headers)
    public_request: Callable  # (base, path, *, extra_headers), never authenticated
    multipart: Callable  # (title, archive) -> (body, content type)
    validate_package: Callable
    check_preview: Callable  # (status, body)
    recover_binding: Callable
    bind: Callable  # (profile, *, slug, share_url, version, release)
    receipt_required: bool = False
    update_title: bool = False
    staged_key_prefix: str | None = None


def operation_identity(
    schema: str, *, base: str, title: str, archive_sha256: str, slug: str = "",
    expected_version: str = "",
) -> dict:
    return {
        "schema": schema,
        "base_url": base,
        "archive_sha256": archive_sha256,
        "title": title,
        "kind": "version" if slug or expected_version else "create",
        "slug": slug,
        "expected_version": expected_version,
    }


def pending_created_site(receipt_path: Path, base: str, archive_sha256: str) -> str:
    """Name a site created by an interrupted run, only for this exact release."""
    if not receipt_path.is_file():
        return ""
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("verified"):
        return ""
    if (
        receipt.get("base_url") != base
        or receipt.get("archive_sha256") != archive_sha256
        or not isinstance(receipt.get("slug"), str)
        or not receipt["slug"]
    ):
        raise ValueError("pending site receipt differs from the current release package")
    return receipt["slug"]


def staged_release_upload(
    request: Callable, archive: Path, *, title: str, slug: str | None,
    expected_version: str | None, identity: dict, key_prefix: str,
) -> bytes:
    """Upload a large archive as validated files and activate it in one commit."""

    def staged_request(path, data=None, content_type=None, *, method=None):
        headers = None
        if (
            data is not None
            and method is None
            and (path.startswith("/api/uploads?") or "/commit" in path)
        ):
            seed = json.dumps(identity, sort_keys=True, separators=(",", ":")) + "|" + path
            headers = {"Idempotency-Key": key_prefix + hashlib.sha256(seed.encode()).hexdigest()}
        status, raw, *response_headers = request(
            path, data, content_type, method=method, extra_headers=headers
        )
        if not 200 <= status < 300:
            raise HTTPError(
                path,
                status,
                raw.decode("utf-8", errors="replace")[:500],
                response_headers[0] if response_headers else {},
                BytesIO(raw),
            )
        return status, raw

    return upload_large_archive(staged_request, archive, slug, expected_version, title=title)


def publish_bound_release(track: BoundTrack, *, create: bool) -> None:
    track.recover_binding()
    with snapshot_publication(track.archive, inputs=track.inputs) as release:
        track.validate_package()
        release.check()
        _publish_bound(track, release, create=create)


def _publish_bound(track: BoundTrack, release: PublicationSnapshot, *, create: bool) -> None:
    profile = json.loads(track.profile.read_text(encoding="utf-8"))
    publication = profile.get("publication") or {}
    base = str(publication.get("base_url") or "").rstrip("/")
    bound_slug = str(publication.get("site_slug") or "")
    expected_version = str(publication.get("expected_version") or "")
    if not base.startswith("https://"):
        raise ValueError(f"current {track.label} profile has no valid publication base")
    title = str(publication.get("title") or track.default_title)
    operations = PublicationOperation(track.operation_journal, track.operation_prefix)
    identity = operation_identity(
        track.operation_schema, base=base, title=title, archive_sha256=release.sha256,
        slug=bound_slug, expected_version=expected_version,
    )

    def raw_send(path, *args, **kwargs):
        release.check()
        return track.request(base, path, *args, **kwargs)

    def send(path, *args, **kwargs):
        return raw_send(path, *args, **kwargs)[:2]

    def upload(path: str, *, slug: str | None, expected: str | None, allow_create: bool):
        if (track.staged_key_prefix is not None
                and release.archive.stat().st_size > STAGED_UPLOAD_THRESHOLD):
            if slug is None and not allow_create:
                raise ValueError("no bound site or matching pending creation operation")
            return 200, staged_release_upload(
                raw_send, release.archive, title=title, slug=slug, expected_version=expected,
                identity=identity, key_prefix=track.staged_key_prefix,
            )
        payload, content_type = track.multipart(title, release.archive)
        return operations.upload(
            path, payload, content_type, identity, allow_create=allow_create,
            request=send, decode=response_json, guard=release.check,
        )

    resume_binding = binding_receipt_matches(
        track.receipt, base=base, archive_sha256=release.sha256, slug=bound_slug,
        version=expected_version, version_field=track.version_field,
    )
    if bound_slug or expected_version:
        if create and not resume_binding:
            raise ValueError(f"{track.label} report is already bound; update it without --create")
        if not bound_slug or not expected_version.startswith("ver_"):
            raise ValueError(f"current {track.label} profile has no complete publication binding")
        if track.receipt_required and not track.receipt.exists():
            raise ValueError(f"bound {track.label} site receipt is missing")
        result = json.loads(track.receipt.read_bytes()) if track.receipt.exists() else {}
        if result and result.get("slug") != bound_slug:
            raise ValueError(f"site receipt differs from the bound {track.label} site")
        slug = bound_slug
        status, raw = send(f"/api/sites/{quote(slug)}")
        remote_version = site_version(response_json(status, raw, operation="site lookup"))
        if remote_version != expected_version or resume_binding:
            # A previous upload may have committed before a post-upload check failed.
            # Resume only when the exported tree proves that the remote version is
            # byte-identical to the current package.
            verify_export(send, slug, release.archive)
        else:
            query = urllib.parse.urlencode({"expected_version": expected_version})
            status, raw = upload(
                f"/api/sites/{quote(slug)}/versions?{query}",
                slug=slug, expected=expected_version, allow_create=True,
            )
            result.update(response_json(status, raw, operation="site version upload"))
        share_url = str(publication.get("share_url") or result.get("share_url") or "")
    else:
        slug = pending_created_site(track.receipt, base, release.sha256)
        if slug:
            result = json.loads(track.receipt.read_bytes())
            if track.operation_journal.exists():
                operations.pending(identity, allow_create=False)
            verify_export(send, slug, release.archive)
        else:
            status, raw = upload("/api/sites", slug=None, expected=None, allow_create=create)
            result = response_json(status, raw, operation="site creation")
            slug = str(result.get("slug") or "")
            if not slug:
                raise RuntimeError("site creation response omitted slug")
            result.update(base_url=base, archive_sha256=release.sha256, verified=False)
            write_receipt(track.receipt, result)
        share_url = str(result.get("share_url") or "")
    site_path = f"/api/sites/{quote(slug)}"
    if not share_url:
        status, raw = send(
            site_path + "/shares", json.dumps({"policy": "public"}).encode(), "application/json"
        )
        share_url = str(response_json(status, raw, operation="public share creation").get("url") or "")
        if not share_url.startswith("https://"):
            raise RuntimeError("share creation response omitted public URL")
    status, raw = send(site_path)
    version = site_version(response_json(status, raw, operation="site lookup after upload"))
    if track.update_title:
        status, raw = send(site_path, json.dumps({"title": title}).encode(), "application/json",
                           method="PATCH")
        if not 200 <= status < 300:
            response_json(status, raw, operation="site title update")
        result["title"] = title
    verify_export(send, slug, release.archive)
    share_path, share_token = public_share_context(base, share_url)
    release.check()
    status, _ = track.public_request(base, share_path)[:2]
    if status != 200:
        raise RuntimeError(f"public report returned HTTP {status}")
    release.check()
    status, body = track.public_request(
        base, f"/api/preview/{quote(slug)}", extra_headers={"X-Artifact-Share": share_token}
    )[:2]
    track.check_preview(status, body)
    release.check()
    track.validate_package()
    release.check()
    status, raw = send(site_path)
    if site_version(response_json(status, raw, operation="final site lookup")) != version:
        raise ValueError("remote version changed before publication binding")
    settled = {key: value for key, value in result.items()
               if key not in {"phase", "version", "versionId", "version_id", "currentVersionId"}}
    settled.update({"base_url": base, "slug": slug, "share_url": share_url,
                    track.version_field: version, "archive_sha256": release.sha256})
    write_receipt(track.receipt, {**settled, "verified": False, "phase": "binding"})
    operations.clear(identity)
    track.bind(profile, slug=slug, share_url=share_url, version=version, release=release)
    write_receipt(track.receipt, {**settled, "verified": True})
    print(json.dumps({"slug": slug, "url": share_url, "version": version}, ensure_ascii=False))


# Staged update lifecycle


@dataclass(frozen=True)
class StagedUpdate:
    """One release of a bound site whose flat profile the report advances itself."""

    label: str
    prefix: str
    client: PublicationClient
    base: str
    slug: str
    expected_version: str
    share_path: str
    title: str
    archive: Path
    archive_sha256: str
    inputs: tuple
    work: Path
    guard: Callable
    bind: Callable  # (version) inside the frozen release
    allow_inline: bool = False
    inline_limit_bytes: int | None = None


def publish_staged_update(update: StagedUpdate, *, transport=None) -> str:
    """Resume the same staged update and verify every deployed byte before binding it."""
    inline_limit = update.inline_limit_bytes
    if inline_limit is not None and (type(inline_limit) is not int or inline_limit <= 0):
        raise ValueError('Inline upload limit must be a positive byte count')
    if inline_limit is None:
        inline_limit = STAGED_UPLOAD_THRESHOLD
    label, slug = update.label, update.slug
    identity = {'base_url': update.base, 'slug': slug, 'archive_sha256': update.archive_sha256}
    # The release identifies the operation; its mutable live version is a guard.
    operation_hash = hashlib.sha256(json_bytes(identity)).hexdigest()
    journal = update.work / f'{update.prefix}-update-{operation_hash[:24]}.json'
    reject_evidence_symlinks(journal)
    resumed = journal.exists()
    operation = (json.loads(journal.read_bytes()) if resumed
                 else {**identity, 'expected_version': update.expected_version})
    if any(operation.get(key) != value for key, value in identity.items()):
        raise ValueError(f'{label} publication journal targets another release')
    if update.expected_version not in {operation.get('expected_version'), operation.get('versionId')}:
        raise ValueError(f'{label} publication version differs from its pending operation')
    expected = operation['expected_version']

    def save():
        atomic_write(journal, json_bytes(operation))
        journal.chmod(0o600)

    save()
    with snapshot_publication(update.archive, inputs=update.inputs) as release:
        with zipfile.ZipFile(release.archive) as bundle:
            _archive_entries(bundle)
        if 'transport' not in operation:
            # Existing journals may already own staged bytes. Never switch them.
            operation['transport'] = ('inline' if update.allow_inline and not resumed and
                                      release.archive.stat().st_size <= inline_limit
                                      else 'staged')
            save()
        if operation['transport'] not in {'inline', 'staged'}:
            raise ValueError(f'{label} publication transport changed')
        commit_key = f'{update.prefix}-{"inline" if operation["transport"] == "inline" else "commit"}-{operation_hash[:32]}'
        def request(path, data=None, content_type=None, auth=True, method=None):
            release.check()
            update.guard()
            if not path.startswith('/') or path.startswith('//'):
                raise ValueError(f'{label} request must stay on its configured service')
            extra_headers = {}
            if path.startswith('/api/uploads') and method != 'PUT' and data is not None:
                phase = 'commit' if '/commit?' in path else 'start'
                extra_headers['Idempotency-Key'] = f'{update.prefix}-{phase}-{operation_hash[:32]}'
            elif path.startswith('/api/sites/') and '/versions?' in path and data is not None:
                extra_headers['Idempotency-Key'] = commit_key
            if transport is not None:
                # Synthetic transports never load a real account credential.
                headers = update.client.headers(update.base, content_type, authenticated=False,
                                                extra_headers=extra_headers)
                status, raw = transport(path, data, content_type, auth=auth, method=method, headers=headers)
            else:
                status, raw, _ = update.client.request(update.base, path, data, content_type, method=method,
                                                       authenticated=auth, extra_headers=extra_headers)
            release.check()
            if status == 404 and path.startswith('/api/operations/'):
                return status, raw
            if not 200 <= status < 300:
                raise RuntimeError(f'{label} publication HTTP {status}: {path}')
            return status, raw

        site_path = '/api/sites/' + quote(slug, safe='')
        started = time.perf_counter()
        print(f'Preparing {label} remote version ({operation["transport"]})...', flush=True)
        _, raw = request(site_path)
        remote = json.loads(raw)
        version = operation.get('versionId')
        if version is None:
            if remote['version']['id'] != expected:
                recovered = PublicationOperation(journal, update.prefix + '-').completed(
                    commit_key, request=request, decode=response_json,
                )
                if recovered is None:
                    raise ValueError(f'Remote {label} version changed; reconcile before updating')
                response = response_json(*recovered, operation=f'{label} commit recovery')
            else:
                recovered = None
                if resumed and operation['transport'] == 'inline':
                    recovered = PublicationOperation(journal, update.prefix + '-').completed(
                        commit_key, request=request, decode=response_json)
                if recovered is not None:
                    response = response_json(*recovered, operation=f'{label} inline recovery')
                elif operation['transport'] == 'inline':
                    body, content_type = multipart_archive(
                        release.archive, update.title, boundary_prefix=update.prefix + '-',
                        filename='report.zip')
                    response = response_json(*request(
                        site_path + '/versions?expected_version=' + quote(expected, safe=''),
                        body, content_type), operation=f'{label} inline upload')
                else:
                    response = json.loads(upload_large_archive(request, release.archive, slug, expected, max_workers=4, resume=resumed))
            version = response.get('versionId')
            if (response.get('slug', slug) != slug or not isinstance(version, str)
                    or not version.startswith('ver_')):
                raise ValueError(f'{label} upload returned another site or an invalid version')
            operation['versionId'] = version
            save()
        elif not isinstance(version, str) or not version.startswith('ver_'):
            raise ValueError(f'{label} journal has an invalid activated version')
        print(f'{label} remote version ready in {time.perf_counter() - started:.1f}s', flush=True)
        started = time.perf_counter()
        print(f'Verifying deployed {label} files...', flush=True)
        _, raw = request(site_path)
        published = json.loads(raw)
        if published['version']['id'] != version or published['version']['entry'] != 'index.html':
            raise ValueError(f'Published {label} site does not bind the activated home page')
        _, exported = request(site_path + '/export')
        if archive_inventory(exported) != archive_inventory(release.archive):
            raise ValueError(f'Deployed {label} bytes differ from the validated package')
        _, shared = request(update.share_path, auth=False)
        if not shared:
            raise ValueError(f'{label} public share page is empty')
        request(site_path, json.dumps({'title': update.title}, ensure_ascii=False).encode(),
                'application/json', method='PATCH')
        release.check()
        update.bind(version)
        print(f'{label} deployed files verified in {time.perf_counter() - started:.1f}s', flush=True)
    operation['verified'] = True
    save()
    return version
