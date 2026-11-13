"""Generated-answer grading keeps source evidence, costs and populations separate."""

import hashlib
import io
import json
from copy import deepcopy

import pytest

from harness.web_search.relevance import judge_main


@pytest.fixture
def answer_run(tmp_path, monkeypatch):
    from harness.web_fetch import config
    from harness.web_search import report
    from lexbench_websearch import answers

    monkeypatch.setattr(config, 'ROOT', tmp_path)
    (tmp_path/'.env').write_text('SEARCH_JUDGE_API_KEY=fixture\nSEARCH_JUDGE_BASE_URL=https://judge.test/v1\n')
    source = b'GRADER_TEMPLATE = "Question {question}; expected {target}; answer {predicted_answer}".strip()\n'
    monkeypatch.setitem(answers.SIMPLEQA_UPSTREAM, 'sha256', hashlib.sha256(source).hexdigest())
    grader = tmp_path/'simpleqa.py'
    grader.write_bytes(source)
    cases = [dict(id='scene', query='Who wrote it?', family='web'),
             dict(id='fact', query='Who wrote it?', family='web', reference_answer='Ada', reference_urls=[]),
             dict(id='excluded', query='Unresolved reference', family='web', reference_answer='Unknown', reference_urls=[])]
    profile = dict(protocol={'top_k': 5}, cases=cases, factual_quality_exclusions=['excluded'],
                   methods=[dict(id='ai', adapter='bocha-ai', output_options={'answer': True})])
    rows = [dict(case_id=c['id'], method='ai', answer='Ada wrote it [引用:7]',
                 evidence={'path': c['id']+'.json', 'sha256': 'a'*64},
                 results=[dict(url='https://example.test/doc', citation_id=7, title='A document',
                               snippet='Ada wrote the document.', summary='Original source text.',
                               content='Full page body must not enter the model request')]) for c in cases]
    monkeypatch.setattr(report, 'check', lambda *a, **kw: (profile, rows))
    requests = []
    behavior = dict(status=200, invalid_citation=False)

    class Response(io.BytesIO):
        status = 200

    def send(request, timeout):
        value = json.loads(request.data)
        requests.append(value)
        if behavior['status'] != 200:
            from urllib.error import HTTPError
            raise HTTPError(request.full_url, behavior['status'], 'Service error', {}, io.BytesIO(b'failed'))
        messages = value['messages']
        if len(messages) == 1:
            text = 'A'
        elif 'judgments containing every citation_index' in messages[0]['content']:
            quote = 'Invented quote' if behavior['invalid_citation'] else 'Ada wrote the document.'
            text = json.dumps(dict(judgments=[dict(citation_index=0, supported=True,
                              claim='Ada wrote it', reason='The source names the author',
                              evidence=dict(field='snippet', quote=quote))]))
        else:
            text = json.dumps(dict(grade=3, reason='The answer identifies the author', evidence='Ada'))
        return Response(json.dumps(dict(model='test-model', choices=[dict(message={'content': text})])).encode())

    monkeypatch.setattr('urllib.request.urlopen', send)
    args = ['--report', str(tmp_path/'report'), '--output', str(tmp_path/'judge'),
            '--model', 'test-model', '--base-url', 'https://judge.test/v1', '--workers', '1',
            '--level', 'l3', '--simpleqa-grader', str(grader)]
    return dict(run=lambda extra=(): judge_main(args + list(extra)), profile=profile, rows=rows, requests=requests,
                behavior=behavior, output=tmp_path/'judge', source=source, grader=grader)


def test_public_l3_judge_uses_official_template_and_replays_without_spending(answer_run):
    test = answer_run
    test['run']()
    path = test['output']/'answers.json'
    evidence = json.loads(path.read_bytes())
    results = {r['case_id']: r for r in evidence['results']}
    assert set(results) == {'scene', 'fact'}
    assert results['fact']['factual_grade'] == 'CORRECT'
    assert results['scene']['answer_grade'] == 3
    assert all(r['citation_support'] == 1 and r['cited_urls_in_results'] == 1 for r in results.values())
    assert len(test['requests']) == 4
    assert all(r['temperature'] == 0 and r['extra_params'] == {'enable_thinking': False}
               for r in test['requests'])
    assert 'Full page body' not in json.dumps(test['requests'])
    assert 'expected Ada' in next(r['messages'][0]['content'] for r in test['requests']
                                  if len(r['messages']) == 1)
    assert evidence['configuration']['attempt_limit'] == 1
    test['run']()
    assert len(test['requests']) == 4
    assert json.loads(path.read_bytes()) == evidence
    test['rows'][0]['answer'] = 'A different answer'
    with pytest.raises(ValueError, match='different frozen inputs'):
        test['run']()
    assert len(test['requests']) == 4


def test_l3_transport_failure_is_zero_retry_including_resume(answer_run):
    test = answer_run
    test['behavior']['status'] = 503
    with pytest.raises(ValueError, match='preserved physical attempts'):
        test['run']()
    assert len(test['requests']) == 1
    assert not (test['output']/'answers.json').exists()
    test['behavior']['status'] = 200
    with pytest.raises(ValueError, match='attempt limit exhausted'):
        test['run']()
    assert len(test['requests']) == 1


def test_l3_invalid_citation_keeps_raw_call_and_cannot_be_rejudged_on_resume(answer_run):
    test = answer_run
    test['behavior']['invalid_citation'] = True
    with pytest.raises(ValueError, match='exact quote'):
        test['run']()
    spent = len(test['requests'])
    assert spent == 2 and not (test['output']/'answers.json').exists()
    test['behavior']['invalid_citation'] = False
    with pytest.raises(ValueError, match='exact quote'):
        test['run']()
    assert len(test['requests']) == spent


@pytest.mark.parametrize('tamper', ['request', 'native', 'bytes', 'source', 'verdict', 'missing-task'])
def test_l3_evidence_rechecks_native_answers_requests_and_physical_bytes(answer_run, tamper):
    from harness.web_search.answers import verify_answer_evidence
    from harness.web_search.output_levels import level_inputs

    test = answer_run
    test['run']()
    evidence = json.loads((test['output']/'answers.json').read_bytes())
    profile, rows = level_inputs(test['profile'], test['rows'], 'l3')
    source = test['source']
    if tamper == 'request':
        next(iter(evidence['calls'].values()))['request']['temperature'] = 1
    elif tamper == 'native':
        rows = deepcopy(rows)
        rows[0]['answer'] = 'Another answer'
    elif tamper == 'bytes':
        next(iter(evidence['calls'].values()))['attempts'][0]['response_sha256'] = 'f'*64
    elif tamper == 'verdict':
        evidence['results'][0]['answer_grade'] = 0
    elif tamper == 'missing-task':
        rows = rows[:-1]
    else:
        source += b'\n'
    with pytest.raises(ValueError):
        verify_answer_evidence(profile, rows, evidence, source)


def test_missing_citations_are_unavailable_support_not_perfect_support(answer_run):
    test = answer_run
    for row in test['rows']:
        row['answer'] = 'Ada wrote it'
    test['run']()
    results = json.loads((test['output']/'answers.json').read_bytes())['results']
    assert len(test['requests']) == 2
    assert all(r['citation_count'] == 0 and r['citation_support'] is None
               and r['cited_urls_in_results'] is None for r in results)


def test_answer_format_repair_preserves_original_paid_call_and_is_explicit(answer_run):
    test = answer_run
    test['behavior']['invalid_citation'] = True
    with pytest.raises(ValueError, match='exact quote'):
        test['run']()
    assert len(test['requests']) == 2
    test['behavior']['invalid_citation'] = False
    test['run'](['--repair-format'])
    assert len(test['requests']) == 5
    evidence = json.loads((test['output']/'answers.json').read_bytes())
    repaired = [c for c in evidence['calls'].values() if c.get('format_repair')]
    assert len(repaired) == 1
    assert repaired[0]['format_repair']['prior_call']['response']['choices'][0]['message']['content'].find('Invented quote') >= 0
    test['run']()
    assert len(test['requests']) == 5


def test_answer_format_repair_is_not_a_retry_for_valid_or_twice_invalid_grades(answer_run):
    test = answer_run
    test['behavior']['invalid_citation'] = True
    for _ in range(2):
        with pytest.raises(ValueError, match='exact quote'):
            test['run'](['--repair-format'])
    assert len(test['requests']) == 3
