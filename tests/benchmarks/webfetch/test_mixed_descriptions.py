"""Mixed native list and non-list descriptions keep independent ownership."""
import pytest
from lexbench_webfetch.stable_tasks import evaluate_stable_task, source_record_hash

CARD = '<section><p>Beta</p><p>Second complete description.</p></section>'
LIST = '<ul><li><a>Alpha</a><p>First complete description.</p></li></ul>'


def contract(minimum):
    return {'version': 1, 'schema': 'name_description', 'minimum_records': minimum,
            'stable_text_anchors': []}


@pytest.mark.parametrize('closed', [False, True])
@pytest.mark.parametrize('list_first', [False, True])
def test_owned_list_and_non_list_record_are_both_kept_once(closed, list_first):
    html = '<main>' + (LIST + CARD if list_first else CARD + LIST) + '</main>'
    result = evaluate_stable_task(html, contract(2), allow_closed_title_cards=closed)
    assert result['stable_task_pass'], result
    assert result['stable_complete_record_count'] == result['stable_unique_record_count'] == 2
    assert {r['fields']['name'] for r in result['stable_records']} == {'Alpha', 'Beta'}
    assert sorted(r['fields']['description'] for r in result['stable_records']) == [
        'First complete description.', 'Second complete description.']


@pytest.mark.parametrize('extra', [
    '<ul><li><p>name</p><p>description</p></li></ul>',
    '<ul><li>Orphan title</li></ul>',
    '<ul><li><p>Parent name</p><p>Parent description.</p><ul>'
    '<li><p>name</p><p>description</p></li></ul></li></ul>',
])
def test_invalid_list_cannot_suppress_or_contaminate_fixed_non_list_record(extra):
    rule = contract(1)
    fields = ['description', 'name']
    rule['source_membership'] = {'kind': 'fixed', 'fields': fields,
        'record_hashes': [source_record_hash(
            {'name': 'Beta', 'description': 'Second complete description.'},
            fields, 'name_description')]}
    result = evaluate_stable_task('<main>' + extra + CARD + '</main>', rule,
                                  allow_closed_title_cards=True)
    assert result['stable_task_pass'], result
    assert result['stable_complete_record_count'] == result['stable_unique_record_count'] == 1
    assert result['stable_source_membership'] == 'fixed'
    assert 'source_record_mismatch' not in result['stable_task_errors']


def test_nested_owned_list_does_not_reappear_as_non_list_record():
    html = '<main><ul><li>Category<ul><li><p>Alpha</p><p>First complete description.</p>' \
           '</li></ul></li></ul>' + CARD + '</main>'
    result = evaluate_stable_task(html, contract(2), allow_closed_title_cards=True)
    assert result['stable_task_pass'], result
    assert result['stable_complete_record_count'] == result['stable_unique_record_count'] == 2


def test_missing_non_list_description_cannot_borrow_from_owned_list():
    html = '<main><section><p>Beta</p></section>' + LIST + '</main>'
    result = evaluate_stable_task(html, contract(2), allow_closed_title_cards=True)
    assert not result['stable_task_pass']
    assert result['stable_complete_record_count'] == 1


@pytest.mark.parametrize('wrapper', ['body', 'main'])
@pytest.mark.parametrize('nesting', [0, 1, 2])
@pytest.mark.parametrize('labeled', [False, True])
@pytest.mark.parametrize('form', ['articles', 'lists', 'mixed'])
def test_html_record_containers_and_labels_preserve_fixed_fields(wrapper, nesting, labeled, form):
    rows = [{'name': 'Alpha', 'description': 'First complete description.'},
            {'name': 'Beta', 'description': 'Second complete description.'}]
    fields = ['description', 'name']
    rule = contract(2)
    rule['source_membership'] = {'kind': 'fixed', 'fields': fields,
        'record_hashes': sorted(source_record_hash(r, fields, 'name_description') for r in rows)}
    def render(missing=False):
        cards = []
        for i, row in enumerate(rows):
            tag = 'li' if form == 'lists' or (form == 'mixed' and i == 0) else 'article'
            name = ('Name: ' if labeled else '') + row['name']
            description = ('Description: ' if labeled else '') + row['description']
            body = '<p>' + name + '</p>'
            if not (missing and i == 0):
                body += '<section><p>' + description + '</p></section>'
            cards.append(f'<{tag}>{body}</{tag}>' if tag == 'article' else '<ul><li>' + body + '</li></ul>')
        return f'<{wrapper}>' + '<article>' * nesting + ''.join(cards) + '</article>' * nesting + f'</{wrapper}>'
    accepted = evaluate_stable_task(render(), rule)
    assert accepted['stable_task_pass'], accepted
    assert accepted['stable_complete_record_count'] == accepted['stable_unique_record_count'] == 2
    assert not evaluate_stable_task(render(missing=True), rule)['stable_task_pass']
    assert not evaluate_stable_task(render().replace('Second complete', 'Fabricated altered'), rule)['stable_task_pass']


@pytest.mark.parametrize('wrapping', ['', 'section', 'div', 'article'])
@pytest.mark.parametrize('split', [False, True])
def test_sibling_sections_cannot_join_fixed_record_fields(wrapping, split):
    rows = [{'name': 'Alpha', 'description': 'First complete description.'},
            {'name': 'Beta', 'description': 'Second complete description.'}]
    fields = ['description', 'name']
    rule = contract(2)
    rule['source_membership'] = {'kind': 'fixed', 'fields': fields,
        'record_hashes': sorted(source_record_hash(r, fields, 'name_description') for r in rows)}
    body = ('<section><p>Beta</p></section><section><p>Second complete description.</p></section>'
            if split else '<section><p>Beta</p><section><p>Second complete description.</p></section></section>')
    if wrapping:
        body = f'<{wrapping}>' + body + f'</{wrapping}>'
    result = evaluate_stable_task('<main>' + LIST + body + '</main>', rule)
    assert result['stable_task_pass'] is not split, result


@pytest.mark.parametrize('root', ['main', 'body'])
@pytest.mark.parametrize('card', ['article', 'section', 'li'])
@pytest.mark.parametrize('missing', [False, True])
def test_record_local_header_retains_title_without_page_header(root, card, missing):
    row = {'name': 'Atlas', 'description': 'A useful reference collection.'}
    fields = sorted(row)
    rule = contract(1)
    rule['source_membership'] = {'kind': 'fixed', 'fields': fields,
        'record_hashes': [source_record_hash(row, fields, 'name_description')]}
    # A page header cannot replace a missing record title.
    title = '' if missing else '<header><h2>Atlas</h2></header>'
    html = (f'<{root}><header><h1>Atlas</h1></header><{card}>' + title
            + '<p>A useful reference collection.</p>' + f'</{card}></{root}>')
    result = evaluate_stable_task(html, rule)
    assert result['stable_task_pass'] is not missing, result
    if not missing:
        assert result['stable_complete_record_count'] == result['stable_unique_record_count'] == 1


@pytest.mark.parametrize('root', ['main', 'body'])
@pytest.mark.parametrize('wrapper', ['section', 'article'])
@pytest.mark.parametrize('heading', ['h1', 'h2'])
@pytest.mark.parametrize('depth', [1, 2, 3])
def test_heading_over_sibling_sections_is_not_a_record(root, wrapper, heading, depth):
    rows = [{'name': 'Alpha', 'description': 'First complete description.'},
            {'name': 'Beta', 'description': 'Second complete description.'}]
    fields = sorted(rows[0])
    rule = contract(2)
    rule['source_membership'] = {'kind': 'fixed', 'fields': fields,
        'record_hashes': sorted(source_record_hash(row, fields, 'name_description') for row in rows)}
    cards = ''.join('<section><p>' + row['name'] + '</p><p>' + row['description'] + '</p></section>'
                    for row in rows)
    html = cards
    for _ in range(depth):
        html = f'<{wrapper}><{heading}>Catalog</{heading}>' + html + f'</{wrapper}>'
    html = f'<{root}>' + html + f'</{root}>'
    result = evaluate_stable_task(html, rule)
    assert result['stable_task_pass'], result
    assert result['stable_complete_record_count'] == result['stable_unique_record_count'] == 2
    assert not evaluate_stable_task(html.replace('Second complete description.', ''), rule)['stable_task_pass']
    assert not evaluate_stable_task(html.replace('Second complete', 'Invented different'), rule)['stable_task_pass']


def test_record_heading_with_one_presentation_section_keeps_description():
    row = {'name': 'Atlas', 'description': 'First paragraph. Second paragraph.'}
    fields = sorted(row)
    rule = contract(1)
    rule['source_membership'] = {'kind': 'fixed', 'fields': fields,
        'record_hashes': [source_record_hash(row, fields, 'name_description')]}
    html = '<section><h2>Atlas</h2><section><p>First paragraph.</p><p>Second paragraph.</p></section></section>'
    assert evaluate_stable_task(html, rule)['stable_task_pass']
