"""Physical record positions and historical comment labels keep source semantics."""
from copy import deepcopy
from html import escape

import pytest

from lexbench_webfetch.article_qualification import qualify_article_comments
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


BREAKS = {'lf': '\n', 'cr': '\r', 'crlf': '\r\n', 'vertical-tab': '\v',
          'form-feed': '\f', 'file-separator': '\x1c', 'group-separator': '\x1d',
          'record-separator': '\x1e', 'next-line': '\x85',
          'line-separator': '\u2028', 'paragraph-separator': '\u2029'}


def controls():
    for kind in ('plain-name', 'plain-product', 'markdown-table', 'image-heading', 'html'):
        for break_name, separator in BREAKS.items():
            for valid in (True, False):
                path = '/item/1' if valid else '/outside/1'
                acceptance = {'version': 1, 'schema': 'named_link', 'minimum_records': 1,
                              'hosts': ['shop.example'], 'entry_path': r'/item/\d+',
                              'stable_text_anchors': []}
                if kind.startswith('plain'):
                    lines = ['Introduction', '', 'Widget', 'https://shop.example' + path, '$10.00']
                    representation = 'plain_text'
                    if kind == 'plain-product':
                        acceptance.update(schema='linked_product', price_patterns=[r'\$\d+\.\d{2}'])
                elif kind == 'markdown-table':
                    lines = ['Introduction', '', '| Name | Details |', '| --- | --- |',
                             '| Widget | [Details](' + path + ') |']
                    representation = 'markdown'
                    acceptance['entry_scope'] = {'html_selector': '.items',
                                                 'markdown_title_column': 'Name',
                                                 'markdown_link_column': 'Details'}
                elif kind == 'image-heading':
                    lines = ['Introduction', '', '[![Widget](/poster.png)](' + path + ')', '', '## Widget']
                    representation = 'markdown'
                else:
                    lines = ['<div hidden><a href="/item/2">Hidden</a></div>',
                             '<p>Introduction</p>', '<a href="' + path + '">Widget</a>']
                    representation = 'html'
                yield {'id': 'record-line-position-' + kind + '-' + break_name + '-' + str(valid).lower(),
                       'category': 'structured-extraction',
                       'root_cause': 'Text records indexed splitlines output with HTMLParser LF-only source offsets.',
                       'failure_mode': 'false-negative' if valid else 'false-positive',
                       'case': {'url': 'https://shop.example/catalog', 'expected_any': [],
                                'acceptance_contract': acceptance},
                       'artifact': separator.join(lines), 'ok': True,
                       'metadata': {'answer_format': representation}, 'expected_task_pass': valid}


LINE_CONTROLS = list(controls())


@pytest.mark.parametrize('row', LINE_CONTROLS, ids=lambda row: row['id'])
def test_public_records_accept_equivalent_line_boundaries_without_exceptions(row):
    metrics = score_attempt(row['case'], row['artifact'].encode(), ok=True, metadata=row['metadata'])
    assert task_passed(row['case'], metrics) is row['expected_task_pass']
    assert metrics['stable_complete_record_count'] == int(row['expected_task_pass'])


def comment_control(field, label, text, expected, surface, role='body'):
    contract = {'version': 2, 'minimum_comments': 1, 'record_selector': '.comment',
                'author_selector': '.author', 'body_selector': '.text',
                'section_markers': ['Comments'], 'ignored_labels': [], 'unavailable_notices': []}
    contract[field] = [label]
    author, body = (text, 'A useful reply.') if role == 'author' else ('Reader', text)
    artifact = ('<div class="comment"><span class="author">' + escape(author) + '</span><p class="text">' + escape(body) + '</p></div>'
                if surface == 'html' else 'Comments\n\n' + author + ': ' + body)
    return {'id': 'comment-qualification-literal-' + field + '-' + surface + '-' + role + '-' + str(expected).lower() + '-' + str(len(text)),
            'category': 'reference-evidence',
            'root_cause': 'Historical comment qualification interpreted rendered scalar labels as Markdown while runtime uses literal text.',
            'failure_mode': 'false-negative' if expected else 'false-positive',
            'case': {'url': 'https://example.test/article', 'expected_any': [],
                     'source_benchmark': {'comment_contract': contract}},
            'artifact': artifact,
            'ok': True, 'metadata': {'answer_format': surface}, 'expected_task_pass': expected}


COMMENT_CONTROLS = [comment_control(field, label, text, expected, surface)
                    for field, label in [('ignored_labels', '**like**'), ('unavailable_notices', '_hidden_')]
                    for surface in ('html', 'plain_text')
                    for text, expected in [(label, False), (label.strip('*_'), True),
                                           ('A reply about ' + label, True)]]
COMMENT_CONTROLS += [comment_control('ignored_labels', '**like**', text, expected, surface, 'author')
                     for surface in ('html', 'plain_text')
                     for text, expected in [('**like**', False), ('like', True)]]
COMMENT_CONTROLS += [comment_control(field, label, text, expected, surface)
                     for field, label, literal in [('ignored_labels', 'like', '**like**'),
                                                   ('unavailable_notices', 'hidden', '_hidden_')]
                     for surface in ('html', 'plain_text')
                     for text, expected in [(literal, True), (label, False)]]
COMMENT_CONTROLS += [comment_control('ignored_labels', 'like', text, expected, surface, 'author')
                     for surface in ('html', 'plain_text')
                     for text, expected in [('**like**', True), ('like', False)]]


@pytest.mark.parametrize('row', COMMENT_CONTROLS, ids=lambda row: row['id'])
def test_historical_comment_floor_matches_public_runtime_literal_labels(row):
    from bs4 import BeautifulSoup

    if row['metadata']['answer_format'] == 'html':
        soup = BeautifulSoup(row['artifact'], 'html.parser')
        author, text = soup.select_one('.author').get_text(), soup.select_one('.text').get_text()
    else:
        author, _, text = row['artifact'].split('\n')[-1].partition(': ')
    case = deepcopy(row['case'])
    reference = {'version': 1, 'case_id': 'article', 'url': case['url'],
                 'scope': 'initial_load_only', 'observation_complete': True,
                 'context_id': 'historical', 'observed_on': '2026-10-19',
                 'source_observation_counts': [1],
                 'source_evidence': [{'path': 'capture.json', 'sha256': 'a' * 64,
                                      'scope': 'complete_initial_page'}],
                 'records': [{'id': 'one', 'author': author, 'text': text, 'parent_id': None,
                              'complete_initial_text': True}]}
    qualification = qualify_article_comments(case['source_benchmark']['comment_contract'], reference,
                                            case_id='article', url=case['url'])
    assert qualification['valid_comment_count'] == int(row['expected_task_pass'])
    assert (qualification['status'] == 'qualified') is row['expected_task_pass']
    metrics = score_attempt(case, row['artifact'].encode(), ok=True, metadata=row['metadata'])
    assert task_passed(case, metrics) is row['expected_task_pass']
    assert metrics['article_comment_count'] == int(row['expected_task_pass'])


CONTROLS = LINE_CONTROLS + COMMENT_CONTROLS
