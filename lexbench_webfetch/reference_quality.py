"""Deterministic reference preparation; original source bytes remain evidence."""
import re
import unicodedata
from dataclasses import dataclass
from html import unescape


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


def blocked_page_hits(text: str) -> list[str]:
    """Return exact standalone challenge headings from any answer surface."""
    # Markdown serializers commonly place a decorative challenge-page image at
    # the start of the same source line as the visible gate phrase. The image is
    # not visible prose and must not prevent the phrase from being standalone.
    visible = visible_markdown_links(text)
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
    escaped_starts = {'[': {}, '(': {}}
    openings = {']': '[', ')': '('}
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\":
            # Existing callers may begin at an escaped opening delimiter.
            # Such an opening does not nest inside another candidate, but it
            # shares that depth's next close when parsed as the link start.
            if i + 1 < len(text) and text[i + 1] in stacks:
                opening = text[i + 1]
                escaped_starts[opening].setdefault(len(stacks[opening]), []).append(i + 1)
            i += 2
            continue
        if char in stacks:
            stacks[char].append(i)
        elif char in openings:
            opening = openings[char]
            starts = escaped_starts[opening].pop(len(stacks[opening]), [])
            if stacks[opening]:
                starts.append(stacks[opening].pop())
            for start in starts:
                if maximum_span is None or i - start < maximum_span:
                    pairs[start] = i
        i += 1
    return pairs


def markdown_links(text: str) -> list[MarkdownLink]:
    """Return explicit balanced Markdown links without guessing from bare URLs."""
    pairs = markdown_delimiter_pairs(text, maximum_span=_MAX_MARKDOWN_LINK_SPAN)
    links: list[MarkdownLink] = []
    index = 0
    while index < len(text):
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
            destination = raw_destination[1:raw_destination.index(">")]
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
        links.append(MarkdownLink(raw_label, unescape(destination), image, index, destination_end + 1, native_title))
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


def visible_markdown_links(text: str) -> str:
    """Keep labels and adjacent link boundaries, consuming balanced destinations.

    Browser Markdown serializers sometimes wrap a generated destination or its
    title onto the next line.  The visible label is still page text, so layout
    newlines inside a bounded, balanced destination must not turn it into a
    false negative.
    """
    result=[];start=0;previous_label=''
    for match in markdown_links(text):
        if match.start < start:
            continue
        label = re.sub(r"\s+", " ", visible_markdown_links(match.label)).strip()
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
            separated = lambda label: label.startswith(('/', '#')) or any(char.isspace() for char in label)
            if (previous_label and gap and not gap[0].isspace()
                    and separated(previous_label) and gap[0].isalnum()):
                gap=' '+gap
            if (gap and label and not gap[-1].isspace()
                    and separated(label) and (gap[-1].isalnum() or gap[-1] in ',.;:!?')):
                gap+=' '
        result.extend([gap, label])
        previous_label=label
        start=match.end
    result.append(text[start:])
    visible = ''.join(result)
    # Some browser Markdown serializers use angle-autolink spelling for a
    # root-relative path even though CommonMark formally reserves autolinks
    # for absolute URIs and email addresses.  It is still visible page text:
    # ``</docs/start>`` renders as ``/docs/start``.  Require a second path
    # segment so raw HTML closing tags such as ``</div>`` remain markup rather
    # than becoming evidence-bearing prose.
    return re.sub(r"<((?:/[^<>\s/]+){2,}(?:[?#][^<>\s]*)?)>", r"\1", visible)


def visible_mixed_html_text(text: str) -> str:
    """Project raw HTML in a Markdown answer, preserving literal code and autolinks."""
    from .dynamic_lists import visible_html_text

    literals = []
    marker = "\ue010"
    while marker in text:
        marker += "\ue010"

    def protect(match):
        literals.append(match.group(0))
        return f"{marker}{len(literals) - 1}{marker}"

    # Native Markdown can serialize multiline code inside table cells.
    # Recognize their opening fence before a closing fence is mistaken for one.
    # Code examples and Markdown-escaped brackets are answer text.
    # Consume escaped backslashes as pairs so an unescaped following tag
    # remains HTML rather than being protected by its second backslash.
    protected = re.sub(
        r"(?ms)^ {0,3}(?:\|[ \t]*)?(`{3,}|~{3,})[^\n]*\n.*?^ {0,3}\1[ \t]*$"
        r"|`+[^`\n]+`+|<(?:https?://|mailto:|/[^<>\s/]+/)[^<>\s]+>"
        r"|<[^<>\s]+@[^<>\s]+>"
        # Some Markdown serializers escape only the closing angle bracket of
        # a literal HTML tag (``<td\>``). Protect the complete token so the
        # unescaped opening bracket cannot turn a displayed code sample into
        # live HTML. A wholly unescaped tag remains markup and is projected.
        r"|(?:\\</?[A-Za-z][^<>\n]*>|</?[A-Za-z][^<>\n]*\\>)|\\[\\<>]",
        protect, text,
    )
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
    if not re.search(
        r"</?(?:a|article|aside|b|blockquote|br|code|dd|div|dl|dt|em|footer|"
        r"h[1-6]|header|hr|i|li|main|nav|ol|p|pre|section|span|strong|table|"
        r"tbody|td|th|thead|tr|ul|script|style|template)\b[^>]*>",
        protected, re.I,
    ):
        projected = unescape(protected)
        for index, literal in enumerate(literals):
            projected = projected.replace(f"{marker}{index}{marker}", literal)
        return projected
    projected = visible_html_text(protected, preserve_source_whitespace=True)
    for index, literal in enumerate(literals):
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

