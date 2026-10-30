"""Only record-owned prose satisfies an unlabeled description field."""
import pytest
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.metrics import task_passed


def description_case():
    return {'url': 'https://example.test/', 'task_kind': 'body', 'min_content_chars': 1,
            'composite_acceptance': {'version': 1, 'groups': [
                {'id': 'forums', 'kind': 'named_link', 'minimum_records': 1,
                 'hosts': ['example.test'], 'entry_path': '/Forum-[a-z]+',
                 'required_fields': [{'id': 'description', 'kind': 'text', 'minimum_chars': 5}]}]}}


@pytest.mark.parametrize('content,expected', [
    ('| **[Category](https://example.test/Forum-category)** |\n| --- |\n| | **Threads** | **Posts** | **Last Post** |', False),
    ('[Child](https://example.test/Forum-child), and 3 more. | 12 | 34 | [A long recent discussion](https://example.test/thread/1)\nYesterday |', False),
    ('| [Forum](https://example.test/Forum-main) (10 users browsing) | 12 | 34 | Recent discussion text |', False),
    ('| [Forum](https://example.test/Forum-main)\nDiscuss this subject here. | 12 | 34 | Recent discussion text |', True),
    ('| [Forum](https://example.test/Forum-main)<br>Discuss this subject here. | 12 | 34 | Recent discussion text |', True),
    ('| [Forum](https://example.test/Forum-main)\nDiscuss escaped \\| pipe examples here. | 12 | 34 | Recent discussion text |', True),
    ('| [Forum](https://example.test/Forum-main) | Discuss this subject here. |', True),
    ('[Forum](https://example.test/Forum-main)\nDiscuss prose with an unescaped | pipe here.', True),
    ('[Forum](https://example.test/Forum-main)\nClosed', True),
    ('[Forum](https://example.test/Forum-main), and 3 more.', False),
])
def test_description_cannot_come_from_table_scaffolding(content, expected):
    metrics = score_attempt(description_case(), content.encode(), ok=True)
    assert task_passed(True, metrics) is expected
    assert metrics['composite_group_results'][0]['count'] == int(expected)
