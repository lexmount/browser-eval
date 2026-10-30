"""Native API-operation summaries stop at the same headings as Markdown."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response


SUMMARY = 'Read a new object'


def task(anchor=True, minimum=1):
    anchors = [{'id': 'read', 'alternatives': ['GET\n/new\n' + SUMMARY]}] if anchor else []
    return {'url': 'https://example.test/', 'acceptance_contract': {
        'version': 1, 'schema': 'api_operation', 'minimum_records': minimum,
        'stable_text_anchors': anchors}}


def text(level, *, complete=True, fields=False, nested=False):
    operation = ('<p>method: GET</p><p>path: /new</p>' if fields else
                 '<h3>GET <code>/new</code></h3>')
    summary = '<p>Summary: ' + SUMMARY + '</p>' if complete else ''
    wrapper = '<section>' if nested else ''
    end = '</section>' if nested else ''
    return '<main>' + wrapper + operation + summary + end + f'<h{level}>Models</h{level}><p>ModelOne</p></main>'


@pytest.mark.parametrize('level', range(1, 7))
@pytest.mark.parametrize('complete', [False, True])
@pytest.mark.parametrize('fields', [False, True])
@pytest.mark.parametrize('representation', [None, 'html'])
def test_html_headings_cannot_supply_or_extend_operation_summary(level, complete, fields, representation):
    result = evaluate_response(task(anchor=complete), text(level, complete=complete, fields=fields).encode(),
                               metadata={'answer_format': representation} if representation else None)
    assert result['measured']
    assert result['passed'] is complete
    if complete:
        assert result['metrics']['stable_records'][0]['fields'] == {'method': 'GET', 'path': '/new', 'summary': SUMMARY}


@pytest.mark.parametrize('nested', [False, True])
def test_html_operation_header_and_next_operation_keep_their_own_summary(nested):
    source = '<h3>GET /new</h3><p>' + SUMMARY + '</p><h3>POST /new</h3><p>Create a new object</p><h2>Appendix</h2><p>Unrelated notes</p>'
    source = '<main>' + ('<section>' + source + '</section>' if nested else source) + '</main>'
    case = task(minimum=2)
    case['acceptance_contract']['stable_text_anchors'].append({'id': 'create', 'alternatives': ['POST\n/new\nCreate a new object']})
    result = evaluate_response(case, source.encode(), metadata={'answer_format': 'html'})
    assert result['passed']
    assert [row['fields']['summary'] for row in result['metrics']['stable_records']] == [SUMMARY, 'Create a new object']


def test_a_deeper_html_heading_also_owns_its_separate_section():
    source = '<main><h3>GET /new</h3><h4>Parameters</h4><p>Required identifier</p></main>'
    assert not evaluate_response(task(anchor=False), source.encode(), metadata={'answer_format': 'html'})['passed']


def scoring_controls():
    for fields in [False, True]:
        for complete in [False, True]:
            yield {'id': 'api19-html-' + ('fields' if fields else 'heading') + '-' + ('complete' if complete else 'missing'),
                   'category': 'structured-extraction', 'failure_mode': 'false-negative' if complete else 'false-positive',
                   'root_cause': 'HTML heading boundaries must survive visibility projection so API-operation summaries stay inside their owning section.',
                   'case': task(anchor=complete), 'artifact': text(3, complete=complete, fields=fields),
                   'metadata': {'answer_format': 'html'}, 'ok': True, 'expected_task_pass': complete}
