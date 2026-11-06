"""Bound report updates choose transport without losing recovery or byte checks."""

import hashlib
import json
import zipfile
from urllib.error import URLError

import pytest

from harness.record_io import json_bytes
from harness.reporting.publication import PublicationClient
from harness.reporting.publish import StagedUpdate, publish_staged_update


def release(tmp_path, names=None):
    archive = tmp_path / "report.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in names or ["index.html", *[f"zh/case-{i}.html" for i in range(200)]]:
            bundle.writestr(name, "<h1>Frozen report</h1>")
    bound = []
    update = StagedUpdate(
        label="Example",
        prefix="example",
        client=PublicationClient("Example"),
        base="https://reports.example",
        slug="existing",
        expected_version="ver_before",
        share_path="/s/existing",
        title="Frozen report",
        archive=archive,
        archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        inputs=(),
        work=tmp_path / "publication",
        allow_inline=True,
        guard=lambda: None,
        bind=bound.append,
    )
    return update, bound


class Service:
    def __init__(self, update, *, lose_reply=False):
        self.update = update
        self.version = "ver_before"
        self.calls = []
        self.uploads = []
        self.lose_reply = lose_reply
        self.bad_export = False

    def __call__(self, path, data, content_type, **kwargs):
        self.calls.append((path, kwargs))
        if path.startswith("/api/operations/"):
            return 200, json_bytes(
                {
                    "status": "completed",
                    "httpStatus": 200,
                    "result": {"slug": "existing", "versionId": self.version},
                }
            )
        if path.startswith("/api/sites/existing/versions?"):
            assert path.endswith("expected_version=ver_before")
            assert content_type.startswith("multipart/form-data;")
            assert self.update.archive.read_bytes() in data
            assert kwargs["headers"]["Idempotency-Key"].startswith("example-inline-")
            self.uploads.append(data)
            self.version = "ver_after"
            if self.lose_reply:
                self.lose_reply = False
                raise URLError("Response lost after commit")
            return 200, json_bytes({"slug": "existing", "versionId": self.version})
        if path == "/api/sites/existing/export":
            return 200, b"not the report" if self.bad_export else self.update.archive.read_bytes()
        if path == "/s/existing":
            assert kwargs["auth"] is False
            return 200, b"Shared report"
        if path == "/api/sites/existing":
            return 200, json_bytes({"version": {"id": self.version, "entry": "index.html"}})
        raise AssertionError("Unexpected remote call: " + path)


def test_small_many_file_report_uses_one_bound_zip_request(tmp_path):
    update, bound = release(tmp_path)
    service = Service(update)
    assert publish_staged_update(update, transport=service) == "ver_after"
    assert len(service.uploads) == 1
    assert not any(path.startswith("/api/uploads") for path, _ in service.calls)
    assert bound == ["ver_after"]


def test_verified_host_limit_can_avoid_unnecessary_per_file_uploads(tmp_path, monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr("harness.reporting.publish.STAGED_UPLOAD_THRESHOLD", 1)
    update, bound = release(tmp_path)
    update = replace(update, inline_limit_bytes=update.archive.stat().st_size)
    service = Service(update)
    assert publish_staged_update(update, transport=service) == "ver_after"
    assert len(service.uploads) == 1
    assert bound == ["ver_after"]


@pytest.mark.parametrize("limit", [0, -1, True, "100", 1.5])
def test_invalid_host_limit_fails_before_remote_work(tmp_path, limit):
    from dataclasses import replace

    update, bound = release(tmp_path)
    update = replace(update, inline_limit_bytes=limit)
    service = Service(update)
    with pytest.raises(ValueError, match="positive byte count"):
        publish_staged_update(update, transport=service)
    assert not service.calls and not bound


def test_lost_inline_reply_recovers_same_operation_without_another_write(tmp_path):
    update, bound = release(tmp_path)
    service = Service(update, lose_reply=True)
    with pytest.raises(URLError):
        publish_staged_update(update, transport=service)
    assert not bound
    assert publish_staged_update(update, transport=service) == "ver_after"
    assert len(service.uploads) == 1
    assert bound == ["ver_after"]


def test_inline_update_keeps_export_verification_and_resume(tmp_path):
    update, bound = release(tmp_path)
    service = Service(update)
    service.bad_export = True
    with pytest.raises(zipfile.BadZipFile):
        publish_staged_update(update, transport=service)
    assert not bound
    service.bad_export = False
    assert publish_staged_update(update, transport=service) == "ver_after"
    assert len(service.uploads) == 1


@pytest.mark.parametrize("names", [["other.html"], ["index.html", "../outside.html"]])
def test_invalid_archive_is_rejected_before_any_external_write(tmp_path, names):
    update, bound = release(tmp_path, names)
    service = Service(update)
    with pytest.raises(ValueError):
        publish_staged_update(update, transport=service)
    assert not service.calls
    assert not bound


@pytest.mark.parametrize("legacy_resume", [False, True])
def test_large_and_already_staged_updates_keep_file_resume(tmp_path, monkeypatch, legacy_resume):
    import harness.reporting.publish as owner

    update, bound = release(tmp_path)
    service = Service(update)
    if legacy_resume:
        identity = {
            "base_url": update.base,
            "slug": update.slug,
            "archive_sha256": update.archive_sha256,
        }
        key = hashlib.sha256(json_bytes(identity)).hexdigest()[:24]
        update.work.mkdir()
        (update.work / (update.prefix + "-update-" + key + ".json")).write_bytes(
            json_bytes({**identity, "expected_version": "ver_before"})
        )
    else:
        monkeypatch.setattr(owner, "STAGED_UPLOAD_THRESHOLD", 0)

    def staged(request, archive, slug, expected, **kwargs):
        assert kwargs["resume"] is legacy_resume
        assert slug == "existing" and expected == "ver_before"
        service.version = "ver_after"
        return json.dumps({"slug": slug, "versionId": service.version}).encode()

    monkeypatch.setattr(owner, "upload_large_archive", staged)
    assert publish_staged_update(update, transport=service) == "ver_after"
    assert not service.uploads
    assert bound == ["ver_after"]
