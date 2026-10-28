"""Mixed card layouts and control anchors retain their declared input scope."""
from copy import deepcopy

import pytest

from lexbench_webfetch.metrics import task_passed, task_measured
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.stable_tasks import evaluate_stable_task


def task(schema='name_description', minimum=2, **rules):
    return {'url': 'https://example.test/catalog', 'task_kind': 'body',
            'acceptance_contract': {'version': 1, 'schema': schema, 'minimum_records': minimum, **rules}}


def controls():
    for order in ('ordinary-first', 'image-first', 'mixed-three'):
        for complete in (False, True):
            ordinary = '- [Alpha](/a)\n' + ('First complete description.' if complete else '')
            image = '- [![Picture](/img)](/b)\n[Beta](/b) Second complete description.'
            cards = [ordinary, image] if order == 'ordinary-first' else [image, ordinary]
            if order == 'mixed-three':
                cards = [ordinary, image, '- [Gamma](/c)\nThird complete description.']
            yield (f'mixed-descriptions-{order}-' + ('complete' if complete else 'incomplete'),
                   '\n\n'.join(cards), task(minimum=len(cards)), {}, complete)
    literal = '<ul>\n<li>Alpha</li>\n<li>First complete description.</li>\n</ul>'
    for representation in ('plain_text', 'html'):
        yield (f'description-representation-{representation}', literal, task(minimum=1),
               {'answer_format': representation}, representation == 'plain_text')
    for exclusion in ('target-footer', 'target-nav', 'selector', 'text-pattern'):
        for excluded in (False, True):
            label = 'Search articles'
            anchor = {'id': 'search', 'alternatives': [label],
                      'visible_control': {'selector': '#search', 'attribute': 'placeholder'}}
            rules = {'stable_text_anchors': [anchor]}
            control = '<input id="search" placeholder="Search articles">'
            prose = '<p>Independent body text.</p>'
            if exclusion.startswith('target-'):
                rules['content_scope'] = 'target_content'
                tag = exclusion.removeprefix('target-') if excluded else 'section'
                content = '<main>' + prose + '<' + tag + '>' + control + '</' + tag + '></main>'
            elif exclusion == 'selector':
                rules['text_exclusions'] = {'html_selectors': ['.excluded']}
                content = '<main>' + prose + '<aside class="' + ('excluded' if excluded else 'included') + '">' + control + '</aside></main>'
            else:
                rules['text_exclusions'] = {'text_patterns': ['^Search articles$' if excluded else '^Other text$']}
                content = '<main>' + prose + control + '</main>'
            yield (f'control-scope-{exclusion}-' + ('excluded' if excluded else 'included'),
                   content, task('body_text', 0, **rules), {}, not excluded)


@pytest.mark.parametrize('identifier, content, case, metadata, expected', list(controls()))
def test_mixed_layout_representation_and_control_scope(identifier, content, case, metadata, expected):
    direct = evaluate_stable_task(content, case['acceptance_contract'], case['url'],
                                  representation=metadata.get('answer_format'))
    assert direct['stable_task_pass'] is expected
    metrics = score_attempt(deepcopy(case), content.encode(), ok=True, metadata=metadata)
    assert task_measured(metrics, 'body')
    assert task_passed(True, metrics, 'body') is expected


def scoring_controls():
    selected = {
        'mixed-descriptions-ordinary-first-complete', 'mixed-descriptions-ordinary-first-incomplete',
        'mixed-descriptions-image-first-complete', 'mixed-descriptions-image-first-incomplete',
        'description-representation-plain_text', 'description-representation-html',
        'control-scope-target-footer-excluded', 'control-scope-target-footer-included',
        'control-scope-text-pattern-excluded', 'control-scope-text-pattern-included',
    }
    for identifier, content, case, metadata, expected in controls():
        if identifier in selected:
            yield {'id': identifier, 'failure_mode': 'false-negative' if expected else 'false-positive',
                   'category': 'answer-surface',
                   'root_cause': 'Record and anchor extraction must preserve mixed card ownership, declared text representation and scope exclusions.',
                   'case': case, 'artifact': content, 'metadata': metadata,
                   'ok': True, 'expected_task_pass': expected}
