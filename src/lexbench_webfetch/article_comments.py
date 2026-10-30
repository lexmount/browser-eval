"""Check fixed initial comment coverage and explicit record ownership."""
from __future__ import annotations

import re

import soupsieve

from .article_text import (author_key, article_html, is_article_html,
                           article_markdown, TIMESTAMP_LINE, mask_markdown_code, mask_markdown_inline_code, label_key,
                           validate_labels, declared_labels, article_field_text)
from .body_coverage import tokens
from .dynamic_lists import visible_html_text
from .reference_quality import markdown_links, visible_markdown_links, without_markdown_images


def validate_comment_contract(contract):
    """Validate the current fixed comment coverage contract."""
    if not isinstance(contract, dict) or type(contract.get("version")) is not int or contract["version"] != 2:
        raise ValueError("only fixed comment coverage version 2 is supported")
    allowed = {'version', 'minimum_comments', 'record_selector', 'author_selector',
               'body_selector', 'section_markers', 'id_attribute', 'text_scope',
               'ignored_labels', 'unavailable_notices'}
    if set(contract) - allowed or type(contract.get('minimum_comments')) is not int or contract['minimum_comments'] < 1:
        raise ValueError('comment coverage requires a positive minimum')
    validate_labels(contract)
    scope = contract.get('text_scope')
    if scope is not None:
        if (not isinstance(scope, dict) or set(scope) != {'body_end', 'end_markers'}
                or not isinstance(scope['body_end'], str) or not tokens(scope['body_end'])
                or not isinstance(scope['end_markers'], list)
                or any(not isinstance(marker, str) or not tokens(marker) for marker in scope['end_markers'])):
            raise ValueError('invalid comment text scope')
    elif 'text_scope' in contract:
        raise ValueError('invalid comment text scope')
    try:
        for key in ('record_selector', 'author_selector', 'body_selector'):
            if not isinstance(contract[key], str) or not contract[key].strip():
                raise ValueError('empty comment selector')
            soupsieve.compile(contract[key])
        if 'id_attribute' in contract and not re.fullmatch(r'[a-zA-Z_][\w:-]*', contract['id_attribute']):
            raise ValueError('invalid comment ID attribute')
        markers = contract['section_markers']
        if not isinstance(markers, list) or not markers or any(not isinstance(x, str) or not x.strip() for x in markers):
            raise ValueError('comment section markers required')
    except (KeyError, TypeError, re.error, soupsieve.SelectorSyntaxError) as error:
        raise ValueError('invalid comment contract') from error


def _html_records(content, contract):
    soup = article_html(content)
    cards = soup.select(contract['record_selector'])
    rows = []
    for card in cards:
        def owner(node):
            return node.find_parent(lambda x: soupsieve.match(contract['record_selector'], x))
        fields = [[node for node in card.select(contract[key]) if owner(node) is card]
                  for key in ('author_selector', 'body_selector')]
        parent = card.find_parent(lambda x: soupsieve.match(
            contract.get('parent_record_selector', contract['record_selector']), x))
        parent_index = next((i for i, candidate in enumerate(cards) if candidate is parent),
                            -1 if parent is not None else None)
        source_id = card.get(contract.get('id_attribute', 'id'))
        if isinstance(source_id, list):
            source_id = ' '.join(sorted(set(source_id)))
        row = {'parent': parent_index, 'source_id': source_id}
        if any(len(nodes) != 1 for nodes in fields):
            rows.append(row)
            continue
        author, body = [nodes[0] for nodes in fields]
        copy = article_html(str(body))
        for nested in list(copy.select(contract['record_selector'])):
            if nested.parent is not None:
                nested.decompose()
        row.update(author=visible_html_text(str(author)), text=visible_html_text(str(copy)))
        rows.append(row)
    return rows


def _section_marker_tokens(line, *, representation=None):
    """Ignore changing count decorations only on a comment section heading."""
    label = re.sub(r'^\s*#{1,6}\s+', '', line).strip().rstrip(':：')
    label = re.sub(r'^[0-9]+(?:\s*(?:条|则|件)\s*|\s+)', '', label)
    label = re.sub(r'\s*(?:\(\s*[0-9]+\s*(?:条|则|件)?\s*\)|（\s*[0-9]+\s*(?:条|则|件)?\s*）)\s*$', '', label)
    return tuple(tokens(label, representation=representation))


# These are interface controls, not author identities or comment bodies. Match
# whole labels only: a real comment can discuss logging in or loading a page.


def valid_comment_records(rows, contract, *, representation=None):
    controls = declared_labels(contract, 'ignored_labels')
    unavailable = declared_labels(contract, 'unavailable_notices')
    valid, seen = [], set()
    for row in rows:
        author, body = row.get('author'), row.get('text')
        if (author_key(author, representation=representation) is None or not isinstance(body, str)
                or author_key(body, representation=representation) is None
                or label_key(author, representation=representation) in controls
                or label_key(body, representation=representation) in controls
                or label_key(body, representation=representation) in unavailable):
            continue
        # Preserve separately identified equal comments. Without source IDs,
        # repeated identical records provide no evidence of additional coverage.
        identity = ('id', row.get('source_id') or row.get('id')) if row.get('source_id') or row.get('id') else (
            'text', author_key(author, representation=representation), label_key(body, representation=representation))
        if identity not in seen:
            seen.add(identity)
            valid.append(row)
    return valid


def _comment_time_metadata(line, *, representation=None):
    """Recognize standalone serializer timestamps, not date mentions in prose.

    Plain output consisting only of an author and date cannot distinguish a
    timestamp from a date-only comment. Native HTML body ownership remains the
    unambiguous representation for that unusual case.
    """
    stamp = (line if representation == 'plain_text' else visible_markdown_links(line)).strip()
    return bool(TIMESTAMP_LINE.fullmatch(stamp))


def _coverage_text_start(lines, contract, *, representation=None):
    """Prefer the reviewed body ending over an earlier navigation label.

    The ending is a scope hint, not an additional exact-body pass requirement.
    If it is absent, an explicit comment heading can independently establish
    the component. A bare navigation label cannot do so for scoped contracts.
    """
    marker_tokens = lambda value: _section_marker_tokens(value, representation=representation)
    markers = {_section_marker_tokens(x) for x in contract['section_markers']}
    scope = contract.get('text_scope')
    end_markers = {_section_marker_tokens(x) for x in scope['end_markers']} if scope else set()
    first = 0
    found_ending = False
    if scope:
        expected = ' '.join(tokens(visible_markdown_links(scope['body_end'])))
        fragments, owners = [], []
        for index, line in enumerate(lines):
            normalized = ' '.join(tokens(line if representation == 'plain_text' else visible_markdown_links(line),
                                        representation=representation))
            if not normalized:
                continue
            fragments.append(normalized)
            owners.extend([index] * (len(normalized) + 1))
        # Frozen endings are character slices, so their first or last word
        # may be partial. Match normalized text, not whole-token sequences.
        # Retain character ownership to find the actual ending line even when
        # a serializer wraps the same visible prose onto multiple lines.
        normalized_content = ' '.join(fragments)
        offset = normalized_content.find(expected)
        if offset >= 0:
            # A real section heading disambiguates earlier body repetitions
            # without allowing a later comment quoting the ending to move the
            # start boundary past already displayed comments.
            section = next((index for index, line in enumerate(lines)
                            if index > owners[offset + len(expected) - 1]
                            and re.match(r'\s*#{1,6}\s+', line)
                            and marker_tokens(line) in markers), None)
            if section is not None:
                candidate = normalized_content.find(expected, offset + 1)
                while candidate >= 0 and owners[candidate + len(expected) - 1] < section:
                    offset = candidate
                    candidate = normalized_content.find(expected, candidate + 1)
        if offset >= 0:
            first = owners[offset + len(expected) - 1] + 1
            found_ending = True
    for index in range(first, len(lines)):
        # A reviewed end boundary wins over a historical start label. Forms
        # below the last comment must never open a new discussion component.
        if marker_tokens(lines[index]) in end_markers:
            break
        if marker_tokens(lines[index]) not in markers:
            continue
        heading = None if representation == 'plain_text' else re.match(r'\s*(#{1,6})\s+', lines[index])
        if not scope or found_ending or heading:
            # Do not skip earlier records just because a comment form appears
            # below them. Only a marker before any content is the start marker.
            if found_ending and any(line.strip() for line in lines[first:index]):
                break
            return index + 1, len(heading[1]) if heading else 0, bool(heading) or representation == 'plain_text'
    return (first, 0, True) if found_ending else (None, 0, False)


def _text_author(stripped, heading, heading_authors, explicit_scope, new_paragraph, *, representation=None):
    """Recognize one author format without treating inline prose as a new record."""
    if heading:
        return heading[2], '', True
    if heading_authors:
        return None, '', True
    links = [] if representation == 'plain_text' else [link for link in markdown_links(stripped) if not link.is_image]
    if len(links) == 1 and links[0].start == 0 and links[0].end == len(stripped):
        return links[0].label, '', False
    bold = None if representation == 'plain_text' else re.fullmatch(r'\*\*(.+?)\*\*[:：]?', stripped)
    if bold:
        return bold[1], '', False
    delimiter = r'(?::(?=\s|$)|：)' if representation == 'plain_text' else r'[:：]'
    colon = re.fullmatch(r'([^\n:：]{1,120})' + delimiter + r'\s*(.*)', stripped)
    if explicit_scope and colon:
        if new_paragraph:
            return colon[1], colon[2], False
        if not colon[2]:
            return colon[1], '', False
    return None, '', False


def _coverage_text_records(content, contract, *, representation=None):
    """Read explicit author/body formatting without a frozen author list.

    An unlabelled prose block cannot establish author attribution. Subheadings,
    linked/bold author lines and colon-labelled records are supported; ambiguous
    bare names are deliberately not guessed from arbitrary prose. Colon-style
    authors additionally require a section heading or reviewed body ending: a
    plain navigation label must not turn article captions into authors.
    Author headings are peers; deeper headings remain in the current comment.
    """
    # Code may be comment text, but cannot introduce fake record boundaries.
    literal = representation == 'plain_text'
    visible = (lambda value: value) if literal else visible_markdown_links
    if literal:
        fences, restore_inline_code = {}, lambda value: value
    else:
        content, fences = mask_markdown_code(content, retain=True)
        content, restore_inline_code = mask_markdown_inline_code(content, fences.values())
    prefix = '\ue000coverage-paragraph'
    while prefix in content:
        prefix += '_'
    if not literal:
        content = without_markdown_images(content)
    paragraph_marker = prefix + 'paragraph'
    content = re.sub(r'\n[ \t]*\n', '\n' + paragraph_marker + '\n', content)
    if not literal:
        content = visible_html_text(str(article_html(content)), preserve_source_whitespace=True, preserve_links=True)
    lines = content.replace(paragraph_marker, '').splitlines()
    markers = {_section_marker_tokens(x) for x in contract['section_markers']}
    start, section_level, explicit_scope = _coverage_text_start(lines, contract, representation=representation)
    if start is None:
        return []
    end_markers = {_section_marker_tokens(x) for x in contract.get('text_scope', {}).get('end_markers', [])}
    controls = declared_labels(contract, "ignored_labels")
    rows, current = [], None
    heading_authors = False
    author_heading_level = None
    for offset, line in enumerate(lines[start:], start):
        previous_blank = offset == start or not lines[offset - 1].strip()
        stripped = line.strip()
        if stripped in fences:
            if current is not None:
                    current['text'] += '\n' + stripped
            continue
        if _section_marker_tokens(line, representation=representation) in end_markers:
            break
        heading = None if literal else re.fullmatch(r'\s*(#{1,6})\s+(.+?)\s*', line)
        if heading and section_level and len(heading[1]) <= section_level:
            break
        if heading and author_heading_level is not None and len(heading[1]) > author_heading_level:
            if current is not None:
                current['text'] += '\n' + visible(line)
            continue
        if label_key(visible(stripped), representation=representation) in controls:
            current = None
            continue
        if _section_marker_tokens(line, representation=representation) in markers:
            if not rows and heading:
                # Metadata may separate the reviewed ending from the section
                # heading. Retain its level before reading any author records.
                section_level = len(heading[1])
                explicit_scope = True
            current = None
            continue
        pingback = re.fullmatch(r'\s*Pingback:\s+(.+?)\s*', line)
        if pingback:
            label = visible(pingback[1])
            rows.append({'author': label, 'text': visible(line).strip()})
            current = None
            continue
        author, body, heading_authors = _text_author(
            stripped, heading, heading_authors, explicit_scope,
            current is None or previous_blank, representation=representation)
        if author is not None:
            if heading:
                author_heading_level = len(heading[1])
            current = {'author': visible(author), 'text': visible(body)}
            rows.append(current)
        elif current is not None and not _comment_time_metadata(line, representation=representation):
            current['text'] += '\n' + visible(line)
    for row in rows:
        row['author'] = article_field_text(row['author'], restore_inline_code, representation=representation)
        row['text'] = article_field_text(row['text'], restore_inline_code, representation=representation,
                                         literal_blocks=fences)
    return rows


def _verify_comment_coverage(content, contract, *, representation=None):
    if is_article_html(content, representation=representation):
        native = article_html(content).select(contract['record_selector'])
        rows = (_html_records(content, contract) if native
                else _coverage_text_records(article_markdown(content), contract))
    else:
        rows = _coverage_text_records(content, contract, representation=representation)
    valid = valid_comment_records(rows, contract, representation='plain_text')
    return {'article_comments_mode': 'fixed_coverage',
            'article_comments_pass': len(valid) >= contract['minimum_comments'],
            'article_comment_count': len(valid),
            'article_comment_minimum': contract['minimum_comments']}


def verify_article_comments(content, contract, *, representation=None):
    """Check fixed visible-comment coverage without a runtime reference."""
    validate_comment_contract(contract)
    return _verify_comment_coverage(content, contract, representation=representation)
