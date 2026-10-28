"""Native heading cards retain ownership; mixed anchor alternatives remain OR."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response


def heading_case(complete=True):
    return {'url': 'https://example.test/', 'acceptance_contract': {'version': 1,
        'schema': 'name_description', 'minimum_records': 2 if complete else 1,
        'maximum_records': 2 if complete else 1}}


def heading_text(level=2, wrapper='main', complete=True):
    first = '<p>First description.</p>' if complete else ''
    return f'<{wrapper}><h{level}>Alpha</h{level}>' + first + f'<h{level}>Beta</h{level}><p>Second description.</p></{wrapper}>'


@pytest.mark.parametrize('level', range(1, 7))
@pytest.mark.parametrize('wrapper', ['main', 'article', 'section'])
@pytest.mark.parametrize('complete', [False, True])
@pytest.mark.parametrize('representation', [None, 'html'])
def test_native_heading_cards_keep_description_boundaries(level, wrapper, complete, representation):
    result = evaluate_response(heading_case(complete), heading_text(level, wrapper, complete).encode(),
                               metadata={'answer_format': representation} if representation else None)
    assert result['measured']
    assert result['passed'] is complete
    fields = [r['fields'] for r in result['metrics']['stable_records']]
    assert fields == [{'name': 'Alpha', 'description': 'First description.' if complete else ''},
                      {'name': 'Beta', 'description': 'Second description.'}]


def mixed_case(schema='product', additional=False):
    if schema == 'product':
        alternatives = ['Old phone $50', 'Available products']
    else:
        alternatives = ['GET\n/new\nRead a new object', 'Available operations']
    anchors = [{'id': 'either', 'alternatives': alternatives}]
    if additional:
        anchors.append({'id': 'mandatory', 'alternatives': ['Mandatory notice']})
    return {'url': 'https://example.test/', 'acceptance_contract': {'version': 1,
        'schema': schema, 'minimum_records': 1, 'stable_text_anchors': anchors}}


OR_CASES = [
    ('literal', mixed_case(), 'Available products\nNew phone $60', True),
    ('neither', mixed_case(), 'Catalog\nNew phone $60', False),
    ('structured', mixed_case(), 'Old phone $50', True),
    ('cross-record-price', mixed_case(), 'Old phone $60\nNew phone $50', False),
    ('structure-still-required', mixed_case(), 'Available products\nNo complete item', False),
    ('other-anchor-still-required', mixed_case(additional=True), 'Available products\nNew phone $60', False),
    ('api-owned-fields', mixed_case('api_operation'), 'GET /new Wrong summary\nPOST /other Read a new object', False),
    ('api-literal', mixed_case('api_operation'), 'Available operations\nGET /new Wrong summary\nPOST /other Read a new object', True),
]


@pytest.mark.parametrize('identifier,case,text,expected', OR_CASES)
@pytest.mark.parametrize('representation', [None, 'plain_text', 'markdown'])
def test_each_anchor_alternative_uses_its_own_matching_route(identifier, case, text, expected, representation):
    result = evaluate_response(case, text.encode(), metadata={'answer_format': representation} if representation else None)
    assert result['measured']
    assert result['passed'] is expected


def scoring_controls():
    for complete in [False, True]:
        yield {'id': 'heading20-native-' + ('complete' if complete else 'missing'),
               'category': 'structured-extraction', 'failure_mode': 'false-negative' if complete else 'false-positive',
               'root_cause': 'HTML heading boundaries survive both ordinary and bounded native name/description projection.',
               'case': heading_case(complete), 'artifact': heading_text(wrapper='article', complete=complete),
               'metadata': {'answer_format': 'html'}, 'ok': True, 'expected_task_pass': complete}
    for identifier, case, text, expected in OR_CASES:
        if identifier in {'literal', 'neither', 'structured', 'cross-record-price', 'structure-still-required', 'api-owned-fields'}:
            yield {'id': 'anchor20-mixed-' + identifier, 'category': 'structured-extraction',
                   'failure_mode': 'false-negative' if expected else 'false-positive',
                   'root_cause': 'Each anchor alternative independently chooses owned-field or visible-literal matching, then alternatives combine with OR.',
                   'case': case, 'artifact': text, 'metadata': {'answer_format': 'markdown'},
                   'ok': True, 'expected_task_pass': expected}
