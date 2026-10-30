"""Frozen profiles bind the actual adapted metric code, not only its ancestry."""
import json
from pathlib import Path

import pytest

from harness.web_fetch import wcxb_scoring as scoring
from tests.wcxb_fixture import create_cohort


@pytest.fixture
def cohort(tmp_path):
    return create_cohort(tmp_path)


def approved_digest():
    authority = Path(scoring.__file__).with_name('data') / 'wcxb-metric-bindings.json'
    return json.loads(authority.read_bytes())['implementations'][scoring.UPSTREAM_EVALUATOR_SHA256]


def assert_rejected(cohort):
    before = cohort['profile_path'].read_bytes()
    with pytest.raises(ValueError, match='metric implementation'):
        scoring.score(**cohort)
    assert cohort['profile_path'].read_bytes() == before
    assert not (cohort['evidence_root'] / 'wcxb-scores.jsonl').exists()
    assert not (cohort['evidence_root'] / 'wcxb-score-summary.json').exists()


def test_changed_word_metric_rejects_legacy_profile(cohort, monkeypatch):
    def changed_word_metric(predicted, reference):
        return 0., 0., 0.
    monkeypatch.setattr(scoring, 'word_f1', changed_word_metric)
    assert_rejected(cohort)


def test_changed_snippet_metric_rejects_legacy_profile(cohort, monkeypatch):
    def changed_snippet_metric(text, snippets):
        return 0.
    monkeypatch.setattr(scoring, 'snippet_check', changed_snippet_metric)
    assert_rejected(cohort)


def test_explicit_profile_implementation_hash_must_match(cohort):
    profile = json.loads(cohort['profile_path'].read_bytes())
    profile['metric_implementation_sha256'] = '0' * 64
    cohort['profile_path'].write_text(json.dumps(profile))
    assert_rejected(cohort)


@pytest.mark.parametrize('explicit', [False, True])
def test_known_adaptation_scores_legacy_and_explicit_profiles_without_rewriting(cohort, explicit):
    if explicit:
        profile = json.loads(cohort['profile_path'].read_bytes())
        profile['metric_implementation_sha256'] = approved_digest()
        cohort['profile_path'].write_text(json.dumps(profile))
    before = cohort['profile_path'].read_bytes()
    summary = scoring.score(**cohort)
    assert summary['metric_implementation_sha256'] == approved_digest()
    assert summary['official_evaluator_sha256'] == scoring.UPSTREAM_EVALUATOR_SHA256
    assert summary['providers']['alpha']['f1'] == 1
    assert cohort['profile_path'].read_bytes() == before


def test_certificate_metric_binding_must_agree_with_actual_implementation(cohort):
    certificate = cohort['evidence_root'] / 'certification.json'
    value = json.loads(certificate.read_bytes())
    value['metric_implementation_sha256'] = '0' * 64
    certificate.write_text(json.dumps(value))
    assert_rejected(cohort)
