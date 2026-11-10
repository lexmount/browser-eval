"""Bound-target publishing and resumable package integrity at the public boundary."""

import json
from copy import deepcopy

import pytest

from harness.record_io import canonical_json_sha256
from harness.web_extract import publication
from harness.web_extract.pipeline import digest, execution_identity, read_json, save_json


def dataset_pin(home):
    root = home / "dataset"
    save_json(root / "dataset/ground-truth.json", {"page": {"values": {"title": ["A"]}}})
    (root / "html").mkdir(parents=True)
    (root / "html/page.html").write_text("<p>A</p>", encoding="utf-8")
    files = {
        name: {"sha256": digest((root / name).read_bytes())}
        for name in ("dataset/ground-truth.json", "html/page.html")
    }
    save_json(root / "manifest.json", {"commit": "native-fixture-v1", "cases": 1, "files": files})
    return {
        "commit": "native-fixture-v1",
        "cases": 1,
        "inventory_sha256": canonical_json_sha256(files),
        "files": {k: v["sha256"] for k, v in files.items()},
    }


@pytest.mark.parametrize("mode", ["full-cohort-progress", "field-extraction-cohort"])
@pytest.mark.parametrize(
    "damage",
    [
        None,
        "public-envelope",
        "original-envelope",
        "http-status",
        "compatibility",
        "missing-delivery",
        "request-parameters",
        "retry-removed",
        "retry-usage",
        "retry-time",
        "single-attempt",
        "injected-usage",
        "injected-time",
        "coerced-type",
        "reordered-keys",
        "retry-coerced-type",
        "missing-home",
        "relocated-home",
        "wrong-home",
    ],
)
def test_release_binds_native_execution_and_accepts_privacy_projection(tmp_path, damage, mode):
    from harness.web_extract.cli import main
    from harness.web_extract.pipeline import normalize
    from harness.web_extract.report import (
        publication_host_names,
        redact_account_endpoint,
        verify_completion_audit,
    )
    from harness.web_fetch.publication_privacy import project_evidence_view

    home = tmp_path / "inputs"
    run = home / "runs/run"
    row = {
        "method": "cloudflare-json",
        "case_id": "page",
        "http_status": 200,
        "compatibility": "structured_output",
        "state": "response_received",
        "elapsed_seconds": 0.25,
        "provider_usage": {"x-browser-ms-used": "100"},
        "response": {
            "result": {"price": 1, "sku": "A", "availability": "InStock"}
            if mode == "full-cohort-progress"
            else {"title": ["A"]},
            "message": "retained metadata",
            "debug_path": "/Users/example/private/trace.json",
            "native_flag": 1,
        },
        "request": {
            "endpoint": "https://api.cloudflare.com/client/v4/accounts/private-account-id/browser-rendering/json",
            "parameters": {"waitUntil": "domcontentloaded", "timeout": 30000},
        },
    }
    if damage == "missing-delivery":
        row.pop("http_status")
        row.pop("compatibility")
    if damage == "injected-usage":
        row.pop("provider_usage")
    if damage == "injected-time":
        row.pop("elapsed_seconds")
    retry = damage is not None and damage.startswith("retry-")
    if retry:
        previous = deepcopy(row)
        previous.update(
            http_status=402,
            compatibility="no_structured_output",
            response={"error": "Insufficient credits", "usage": {"credits": 0}},
            elapsed_seconds=0.5,
            provider_usage={"x-browser-ms-used": "0"},
            physical_attempt=1,
        )
        save_json(run / "first.json", previous)
        row.update(physical_attempt=2, retry_execution_seconds=0.5)
    save_json(run / "call.json", row)
    save_json(
        run / "contract.json",
        {"methods": [row["method"]], "case_ids": ["page"], "access_retry_limit": int(retry)},
    )
    inventory = {
        "contract_sha256": digest((run / "contract.json").read_bytes()),
        "calls": [{"path": "call.json", "sha256": digest((run / "call.json").read_bytes())}],
    }
    if retry:
        inventory["physical_calls"] = [
            {"path": "first.json", "sha256": digest((run / "first.json").read_bytes())},
            *inventory["calls"],
        ]
        (run / "calls.journal.jsonl").write_text(
            "".join(json.dumps(v) + "\n" for v in inventory["physical_calls"]), encoding="utf-8"
        )
    save_json(run / "manifest.json", inventory)
    pin = dataset_pin(home)
    audit = home / "audit.json"
    save_json(
        audit,
        {
            "results": {
                row["method"] + ":page": {
                    "execution_sha256": execution_identity(row),
                    "native_response_sha256": digest(
                        json.dumps(row["response"], sort_keys=True, ensure_ascii=False).encode()
                    ),
                }
            }
        },
    )
    content = home / "content.json"
    save_json(
        content,
        {
            "audit": {"path": "audit.json", "sha256": digest(audit.read_bytes())},
            "dataset_manifest": str(
                (tmp_path / "retired-inputs" if damage == "relocated-home" else home)
                / "dataset/manifest.json"
            ),
            "evidence_runs": [{"run": "run"}],
        },
    )
    profile = home / "profile.json"
    save_json(
        profile,
        {
            "dataset": pin,
            "research_report": {
                "content": {"path": "content.json", "sha256": digest(content.read_bytes())}
            },
        },
    )
    report = tmp_path / "report"
    public_row = {**row, "physical_attempts": [previous, row]} if retry else row
    public, _ = project_evidence_view(
        "public-evidence/native-row.json",
        json.dumps(redact_account_endpoint(public_row)).encode(),
        role="native_response",
        content_format="json",
        host_names=publication_host_names(),
    )
    projected = json.loads(public)
    assert projected["response"] != row["response"]  # A real local-path redaction.
    assert "/accounts/[REDACTED]/" in projected["request"]["endpoint"]
    assert "/accounts/private-account-id/" in row["request"]["endpoint"]
    if retry:
        assert all(
            "/accounts/[REDACTED]/" in v["request"]["endpoint"]
            for v in projected["physical_attempts"]
        )
    path = report / "public-evidence" / (row["method"] + ".json")
    save_json(path, {"page": projected})
    public_audit, _ = project_evidence_view(
        "public-evidence/audit.json",
        audit.read_bytes(),
        role="native_response",
        content_format="json",
        host_names=publication_host_names(),
    )
    (report / "public-evidence/audit.json").write_bytes(public_audit)
    save_json(
        report / "report-manifest.json",
        {
            "mode": mode,
            "profile_sha256": digest(profile.read_bytes()),
            ("completion_audit" if mode == "full-cohort-progress" else "audit"): {
                "path": "public-evidence/audit.json",
                "source_sha256": digest(audit.read_bytes()),
            },
        },
    )
    verify_completion_audit(report, profile, home=home)
    if damage == "relocated-home":
        return
    if damage == "missing-home":
        with pytest.raises(ValueError, match="explicit benchmark home"):
            verify_completion_audit(report, profile)
        with pytest.raises(SystemExit) as error:
            main(
                [
                    "package",
                    str(report),
                    "--profile",
                    str(profile),
                    "--output",
                    str(tmp_path / "missing-home.zip"),
                    "--visual-receipt",
                    str(tmp_path / "visual.json"),
                ]
            )
        assert error.value.code == 2
        assert not (tmp_path / "missing-home.zip").exists()
        return
    if damage == "wrong-home":
        wrong_home = tmp_path / "other-inputs"
        dataset_pin(wrong_home)
        wrong = read_json(wrong_home / "dataset/manifest.json")
        wrong["commit"] = "another-dataset"
        save_json(wrong_home / "dataset/manifest.json", wrong)
        with pytest.raises(ValueError, match="benchmark home differs"):
            verify_completion_audit(report, profile, home=wrong_home)
        return
    if damage == "reordered-keys":
        projected["response"] = dict(reversed(list(projected["response"].items())))
        save_json(path, {"page": projected})
        verify_completion_audit(report, profile, home=home)
        return
    if damage == "single-attempt":
        projected["physical_attempts"] = [deepcopy(projected)]
        save_json(path, {"page": projected})
        verify_completion_audit(report, profile, home=home)
        return
    if damage is None:
        return
    before = normalize(row["method"], projected["response"], schema={})
    if damage == "public-envelope":
        projected["response"]["message"] = "substituted metadata"
        save_json(path, {"page": projected})
    elif damage == "coerced-type":
        if mode == "full-cohort-progress":
            projected["response"]["result"]["price"] = True
        else:
            projected["response"]["native_flag"] = True
        save_json(path, {"page": projected})
    elif damage == "missing-delivery":
        projected.update(http_status=200, compatibility="structured_output")
        save_json(path, {"page": projected})
    elif damage == "request-parameters":
        projected["request"]["parameters"]["timeout"] = 60000
        save_json(path, {"page": projected})
    elif damage in (
        "retry-removed",
        "retry-usage",
        "retry-time",
        "retry-coerced-type",
        "injected-usage",
        "injected-time",
    ):
        if damage == "retry-removed":
            projected.pop("physical_attempts")
        elif damage == "retry-usage":
            projected["physical_attempts"][0]["provider_usage"]["x-browser-ms-used"] = "90000"
        elif damage == "retry-time":
            projected["physical_attempts"][0]["elapsed_seconds"] = 50
        elif damage == "retry-coerced-type":
            projected["physical_attempts"][0]["response"]["usage"]["credits"] = False
        elif damage == "injected-usage":
            projected["provider_usage"] = {"x-browser-ms-used": "1"}
        else:
            projected["elapsed_seconds"] = 0
        save_json(path, {"page": projected})
    elif damage in ("http-status", "compatibility"):
        projected["http_status" if damage == "http-status" else "compatibility"] = (
            500 if damage == "http-status" else "no_structured_output"
        )
        save_json(path, {"page": projected})
    else:
        row["response"]["message"] = "substituted original"
        save_json(run / "call.json", row)
        inventory["calls"][0]["sha256"] = digest((run / "call.json").read_bytes())
        save_json(run / "manifest.json", inventory)
    assert normalize(row["method"], projected["response"], schema={}) == before
    archive = tmp_path / "forged.zip"
    save_json(tmp_path / "visual.json", {})
    with pytest.raises(ValueError, match="Completion audit native"):
        main(
            [
                "--home",
                str(home),
                "package",
                str(report),
                "--profile",
                str(profile),
                "--output",
                str(archive),
                "--visual-receipt",
                str(tmp_path / "visual.json"),
            ]
        )
    assert not archive.exists()


def setup_report(tmp_path):
    source = tmp_path / "report"
    source.mkdir()
    profile = tmp_path / "profile.json"
    save_json(profile, {"local_example": True})
    profile_hash = digest(profile.read_bytes())
    save_json(source / "report-manifest.json", {"profile_sha256": profile_hash})
    save_json(source / "browser-eval-version.json", {"revision": "a" * 40})
    target = tmp_path / "target.json"
    save_json(
        target,
        {
            "host": "https://reports.example",
            "slug": "existing",
            "expected_version": "ver_before",
            "title": "Existing report",
            "visibility": "private",
            "profile_sha256": profile_hash,
            "profile": {"path": str(profile)},
        },
    )
    save_json(tmp_path / "visual.json", {})
    return source, target, tmp_path / "upload", tmp_path / "visual.json"


def test_existing_target_is_preserved_and_rebound_only_after_success(tmp_path, monkeypatch):
    source, target, work, visual = setup_report(tmp_path)
    audit_calls = []
    original_audit = publication.verify_completion_audit

    def verify(*args, **kwargs):
        audit_calls.append(1)
        return original_audit(*args, **kwargs)

    monkeypatch.setattr(publication, "verify_completion_audit", verify)

    def package(report, archive, *, visual_receipt, profile, home=None):
        archive.write_bytes(b"checked-package")
        return {
            "archive_sha256": digest(archive.read_bytes()),
            "report_manifest_sha256": digest((report / "report-manifest.json").read_bytes()),
        }

    def update(staged, *, transport):
        assert staged.slug == "existing"
        assert staged.expected_version == "ver_before"
        assert read_json(target)["expected_version"] == "ver_before"
        for _ in range(20):
            staged.guard()
        staged.bind("ver_after")
        return "ver_after"

    monkeypatch.setattr(publication, "package", package)
    monkeypatch.setattr(publication, "publish_staged_update", update)
    result = publication.publish(source, target, work, visual, home=tmp_path)
    bound = read_json(target)
    assert result["versionId"] == "ver_after"
    assert len(audit_calls) == 1  # Once at the frozen upload boundary, not per request.
    assert bound["visibility"] == "private"
    assert bound["slug"] == "existing"
    assert bound["profile"]["path"] == str(tmp_path / "profile.json")
    assert bound["renderer_source_revision"] == "a" * 40


def test_incomplete_target_never_packages_or_dispatches(tmp_path, monkeypatch):
    source, target, work, visual = setup_report(tmp_path)
    save_json(target, {"host": "https://reports.example", "slug": "existing"})
    monkeypatch.setattr(publication, "package", lambda *a, **k: pytest.fail("packaged"))
    with pytest.raises(ValueError, match="complete existing target"):
        publication.publish(source, target, work, visual, home=tmp_path)
    assert not work.exists()


@pytest.mark.parametrize(
    "damage",
    [None, "coordinated-public-audit", "missing-authority", "original", "content", "profile"],
)
@pytest.mark.parametrize("local_example", [False, True])
def test_publication_uses_frozen_audit_even_when_public_receipts_are_rewritten(
    tmp_path, monkeypatch, damage, local_example
):
    from harness.web_extract.report import publication_host_names
    from harness.web_fetch.publication_privacy import project_evidence_view

    source, target, work, visual = setup_report(tmp_path)
    frozen = tmp_path / "frozen"
    pin = dataset_pin(frozen)
    audit = frozen / "audit.json"
    save_json(audit, {"result_count": 1, "excluded_fields": {}})
    audit_hash = digest(audit.read_bytes())
    content = frozen / "content.json"
    save_json(content, {"audit": {"path": "audit.json", "sha256": audit_hash}})
    profile = frozen / "profile.json"
    save_json(
        profile,
        {
            "local_example": local_example,
            "dataset": pin,
            "research_report": {
                "content": {"path": "content.json", "sha256": digest(content.read_bytes())}
            },
        },
    )
    profile_hash = digest(profile.read_bytes())
    path = "public-evidence/audit.json"
    raw, receipt = project_evidence_view(
        path,
        audit.read_bytes(),
        role="native_response",
        content_format="json",
        host_names=publication_host_names(),
    )
    published = source / path
    published.parent.mkdir()
    published.write_bytes(raw)
    manifest = {
        "profile_sha256": profile_hash,
        "completion_audit": {"path": path, "source_sha256": audit_hash, "sha256": digest(raw)},
        "public_evidence_views": [receipt],
        "completion_reference_exclusions": {},
    }
    if damage == "coordinated-public-audit":
        save_json(published, {"result_count": 1, "excluded_fields": {"page": ["sku"]}})
        manifest["completion_audit"]["sha256"] = digest(published.read_bytes())
        receipt["published_sha256"] = digest(published.read_bytes())
        manifest["completion_reference_exclusions"] = {"page": ["sku"]}
        # Source hashes still claim the untouched, frozen original. Rebuilding
        # the bundle and tables cannot legitimize the forged public audit.
    elif damage == "missing-authority":
        manifest.pop("completion_audit")
    elif damage:
        changed = {"original": audit, "content": content, "profile": profile}[damage]
        save_json(changed, {"substituted": True})
    save_json(source / "report-manifest.json", manifest)
    bound = read_json(target)
    bound.update(
        {"profile_sha256": profile_hash, "profile": {"path": str(profile), "sha256": profile_hash}}
    )
    save_json(target, bound)
    calls = []

    def checked_package(source, *args, profile, home, **kwargs):
        # Preserve the real package boundary's audit gate while stubbing ZIP I/O.
        publication.verify_completion_audit(source, profile, home=home)
        calls.append("package")
        return {"archive_sha256": "archive", "report_manifest_sha256": "manifest"}

    monkeypatch.setattr(publication, "package", checked_package)

    def update(staged, **kwargs):
        calls.append("network")
        staged.guard()
        staged.bind("ver_after")
        return "ver_after"

    monkeypatch.setattr(publication, "publish_staged_update", update)
    if damage:
        from harness.web_extract.cli import main

        archive = tmp_path / "forged.zip"
        for command in ("check", "package"):
            args = [
                "--home",
                str(frozen),
                command,
                str(source),
                "--profile",
                str(profile),
                "--visual-receipt",
                str(visual),
            ]
            args += ["--release"] if command == "check" else ["--output", str(archive)]
            with pytest.raises(ValueError, match="Completion audit"):
                main(args)
        assert not archive.exists()
        with pytest.raises(ValueError, match="Completion audit"):
            publication.publish(source, target, work, visual, home=frozen)
        assert calls == []
        # No package or journal survives rejection.
        assert not work.exists() or not any(work.iterdir())
    else:
        assert (
            publication.publish(source, target, work, visual, home=frozen)["versionId"]
            == "ver_after"
        )
        assert calls == ["package", "network"]


@pytest.mark.parametrize("approved", [False, True])
def test_different_report_profile_needs_explicit_successor(tmp_path, monkeypatch, approved):
    source, target, work, visual = setup_report(tmp_path)
    next_profile = tmp_path / "next-profile.json"
    save_json(next_profile, {"report": "next", "local_example": True})
    next_hash = digest(next_profile.read_bytes())
    save_json(source / "report-manifest.json", {"profile_sha256": next_hash})
    bound = read_json(target)
    if approved:
        bound["approved_profile_successor"] = {
            "from_sha256": read_json(target)["profile_sha256"],
            "to_sha256": next_hash,
            "profile_path": str(next_profile),
        }
        save_json(target, bound)
    called = []
    monkeypatch.setattr(
        publication,
        "package",
        lambda *a, **k: (
            called.append("package")
            or {"archive_sha256": "archive", "report_manifest_sha256": "manifest"}
        ),
    )

    def update(staged, **kwargs):
        staged.bind("ver_after")
        return "ver_after"

    monkeypatch.setattr(publication, "publish_staged_update", update)
    if approved:
        assert (
            publication.publish(source, target, work, visual, home=tmp_path)["versionId"]
            == "ver_after"
        )
        assert called == ["package"]
        assert read_json(target)["profile"] == {"path": str(next_profile), "sha256": next_hash}
        assert "approved_profile_successor" not in read_json(target)
    else:
        with pytest.raises(ValueError, match="profile differs"):
            publication.publish(source, target, work, visual, home=tmp_path)
        assert not called
        # No package or journal survives rejection.
        assert not work.exists() or not any(work.iterdir())


def test_tampered_resume_package_never_dispatches(tmp_path, monkeypatch):
    source, target, work, visual = setup_report(tmp_path)
    work.mkdir()
    (work / "report.zip").write_bytes(b"tampered")
    save_json(
        work / "report.receipt.json",
        {
            "archive_sha256": digest(b"original"),
            "report_manifest_sha256": digest((source / "report-manifest.json").read_bytes()),
        },
    )
    monkeypatch.setattr(publication, "check", lambda *a, **k: None)
    monkeypatch.setattr(
        publication, "publish_staged_update", lambda *a, **k: pytest.fail("dispatched")
    )
    with pytest.raises(ValueError, match="Retained publication package differs"):
        publication.publish(source, target, work, visual, home=tmp_path)


@pytest.mark.parametrize("damage", ["stale-file", "extra-file", "missing-manifest"])
def test_self_consistent_zip_hash_cannot_replace_checked_contents(tmp_path, monkeypatch, damage):
    import zipfile

    source, target, work, visual = setup_report(tmp_path)
    (source / "index.html").write_text("checked", encoding="utf-8")
    manifest = read_json(source / "report-manifest.json")
    manifest["files"] = {
        "index.html": digest(b"checked"),
        "browser-eval-version.json": digest((source / "browser-eval-version.json").read_bytes()),
    }
    save_json(source / "report-manifest.json", manifest)
    work.mkdir()
    archive = work / "report.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in source.iterdir():
            if damage == "missing-manifest" and path.name == "report-manifest.json":
                continue
            bundle.writestr(
                path.name,
                b"stale"
                if damage == "stale-file" and path.name == "index.html"
                else path.read_bytes(),
            )
        if damage == "extra-file":
            bundle.writestr("private.txt", "private")
    save_json(
        work / "report.receipt.json",
        {
            "archive_sha256": digest(archive.read_bytes()),
            "report_manifest_sha256": digest((source / "report-manifest.json").read_bytes()),
        },
    )
    monkeypatch.setattr(publication, "check", lambda *a, **k: None)
    monkeypatch.setattr(
        publication, "publish_staged_update", lambda *a, **k: pytest.fail("dispatched")
    )
    with pytest.raises(ValueError, match="ZIP inventory"):
        publication.publish(source, target, work, visual, home=tmp_path)


@pytest.mark.parametrize("when", ["package", "upload", "bind"])
def test_approved_profile_cannot_change_during_publication(tmp_path, monkeypatch, when):
    source, target, work, visual = setup_report(tmp_path)
    selected = tmp_path / "successor.json"
    save_json(selected, {"cohort": "approved", "local_example": True})
    expected = digest(selected.read_bytes())
    save_json(source / "report-manifest.json", {"profile_sha256": expected})
    bound = read_json(target)
    bound["approved_profile_successor"] = {
        "from_sha256": read_json(target)["profile_sha256"],
        "to_sha256": expected,
        "profile_path": str(selected),
    }
    save_json(target, bound)

    def change():
        save_json(selected, {"cohort": "changed"})

    def package(*args, **kwargs):
        if when == "package":
            change()
        return {"archive_sha256": "archive", "report_manifest_sha256": "manifest"}

    def update(staged, **kwargs):
        assert selected in staged.inputs
        change()
        if when == "bind":
            staged.bind("ver_after")
        else:
            staged.guard()
        pytest.fail("changed profile accepted")

    monkeypatch.setattr(publication, "package", package)
    monkeypatch.setattr(publication, "publish_staged_update", update)
    with pytest.raises(ValueError, match="successor profile changed"):
        publication.publish(source, target, work, visual, home=tmp_path)
    assert read_json(target)["expected_version"] == "ver_before"
    assert read_json(target)["approved_profile_successor"]["to_sha256"] == expected


def test_bound_target_profile_reaches_the_real_packager(tmp_path, monkeypatch):
    """Publishing resolves a JSON path and packages it through the shared boundary."""
    from harness.web_extract import report as report_owner

    source, target, work, visual = setup_report(tmp_path)
    manifest = read_json(source / "report-manifest.json")
    manifest["files"] = {
        "browser-eval-version.json": digest((source / "browser-eval-version.json").read_bytes())
    }
    save_json(source / "report-manifest.json", manifest)
    save_json(visual, {})
    audit_calls = []
    original_audit = report_owner.verify_completion_audit

    def verify(*args, **kwargs):
        audit_calls.append(1)
        return original_audit(*args, **kwargs)

    monkeypatch.setattr(publication, "verify_completion_audit", verify)
    monkeypatch.setattr(report_owner, "verify_completion_audit", verify)
    monkeypatch.setattr(
        report_owner,
        "check",
        lambda report, **kwargs: verify(report, kwargs["profile"], home=kwargs["home"]),
    )

    def update(staged, *, transport):
        assert set(publication.archive_inventory(staged.archive)) == {
            "report-manifest.json",
            "browser-eval-version.json",
        }
        staged.guard()
        staged.bind("ver_packaged")
        return "ver_packaged"

    monkeypatch.setattr(publication, "publish_staged_update", update)
    result = publication.publish(source, target, work, visual, home=tmp_path)
    assert result["versionId"] == "ver_packaged"
    assert len(audit_calls) == 2  # Package and upload have independent frozen snapshots.


def test_upload_snapshot_includes_original_dataset_audit_calls_and_repairs(tmp_path):
    from harness.web_extract.report import completion_authorities

    home = tmp_path / "home"
    content = tmp_path / "content.json"
    profile = tmp_path / "profile.json"
    save_json(
        profile,
        {"dataset": {"id": "frozen"}, "research_report": {"content": {"path": str(content)}}},
    )
    save_json(
        content,
        {
            "audit": {"path": "audit.json"},
            "cohort_plan": {"path": str(home / "plan.json")},
            "reference_revision": {"path": str(home / "reference.json")},
            "dataset_manifest": str(tmp_path / "retired-home/dataset/manifest.json"),
            "evidence_runs": [
                {
                    "run": "first",
                    "plan": str(home / "plan.json"),
                    "assessment": {"path": str(home / "assessment.json")},
                    "repair": [{"path": str(home / "repairs/final")}],
                },
                {"run": "runs/second"},
            ],
        },
    )
    assert set(completion_authorities(profile, home)) == {
        profile,
        content,
        tmp_path / "audit.json",
        home / "dataset",
        home / "runs/first",
        home / "runs/second",
        home / "repairs/final",
        home / "plan.json",
        home / "reference.json",
        home / "assessment.json",
    }


def test_release_check_rejects_missing_profile_before_opening_report(tmp_path, capsys):
    from harness.web_extract.cli import main
    from harness.web_extract.report import check

    with pytest.raises(SystemExit) as error:
        main(["--home", str(tmp_path), "check", str(tmp_path / "absent"), "--release"])
    assert error.value.code == 2
    assert "--profile" in capsys.readouterr().err
    with pytest.raises(ValueError, match="explicit profile"):
        check(tmp_path / "absent", release=True, home=tmp_path)


@pytest.mark.parametrize("operation", ["check", "release_check", "package", "publish"])
def test_registered_release_commands_supply_real_cli_requirements(tmp_path, monkeypatch, operation):
    import shlex
    from pathlib import Path

    from harness.distribution import repository_root
    from harness.web_extract import cli, report

    command = read_json(repository_root() / "configs/report-templates.json")["tracks"][
        "web-extract"
    ][operation]
    argv = shlex.split(command)[5:]  # uv run --frozen python -m MODULE
    assert argv.pop(0) == "harness.web_extract.cli"
    called = []

    def boundary(*args, **kwargs):
        assert kwargs["home"] == Path("/absolute/benchmark-home")
        if operation != "publish":
            assert kwargs["profile"] == Path("/absolute/report-profile.json")
        called.append(True)
        return {}

    monkeypatch.setattr(
        publication if operation == "publish" else report,
        {"check": "check", "release_check": "check", "package": "package", "publish": "publish"}[
            operation
        ],
        boundary,
    )
    cli.main(argv)
    assert called == [True]


@pytest.mark.parametrize("mode", ["full-cohort-progress", "field-extraction-cohort"])
def test_check_verifies_original_evidence_once_for_each_cohort(tmp_path, monkeypatch, mode):
    from harness.web_extract import report
    from harness.web_extract.example import create

    output = tmp_path / "report"
    home = create(output, mode=mode)
    calls = []
    monkeypatch.setattr(report, "verify_completion_audit", lambda *a, **k: calls.append(1))
    report.check(output, profile=home / "profile.json", home=home)
    assert calls == [1]


@pytest.mark.parametrize(
    "changed",
    [
        "profile",
        "content",
        "audit",
        "dataset",
        "execution",
        "plan",
        "assessment",
        "reference",
    ],
)
def test_packaging_rejects_private_authority_changes_during_validation(
    tmp_path, monkeypatch, changed
):
    from harness.web_extract import report

    source, target, work, visual = setup_report(tmp_path)
    paths = {
        "profile": tmp_path / "profile.json",
        "content": tmp_path / "content.json",
        "audit": tmp_path / "audit.json",
        "dataset": tmp_path / "dataset/page.html",
        "execution": tmp_path / "runs/first/call.json",
        "plan": tmp_path / "plan.json",
        "assessment": tmp_path / "assessment.json",
        "reference": tmp_path / "reference.json",
    }
    for path in paths.values():
        save_json(path, {})
    save_json(
        paths["content"],
        {
            "audit": {"path": "audit.json"},
            "reference_revision": {"path": str(paths["reference"])},
            "evidence_runs": [
                {
                    "run": "first",
                    "plan": str(paths["plan"]),
                    "assessment": {"path": str(paths["assessment"])},
                }
            ],
        },
    )
    save_json(
        paths["profile"],
        {"dataset": {"id": "frozen"}, "research_report": {"content": {"path": "content.json"}}},
    )

    def validation(*args, **kwargs):
        paths[changed].write_text("substituted bytes", encoding="utf-8")

    monkeypatch.setattr(report, "check", validation)
    archive = tmp_path / "report.zip"
    with pytest.raises(ValueError, match="changed"):
        report.package(
            source, archive, visual_receipt=visual, profile=paths["profile"], home=tmp_path
        )
    assert not archive.exists()
    assert not archive.with_suffix(".receipt.json").exists()


@pytest.mark.parametrize("missing", ["both", "profile", "home"])
def test_public_check_never_infers_or_omits_its_authorities(tmp_path, missing, capsys):
    from harness.web_extract.cli import main
    from harness.web_extract.report import check

    arguments = ["check", str(tmp_path / "absent-report")]
    if missing == "home":
        arguments += ["--profile", str(tmp_path / "profile.json")]
    if missing == "profile":
        arguments = ["--home", str(tmp_path)] + arguments
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2
    assert ("--home" if missing == "home" else "--profile") in capsys.readouterr().err
    with pytest.raises(ValueError, match="explicit profile and benchmark home"):
        check(tmp_path / "absent-report")


def test_standalone_package_preserves_previous_release_when_audit_changes_during_zip(
    tmp_path, monkeypatch
):
    import zipfile

    from harness.web_extract import report as owner

    source, _, _, visual = setup_report(tmp_path)
    audit, content = tmp_path / 'audit.json', tmp_path / 'content.json'
    save_json(audit, {'result_count': 1})
    save_json(content, {'audit': {'path': str(audit), 'sha256': digest(audit.read_bytes())}})
    profile = tmp_path / 'profile.json'
    save_json(profile, {'local_example': True, 'research_report': {'content': {
        'path': str(content), 'sha256': digest(content.read_bytes())
    }}})
    manifest = read_json(source / 'report-manifest.json')
    manifest.update(profile_sha256=digest(profile.read_bytes()), files={
        'browser-eval-version.json': digest((source / 'browser-eval-version.json').read_bytes())
    })
    save_json(source / 'report-manifest.json', manifest)
    save_json(visual, {})
    archive = tmp_path / 'report.zip'
    receipt = archive.with_suffix('.receipt.json')
    archive.write_bytes(b'previous release')
    receipt.write_bytes(b'previous proof')
    monkeypatch.setattr(owner, 'verify_completion_audit', lambda *a, **k: None)
    monkeypatch.setattr(owner, 'check', lambda *a, **k: None)
    original = zipfile.ZipFile.write

    def change_after_validation(*args, **kwargs):
        result = original(*args, **kwargs)
        save_json(audit, {'result_count': 2})
        return result

    monkeypatch.setattr(zipfile.ZipFile, 'write', change_after_validation)
    with pytest.raises(ValueError, match='inputs changed during packaging'):
        owner.package(source, archive, visual_receipt=visual, profile=profile, home=tmp_path)
    assert archive.read_bytes() == b'previous release'
    assert receipt.read_bytes() == b'previous proof'
