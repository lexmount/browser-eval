"""Representation-neutral-ish list structure probe, NOT factual verification.

No saved names, ranks, prices or item IDs are compared. Correctness remains
unverified without an independent, contemporaneous reference in the same context.
"""
import hashlib
import re
import unicodedata
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit


class Links(HTMLParser):
    def __init__(self):super().__init__();self.current=None;self.items=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='a':self.current=[attrs.get('href',''),[]]
        # Image metadata is not a visible text name.
    def handle_data(self,data):
        if self.current:self.current[1].append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.current:
            self.items.append((self.current[0],''.join(self.current[1])));self.current=None


_PURE_CTA = re.compile(
    r'(?:details?|read more|view more|learn more|see (?:more|details|options)|open|visit|go|'
    r'watch|free tv|tv|buy now|add to cart|click to see price|sign in|log in|'
    r'详情|查看|更多|评论|分享|图片|点击|了解更多|打开|访问|进入|前往|'
    r'立即查看|立即购买|加入购物车|前往登录|登录|去看看|\d+)', re.I)


def _label_key(value):
    value=unicodedata.normalize('NFKC',unquote(unescape(value))).casefold()
    return re.sub(r'\s+',' ',value).strip(' *#>`')


def _is_named_label(title,url,parts,nonempty=False):
    """A record name must identify the entity, not repeat its route or UI action."""
    label=_label_key(title)
    if nonempty:
        if not label:
            return False
        if _PURE_CTA.fullmatch(label) and not label.isdecimal():
            return False
    elif (len(label)<2 or not any(c.isalpha() for c in label) or _PURE_CTA.fullmatch(label)
            or re.fullmatch(r'\(?\d+(?:[.,]\d+)?\s*[kmb]?\)?',label,re.I)):
        return False
    path=parts.path or '/'
    path_query=path + (('?' + parts.query) if parts.query else '')
    path_query_fragment=path_query + (('#' + parts.fragment) if parts.fragment else '')
    authority_path='//' + parts.netloc + path_query_fragment
    absolute=urlunsplit((parts.scheme,parts.netloc,parts.path,parts.query,parts.fragment))
    route_labels={absolute,authority_path,path,path.rstrip('/') or '/',path_query,
                  path_query_fragment}
    if parts.query:
        route_labels.update((parts.query,'?'+parts.query))
    return label not in {_label_key(value) for value in route_labels}


def _canonical_entry_path(path):
    """Normalize IRI text without decoding path separators or delimiters."""
    def decode(match):
        encoded = match[0]
        octets = bytes(int(value, 16) for value in re.findall(r'%([0-9a-fA-F]{2})', encoded))
        try:
            characters = octets.decode('utf-8')
        except UnicodeDecodeError:
            return encoded.upper()
        normalized = []
        for character in characters:
            character_bytes = character.encode('utf-8')
            if ord(character) > 127 or character.isalnum() or character in '-._~()':
                normalized.append(character)
            else:
                normalized.extend(f'%{value:02X}' for value in character_bytes)
        return ''.join(normalized)
    return re.sub(r'(?:%[0-9a-fA-F]{2})+', decode, path)


def _markdown_safe_destination(destination):
    """Escape only characters that can change Markdown link structure."""
    return ''.join(
        quote(character, safe='')
        if character in r'()[]<>\\' or character.isspace() or ord(character) < 32
        else character
        for character in destination
    )


def named_entry_identity(url, contract):
    """Return the contract's canonical linked-record identity."""
    parts = urlsplit(url)
    identity = _canonical_entry_path(parts.path).rstrip('/')
    if pattern := contract.get('entry_identity_pattern'):
        match = re.search(pattern, identity)
        if not match or not match.groups():
            raise ValueError('entry_identity_pattern must match and capture an identity')
        identity = match[1]
    elif identity_keys := contract.get('identity_query_keys', []):
        query_values = {key: [] for key in identity_keys}
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key in query_values:
                query_values[key].append(value)
        present = {key: values for key, values in query_values.items() if values}
        if (not present or any(len(values) != 1 or not values[0].strip()
                               for values in present.values())):
            raise ValueError('identity query must contain one nonempty declared value')
        identity += '?' + urlencode(sorted(
            (key, values[0]) for key, values in present.items()
        ))
    return parts.hostname + '::' + identity


def named_entry_source_pair_hash(title, url, contract):
    """Hash a normalized visible name with its canonical detail identity."""
    identity = named_entry_identity(url, contract)
    return hashlib.sha256((_label_key(title) + '\0' + identity).encode()).hexdigest()


def _scoped_html(content):
    """Exclude explicitly hidden nodes and image metadata from visible names.

    This is not a CSS renderer: external stylesheet visibility still needs
    independently captured browser evidence when qualifying source counts.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(content, 'html.parser')
    # HTML permits an omitted head end tag before an explicit body. Python's
    # html.parser leaves that body nested in head; preserve the existing body
    # before excluding metadata. No rendering or script execution is involved.
    if soup.head is not None and soup.body is not None and soup.body.parent is soup.head:
        soup.head.insert_after(soup.body.extract())
    # aria-hidden does not hide rendered labels. Explicit display/visibility
    # styles below, rather than accessibility metadata, determine exclusion.
    for node in list(soup.select('head,title,script,style,template,[hidden]')):
        if node.parent is not None:
            node.decompose()
    for node in list(soup.select('[style]')):
        if node.parent is None:
            continue
        declarations = {}
        for declaration in node.get('style', '').split(';'):
            key, separator, value = declaration.partition(':')
            if separator:
                declarations[key.strip().lower()] = value.replace('!important', '').strip().lower()
        if (declarations.get('display') == 'none'
                or declarations.get('visibility') in {'hidden', 'collapse'}
                or declarations.get('opacity') in {'0', '0.0', '0%'}):
            node.decompose()
    # An image description is not a visible news/product title. Explicit
    # title_selector extraction below already uses actual text nodes only.
    for node in soup.select('img'):
        node.attrs.pop('alt', None)
    return soup


def visible_html_text(
    content, *, preserve_source_whitespace=False, preserve_links=False
):
    """Preserve block boundaries without splitting inline prose into headings.

    Mixed Markdown retains source line boundaries for scoped record fields.
    This uses the same explicit visibility exclusions as record extraction,
    not computed CSS. Source indentation is whitespace, not a rendered line.
    """
    from bs4 import Comment, NavigableString, Tag
    from .reference_quality import HTML_BLOCK_ELEMENTS

    blocks = HTML_BLOCK_ELEMENTS
    parts = []
    stack = [(_scoped_html(content), False, False)]
    while stack:
        node, closing, preformatted = stack.pop()
        if isinstance(node, Comment):
            continue
        if isinstance(node, NavigableString):
            parts.append(str(node) if preformatted or preserve_source_whitespace
                         else re.sub(r'\s+', ' ', str(node)))
        elif isinstance(node, Tag):
            separator = '\n' if node.name in blocks or node.name == 'br' else '\t' if node.name in {'td', 'th'} else ''
            if preserve_links:
                if node.name in {'td', 'th'}:
                    separator = '\t' if closing else ''
                if node.name == 'a' and node.get('href'):
                    parts.append('](' + _markdown_safe_destination(str(node['href'])) + ')' if closing else '[')
                if not closing and re.fullmatch(r'h[1-6]', node.name):
                    parts.append(separator + '#' * int(node.name[1]) + ' ')
                    separator = ''
            parts.append(separator)
            if not closing:
                preformatted = preformatted or node.name in {'pre', 'textarea'}
                stack.append((node, True, preformatted))
                stack.extend((child, False, preformatted) for child in reversed(node.contents))
    text = '\n'.join(line.strip() for line in ''.join(parts).splitlines() if line.strip())
    if preserve_links:
        from .reference_quality import markdown_links
        # Source indentation after a native heading is layout whitespace.
        text = re.sub(r"(?m)^(#{1,6})[ \t]+", r"\1 ", text)
        # Native headings inside an anchor retain their actual level;
        # Markdown puts the heading marker outside its link label.
        for link in reversed(markdown_links(text)):
            heading = re.fullmatch(r"\s*(#{1,6})[ \t]+([^\n]+)\s*", link.label)
            if heading and not link.is_image:
                text = (text[:link.start] + "\n" + heading[1] + " [" + heading[2].strip()
                        + "](" + link.destination + ")\n" + text[link.end:])
    return text


def _named_candidate(url, title, contract, base_url, excluded_titles, diagnostics=None):
    """Normalize one output candidate; malformed output drops only that candidate."""
    try:
        destination = urljoin(base_url, unescape(url))
        parts = urlsplit(destination)
        parts.port
    except ValueError:
        if diagnostics is not None and "invalid_link_output" not in diagnostics:
            diagnostics.append("invalid_link_output")
        return None
    if (parts.scheme not in {'http', 'https'} or not parts.hostname
            or parts.username or parts.password
            or parts.hostname not in contract['hosts']
            or not re.fullmatch(
                contract['entry_path'],
                _canonical_entry_path(parts.path).rstrip('/'),
            )):
        return None
    title = re.sub(r'<[^>]+>', ' ', unescape(title))
    title = re.sub(r'\s+', ' ', title).strip(' *#>-')
    if (not _is_named_label(
            title, destination, parts,
            contract.get('name_validation') == 'nonempty')
            or any(pattern.fullmatch(title) for pattern in excluded_titles)):
        return None
    try:
        identity = named_entry_identity(destination, contract)
    except ValueError:
        # Output URLs with absent/ambiguous identity parameters are invalid
        # candidates. They must not erase valid siblings from the same answer.
        return None
    return identity, {'url': destination, 'title': title}


def _note_malformed_markdown_destinations(content, base_url, diagnostics):
    """Retain syntax diagnostics omitted by the balanced Markdown parser."""
    if diagnostics is None:
        return
    # The balanced Markdown parser intentionally omits syntactically malformed
    # destinations. Preserve that validation signal for the scorer without
    # letting one bad candidate erase independently valid siblings.
    for raw_destination in re.findall(r'\]\(([^)\s]+)\)', content):
        try:
            parsed = urlsplit(urljoin(base_url, unescape(raw_destination)))
            parsed.port
        except ValueError:
            if "invalid_link_output" not in diagnostics:
                diagnostics.append("invalid_link_output")
            break


def _text_scope_boundary(content, pattern):
    """Match literal text or an equivalent standalone Markdown heading.

    Return offsets in the original answer so record syntax and boundaries are
    never flattened. Preserve the declared anchors, including intentional
    prefix-only end boundaries; do not replace them with substring search.
    """
    from .reference_quality import visible_markdown_links
    candidates=[]
    raw=re.search(pattern,content,re.M)
    if raw:candidates.append((raw.start(),raw.end()))
    offset=0
    for line in content.splitlines(keepends=True):
        decoded_line = unescape(line)
        if re.match(pattern, decoded_line, re.M):
            candidates.append((offset, offset + len(line)))
        visible=unescape(visible_markdown_links(line)).strip()
        visible=re.sub(r'^#{1,6}\s+','',visible)
        visible=re.sub(r'^(?:[-*+]|\d+[.)])\s+','',visible)
        visible=re.sub(r'([*_]{1,2})(.+?)\1',r'\2',visible)
        visible=re.sub(r'\s+#+\s*$','',visible).strip()
        if re.match(pattern,visible,re.M):
            candidates.append((offset,offset+len(line)))
        offset+=len(line)
    return min(candidates) if candidates else None


def _has_scoped_html(content):
    # Native Markdown may retain empty bookmark anchors. They provide no HTML
    # record ownership and must not select the HTML-only CSS scope parser.
    without_bookmarks = re.sub(r'<a\b[^>]*>\s*</a\s*>', '', content, flags=re.I)
    return bool(re.search(
        r'<(?:html|body|main|div|section|article|table|ul|ol|a|p|h[1-6])\b',
        without_bookmarks, re.I,
    ))


def _document_html(content):
    from .reference_quality import is_html_document_or_fragment

    return is_html_document_or_fragment(content)


def _owned_metadata_links(full_content, scoped_content, links, base_url):
    """Use flat link metadata only when its visible name proves scope ownership."""
    from collections import defaultdict

    from .reference_quality import visible_markdown_links
    from .response_input import safe_named_links

    def visible_text(text):
        return _label_key(re.sub(r'<[^>]+>', ' ', visible_markdown_links(text)))

    def occurrence_count(text, label):
        if not label:
            return 0
        count = 0
        start = 0
        while (position := text.find(label, start)) >= 0:
            end = position + len(label)
            left_boundary = label[0].isascii() and label[0].isalnum()
            right_boundary = label[-1].isascii() and label[-1].isalnum()
            ascii_word = lambda char: char.isascii() and char.isalnum()
            left_ok = not left_boundary or position == 0 or not ascii_word(text[position - 1])
            right_ok = not right_boundary or end == len(text) or not ascii_word(text[end])
            count += bool(left_ok and right_ok)
            start = position + max(1, len(label))
        return count

    full_text = visible_text(full_content)
    scoped_text = visible_text(scoped_content)
    candidates = safe_named_links(links, base_url)
    destinations = defaultdict(set)
    for url, label in candidates:
        destinations[_label_key(label)].add(url)
    return [
        {'url': url, 'title': label}
        for url, label in candidates
        if occurrence_count(full_text, _label_key(label)) == 1
        and occurrence_count(scoped_text, _label_key(label)) == 1
        and len(destinations[_label_key(label)]) == 1
    ]


def _breadcrumb_scope_end(content, contract, base_url):
    """An ordered chain of non-record ancestor links returns to page navigation."""
    from .reference_quality import markdown_links, visible_markdown_links
    page = urlsplit(base_url)
    def route(path):
        return re.sub(r'/index\.html?$', '', _canonical_entry_path(path)).rstrip('/')
    current = route(page.path)
    offset = 0
    chain = []
    for line in content.splitlines(keepends=True):
        if not line.strip():
            offset += len(line)
            continue
        parsed = markdown_links(line)
        match = re.match(r"[ \t]*(\d+)[.)][ \t]+", line)
        valid = (match and len(parsed) == 1 and not parsed[0].is_image
                 and line[match.end():parsed[0].start].strip() == ''
                 and line[parsed[0].end:].strip() == ''
                 and visible_markdown_links(parsed[0].label).strip())
        if valid:
            link = parsed[0]
            try:
                destination = urljoin(base_url, link.destination)
                target = urlsplit(destination)
            except ValueError:
                valid = False
            else:
                path = route(target.path)
                valid = (target.netloc.casefold() == page.netloc.casefold()
                         and not target.fragment and (path == current or current.startswith(path + '/'))
                         and _named_candidate(destination, visible_markdown_links(link.label),
                                              contract, base_url, [], None) is None)
        if valid:
            number = int(match[1])
            if number == 1:
                chain = [(offset, path)]
            elif chain and number == len(chain) + 1 and (path == chain[-1][1]
                    or path.startswith(chain[-1][1] + '/')):
                chain.append((offset, path))
            else:
                chain = []
            if len(chain) >= 2 and len({path for _, path in chain}) >= 2:
                return chain[0][0]
        else:
            chain = []
        offset += len(line)
    return None


def _scope_named_content(content, scope, *, contract=None, base_url=''):

    """Apply one declared HTML or text scope and return owned name/link pairs."""
    owned_items = []
    if _has_scoped_html(content):
        soup = _scoped_html(content)
        owners = soup.select(scope['html_selector'])
        # Inline HTML labels in Markdown do not make a document HTML.
        # A real document still owns its entries through the HTML selector.
        from .reference_quality import markdown_links
        # Inline HTML does not turn a native Markdown answer into a document.
        # Require real visible Markdown links before taking the existing
        # Markdown route; script-only links cannot bypass a DOM scope.
        if (owners or _document_html(content)
                or (not scope.get("start_pattern")
                    and not markdown_links(visible_html_text(content)))):
            if not scope.get('title_selector'):
                return '\n'.join(str(node) for node in owners), owned_items, True
            for owner in owners:
                titles = owner.select(scope['title_selector'])
                destination_nodes = owner.select(scope['link_selector'])
                if owner.name == 'a' and owner.css.match(scope['link_selector']):
                    destination_nodes.insert(0, owner)
                destinations = {a.get('href') for a in destination_nodes if a.get('href')}
                if len(titles) == 1 and len(destinations) == 1:
                    owned_items.append((destinations.pop(), titles[0].get_text(' ', strip=True)))
            return '', owned_items, True
    if scope.get('start_pattern'):
        start = _text_scope_boundary(content, scope['start_pattern'])
        if not start:
            return '', [], False
        content = content[start[1]:]
    if scope.get('end_pattern'):
        end = _text_scope_boundary(content, scope['end_pattern'])
        if end:
            content = content[:end[0]]
    if scope.get('start_pattern') and contract is not None and base_url:
        navigation = _breadcrumb_scope_end(content, contract, base_url)
        if navigation is not None:
            content = content[:navigation]
    if scope.get('auxiliary_links_end_pattern'):
        from .reference_quality import markdown_links
        lines = content.splitlines()
        for index, line in enumerate(lines):
            if not re.fullmatch(scope['auxiliary_links_end_pattern'], line.strip()):
                continue
            cursor = index - 1
            while cursor >= 0:
                previous = lines[cursor].strip()
                if not previous:
                    cursor -= 1
                    continue
                links_on_line = markdown_links(previous)
                residue = previous
                for link in reversed(links_on_line):
                    residue = residue[:link.start] + residue[link.end:]
                if not links_on_line or residue.strip(' *-•\t'):
                    break
                lines[cursor] = ''
                cursor -= 1
        content = '\n'.join(lines)
    return content, owned_items, True


def _paired_markdown_items(content, scope):
    """Join independently identified title/detail Markdown links by entity ID."""
    if not scope or not scope.get('markdown_title_identity_pattern'):
        return []
    from .reference_quality import markdown_links, visible_markdown_links
    title_pattern = re.compile(scope['markdown_title_identity_pattern'])
    detail_pattern = re.compile(scope['markdown_detail_identity_pattern'])
    items = []
    for line in content.splitlines():
        if '|' not in line:
            continue
        titles, details = {}, {}
        for link in markdown_links(line):
            if link.is_image:
                continue
            destination = unquote(link.destination).replace(r'\(', '(').replace(r'\)', ')')
            title_match = title_pattern.fullmatch(destination)
            if title_match and link.label.strip():
                titles.setdefault(title_match[1], set()).add(link.label.strip())
            detail_match = detail_pattern.search(destination)
            if detail_match:
                details.setdefault(detail_match[1], set()).add(link.destination)
        # Native converters may drop a script title target while retaining
        # a visible plain title cell and the same row's real detail link.
        # Link-title metadata can only bind a title already present visibly
        # in another cell; it cannot supply the answer on its own.
        if not titles and len(details) == 1:
            from .structured_tables import _pipe_cells
            identity, destinations = next(iter(details.items()))
            if len(destinations) == 1:
                destination = next(iter(destinations))
                names = set()
                for link in markdown_links(line):
                    if link.destination != destination or not link.title:
                        continue
                    label = visible_markdown_links(link.label).strip()
                    name = link.title.strip()
                    if label and name.endswith(' ' + label):
                        name = name[:-len(label)].strip()
                    if not name:
                        continue
                    for cell in _pipe_cells(line):
                        primary = re.split(r'<br\s*/?>', cell, maxsplit=1, flags=re.I)[0].strip()
                        if not markdown_links(primary):
                            visible = visible_html_text(primary).strip()
                            if _label_key(visible) == _label_key(name):
                                names.add(visible)
                if len(names) == 1:
                    items.append((destination, next(iter(names))))
        for identity in titles.keys() & details.keys():
            if len(titles[identity]) == len(details[identity]) == 1:
                items.append((next(iter(details[identity])), next(iter(titles[identity]))))
    visible_links = [link for link in markdown_links(content) if not link.is_image]
    for first, second in zip(visible_links, visible_links[1:]):
        if content[first.end:second.start].strip():
            continue
        for title, detail in ((first, second), (second, first)):
            destination = unquote(title.destination).replace(r'\(', '(').replace(r'\)', ')')
            title_match = title_pattern.fullmatch(destination)
            detail_match = detail_pattern.search(unquote(detail.destination))
            if (title_match and detail_match and title_match[1] == detail_match[1]
                    and title.label.strip()):
                items.append((detail.destination, title.label.strip()))
                break
    return items


def _owned_model_caption(content, links, index):
    """Bind a visible model/name caption to its own empty link and image."""
    from .reference_quality import markdown_links
    link = links[index]
    finish = next((other.start for other in links[index + 1:]
                   if not other.is_image), len(content))
    block = content[link.end:finish]
    identity = _canonical_entry_path(urlsplit(link.destination).path).rstrip('/').rsplit('/', 1)[-1]
    if not identity or re.search(r"`+|~{3,}", block):
        return ''
    images = [image for image in markdown_links(block) if image.is_image
              and re.search(rf"(?<![A-Za-z0-9]){re.escape(identity)}(?![A-Za-z0-9])",
                            _canonical_entry_path(urlsplit(image.destination).path))]
    if not images:
        return ''
    captions = []
    for match in re.finditer(r"(?m)^[ \t]*([A-Z]+[0-9][A-Z0-9_.-]+[ \t]+[^\n]+)[ \t]*$", block):
        caption = match[1].strip()
        name = re.split(r"[ \t]+", caption, maxsplit=1)[1]
        if (any(image.end <= match.start() for image in images)
                and not markdown_links(caption)
                and not re.search(r"<[^>]*>|[|]", caption)
                and not re.search(r"(?m)^[ \t]*#{1,6}[ \t]+", block[:match.start()])
                and any(character.isalpha() for character in name)):
            captions.append(caption)
    return captions[0] if len(captions) == 1 else ''


def _named_table_items(content, scope):
    """Declared table columns bind a visible name to its same-row detail link."""
    if not scope or not scope.get('markdown_title_column'):
        return []
    from .reference_quality import markdown_links, visible_markdown_links, visible_mixed_html_text

    def cells(line):
        # Escaped pipes remain text; a doubled backslash leaves a real delimiter.
        values, start, slashes = [], 0, 0
        for index, char in enumerate(line):
            if char == '|' and slashes % 2 == 0:
                values.append(line[start:index].strip())
                start = index + 1
            slashes = slashes + 1 if char == '\\' else 0
        values.append(line[start:].strip())
        if line.lstrip().startswith('|'):
            values.pop(0)
        if values and not values[-1] and line.rstrip().endswith('|'):
            values.pop()
        return values

    lines, fence = [], None
    for line in content.splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if marker:
            if fence is None:
                fence = marker[1]
            elif (marker[1][0] == fence[0] and len(marker[1]) >= len(fence)
                  and not marker[2].strip()):
                fence = None
            lines.append('')
        else:
            lines.append('' if fence else line)
    items = []
    title_identity = (re.compile(scope['markdown_title_identity_pattern'])
                      if scope.get('markdown_title_identity_pattern') else None)
    detail_identity = (re.compile(scope['markdown_detail_identity_pattern'])
                       if scope.get('markdown_detail_identity_pattern') else None)
    for index in range(len(lines) - 1):
        if '|' not in lines[index]:
            continue
        headers = [visible_markdown_links(value).strip() for value in cells(lines[index])]
        divider = cells(lines[index + 1])
        if (len(divider) != len(headers)
                or not all(re.fullmatch(r':?-{3,}:?', value) for value in divider)
                or headers.count(scope['markdown_title_column']) != 1
                or headers.count(scope['markdown_link_column']) != 1):
            continue
        title_column = headers.index(scope['markdown_title_column'])
        link_column = headers.index(scope['markdown_link_column'])
        for line in lines[index + 2:]:
            if not line.strip() or '|' not in line:
                break
            row = cells(line)
            if len(row) != len(headers):
                continue
            detail_links = {link.destination for link in markdown_links(row[link_column])
                            if not link.is_image}
            if len(detail_links) != 1:
                continue
            destination = next(iter(detail_links))
            title_cell = re.split(r'<br\s*/?>', row[title_column], maxsplit=1, flags=re.I)[0]
            title = visible_markdown_links(visible_mixed_html_text(title_cell)).strip()
            if not title:
                continue
            if title_identity and detail_identity:
                stated_ids = {match[1] for link in markdown_links(title_cell)
                              if (match := title_identity.fullmatch(unquote(link.destination)
                                  .replace(r'\(', '(').replace(r'\)', ')')))}
                detail_id = detail_identity.search(unquote(destination))
                if stated_ids and (not detail_id or stated_ids != {detail_id[1]}):
                    continue
            items.append((destination, title))
    return items


def _markdown_heading_card_items(content, contract, scope, base_url):
    """Bind one heading title to one detail link inside that heading's card.

    This parser is opt-in because a heading does not ordinarily own every link
    below it. A declared title pattern starts a card, the next Markdown heading
    ends it, and visible body copy must occur before the detail link. The
    contract's host and path rules identify detail links without relying on a
    provider, site name, action label, or saved entity title.
    """
    if not scope or not scope.get('markdown_card_title_pattern'):
        return [], set()
    from .reference_quality import markdown_links, visible_markdown_links

    title_pattern = re.compile(scope['markdown_card_title_pattern'])
    headings = list(re.finditer(r'(?m)^ {0,3}#{1,6}[ \t]+[^\n]+$', content))
    items, owned_link_spans = [], set()
    excluded_titles = [re.compile(pattern, re.I)
                       for pattern in contract.get('title_exclusion_patterns', [])]
    for heading in headings:
        title_match = title_pattern.fullmatch(heading[0])
        if not title_match:
            continue
        title = re.sub(r'\s+', ' ', visible_markdown_links(title_match['title'])).strip()
        heading_candidates = []
        for link in markdown_links(title_match['title']):
            if link.is_image:
                continue
            candidate = _named_candidate(
                link.destination, title, contract, base_url, excluded_titles,
            )
            if candidate is not None:
                heading_candidates.append(candidate)
        heading_identities = {candidate[0] for candidate in heading_candidates}
        if len(heading_identities) == 1:
            identity = next(iter(heading_identities))
            candidate = next(
                candidate for candidate in heading_candidates
                if candidate[0] == identity
            )
            items.append((candidate[1]['url'], candidate[1]['title']))
            continue
        if heading_candidates:
            continue
        following = content[heading.end():]
        block_boundary = re.search(
            r'(?m)^(?: {0,3}#{1,6}[ \t]+| {0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,}))',
            following,
        )
        boundary = (
            heading.end() + block_boundary.start()
            if block_boundary else len(content)
        )
        segment = content[heading.end():boundary]
        candidates = []
        for link in markdown_links(segment):
            if link.is_image:
                continue
            candidate = _named_candidate(
                link.destination, title, contract, base_url,
                excluded_titles,
            )
            if candidate is not None:
                candidates.append((link, candidate))
        identities = {candidate[0] for _link, candidate in candidates}
        if len(identities) != 1:
            continue
        identity = next(iter(identities))
        first_link, candidate = next(
            (link, candidate) for link, candidate in candidates
            if candidate[0] == identity
        )
        before_link = segment[:first_link.start]
        for link in reversed(markdown_links(before_link)):
            before_link = before_link[:link.start] + before_link[link.end:]
        body = visible_markdown_links(
            re.sub(r'!\[[^\]]*\]\([^)]*\)|<[^>]+>', ' ', before_link)
        )
        if not any(any(character.isalnum() for character in line)
                   for line in body.splitlines()):
            continue
        owned_link_spans.update(
            (heading.end() + link.start, heading.end() + link.end)
            for link, owned_candidate in candidates
            if owned_candidate[0] == candidate[0]
        )
        items.append((candidate[1]['url'], candidate[1]['title']))
    return items, owned_link_spans


def _markdown_named_items(content, contract, scope, excluded_spans=frozenset()):
    """Extract explicit named Markdown links without treating prose as a list."""
    from .reference_quality import markdown_links, visible_markdown_links
    items = []
    parsed_links = markdown_links(content)
    for link_index, link in enumerate(parsed_links):
        if (link.start, link.end) in excluded_spans:
            continue
        destination = link.destination.strip()
        try:
            target = urlsplit(destination)
        except ValueError:
            continue
        if (link.is_image or not destination or destination.startswith(('#', '?'))
                or (target.scheme and target.scheme not in {'http', 'https'})):
            continue
        # Explicit balanced link syntax owns a multiline label, including card
        # headings separated by blank layout lines. Text before the opening on
        # the same line must still be list/rank markup, not recommendation prose.
        line_start=content.rfind('\n',0,link.start)+1
        prefix=content[line_start:link.start].strip()
        # A card can expose its image and title as adjacent links on one line.
        # Remove already-complete links before classifying the remaining prefix;
        # prose such as ``Watch [A] or [B]`` still leaves words and is rejected.
        for previous in reversed(markdown_links(prefix)):
            prefix=prefix[:previous.start]+prefix[previous.end:]
        # The physical line can begin inside the preceding multiline card.
        # Its closing label/destination is link syntax, not prose before this
        # card. Preserve any intervening words so recommendations still fail.
        if link_index:
            previous = parsed_links[link_index - 1]
            if previous.start < line_start < previous.end <= link.start:
                prefix = content[previous.end:link.start]
        prefix=prefix.strip()
        # Emphasized numeric ranks remain list markup. Do not strip arbitrary
        # underscores: recommendation prose and unmatched markup stay invalid.
        prefix=re.sub(r'(?<!\w)(_{1,2})(\d+)\1(?=$|\s|[.)])',r'\2',prefix)
        # CommonMark escapes change punctuation spelling, not list ownership.
        # Normalize only this prefix; words still fail the prose exclusion.
        prefix=re.sub(r'\\([!"#$%&\'()*+,\-./:;<=>?@\[\]\\^_`{|}~])',r'\1',prefix)
        line_end=content.find('\n',link.end)
        line=content[line_start:None if line_end < 0 else line_end]
        line_links=markdown_links(line)
        linked_chars=sum(item.end-item.start for item in line_links)
        link_collection=(len(line_links) >= 3 and linked_chars >= 0.8 * len(line))
        # A complete typed date before one title link is card metadata.
        # Recommendation prose cannot substitute for that complete prefix.
        from .composite_tasks import _DATE_OR_TIME, serialized_link_card_fields
        card_fields = serialized_link_card_fields(link)
        if card_fields and re.match(r"^[\s*+\-]*\d|^[\s*+\-]*(?:January|February|March|April|May|June|July|August|September|October|November|December)\b", prefix):
            prefix = ""
        date_prefix = re.sub(r"[ \t]*#{1,6}[ \t]*$", "", prefix)
        if len(line_links) == 1 and _DATE_OR_TIME.fullmatch(date_prefix):
            prefix = ""
        # A closed emphasized line can declare an unlinked bracketed category
        # before its sole title link. The category is card markup, not title
        # text; ordinary recommendation prose and unclosed lines stay invalid.
        category = re.fullmatch(r"(?P<bold>\*\*|__)(?P<italic>\*|_)\[[^\[\]\n]+\](?P=italic)\s*", prefix)
        if (category and len(line_links) == 1
                and line.rstrip().endswith(category['bold'])):
            prefix = ""
        if (prefix and not link_collection
                and not re.fullmatch(r'[\s#>*+\-\d.)|/:•·●▪◦,，、;；]*',prefix)):
            continue
        non_image_label=card_fields[0] if card_fields else link.label
        for nested in reversed(markdown_links(link.label) if not card_fields else []):
            if nested.is_image:
                non_image_label=non_image_label[:nested.start]+non_image_label[nested.end:]
        if not non_image_label.strip() and any(item.is_image for item in markdown_links(link.label)):
            # A thumbnail-only link can own the immediately following plain
            # caption paragraph. Never use image alt text or skip another
            # link/heading to find a name; declared scope is already applied.
            trailing = content[link.end:]
            caption = ''
            if re.match(r'[ \t]*\n[ \t]*\n', trailing):
                caption = trailing.lstrip(' \t\r\n').split('\n\n', 1)[0].strip()
            else:
                # Native line-oriented cards can put the visible title on
                # the next physical line, followed directly by metadata.
                newline = re.match(r'[ \t]*\r?\n', trailing)
                if newline:
                    caption = trailing[newline.end():].split('\n', 1)[0].strip()
            if caption:
                if (caption and '\n' not in caption and not markdown_links(caption)
                        and not re.search(r'<[^>]*>|^#{1,6}\s|^[-*+]\s', caption)
                        and any(character.isalpha() for character in caption)):
                    non_image_label = caption
        if not non_image_label.strip() and not link.label.strip():
            non_image_label = _owned_model_caption(content, parsed_links, link_index)
        if (not non_image_label.strip() if contract.get('name_validation') == 'nonempty'
                else not re.search(r'[\w\u3400-\u9fff]',non_image_label)):
            continue
        if scope and scope.get('markdown_label_pattern'):
            # A card caption is an alternative visible identifier when its
            # title exists only in artwork. Match within this link's label;
            # never borrow a caption from the following card or navigation.
            match = re.search(scope['markdown_label_pattern'], non_image_label)
            if not match and not re.search(r'`+|~{3,}', non_image_label):
                # Native converters can preserve the card's heading tag but
                # flatten the following caption paragraph. The closed heading
                # and this same link's end retain both caption boundaries.
                headings = list(re.finditer(r'</h[1-6]\s*>', non_image_label, re.I))
                if headings:
                    tail = non_image_label[headings[-1].end():]
                    if not markdown_links(tail):
                        caption = re.sub(r'\s+', ' ', visible_html_text(tail)).strip()
                        match = re.search(scope['markdown_label_pattern'],
                                          '<br>' + caption + '<br>')
            if not match:
                continue
            else:
                non_image_label = match['label']
        title=re.sub(r'\s+',' ',visible_markdown_links(non_image_label)).strip()
        items.append((destination,title))
    return items


def _image_heading_items(content, scope):
    """Pair an image-only card link with one matching following heading."""
    if scope and scope.get('markdown_label_pattern'):
        return []
    from .reference_quality import visible_markdown_links
    items = []
    markdown_lines = content.splitlines()
    for line_index, line in enumerate(markdown_lines):
        image_only = re.fullmatch(
            r'\s*\[!\[([^]]*)\]\([^)]*\)\]\((https?://[^\s)]+|/[^\s)]+)\)\s*',
            line,
        )
        if not image_only:
            continue
        image_title=re.sub(r'\s+',' ',visible_markdown_links(image_only[1])).strip()
        for following in markdown_lines[line_index + 1:]:
            if re.fullmatch(r'\s*\[!\[[^]]*\]\([^)]*\)\]\([^\s)]+\)\s*',following):
                break
            if not following.strip():
                continue
            heading = re.fullmatch(r'\s*#{1,6}\s+(.+?)\s*', following)
            if heading:
                heading_title=re.sub(r'\s+',' ',visible_markdown_links(heading[1])).strip()
                if heading_title==image_title:
                    items.append((image_only[2], heading_title))
                break
    return items


def _deduplicated_named_candidates(items, contract, base_url, diagnostics):
    """Apply one candidate validator and retain its canonical identity."""
    excluded_titles = [re.compile(pattern, re.I)
                       for pattern in contract.get('title_exclusion_patterns', [])]
    candidates={}
    for url,title in items:
        candidate = _named_candidate(
            url, title, contract, base_url, excluded_titles, diagnostics,
        )
        if candidate is not None:
            identity, entry = candidate
            candidates[identity] = entry
    return candidates


def _extract_named_candidates(content,contract,base_url,links=None,diagnostics=None):
    """Orchestrate source parsers into one identity-keyed candidate mapping."""
    from .response_input import safe_named_links
    base_url = contract.get('link_base_url', base_url)
    _note_malformed_markdown_destinations(content, base_url, diagnostics)
    scope = contract.get('entry_scope')
    owned_items = []
    if scope:
        full_content = content
        content, owned_items, matched = _scope_named_content(content, scope, contract=contract, base_url=base_url)
        if not matched:
            return {}
        # Flat links cannot supply missing section text. A unique exact label
        # already delivered inside a declared text scope establishes ownership.
        links = (_owned_metadata_links(full_content, content, links, base_url)
                 if scope.get('start_pattern') and not _document_html(full_content)
                 else None)
    parser=Links();parser.feed(content)
    heading_items, heading_link_spans = _markdown_heading_card_items(
        content, contract, scope, base_url,
    )
    items = owned_items + _paired_markdown_items(content, scope) + _named_table_items(content, scope)
    items += list(parser.items) + safe_named_links(links, base_url)
    items += _markdown_named_items(content, contract, scope, heading_link_spans)
    items += _image_heading_items(content, scope)
    # Explicit card ownership is stronger than a generic link label for the
    # same destination, so append it last before canonical deduplication.
    items += heading_items
    return _deduplicated_named_candidates(items, contract, base_url, diagnostics)


def extract_named_entries(content,contract,base_url,links=None,diagnostics=None):
    """Return validated named entries after one canonical identity pass."""
    return list(_extract_named_candidates(
        content, contract, base_url, links=links, diagnostics=diagnostics,
    ).values())


def extract_linked_products(content, contract, base_url, diagnostics=None):
    """Extract title/link/price triples from one owned product segment."""
    scope = contract.get('entry_scope')
    if scope and scope.get('price_selector') and _has_scoped_html(content):
        soup = _scoped_html(content)
        allowed = [re.compile(p, re.I) for p in contract['price_patterns']]
        excluded = [re.compile(p, re.I) for p in contract.get('price_exclusion_patterns', [])]
        unavailable = [re.compile(p, re.I) for p in contract.get('price_unavailable_patterns', [])]
        records = {}
        for owner in soup.select(scope['html_selector']):
            candidates = _extract_named_candidates(
                str(owner), contract, base_url, diagnostics=diagnostics,
            )
            if len(candidates) != 1:
                continue
            identity, entry = next(iter(candidates.items()))
            prices = [node.get_text(' ', strip=True) for node in owner.select(scope['price_selector'])]
            prices = [p for p in prices if any(r.fullmatch(p) for r in allowed)
                      and not any(r.fullmatch(p) for r in excluded)]
            if prices:
                records[identity] = {**entry, 'price': prices[0]}
            else:
                text = owner.get_text(' ', strip=True)
                hint = next((m.group(0) for pattern in unavailable
                             if (m := pattern.search(text))), None)
                if hint:
                    records[identity] = {
                        **entry, 'price': None, 'price_status': 'not_shown_on_page',
                        'price_unavailable_text': hint}
        return list(records.values())
    if scope:
        content, _, matched = _scope_named_content(content, scope)
        if not matched:
            return []
    scannable = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", content)
    link_pattern = re.compile(
        r"\[([^\]\n]+)\]\((https?://[^\s)]+|/[^\s)]+)\)"
        r"|<a\b[^>]*\bhref=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        re.I | re.S,
    )
    links = []
    excluded_titles = [re.compile(pattern, re.I)
                       for pattern in contract.get('title_exclusion_patterns', [])]
    for match in link_pattern.finditer(scannable):
        title = match.group(1) if match.group(1) is not None else match.group(4)
        raw_url = match.group(2) if match.group(2) is not None else match.group(3)
        candidate = _named_candidate(
            raw_url, title, contract, base_url, excluded_titles, diagnostics,
        )
        if candidate is None:
            continue
        identity, entry = candidate
        links.append((match.start(), match.end(), identity,
                      entry['url'], entry['title']))

    allowed = [re.compile(pattern, re.I) for pattern in contract["price_patterns"]]
    excluded = [
        re.compile(pattern, re.I)
        for pattern in contract.get("price_exclusion_patterns", [])
    ]
    candidates = {}
    groups = []
    for link in links:
        if not groups or groups[-1][0][2] != link[2]:
            groups.append([link])
        else:
            groups[-1].append(link)
    for index, group in enumerate(groups):
        _, first_end, identity, url, _ = group[0]
        boundary = groups[index + 1][0][0] if index + 1 < len(groups) else len(scannable)
        title = max((item[4] for item in group), key=lambda value: (len(value), value))
        segment = "\n".join(item[4] for item in group) + "\n" + scannable[
            first_end:min(boundary, first_end + 2400)
        ]
        prices = []
        for pattern in allowed:
            for price_match in pattern.finditer(segment):
                value = re.sub(r"\s+", " ", price_match.group(0)).strip()
                if any(rule.fullmatch(value) for rule in excluded):
                    continue
                prices.append((price_match.start(), value))
        if prices:
            candidates.setdefault(identity, {
                "url": url, "title": title, "price": min(prices)[1],
            })
        else:
            hint = next((m.group(0) for pattern in contract.get('price_unavailable_patterns', [])
                         if (m := re.search(pattern, segment, re.I))), None)
            if hint:
                candidates.setdefault(identity, {'url': url, 'title': title, 'price': None,
                    'price_status': 'not_shown_on_page', 'price_unavailable_text': hint})
    return list(candidates.values())


def evaluate_dynamic_list(content,contract,base_url,eligible=True,links=None):
    candidates=extract_named_entries(content,contract,base_url,links)
    enough=len(candidates)>=contract.get('minimum_entries',3)
    return {'structure_pass':bool(eligible and enough),'named_entry_count':len(candidates),
            'minimum_entries':contract.get('minimum_entries',3),'factual_correctness':None,
            'snapshot_f1':None,'dynamic_titles':[x['title'] for x in candidates],
            'verification_note':'仅验证标题与详情入口结构；没有同时间、同地区、同会话的独立参考，不判事实正确性或完整度。'}
