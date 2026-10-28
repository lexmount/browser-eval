"""Responsive picture sources are image answers; media and hidden sources are not."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response


def _case(paths):
    return {'url': 'https://example.test/gallery', 'task_kind': 'resource',
            'expected_resources': [{'id': str(index), 'urls': [path]} for index, path in enumerate(paths)],
            'resource_output_contract': {'candidate_scope': 'image_answer_fields', 'unexpected': 'reject'}}


PICTURE = '<picture><source srcset="/required.webp 1x"><img src="/fallback.jpg"></picture>'
CASES = [
    ('picture-recall', PICTURE, ['/required.webp', '/fallback.jpg'], True),
    ('picture-unexpected', PICTURE, ['/fallback.jpg'], False),
    ('picture-no-source', '<picture><img src="/fallback.jpg"></picture>', ['/fallback.jpg'], True),
    ('picture-missing-source', '<picture><img src="/fallback.jpg"></picture>', ['/required.webp', '/fallback.jpg'], False),
    ('multiple-candidates', PICTURE.replace('/required.webp 1x', '/required.webp 1x, /large.webp 2x'),
     ['/required.webp', '/large.webp', '/fallback.jpg'], True),
    ('unexpected-candidate', PICTURE.replace('/required.webp 1x', '/required.webp 1x, /large.webp 2x'),
     ['/required.webp', '/fallback.jpg'], False),
]
for label, source in [
    ('video', '<video><source srcset="/required.webp 1x"></video>'),
    ('audio', '<audio><source srcset="/required.webp 1x"></audio>'),
    ('orphan', '<source srcset="/required.webp 1x">'),
    ('nested-video', '<picture><video><source srcset="/required.webp 1x"></video></picture>'),
    ('hidden-picture', '<picture hidden><source srcset="/required.webp 1x"></picture>'),
    ('hidden-source', '<picture><source hidden srcset="/required.webp 1x"></picture>'),
    ('closed-details', '<details><summary>More</summary><picture><source srcset="/required.webp 1x"></picture></details>'),
]:
    text = '<img src="/fallback.jpg">' + source
    CASES.append((label + '-not-image-recall', text, ['/required.webp', '/fallback.jpg'], False))
    CASES.append((label + '-not-unexpected-image', text, ['/fallback.jpg'], True))


@pytest.mark.parametrize('identifier,text,paths,passed', CASES)
def test_typed_image_answers_include_only_visible_picture_owned_sources(identifier, text, paths, passed):
    result = evaluate_response(_case(paths), text.encode())
    assert result['passed'] is passed


def scoring_controls():
    for identifier, text, paths, passed in CASES:
        yield {'id': 'runtime10-' + identifier, 'category': 'answer-surface',
               'failure_mode': 'false-negative' if passed else 'false-positive',
               'root_cause': 'typed_image_srcset_requires_visible_picture_ownership',
               'case': _case(paths), 'artifact': text, 'ok': True, 'expected_task_pass': passed}
