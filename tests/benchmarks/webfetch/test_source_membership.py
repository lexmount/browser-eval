"""Fixed records retain source identity; duplicate tasks share the same policy."""
from copy import deepcopy

import pytest

from lexbench_webfetch import benchmark
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.stable_tasks import source_record_hash, validate_acceptance_contract


@pytest.fixture(scope='module')
def paired_cases():
    return {suite: {case['id']: case for case in benchmark.apply_annotation_revision(
        suite, benchmark.read(benchmark.suite(suite)[1] / 'tasks.json'),
        'review-550-20261023')['cases']} for suite in ('webfetch', 'dynamic-content')}


def test_duplicate_tasks_share_contract_and_have_no_lexical_veto(paired_cases):
    pairs = set(paired_cases['webfetch']) & set(paired_cases['dynamic-content'])
    assert len(pairs) == 10
    for name in pairs:
        first, second = (paired_cases[suite][name] for suite in paired_cases)
        assert first['url'] == second['url']
        assert first['task_prompt'] == second['task_prompt']
        assert first['acceptance_contract'] == second['acceptance_contract']
        assert not first.get('forbidden_any') and not second.get('forbidden_any')


@pytest.mark.parametrize('case_id', ['dynamic-quotes-api', 'dynamic-swagger'])
@pytest.mark.parametrize('variant,expected', [
    ('correct', True), ('notice', True), ('notice-only', False),
    ('wrong', False), ('invented', False),
])
def test_duplicate_public_scores(paired_cases, case_id, variant, expected):
    original = next(c for c in benchmark.read(benchmark.suite('webfetch')[1] / 'tasks.json')['cases']
                    if c['id'] == case_id)
    text = '\n\n'.join(a['alternatives'][0]
                        for a in original['acceptance_contract']['stable_text_anchors'])
    if variant == 'notice':
        text = 'About cookies on this site\n\n' + text
    elif variant == 'notice-only':
        text = 'About cookies on this site'
    elif variant == 'wrong':
        text = text.replace('Albert Einstein', 'Invented Author') if case_id == 'dynamic-quotes-api' else text.replace('/pet', '/fakepet')
    elif variant == 'invented':
        text = '\n\n'.join(f'“An invented quotation with sufficient words number {i}.”\nby Invented Author {i}\nTags: fake testing'
                            for i in range(10)) if case_id == 'dynamic-quotes-api' else text.replace('pet', 'invented').replace('store', 'fabricated').replace('user', 'unreal')
    for cases in paired_cases.values():
        result = score_attempt(cases[case_id], text.encode(), ok=True)
        assert not result['forbidden_requirement_hits']
        assert task_passed(True, result) is expected


def test_fixed_records_bind_fields_together_and_changing_accepts_new_records():
    fields = ['author', 'quote', 'tags']
    records = [{'author': name, 'quote': f'A synthetic quotation with enough words from {name}.',
                'tags': 'testing sample'} for name in ('Alice', 'Bob')]
    contract = {'version': 1, 'schema': 'quote', 'minimum_records': 2,
                'stable_text_anchors': [], 'source_membership': {'kind': 'fixed',
                'fields': fields, 'record_hashes': sorted(source_record_hash(r, fields, 'quote') for r in records)}}
    case = {'url': 'https://example.test/quotes', 'task_kind': 'body',
            'min_content_chars': 1, 'acceptance_contract': contract}
    def answer(rows):
        return '\n\n'.join(f'“{r["quote"]}”\nby {r["author"]}\nTags: {r["tags"]}' for r in rows).encode()
    assert task_passed(True, score_attempt(case, answer(records), ok=True))
    swapped = deepcopy(records)
    swapped[0]['author'], swapped[1]['author'] = swapped[1]['author'], swapped[0]['author']
    assert not task_passed(True, score_attempt(case, answer(swapped), ok=True))
    contract['source_membership'] = {'kind': 'changing'}
    assert task_passed(True, score_attempt(case, answer(swapped), ok=True))
    del contract['source_membership']
    assert task_passed(True, score_attempt(case, answer(swapped), ok=True))


@pytest.mark.parametrize('policy', [
    None,
    {'kind': 'changing', 'record_hashes': []}, {'kind': 'fixed'},
    {'kind': 'fixed', 'fields': ['author'], 'record_hashes': ['a' * 64]},
    {'kind': 'fixed', 'fields': ['author', 'quote', 'tags'], 'record_hashes': ['invalid']},
])
def test_invalid_source_membership_rejected(policy):
    with pytest.raises(ValueError):
        validate_acceptance_contract({'version': 1, 'schema': 'quote', 'minimum_records': 1,
                                      'stable_text_anchors': [], 'source_membership': policy})


@pytest.mark.parametrize('prefix', ['', 'An unrelated notice.\n\n'])
@pytest.mark.parametrize('missing', [False, True])
@pytest.mark.parametrize('count', [1, 2])
@pytest.mark.parametrize('spaced', [False, True])
def test_catalog_notice_cannot_own_a_complete_card_or_repair_missing_fields(prefix, missing, count, spaced):
    fields = ['description', 'price', 'reviews', 'title']
    record = {'title': 'Sample Laptop', 'price': '$100', 'reviews': '2',
              'description': 'A complete synthetic laptop description.'}
    contract = {'version': 1, 'schema': 'catalog_product', 'minimum_records': 1,
                'stable_text_anchors': [], 'source_membership': {'kind': 'fixed',
                'fields': fields, 'record_hashes': [source_record_hash(record, fields, 'catalog_product')]}}
    body = '$100\nSample Laptop\nA complete synthetic laptop description.\n2 reviews'
    if count == 2:
        second = {**record, 'title': 'Second Laptop'}
        contract['minimum_records'] = 2
        contract['source_membership']['record_hashes'] = sorted([
            *contract['source_membership']['record_hashes'], source_record_hash(second, fields, 'catalog_product')])
        body += '\n' + body.replace('Sample Laptop', 'Second Laptop')
    if spaced:
        body = body.replace('\nA complete', '\n\nA complete').replace('\n2 reviews', '\n\n2 reviews')
    if missing:
        body = body.replace('Sample Laptop\n', '')
    case = {'url': 'https://example.test/catalog', 'task_kind': 'body',
            'min_content_chars': 1, 'acceptance_contract': contract}
    assert task_passed(True, score_attempt(case, (prefix + body).encode(), ok=True)) is not missing


def test_revised_record_rules_do_not_carry_unused_historical_facts(paired_cases):
    for cases in paired_cases.values():
        for case in cases.values():
            if case.get('acceptance_contract', {}).get('source_membership', {}).get('kind') == 'fixed':
                assert not case.get('required_facts')


def test_reviewed_stable_text_is_required_without_obsolete_ibiblio_groups():
    from lexbench_webfetch.stable_tasks import evaluate_stable_task
    cases = benchmark.apply_annotation_revision('access-restrictions',
        benchmark.read(benchmark.suite('access-restrictions')[1] / 'tasks.json'),
        'review-550-20261023')['cases']
    for case_id, count in [('risk-diverse-042', 20), ('risk-diverse-192', 11)]:
        case = next(c for c in cases if c['id'] == case_id)
        contract = deepcopy(case['acceptance_contract'])
        assert contract.get('stable_text_role', 'required') == 'required'
        assert len(contract['stable_text_anchors']) == count
        # Exercise the actual anchor contract separately from body/source replay.
        text = '\n'.join(a['alternatives'][0] for a in contract['stable_text_anchors'])
        if case_id == 'risk-diverse-192':
            text += '\nibiblio public digital library freely available information software UNC'
            assert 'stable-06' not in {a['id'] for a in contract['stable_text_anchors']}
            assert 'stable-13' not in {a['id'] for a in contract['stable_text_anchors']}
        assert evaluate_stable_task(text, contract)['stable_task_pass']
        label = 'Register' if case_id == 'risk-diverse-042' else 'Privacy'
        assert not evaluate_stable_task(text.replace(label, 'omitted'), contract)['stable_task_pass']


def test_deduplicated_facts_still_have_mandatory_anchor_checks(paired_cases):
    from lexbench_webfetch.metrics import normalize_for_match
    from lexbench_webfetch.stable_tasks import evaluate_stable_task
    removed = {
        'dynamic-041-en-stable-heroui-table': ['Name', 'Role', 'Status'],
        'dynamic-038-material-angular-dev-components': ['No.', 'Name', 'Weight', 'Symbol'],
        'dynamic-092-play-uol-com-br': ['UOL Play'],
        'dynamic-025-025-tabulator-vertical-virtual-dom': ['Virtual DOM - Vertical', 'View Source'],
        'dynamic-033-033-vuetify-usage-table': ['Usage'],
        'dynamic-104-mui-data-grid': ['MUI X Data Grid', 'API'],
    }
    for case_id, labels in removed.items():
        case = paired_cases['dynamic-content'][case_id]
        contract = case['acceptance_contract']
        assert contract.get('stable_text_role', 'required') == 'required'
        facts = {normalize_for_match(v) for f in case.get('required_facts', []) for v in f['alternatives']}
        for label in labels:
            assert normalize_for_match(label) not in facts
            anchor = next(a for a in contract['stable_text_anchors'] if {normalize_for_match(v) for v in a['alternatives']} == {normalize_for_match(label)})
            # Verify the retained checker, independently of missing full body inputs.
            for text, expected in [(label, True), ('unrelated', False)]:
                result = evaluate_stable_task(text, contract)
                row = next(a for a in result['stable_anchor_results'] if a['id'] == anchor['id'])
                assert row['pass'] is expected, (case_id, label, text)

@pytest.mark.parametrize('markup', ['html', 'markdown'])
@pytest.mark.parametrize('variant,expected', [
    ('complete', True), ('sibling', True), ('wrong', False),
    ('inside', False), ('nested-heading', False), ('borrow', False),
])
def test_fixed_review_ownership_respects_structural_boundaries(paired_cases, markup, variant, expected):
    from html import escape
    original = next(c for c in benchmark.read(benchmark.suite('webfetch')[1] / 'tasks.json')['cases']
                    if c['id'] == 'dynamic-product-reviews')
    facts = {f['id']: f['alternatives'][0] for f in original['required_facts']}
    rows = [(facts[f'entry-{i}-date'], facts[f'entry-{i}-text']) for i in range(1, 6)]
    saved = rows[-1][1]
    if variant == 'wrong':
        rows[-1] = (rows[-1][0], 'A fabricated different review about another product.')
    elif variant == 'borrow':
        rows[-1] = (rows[-1][0], '')
    if markup == 'html':
        cards = ['<section><p>' + escape(date) + '</p><p>' + escape(text) + '</p></section>'
                 for date, text in rows]
        if variant in {'inside', 'nested-heading'}:
            tag = 'p' if variant == 'inside' else 'h3'
            cards[-1] = cards[-1].replace('</section>',
                f'<{tag}>Invented extra review text.</{tag}></section>')
        tail = '<footer>' + (escape(saved) if variant == 'borrow' else 'Unrelated page information.') + '</footer>'
        text = '<main>' + ''.join(cards) + '</main>' + (tail if variant in {'sibling', 'wrong', 'borrow'} else '')
    else:
        text = '\n\n'.join('## ' + date + '\n\n' + body for date, body in rows)
        if variant in {'sibling', 'wrong', 'borrow'}:
            text += '\n\n## Other information\n\n' + (saved if variant == 'borrow' else 'Unrelated page information.')
        elif variant in {'inside', 'nested-heading'}:
            text += ('\n\n### Added claim' if variant == 'nested-heading' else '') + '\n\nInvented extra review text.'
    for cases in paired_cases.values():
        result = score_attempt(cases['dynamic-product-reviews'], text.encode(), ok=True)
        assert task_passed(True, result) is expected


@pytest.mark.parametrize('form,expected', [('nested-body', True), ('peer-headings', True), ('nested-heading-change', False)])
def test_fixed_review_html_nested_body_and_peer_heading_ownership(paired_cases, form, expected):
    from html import escape
    original = next(c for c in benchmark.read(benchmark.suite('webfetch')[1] / 'tasks.json')['cases']
                    if c['id'] == 'dynamic-product-reviews')
    facts = {f['id']: f['alternatives'][0] for f in original['required_facts']}
    rows = [(facts[f'entry-{i}-date'], facts[f'entry-{i}-text']) for i in range(1, 6)]
    if form == 'nested-body':
        text = '<main>' + ''.join('<section><p>' + escape(date) + '</p><section><p>'
            + escape(body) + '</p></section></section>' for date, body in rows) + '<footer>Other information</footer></main>'
    else:
        text = '<main>' + ''.join('<h2>' + escape(date) + '</h2><p>' + escape(body) + '</p>' for date, body in rows)
        text += ('<h2>Other information</h2><p>Page information</p>' if form == 'peer-headings'
                 else '<h3>Extra claim</h3><p>Invented extra review text.</p>') + '</main>'
    for cases in paired_cases.values():
        result = score_attempt(cases['dynamic-product-reviews'], text.encode(), ok=True)
        assert task_passed(True, result) is expected


@pytest.mark.parametrize('outer', ['section', 'article'])
@pytest.mark.parametrize('depth', [1, 2])
@pytest.mark.parametrize('variant', ['correct', 'missing', 'wrong'])
def test_innermost_review_cannot_absorb_sibling_in_single_date_container(outer, depth, variant):
    from lexbench_webfetch.stable_tasks import evaluate_stable_task
    row = {'date': '2026-10-01', 'text': 'Excellent service.'}
    fields = ['date', 'text']
    contract = {'version': 1, 'schema': 'review', 'minimum_records': 1,
                'stable_text_anchors': [], 'source_membership': {'kind': 'fixed',
                'fields': fields, 'record_hashes': [source_record_hash(row, fields, 'review')]}}
    body = row['text'] if variant == 'correct' else '' if variant == 'missing' else 'Different service.'
    sibling = 'Subscribe to our newsletter.' if variant == 'correct' else row['text']
    text = (f'<{outer}>' * depth + '<article><p>' + row['date'] + '</p><section><p>'
            + body + '</p></section></article><article><p>' + sibling + '</p></article>'
            + f'</{outer}>' * depth)
    result = evaluate_stable_task(text, contract)
    assert result['stable_task_pass'] is (variant == 'correct'), result


@pytest.mark.parametrize('wrapper', ['div', 'article', 'section', 'li'])
@pytest.mark.parametrize('missing', [False, True])
def test_review_header_keeps_date_with_own_body(paired_cases, wrapper, missing):
    from html import escape
    original = next(c for c in benchmark.read(benchmark.suite('webfetch')[1] / 'tasks.json')['cases']
                    if c['id'] == 'dynamic-product-reviews')
    facts = {f['id']: f['alternatives'][0] for f in original['required_facts']}
    cards = []
    for i in range(1, 6):
        body = '' if missing and i == 5 else '<p>' + escape(facts[f'entry-{i}-text']) + '</p>'
        cards.append(f'<{wrapper}><header><p>' + facts[f'entry-{i}-date'] + '</p></header>' + body + f'</{wrapper}>')
    # Matching sibling text must not complete the last date-only review.
    tail = '<div><p>' + escape(facts['entry-5-text']) + '</p></div>'
    text = '<main>' + ''.join(cards) + tail + '<footer>Other information</footer></main>'
    for cases in paired_cases.values():
        result = score_attempt(cases['dynamic-product-reviews'], text.encode(), ok=True)
        assert task_passed(True, result) is not missing, result


@pytest.mark.parametrize('wrapper', ['article', 'section', 'div'])
@pytest.mark.parametrize('depth', [1, 2])
@pytest.mark.parametrize('missing', [False, True])
@pytest.mark.parametrize('body_wrapper', ['', 'div', 'section'])
def test_date_only_presentation_block_keeps_enclosing_owned_body(wrapper, depth, missing, body_wrapper):
    from lexbench_webfetch.stable_tasks import evaluate_stable_task
    row = {'date': '2026-10-01', 'text': 'Excellent service.'}
    fields = ['date', 'text']
    contract = {'version': 1, 'schema': 'review', 'minimum_records': 1,
                'stable_text_anchors': [], 'source_membership': {'kind': 'fixed',
                'fields': fields, 'record_hashes': [source_record_hash(row, fields, 'review')]}}
    body = '' if missing else '<p>' + row['text'] + '</p>'
    if body_wrapper:
        body = f'<{body_wrapper}>' + body + f'</{body_wrapper}>'
    date_wrapper = 'div' if depth == 1 else 'section'
    text = (f'<{wrapper}>' + f'<{date_wrapper}>' * depth + '<p>' + row['date'] + '</p>'
            + f'</{date_wrapper}>' * depth + body
            + '<article><p>' + row['text'] + '</p></article>' + f'</{wrapper}>')
    result = evaluate_stable_task(text, contract)
    assert result['stable_task_pass'] is not missing, result
