import json

import pytest

from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.metrics import task_passed
from harness.web_fetch.scoring_regressions import load_scoring_regressions


def test_verification_policy_is_explicit_and_does_not_relax_positive_requirements():
    case = {'id': 'policy', 'url': 'https://example.test/', 'task_kind': 'body',
            'acceptance_contract': {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
                'stable_text_anchors': [{'id': 'required', 'alternatives': ['Required article text']}]}}
    artifact = b'Required article text\nCAPTCHA'
    for version, expected in [(1, False), (2, True)]:
        metrics = score_attempt({**case, 'acceptance_policy_version': version}, artifact, ok=True)
        assert metrics['acceptance_policy_version'] == version
        assert task_passed(True, metrics) is expected
    assert not task_passed(True, score_attempt({**case, 'acceptance_policy_version': 2}, b'CAPTCHA', ok=True))
    with pytest.raises(ValueError, match='policy version'):
        score_attempt({**case, 'acceptance_policy_version': 3}, artifact, ok=True)


def test_historical_control_input_and_expectation_cannot_be_replaced(tmp_path):
    path = tmp_path / 'controls.json'
    path.write_text(json.dumps({'schema_version': 1, 'cases': [], 'control_revisions': {
        'overrides': {'old': {'expected_task_pass': True}}}}))
    with pytest.raises(ValueError, match='cannot override'):
        load_scoring_regressions(path)
