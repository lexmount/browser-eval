"""Shared article-only visible text and identity policy."""
import re
import unicodedata
from .dynamic_lists import scoped_html
from .body_coverage import tokens
from .reference_quality import is_html_document_or_fragment, markdown_links

def author_key(value):
    """Keep literal symbol-only identities instead of comparing empty tokens."""
    if not isinstance(value, str):
        return None
    value = unicodedata.normalize('NFKC', value)
    # Joiners matter in rendered names and emoji sequences. Other invisible
    # format/control characters cannot create a username by themselves.
    value = ''.join(char for char in value
                    if not unicodedata.category(char).startswith('C') or char in '\u200c\u200d')
    value = ' '.join(value.split())
    if not any(not char.isspace() and not unicodedata.category(char).startswith('C') for char in value):
        return None
    words = tokens(value)
    return ('tokens', value.startswith('@'), tuple(words)) if words else ('literal', value)

def article_html(content):
    """Apply shared visibility and remove article-only fallback text."""
    soup = scoped_html(content)
    for fallback in list(soup.select('noscript')):
        if fallback.parent is not None:
            fallback.decompose()
    return soup

def without_markdown_images(content):
    """Image alt labels are metadata, including images nested in normal links."""
    pieces, cursor = [], 0
    for link in markdown_links(content):
        if link.start < cursor:
            continue
        pieces.append(content[cursor:link.start])
        if not link.is_image:
            original = content[link.start:link.end]
            pieces.append(original.replace(link.label, without_markdown_images(link.label), 1))
        cursor = link.end
    pieces.append(content[cursor:])
    return ''.join(pieces)

def is_article_html(content):
    """Classify HTML after complete leading non-content blocks only."""
    remaining = content.lstrip()
    # Attributes may contain a greater-than sign inside quoted values.
    attributes = r"(?:[^>\"']|\"[^\"]*\"|'[^']*')*"
    while True:
        prefix = re.match(
            r'<!--.*?-->|<(noscript|style|script)\b' + attributes
            + r'>.*?</\1\s*>|<(?:meta|link)\b' + attributes + r'>',
            remaining, re.I | re.S,
        )
        if not prefix:
            return is_html_document_or_fragment(remaining)
        remaining = remaining[prefix.end():].lstrip()


TIMESTAMP_LINE = re.compile(
    r'(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}'
    r'(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?'
    r'|\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?'
    r'|\d+\s+(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago'
    r'|\d+\s*(?:秒|分钟|小时|天|周|个月|年)前|刚刚)', re.I)


def mask_markdown_code(content, *, retain=False):
    """Shield fenced and indented literals before article record recognition.

    Retained blocks may belong to an existing comment, but their headings and
    links cannot introduce a title, section or author. Tabs use four-column stops.
    """
    prefix = '\ue000article-code'
    while prefix in content:
        prefix += '_'
    lines = content.splitlines(keepends=True)
    output, blocks = [], {}
    i = 0
    while i < len(lines):
        start = i
        line = lines[i]
        fence = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\r\n'))
        if fence and not (fence[1][0] == '`' and '`' in fence[2]):
            run = fence[1]
            i += 1
            closing = re.compile(r'^ {0,3}' + re.escape(run[0]) + '{' + str(len(run)) + r',}[ \t]*$')
            while i < len(lines):
                end = bool(closing.fullmatch(lines[i].rstrip('\r\n')))
                i += 1
                if end:
                    break
        elif line.strip() and line.expandtabs(4).startswith('    '):
            # Treat serializer indentation as literal, never as record syntax.
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i].expandtabs(4).startswith('    ')):
                i += 1
        else:
            output.append(line)
            i += 1
            continue
        key = prefix + str(len(blocks))
        blocks[key] = ''.join(lines[start:i])
        output.append('\n' + (key if retain else '') + '\n')
    return ''.join(output), blocks


def article_markdown(content):
    """Project cleaned HTML while retaining headings, links and ownership stops.

    This is used only when no reviewed native record containers remain. It is
    not a competing parser whose more permissive count can override native DOM
    ownership. Navigation and explicit related-content landmarks are excluded.
    """
    from bs4 import NavigableString, Tag
    soup = article_html(content)
    for node in list(soup.select('nav, aside, footer, [role="navigation"], [role="complementary"]')):
        if node.parent is not None:
            node.decompose()
    def render(node):
        if isinstance(node, NavigableString):
            return str(node)
        if not isinstance(node, Tag):
            return ''
        if node.name == 'pre':
            literal = node.get_text()
            longest = max((len(run) for run in re.findall(r'`+', literal)), default=0)
            fence = '`' * max(3, longest + 1)
            return '\n\n' + fence + '\n' + literal + '\n' + fence + '\n\n'
        if node.name == 'code':
            # Inline literal syntax must not create a record or section boundary.
            literal = node.get_text().replace('\n', ' ')
            return '`' + literal.replace('`', '\\`') + '`'
        if node.name == 'tr':
            cells = [render(child).replace('\n', ' ').strip()
                     for child in node.children if isinstance(child, Tag)
                     and child.name in ('td', 'th')]
            return '\n| ' + ' | '.join(cells) + ' |\n'
        text = ''.join(render(child) for child in node.children)
        if node.name == 'img':
            return ''
        if node.name == 'a':
            return '[' + text.strip() + '](' + node.get('href', '') + ')'
        if node.name in ('strong', 'b'):
            return '**' + text.strip() + '**'
        if re.fullmatch(r'h[1-6]', node.name):
            return '\n\n' + '#' * int(node.name[1]) + ' ' + text.strip() + '\n\n'
        if node.name == 'blockquote':
            return '\n\n' + '\n'.join('> ' + line for line in text.strip().splitlines()) + '\n\n'
        if node.name == 'br':
            return '\n'
        if node.name in ('p', 'div', 'section', 'article', 'ul', 'ol', 'li'):
            return '\n\n' + text.strip() + '\n\n'
        return text
    return render(soup)


def label_key(text):
    """Normalize whole-label formatting identically for rules and observations."""
    return ' '.join(unicodedata.normalize('NFKC', text).split()).casefold().strip(':：*_').strip()


def validate_labels(contract, fields=('ignored_labels', 'unavailable_notices')):
    """Require explicit task labels; an empty list is valid, an omission is not."""
    for field in fields:
        labels = contract.get(field)
        if (not isinstance(labels, list) or any(not isinstance(label, str)
                or not label_key(label) for label in labels)):
            raise ValueError('Invalid or missing task-declared ' + field)
        if len({label_key(label) for label in labels}) != len(labels):
            raise ValueError('Duplicate task-declared ' + field)


def declared_labels(contract, field):
    """Read the validated, explicit task list without runtime defaults."""
    return frozenset(label_key(label) for label in contract[field])
