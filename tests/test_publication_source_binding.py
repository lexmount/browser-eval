"""Publication must represent the complete current report, including unchanged routes."""

import hashlib
import json
import zipfile

import pytest

from harness.web_fetch.publication_check import check
from harness.web_fetch.report_package import build, build_incremental


@pytest.fixture
def report(tmp_path):
    source = tmp_path / "source"
    files = {
        "index.html": "<html><body>Summary</body></html>",
        "dataset.json": '{"url":"https://example.test/original"}',
        "results.jsonl": json.dumps(
            {
                "attempt_id": "browser__task__r001",
                "case_id": "task",
                "artifact_path": "artifacts/output.txt",
                "output_sha256": hashlib.sha256(b"Measured response").hexdigest(),
            }
        )
        + "\n",
        "reliability.json": '{"complete":true}',
        "report-manifest.json": "{}",
        "references/task.txt": "Independent reference",
        "datasets/task.html": "<html><body>Task definition</body></html>",
        "guide/index.html": "<html><body>Reading guide</body></html>",
        "changes/index.html": "<html><body>Release history</body></html>",
        "assets/style.css": "body {color:black}",
        "assets/report.js": "console.log('report');",
        "sources/task.txt": '{"access_token":"private-source-token"}',
        "artifacts/output.txt": "Measured response",
        "attempts/browser__task__r001.html": (
            '<html><body><pre data-kind="reference">Independent reference</pre>'
            '<pre data-kind="output">Measured response</pre>'
            '<pre data-kind="metadata">{}</pre></body></html>'
        ),
    }
    for name, text in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    archive = tmp_path / "report.zip"
    build(source, archive)
    check(archive, source)
    return source, archive


def refresh(source):
    (source / "index.html").write_text("<html><body>New explanation</body></html>")
    (source / "report-manifest.json").write_text(
        json.dumps(
            {
                "presentation_refresh": {
                    "scores_changed": False,
                    "provider_calls_added": 0,
                    "changed_routes": ["index.html"],
                }
            }
        )
    )


@pytest.mark.parametrize(
    "name",
    [
        "dataset.json",
        "references/task.txt",
        "datasets/task.html",
        "guide/index.html",
        "changes/index.html",
        "assets/style.css",
        "assets/report.js",
        "sources/task.txt",
        "attempts/browser__task__r001.html",
    ],
)
def test_stale_source_rejected_by_full_and_incremental_publication(report, tmp_path, name):
    source, archive = report
    path = source / name
    original = path.read_bytes()
    # Ordinary publication must reject a previously built archive after any source change.
    path.write_bytes(original + b"\n ")
    with pytest.raises(ValueError, match="source.*hash|source binding"):
        check(archive, source)
    path.write_bytes(original)
    refresh(source)
    overlay = tmp_path / "valid-update"
    build_incremental(source, archive, overlay)
    check(archive, source, overlay)
    # An independent check must also reject stale evidence in an existing overlay.
    path.write_bytes(original + b"\n ")
    with pytest.raises(ValueError, match="source.*hash|source binding"):
        check(archive, source, overlay)
    destination = tmp_path / "rejected-update"
    with pytest.raises(ValueError, match="source.*hash|source binding"):
        build_incremental(source, archive, destination)
    assert not destination.exists()


def test_declared_explanation_update_and_full_rebuild_remain_supported(report, tmp_path):
    source, archive = report
    refresh(source)
    overlay = tmp_path / "update"
    build_incremental(source, archive, overlay)
    check(archive, source, overlay)
    (source / "references/task.txt").write_text("Corrected independent reference")
    replacement = tmp_path / "replacement.zip"
    build(source, replacement)
    check(replacement, source)


def test_missing_source_bindings_require_full_repackaging(report, tmp_path):
    source, archive = report
    unbound = tmp_path / "unbound.zip"
    with zipfile.ZipFile(archive) as original, zipfile.ZipFile(unbound, "w") as output:
        for name in original.namelist():
            raw = original.read(name)
            if name == "manifest.json":
                manifest = json.loads(raw)
                for entry in manifest["files"].values():
                    entry.pop("source_sha256", None)
                raw = json.dumps(manifest).encode()
            output.writestr(name, raw)
    with pytest.raises(ValueError, match="source binding"):
        check(unbound, source)
    refresh(source)
    with pytest.raises(ValueError, match="source binding"):
        build_incremental(source, unbound, tmp_path / "update")


def test_source_cannot_replace_generated_privacy_disclosure(report):
    source, archive = report
    original = archive.read_bytes()
    (source / "publication-privacy.json").write_text("{}")
    with pytest.raises(ValueError, match="reserved generated file"):
        build(source, archive)
    assert archive.read_bytes() == original


def test_declared_redaction_only_change_updates_original_binding(report, tmp_path):
    source, archive = report
    interface = source / "adoption/interfaces/example.json"
    interface.parent.mkdir(parents=True)
    interface.write_text('{"access_token":"original-secret"}')
    build(source, archive)
    refresh(source)
    receipt = json.loads((source / "report-manifest.json").read_text())
    receipt["presentation_refresh"]["changed_routes"].append("adoption/interfaces/example.json")
    (source / "report-manifest.json").write_text(json.dumps(receipt))
    interface.write_text('{"access_token":"replacement-secret"}')
    overlay = tmp_path / "update"
    build_incremental(source, archive, overlay)
    check(archive, source, overlay)
    manifest = json.loads((overlay / "manifest.json").read_text())
    assert (
        manifest["files"]["adoption/interfaces/example.json"]["source_sha256"]
        == hashlib.sha256(interface.read_bytes()).hexdigest()
    )
