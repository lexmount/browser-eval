"""A newly checked record must not be presented as a blind review by default."""
import hashlib
import json

import pytest

from harness.web_fetch import report


@pytest.mark.parametrize('blind', [False, True])
def test_current_review_text_reports_actual_blindness(tmp_path, blind):
    replacement = {'provider_calls_added': 0, 'inherited_results': 1}
    raw = json.dumps(replacement).encode()
    (tmp_path/'replacement.json').write_bytes(raw)
    dataset = {'cases': [{'id': 'case', 'track': 'web'}], 'comparison': {
        'task_replacement': {'path': 'replacement.json',
                             'sha256': hashlib.sha256(raw).hexdigest()}}}
    record = {'attempt_id': 'method__case', 'case_id': 'case', 'ok': True,
              'metrics': {'task_kind': 'body', 'content_pass': True}}
    audit = {'kind': 'local_independent_review', 'review_protocol': {
        'scope': 'all', 'blind_initial_verdict': blind},
        'reviewer': {}, 'reviewed_attempts': 1, 'expected_attempts': 1,
        'fresh_reviewed_attempts': 1, 'retained_reviewed_attempts': 0,
        'retained_reviews': {'path': 'prior.json'},
        'supplemental_reviews': [{'path': 'supplement.json'}],
        'attempts': [{'attempt_id': record['attempt_id'], 'review_origin': 'fresh',
                      'agent_verdict': 'pass', 'machine_verdict': 'pass',
                      'final_verdict': 'pass'}],
        'review_rounds': [], 'unresolved': 0,
        'adjudication_revision': {'adjudicated_attempt_ids': []}}
    (tmp_path/'result-review.json').write_text(json.dumps(audit))
    (tmp_path/'dataset.json').write_text(json.dumps(dataset))
    summary = report.review_summary(tmp_path)
    findings = report.current_local_review_findings(tmp_path, dataset, [record], audit)
    text = '\n'.join(row['text']+' '+row['text_en'] for row in findings)
    if blind:
        assert '先隐藏程序结论' in text
        assert 'This round checked evidence with known machine outcomes' not in summary
    else:
        assert '已知程序结论' in summary
        assert 'known machine outcomes' in summary
        assert '先隐藏程序结论' not in text
        assert 'Fresh review withheld machine verdicts' not in text
        assert '不是独立盲审' in summary
    report.write_review_page(tmp_path, [record])
    page = (tmp_path/'review/index.html').read_text()
    if not blind:
        assert '本次重新检查的记录采用已知程序结论的证据复核' in page
        assert '发生变化的调用必须重新盲审' not in page
