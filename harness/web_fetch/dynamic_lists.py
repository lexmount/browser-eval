"""Representation-neutral-ish list structure probe, NOT factual verification.

No saved names, ranks, prices or item IDs are compared. Correctness remains
unverified without an independent, contemporaneous reference in the same context.
"""
import hashlib
import re
import unicodedata
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qsl,unquote,urlencode,urljoin,urlsplit,urlunsplit


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
    """Decode only unreserved URL characters; keep encoded separators intact."""
    def decode(match):
        character = chr(int(match[1], 16))
        return character if character.isascii() and (
            character.isalnum() or character in '-._~') else match[0].upper()
    return re.sub(r'%([0-9a-fA-F]{2})', decode, path)


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
    # aria-hidden does not hide rendered labels. Explicit display/visibility
    # styles below, rather than accessibility metadata, determine exclusion.
    for node in list(soup.select('head,script,style,template,[hidden]')):
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


def visible_html_text(content):
    """Preserve block boundaries without splitting inline prose into headings.

    This uses the same explicit visibility exclusions as record extraction,
    not computed CSS. Source indentation is whitespace, not a rendered line.
    """
    from bs4 import Comment, NavigableString, Tag
    blocks = {'address', 'article', 'aside', 'blockquote', 'dd', 'details', 'dialog',
              'div', 'dl', 'dt', 'fieldset', 'figcaption', 'figure', 'footer',
              'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header', 'hr', 'li',
              'main', 'nav', 'ol', 'p', 'pre', 'section', 'summary', 'table',
              'tbody', 'thead', 'tfoot', 'tr', 'ul'}
    parts = []
    stack = [(_scoped_html(content), False, False)]
    while stack:
        node, closing, preformatted = stack.pop()
        if isinstance(node, Comment):
            continue
        if isinstance(node, NavigableString):
            parts.append(str(node) if preformatted else re.sub(r'\s+', ' ', str(node)))
        elif isinstance(node, Tag):
            separator = '\n' if node.name in blocks or node.name == 'br' else '\t' if node.name in {'td', 'th'} else ''
            parts.append(separator)
            if not closing:
                preformatted = preformatted or node.name in {'pre', 'textarea'}
                stack.append((node, True, preformatted))
                stack.extend((child, False, preformatted) for child in reversed(node.contents))
    return '\n'.join(line.strip() for line in ''.join(parts).splitlines() if line.strip())


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


def _scope_named_content(content, scope):
    """Apply one declared HTML or text scope and return owned name/link pairs."""
    owned_items = []
    if re.search(r'<(?:html|body|main|div|section|article|table|ul|ol|a|p|h[1-6])\b', content, re.I):
        soup = _scoped_html(content)
        owners = soup.select(scope['html_selector'])
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
        start = re.search(scope['start_pattern'], content, re.M)
        if not start:
            return '', [], False
        content = content[start.end():]
    if scope.get('end_pattern'):
        end = re.search(scope['end_pattern'], content, re.M)
        if end:
            content = content[:end.start()]
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
    from .reference_quality import markdown_links
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
            destination = unquote(link.destination)
            title_match = title_pattern.fullmatch(destination)
            if title_match and link.label.strip():
                titles.setdefault(title_match[1], set()).add(link.label.strip())
            detail_match = detail_pattern.search(destination)
            if detail_match:
                details.setdefault(detail_match[1], set()).add(link.destination)
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


def _markdown_named_items(content, contract, scope):
    """Extract explicit named Markdown links without treating prose as a list."""
    from .reference_quality import markdown_links, visible_markdown_links
    items = []
    for link in markdown_links(content):
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
        prefix=prefix.strip()
        line_end=content.find('\n',link.end)
        line=content[line_start:None if line_end < 0 else line_end]
        line_links=markdown_links(line)
        linked_chars=sum(item.end-item.start for item in line_links)
        link_collection=(len(line_links) >= 3 and linked_chars >= 0.8 * len(line))
        if (prefix and not link_collection
                and not re.fullmatch(r'[\s#>*\-\d.)|/:•·●▪◦,，、;；]*',prefix)):
            continue
        non_image_label=link.label
        for nested in reversed(markdown_links(link.label)):
            if nested.is_image:
                non_image_label=non_image_label[:nested.start]+non_image_label[nested.end:]
        if (not non_image_label.strip() if contract.get('name_validation') == 'nonempty'
                else not re.search(r'[\w\u3400-\u9fff]',non_image_label)):
            continue
        if scope and scope.get('markdown_label_pattern'):
            # A card caption is an alternative visible identifier when its
            # title exists only in artwork. Match within this link's label;
            # never borrow a caption from the following card or navigation.
            match = re.search(scope['markdown_label_pattern'], non_image_label)
            if not match:
                continue
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
        # Flat metadata links carry no section ownership. Never let them fill
        # a short list from a different region of the page.
        links = None
        content, owned_items, matched = _scope_named_content(content, scope)
        if not matched:
            return {}
    parser=Links();parser.feed(content)
    items = owned_items + _paired_markdown_items(content, scope)
    items += list(parser.items) + safe_named_links(links, base_url)
    items += _markdown_named_items(content, contract, scope)
    items += _image_heading_items(content, scope)
    return _deduplicated_named_candidates(items, contract, base_url, diagnostics)


def extract_named_entries(content,contract,base_url,links=None,diagnostics=None):
    """Return validated named entries after one canonical identity pass."""
    return list(_extract_named_candidates(
        content, contract, base_url, links=links, diagnostics=diagnostics,
    ).values())


def extract_linked_products(content, contract, base_url, diagnostics=None):
    """Extract title/link/price triples from one owned product segment."""
    scope = contract.get('entry_scope')
    if scope and scope.get('price_selector') and re.search(
            r'<(?:html|body|main|div|section|article|table|ul|ol|a|p|h[1-6])\b', content, re.I):
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
