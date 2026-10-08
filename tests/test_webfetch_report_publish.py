"""Synthetic publication service exercises submission, recovery and local authority guards."""

import gzip
import json
import runpy
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from harness.web_fetch import publication_check, report_package, report_publish
from harness.web_fetch.report_publish import PublishConfig


def fixture(root, mode="update"):
    source = root / "report"
    (source / "assets").mkdir(parents=True)
    (source / "assets/style.css").write_text("body {color:black}")
    (source / "assets/report.js").write_text("window.addEventListener('report-ready',initReport);")
    (source / "index.html").write_text("<html><head></head><body>Test</body></html>")
    (source / "dataset.json").write_text("{}")
    (source / "results.jsonl").write_text("")
    (source / "reliability.json").write_text('{"complete":true}')
    archive = root / "report.zip"
    report_package.build(source, archive)
    profile = root / "profile.json"
    target = {"artifact_hub_base_url": "https://reports.example"}
    receipt = root / "receipt.json"
    if mode != "create":
        target.update(artifact_hub_site="site-one", artifact_hub_expected_version="ver_old")
        receipt.write_text(
            json.dumps(
                {
                    "slug": "site-one",
                    "versionId": "ver_old",
                    "base_url": "https://reports.example",
                    "verified": True,
                }
            )
        )
    profile.write_text(json.dumps(target))
    return PublishConfig(profile, archive, source, receipt, root / "pending.json", mode)


class Service:
    def __init__(self, failure=None):
        self.failure = failure
        self.interrupted = False
        self.operations = {}
        self.uploads = []
        self.puts = []
        self.requests = []
        self.current = "ver_old"

    def interrupt(self, phase):
        if self.failure == phase and not self.interrupted:
            self.interrupted = True
            raise OSError("simulated " + phase)

    def __call__(self, path, data=None, content_type=None, *, method=None, extra_headers=None):
        self.requests.append(path)
        if path.startswith("/api/operations/"):
            value = self.operations.get(path.removeprefix("/api/operations/"))
            if value is None:
                return 404, b"{}"
        elif path.startswith("/api/uploads") and method == "PUT":
            self.puts.append((path, data))
            self.interrupt("staging-put")
            value = {}
        elif path == "/api/sites/site-one" and data is None:
            value = {"currentVersionId": self.current}
        elif path.endswith("/shares"):
            self.interrupt("share")
            value = {"url": "https://reports.example/v/token"}
        elif path.startswith("/api/uploads") and "/commit" not in path:
            assert "Idempotency-Key" in extra_headers
            value = {"versionId": "ver_staging"}
        elif path == "/api/sites" or "/versions?" in path or "/commit" in path:
            key = extra_headers["Idempotency-Key"]
            assert key not in self.operations, (
                "A completed upload must be recovered, not submitted again"
            )
            self.uploads.append((path, key, data))
            self.current = "ver_new"
            value = {
                "slug": "site-one",
                "versionId": "ver_new",
                "claimToken": "private-secret",
                "metadata": {"editToken": "private-secret"},
            }
            self.operations[key] = {"status": "completed", "httpStatus": 200, "result": value}
            self.interrupt("upload-response")
        else:
            raise AssertionError("Unexpected request or report readback: " + path)
        return 200, json.dumps(value).encode()


SCENARIOS = [
    (mode, staged, failure)
    for mode in ("create", "update")
    for staged in (False, True)
    for failure in ("upload-response", "share", "receipt", "clear", "staging-put")
    if staged or failure != "staging-put"
]


@pytest.mark.parametrize("mode,staged,failure", SCENARIOS)
def test_interruption_resumes_one_activation(tmp_path, monkeypatch, mode, staged, failure):
    config = fixture(tmp_path, mode)
    monkeypatch.setattr(report_publish, "STAGED_UPLOAD_THRESHOLD", 0 if staged else 10**9)
    service = Service(failure)
    write = report_publish.atomic_write
    clear = report_publish.PublicationOperation.clear

    def fail_receipt(path, payload):
        if path == config.receipt and json.loads(payload).get("publication_pending") is False:
            service.interrupt("receipt")
        write(path, payload)

    def fail_clear(operation, identity):
        service.interrupt("clear")
        clear(operation, identity)

    monkeypatch.setattr(report_publish, "atomic_write", fail_receipt)
    monkeypatch.setattr(report_publish.PublicationOperation, "clear", fail_clear)
    with pytest.raises(OSError, match="simulated"):
        report_publish.publish(config, transport=service)
    summary = report_publish.publish(config, transport=service)
    assert summary == {
        "slug": "site-one",
        "versionId": "ver_new",
        "url": "https://reports.example/v/token",
    }
    assert len(service.uploads) == 1 and not config.journal.exists()
    receipt = json.loads(config.receipt.read_bytes())
    assert receipt["submission_state"] == "submitted_unverified"
    assert receipt["publication_pending"] is False and "verified" not in receipt
    assert b"private-secret" not in config.receipt.read_bytes()
    assert config.receipt.stat().st_mode & 0o777 == 0o600
    if staged:
        assert "/files/index.html" in service.puts[0][0]


@pytest.mark.parametrize("changed", ["source", "archive", "profile", "receipt"])
def test_changed_input_after_validation_prevents_external_work(tmp_path, monkeypatch, changed):
    config = fixture(tmp_path)
    check = report_publish.check
    paths = {
        "source": config.source / "index.html",
        "archive": config.archive,
        "profile": config.profile,
        "receipt": config.receipt,
    }

    def mutate(*args):
        check(*args)
        paths[changed].write_bytes(b"new independent state")

    monkeypatch.setattr(report_publish, "check", mutate)
    service = Service()
    with pytest.raises(ValueError, match="changed"):
        report_publish.publish(config, transport=service)
    assert not service.requests


def test_explicit_host_must_match_profile_before_credentials_or_requests(tmp_path):
    config = replace(fixture(tmp_path), base_url="https://another.example")
    service = Service()
    with pytest.raises(ValueError, match="differs"):
        report_publish.publish(config, transport=service)
    assert not service.requests and not config.journal.exists()


def test_create_requires_an_unbound_profile(tmp_path):
    config = replace(fixture(tmp_path), mode="create")
    service = Service()
    with pytest.raises(ValueError, match="unbound"):
        report_publish.publish(config, transport=service)
    assert not service.requests


def test_reusing_receipt_does_not_require_archive_or_download_site(tmp_path):
    config = replace(fixture(tmp_path), mode="reuse")
    config.archive.unlink()
    config.receipt.write_text(
        json.dumps(
            {
                "slug": "site-one",
                "versionId": "ver_old",
                "share_url": "https://reports.example/v/token",
            }
        )
    )
    previous = config.receipt.read_bytes()
    service = Service()
    summary = report_publish.publish(config, transport=service)
    assert summary["versionId"] == "ver_old" and not service.requests
    assert config.receipt.read_bytes() == previous


def test_imports_do_not_read_credentials_parse_args_or_create_files(tmp_path, monkeypatch):
    import sys

    monkeypatch.setattr(
        report_publish, "load_token", lambda **kwargs: pytest.fail("credential read")
    )
    monkeypatch.setattr(sys, "argv", ["command", "--unknown"])
    monkeypatch.chdir(tmp_path)
    root = Path(__file__).resolve().parents[1]
    for name in (
        "publish_artifact_hub.py",
        "verify_artifact_hub.py",
        "check_publication_bundle.py",
    ):
        runpy.run_path(str(root / "scripts" / name))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "damage",
    ["extra", "duplicate", "unreferenced", "trailing-bytes", "physical-secret", "changed-loader"],
)
def test_bundle_verification_covers_every_public_byte(tmp_path, damage):
    config = fixture(tmp_path)
    with zipfile.ZipFile(config.archive) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(contents["manifest.json"])
    if damage == "extra":
        contents["unlisted.txt"] = b"not declared"
    elif damage == "unreferenced":
        manifest["blocks"]["unused"] = {"pack": "data/000.gz", "offset": 0, "size": 1}
    elif damage == "trailing-bytes":
        pack = next(iter(manifest["blocks"].values()))["pack"]
        contents[pack] = gzip.compress(gzip.decompress(contents[pack]) + b"hidden payload")
    elif damage == "physical-secret":
        contents["report.js"] += b'\nconst config={"access_token":"private-secret-canary"};'
    elif damage == "changed-loader":
        contents["loader.js"] += b'\nalert("different loader");'
    contents["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(config.archive, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
        if damage == "duplicate":
            archive.writestr("index.html", contents["index.html"])
    with pytest.raises(ValueError):
        publication_check.check(config.archive, config.source)


@pytest.mark.parametrize("damage", ["wrong-site", "duplicate-files", "invalid-version"])
def test_metadata_verification_rejects_invalid_authority(tmp_path, monkeypatch, damage):
    import io

    from harness.web_fetch import publication_verify

    data = {"slug": "site-one", "version": {"id": "ver_current"}, "files": ["index.html"]}
    if damage == "wrong-site":
        data["slug"] = "another-site"
    elif damage == "duplicate-files":
        data["files"].append("index.html")
    else:
        data["version"]["id"] = "not-a-version"
    monkeypatch.setattr(publication_verify, "load_token", lambda **kwargs: "fixture")
    monkeypatch.setattr(
        publication_verify.urllib.request,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO(json.dumps(data).encode()),
    )
    with pytest.raises(ValueError):
        publication_verify.verify_metadata("site-one", "https://reports.example")


def test_public_verifier_rejects_duplicate_zip_before_network(tmp_path, monkeypatch):
    from harness.web_fetch import publication_verify

    archive = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("index.html", "one")
        bundle.writestr("index.html", "two")
    monkeypatch.setattr(
        publication_verify, "load_token", lambda **kwargs: pytest.fail("credential read")
    )
    with pytest.raises(ValueError, match="unique"):
        publication_verify.verify_public_files("site-one", archive, "https://reports.example")


@pytest.mark.parametrize(
    "base",
    [
        None,
        "http://reports.example",
        "https://@reports.example",
        "https://reports.example?key=secret",
        "https://reports.example#frag",
        "https://reports.example:invalid",
        "https://reports.example\\bad",
    ],
)
def test_invalid_service_base_fails_before_requests(tmp_path, base):
    config = fixture(tmp_path)
    profile = json.loads(config.profile.read_bytes())
    profile["artifact_hub_base_url"] = base
    config.profile.write_text(json.dumps(profile))
    service = Service()
    with pytest.raises(ValueError):
        report_publish.publish(config, transport=service)
    assert not service.requests and not config.journal.exists()
