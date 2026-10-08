"""A simulated Artifact Site proves both tracks resume one committed release."""

import json
import zipfile

import pytest

from harness.browser_use import report_publish as autonomous
from harness.trajectory_replay import webarena_report_publish as replay


@pytest.mark.parametrize("track", ["browser-use", "replay"])
@pytest.mark.parametrize(
    "failure",
    [
        "creation-response",
        "share",
        "preview",
        "version-response",
        "remote-version",
        "binding",
        "receipt-write",
    ],
)
def test_retry_recovers_same_site_and_version(tmp_path, monkeypatch, track, failure):
    owner = autonomous if track == "browser-use" else replay
    archive = tmp_path / "archive.zip"
    marker = (
        autonomous.PUBLIC_REPORT_MARKER
        if track == "browser-use"
        else "<title>浏览器轨迹重放</title>"
    )
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("index.html", marker)
    files = {}
    for key in ("PROFILE", "ATTESTATION", "REVIEW"):
        path = tmp_path / (key.lower() + ".json")
        path.write_text("{}")
        files[key] = path
        if hasattr(owner, key):
            monkeypatch.setattr(owner, key, path)
    publication = {"base_url": "https://example.invalid", "title": "Shared lifecycle"}
    if failure == "version-response":
        publication.update(
            site_slug="site-1",
            expected_version="ver_old",
            share_url="https://example.invalid/v/token",
        )
    files["PROFILE"].write_text(json.dumps({"publication": publication}))
    receipt = tmp_path / "receipt.json"
    if failure == "version-response":
        receipt.write_text(
            json.dumps(
                {"slug": "site-1", "verified": True, "share_url": "https://example.invalid/v/token"}
            )
        )
    monkeypatch.setattr(owner, "ARCHIVE", archive)
    monkeypatch.setattr(owner, "RECEIPT", receipt)
    monkeypatch.setattr(owner, "BINDING_JOURNAL", tmp_path / "binding.json")
    monkeypatch.setattr(owner, "OPERATION_JOURNAL", tmp_path / "operation.json")
    monkeypatch.setattr(owner, "validate_package", lambda: {})
    uploads, operations = [], {}
    version = "ver_old"
    interrupted = False

    def interrupt_once(phase):
        nonlocal interrupted
        if phase == failure and not interrupted:
            interrupted = True
            raise OSError("simulated " + phase + " failure")

    original_write = owner.atomic_write

    def write(path, data):
        if path == files["ATTESTATION"]:
            interrupt_once("binding")
        if path == receipt and json.loads(data).get("verified") is True:
            interrupt_once("receipt-write")
        original_write(path, data)

    monkeypatch.setattr(owner, "atomic_write", write)

    def observed_preview():
        nonlocal interrupted, version
        if failure == "remote-version" and not interrupted:
            interrupted = True
            version = "ver_other"
        else:
            interrupt_once("preview")

    def request(*args, **kwargs):
        nonlocal version
        values = args[1:] if track == "replay" else args
        path, *rest = values
        data = rest[0] if rest else kwargs.get("data")
        if path == "/api/sites" or "/versions?" in path:
            key = kwargs["extra_headers"]["Idempotency-Key"]
            uploads.append((path, key, data))
            version = "ver_new"
            result = {"slug": "site-1", "versionId": version}
            operations[key] = {"status": "completed", "httpStatus": 200, "result": result}
            interrupt_once("creation-response" if path == "/api/sites" else "version-response")
        elif path.startswith("/api/operations/"):
            result = operations[path.removeprefix("/api/operations/")]
        elif path.endswith("/shares"):
            interrupt_once("share")
            result = {"url": "https://example.invalid/v/token"}
        elif path.endswith("/export"):
            raw = archive.read_bytes()
            return (200, raw, {}) if track == "replay" else (200, raw)
        elif path.startswith("/api/preview/"):
            observed_preview()
            raw = marker.encode()
            return (200, raw, {}) if track == "replay" else (200, raw)
        elif path == "/v/token":
            result = {}
        elif path == "/api/sites/site-1":
            result = {"site": {"currentVersionId": version}}
        else:
            raise AssertionError(path)
        raw = json.dumps(result).encode()
        return (200, raw, {}) if track == "replay" else (200, raw)

    monkeypatch.setattr(owner, "request", request)

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return marker.encode()

    def urlopen(request, **kwargs):
        if "/api/preview/" in request.full_url:
            observed_preview()
        return Response()

    monkeypatch.setattr(autonomous.urllib.request, "urlopen", urlopen)
    before = (files["PROFILE"].read_bytes(), files["ATTESTATION"].read_bytes())
    with pytest.raises((OSError, ValueError), match="simulated|remote version changed"):
        owner.main(create=failure != "version-response")
    if failure not in ("binding", "receipt-write"):
        assert (files["PROFILE"].read_bytes(), files["ATTESTATION"].read_bytes()) == before
    # Resume without requesting another site, including when the first response was lost.
    owner.main(create=failure in ("binding", "receipt-write"))
    assert len(uploads) == 1
    bound = json.loads(files["PROFILE"].read_bytes())["publication"]
    assert bound["site_slug"] == "site-1"
    assert bound["expected_version"] == ("ver_other" if failure == "remote-version" else "ver_new")
    assert bound["share_url"] == "https://example.invalid/v/token"
    assert json.loads(receipt.read_bytes())["verified"] is True
    assert not owner.OPERATION_JOURNAL.exists() and not owner.BINDING_JOURNAL.exists()


def test_browser_multipart_is_identical_for_retries(tmp_path):
    archive = tmp_path / "package.zip"
    archive.write_bytes(b"fixed validated package")
    first = autonomous.multipart_archive(archive=archive, title="Registry title")
    assert first == autonomous.multipart_archive(archive=archive, title="Registry title")


@pytest.mark.parametrize("same_release", [False, True])
def test_concurrent_publishers_share_one_operation_record(tmp_path, same_release):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from harness.reporting.publication import PublicationOperation, atomic_create

    journal = tmp_path / "operation.json"
    barrier = Barrier(2)

    def create(path, data):
        barrier.wait(timeout=5)
        atomic_create(path, data)

    client = PublicationOperation(journal, "test-", create)
    first = {"schema": "fixture/1", "archive_sha256": "a" * 64, "title": "First release"}
    second = dict(first, title="First release" if same_release else "Different release")

    def pending(identity):
        try:
            return client.pending(identity, allow_create=True)
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(pending, (first, second)))
    record = json.loads(journal.read_bytes())
    successful = [result for result in results if isinstance(result, tuple)]
    if same_release:
        assert len(successful) == 2
        assert {item[0]["key"] for item in successful} == {record["key"]}
        assert sorted(item[1] for item in successful) == [False, True]
    else:
        assert len(successful) == 1
        assert any(
            "differs from this release" in result for result in results if isinstance(result, str)
        )


@pytest.mark.parametrize("track", ["browser-use", "replay"])
def test_create_cannot_replace_an_existing_binding(tmp_path, monkeypatch, track):
    owner = autonomous if track == "browser-use" else replay
    for key in ("ARCHIVE", "ATTESTATION", "PROFILE", "REVIEW"):
        path = tmp_path / (key.lower() + ".json")
        path.write_text("{}")
        if hasattr(owner, key):
            monkeypatch.setattr(owner, key, path)
    owner.PROFILE.write_text(
        json.dumps(
            {
                "publication": {
                    "base_url": "https://example.invalid",
                    "site_slug": "site-1",
                    "expected_version": "ver_old",
                }
            }
        )
    )
    monkeypatch.setattr(owner, "RECEIPT", tmp_path / "receipt.json")
    monkeypatch.setattr(owner, "BINDING_JOURNAL", tmp_path / "binding.json")
    monkeypatch.setattr(owner, "OPERATION_JOURNAL", tmp_path / "operation.json")
    monkeypatch.setattr(owner, "validate_package", lambda: {})

    def unexpected(*args, **kwargs):
        raise AssertionError("a bound create must fail before remote calls")

    monkeypatch.setattr(owner, "request", unexpected)
    with pytest.raises(ValueError, match="already bound"):
        owner.main(create=True)
