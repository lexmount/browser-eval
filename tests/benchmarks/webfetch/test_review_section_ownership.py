"""Real reviewed section rules reject title inventories plus unrelated padding."""
from copy import deepcopy
from html import escape

import pytest

from lexbench_webfetch import benchmark
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


@pytest.fixture(scope='module')
def reviewed():
    result = {}
    for suite in ['popular-sites', 'access-restrictions']:
        root = benchmark.suite(suite)[1]
        data = benchmark.apply_annotation_revision(
            suite, benchmark.read(root / 'tasks.json'), 'review-550-20261023')
        result.update({c['id']: c for c in data['cases']})
    return result


@pytest.mark.parametrize('case_id', [
    'global-homepage-021-netflix-com', 'global-homepage-049-brave-com',
    'global-homepage-075-telegram-org', 'global-homepage-087-instructure-com',
    'china-homepage-012-feishu-cn', 'risk-diverse-192',
])
def test_titles_and_total_length_cannot_replace_local_section_body(reviewed, case_id):
    case = reviewed[case_id]
    section = case['document_structure']
    anchors = '\n'.join(a['alternatives'][0]
                        for a in case['acceptance_contract']['stable_text_anchors'])
    facts = '\n'.join(f['alternatives'][0] for f in case.get('required_facts', []))
    headings = ''.join(f'<a href="#item-{i}">{escape(h["text"])}</a>\n'
                       for i, h in enumerate(section['headings']))
    if case_id == 'risk-diverse-192':
        # Preserve the independent page-identity sentence in both variants.
        facts += '\nibiblio public digital library freely available information software UNC'
    padding = 'Unrelated information about a different subject. ' * 100
    answer = ('<nav>' + escape(anchors + '\n' + facts) + headings + '</nav>'
              '<main><p>' + escape(facts) + ' ' + padding + '</p></main>')
    substitute = deepcopy(case)
    substitute.pop('document_structure')
    # The proposed title-presence substitute has every declared title and
    # exceeds the total floor, but none of the actual feature descriptions.
    substitute['required_facts'] = [*substitute.get('required_facts', []),
                                   *({'id': h.get('id', f'section-{i+1}'), 'alternatives': [h['text']]}
                                     for i, h in enumerate(section['headings']))]
    substitute['min_content_chars'] = max(substitute.get('min_content_chars', 0),
                                          section['minimum_body_chars'])
    assert task_passed(True, score_attempt(substitute, answer.encode(), ok=True))
    assert not task_passed(True, score_attempt(case, answer.encode(), ok=True))
