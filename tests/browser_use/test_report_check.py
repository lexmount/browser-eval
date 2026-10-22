"""Public checker boundary scenarios independent of measured cohorts."""

import hashlib
import json
from pathlib import Path

import pytest

from harness.browser_use import report_check as checker


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
        )
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
        outside.write_text("{}")
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
    review.write_text("{}")
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
        outside.write_text("{}")
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
    source.write_text('{}')
    profile = authority(root, failure_cause_review={
        'required_for_publication': True, 'source_path': str(source),
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
    import harness.browser_use.report as renderer
    for name in ('ENGINE_LABELS', 'MODEL_LABELS', 'BASELINE_ENGINE'):
        monkeypatch.setattr(renderer, name, getattr(renderer, name))
    external = tmp_path / 'instance.json'
    external.write_bytes(profile.read_bytes())
    (profile.parent / 'webarena-verified-pilot.json').write_text('{}')
    profile.unlink()
    def evidence_boundary(_paths):
        raise LookupError('bound external inputs reached')
    monkeypatch.setattr(checker, '_load_evidence_bundles', evidence_boundary)
    with pytest.raises(LookupError, match='external inputs reached'):
        checker.check(tmp_path / 'report', tmp_path / 'evidence', None,
                      require_complete=True, profile_path=external, authority_root=root)
    source.write_text('{"changed":true}')
    with pytest.raises(ValueError, match='bound to the failure-cause review'):
        checker.check(tmp_path / 'report', tmp_path / 'evidence', None,
                      require_complete=True, profile_path=external, authority_root=root)


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
