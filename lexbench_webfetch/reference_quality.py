"""Deterministic reference preparation; original source bytes remain evidence."""
import re
import unicodedata
from dataclasses import dataclass
from html import unescape
from html.entities import html5 as _HTML_ENTITIES


# Cover the alphabets whose word boundaries the literal scorer enforces.
_CASE_CODEPOINTS = (*range(0x0250), *range(0x0370, 0x0530), *range(0x1E00, 0x2000),
                    *range(0x2DE0, 0x2E00), *range(0xA640, 0xA6A0))
_LOWER_LETTERS = re.escape(''.join(chr(n) for n in _CASE_CODEPOINTS if chr(n).islower()))
_UPPER_LETTERS = re.escape(''.join(chr(n) for n in _CASE_CODEPOINTS if chr(n).isupper()))
_CASE_BOUNDARY = re.compile(
    rf'(?<=[{_LOWER_LETTERS}])(?=[{_UPPER_LETTERS}])|'
    rf'(?<=[{_UPPER_LETTERS}])(?=[{_UPPER_LETTERS}][{_LOWER_LETTERS}])')


def separate_case_labels(text: str) -> str:
    """Restore cased label boundaries without splitting lowercase word fragments."""
    return _CASE_BOUNDARY.sub(' ', text)


HTML_BLOCK_ELEMENTS = frozenset({
    "address", "article", "aside", "blockquote", "caption", "colgroup", "dd",
    "details", "dialog", "div", "dl", "dt", "fieldset", "figcaption", "figure",
    "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr",
    "li", "main", "nav", "ol", "p", "pre", "section", "summary", "table",
    "tbody", "thead", "tfoot", "tr", "ul",
})


def clean_reference_text(text: str) -> str:
    # Explicit list, not all Unicode format characters: ZWJ/ZWNJ can carry meaning.
    return text.translate({0x200B:None, 0xFEFF:None, 0x00AD:None})



def markdown_body_content(text: str) -> str:
    """Exclude a declared leading YAML metadata mapping from page prose.

    Require both front-matter delimiters and a mapping key. Ordinary horizontal
    rules, fenced examples, and unclosed headers remain body content. The caller
    retains original answer bytes separately from this acceptance surface.
    """
    block = re.match(
        r"\A\ufeff?---[ \t]*\r?\n(?P<metadata>.*?)\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)",
        text, re.S,
    )
    if block is None:
        return text
    key = re.compile(r"^(?:[A-Za-z_][\w.:-]*|\"[^\"\n]+\"|'[^'\n]+')[ \t]*:")
    lines = block['metadata'].splitlines()
    if not any(key.match(line) for line in lines) or any(
        line.strip() and not line[0].isspace()
        and not line.startswith('#') and not key.match(line)
        for line in lines
    ):
        return text
    return text[block.end():]



def delivered_page_title(content: str, explicit_title: str | None = None) -> str:
    """Read a declared title field, never keywords or a description snippet."""
    if explicit_title and explicit_title.strip():
        return explicit_title.strip()
    if is_html_document_or_fragment(content):
        from bs4 import BeautifulSoup
        title = BeautifulSoup(content, "html.parser").find("title")
        return title.get_text(" ", strip=True) if title else ""
    body = markdown_body_content(content)
    if body == content:
        return ""
    header = content[:len(content)-len(body)]
    values = re.findall(r"(?m)^title:[ \t]*([^\r\n]*)$", header)
    if len(values) != 1:
        return ""
    value = values[0].strip()
    if value.startswith('"'):
        import json
        try:
            parsed = json.loads(value)
        except ValueError:
            return ""
        return parsed if isinstance(parsed, str) else ""
    if value.startswith("'"):
        return value[1:-1].replace("''", "'") if value.endswith("'") else ""
    if not value or value[0] in "[{|>" or value.casefold() in {"null", "true", "false", "~"}:
        return ""
    return value.split(" #", 1)[0].strip()


def markdown_heading_text(text: str) -> str:
    """Remove ATX presentation markers while retaining visible heading words."""
    output = []
    fence = None
    for line in text.splitlines(keepends=True):
        opening = re.match(r"^[ ]{0,3}(`{3,}|~{3,})", line)
        if fence is not None:
            output.append(line)
            if re.match(r"^[ ]{0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*(?:\r?\n|$)", line):
                fence = None
            continue
        if opening:
            fence = opening[1]
            output.append(line)
            continue
        heading = re.match(r"^[ ]{0,3}#{1,6}(?:[ \t]+|(?=\r?$))(.*)", line)
        if heading:
            suffix = '\r\n' if line.endswith('\r\n') else '\n' if line.endswith('\n') else ''
            title = re.sub(r"[ \t]+#+[ \t]*$", "", heading[1].rstrip('\r\n'))
            output.append(title + suffix)
        else:
            output.append(line)
    return ''.join(output)


def normalize_reference_anchor(text: str) -> str:
    """Normalize capture layout while retaining evidence-bearing punctuation.

    The ordinary scorer deliberately treats most punctuation as presentation.
    Reference promotion has a different job: an anchor must be transcribed from
    the independently captured scope.  In particular, dropping a comma must not
    be hidden by the scorer's punctuation-insensitive normalization.
    """
    value = unicodedata.normalize("NFKC", clean_reference_text(text)).casefold()
    value = re.sub(r"\s+", " ", value).strip()
    return re.sub(r"\s*([,.;:!?])\s*", r"\1", value)


def reference_anchor_in_text(reference: str, anchor: str) -> bool:
    expected = normalize_reference_anchor(anchor)
    return bool(expected and expected in normalize_reference_anchor(reference))


def is_html_document_or_fragment(text: str) -> bool:
    """Return whether an answer starts with a block HTML document surface.

    A Markdown answer may retain inline tags inside link labels or prose.  Only
    a leading document or block element selects the HTML projection path.
    """
    elements = "|".join(sorted(HTML_BLOCK_ELEMENTS | {"html", "body"}))
    return bool(re.match(
        rf"\s*(?:(?:<!--.*?-->|<!doctype\s+html\b[^>]*>)\s*)*<(?:{elements})\b",
        text,
        re.I | re.S,
    ))


_BLOCKED_PAGE = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:[*_]{1,3})?(?:access denied|403 forbidden|"
    r"error:\s*forbidden\s*/\s*403|verify (?:that )?you are human|"
    r"checking your browser|(?:w3\s+)?performing security verification|making sure you're not a bot|"
    r"testing to determine if you are a bot|checking (?:if )?you are not a bot|"
    r"verifying visitor request|pardon our interruption|(?:opensuse\s+)?request on hold|"
    r"this is a measure to protect this website from bots|"
    r"just a moment(?:\.{3}|…)?|attention required!?|captcha(?: challenge)?|"
    r"京东验证|验证中心|身份核实|验证一下\s*[,，]?\s*购物无忧)"
    r"(?:[*_]{1,3})?[.!: 。！：\t]*$"
)


def blocked_page_hits(text: str, *, representation=None) -> list[str]:
    """Return exact standalone challenge headings from any answer surface."""
    # Markdown serializers commonly place a decorative challenge-page image at
    # the start of the same source line as the visible gate phrase. The image is
    # not visible prose and must not prevent the phrase from being standalone.
    visible = text if representation == 'plain_text' else visible_markdown_links(text)
    return [match.group(0).strip() for match in _BLOCKED_PAGE.finditer(visible)]


_SCRIPT_SHELL_LINE = re.compile(
    r"(?m)^\s*(?:window(?:\.[A-Za-z_$][\w$]*)+\s*=|"
    r"(?:var|let|const)\s+[A-Za-z_$][\w$]*\s*=|"
    r"function\s+[A-Za-z_$][\w$]*\s*\(|//\# sourceURL=)"
)


def script_payload_shell(text: str) -> bool:
    """Detect a minified script/state dump presented as page body text.

    This deliberately requires several independent structural signals.  A
    normal article containing JavaScript examples must remain valid; a huge
    serialized application state or bundle with a title above it is not the
    readable body requested by a text task, even if target phrases occur
    inside quoted state.
    """
    if len(text) < 1_000:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    longest_ratio = max(map(len, lines)) / len(text)
    short_prose_ratio = sum(len(line) for line in lines if len(line) < 1_000) / len(text)
    return (len(_SCRIPT_SHELL_LINE.findall(text)) >= 10
            and longest_ratio >= 0.5
            and short_prose_ratio < 0.15)


@dataclass(frozen=True)
class MarkdownLink:
    label: str
    destination: str
    is_image: bool
    start: int
    end: int
    title: str = ""


_MAX_MARKDOWN_LINK_SPAN = 8192


def _markdown_unescape(value):
    """Decode source escapes/references once, without rescanning their output."""
    def decode(match):
        if match[1] is not None:
            return match[1]
        reference = match[0]
        return unescape(reference) if reference.startswith('&#') or reference[1:] in _HTML_ENTITIES else reference
    return re.sub(
        r'''\\([!"#$%&'()*+,\-./:;<=>?@\[\]\\^_`{|}~])|&(?:\#[xX][0-9a-fA-F]{1,6}|\#\d{1,7}|[A-Za-z][A-Za-z0-9]*);''',
        decode, value)


def is_markdown_image_start(text: str, index: int) -> bool:
    """Return whether ``![`` starts image syntax at *index*.

    CommonMark punctuation escapes are parity-sensitive: an odd number of
    immediately preceding backslashes escapes the bang, while an even number
    leaves it active after the backslashes are paired.
    """
    if not text.startswith("![", index):
        return False
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 0


def markdown_delimiter_pairs(text, *, maximum_span=None):
    """Index escaped, nested Markdown delimiters once, including malformed prose."""
    pairs = {}
    stacks = {'[': [], '(': []}
    openings = {']': '[', ')': '('}
    label_ends, destinations = set(), set()
    titles = {'"': re.compile(r'"(?:\\.|[^"\\])*"\s*\)'),
              "'": re.compile(r"'(?:\\.|[^'\\])*'\s*\)")}
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if (char in titles and stacks['('] and stacks['('][-1] in destinations
                and i > 0 and text[i - 1].isspace()):
            start = stacks['('][-1]
            title = titles[char].match(text, i, len(text) if maximum_span is None
                                       else min(len(text), start + maximum_span))
            if title is not None:
                stacks['('].pop()
                pairs[start] = title.end() - 1
                i = title.end()
                continue
        if char in stacks:
            if char == '(':
                previous = i - 1
                while previous >= 0 and text[previous].isspace():
                    previous -= 1
                if previous in label_ends:
                    destinations.add(i)
            stacks[char].append(i)
        elif char in openings:
            opening = openings[char]
            if stacks[opening]:
                start = stacks[opening].pop()
                if maximum_span is None or i - start < maximum_span:
                    pairs[start] = i
                    if char == ']':
                        label_ends.add(i)
        i += 1
    return pairs


def markdown_links(text: str) -> list[MarkdownLink]:
    """Return explicit balanced Markdown links without guessing from bare URLs."""
    links: list[MarkdownLink] = []
    # Attribute values are HTML metadata, even when they contain balanced
    # Markdown syntax. Keep tag bytes intact for the HTML-specific readers.
    tags = [match.span() for match in re.finditer(
        r'''</?[A-Za-z][\w:-]*(?=\s|/?>)(?:[^>"']|"[^"]*"|'[^']*')*>''', text)]
    pieces, cursor = [], 0
    for left, right in tags:
        pieces.extend([text[cursor:left], ' ' * (right - left)])
        cursor = right
    pieces.append(text[cursor:])
    pairs = markdown_delimiter_pairs(''.join(pieces), maximum_span=_MAX_MARKDOWN_LINK_SPAN)
    tag_index = 0
    index = 0
    while index < len(text):
        while tag_index < len(tags) and tags[tag_index][1] <= index:
            tag_index += 1
        if tag_index < len(tags) and tags[tag_index][0] <= index < tags[tag_index][1]:
            index = tags[tag_index][1]
            continue
        image = is_markdown_image_start(text, index)
        start = index + 1 if image else index
        if text[start:start + 1] != "[":
            index += 1
            continue
        label_end = pairs.get(start)
        if label_end is None:
            index += 1
            continue
        destination_start = label_end + 1
        while destination_start < len(text) and text[destination_start].isspace():
            destination_start += 1
        if text[destination_start:destination_start + 1] != "(":
            index += 1
            continue
        destination_end = pairs.get(destination_start)
        if destination_end is None:
            index += 1
            continue
        raw_label = text[start + 1:label_end]
        raw_destination = text[destination_start + 1:destination_end].strip()
        native_title = ""
        if raw_destination.startswith("<") and ">" in raw_destination:
            close = raw_destination.index(">")
            destination = raw_destination[1:close]
            titled = re.fullmatch(r"\s+(['\"])(.*)\1", raw_destination[close + 1:], re.S)
            native_title = titled[2] if titled else ""
        else:
            # A quoted Markdown title is metadata, not part of the URL. Layout
            # whitespace inside a destination is otherwise serialization noise.
            # The opening and closing title delimiters must agree. Quotes
            # inside metadata do not change the explicit link destination;
            # serializers may also fail to escape a repeated delimiter.
            titled = re.match(r"(?s)^(.*?)\s+(['\"])(.*)\2$", raw_destination)
            destination = re.sub(r"\s+", "", titled[1] if titled else raw_destination)
            native_title = titled[3] if titled else ""
        # Character references in a Markdown destination are rendered URL
        # syntax. Decode once here; bare URL answers retain their literal bytes.
        links.append(MarkdownLink(raw_label, _markdown_unescape(destination), image, index,
                                  destination_end + 1, _markdown_unescape(native_title)))
        index = destination_end + 1
    return links


def markdown_link_destinations(text: str) -> list[str]:
    """Return destinations from outer links and nested linked media.

    Markdown commonly represents a linked image as ``[![alt](image)](page)``.
    Visible-text parsing treats that as one outer span, while resource
    discovery must retain both independently returned destinations.
    """
    destinations: list[str] = []
    pending = [text]
    while pending:
        for link in markdown_links(pending.pop()):
            destinations.append(link.destination)
            if '[' in link.label:
                pending.append(link.label)
    return destinations


def without_markdown_images(content):
    """Remove image metadata, including images nested in ordinary links."""
    literals = markdown_literal_spans(content)
    pieces, cursor = [], 0
    for link in markdown_links(content):
        if link.start < cursor or any(left <= link.start < right for left, right in literals):
            continue
        pieces.append(content[cursor:link.start])
        if not link.is_image:
            original = content[link.start:link.end]
            pieces.append(original.replace(link.label, without_markdown_images(link.label), 1))
        cursor = link.end
    pieces.append(content[cursor:])
    return ''.join(pieces)


def _same_inline_block(text, start, end):
    """Inline delimiters cannot pair across a paragraph or block boundary."""
    newline = text.find('\n', start, end)
    if newline < 0:
        return True
    closing_line_end = text.find('\n', end)
    opening_prefix = text[text.rfind('\n', 0, start)+1:start]
    quote = re.match(r'^ {0,3}((?:>[ \t]?)+)', opening_prefix)
    quote_depth = quote[1].count('>') if quote else 0
    list_context = re.match(r'^ {0,3}(?:>[ \t]?)*(?:[-+*]|\d+[.)])[ \t]+', opening_prefix)
    ordered = r'\d+' if list_context else '1'
    for line in text[newline+1:closing_line_end if closing_line_end >= 0 else len(text)].split('\n'):
        if quote_depth:
            line = re.sub(r'^ {0,3}(?:>[ \t]?){1,' + str(quote_depth) + '}', '', line, count=1)
        fence = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if (not line.strip()
                or re.match(r'^ {0,3}(?:#{1,6}(?:[ \t]|$)|>)', line)
                or re.match(r'^ {0,3}(?:[-+*]|' + ordered + r'[.)])[ \t]+\S', line)
                or re.fullmatch(r' {0,3}(?:=+|-+|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,}|(?:-[ \t]*){3,})[ \t]*', line)
                or (fence and not (fence[1][0] == '`' and '`' in fence[2]))
                or (not line.startswith(('    ', '\t')) and is_html_document_or_fragment(line))):
            return False
    return True


def markdown_inline_code_spans(text, *, excluded_spans=()):
    """Locate code spans within a single inline-content block."""
    spans = []
    runs = list(re.finditer(r'`+', text))
    consumed = 0
    for index, run in enumerate(runs):
        if run.start() < consumed or any(left <= run.start() < right for left, right in excluded_spans):
            continue
        escape = run.start()
        while escape and text[escape - 1] == '\\':
            escape -= 1
        if (run.start() - escape) % 2:
            continue
        closing = next((other for other in runs[index + 1:]
                        if len(other[0]) == len(run[0])
                        and not any(run.end() <= left <= other.start() for left, _ in excluded_spans)), None)
        if closing is not None and _same_inline_block(text, run.start(), closing.start()):
            spans.append((run.start(), closing.end()))
            consumed = closing.end()
    return spans


def _markdown_fenced_code_spans(text):
    spans = []
    fence = None
    offset = 0
    for line in text.splitlines(keepends=True):
        if fence is None:
            opening = re.match(r'^ {0,3}(`{3,}|~{3,})([^\r\n]*)', line)
            if opening and not (opening[1][0] == '`' and '`' in opening[2]):
                fence = (offset, opening[1])
        elif re.fullmatch(r' {0,3}' + re.escape(fence[1][0])
                          + '{' + str(len(fence[1])) + r',}[ \t]*(?:\r?\n)?', line):
            spans.append((fence[0], offset + len(line)))
            fence = None
        offset += len(line)
    if fence is not None:
        spans.append((fence[0], len(text)))
    return spans


def _markdown_indented_code_blocks(text, *, excluded_spans=None):
    """Locate indented blocks, respecting paragraph and list content ownership."""
    # Native HTML indentation is serialization layout. Its pre/code elements
    # are handled by HTML readers, rather than interpreted as Markdown blocks.
    if is_html_document_or_fragment(text):
        return []
    if excluded_spans is None:
        excluded_spans = _markdown_fenced_code_spans(text)
    spans, list_indents = [], []
    paragraph, previous_depth, empty_item = False, 0, False
    pending = None
    offset = 0
    def payload(line, base, raw):
        indentation = len(line) - len(line.lstrip(' '))
        return line[min(indentation, base + 4):] + ('\n' if raw.endswith(('\n', '\r')) else '')
    def finish(block):
        while block[2] and not block[2][-1].strip():
            block[2].pop()
        return block[0], block[1], ''.join(block[2])
    for raw in text.splitlines(keepends=True):
        end = offset + len(raw)
        line = raw.expandtabs(4).rstrip('\r\n')
        quote = re.match(r'^ {0,3}((?:> ?)+)', line)
        depth = quote[1].count('>') if quote else 0
        if depth != previous_depth and not (depth < previous_depth and paragraph):
            paragraph, list_indents = False, []
        previous_depth = depth
        if quote:
            line = line[quote.end():]
        blank = not line.strip()
        excluded = any(left <= offset < right for left, right in excluded_spans)
        indent = len(line) - len(line.lstrip(' '))
        list_context = bool(list_indents)
        if not blank:
            while list_indents and indent < list_indents[-1]:
                list_indents.pop()
        base = list_indents[-1] if list_indents else 0
        indented = indent >= base + 4 and not paragraph and not excluded
        if pending is not None:
            if blank or indented:
                pending[1] = end
                pending[2].append(payload(line, base, raw))
                offset = end
                continue
            spans.append(finish(pending))
            pending = None
        if blank or excluded:
            if blank and empty_item and list_indents:
                list_indents.pop()
            empty_item = False
            paragraph = False
        elif indented:
            pending = [offset, end, [payload(line, base, raw)]]
        else:
            relative = line[base:]
            block = (re.match(r'^ {0,3}#{1,6}(?:\s|$)', relative)
                     or (paragraph and re.fullmatch(r' {0,3}(?:=+|-+)[ \t]*', relative))
                     or re.fullmatch(r' {0,3}(?:(?:\*[ \t]*){3,}|(?:_[ \t]*){3,}|(?:-[ \t]*){3,})', relative)
                     or is_html_document_or_fragment(relative))
            marker = re.match(r'^( *)([-+*]|\d+[.)])( +|$)(.*)$', line)
            if (marker and not block and indent < base + 4
                    and (not paragraph or list_context or marker[2] in '-+*' or marker[2] in ('1.', '1)'))):
                padding = len(marker[3]) or 1
                list_indents.append(marker.start(3) + (padding if padding <= 4 else 1))
                empty_item = not bool(marker[4].strip())
                if padding > 4 and marker[4].strip():
                    code_base = list_indents[-1]
                    pending = [offset, end, [line[code_base+4:] + ('\n' if raw.endswith(('\n', '\r')) else '')]]
                    paragraph = False
                else:
                    paragraph = bool(marker[4].strip()) and not re.match(r'#{1,6}(?:\s|$)', marker[4])
            else:
                empty_item = False
                paragraph = not bool(block)
        offset = end
    if pending is not None:
        spans.append(finish(pending))
    return spans


def markdown_indented_code_spans(text, *, excluded_spans=None):
    """Return indented literal regions from the shared block-content owner."""
    return [(start, end) for start, end, _ in _markdown_indented_code_blocks(text, excluded_spans=excluded_spans)]


def markdown_indented_code_contents(text):
    """Return displayed code with container syntax and code indentation removed."""
    return {(start, end): content for start, end, content in _markdown_indented_code_blocks(text)}


def normalize_native_markdown_layout(text):
    """Restore supported serializer card indentation in an inferred answer view.

    Explicit Markdown must retain literal-block semantics. Callers select this
    view only for undeclared/native serialization, never for declared Markdown.
    A card needs unindented body fields; fully indented examples stay literal.
    """
    if (not re.search(r'(?m)^[ \t]+(?:[^\n]*#{1,6}[ \t]+|[-+*][ \t]+\[!\[)', text)
            or is_html_document_or_fragment(text)):
        return text
    from .composite_tasks import _DATE_OR_TIME
    lines = text.splitlines(keepends=True)
    fences = _markdown_fenced_code_spans(text)
    offsets, offset = [], 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)
    def following(index):
        return next((i for i in range(index + 1, len(lines)) if lines[i].strip()), None)
    def outside_body(index):
        if index is None:
            return False
        line = lines[index].expandtabs(4)
        body = line.lstrip(' ')
        return (len(line) - len(body) < 4 and bool(body.strip())
                and not re.match(r'(?:#{1,6}(?:\s|$)|>|[-+*](?:\s|$)|\d+[.)]\s|`{3,}|~{3,}|!?\[)', body))
    for index, raw in enumerate(lines):
        if (len(raw.expandtabs(4)) - len(raw.expandtabs(4).lstrip(' ')) < 4
                or any(start <= offsets[index] < end for start, end in fences)):
            continue
        line = raw.strip()
        links = markdown_links(line)
        if len(links) != 1 or links[0].is_image or line[links[0].end:].strip():
            continue
        link = links[0]
        prefix = line[:link.start].strip()
        heading = re.fullmatch(r'(.+?)\s+#{1,6}', prefix)
        if heading and _DATE_OR_TIME.fullmatch(heading[1]) and outside_body(following(index)):
            lines[index] = raw.lstrip(' \t')
            continue
        images = markdown_links(link.label)
        if (prefix not in {'-', '+', '*'} or len(images) != 1 or not images[0].is_image
                or images[0].start != 0 or images[0].end != len(link.label)):
            continue
        title_index = following(index)
        if title_index is None:
            continue
        title = lines[title_index].strip()
        titles = markdown_links(title)
        if (len(titles) == 1 and not titles[0].is_image and titles[0].start == 0
                and titles[0].end == len(title) and titles[0].destination == link.destination
                and outside_body(following(title_index))):
            lines[index] = raw.lstrip(' \t')
            lines[title_index] = '  ' + lines[title_index].lstrip(' \t')
    return ''.join(lines)


def _markdown_html_block_spans(text, *, excluded_spans=()):
    """Backticks owned by native block elements are ordinary HTML text."""
    elements = "|".join(sorted(HTML_BLOCK_ELEMENTS | {'html', 'body'}))
    starts = [match.start('tag') for match in re.finditer(
        rf'(?im)^ {{0,3}}(?P<tag><(?:{elements})(?=[\s/>]))', text)
        if not any(left <= match.start('tag') < right for left, right in excluded_spans)]
    if not starts:
        return []
    from bs4.builder import HTMLTreeBuilder
    from .dynamic_lists import _HTMLRanges

    parser = _HTMLRanges(text, HTMLTreeBuilder.DEFAULT_EMPTY_ELEMENT_TAGS)
    parser.feed(text)
    parser.close()
    return [(start, parser.ranges[start]) for start in starts if start in parser.ranges]


def markdown_literal_spans(text: str) -> list[tuple[int, int]]:
    """Locate block and inline literals before interpreting Markdown syntax."""
    spans = _markdown_fenced_code_spans(text)
    spans += markdown_indented_code_spans(text, excluded_spans=spans)
    html_blocks = _markdown_html_block_spans(text, excluded_spans=spans)
    return sorted(spans + markdown_inline_code_spans(text, excluded_spans=spans + html_blocks))


def visible_markdown_links(text: str, *, include_image_alt: bool = False) -> str:
    """Keep labels and adjacent link boundaries, consuming balanced destinations.

    Browser Markdown serializers sometimes wrap a generated destination or its
    title onto the next line.  The visible label is still page text, so layout
    newlines inside a bounded, balanced destination must not turn it into a
    false negative.
    """
    literals = markdown_literal_spans(text)
    result=[];start=0;previous_label=''
    separated = lambda label: label.startswith(('/', '#')) or any(char.isspace() for char in label)
    for match in markdown_links(text):
        if match.start < start:
            continue
        literal = any(left <= match.start < right for left, right in literals)
        if literal:
            continue
        label = ('' if match.is_image and not include_image_alt else
                 re.sub(r"\s+", " ", visible_markdown_links(
                     match.label, include_image_alt=include_image_alt)).strip())
        # A native carousel can append its accessibility position directly
        # to the label. Keep both values but restore their observable boundary.
        label = re.sub(r'(?<=[^\W\d_])(?=\d+\s+of\s+\d+$)', ' ', label)
        # Distinct adjacent link labels retain a token boundary. Some native
        # serializers also drop a real DOM text boundary immediately before a
        # path link or a multi-word link label (``out[/r/x]`` / ``or[Stack
        # Overflow]``). Restore only those unambiguous boundaries; ordinary
        # links embedded in a word (``pre[fix]`` / ``[read]able``) stay joined.
        gap = text[start:match.start]
        if match.start == start and previous_label and label:
            # A numeric value may span links: never turn -10, 10.5 or 100
            # into a separately matchable positive 10 by adding whitespace.
            numeric = lambda char: char.isdigit() or char in '+-.−'
            gap='' if numeric(previous_label[-1]) and numeric(label[0]) else ' '
        else:
            if (previous_label and gap and not gap[0].isspace()
                    and separated(previous_label) and gap[0].isalnum()):
                gap=' '+gap
            if (gap and label and not gap[-1].isspace()
                    and separated(label) and (gap[-1].isalnum() or gap[-1] in ',.;:!?')):
                gap+=' '
        result.extend([gap, label])
        previous_label=label
        start=match.end
    suffix = text[start:]
    if previous_label and suffix and separated(previous_label) and suffix[0].isalnum():
        suffix = ' ' + suffix
    result.append(suffix)
    visible = ''.join(result)
    # Some browser Markdown serializers use angle-autolink spelling for a
    # root-relative path even though CommonMark formally reserves autolinks
    # for absolute URIs and email addresses.  It is still visible page text:
    # ``</docs/start>`` renders as ``/docs/start``.  Require a second path
    # segment so raw HTML closing tags such as ``</div>`` remain markup rather
    # than becoming evidence-bearing prose.
    relative_autolink = re.compile(r"<((?:/[^<>\s/]+){2,}(?:[?#][^<>\s]*)?)>")
    if not relative_autolink.search(visible):
        return visible
    visible_literals = markdown_literal_spans(visible)
    return relative_autolink.sub(
        lambda match: match[0] if any(left <= match.start() < right for left, right in visible_literals) else match[1],
        visible,
    )


def visible_mixed_html_text(text: str, *, excluded_html_selectors=()) -> str:
    """Project raw HTML in a Markdown answer, preserving literal code and autolinks."""
    from .dynamic_lists import visible_html_text

    literals = []
    marker = "\ue010"
    while marker in unescape(text):
        marker += "\ue010"

    def protect_text(value):
        literals.append(value)
        return f"{marker}{len(literals) - 1}{marker}"

    def protect(match):
        return protect_text(match.group(0))

    # Preserve the existing native table-cell fence spelling, which is not a
    # CommonMark block opener. Only a complete paired block is literal here.
    protected = re.sub(
        r"(?ms)^ {0,3}\|[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^ {0,3}\1[ \t]*$",
        protect, text)
    pieces, cursor = [], 0
    for left, right in markdown_literal_spans(protected):
        pieces.extend([protected[cursor:left], protect_text(protected[left:right])])
        cursor = right
    pieces.append(protected[cursor:])
    protected = ''.join(pieces)
    # Code examples and Markdown-escaped brackets are answer text.
    # Consume escaped backslashes as pairs so an unescaped following tag
    # remains HTML rather than being protected by its second backslash.
    protected = re.sub(
        r"<(?:[A-Za-z][A-Za-z0-9+.-]{1,31}:|/[^<>\s/]+/)[^<>\s]+>"
        r"|<[^<>\s]+@[^<>\s]+>"
        # Some Markdown serializers escape only the closing angle bracket of
        # a literal HTML tag (``<td\>``). Protect the complete token so the
        # unescaped opening bracket cannot turn a displayed code sample into
        # live HTML. A wholly unescaped tag remains markup and is projected.
        r"|(?:\\</?[A-Za-z][^<>\n]*>|</?[A-Za-z][^<>\n]*\\>)|\\[\\<>]",
        protect, protected,
    )
    protected = re.sub(r'<!--.*?(?:-->|\Z)', '', protected, flags=re.S)
    # Extracted documentation can name a raw-text element in ordinary prose
    # without escaping its angle brackets. Recognize that explicit grammatical
    # mention only when it has no matching closing tag; real raw-text blocks
    # (including incomplete blocks without this prose cue) remain hidden.
    def element_mention(match):
        from bs4 import BeautifulSoup
        sentinel = marker + 'element-context' + marker
        prefix = BeautifulSoup(protected[:match.start()] + sentinel, 'html.parser')
        context = prefix.find(string=lambda value: sentinel in value)
        html_owners = HTML_BLOCK_ELEMENTS | {
            'html', 'body', 'a', 'b', 'code', 'em', 'i', 'span', 'strong',
            'script', 'style', 'template'}
        if context is None or any(parent.name in html_owners for parent in context.parents):
            return match[0]
        if re.search(r'</' + match['element'] + r'\s*>', protected[match.end():], re.I):
            return match[0]
        return protect(match)

    protected = re.sub(
        r'\b(?:the|an?) +<(?P<element>script|style)> +(?:element|tag)\b',
        element_mention, protected, flags=re.I)
    if excluded_html_selectors:
        from bs4 import BeautifulSoup
        surface = BeautifulSoup(protected, 'html.parser')
        for selector in excluded_html_selectors:
            for node in list(surface.select(selector)):
                if node.parent is not None:
                    node.decompose()
        protected = str(surface)
    if not re.search(
        r"</?[A-Za-z][^<>]*>",
        protected, re.I,
    ):
        projected = unescape(protected)
        for index, literal in reversed(list(enumerate(literals))):
            projected = projected.replace(f"{marker}{index}{marker}", literal)
        return projected
    projected = visible_html_text(protected, preserve_source_whitespace=True)
    for index, literal in reversed(list(enumerate(literals))):
        projected = projected.replace(f"{marker}{index}{marker}", literal)
    return projected


def audit_reference(text, facts, *, normalize):
    issues=[]
    if not text.strip(): issues.append('empty_reference')
    if '\ufffd' in text: issues.append('replacement_character')
    if re.search(r'此处是\{\{icon_[a-z_]+\}\}icon',text): issues.append('unresolved_renderer_placeholder')
    if re.search(r'[\x00-\x08\x0b\x0e-\x1f\x7f]',text): issues.append('unexpected_control_character')
    if clean_reference_text(text)!=text: issues.append('layout_characters')
    seen=set();folded=normalize(text)
    for fact in facts:
        alternatives={normalize(a) for a in fact.get('alternatives',[])}-{''}
        if not alternatives:issues.append('empty_fact:'+fact['id'])
        elif not any(a in folded for a in alternatives):issues.append('fact_absent_from_reference:'+fact['id'])
        key=tuple(sorted(alternatives))
        # Repeated values across separately owned structured records are valid
        # (for example two products with the same price or two GET operations).
        if key in seen and not re.fullmatch(r'entry-[1-9][0-9]*-[a-z]+', str(fact.get('id', ''))):
            issues.append('duplicate_fact:'+fact['id'])
        seen.add(key)
    return issues


def bind_annotation_evidence(case, source):
    """Validate literal groups against their independent evidence before scoring."""
    import hashlib
    from lexbench_webfetch.metrics import fact_in_text, normalize_for_match
    from lexbench_webfetch.paths import contained_path
    annotation = case.get('source_benchmark', {}).get('stable_annotation')
    if not annotation or not annotation.get('anchor_evidence'):
        return
    raw = contained_path(source, annotation['anchor_evidence']).read_bytes()
    reference = normalize_for_match(raw.decode('utf-8'))
    for anchor in case['acceptance_contract'].get('stable_text_anchors', []):
        if not any(fact_in_text(reference, normalize_for_match(value)) for value in anchor['alternatives']):
            raise ValueError('Stable text lacks independent evidence: ' + case['id'] + '/' + anchor['id'])
    annotation['anchor_evidence_sha256'] = hashlib.sha256(raw).hexdigest()

