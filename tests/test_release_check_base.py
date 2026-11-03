"""The shared release-check flow is reached by every track gate and fails closed."""

import json

import pytest

from harness.browser_use import report_check as browser_use
from harness.record_io import file_sha256
from harness.report_languages import write_language_contract
from harness.reporting.release_check import (
    check_report_identity,
    checked_page,
    language_editions,
    open_report,
    read_page,
)
from harness.reporting.templates import template_identity
from harness.reporting.versions import FILENAME
from harness.trajectory_replay import webarena_report_check as trajectory
from harness.web_fetch import (
    published_snapshot,
    report_check,
    report_diagnostic,
    wcxb_report_check,
    webmainbench_report_check,
)

GATES = {
    "webfetch": lambda root: report_check.check(root, authority_root=root),
    "webfetch-revision": lambda root: report_check.check_revision(root, authority_root=root),
    "webfetch-diagnostic": report_diagnostic.check_diagnostic_report,
    "webfetch-snapshot": published_snapshot.check_snapshot_audit,
    "wcxb": lambda root: wcxb_report_check.check(root, authority_root=root),
    "webmainbench": lambda root: webmainbench_report_check.check(root, authority_root=root),
    "webmainbench-sample": lambda root: webmainbench_report_check.check_sample_report(
        root, authority_root=root
    ),
    "trajectory-replay": trajectory.check,
    "browser-use": lambda root: browser_use.check(
        root, root, None, require_complete=False, authority_root=root
    ),
}
# Directional highlight on the smaller of two values, so only the shared rule can reject it.
WRONG_HIGHLIGHT = """<table><tr>
<td data-metric="pass" data-metric-direction="max" data-sort="1"><span class="comparison-winner">1</span></td>
<td data-metric="pass" data-metric-direction="max" data-sort="2">2</td></tr></table>"""


@pytest.mark.parametrize("gate", GATES.values(), ids=GATES)
def test_every_gate_refuses_a_linked_report_before_reading_it(tmp_path, gate):
    (tmp_path / "linked").symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="symlink"):
        gate(tmp_path)


@pytest.mark.parametrize("gate", GATES.values(), ids=GATES)
def test_every_gate_goes_on_to_its_own_rules_without_a_link(tmp_path, gate):
    with pytest.raises((OSError, ValueError, KeyError)) as failure:
        gate(tmp_path)
    assert "symlink" not in str(failure.value)


def test_open_report_returns_the_resolved_root(tmp_path, monkeypatch):
    (tmp_path / "report").mkdir()
    monkeypatch.chdir(tmp_path)
    assert open_report("report") == (tmp_path / "report").resolve()


def test_checked_page_applies_the_shared_presentation_rules(tmp_path):
    page = tmp_path / "index.html"
    page.write_text(WRONG_HIGHLIGHT, encoding="utf-8")
    assert read_page(page).select_one(".comparison-winner")
    with pytest.raises(ValueError, match="highlight"):
        checked_page(page)
    page.write_text(WRONG_HIGHLIGHT.replace('data-sort="2"', 'data-sort="0"'), encoding="utf-8")
    assert checked_page(page).select_one(".comparison-winner")


@pytest.mark.parametrize(
    ("english", "expected"),
    [(False, (("zh", ""),)), (True, (("zh", ""), ("en", "en/")))],
)
def test_language_editions_follow_the_language_contract(tmp_path, english, expected):
    write_language_contract(tmp_path, include_english=english)
    assert language_editions(tmp_path) == expected


def _bound_manifest(profile):
    profile.write_text("{}", encoding="utf-8")
    return {"profile_sha256": file_sha256(profile), "template": template_identity("browser-use")}


def test_report_identity_accepts_the_bound_profile(tmp_path):
    profile = tmp_path / "profile.json"
    check_report_identity(tmp_path, _bound_manifest(profile), "browser-use", profile)


@pytest.mark.parametrize("defect", ["changed", "unbound", "linked"])
def test_report_identity_rejects_a_different_profile(tmp_path, defect):
    profile = tmp_path / "profile.json"
    manifest = _bound_manifest(profile)
    if defect == "changed":
        profile.write_text('{"changed": true}', encoding="utf-8")
    elif defect == "unbound":
        del manifest["profile_sha256"]
    else:
        target = tmp_path / "target.json"
        profile.rename(target)
        profile.symlink_to(target)
    with pytest.raises(ValueError, match="different instance profile"):
        check_report_identity(tmp_path, manifest, "browser-use", profile)


def test_only_a_declared_legacy_track_accepts_an_unbound_report(tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text("{}", encoding="utf-8")
    check_report_identity(tmp_path, {}, "browser-use", profile, allow_unbound_profile=True)
    with pytest.raises(ValueError, match="different instance profile"):
        check_report_identity(tmp_path, {"profile_sha256": "0" * 64, "template": template_identity("browser-use")},
                              "browser-use", profile, allow_unbound_profile=True)


def test_report_identity_still_binds_the_template_and_the_build(tmp_path):
    profile = tmp_path / "profile.json"
    bypassed = {**_bound_manifest(profile), "template": {"track": "other", "renderer": "other.py"}}
    with pytest.raises(ValueError, match="bypasses the maintained"):
        check_report_identity(tmp_path, bypassed, "browser-use")
    (tmp_path / FILENAME).write_text(json.dumps({"role": "report_builder"}), encoding="utf-8")
    with pytest.raises(ValueError, match="version binding"):
        check_report_identity(tmp_path, {}, "browser-use")
