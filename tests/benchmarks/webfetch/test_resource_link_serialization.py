"""Rendered link syntax must not change the identity or count of a resource."""
import pytest

from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.metrics import task_passed


TARGET = 'https://cdn.example.test/photo.jpg?width=640&height=480'


def score(output, target=TARGET):
    return score_attempt({'url': 'https://example.test/gallery', 'task_kind': 'resource',
        'expected_resources': [{'id': 'photo', 'urls': [target]}],
        'resource_output_contract': {'candidate_scope': 'image_answer_fields', 'unexpected': 'reject'}},
        output.encode(), ok=True)


@pytest.mark.parametrize('template', ['![Photo]({url})', '[Download]({url})', '<img src="{url}">'])
def test_entity_escaped_link_destination_is_one_resource(template):
    result = score(template.format(url=TARGET.replace('&', '&amp;')))
    assert task_passed(True, result)
    assert result['resource_candidate_urls'] == [TARGET]
    assert result['resource_precision'] == 1.0


@pytest.mark.parametrize('output', [
    '![Photo](https://cdn.example.test/photo.jpg?width=641&amp;height=480)',
    'https://cdn.example.test/photo.jpg?width=640&amp;height=480',
    '![Photo](https://cdn.example.test/photo.jpg?width=640%26amp%3Bheight=480)',
])
def test_markup_decoding_does_not_equate_different_or_literal_url_values(output):
    assert not task_passed(True, score(output))
