"""Read the existing report export and optionally verify exact uploaded files."""

import argparse
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote

from harness.reporting.publication import archive_inventory
from harness.web_fetch.artifact_auth import load_token
from harness.web_fetch.publication_target import publication_base, validate_service_base


def _validate_metadata(data, slug, *, require_slug=False):
    if not isinstance(data, dict) or data.get("slug", None if require_slug else slug) != slug:
        raise ValueError("Site metadata identifies a different site")
    version_field = data.get("version")
    version = version_field.get("id") if isinstance(version_field, dict) else None
    if not isinstance(version, str) or not version.startswith("ver_"):
        raise ValueError("Site metadata omitted the committed version id")
    files = data.get("files")
    if (
        not isinstance(files, list)
        or not files
        or not all(isinstance(name, str) for name in files)
        or len(set(files)) != len(files)
    ):
        raise ValueError("Site metadata omitted the committed file inventory")
    return version


def verify_metadata(slug, base):
    """Check the committed site record without downloading its published tree."""
    base = validate_service_base(base)
    endpoint = base + "/api/sites/" + quote(slug, safe="")
    request = urllib.request.Request(
        endpoint,
        headers={"Authorization": "Bearer " + load_token(base_url=base)},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.load(response)
    version = _validate_metadata(data, slug, require_slug=True)
    files = data.get("files")
    return {
        "slug": data.get("slug"),
        "version": version,
        "files": len(files),
        "kind": data.get("kind"),
        "method": "authenticated site metadata only; no report files downloaded",
    }


def verify_public_files(slug, expected_archive, base):
    base = validate_service_base(base)
    endpoint = base + "/api/sites/" + quote(slug, safe="")

    def metadata():
        request = urllib.request.Request(
            endpoint, headers={"Authorization": "Bearer " + load_token(base_url=base)}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.load(response)
        _validate_metadata(data, slug)
        return data

    wanted = archive_inventory(expected_archive)
    before = metadata()
    with zipfile.ZipFile(expected_archive) as expected:
        expected_html = {}
        for info in expected.infolist():
            if info.is_dir():
                continue
            raw = expected.read(info)
            if info.filename.endswith(".html"):
                expected_html[info.filename] = raw
    if set(before["files"]) != set(wanted):
        raise ValueError("Remote file set differs")

    entry = before.get("file")
    authenticated_entry = False
    if entry in wanted and isinstance(before.get("content"), str):
        original = before["content"].encode()
        if hashlib.sha256(original).hexdigest() != wanted[entry]:
            raise ValueError("Authenticated original entry differs: " + entry)
        authenticated_entry = True

    def injected_html_matches(name, served):
        """Accept only an exact submitted document plus a known Hub bootstrap."""
        original = expected_html[name]
        quoted_slug = re.escape(quote(slug, safe="").encode())
        # Newer deployments pin the injected base to the immutable served
        # version (``slug~opaque-version-token``). Keep this deliberately
        # narrow: the site slug must still match exactly and the suffix may
        # contain URL-path-safe token characters only.
        base_pattern = re.compile(
            rb'^<base href="/api/preview/' + quoted_slug + rb'(?:~[A-Za-z0-9._-]+)?/">'
        )

        def known_injection(injected):
            base_match = base_pattern.match(injected)
            if not base_match:
                return False
            bootstrap = injected[base_match.end() :]
            # The service may add one or more bootstrap scripts. Do not normalize
            # or ignore arbitrary HTML: every injected byte must be one such script.
            while bootstrap:
                opening = b"<script data-artifact-bootstrap>"
                if not bootstrap.startswith(opening):
                    return False
                end = bootstrap.find(b"</script>", len(opening))
                if end < 0:
                    return False
                bootstrap = bootstrap[end + len(b"</script>") :]
            return True

        # Older deployments prepend the bootstrap to the submitted document.
        if served.endswith(original):
            injected = served[: -len(original)] if original else served
            if known_injection(injected):
                return True

        # Current deployments insert it immediately after the literal <head> so
        # the submitted doctype and document element remain at the start.
        head_end = original.find(b"<head>")
        if head_end < 0:
            return False
        head_end += len(b"<head>")
        prefix, suffix = original[:head_end], original[head_end:]
        if not served.startswith(prefix):
            return False
        if suffix and not served.endswith(suffix):
            return False
        injected = served[len(prefix) : len(served) - len(suffix) if suffix else None]
        return known_injection(injected)

    def digest(name):
        url = base + "/api/preview/" + quote(slug, safe="") + "/" + quote(name, safe="/")
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=180) as response:
                    if name.endswith(".html"):
                        served = response.read()
                        digest = hashlib.sha256(served).hexdigest()
                        if digest == wanted[name]:
                            return name, digest, "byte_identical"
                        if injected_html_matches(name, served):
                            return name, wanted[name], "injected_html"
                        return name, digest, "different"
                    digest = hashlib.sha256()
                    while chunk := response.read(1024 * 1024):
                        digest.update(chunk)
                return name, digest.hexdigest(), "byte_identical"
            except HTTPError as error:
                retryable = error.code >= 500
                error.close()
                if not retryable or attempt == 2:
                    raise
            except (URLError, TimeoutError, IncompleteRead):
                if attempt == 2:
                    raise
            time.sleep(2**attempt)

    with ThreadPoolExecutor(max_workers=2) as pool:
        actual = {}
        verification = {}
        for name, sha, layer in pool.map(digest, wanted):
            actual[name] = sha
            if sha != wanted[name]:
                raise ValueError("Remote file differs: " + name)
            verification[name] = layer
            if len(actual) % 20 == 0:
                print(f"Verified {len(actual)}/{len(wanted)} public files", flush=True)
    after = metadata()
    if before["version"]["id"] != after["version"]["id"]:
        raise ValueError("Remote version changed during verification")
    exact = sum(layer == "byte_identical" for layer in verification.values())
    injected = sum(layer == "injected_html" for layer in verification.values())
    return {
        "version": after["version"]["id"],
        "files": len(actual),
        "anonymous_byte_identical_files": exact,
        "anonymous_injected_html_files": injected,
        "anonymous_all_files_byte_identical": exact == len(actual),
        "authenticated_original_entry_verified": authenticated_entry,
        "submitted_bytes_verified_by_layer": exact + injected == len(actual),
        "method": (
            "anonymous_file_bytes; injected HTML requires an exact submitted "
            "document around validated Artifact Hub bootstrap scripts; authenticated "
            "metadata verifies the original entry"
        ),
    }


def download_export(request, destination, attempts=3):
    """Download one immutable site export, retrying transient gateway/read failures."""
    for attempt in range(attempts):
        path = None
        complete = False
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                version = response.headers.get("x-artifact-version")
                if not version or not version.startswith("ver_"):
                    raise ValueError("Export omitted version baseline")
                with tempfile.NamedTemporaryFile(
                    dir=destination, prefix="remote-export-", suffix=".zip", delete=False
                ) as stream:
                    path = Path(stream.name)
                    while chunk := response.read(1024 * 1024):
                        stream.write(chunk)
            complete = True
            return version, path
        except HTTPError as error:
            retryable = error.code >= 500
            error.close()
            if not retryable or attempt == attempts - 1:
                raise
        except (URLError, TimeoutError, IncompleteRead):
            if attempt == attempts - 1:
                raise
        finally:
            if path is not None and not complete:
                path.unlink(missing_ok=True)
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


def main(*, root: Path | None = None):
    root = root or Path.cwd()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-archive", type=Path)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--profile", type=Path, default=root / "evaluation/current-report.json")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("ARTIFACT_SITE_BASE_URL", os.environ.get("ARTIFACT_HUB_BASE_URL")),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--export",
        action="store_true",
        help="Diagnostic only: download and verify the complete site export",
    )
    mode.add_argument(
        "--public-files",
        action="store_true",
        help="Diagnostic only: download and verify every served file",
    )
    args = parser.parse_args()
    base = validate_service_base(args.base_url) if args.base_url else publication_base(args.profile)
    if args.public_files:
        if not args.expected_archive:
            parser.error("--public-files requires --expected-archive")
        print(json.dumps(verify_public_files(args.slug, args.expected_archive, base)), flush=True)
        return
    if not args.export:
        if args.expected_archive:
            parser.error("--expected-archive requires --export or --public-files")
        print(json.dumps(verify_metadata(args.slug, base)), flush=True)
        return
    request = urllib.request.Request(
        base + "/api/sites/" + quote(args.slug, safe="") + "/export",
        headers={"Authorization": "Bearer " + load_token(base_url=base)},
    )
    (root / "publish").mkdir(exist_ok=True)
    version, path = download_export(request, root / "publish")
    actual = archive_inventory(path)
    if args.expected_archive and actual != archive_inventory(args.expected_archive):
        raise ValueError("Remote export differs from submitted archive")
    print(
        json.dumps(
            {
                "version": version,
                "export_path": str(path),
                "files": len(actual),
                "submitted_bytes_verified": bool(args.expected_archive),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
