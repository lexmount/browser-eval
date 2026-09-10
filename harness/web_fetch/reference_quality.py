"""Deterministic reference preparation; original source bytes remain evidence."""
import re
from dataclasses import dataclass


def clean_reference_text(text: str) -> str:
    # Explicit list, not all Unicode format characters: ZWJ/ZWNJ can carry meaning.
    return text.translate({0x200B:None, 0xFEFF:None, 0x00AD:None})


@dataclass(frozen=True)
class MarkdownLink:
    label: str
    destination: str
    is_image: bool
    start: int
    end: int


_MAX_MARKDOWN_LINK_SPAN = 8192


def _closing(text: str, start: int, opening: str, closing: str) -> int | None:
    depth = 1
    index = start + 1
    limit = min(len(text), start + _MAX_MARKDOWN_LINK_SPAN)
    while index < limit:
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == opening:
            depth += 1
        elif text[index] == closing:
            depth -= 1
            if not depth:
                return index
        index += 1
    return None


def markdown_links(text: str) -> list[MarkdownLink]:
    """Return explicit balanced Markdown links without guessing from bare URLs."""
    links: list[MarkdownLink] = []
    index = 0
    while index < len(text):
        image = text.startswith("![", index)
        start = index + 1 if image else index
        if text[start:start + 1] != "[":
            index += 1
            continue
        label_end = _closing(text, start, "[", "]")
        if label_end is None:
            index += 1
            continue
        destination_start = label_end + 1
        while destination_start < len(text) and text[destination_start].isspace():
            destination_start += 1
        if text[destination_start:destination_start + 1] != "(":
            index += 1
            continue
        destination_end = _closing(text, destination_start, "(", ")")
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
            titled = re.match(r"(?s)^(.*?)(?:\s+['\"](?:[^'\"]*)['\"])$", raw_destination)
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
    return ''.join(result)


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
