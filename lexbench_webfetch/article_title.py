"""Verify the independently reviewed article title as a separate component."""
import re

import soupsieve

from .article_text import article_html, is_article_html, without_markdown_images, mask_markdown_code
from .metrics import normalize_for_match
from .reference_quality import visible_mixed_html_text


def _title_key(text):
    normalized = normalize_for_match(text).strip()
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
                       or '\n' in text or '\r' in text or not _title_key(text)
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


def verify_article_title(content, contract):
    """Require a visible selected HTML title or an exact standalone text line.

    Explicit visibility follows the shared HTML projection. External stylesheet
    visibility must be reviewed when authoring source contracts. Plain text
    cannot establish DOM ownership, but a title mention inside prose cannot
    substitute for the standalone title component.
    """
    validate_title_contract(contract)
    if not isinstance(content, str):
        raise ValueError('article title content must be text')
    expected = {_title_key(text) for text in contract['alternatives']}
    candidates = []
    if is_article_html(content):
        soup = article_html(content)
        for selector in contract.get('excluded_html_selectors', []):
            for node in list(soup.select(selector)):
                if node.parent is not None:
                    node.decompose()
        candidates = [node.get_text(' ', strip=True)
                      for node in soup.select(contract['html_selector'])]
        surface = 'reviewed_html_title_selector'
    else:
        content, _ = mask_markdown_code(content)
        if re.search(r'<details\b', content, re.I):
            content = str(article_html(content))
        for line in visible_mixed_html_text(content).splitlines():
            text = line.strip()
            text = re.sub(r'^#{1,6}[ \t]+', '', text)
            text = re.sub(r'[ \t]+#+[ \t]*$', '', text)
            # Inline markup can format a title but cannot supply hidden text.
            candidates.append(without_markdown_images(text))
        surface = 'standalone_text_line'
    matched = next((text for text in candidates if _title_key(text) in expected), None)
    return {'article_title_pass': matched is not None,
            'article_title_surface': surface,
            'article_title_matched': matched}
