"""Code samples and serializer metadata cannot manufacture required components."""
import re
from textwrap import indent

import pytest

from lexbench_webfetch.evaluation import evaluate_response
from .test_article_post_coverage_integration import fixture
from . import test_article_component_integration as component_fixture


@pytest.mark.parametrize('number', [38, 39, 40, 41, 101])
@pytest.mark.parametrize('representation', ['real', 'spaces', 'tabs', 'fenced', 'unclosed'])
def test_collection_code_samples_do_not_supply_posts(number, representation):
    case, stable, posts = fixture(number)
    text = '\n\n'.join(posts)
    if representation in ('spaces', 'tabs'):
        text = indent(text, '    ' if representation == 'spaces' else '\t')
    elif representation == 'fenced':
        text = '```markdown\n'+text+'\n```'
    elif representation == 'unclosed':
        text = '```markdown\n'+text
    result = evaluate_response(case, (stable+'\n\n'+text).encode())
    assert result['passed'] is (representation == 'real')


@pytest.mark.parametrize('number', [38, 39, 40, 41, 101])
@pytest.mark.parametrize('author', ['Today', '2026-10-22', '12:34', '\u200b', 'Actual reader', '🙂'])
@pytest.mark.parametrize('route', ['previous_line', 'prefix', 'linked_profile'])
def test_collection_author_validation_after_every_extraction_route(number, author, route):
    case, stable, posts = fixture(number)
    records = []
    for post in posts:
        url = re.search(r'\]\(([^)]+)\)', post)[1]
        if route == 'previous_line':
            header = author+'\n[Today]('+url+')'
        elif route == 'prefix':
            header = '## '+author+' [Today]('+url+')'
        else:
            profile = 'https://'+case['source_benchmark']['post_contract']['hosts'][0]+'/reader'
            header = '## ['+author+']('+profile+') [Today]('+url+')'
        records.append(header+'\n\nVisible post body.')
    result = evaluate_response(case, (stable+'\n\n'+'\n\n'.join(records)).encode())
    assert result['passed'] is (author in ['Actual reader', '🙂'])


@pytest.mark.parametrize('component', ['title', 'comments'])
@pytest.mark.parametrize('prefix', ['    ', '\t'])
def test_code_cannot_supply_title_or_comment_authors(component, prefix):
    fixture = component_fixture.ArticleComponentIntegration()
    fixture.setUp()
    assert fixture.run_score(fixture.complete)['passed']
    text = fixture.complete
    if component == 'title':
        text = text.replace('# Life and dignity', prefix+'# Life and dignity')
    else:
        text = text.replace('## Reader\nA thoughtful book.', indent('## Reader\nA thoughtful book.', prefix))
    assert not fixture.run_score(text)['passed']


@pytest.mark.parametrize('literal', ['    ### Fake reader\n    Literal example.', '\t### Fake reader\n\tLiteral example.', '```\n### Fake reader\n```'])
def test_code_remains_body_of_existing_comment(literal):
    fixture = component_fixture.ArticleComponentIntegration()
    fixture.setUp()
    text = fixture.complete.replace('A thoughtful book.', '\n'+literal)
    result = fixture.run_score(text)
    assert result['passed']
    assert result['metrics']['article_comment_count'] == 1


@pytest.mark.parametrize('spaces', [0, 1, 2, 3])
def test_ordinary_markdown_heading_indentation_remains_valid(spaces):
    fixture = component_fixture.ArticleComponentIntegration()
    fixture.setUp()
    text = fixture.complete.replace('# Life and dignity', ' '*spaces+'# Life and dignity')
    assert fixture.run_score(text)['passed']


@pytest.mark.parametrize('author,expected', [('返信', False), ('2026-10-22T12:34:00Z', False), ('\u200b', False), ('Reply researcher', True), ('🙂', True)])
@pytest.mark.parametrize('quoted', [False, True])
def test_native_and_quote_authors_follow_task_policy(author, expected, quoted):
    from .test_article_cleaned_html import POSTS
    from copy import deepcopy
    case, stable, _ = fixture(38)
    contract = deepcopy(POSTS)
    contract.update(minimum_posts=1, non_author_labels=['返信'], allow_empty_outer_with_quotes=True)
    case['source_benchmark']['post_contract'] = contract
    if quoted:
        fields = '<span class="author">Real reader</span><blockquote><span class="author">'+author+'</span><p class="body">Visible quote.</p></blockquote>'
    else:
        fields = '<span class="author">'+author+'</span><p class="body">Visible body.</p>'
    content = '<p>'+stable+'</p><article class="post"><a class="permalink" href="https://example.test/post/1">Today</a>'+fields+'</article>'
    assert evaluate_response(case, content.encode())['passed'] is expected
