"""Explicit mutable-document section coverage, without frozen prose matching."""
from __future__ import annotations

import math
import re

import soupsieve
import unicodedata

from .reference_quality import visible_markdown_links


def _key(text):
    # Formatting and serializer line breaks may vary; word order may not.
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def _headings(contract):
    return [{'id': f'section-{index + 1}', 'body_required': True, **heading}
            for index, heading in enumerate(contract['headings'])]


def validate_document_structure(contract):
    if not isinstance(contract, dict) or set(contract) - {
        'version', 'headings', 'minimum_heading_coverage', 'minimum_body_chars', 'type',
        'minimum_section_chars',
    }:
        raise ValueError('Invalid document_structure fields')
    if contract.get('version') != 1 or isinstance(contract.get('version'), bool):
        raise ValueError('Unsupported document_structure version')
    if 'type' in contract and contract['type'] != 'definition':
        raise ValueError('Unsupported document structure type')
    threshold = contract.get('minimum_heading_coverage')
    if (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
            or not math.isfinite(threshold) or not 0 < threshold <= 1):
        raise ValueError('Invalid document heading coverage')
    minimum = contract.get('minimum_body_chars')
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        raise ValueError('Invalid document body character floor')
    section_minimum = contract.get('minimum_section_chars', 1)
    if type(section_minimum) is not int or section_minimum < 1:
        raise ValueError('Invalid document section character floor')
    headings = contract.get('headings')
    if (not isinstance(headings, list) or not headings
            or any(not isinstance(h, dict) for h in headings)):
        raise ValueError('Document structure requires evidenced headings')
    headings = _headings(contract)
    ids, texts = set(), set()
    key_function = _definition_key if contract.get('type') == 'definition' else _key
    for heading in headings:
        if (not isinstance(heading, dict)
                or not {'id', 'text', 'level', 'body_required'} <= set(heading)
                or set(heading) - {'id', 'text', 'level', 'body_required', 'html_selector'}):
            raise ValueError('Invalid document heading fields')
        if (not isinstance(heading['id'], str) or not heading['id'].strip()
                or heading['id'] in ids or not isinstance(heading['text'], str)
                or not key_function(heading['text']) or key_function(heading['text']) in texts):
            raise ValueError('Document headings must have unique IDs and text')
        if (isinstance(heading['level'], bool) or not isinstance(heading['level'], int)
                or not 1 <= heading['level'] <= 6 or not isinstance(heading['body_required'], bool)):
            raise ValueError('Invalid document heading level or body requirement')
        if contract.get('type') == 'definition' and (heading['level'] != 1 or not heading['body_required']):
            raise ValueError('Definitions require a local description and level 1')
        if 'html_selector' in heading:
            selector = heading['html_selector']
            if (contract.get('type') == 'definition' or not isinstance(selector, str)
                    or not selector.strip()):
                raise ValueError('Invalid document heading selector')
            try:
                soupsieve.compile(selector)
            except soupsieve.SelectorSyntaxError as error:
                raise ValueError('Invalid document heading selector') from error
        ids.add(heading['id'])
        texts.add(key_function(heading['text']))
    if not any(h['body_required'] for h in headings):
        raise ValueError('A document contract needs at least one substantive section')


def _content_root(content, *, preserve_local_header=False, single_article=True):
    from .dynamic_lists import scoped_html
    # Accessibility-tree hiding is not visual hiding. Reuse the shared
    # explicit HTML/inline-style visibility rules, including closed details.
    soup = scoped_html(content)
    root = (soup.select_one('main')
            or (soup.select_one('article') if single_article else None)
            or soup.body or soup)
    excluded = 'nav,footer,aside,[role="navigation"],.toc,#toc,.reference-toc'
    if not preserve_local_header or root.name not in {'main', 'article'}:
        excluded += ',header'
    for node in root.select(excluded):
        if node.parent is not None:
            node.decompose()
    return root


def _html_events(content, headings=()):
    from bs4 import Comment, NavigableString
    root = _content_root(content, preserve_local_header=True)
    # Lists containing only local section links are tables of contents. Other
    # lists and article-specific external reading links remain body content.
    for node in list(root.select('ul,ol')):
        if not node.parent:
            continue
        links = node.select('a[href]')
        if (links and all(str(a.get('href', '')).startswith('#') for a in links)
                and _key(node.get_text()) == _key(''.join(a.get_text() for a in links))):
            node.decompose()
    selected = {}
    for heading in headings:
        if 'html_selector' not in heading:
            continue
        for node in root.select(heading['html_selector']):
            if _key(node.get_text(' ', strip=True)) == _key(heading['text']):
                selected[id(node)] = heading['level']
    events = []
    consumed = set()
    for node in root.descendants:
        if id(node) in consumed:
            continue
        if id(node) in selected or getattr(node, 'name', None) in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            level = selected[id(node)] if id(node) in selected else int(node.name[-1])
            events.append((node.get_text(' ', strip=True), level))
            consumed.update(id(child) for child in node.descendants)
        elif (isinstance(node, NavigableString) and not isinstance(node, Comment)
              and id(node) not in consumed and node.strip()):
            events.append((str(node).strip(), None))
    return events


def _text_events(content, headings, key_function=_key):
    # Markdown code contributes body content, but cannot declare section titles.
    numbered = {m[1]: h['level'] for h in headings
                if (m := re.match(r'^(\d+(?:\.\d+)*)\.\s+', h['text']))}
    lines, eligible = [], []
    paragraph_boundaries = set()
    continuation_column = None
    fence = None
    previous_line = ''
    paragraph_start = 0
    for raw in content.splitlines():
        previous_text = previous_line.strip()
        previous_line = raw
        raw = raw.expandtabs(4)
        line = raw.strip()
        indent = len(raw) - len(raw.lstrip())
        marker = re.match(r'^(`{3,}|~{3,})(.*)$', line) if indent < 4 else None
        if marker:
            token, suffix = marker.groups()
            if fence is None and not (token[0] == '`' and '`' in suffix):
                fence = token
                continuation_column = None
                paragraph_start = len(lines)
                paragraph_boundaries.add(len(lines))
                continue
            if (fence is not None and token[0] == fence[0]
                    and len(token) >= len(fence) and not suffix.strip()):
                fence = None
                paragraph_start = len(lines)
                paragraph_boundaries.add(len(lines))
                continue
        if not line:
            paragraph_boundaries.add(len(lines))
            continuation_column = None
            paragraph_start = len(lines)
            continue
        # Wrapped numbered titles continue past the number in plain text.
        # A blank line or a less-indented paragraph ends that continuation.
        if numbered and fence is None:
            if continuation_column is not None and indent >= continuation_column:
                lines[-1] += ' ' + line
                continue
            marker = re.match(r'^(\s*\d+(?:\.\d+)*\.)\s+(?=\S)', raw)
            continuation_column = marker.end(1) if marker else None
        heading_eligible = fence is None and indent < 4
        if (heading_eligible and previous_text and lines and lines[-1] == previous_text
                and eligible[-1] == (True, None)
                and re.fullmatch(r'=+|-+', line)):
            lines[paragraph_start:] = [' '.join(lines[paragraph_start:])]
            eligible[paragraph_start:] = [(True, 1 if line[0] == '=' else 2)]
            paragraph_start = len(lines)
            continue
        if heading_eligible and re.fullmatch(r'(?:[-*+]\s*|\d+[.)]\s*)?\[[^]]+\]\(#[^)]*\)', line):
            continue
        level_match = re.match(r'^(#{1,6})\s+(.+?)(?:\s+#+)?$', line) if heading_eligible else None
        if level_match:
            # Native serializers may append the heading's permalink without a separator.
            label = re.split(r'https?://\S+#\S*$', level_match[2], maxsplit=1)[0].rstrip()
            if any(key_function(label) == key_function(h['text']) for h in headings):
                line = label
            else:
                line = level_match[2]
        lines.append(line)
        eligible.append((heading_eligible, len(level_match[1]) if level_match else None))
        if not heading_eligible or level_match:
            paragraph_start = len(lines)
    declared = {key_function(h['text']): h for h in headings}
    prefixes = {key[:i] for key in declared for i in range(1, len(key) + 1)}
    events, index = [], 0
    while index < len(lines):
        available, explicit_level = eligible[index]
        if not available:
            events.append((lines[index], None))
            index += 1
            continue
        joined, match = '', None
        # Text serializers often split inline code/links within a heading.
        # Only complete adjacent lines, in source order, may form that heading.
        for end in range(index, min(index + 12, len(lines))):
            if not eligible[end][0] or (end > index and (eligible[end][1] is not None
                                                       or end in paragraph_boundaries)):
                break
            joined += key_function(visible_markdown_links(lines[end]))
            if joined in declared:
                match = (end, declared[joined])
                break
            if joined not in prefixes:
                break
        if match:
            end, heading = match
            events.append((heading['text'], heading['level']))
            index = end + 1
        else:
            # Undeclared children of numbered sections are headings too, not
            # prose that can make a table-of-contents entry look complete.
            number = re.match(r'^(\d+(?:\.\d+)*)\.\s+\S', lines[index])
            if explicit_level is None and number:
                parents = [n for n in numbered if number[1].startswith(n + '.')]
                if numbered:
                    parent = max(parents, key=len) if parents else next(iter(numbered))
                    explicit_level = numbered[parent] + number[1].count('.') - parent.count('.')
            if key_function is _definition_key and re.fullmatch(
                    r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\(.*\)(?:\s*->\s*\S.*)?', lines[index].strip('`¶ ')):
                explicit_level = 1
            events.append((lines[index], explicit_level))
            index += 1
    return events



def _definition_key(text):
    # Preserve signature punctuation and argument order; tolerate inline-code
    # formatting and serializer whitespace, not bare API names.
    return re.sub(r'[\s`¶]', '', unicodedata.normalize('NFKC', text).casefold())


def _definition_html_events(content):
    root = _content_root(content)
    events = []
    for term in root.select('dt'):
        for anchor in term.select('.headerlink'):
            anchor.decompose()
        events.append((term.get_text('', strip=False).strip(), 1))
        description = term.find_next_sibling()
        # A term cannot borrow unrelated paragraphs or another term's dd.
        if description is not None and description.name == 'dd':
            events.append((description.get_text(' ', strip=True), None))
    return events

def evaluate_document_structure(content, contract, *, html_document=False):
    if not contract:
        return {}
    validate_document_structure(contract)
    headings = _headings(contract)
    definitions = contract.get('type') == 'definition'
    key_function = _definition_key if definitions else _key
    if definitions:
        events = (_definition_html_events(content) if html_document else
                  _text_events(content, headings, key_function))
    else:
        events = (_html_events(content, headings) if html_document else
                  _text_events(content, headings))
    declared = {key_function(h['text']): h for h in headings}
    # Serializers may change heading levels. The declared source hierarchy
    # determines which subsequent text belongs to a registered section.
    events = [(text, declared[key_function(text)]['level']
               if level is not None and key_function(text) in declared else level)
              for text, level in events]
    supported = set()
    found = set()
    definition_body_indices = set()
    for index, (text, level) in enumerate(events):
        heading = declared.get(key_function(text)) if level is not None else None
        if heading is None:
            continue
        found.add(heading['id'])
        end = next((i for i in range(index + 1, len(events))
                    if events[i][1] is not None and events[i][1] <= level), len(events))
        if definitions:
            definition_body_indices.update(i for i in range(index + 1, end) if events[i][1] is None)
        section_chars = sum(len(_key(value)) for value, event_level in events[index + 1:end]
                            if event_level is None)
        has_body = section_chars >= contract.get('minimum_section_chars', 1)
        if has_body or not heading['body_required']:
            supported.add(heading['id'])
    body_chars = sum(len(text) for i, (text, level) in enumerate(events)
                     if level is None and (not definitions or i in definition_body_indices))
    coverage = len(supported) / len(declared)
    return {
        'document_structure_pass': (coverage >= contract['minimum_heading_coverage']
                                    and body_chars >= contract['minimum_body_chars']),
        'document_heading_coverage': coverage,
        'document_body_chars': body_chars,
        'document_minimum_body_chars': contract['minimum_body_chars'],
        'document_heading_results': [
            {'id': h['id'], 'present': h['id'] in found, 'pass': h['id'] in supported}
            for h in headings
        ],
    }
