"""First-record labels and redirected resource bases have separate owners."""
import copy

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def row(identifier, case, source, passed, *, metadata=None, **options):
    return {'id': identifier, 'category': 'structured-extraction' if identifier.startswith('first-label-') else 'answer-surface',
            'failure_mode': 'false-negative' if passed else 'false-positive',
            'root_cause': ('Canonical deduplication retained the earliest physical position but overwrote its label with a later occurrence.'
                           if identifier.startswith('first-label-') else
                           'Redirect resolution reused the final document base as the URL excluded for request echoes.'),
            'case': case, 'artifact': source, 'ok': True, 'metadata': metadata or {},
            'expected_task_pass': passed, **options}


def list_controls():
    case = {'url': 'https://shop.example/catalog', 'task_kind': 'body', 'expected_any': [],
            'source_benchmark': {'mode': 'dynamic_list_structure', 'verification_scope': 'first_three_name_detail_pairs',
                                 'hosts': ['shop.example'], 'entry_path': r'/item/\d+', 'minimum_entries': 3}}
    reference = {'items': [{'url': 'https://shop.example/item/' + str(index), 'title': 'Confirmed ' + str(index)}
                           for index in range(1, 4)]}
    for surface in ['markdown', 'html', 'html-first', 'markdown-first', 'plain_text', 'metadata']:
        for correct_first in [False, True]:
            first = 'Confirmed 1' if correct_first else 'Wrong'
            last = 'Wrong' if correct_first else 'Confirmed 1'
            entries = [(1, first), (2, 'Confirmed 2'), (3, 'Confirmed 3'), (1, last)]
            pieces = []
            metadata = {'answer_format': surface} if surface in {'html', 'markdown', 'plain_text'} else {}
            for position, (index, title) in enumerate(entries):
                url = 'https://shop.example/item/' + str(index)
                kind = ('html' if (position == 0) == (surface == 'html-first') else 'markdown') if surface.endswith('-first') else surface
                pieces.append('<a href="' + url + '">' + title + '</a>' if kind == 'html' else
                              title + '\n' + url if kind == 'plain_text' else '[' + title + '](' + url + ')')
            if surface == 'metadata':
                metadata['links'] = [{'url': 'https://shop.example/item/' + str(index), 'title': title}
                                     for index, title in entries]
            source = 'Catalog' if surface == 'metadata' else '\n\n'.join(pieces)
            yield row('first-label-' + surface + '-' + str(correct_first), copy.deepcopy(case), source, correct_first,
                      metadata=metadata, list_reference=copy.deepcopy(reference),
                      expected_metrics={'confirmed_entries': 3 if correct_first else 2, 'checked_entries': 3})
    for correct_first in [False, True]:
        first, last = ('Confirmed 1', 'Wrong') if correct_first else ('Wrong', 'Confirmed 1')
        image = '[![' + last + '](/poster.png)](/item/1)\n\n## ' + last
        source = '[' + first + '](/item/1)\n[Confirmed 2](/item/2)\n[Confirmed 3](/item/3)\n\n' + image
        yield row('first-label-later-image-card-' + str(correct_first), copy.deepcopy(case), source, correct_first,
                  metadata={'answer_format': 'markdown'}, list_reference=copy.deepcopy(reference),
                  expected_metrics={'confirmed_entries': 3 if correct_first else 2, 'checked_entries': 3})
    image = '[![Confirmed 1](/poster.png)](/item/1)\n\n## Confirmed 1'
    yield row('first-label-earlier-image-card', copy.deepcopy(case),
              image + '\n[Confirmed 2](/item/2)\n[Confirmed 3](/item/3)\n[Wrong](/item/1)', True,
              metadata={'answer_format': 'markdown'}, list_reference=copy.deepcopy(reference),
              expected_metrics={'confirmed_entries': 3, 'checked_entries': 3})
    scoped = copy.deepcopy(case)
    scoped['source_benchmark']['entry_scope'] = {
        'html_selector': '.cards', 'start_pattern': r'(?m)^# Catalog$', 'end_pattern': r'(?m)^# End$',
        'markdown_card_title_pattern': r'## (?P<title>.+)'}
    cards = '\n'.join('## Confirmed ' + str(index) + '\nUseful description\n[More](/item/' + str(index) + ')'
                      for index in range(1, 4))
    for earlier_wrong in [False, True]:
        source = '# Catalog\n' + ('[Wrong](/item/1)\n' if earlier_wrong else '') + cards + '\n# End'
        yield row('first-label-heading-owner-' + str(earlier_wrong), copy.deepcopy(scoped), source, not earlier_wrong,
                  metadata={'answer_format': 'markdown'}, list_reference=copy.deepcopy(reference),
                  expected_metrics={'confirmed_entries': 2 if earlier_wrong else 3, 'checked_entries': 3})


def redirect_controls():
    request = 'https://origin.example/start'
    final = 'https://cdn.example/manual/index.html'
    target = 'https://cdn.example/manual/target.png'
    case = {'url': request, 'task_kind': 'resource', 'content_score_applicable': False,
            'expected_resources': [{'id': 'image', 'urls': [target], 'aliases': [request]}]}
    for identifier, source, metadata, passed in [
        ('echo-only', request, {}, False),
        ('echo-fragment', request + '#page', {}, False),
        ('target-only', target, {}, True),
        ('relative-target', '[Image](target.png)', {'answer_format': 'markdown'}, True),
        ('html-relative-target', '<img src="target.png">', {'answer_format': 'html'}, True),
        ('metadata-echo', '', {'images': [request]}, False),
        ('metadata-target', '', {'images': [target]}, True),
        ('echo-and-target', request + '\n' + target, {}, True),
    ]:
        yield row('redirect-resource-' + identifier, copy.deepcopy(case), source, passed,
                  metadata=metadata, final_url=final)
    strict = {**case, 'resource_output_contract': {'candidate_scope': 'image_answer_fields', 'unexpected': 'reject'}}
    for identifier, values, passed in [('echo-only', [request], False), ('target-only', [target], True),
                                       ('echo-and-target', [request, target], True)]:
        yield row('redirect-resource-strict-' + identifier, copy.deepcopy(strict), '', passed,
                  metadata={'images': values}, final_url=final)
    final_image = 'https://cdn.example/manual/target.png'
    image_case = {**case, 'expected_resources': [{'id': 'image', 'urls': [final_image]}]}
    for identifier, source, metadata, passed in [('explicit-final-target', final_image, {}, True),
                                               ('relative-final-target', '[Image](target.png)', {}, True),
                                               ('final-metadata-only', '', {'final_url': final_image}, False)]:
        yield row('redirect-resource-' + identifier, copy.deepcopy(image_case), source, passed,
                  metadata=metadata, final_url=final_image)
    yield row('redirect-resource-no-redirect-echo', copy.deepcopy(case), request, False)


LIST_CONTROLS = list(list_controls())
RESOURCE_CONTROLS = list(redirect_controls())
CONTROLS = LIST_CONTROLS + RESOURCE_CONTROLS


@pytest.mark.parametrize('control', CONTROLS, ids=[control['id'] for control in CONTROLS])
def test_first_record_and_redirect_echo_through_public_scorer(control):
    options = {key: control[key] for key in ['metadata', 'list_reference', 'final_url'] if key in control}
    metrics = score_attempt(control['case'], control['artifact'].encode(), ok=True, **options)
    assert task_passed(True, metrics, control['case']['task_kind']) is control['expected_task_pass']
    for key, expected in control.get('expected_metrics', {}).items():
        assert metrics[key] == expected
