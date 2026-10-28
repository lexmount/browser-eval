"""Field ownership and anchor exclusions remain effective at every projection."""
import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def scored(case, artifact):
    metrics = score_attempt(case, artifact.encode(), ok=True)
    return task_passed(True, metrics, 'body'), metrics


def composite_case(group):
    return {'url': 'https://example.test/catalog', 'task_kind': 'body', 'min_content_chars': 1,
            'composite_acceptance': {'version': 1, 'groups': [group]}}


def test_malformed_auxiliary_card_is_discarded_without_losing_valid_author_sibling():
    group = {'id': 'items', 'kind': 'named_link', 'minimum_records': 1,
             'hosts': ['example.test'], 'entry_path': r'/item/\d+',
             'required_fields': [{'id': 'author', 'kind': 'text', 'aliases': ['Author']}]}
    case = composite_case(group)
    artifact = '[Widget](/item/1)\n[**Widget**\nby\nAlice]({url})'
    assert not scored(case, artifact.format(url='http://[bad]'))[0]
    assert scored(case, artifact.format(url='/item/1'))[0]
    assert scored(case, artifact.format(url='http://[bad]') + '\nAuthor: Bob')[0]


def table_group(kind):
    group = {'id': 'items', 'kind': kind, 'minimum_records': 1, 'identity_column': 'name',
             'required_columns': [{'id': 'name', 'kind': 'text', 'aliases': ['Name']},
                                  {'id': 'owner', 'kind': 'text', 'aliases': ['Owner', 'Name']}]}
    if kind == 'linked_table':
        group.update(hosts=['example.test'], entry_path=r'/item/\d+', link_column='name')
    return group


@pytest.mark.parametrize('kind', ['table', 'linked_table'])
def test_overlapping_aliases_cannot_reuse_one_physical_table_column(kind):
    group = table_group(kind)
    case = composite_case(group)
    name = '[Widget](/item/1)' if kind == 'linked_table' else 'Widget'
    assert not scored(case, '|Name|\n|---|\n|' + name + '|')[0]
    assert scored(case, '|Name|Owner|\n|---|---|\n|' + name + '|Alice|')[0]
    assert not scored(case, '|Name|Owner|\n|---|---|\n|' + name + '||')[0]


def test_only_explicit_combined_column_can_supply_multiple_fields():
    group = table_group('table')
    group['required_columns'][0]['aliases'] = ['Name', 'Person']
    group['required_columns'][1]['aliases'] = ['Owner', 'Person']
    group['combined_columns'] = [{'aliases': ['Person'],
        'pattern': r'^(?P<name>[^;]+);\s*(?P<owner>.+)$'}]
    case = composite_case(group)
    assert scored(case, '|Person|\n|---|\n|Widget; Alice|')[0]
    assert not scored(case, '|Person|\n|---|\n|Widget|')[0]


def test_linearized_linked_table_requires_distinct_header_occurrences():
    case = composite_case(table_group('linked_table'))
    assert not scored(case, 'Name\n[Widget](/item/1)\n[Alice](/people/alice)')[0]
    assert scored(case, 'Name\nOwner\n[Widget](/item/1)\n[Alice](/people/alice)')[0]


def anchor_case(**extra):
    return {'url': 'https://example.test/catalog', 'task_kind': 'body', 'min_content_chars': 1,
            'acceptance_contract': {'version': 1, 'schema': 'named_link', 'minimum_records': 1,
                'hosts': ['example.test'], 'entry_path': r'/item/\d+',
                'stable_text_anchors': [{'id': 'identity', 'alternatives': ['Live'],
                                         'match_scope': 'non_record_lines'}], **extra}}


def test_non_record_anchor_obeys_visible_text_exclusions():
    case = anchor_case(text_exclusions={'text_patterns': ['^Live$']})
    artifact = '- [Widget](/item/1)\nLive'
    assert not scored(case, artifact)[0]
    assert scored(case, artifact + '\nLive updates')[0]


@pytest.mark.parametrize('mixed', [False, True])
def test_non_record_anchor_obeys_html_selector_exclusions(mixed):
    case = anchor_case(text_exclusions={'html_selectors': ['.excluded']})
    prefix = 'Report\n' if mixed else '<main>'
    suffix = '' if mixed else '</main>'
    artifact = prefix + '<a href="/item/1">Widget</a><aside class="excluded">Live</aside>'
    assert not scored(case, artifact + suffix)[0]
    assert scored(case, artifact + '<p>Live updates</p>' + suffix)[0]


@pytest.mark.parametrize('tag', ['nav', 'footer'])
def test_target_content_non_record_anchor_excludes_navigation_and_footer(tag):
    case = anchor_case(content_scope='target_content')
    artifact = '<main><a href="/item/1">Widget</a>{extra}</main><' + tag + '>Live</' + tag + '>'
    assert not scored(case, artifact.format(extra=''))[0]
    assert scored(case, artifact.format(extra='<p>Live updates</p>'))[0]


def test_linearized_headers_belong_to_their_nearest_record_block():
    case = composite_case(table_group('linked_table'))
    invalid = 'Name\nOwner\n[Invalid](https://[bad])\n[Alice](/people/alice)'
    valid = 'Name\n\nOwner\n\n[Widget](/item/1)\n[Alice](/people/alice)'
    assert scored(case, valid)[0]
    assert scored(case, invalid + '\n\n' + valid)[0]
    assert not scored(case, invalid + '\n\nName\n[Widget](/item/1)\n[Alice](/people/alice)')[0]
