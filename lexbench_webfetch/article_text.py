"""Shared article-only visible text and identity policy."""
import re
import unicodedata
from .dynamic_lists import scoped_html
from .body_coverage import tokens
from .reference_quality import is_html_document_or_fragment


def _visible_character(char):
    """Ignore format controls, invisible marks and blank filler glyphs."""
    code = ord(char)
    return (not char.isspace() and not unicodedata.category(char).startswith('C')
            and code not in {0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180F, 0x2800}
            and not 0x180B <= code <= 0x180D
            and not 0xFE00 <= code <= 0xFE0F
            and not 0xE0100 <= code <= 0xE01EF)


def author_key(value, *, representation=None):
    """Keep literal symbol-only identities instead of comparing empty tokens."""
    if not isinstance(value, str):
        return None
    value = unicodedata.normalize('NFKC', value)
    # Joiners matter in rendered names and emoji sequences. Other invisible
    # format/control characters cannot create a username by themselves.
    value = ''.join(char for char in value
                    if not unicodedata.category(char).startswith('C') or char in '\u200c\u200d')
    value = ' '.join(value.split())
    if not any(_visible_character(char) for char in value):
        return None
    words = tokens(value, representation='plain_text' if representation in {'plain_text', 'html'} else None)
    return ('tokens', value.startswith('@'), tuple(words)) if words else ('literal', value)

def article_html(content):
    """Apply shared visibility and remove article-only fallback text."""
    soup = scoped_html(content)
    for fallback in list(soup.select('noscript')):
        if fallback.parent is not None:
            fallback.decompose()
    return soup


def article_surface(content, *, representation=None):
    """Apply article visibility to mixed markup before Markdown extraction."""
    return str(article_html(content)) if representation != 'plain_text' and re.search(r'<(?:details|noscript)\b', content, re.I) else content

def is_article_html(content, *, representation=None):
    """Classify HTML after complete leading non-content blocks only."""
    if representation == 'plain_text':
        return False
    if representation == 'html':
        return True
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
        fence = re.match(r'^ {0,3}((?:> ?)*)(`{3,}|~{3,})(.*)$', line.rstrip('\r\n'))
        if fence and not (fence[2][0] == '`' and '`' in fence[3]):
            run = fence[2]
            depth = fence[1].count('>')
            quote_prefix = r'^ {0,3}(?:> ?){' + str(depth) + '}'
            i += 1
            body_start = i
            end = False
            closing = re.compile(quote_prefix + re.escape(run[0]) + '{' + str(len(run)) + r',}[ \t]*$')
            while i < len(lines):
                if depth and lines[i].strip() and not re.match(quote_prefix, lines[i]):
                    break
                end = bool(closing.fullmatch(lines[i].rstrip('\r\n')))
                i += 1
                if end:
                    break
            body = ''.join(re.sub(quote_prefix, '', part) if depth else part
                           for part in lines[body_start:i-int(end)])
        elif line.strip() and line.expandtabs(4).startswith('    '):
            # Treat serializer indentation as literal, never as record syntax.
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i].expandtabs(4).startswith('    ')):
                i += 1
            body = ''.join(lines[start:i])
        else:
            output.append(line)
            i += 1
            continue
        key = prefix + str(len(blocks)) + '\ue001'
        blocks[key] = body
        output.append('\n' + (key if retain else '') + '\n')
    return ''.join(output), blocks


def mask_markdown_inline_code(content, protected_literals=()):
    """Shield inline-code boundaries while retaining rendered text for owned fields."""
    prefix = '\ue000article-inline-code'
    while prefix in content or any(prefix in literal for literal in protected_literals):
        prefix += '_'
    runs = list(re.finditer(r'`+', content))
    following, matching = {}, {}
    for index in range(len(runs)-1, -1, -1):
        size = len(runs[index][0])
        matching[index] = following.get(size)
        following[size] = index
    pieces, literals, cursor, index = [], {}, 0, 0
    while index < len(runs):
        run = runs[index]
        previous = run.start()-1
        while previous >= 0 and content[previous] == '\\':
            previous -= 1
        end = matching[index]
        if (run.start()-previous-1) % 2 or end is None:
            index += 1
            continue
        key = prefix + str(len(literals)) + '\ue001'
        pieces.extend((content[cursor:run.start()], key))
        cursor = runs[end].end()
        literal = content[run.end():runs[end].start()].replace('\n', ' ')
        if literal.startswith(' ') and literal.endswith(' ') and literal.strip():
            literal = literal[1:-1]
        literals[key] = literal
        index = end+1
    pieces.append(content[cursor:])
    pattern = re.compile(re.escape(prefix) + r'\d+\ue001')
    def restore(value, *, remove=False):
        return pattern.sub(lambda match: '' if remove else literals[match[0]], value)
    return ''.join(pieces), restore


def article_markdown(content):
    """Project cleaned HTML while retaining headings, links and ownership stops.

    This is used only when no reviewed native record containers remain. It is
    not a competing parser whose more permissive count can override native DOM
    ownership. Navigation and explicit related-content landmarks are excluded.
    """
    from bs4 import Comment, NavigableString, Tag
    soup = article_html(content)
    for node in list(soup.select('nav, aside, footer, [role="navigation"], [role="complementary"]')):
        if node.parent is not None:
            node.decompose()
    def render(node):
        if isinstance(node, Comment):
            return ''
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
            longest = max((len(run) for run in re.findall(r'`+', literal)), default=0)
            fence = '`' * (longest + 1)
            return fence + ' ' + literal + ' ' + fence
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


def label_key(text, *, representation=None):
    """Normalize whole-label formatting identically for rules and observations."""
    return ' '.join(unicodedata.normalize('NFKC', text).split()).casefold().strip(
        ':：' if representation in {'plain_text', 'html'} else ':：*_').strip()


def article_field_text(content, restore_inline_code, *, representation=None, literal_blocks=None):
    """Render field formatting before restoring protected literal contents."""
    if representation != 'plain_text':
        from .stable_tasks import _visible
        escaped = {}
        prefix = '\ue000article-field-escape'
        while prefix in content:
            prefix += '_'
        def protect(match):
            marker = prefix + str(len(escaped)) + '\ue001'
            escaped[marker] = match[1]
            return marker
        content = re.sub(r'\\([!"#$%&\'()*+,\-./:;<=>?@\[\]\\^_`{|}~])', protect, content)
        content = _visible(content, normalized_html=True)
        for marker, literal in escaped.items():
            content = content.replace(marker, literal)
    content = restore_inline_code(content)
    for marker, literal in (literal_blocks or {}).items():
        content = content.replace(marker, literal)
    return content


def validate_labels(contract, fields=('ignored_labels', 'unavailable_notices')):
    """Require explicit task labels; an empty list is valid, an omission is not."""
    for field in fields:
        labels = contract.get(field)
        if (not isinstance(labels, list) or any(not isinstance(label, str)
                or not label_key(label, representation='plain_text') for label in labels)):
            raise ValueError('Invalid or missing task-declared ' + field)
        if len({label_key(label, representation='plain_text') for label in labels}) != len(labels):
            raise ValueError('Duplicate task-declared ' + field)


def declared_labels(contract, field):
    """Read the validated, explicit task list without runtime defaults."""
    return frozenset(label_key(label, representation='plain_text') for label in contract[field])
