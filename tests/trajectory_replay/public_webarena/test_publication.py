from __future__ import annotations

import json

import pytest

from harness import env_file
from harness.trajectory_replay.public_webarena.publication import (
    build,
    export_public_report_bundle,
    public_projection,
    stage_report,
)
from tests.public_webarena_fixture import write_report_fixture


def write_site_logins(path, reddit_password="synthetic-reddit-secret"):
    """Write a complete synthetic .env, as packaging requires every registered login."""
    path.write_text(
        "".join(
            f"WEBARENA_{site}_USERNAME=synthetic-{site.lower()}-user\n"
            f"WEBARENA_{site}_PASSWORD=synthetic-{site.lower()}-secret\n"
            for site in ("SHOPPING", "SHOPPING_ADMIN", "GITLAB")
        )
        + "WEBARENA_REDDIT_USERNAME=synthetic-reddit-user\n"
        + f"WEBARENA_REDDIT_PASSWORD={reddit_password}\n",
        encoding="utf-8",
    )


def test_public_projection_scrubs_private_hosts_and_user_paths():
    value = {"url": "http://10.0.0.41:17770/x", "path": "/data/example/run/file"}
    assert public_projection(value) == {
        "url": "http://webarena.local:17770/x",
        "path": "/[local-user]/run/file",
    }


def test_public_projection_needs_no_site_logins_to_rebuild_saved_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(env_file, "_repository_credentials_file", tmp_path / "absent.env")
    assert public_projection({"note": "unchanged"}) == {"note": "unchanged"}


@pytest.mark.parametrize(
    ("password", "written"),
    [
        ("leaked-secret", "leaked-secret"),
        ('secret"value', 'secret"value'),
        ('secret"value', "secret&quot;value"),
        ('secret"value', "secret&#34;value"),
        ('secret"value', 'secret\\"value'),
        ('secret"value', "secret\\&quot;value"),
        ("sécret", "s\\u00e9cret"),
        ("it's\"both", "it\\'s\"both"),
        ("ab'\tcd", "ab\\'\\tcd"),
    ],
)
def test_public_package_rejects_a_site_password_the_projection_missed(
    tmp_path, monkeypatch, password, written
):
    source, authority = write_report_fixture(tmp_path)
    staged = tmp_path / "staged"
    stage_report(source, staged)
    page = staged / "reports/webarena-trajectory-replay-latest.html"
    page.write_text(page.read_text(encoding="utf-8") + written, encoding="utf-8")
    secrets = tmp_path / "repository.env"
    write_site_logins(secrets, password)
    monkeypatch.setattr(env_file, "_repository_credentials_file", secrets)
    monkeypatch.setenv("WEBARENA_REDDIT_PASSWORD", "exported-secret")

    with pytest.raises(ValueError, match="site password in public report"):
        build(staged, tmp_path / "a.zip", tmp_path / "a.json", authority=authority)
    assert not (tmp_path / "a.zip").exists()


@pytest.mark.parametrize(
    "written",
    ['a"&xtailx', 'a"&amp;xtailx', "a&quot;&amp;xtailx", 'a\\"&amp;xtailx', "a\\&quot;&amp;xtailx"],
)
def test_public_projection_redacts_escaped_site_passwords(tmp_path, monkeypatch, written):
    secrets = tmp_path / "repository.env"
    secrets.write_text('WEBARENA_REDDIT_PASSWORD=a"&xtailx\n', encoding="utf-8")
    monkeypatch.setattr(env_file, "_repository_credentials_file", secrets)

    assert public_projection({"error": f"alert: {written}"}) == {"error": "alert: [REDACTED]"}


def test_public_package_accepts_a_report_without_the_site_password(tmp_path, monkeypatch):
    source, authority = write_report_fixture(tmp_path)
    staged = tmp_path / "staged"
    stage_report(source, staged)
    secrets = tmp_path / "repository.env"
    write_site_logins(secrets, 'secret"value')
    monkeypatch.setattr(env_file, "_repository_credentials_file", secrets)

    build(staged, tmp_path / "a.zip", tmp_path / "a.json", authority=authority)

    assert (tmp_path / "a.zip").is_file()


@pytest.mark.parametrize("unset", ["WEBARENA_REDDIT_PASSWORD", "WEBARENA_GITLAB_PASSWORD"])
def test_public_package_stops_while_a_site_password_is_unset(tmp_path, monkeypatch, unset):
    source, authority = write_report_fixture(tmp_path)
    secrets = tmp_path / "repository.env"
    write_site_logins(secrets)
    secrets.write_text(
        "".join(
            line
            for line in secrets.read_text(encoding="utf-8").splitlines(keepends=True)
            if not line.startswith(unset + "=")
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(env_file, "_repository_credentials_file", secrets)
    monkeypatch.setenv(unset, "exported-secret")

    staged = tmp_path / "staged"
    stage_report(source, staged)
    assert (staged / "reports/webarena-trajectory-replay-latest.html").is_file()

    with pytest.raises(ValueError, match=unset):
        build(staged, tmp_path / "a.zip", tmp_path / "a.json", authority=authority)
    assert not (tmp_path / "a.zip").exists()
    assert not (tmp_path / "a.json").exists()


def test_public_export_stops_while_a_site_password_is_unset(tmp_path, monkeypatch):
    source, _ = write_report_fixture(tmp_path)
    staged = tmp_path / "staged"
    stage_report(source, staged)
    page = staged / "reports/webarena-trajectory-replay-latest.html"
    page.write_text(page.read_text(encoding="utf-8") + "secret-value", encoding="utf-8")
    monkeypatch.setattr(env_file, "_repository_credentials_file", tmp_path / "absent.env")
    monkeypatch.setenv("WEBARENA_REDDIT_PASSWORD", "secret-value")

    with pytest.raises(ValueError, match="WEBARENA_"):
        export_public_report_bundle(staged, tmp_path / "public")
    assert not (tmp_path / "public").exists()


@pytest.mark.parametrize(
    "written", ["secret-value", "secret&#45;value"], ids=["plain", "html-escaped"]
)
def test_public_export_rejects_a_site_password_in_a_report_page(tmp_path, monkeypatch, written):
    source, _ = write_report_fixture(tmp_path)
    staged = tmp_path / "staged"
    stage_report(source, staged)
    page = staged / "reports/webarena-trajectory-replay-latest.html"
    page.write_text(page.read_text(encoding="utf-8") + written, encoding="utf-8")
    secrets = tmp_path / "repository.env"
    write_site_logins(secrets, "secret-value")
    monkeypatch.setattr(env_file, "_repository_credentials_file", secrets)

    with pytest.raises(ValueError, match="site password in public report"):
        export_public_report_bundle(staged, tmp_path / "public")
    assert not (tmp_path / "public").exists()


def test_public_bundle_contains_reports_and_minimal_binding(tmp_path):
    source, _ = write_report_fixture(tmp_path)
    manifest_path = source / "run/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_provenance"] = {"artifact_directory": "/data/example/run"}
    manifest["private_notes"] = "private reviewer notes"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    source_summary = json.loads((source / "run/summary.json").read_text(encoding="utf-8"))
    formal = tmp_path / "formal"
    stage_report(source, formal)

    target = export_public_report_bundle(formal, tmp_path / "public")

    manifest = json.loads((target / "run/manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((target / "run/summary.json").read_text(encoding="utf-8"))
    assert manifest == {
        "suite_sha256": "suite",
        "artifact_provenance": {"artifact_directory": "/[local-user]/run"},
    }
    assert summary == {"functional": source_summary["functional"], "resource_comparison": {}}
    assert (target / "reports/webarena-trajectory-replay-latest.html").read_bytes() == (
        formal / "reports/webarena-trajectory-replay-latest.html").read_bytes()
