"""Homepage product records must belong to the main content, with mutable routes."""
import html

import pytest

from lexbench_webfetch import benchmark
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


@pytest.fixture(scope='module')
def cases():
    root = benchmark.suite('popular-sites')[1]
    data = benchmark.apply_annotation_revision(
        'popular-sites', benchmark.read(root / 'tasks.json'), 'review-550-20261023')
    return {c['id']: c for c in data['cases']}


@pytest.mark.parametrize('case_id,owner,prefix', [
    ('global-homepage-042-github-com', '<main>', '/features/'),
    ('global-homepage-046-zoom-us', '<main id="main">', '/en/products/'),
    ('china-homepage-075-volcengine-com', '<main class="page">', '/product/'),
    ('china-homepage-081-elsevier-com', '<main id="main-content">', '/products/'),
])
@pytest.mark.parametrize('format,location,expected', [
    ('html', 'body', True), ('html', 'navigation', False),
    ('markdown', 'body', True), ('markdown', 'footer', False),
    ('markdown', 'missing-boundary', False),
])
def test_new_products_need_body_ownership(cases, case_id, owner, prefix,
                                         format, location, expected):
    case = cases[case_id]
    contract = case['acceptance_contract']
    scope = contract['entry_scope']
    anchors = '\n'.join(a['alternatives'][0] for a in contract['stable_text_anchors'])
    entries = [(f'New feature {i}', f'https://{contract["hosts"][0]}{prefix}future-{i}')
               for i in range(contract['minimum_records'])]
    if format == 'html':
        records = ''.join(f'<a href="{url}">{title}</a>' for title, url in entries)
        content = owner + (records if location == 'body' else '<p>Empty body.</p>') + '</main>'
        content += '<nav>' + (records if location == 'navigation' else '') + '</nav>'
        content += '<footer>' + html.escape(anchors) + '</footer>'
    else:
        records = '\n'.join(f'[{title}]({url})' for title, url in entries)
        start, end = scope['start_pattern'][1:-1], scope['end_pattern'][1:-1]
        content = start + '\n' + (records if location == 'body' else 'Empty body.')
        if location != 'missing-boundary':
            content += '\n' + end
        content += '\n' + (records if location == 'footer' else '') + '\n' + anchors
    assert task_passed(True, score_attempt(case, content.encode(), ok=True)) is expected


@pytest.mark.parametrize('owner,label', [
    ('<main class="nav-main">', 'A product'),
    ('<main id="main">', 'Explore products'),
])
def test_zoom_navigation_main_and_generic_buttons_do_not_count(cases, owner, label):
    case = cases['global-homepage-046-zoom-us']
    contract = case['acceptance_contract']
    anchors = ' '.join(a['alternatives'][0] for a in contract['stable_text_anchors'])
    records = ''.join(f'<a href="https://www.zoom.com/en/products/future-{i}/">{label}</a>'
                      for i in range(contract['minimum_records']))
    answer = owner + records + '</main><footer>' + html.escape(anchors) + '</footer>'
    assert not task_passed(True, score_attempt(case, answer.encode(), ok=True))


@pytest.mark.parametrize('format,location,expected', [
    ('html', 'body', True), ('html', 'navigation', False),
    ('markdown', 'body', True), ('markdown', 'footer', False),
    ('markdown', 'missing-boundary', False),
])
def test_figma_campaign_titles_do_not_define_product_ownership(cases, format, location, expected):
    case = cases['china-homepage-088-figma-com']
    anchors = '\n'.join(a['alternatives'][0]
                        for a in case['acceptance_contract']['stable_text_anchors'])
    entries = [('Explore design tools', 'design-explore'), ('Explore build tools', 'build-ship')]
    if format == 'html':
        records = ''.join(f'<a href="https://www.figma.com/{path}/">{title}</a>'
                          for title, path in entries)
        answer = '<main id="main"><h1>A new campaign</h1><h2>A new design heading</h2>'
        answer += records if location == 'body' else '<p>Body omitted.</p>'
        answer += '</main><nav>' + (records if location == 'navigation' else '') + '</nav>'
        answer += '<footer>' + html.escape(anchors) + '</footer>'
    else:
        records = '\n'.join(f'[{title}](https://www.figma.com/{path}/)' for title, path in entries)
        answer = '# A new campaign\n[Get started](https://www.figma.com/signup/)\n'
        answer += '## A new design heading\n' + (records if location == 'body' else 'Body omitted.')
        if location != 'missing-boundary':
            answer += '\n## Figma Socials'
        answer += '\n' + (records if location == 'footer' else '') + '\n' + anchors
    assert task_passed(True, score_attempt(case, answer.encode(), ok=True)) is expected


@pytest.mark.parametrize('case_id,prefix', [
    ('global-homepage-063-paypal-com', '/us/digital-wallet/manage-money/'),
    ('china-homepage-057-nih-gov', '/news-events/news-releases/'),
])
def test_homepage_route_families_allow_new_named_destinations(cases, case_id, prefix):
    case = cases[case_id]
    contract = case['acceptance_contract']
    anchors = '\n'.join(a['alternatives'][0] for a in contract['stable_text_anchors'])
    records = [f'[New resource {i}](https://{contract["hosts"][0]}{prefix}future-{i})'
               for i in range(contract['minimum_records'])]
    assert task_passed(True, score_attempt(case, ('\n'.join(records) + '\n' + anchors).encode(), ok=True))
    # A repeated destination or utility route cannot replace a real distinct item.
    records[-1] = records[0]
    assert not task_passed(True, score_attempt(case, ('\n'.join(records) + '\n' + anchors).encode(), ok=True))
    records[-1] = f'[Privacy](https://{contract["hosts"][0]}/privacy/)'
    assert not task_passed(True, score_attempt(case, ('\n'.join(records) + '\n' + anchors).encode(), ok=True))
