"""Verify reconstructed public files, scored bytes and explicit redaction hashes."""

import argparse
import gzip
import hashlib
import json
import zipfile
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from bs4 import BeautifulSoup
from lexbench_webfetch.paths import contained_path

from harness.record_io import parse_jsonl
from harness.reporting.links import validate_virtual_links
from harness.reporting.packs import (
    DecompressedPackCache,
    read_packed_manifest,
    reconstruct_packed_file,
)
from harness.reporting.publication import archive_inventory
from harness.web_fetch.publication_privacy import (
    bound_public_view,
    project_file,
    project_redundant_result_streams,
    publication_file_risk,
    publication_risk,
)
from harness.web_fetch.report_package import DATA_ATTACHMENT_HTML, validate_release
from harness.web_fetch.safety import evidence_tree_identity
from harness.web_fetch.webmainbench_diagnostics import (
    require_valid_teds_for_release,
    validate_teds_revision_if_present,
)
from harness.web_fetch.webmainbench_latency_correction import validate_latency_correction


def scored_output_hash(row: dict) -> str:
    expected = row.get("output_sha256") or row.get("metrics", {}).get("content_sha256")
    if not expected:
        raise ValueError("Record missing scored output hash: " + str(row.get("artifact_path")))
    return expected


def check(archive, source, overlay=None, *, presentation_binding=None, external_archives=None, allow_pending_external=False, snapshot_audit=False):
    validate_release(source, presentation_binding=presentation_binding, snapshot_audit=snapshot_audit)
    source_files = evidence_tree_identity(source)
    physical = archive_inventory(archive)
    if overlay:
        overlay_files = evidence_tree_identity(overlay)
        if not {"manifest.json"} <= set(overlay_files) or set(overlay_files) - {"manifest.json", "loader.js", "update-receipt.json"}:
            raise ValueError("Incremental overlay contains undeclared physical files")
    with ExitStack() as stack:
        bundle = stack.enter_context(zipfile.ZipFile(archive))
        attachments = {label:stack.enter_context(zipfile.ZipFile(path))
                       for label,path in (external_archives or {}).items()}
        external = {}
        def read(name):
            if name in external:
                return attachments[external[name]['part']].read(name)
            patched = overlay / name if overlay else None
            return patched.read_bytes() if patched and patched.is_file() else bundle.read(name)
        shell = BeautifulSoup(read("index.html"), "html.parser")
        shell_assets = [
            node.get("src") or node.get("href")
            for node in shell.select('script[src],link[rel="stylesheet"][href]')
        ]
        if shell_assets != ["style.css", "report.js", "loader.js"]:
            raise ValueError("Publication shell must use exact query-free physical asset paths")
        manifest = read_packed_manifest(read)

        external = manifest.get('external_packs',{})
        if set(attachments) != {item['part'] for item in external.values()}:
            raise ValueError('Publication data attachment inventory differs')
        for name,item in external.items():
            raw = read(name)
            if len(raw) != item['bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise ValueError('Data attachment pack differs: '+name)
            url = item.get('url')
            if not url and allow_pending_external:
                continue
            if not isinstance(url,str):
                raise ValueError('Data attachment is not bound to a published version')
            parsed = urlsplit(url)
            expected = '/api/preview/'+quote(item.get('site_slug',''),safe='')+'/'+name
            if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment
                or parsed.path != expected
                or parse_qs(parsed.query) != {'__artifact_version':[item.get('version_id')]}
                or not str(item.get('version_id','')).startswith('ver_')):
                raise ValueError('Data attachment lacks an exact immutable version URL')
        for label,attachment in attachments.items():
            wanted = {name for name,item in external.items() if item['part'] == label}
            inventory = archive_inventory((external_archives or {})[label])
            if set(inventory) != wanted|{'index.html'} or attachment.read('index.html') != DATA_ATTACHMENT_HTML:
                raise ValueError('Data attachment contains an undeclared file')
        for candidate in [bundle,*attachments.values()]:
            entries = [entry for entry in candidate.infolist() if not entry.is_dir()]
            if (sum(entry.file_size for entry in entries) > 300*1024*1024
                or len(entries) > 2000 or any(entry.file_size > 250*1024*1024 for entry in entries)):
                raise ValueError('One artifact exceeds the publication capacity')

        baseline = read_packed_manifest(bundle.read) if overlay else manifest
        if overlay and (manifest["blocks"] != baseline["blocks"] or set(manifest["files"]) != set(baseline["files"])):
            raise ValueError("Incremental overlay changed the frozen pack or file inventory")
        if overlay:
            from harness.web_fetch.report_package import explanatory_route
            for name, entry in manifest["files"].items():
                if entry != baseline["files"][name] and not (explanatory_route(name) or name == "publication-privacy.json"):
                    raise ValueError("Incremental overlay changed measured evidence: " + name)

        pack = stack.enter_context(DecompressedPackCache(read))

        used = {key for row in manifest["files"].values() for key in row.get("parts", [])}
        if not overlay and used != set(manifest["blocks"]):
            raise ValueError("Publication contains unreferenced content blocks")
        packs = {block["pack"] for block in manifest["blocks"].values()}
        catalog_files = ({"manifest.json.gz", "catalog.bin.gz"} if manifest['schema_version'] == 3 else set())
        if (
            set(physical)
            != {"index.html", "style.css", "report.js", "loader.js", "manifest.json"} | (packs-set(external)) | catalog_files
        ):
            raise ValueError("Publication contains an undeclared physical file")
        for name in set(physical) - packs:
            if name == "loader.js":
                loader = Path(__file__).parent / "assets/report-loader.js"
                if read(name) != loader.read_bytes():
                    raise ValueError("Publication loader differs from the installed resource")
                continue
            if name == 'catalog.bin.gz':
                continue  # Typed integer catalog; its complete ranges are checked below.
            raw = gzip.decompress(read(name)) if name == 'manifest.json.gz' else read(name)
            if publication_risk(raw):
                raise ValueError("Potential sensitive data remains in physical file: " + name)
        grouped_blocks = {}
        if manifest['schema_version'] == 2:
            for block in manifest['blocks'].values():
                grouped_blocks.setdefault(block['pack'], []).append(block)
        for name in packs:
            entries = (manifest['blocks'].for_pack(name) if manifest['schema_version'] == 3
                       else sorted(grouped_blocks[name],key=lambda block:block['offset']))
            offset = 0
            for block in entries:
                if (
                    type(block["size"]) is not int
                    or block["size"] <= 0
                    or type(block["offset"]) is not int
                    or block["offset"] != offset
                ):
                    raise ValueError("Publication content pack has gaps or overlapping blocks")
                offset += block["size"]
            if offset != len(pack(name)):
                raise ValueError("Publication content pack contains undeclared bytes")
        expected_files = set(source_files) - {".generated-report"}
        if set(manifest["files"]) != expected_files | {"publication-privacy.json"}:
            raise ValueError("Publication virtual files differ from the source report")

        def data(name):
            return reconstruct_packed_file(manifest,name,pack)

        validate_virtual_links(manifest["files"], data)
        for name in sorted(expected_files):
            if manifest["files"][name].get("source_sha256") != source_files[name]:
                raise ValueError("Publication source hash differs or source binding is missing: " + name)
        privacy = json.loads(data("publication-privacy.json"))
        privacy_by_path = {row["path"]: row for row in privacy["files"]}
        footprint_by_path = {
            row["path"]: row for row in privacy.get("footprint_projections", [])
        }
        results_raw = data("results.jsonl")
        source_results = (source / "results.jsonl").read_bytes()
        privacy_results = project_file("results.jsonl", source_results)[0]
        expected_results, expected_footprint = project_redundant_result_streams(
            "results.jsonl", privacy_results
        )
        if results_raw != expected_results:
            raise ValueError("Published results differ from the current source evidence")
        if bool(expected_footprint) != bool(footprint_by_path):
            raise ValueError("Publication footprint disclosure differs from the public projection")
        if expected_footprint:
            receipt = footprint_by_path.get("results.jsonl")
            if (
                len(footprint_by_path) != 1
                or not receipt
                or receipt.get("original_sha256") != hashlib.sha256(source_results).hexdigest()
                or receipt.get("published_sha256") != hashlib.sha256(results_raw).hexdigest()
                or receipt.get("original_bytes") != len(source_results)
                or receipt.get("published_bytes") != len(results_raw)
                or receipt.get("omitted_fields") != expected_footprint.get("omitted_fields")
                or receipt.get("omitted_payload_bytes")
                != expected_footprint.get("omitted_payload_bytes")
            ):
                raise ValueError("Invalid publication footprint disclosure")
        rows = parse_jsonl(results_raw)
        require_valid_teds_for_release(rows)
        latency_receipt = validate_latency_correction(source)
        if latency_receipt is not None:
            for name in ("latency-corrections.json", "prior-latency-results.jsonl"):
                if name not in manifest["files"] or (
                    data(name) != (source / name).read_bytes() and name not in privacy_by_path
                ):
                    raise ValueError(
                        "Published latency correction history is missing or changed: " + name
                    )
        revision = validate_teds_revision_if_present(source, rows)
        if revision is not None:
            for name, label in (
                ("index.html", "修正后复算"),
                ("en/index.html", "recomputed after correction"),
            ):
                if name not in manifest["files"] or label not in BeautifulSoup(
                    data(name), "html.parser"
                ).get_text(" ", strip=True):
                    raise ValueError(
                        "Published corrected scores lack a localized correction label: " + name
                    )
            bound_files = [
                "teds-correction.json",
                "teds-correction-review.json",
                "original-results.jsonl",
            ]
            bound_files += [
                "correction-source/" + name for name in revision[0]["source_code_sha256"]
            ]
            for name in bound_files:
                if name not in manifest["files"]:
                    raise ValueError("Published scoring correction evidence missing: " + name)
                if data(name) != (source / name).read_bytes() and name not in privacy_by_path:
                    raise ValueError("Undisclosed scoring correction evidence projection: " + name)
        if (source / "scoring-correction.json").is_file():
            bound_files = [
                "scoring-correction.json",
            ]
            bound_files += [
                path.relative_to(source).as_posix()
                for path in sorted((source / "scoring-correction-source").rglob("*"))
                if path.is_file()
            ]
            for name in bound_files:
                if name not in manifest["files"]:
                    raise ValueError(
                        "Published shared-population correction evidence missing: " + name
                    )
                if data(name) != (source / name).read_bytes() and name not in privacy_by_path:
                    raise ValueError("Undisclosed shared-population correction projection: " + name)
        projected_scored = 0
        retained_public_views = 0
        for row in rows:
            raw = data(row["artifact_path"])
            original = contained_path(
                source, row["artifact_path"], reject_symlinks=True
            ).read_bytes()
            expected_hash = scored_output_hash(row)
            retained_view = False
            if hashlib.sha256(original).hexdigest() != expected_hash:
                binding={'path':row['artifact_path'],'sha256':expected_hash}
                public = bound_public_view(source,binding)
                if public is None and snapshot_audit:
                    from harness.web_fetch.published_snapshot import read_snapshot_public_file
                    retained=read_snapshot_public_file(source,binding)
                    public=(retained,{'role':'scored_answer','published_sha256':hashlib.sha256(original).hexdigest()})
                if (public is None or public[1]['role'] != 'scored_answer'
                    or public[1]['published_sha256'] != hashlib.sha256(original).hexdigest()):
                    raise ValueError("Original scored output hash differs")
                retained_view = True
                retained_public_views += 1
            disclosure = privacy_by_path.get(row["artifact_path"])
            if disclosure:
                if disclosure.get("scored_output") is not True:
                    raise ValueError("Scored projection is not disclosed")
            elif raw != original:
                raise ValueError("Undisclosed scored output projection")
            projected_scored += bool(disclosure or retained_view)
        for row in privacy["files"]:
            if (
                hashlib.sha256(
                    contained_path(source, row["path"], reject_symlinks=True).read_bytes()
                ).hexdigest()
                != row["original_sha256"]
            ):
                raise ValueError("Original privacy hash differs")
            if hashlib.sha256(data(row["path"])).hexdigest() != row["published_sha256"]:
                raise ValueError("Projected privacy hash differs")
        standalone = "not-present"
        if "guide/article.html" in manifest["files"]:
            article = BeautifulSoup(data("guide/article.html"), "html.parser")
            if article.select('script,link[rel="stylesheet"]'):
                raise ValueError("Standalone article has runtime dependencies")
            for anchor in article.select("a[href]"):
                figure = anchor.find_parent("figure", class_="m-illustration")
                image = figure.find("img") if figure else None
                if (
                    image
                    and anchor["href"].startswith("data:image/png;base64,")
                    and anchor["href"] == image.get("src")
                ):
                    continue
                if not anchor["href"].startswith(("https://", "http://", "#")):
                    raise ValueError("Standalone article has a local evidence link")
            standalone = "pass"
        print('Source/result bindings checked; scanning all public files',flush=True)
        for index,(name,row) in enumerate(manifest["files"].items(),1):
            raw = data(name)
            if len(raw) != row["size"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
                raise ValueError("Published file hash differs: " + name)
            risk = publication_file_risk(name,raw)
            if risk:
                raise ValueError("Potential " + risk + " remains: " + name)
            if index % 1000 == 0:
                print(f'Verified {index}/{len(manifest["files"])} public files',flush=True)
        print(
            json.dumps(
                {
                    "files": len(manifest["files"]),
                    "scored_outputs_byte_identical": len(rows) - projected_scored,
                    "scored_outputs_privacy_projected": projected_scored,
                    "scored_outputs_retained_public_views": retained_public_views,
                    "redacted_public_files": len(privacy["files"]),
                    "replacements": sum(r["replacements"] for r in privacy["files"]),
                    "public_sensitive_data_scan": "pass",
                    "standalone_article": standalone,
                },
                ensure_ascii=False,
            )
        )
    if evidence_tree_identity(source) != source_files:
        raise ValueError("Publication source changed during bundle verification")


def main(*, root: Path | None = None):
    root = root or Path.cwd()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "archive", nargs="?", type=Path, default=root / "publish/webfetch-sites.zip"
    )
    parser.add_argument("source", nargs="?", type=Path, default=root / "reports/webfetch")
    parser.add_argument("overlay", nargs="?", type=Path)
    parser.add_argument("--presentation-binding", type=Path)
    parser.add_argument("--snapshot-audit",action="store_true")
    args = parser.parse_args()
    check(args.archive, args.source, args.overlay, presentation_binding=args.presentation_binding,snapshot_audit=args.snapshot_audit)
