"""Verify the independently reviewed article title as a separate component."""
import re

import soupsieve

from .article_text import article_html, article_surface, is_article_html, mask_markdown_code, mask_markdown_inline_code, article_field_text
from .reference_quality import without_markdown_images
from .metrics import normalize_for_match
from .reference_quality import visible_mixed_html_text


def _outside_container_paragraphs(content):
    """Keep title candidates outside quoted/list paragraphs and lazy continuations."""
    from .reference_quality import markdown_literal_spans, is_html_document_or_fragment
    prefix = '\ue000title-literal'
    while prefix in content:
        prefix += '_'
    literals, block_tokens = {}, set()
    for start, end in reversed(markdown_literal_spans(content)):
        literal = content[start:end]
        token = prefix + str(len(literals)) + '\ue001'
        before = content[content.rfind('\n', 0, start)+1:start]
        if (re.fullmatch(r' {0,3}(?:(?:>[ \t]?)*|(?:[-+*]|\d+[.)])[ \t]+)', before)
                and re.match(r'(?:`{3,}|~{3,})[^\n]*\n', literal)):
            block_tokens.add(token)
        literals[token] = literal
        content = content[:start] + token + ('\n' if literal.endswith('\n') else '') + content[end:]

    def block_start(line):
        return (line.strip() in block_tokens
                or re.match(r'^ {0,3}(?:#{1,6}(?:[ \t]|$)|`{3,}|~{3,})', line)
                or re.fullmatch(r' {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})', line)
                or (not line.startswith(('    ', '\t')) and is_html_document_or_fragment(line)))

    output, paragraph, list_indent, quote_fence, quote_html = [], False, None, None, False
    for line in content.splitlines():
        indent = len(line)-len(line.lstrip(' '))
        quote = re.match(r'^ {0,3}(?:>[ \t]?)+(.*)$', line)
        if quote:
            inner = quote[1]
            if list_indent is not None and indent < list_indent:
                list_indent = None
            if not inner.strip():
                paragraph, quote_html = False, False
            elif quote_fence:
                if re.fullmatch(r' {0,3}' + re.escape(quote_fence[0]) + '{' + str(quote_fence[1]) + r',}[ \t]*', inner):
                    quote_fence = None
                paragraph = False
            elif fence := re.match(r'^ {0,3}(`{3,}|~{3,})', inner):
                quote_fence, paragraph = (fence[1][0], len(fence[1])), False
            elif quote_html or is_html_document_or_fragment(inner):
                quote_html, paragraph = True, False
            else:
                empty_item = re.fullmatch(r' {0,3}(?:[-+*]|\d+[.)])[ \t]*', inner)
                setext = paragraph and re.fullmatch(r' {0,3}=+[ \t]*', inner)
                paragraph = not (block_start(inner) or empty_item or setext
                                 or (not paragraph and inner.startswith(('    ', '\t'))))
            output.append('')
            continue
        if quote_fence or quote_html:
            quote_fence, quote_html, paragraph = None, False, False
        if not line.strip():
            paragraph = False
        elif block_start(line):
            paragraph = False
            if list_indent is not None and indent >= list_indent:
                output.append('')
                continue
            list_indent = None
        elif item := re.match(r'^( {0,3})(?:[-+*]|\d+[.)])(?:[ \t]+|$)(.*)$', line):
            if list_indent is None or len(item[1]) < list_indent:
                list_indent = len(line) - len(item[2])
            paragraph = bool(item[2].strip()) and not block_start(item[2])
            output.append('')
            continue
        elif paragraph or (list_indent is not None and indent >= list_indent):
            paragraph = True
            output.append('')
            continue
        else:
            list_indent = None
        output.append(line)
    content = '\n'.join(output)
    for token, literal in literals.items():
        content = content.replace(token, literal)
    return content


def _title_key(text, *, representation=None):
    normalized = normalize_for_match(text, representation=representation).strip()
    # DOM line breaks around punctuation are layout, not different wording.
    return re.sub(r'\s*([，。！？、；：,.!?;:])\s*', r'\1', normalized)


def validate_title_contract(contract):
    """Reject malformed frozen title contracts before evaluating a response."""
    try:
        if (not isinstance(contract, dict)
                or set(contract) - {'version', 'alternatives', 'html_selector',
                                    'excluded_html_selectors'}
                or not {'version', 'alternatives', 'html_selector'} <= set(contract)
                or type(contract['version']) is not int or contract['version'] != 1):
            raise ValueError('invalid fields or version')
        alternatives = contract['alternatives']
        if (not isinstance(alternatives, list) or not alternatives
                or any(not isinstance(text, str) or not text.strip()
                       or '\n' in text or '\r' in text or not _title_key(text, representation='plain_text')
                       for text in alternatives)):
            raise ValueError('nonempty observed single-line titles required')
        if not isinstance(contract['html_selector'], str) or not contract['html_selector'].strip():
            raise ValueError('reviewed title selector required')
        soupsieve.compile(contract['html_selector'])
        exclusions = contract.get('excluded_html_selectors', [])
        if (not isinstance(exclusions, list)
                or any(not isinstance(selector, str) or not selector.strip()
                       for selector in exclusions)):
            raise ValueError('reviewed hidden title selectors must be a list')
        for selector in exclusions:
            soupsieve.compile(selector)
    except (KeyError, TypeError, soupsieve.SelectorSyntaxError) as error:
        raise ValueError('invalid article title contract') from error


def verify_article_title(content, contract, *, representation=None):
    """Require a visible selected HTML title or an exact standalone text line.

    Explicit visibility follows the shared HTML projection. External stylesheet
    visibility must be reviewed when authoring source contracts. Plain text
    cannot establish DOM ownership, but a title mention inside prose cannot
    substitute for the standalone title component.
    """
    validate_title_contract(contract)
    if not isinstance(content, str):
        raise ValueError('article title content must be text')
    expected = {_title_key(text, representation='plain_text') for text in contract['alternatives']}
    candidates = []
    if representation == 'plain_text':
        candidates = [line.strip() for line in content.splitlines()]
        surface = 'standalone_text_line'
    elif representation == 'html' or is_article_html(content):
        from .dynamic_lists import visible_html_text
        soup = article_html(content)
        for selector in contract.get('excluded_html_selectors', []):
            for node in list(soup.select(selector)):
                if node.parent is not None:
                    node.decompose()
        candidates = [visible_html_text(str(node))
                      for node in soup.select(contract['html_selector'])]
        surface = 'reviewed_html_title_selector'
    else:
        content = _outside_container_paragraphs(content)
        content, blocks = mask_markdown_code(content)
        content, restore_inline_code = mask_markdown_inline_code(content, blocks.values())
        content = article_surface(content)
        for line in visible_mixed_html_text(content).splitlines():
            text = line.strip()
            heading = re.match(r'^#{1,6}[ \t]+', text)
            if heading:
                text = re.sub(r'^#{1,6}[ \t]+', '', text)
                text = re.sub(r'[ \t]+#+[ \t]*$', '', text)
            # Inline markup can format a title but cannot supply hidden text.
            text = without_markdown_images(text)
            if heading or restore_inline_code(text, remove=True).strip():
                candidates.append(article_field_text(text, restore_inline_code))
        surface = 'standalone_text_line'
    matched = next((text for text in candidates if _title_key(text, representation='plain_text') in expected), None)
    return {'article_title_pass': matched is not None,
            'article_title_surface': surface,
            'article_title_matched': matched}
