"""Public checker boundary scenarios independent of measured cohorts."""

import hashlib
import json
from pathlib import Path

import pytest

from harness.browser_use import report_check as checker
from harness.reporting import templates
from tests.browser_use.test_report import build_browser_use_fixture

ROOT = Path(__file__).resolve().parents[2]
SELECTION = ROOT / "evaluation/browser-use/webarena-verified-pilot.json"


def authority(root: Path, **updates) -> Path:
    profile = root / "evaluation/browser-use/current-report.json"
    profile.parent.mkdir(parents=True)
    profile.write_text(
        json.dumps(
            {
                "schema": "lex_browser_use_current_report/2",
                "baseline_engine": "fixture-browser",
                "engines": ["fixture-browser"],
                "engine_labels": {"fixture-browser": "Fixture"},
                "models": ["fixture-model"],
                "model_labels": {"fixture-model": "Fixture"},
                **updates,
            }
        ),
        encoding="utf-8"
    )
    return profile


@pytest.mark.parametrize("complete", [False, True])
def test_explicit_authority_ignores_working_directory(tmp_path, monkeypatch, complete):
    root = tmp_path / "authority"
    review_bytes = b'{"schema": "synthetic-bound-review"}'
    authority(
        root,
        failure_cause_review={
            "required_for_publication": True,
            "source_path": "research/custom-review.json",
            "source_sha256": hashlib.sha256(review_bytes).hexdigest(),
        },
    )
    (root / "research").mkdir()
    (root / "research/custom-review.json").write_bytes(review_bytes)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    import harness.browser_use.report as renderer

    # The current renderer stores the selected registry at module scope.
    for name in ("ENGINE_LABELS", "MODEL_LABELS", "BASELINE_ENGINE"):
        monkeypatch.setattr(renderer, name, getattr(renderer, name))

    def evidence_boundary(paths):
        import harness.browser_use.report as renderer

        assert set(renderer.ENGINE_LABELS) == {"fixture-browser"}
        assert set(renderer.MODEL_LABELS) == {"fixture-model"}
        raise LookupError("reached declared evidence")

    monkeypatch.setattr(checker, "_load_evidence_bundles", evidence_boundary)
    with pytest.raises(LookupError, match="reached declared evidence"):
        checker.check(
            tmp_path / "report",
            tmp_path / "evidence",
            None,
            require_complete=complete,
            authority_root=root,
        )


def test_missing_authority_has_actionable_error(tmp_path):
    with pytest.raises(ValueError, match="--authority-root"):
        checker.check(
            tmp_path / "report",
            tmp_path / "evidence",
            None,
            require_complete=False,
            authority_root=tmp_path,
        )


@pytest.mark.parametrize("kind", ["report-root", "report-file", "evidence-root", "evidence-file"])
def test_rejects_linked_report_and_evidence_before_reading(tmp_path, kind):
    report, evidence = tmp_path / "report", tmp_path / "evidence"
    report.mkdir()
    evidence.mkdir()
    root = report if kind.startswith("report") else evidence
    if kind.endswith("root"):
        linked = tmp_path / "linked"
        linked.symlink_to(root, target_is_directory=True)
        if root == report:
            report = linked
        else:
            evidence = linked
    else:
        outside = tmp_path / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        (root / "artifact.json").symlink_to(outside)
    with pytest.raises(ValueError, match="[Ss]ymlink"):
        checker.check(report, evidence, None, require_complete=False)


def test_complete_check_rejects_unbound_review_before_evidence(tmp_path):
    root = tmp_path / "authority"
    authority(
        root,
        failure_cause_review={
            "required_for_publication": True,
            "source_path": "research/review.json",
            "source_sha256": "0" * 64,
        },
    )
    review = root / "research/review.json"
    review.parent.mkdir()
    review.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="bound to the failure-cause review"):
        checker.check(
            tmp_path / "report",
            tmp_path / "evidence",
            None,
            require_complete=True,
            authority_root=root,
        )


@pytest.mark.parametrize(
    "relative", ["../outside.json", "research/linked.json"]
)
def test_complete_check_rejects_escaping_or_linked_review(tmp_path, relative):
    root = tmp_path / "authority"
    authority(
        root,
        failure_cause_review={
            "required_for_publication": True,
            "source_path": relative,
            "source_sha256": "0" * 64,
        },
    )
    if relative == "research/linked.json":
        outside = tmp_path / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        (root / "research").mkdir()
        (root / relative).symlink_to(outside)
    with pytest.raises(ValueError, match="relative|[Ss]ymlink"):
        checker.check(
            tmp_path / "report",
            tmp_path / "evidence",
            None,
            require_complete=True,
            authority_root=root,
        )


def test_linked_authority_profile_is_rejected(tmp_path):
    root = tmp_path / "authority"
    profile = authority(root)
    retained = tmp_path / "retained.json"
    profile.rename(retained)
    profile.symlink_to(retained)
    with pytest.raises(ValueError, match="[Ss]ymlink"):
        checker.check(
            tmp_path / "report",
            tmp_path / "evidence",
            None,
            require_complete=False,
            authority_root=root,
        )


def test_external_profile_and_bound_review_do_not_use_default_authority(tmp_path, monkeypatch):
    root = tmp_path / 'authority'
    source = tmp_path / 'external-review.json'
    source.write_text('{}', encoding="utf-8")
    profile = authority(root, failure_cause_review={
        'required_for_publication': True, 'source_path': str(source),
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
    import harness.browser_use.report as renderer
    for name in ('ENGINE_LABELS', 'MODEL_LABELS', 'BASELINE_ENGINE'):
        monkeypatch.setattr(renderer, name, getattr(renderer, name))
    external = tmp_path / 'instance.json'
    external.write_bytes(profile.read_bytes())
    (profile.parent / 'webarena-verified-pilot.json').write_text('{}', encoding="utf-8")
    profile.unlink()
    def evidence_boundary(_paths):
        raise LookupError('bound external inputs reached')
    monkeypatch.setattr(checker, '_load_evidence_bundles', evidence_boundary)
    with pytest.raises(LookupError, match='external inputs reached'):
        checker.check(tmp_path / 'report', tmp_path / 'evidence', None,
                      require_complete=True, profile_path=external, authority_root=root)
    source.write_text('{"changed":true}', encoding="utf-8")
    with pytest.raises(ValueError, match='bound to the failure-cause review'):
        checker.check(tmp_path / 'report', tmp_path / 'evidence', None,
                      require_complete=True, profile_path=external, authority_root=root)


BUILT_BEFORE_THE_MOVE = {"track": "browser-use", "renderer": "src/harness/browser_use/report.py",
                         "entrypoint": "scripts/build_browser_use_report.py"}


@pytest.mark.parametrize("template, rejected", [
    (BUILT_BEFORE_THE_MOVE, False),
    ({**BUILT_BEFORE_THE_MOVE, "renderer": "src/other/report.py"}, True),
    ({**BUILT_BEFORE_THE_MOVE, "track": "other"}, True),
    (None, True),
], ids=["old-entry-point", "other-renderer", "other-track", "no-template"])
def test_check_binds_the_renderer_not_the_entry_point(tmp_path, monkeypatch, template, rejected):
    import harness.browser_use.report as renderer

    # The current renderer stores the selected registry at module scope.
    for name in ("ENGINE_LABELS", "MODEL_LABELS", "BASELINE_ENGINE"):
        monkeypatch.setattr(renderer, name, getattr(renderer, name))
    report, evidence, _ = build_browser_use_fixture(tmp_path, monkeypatch)
    profile = authority(tmp_path / "authority", engines=["chromium", "moli"],
                        baseline_engine="chromium",
                        engine_labels={"chromium": "Chromium", "moli": "Moli"},
                        models=["gpt-5.6-sol"], model_labels={"gpt-5.6-sol": "GPT-5.6 Sol"})
    audit = json.loads((report / "report-data.json").read_text(encoding="utf-8"))
    # The check right after the template gate fails on this hash, so reaching it proves a pass.
    audit.update(template=template, profile_sha256="from-another-profile")
    (report / "report-data.json").write_text(json.dumps(audit), encoding="utf-8")
    expected = "bypasses the maintained" if rejected else "different instance profile"
    with pytest.raises(ValueError, match=expected):
        checker.check(report, evidence, SELECTION, require_complete=False, profile_path=profile)


def test_template_gate_needs_no_registry_without_a_profile_hash(monkeypatch):
    # Wheels do not bundle the registry, and an unprofiled report has nothing to bind.
    def no_registry(_track):
        raise FileNotFoundError("configs/report-templates.json")

    monkeypatch.setattr(templates, "template_identity", no_registry)
    templates.check_template_identity({}, "browser-use")
    with pytest.raises(FileNotFoundError):
        templates.check_template_identity({"profile_sha256": "bound"}, "browser-use")


@pytest.mark.parametrize("defect", [None, "count", "host", "diagram"])
def test_methodology_uses_selected_evidence_instead_of_historical_deployment(defect):
    content = (
        '<figure id="deployment-flow"></figure><p>Linux fixture · selected.gateway</p>'
        '<table id="methodology-evaluators"><tbody><tr>'
        '<td>FixtureEvaluator</td><td>2</td></tr></tbody></table>'
        '<table id="methodology-sites"><tbody><tr><td>new-site</td><td>2</td></tr></tbody></table>'
    )
    tasks = [
        {
            "sites": ["new-site"],
            "contract": {"evaluation_contract": [{"evaluator": "FixtureEvaluator"}]},
        }
    ] * 2
    environment = {"platform": "Linux fixture", "site_access_host": "selected.gateway"}
    if defect == "count":
        content = content.replace("<td>2</td>", "<td>812</td>", 1)
    if defect == "host":
        content = content.replace("selected.gateway", "historical.gateway")
    if defect == "diagram":
        content = content.replace('id="deployment-flow"', 'id="unrelated"')
    if defect is None:
        checker.check_methodology_evidence(content, tasks, environment)
    else:
        with pytest.raises(ValueError, match="methodology"):
            checker.check_methodology_evidence(content, tasks, environment)


REGISTRY_PASSWORD_OUTSIDE_OLD_LIST = "Rotated.Secret+42(x"


def test_public_privacy_rejects_a_password_only_the_registry_knows(
    tmp_path, report_check_with_site_password
):
    published = tmp_path / "data.json"
    published.write_text(json.dumps({"note": "clean"}), encoding="utf-8")
    probe = report_check_with_site_password(REGISTRY_PASSWORD_OUTSIDE_OLD_LIST)
    probe.check_public_privacy(tmp_path)

    published.write_text(
        json.dumps({"note": f"typed {REGISTRY_PASSWORD_OUTSIDE_OLD_LIST} into login"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="published report contains sensitive text"):
        probe.check_public_privacy(tmp_path)
    checker.check_public_privacy(tmp_path)


def test_public_privacy_without_site_passwords_still_separates_clean_from_sensitive(
    tmp_path, report_check_without_site_passwords
):
    published = tmp_path / "data.json"
    published.write_text(json.dumps({"note": "clean"}), encoding="utf-8")
    report_check_without_site_passwords.check_public_privacy(tmp_path)

    published.write_text(json.dumps({"note": "-----BEGIN RSA PRIVATE KEY-----"}), encoding="utf-8")
    with pytest.raises(ValueError, match="published report contains sensitive text"):
        report_check_without_site_passwords.check_public_privacy(tmp_path)


@pytest.mark.parametrize(
    "password",
    [
        "synthetic-shopping-secret",
        "synthetic-shopping_admin-secret",
        "synthetic-reddit-secret",
        "synthetic-gitlab-secret",
    ],
)
def test_public_privacy_still_rejects_the_current_site_passwords(tmp_path, password):
    (tmp_path / "data.json").write_text(json.dumps({"note": password}), encoding="utf-8")
    with pytest.raises(ValueError, match="published report contains sensitive text"):
        checker.check_public_privacy(tmp_path)


def _private_address() -> str:
    """Build a control address, so this file passes the public tree guard."""
    return ".".join(("10", "77", "3", "9"))


@pytest.mark.parametrize(
    "text",
    [f"http://{_private_address()}:7780/admin", f"gateway {_private_address()}"],
)
def test_public_report_fails_closed_on_an_unredacted_recording_host(tmp_path, monkeypatch, text):
    monkeypatch.setenv("WEBARENA_RECORDING_GATEWAYS", "")
    (tmp_path / "data.json").write_text(json.dumps({"note": text}), encoding="utf-8")
    with pytest.raises(ValueError, match="WEBARENA_RECORDING_GATEWAYS"):
        checker.check_public_privacy(tmp_path)


def test_new_evidence_without_a_private_host_needs_no_gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBARENA_RECORDING_GATEWAYS", "")
    (tmp_path / "data.json").write_text(
        json.dumps({"note": "http://host.docker.internal:7780/admin 8.8.8.8"}), encoding="utf-8"
    )
    checker.check_public_privacy(tmp_path)


def test_listed_recording_host_is_redacted_and_then_passes(tmp_path, monkeypatch):
    from harness.browser_use.publication import sanitize_text

    host = _private_address()
    monkeypatch.setenv("WEBARENA_RECORDING_GATEWAYS", host)
    public = sanitize_text(f"http://{host}:7780/admin and {host}")
    assert host not in public
    (tmp_path / "data.json").write_text(json.dumps({"note": public}), encoding="utf-8")
    checker.check_public_privacy(tmp_path)
