"""Deterministic reference preparation; original source bytes remain evidence."""
import re
import unicodedata
from dataclasses import dataclass


def clean_reference_text(text: str) -> str:
    # Explicit list, not all Unicode format characters: ZWJ/ZWNJ can carry meaning.
    return text.translate({0x200B:None, 0xFEFF:None, 0x00AD:None})


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


_MAX_MARKDOWN_LINK_SPAN = 8192


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
        image = text.startswith("![", index)
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
        links.append(MarkdownLink(raw_label, destination, image, index, destination_end + 1))
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
    if not re.search(
        r"</?(?:a|article|aside|b|blockquote|br|code|dd|div|dl|dt|em|footer|"
        r"h[1-6]|header|hr|i|li|main|nav|ol|p|pre|section|span|strong|table|"
        r"tbody|td|th|thead|tr|ul|script|style|template)\b[^>]*>",
        protected, re.I,
    ):
        return text
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
