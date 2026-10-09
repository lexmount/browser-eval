"""Verify reconstructed public files, scored bytes and explicit redaction hashes."""

import argparse
import base64
import gzip
import hashlib
import json
import zipfile
from functools import lru_cache
from pathlib import Path

from bs4 import BeautifulSoup

from harness.record_io import parse_jsonl
from harness.reporting.publication import archive_inventory
from harness.web_fetch.publication_privacy import project_file, publication_risk
from harness.web_fetch.safety import contained_path, evidence_tree_identity
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


def check(archive, source, overlay=None):
    from .report_package import validate_release
    validate_release(source)
    source_files = evidence_tree_identity(source)
    physical = archive_inventory(archive)
    with zipfile.ZipFile(archive) as bundle:
        def read(name):
            patched = overlay / name if overlay else None
            return patched.read_bytes() if patched and patched.is_file() else bundle.read(name)
        shell = BeautifulSoup(read("index.html"), "html.parser")
        shell_assets = [
            node.get("src") or node.get("href")
            for node in shell.select('script[src],link[rel="stylesheet"][href]')
        ]
        if shell_assets != ["style.css", "report.js", "loader.js"]:
            raise ValueError("Publication shell must use exact query-free physical asset paths")
        manifest = json.loads(read("manifest.json"))

        @lru_cache(maxsize=8)
        def pack(name):
            return gzip.decompress(read(name))

        used = {key for row in manifest["files"].values() for key in row.get("parts", [])}
        if not overlay and used != set(manifest["blocks"]):
            raise ValueError("Publication contains unreferenced content blocks")
        packs = {block["pack"] for block in manifest["blocks"].values()}
        if (
            set(physical)
            != {"index.html", "style.css", "report.js", "loader.js", "manifest.json"} | packs
        ):
            raise ValueError("Publication contains an undeclared physical file")
        for name in set(physical) - packs:
            if name == "loader.js":
                loader = Path(__file__).parent / "assets/report-loader.js"
                if read(name) != loader.read_bytes():
                    raise ValueError("Publication loader differs from the installed resource")
                continue
            if publication_risk(read(name)):
                raise ValueError("Potential sensitive data remains in physical file: " + name)
        for name in packs:
            entries = sorted(
                (block for block in manifest["blocks"].values() if block["pack"] == name),
                key=lambda block: block["offset"],
            )
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
            if "inline_base64" in manifest["files"][name]:
                return base64.b64decode(manifest["files"][name]["inline_base64"], validate=True)
            parts = []
            for digest in manifest["files"][name]["parts"]:
                block = manifest["blocks"][digest]
                part = pack(block["pack"])[block["offset"] : block["offset"] + block["size"]]
                if hashlib.sha256(part).hexdigest() != digest:
                    raise ValueError("Invalid block hash")
                parts.append(part)
            return b"".join(parts)

        for name, row in manifest["files"].items():
            raw = data(name)
            if len(raw) != row["size"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
                raise ValueError("Published file hash differs: " + name)
            risk = publication_risk(raw)
            if risk:
                raise ValueError("Potential " + risk + " remains: " + name)
        privacy = json.loads(data("publication-privacy.json"))
        privacy_by_path = {row["path"]: row for row in privacy["files"]}
        results_raw = data("results.jsonl")
        if results_raw != project_file("results.jsonl", (source / "results.jsonl").read_bytes())[0]:
            raise ValueError("Published results differ from the current source evidence")
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
        for row in rows:
            raw = data(row["artifact_path"])
            original = contained_path(
                source, row["artifact_path"], reject_symlinks=True
            ).read_bytes()
            expected_hash = scored_output_hash(row)
            if hashlib.sha256(original).hexdigest() != expected_hash:
                raise ValueError("Original scored output hash differs")
            disclosure = privacy_by_path.get(row["artifact_path"])
            if disclosure:
                if disclosure.get("scored_output") is not True:
                    raise ValueError("Scored projection is not disclosed")
                projected_scored += 1
            elif raw != original:
                raise ValueError("Undisclosed scored output projection")
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
        print(
            json.dumps(
                {
                    "files": len(manifest["files"]),
                    "scored_outputs_byte_identical": len(rows) - projected_scored,
                    "scored_outputs_privacy_projected": projected_scored,
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
    args = parser.parse_args()
    check(args.archive, args.source, args.overlay)
