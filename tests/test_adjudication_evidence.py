import hashlib

import pytest

from harness.web_fetch.adjudication_evidence import case_hash, require_adjudication_evidence, TEMPLATE


def test_overridden_review_requires_rules_and_verifiable_answer_excerpt(tmp_path):
    text = 'Home\n0:00 / Live · Watch full video'
    (tmp_path / 'answer.txt').write_text(text)
    case = {'id': 'a', 'task_prompt': 'Player controls do not substitute for ordinary text'}
    record = {'attempt_id': 'p__a', 'case_id': 'a', 'artifact_path': 'answer.txt',
              'metrics': {'content_sha256': hashlib.sha256(text.encode()).hexdigest()}}
    row = {'attempt_id': 'p__a', 'agent_verdict': 'pass', 'final_verdict': 'fail', 'adjudication': TEMPLATE}
    def check():
        return require_adjudication_evidence(tmp_path, {'cases': [case]}, [record], {'attempts': [row]})
    with pytest.raises(ValueError, match='specific rule/evidence'):
        check()
    row['adjudication'] = 'Live occurs only in the excluded player control, so the required ordinary-text group is missing.'
    row['adjudication_evidence'] = {'reason': row['adjudication'], 'case_sha256': case_hash(case),
        'content_sha256': record['metrics']['content_sha256'], 'rule_ids': ['ordinary-text:Live'],
        'samples': [{'start': 5, 'end': len(text), 'text': text[5:]}]}
    assert check() == 1
    row['adjudication_evidence']['samples'][0]['text'] = 'invented evidence'
    with pytest.raises(ValueError, match='excerpt differs'):
        check()
