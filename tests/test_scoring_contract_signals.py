import json

import pytest

from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.metrics import task_passed
from harness.web_fetch.scoring_regressions import load_scoring_regressions


def test_declared_ban_is_distinct_from_heuristic_hint():
    case = {'id': 'contract', 'url': 'https://example.test/', 'task_kind': 'body',
            'required_facts': [{'id': 'body', 'alternatives': ['Required article text']}]}
    output = b'Required article text. CAPTCHA'
    allowed = score_attempt({**case, 'diagnostic_any': ['CAPTCHA']}, output, ok=True)
    banned = score_attempt({**case, 'forbidden_any': ['CAPTCHA']}, output, ok=True)
    assert task_passed(True, allowed)
    assert allowed['diagnostic_hits'] == ['CAPTCHA']
    missing = score_attempt({**case, 'diagnostic_any': ['CAPTCHA']}, b'CAPTCHA', ok=True)
    assert not task_passed(True, missing)
    assert not task_passed(True, banned)
    assert not banned['content_pass']
    assert banned['forbidden_requirement_hits'] == ['CAPTCHA']
    with pytest.raises(ValueError, match='Historical scoring metrics'):
        task_passed(True, {'content_pass': True, 'blocked_page': True})
    with pytest.raises(ValueError, match='frozen scorer'):
        score_attempt({**case, 'acceptance_policy_version': 2}, output, ok=True)


def test_current_registry_rejects_old_selector_and_silent_overrides(tmp_path):
    for extra in [{'historical_policy_versions': {'old': 1}},
                  {'control_revisions': {'overrides': {'old': {'expected_task_pass': True}}}}]:
        path = tmp_path/'controls.json'
        path.write_text(json.dumps({'schema_version': 1, 'cases': [], **extra}))
        with pytest.raises(ValueError):
            load_scoring_regressions(path)


@pytest.mark.parametrize('contract', [None, {'version': 1, 'schema': 'body_text',
    'minimum_records': 0, 'stable_text_anchors': [{'id': 'body', 'alternatives': ['Required article text']}]}])
def test_explicit_prohibition_keeps_content_and_task_verdict_consistent(contract):
    from harness.web_fetch.metrics import evaluate_content
    values = {'forbidden_any': ['secret-token'], 'acceptance_contract': contract}
    output = 'Required article text. secret-token'
    direct = evaluate_content(output, [], **values)
    assert not direct['content_pass']
    scored = score_attempt({'id': 'a', 'url': 'https://example.test/', **values}, output.encode(), ok=True)
    assert not scored['content_pass']
    assert not task_passed(True, scored)
