"""Build the lazy Web Fetch publication bundle from an explicit report tree."""

import argparse
import base64
from functools import lru_cache
import gzip
import hashlib
import html
import json
import mimetypes
import os
import re
import tempfile
import zipfile
from pathlib import Path

from harness.record_io import load_jsonl
from harness.reporting.packs import write_packs
from harness.reporting.links import validate_virtual_links
from harness.web_fetch.publication_privacy import project_file, project_redundant_result_streams
from harness.web_fetch.safety import evidence_tree_identity


def _require(condition, detail):
    if not condition:
        raise ValueError(str(detail))


def validate_release(source: Path) -> bool:
    dataset_path = source / "dataset.json"
    dataset = json.loads(dataset_path.read_text()) if dataset_path.exists() else {}
    from .reference_lifecycle import require_certifiable_evidence
    manifests = []
    for name in ('manifest.json', 'execution.json'):
        path = source / name
        if path.is_file():
            value = json.loads(path.read_text())
            manifests.extend(value.get('batches', [value]))
    require_certifiable_evidence(dataset, manifests)
    content_binding = dataset.get('comparison', {}).get('report_interpretation')
    if content_binding:
        from .report_explainer import current_browser_article_model
        current_browser_article_model(source, {case['id']:case for case in dataset['cases']},
                                      load_jsonl(source/'results.jsonl'), content_binding)
    sampled = dataset.get("comparison", {}).get("kind") == "webmainbench_frozen_html_to_markdown"
    if not sampled:
        reliability = json.loads((source / "reliability.json").read_text())
        if reliability.get("complete") is not True:
            raise ValueError("Refusing to package an incomplete or unjudged benchmark report")
        if dataset.get("comparison", {}).get("require_independent_result_review"):
            from .result_review import require_result_review
            require_result_review(source, dataset, load_jsonl(source / "results.jsonl"))
        return False
    review = json.loads((source / "result-review.json").read_text())
    if review.get("status") != "passed" or review.get("failures"):
        raise ValueError("Refusing to package an incomplete or unreviewed benchmark report")
    cases = [row["id"] for row in dataset.get("cases", [])]
    providers = dataset.get("coverage_plan", {}).get("static_html", [])
    if (
        not cases
        or len(set(cases)) != len(cases)
        or not providers
        or len(set(providers)) != len(providers)
        or review.get("sample_case_count") != len(cases)
        or set(review.get("providers", [])) != set(providers)
    ):
        raise ValueError("Report/review scope differs from the declared matrix")
    records = load_jsonl(source / "results.jsonl")
    expected = {(provider, case) for provider in providers for case in cases}
    if (
        len(records) != len(expected)
        or {(row.get("provider"), row.get("case_id")) for row in records} != expected
    ):
        raise ValueError("Result matrix differs from the reviewed scope")
    binding = dataset.get("sample_manifest_sha256")
    if not binding or binding != review.get("sample_manifest_sha256"):
        raise ValueError("Sample/review binding mismatch")
    return True


def _build(source: Path, destination: Path, archive: Path, *, webmainbench: bool):
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "data").mkdir(exist_ok=True)
    records = load_jsonl(source / "results.jsonl")
    by_attempt = {r["attempt_id"]: (i, r) for i, r in enumerate(records)}
    scored_paths = {r["artifact_path"] for r in records}
    files = {}
    blocks = {}
    raw_blocks = {}
    original = {}
    pages = {}
    redactions = []
    footprint_projections = []
    source_paths = sorted(path for path in source.rglob("*") if path.is_file())
    source_file_count = sum(path.name != ".generated-report" for path in source_paths)
    logical_route_count = sum(path.suffix == ".html" for path in source_paths)

    def add(path, data, *, source_sha256=None):
        parts = []
        for start in range(0, len(data), 65536):
            chunk = data[start : start + 65536]
            key = hashlib.sha256(chunk).hexdigest()
            raw_blocks.setdefault(key, chunk)
            parts.append(key)
        files[path] = {
            "parts": parts,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "mime": mimetypes.guess_type(path)[0] or "application/octet-stream",
        }
        if source_sha256 is not None:
            files[path]["source_sha256"] = source_sha256

    for path in source_paths:
        name = path.relative_to(source).as_posix()
        data = path.read_bytes()
        if name == ".generated-report":
            continue
        if name == "publication-privacy.json":
            raise ValueError("Publication source contains a reserved generated file: " + name)
        source_sha256 = hashlib.sha256(data).hexdigest()
        if path.is_symlink():
            raise ValueError("Publication source must not contain symlinks: " + name)
        data, redaction = project_file(name, data)
        data, footprint_projection = project_redundant_result_streams(name, data)
        if footprint_projection:
            footprint_projections.append(footprint_projection)
        if redaction:
            if name in scored_paths:
                redaction["scored_output"] = True
        if path.suffix == ".html":
            text = data.decode()
            if path.parent.name == "attempts":
                idx, record = by_attempt[path.stem]
                kinds = (
                    "reference",
                    "contract",
                    "output",
                    "metadata",
                    "call-output",
                    "call-metadata",
                    "call-log",
                )
                matches = list(
                    re.finditer(
                        r'<pre data-kind="(' + ("|".join(kinds)) + r')">(.*?)</pre>', text, re.S
                    )
                )
                by_kind = {kind: [match for match in matches if match[1] == kind] for kind in kinds}
                source_kind = "contract" if by_kind["contract"] else "reference"
                # Text tasks show the complete source and output in <pre> blocks, which we
                # replace with lazy-loaded evidence. Direct-file tasks intentionally show a
                # structured byte/hash comparison instead, so they have neither block.
                _require(
                    all(
                        len(by_kind[kind]) <= 1
                        for kind in ("output", "call-output", "call-metadata")
                    )
                    and by_kind["metadata"],
                    (name, {key: len(value) for key, value in by_kind.items()}),
                )
                # The first source block is the complete reference/contract. Newer detail pages
                # can also show several derived body-region excerpts; keep those excerpts inline
                # instead of incorrectly replacing each one with the complete source file.
                selected = []
                if by_kind[source_kind]:
                    selected.append(by_kind[source_kind][0])
                if by_kind["output"]:
                    selected.append(by_kind["output"][0])
                # Several metadata blocks represent distinct parsed log/response fields.
                # A whole-result lazy loader cannot replace those narrower views.
                if len(by_kind["metadata"]) == 1:
                    selected.append(by_kind["metadata"][0])
                if by_kind["call-output"]:
                    selected.append(by_kind["call-output"][0])
                if by_kind["call-metadata"]:
                    selected.append(by_kind["call-metadata"][0])
                selected.extend(by_kind["call-log"])
                metadata = record.get("provider_metadata") or {}
                log_paths = []
                retry = metadata.get("retry") or {}
                if isinstance(retry, dict):
                    for attempt in retry.get("attempts", []):
                        if isinstance(attempt, dict) and isinstance(attempt.get("call_log"), dict):
                            log_paths.append(attempt["call_log"].get("path"))
                for value in (record.get("call_log"), metadata.get("call_log")):
                    if isinstance(value, dict):
                        log_paths.append(value.get("path"))
                log_paths.extend([record.get("call_log_path"), metadata.get("call_log_path")])
                log_paths = list(
                    dict.fromkeys(
                        str(value)
                        for value in log_paths
                        if value and not str(value).startswith("private_artifacts/")
                    )
                )
                # Current detail pages link to separate log viewers; older pages embedded
                # one pre block per call. Both must retain the actual public log files.
                _require(
                    all((source / path).is_file() for path in log_paths),
                    (name, "missing call log"),
                )
                _require(
                    not by_kind["call-log"] or len(log_paths) == len(by_kind["call-log"]),
                    (name, len(log_paths), len(by_kind["call-log"])),
                )
                log_replacements = {
                    match.start(): f'<pre data-file="{path}" data-readable="1"></pre>'
                    for match, path in zip(by_kind["call-log"], log_paths, strict=False)
                }
                reference_suffix = (
                    ".md"
                    if (source / "references" / f"{record['case_id']}.md").exists()
                    else ".txt"
                )
                reference_path = f"references/{record['case_id']}{reference_suffix}"
                output_pre = f'<pre data-file="{record["artifact_path"]}" data-readable="1"></pre>'
                replacements = {
                    source_kind: (
                        f'<pre data-file="contracts/{record["case_id"]}.json"></pre>'
                        if source_kind == "contract"
                        else f'<pre data-file="{reference_path}"></pre>'
                    ),
                    "output": output_pre,
                    "call-output": output_pre,
                    "metadata": f'<pre data-record="{idx + 1}"></pre>',
                    "call-metadata": f'<pre data-record="{idx + 1}"></pre>',
                }
                for match in reversed(selected):
                    replacement = (
                        log_replacements[match.start()]
                        if match[1] == "call-log"
                        else replacements[match[1]]
                    )
                    text = text[: match.start()] + replacement + text[match.end() :]
            elif path.parent.name == "datasets" and path.name != "index.html":
                suffix = ".md" if (source / "references" / f"{path.stem}.md").exists() else ".txt"
                reference_file = html.escape(f"references/{path.stem}{suffix}", quote=True)
                text = re.sub(
                    r"(<h2>(?:官方人工真值|Official human ground truth)</h2>)<pre>.*?</pre>",
                    lambda match, target=reference_file: (
                        match.group(1) + f'<pre data-file="{target}"></pre>'
                    ),
                    text,
                    count=1,
                    flags=re.S,
                )
            data = text.encode()
            pages[name] = text
        else:
            original[name] = hashlib.sha256(data).hexdigest()
        if redaction:
            redaction["published_sha256"] = hashlib.sha256(data).hexdigest()
            redaction["published_bytes"] = len(data)
            redactions.append(redaction)
        if footprint_projection:
            footprint_projection["original_sha256"] = source_sha256
            footprint_projection["original_bytes"] = path.stat().st_size
            footprint_projection["published_sha256"] = hashlib.sha256(data).hexdigest()
            footprint_projection["published_bytes"] = len(data)
        add(name, data, source_sha256=source_sha256)
    privacy = {
        "schema_version": 1,
        "scope": "all_public_files",
        "scoring_inputs_unchanged": True,
        "scored_outputs_privacy_projected": sum(bool(r.get("scored_output")) for r in redactions),
        "original_evidence_preserved_locally": True,
        "detection_limits": (
            "Pattern-based scan; not a guarantee of detecting every secret. "
            "Key validity is not tested."
        ),
        "files": redactions,
        "footprint_projections": footprint_projections,
    }
    add(
        "publication-privacy.json",
        (json.dumps(privacy, ensure_ascii=False, indent=2) + "\n").encode(),
    )
    # Privacy projection and block deduplication remain report-specific.
    blocks, index = write_packs(
        raw_blocks.items(),
        destination,
        limit=4 * 1024 * 1024,
        pattern="data/{index:03d}.gz",
        compresslevel=9,
    )
    manifest = {
        "schema_version": 2,
        "architecture": {
            "kind": "single_html_shell_with_lazy_virtual_routes",
            "logical_routes": logical_route_count,
            "source_files": source_file_count,
            "content_packs": index,
            "block_bytes": 65536,
            "large_text_on_demand_bytes": 256 * 1024,
        },
        "files": files,
        "blocks": blocks,
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, separators=(",", ":")))
    (destination / "style.css").write_bytes((source / "assets/style.css").read_bytes())
    js = (source / "assets/report.js").read_text()
    if not webmainbench:
        ready_listener = "window.addEventListener('report-ready',initReport);"
        dom_listener = "window.addEventListener('DOMContentLoaded',initReport);"
        if ready_listener in js:
            js = js.replace(dom_listener, "")
        else:
            js = js.replace(
                "window.addEventListener('DOMContentLoaded',",
                "window.addEventListener('report-ready',",
            )
    js = js.replace("localStorage.getItem('webfetch-theme')", "readTheme()").replace(
        "localStorage.setItem('webfetch-theme',t)", "saveTheme(t)"
    )
    js = (
        "function readTheme(){try{return localStorage.getItem('webfetch-theme')}"
        "catch{return 'light'}}\n"
        "function saveTheme(t){try{localStorage.setItem('webfetch-theme',t)}catch{}}\n" + js
    )
    (destination / "report.js").write_text(js)
    (destination / "loader.js").write_bytes(
        (Path(__file__).parent / "assets/report-loader.js").read_bytes()
    )
    # Artifact Site serves each committed version under an immutable signed base URL.
    # Its signed preview route resolves exact file names but rejects cache-busting
    # query strings on subresources, so the shell must reference the three physical
    # assets without ``?v=...``. The versioned base already provides cache isolation.
    (destination / "index.html").write_text(
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Web Fetch 评测</title><link rel="stylesheet" href="style.css">'
        '<script defer src="report.js"></script><script defer src="loader.js"></script>'
        "</head><body><p>正在加载报告…</p></body></html>"
    )
    # Validate every download and every internal page/file link after transformation.
    for name, digest in original.items():
        _require(
            hashlib.sha256(b"".join(raw_blocks[k] for k in files[name]["parts"])).hexdigest()
            == digest,
            name,
        )
    validate_virtual_links(files, lambda name: pages[name])
    # Verify packed data independently, not just in-memory deduplication.
    for name in sorted({v["pack"] for v in blocks.values()}):
        raw = gzip.decompress((destination / name).read_bytes())
        for key, b in blocks.items():
            if b["pack"] == name:
                observed = hashlib.sha256(raw[b["offset"] : b["offset"] + b["size"]]).hexdigest()
                _require(observed == key, (name, key, observed, b["offset"], b["size"], len(raw)))
    total = sum(p.stat().st_size for p in destination.rglob("*") if p.is_file())
    print(
        json.dumps(
            {
                "source_files": source_file_count,
                "logical_routes": logical_route_count,
                "html_shells": 1,
                "content_packs": index,
                "published_files": len(list(destination.rglob("*"))) - 1,
                "published_bytes": total,
                "all_downloads_byte_identical": not redactions,
                "redacted_public_files": len(redactions),
                "scored_outputs_privacy_projected": sum(
                    bool(r.get("scored_output")) for r in redactions
                ),
                "all_virtual_links_valid": True,
            }
        )
    )
    # Artifact Hub publishes large bundles through staged per-file uploads.
    # Its documented total limit is 300 MB, not the former local 50 MB budget.
    _require(total <= 300000000, "Published package exceeds the 300 MB service limit")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(destination))
    return manifest


def _archive_identity(path: Path):
    if not path.exists():
        return None
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build(source: Path, archive: Path):
    before = evidence_tree_identity(source)
    if archive.is_symlink() or (archive.exists() and not archive.is_file()):
        raise ValueError("Publication archive must be a regular file")
    if archive.resolve().is_relative_to(source.resolve()):
        raise ValueError("Publication archive must be outside the source report")
    archive.parent.mkdir(parents=True, exist_ok=True)
    target = archive.resolve()
    previous = _archive_identity(target)
    sampled = validate_release(source)
    with tempfile.TemporaryDirectory(prefix=".webfetch-package-", dir=archive.parent) as work:
        staging = Path(work)
        staged_archive = staging / "package.zip"
        manifest = _build(source, staging / "content", staged_archive, webmainbench=sampled)
        if evidence_tree_identity(source) != before:
            raise ValueError("Report source changed during packaging")
        if (
            archive.is_symlink()
            or archive.resolve() != target
            or _archive_identity(target) != previous
        ):
            raise ValueError("Publication destination changed during packaging")
        os.replace(staged_archive, target)
    return manifest


def explanatory_route(name: str) -> bool:
    return (
        name
        in {
            "index.html",
            "methodology/index.html",
            "adoption/index.html",
            "reproduce/index.html",
            "changes/index.html",
            "report-manifest.json",
        }
        or (name.startswith("providers/") and name.endswith(".html"))
        or (name.startswith("adoption/interfaces/") and name.endswith(".json"))
    )


def _build_incremental(source: Path, baseline: Path, destination: Path):
    """Keep every baseline pack; replace explanatory routes in one text manifest.

    The backward-compatible loader is installed first, then the complete manifest.
    This avoids both binary pack uploads and partially changed report content.
    """
    with zipfile.ZipFile(baseline) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        if manifest.get("schema_version") != 2:
            raise ValueError("Incremental update requires the lazy-route package")

        @lru_cache(maxsize=8)
        def pack(name):
            return gzip.decompress(bundle.read(name))

        def old_bytes(name):
            file = manifest["files"][name]
            if "inline_base64" in file:
                return base64.b64decode(file["inline_base64"], validate=True)
            parts = []
            for key in file["parts"]:
                block = manifest["blocks"][key]
                raw = pack(block["pack"])
                parts.append(raw[block["offset"] : block["offset"] + block["size"]])
            return b"".join(parts)

        if (
            old_bytes("results.jsonl")
            != project_redundant_result_streams(
                "results.jsonl",
                project_file("results.jsonl", (source / "results.jsonl").read_bytes())[0],
            )[0]
        ):
            raise ValueError("Incremental update cannot replace measured results")
        refresh = json.loads((source / "report-manifest.json").read_text()).get(
            "presentation_refresh"
        )
        if (
            not refresh
            or refresh.get("scores_changed") is not False
            or refresh.get("provider_calls_added") != 0
        ):
            raise ValueError("Incremental update requires a presentation-only receipt")
        routes = refresh["changed_routes"] + ["report-manifest.json"]
        privacy = json.loads(old_bytes("publication-privacy.json"))
        disclosures = {item["path"]: item for item in privacy["files"]}
        changes = {}

        def replace(name, data, *, source_sha256=None):
            if name not in manifest["files"]:
                raise ValueError("Incremental update cannot add a route: " + name)
            if data == old_bytes(name) and source_sha256 == manifest["files"][name].get("source_sha256"):
                return
            manifest["files"][name] = {
                "inline_base64": base64.b64encode(data).decode(),
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "mime": manifest["files"][name]["mime"],
            }
            if source_sha256 is not None:
                manifest["files"][name]["source_sha256"] = source_sha256
            changes[name] = manifest["files"][name]["sha256"]

        from lexbench_webfetch.paths import contained_path

        for name in routes:
            allowed = explanatory_route(name)
            if not allowed:
                raise ValueError("Not an explanatory route: " + name)
            original = contained_path(source, name).read_bytes()
            data, redaction = project_file(name, original)
            disclosures.pop(name, None)
            if redaction:
                redaction["published_sha256"] = hashlib.sha256(data).hexdigest()
                redaction["published_bytes"] = len(data)
                disclosures[name] = redaction
            replace(name, data, source_sha256=hashlib.sha256(original).hexdigest())
        privacy["files"] = list(disclosures.values())
        replace(
            "publication-privacy.json",
            (json.dumps(privacy, ensure_ascii=False, indent=2) + "\n").encode(),
        )
        validate_virtual_links(manifest["files"], old_bytes)
        destination.mkdir(parents=True, exist_ok=False)
        (destination / "loader.js").write_bytes(
            (Path(__file__).parent / "assets/report-loader.js").read_bytes()
        )
        raw = json.dumps(manifest, separators=(",", ":")).encode()
        (destination / "manifest.json").write_bytes(raw)
        receipt = {
            "kind": "presentation_only_incremental_update",
            "baseline_archive_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest(),
            "changed_virtual_files": changes,
            "physical_files": ["loader.js", "manifest.json"],
            "unchanged_content_packs": len(
                {block["pack"] for block in manifest["blocks"].values()}
            ),
            "provider_calls_added": 0,
            "scores_changed": False,
            "upload_bytes": sum(
                (destination / name).stat().st_size for name in ["loader.js", "manifest.json"]
            ),
        }
        (destination / "update-receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
        )
        return receipt


def build_incremental(source: Path, baseline: Path, destination: Path):
    """Stage a presentation update against immutable source and baseline bytes."""
    from .safety import unlinked_path

    source, baseline, destination = map(unlinked_path, (source, baseline, destination))
    if (
        destination.exists()
        or destination.is_relative_to(source)
        or source.is_relative_to(destination)
    ):
        raise ValueError("Incremental destination must be new and outside the report")
    before = evidence_tree_identity(source)
    baseline_hash = _archive_identity(baseline)
    validate_release(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".webfetch-update-", dir=destination.parent) as work:
        staged = Path(work) / "update"
        receipt = _build_incremental(source, baseline, staged)
        from .publication_check import check

        check(baseline, source, staged)
        if evidence_tree_identity(source) != before or _archive_identity(baseline) != baseline_hash:
            raise ValueError("Incremental inputs changed during packaging")
        if destination.exists() or unlinked_path(destination) != destination:
            raise ValueError("Incremental destination changed during packaging")
        staged.rename(destination)
    return receipt

def main(*, root: Path | None = None):
    root = root or Path.cwd()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, default=root / "reports/webfetch")
    parser.add_argument(
        "archive", nargs="?", type=Path, default=root / "publish/webfetch-sites.zip"
    )
    parser.add_argument("--incremental", type=Path, help="Write an explanatory update against the baseline archive")
    args = parser.parse_args()
    if args.incremental:
        print(json.dumps(build_incremental(args.source, args.archive, args.incremental), ensure_ascii=False))
        return
    build(args.source, args.archive)
    print(args.archive.resolve())
