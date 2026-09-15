"""Representation-neutral-ish list structure probe, NOT factual verification.

No saved names, ranks, prices or item IDs are compared. Correctness remains
unverified without an independent, contemporaneous reference in the same context.
"""
import re
import unicodedata
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qsl,unquote,urljoin,urlsplit,urlunsplit


class Links(HTMLParser):
    def __init__(self):super().__init__();self.current=None;self.items=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='a':self.current=[attrs.get('href',''),[]]
        elif tag=='img' and self.current:self.current[1].append(attrs.get('alt',''))
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


def _is_named_label(title,url,parts):
    """A record name must identify the entity, not repeat its route or UI action."""
    label=_label_key(title)
    if (len(label)<2 or not any(c.isalpha() for c in label) or _PURE_CTA.fullmatch(label)
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


def extract_named_entries(content,contract,base_url,links=None):
    parser=Links();parser.feed(content)
    from .response_input import safe_named_links
    from .reference_quality import markdown_links, visible_markdown_links
    items=list(parser.items) + safe_named_links(links, base_url)
    for link in markdown_links(content):
        if link.is_image or not link.destination.startswith(('http://','https://','/')):
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
        if prefix and not re.fullmatch(r'[\s#>*\-\d.)|•·●▪◦]*',prefix):
            continue
        non_image_label=link.label
        for nested in reversed(markdown_links(link.label)):
            if nested.is_image:
                non_image_label=non_image_label[:nested.start]+non_image_label[nested.end:]
        if not re.search(r'[\w\u3400-\u9fff]',non_image_label):
            continue
        title=re.sub(r'\s+',' ',visible_markdown_links(link.label)).strip()
        items.append((link.destination,title))
    markdown_lines = content.splitlines()
    for line_index, line in enumerate(markdown_lines):
        image_only = re.fullmatch(
            r'\s*\[!\[[^]]*\]\([^)]*\)\]\((https?://[^\s)]+|/[^\s)]+)\)\s*',
            line,
        )
        if not image_only:
            continue
        for following in markdown_lines[line_index + 1:line_index + 4]:
            if not following.strip():
                continue
            heading = re.fullmatch(r'\s*#{1,6}\s+(.+?)\s*', following)
            if heading:
                items.append((image_only[1], heading[1]))
            break
    candidates={}
    for url,title in items:
        url=urljoin(base_url,unescape(url));parts=urlsplit(url)
        if parts.hostname not in contract['hosts'] or not re.fullmatch(contract['entry_path'],parts.path.rstrip('/')):continue
        title=re.sub(r'<[^>]+>',' ',unescape(title))
        title=re.sub(r'\s+',' ',title).strip(' *#>-')
        if not _is_named_label(title,url,parts):continue
        # Deduplicate by identity, but retain the provider's navigable URL.
        # Query parameters can be required for access (e.g. signed note links).
        identity = parts.path.rstrip('/')
        if pattern := contract.get('entry_identity_pattern'):
            match = re.search(pattern, identity)
            if not match or not match.groups():
                raise ValueError('entry_identity_pattern must match and capture an identity')
            identity = match[1]
        elif query_keys := set(contract.get('identity_query_keys', [])):
            identity += '?' + '&'.join(
                f'{key}={value}' for key, value in parse_qsl(parts.query)
                if key in query_keys
            )
        candidates[parts.hostname+'::'+identity]={'url':url,'title':title}
    return list(candidates.values())


def extract_linked_products(content, contract, base_url):
    """Extract title/link/price triples from one owned product segment."""
    scannable = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", content)
    link_pattern = re.compile(
        r"\[([^\]\n]+)\]\((https?://[^\s)]+|/[^\s)]+)\)"
        r"|<a\b[^>]*\bhref=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        re.I | re.S,
    )
    links = []
    for match in link_pattern.finditer(scannable):
        title = match.group(1) if match.group(1) is not None else match.group(4)
        raw_url = match.group(2) if match.group(2) is not None else match.group(3)
        url = urljoin(base_url, unescape(raw_url))
        parts = urlsplit(url)
        if (parts.hostname not in contract["hosts"]
                or not re.fullmatch(contract["entry_path"], parts.path.rstrip("/"))):
            continue
        title = re.sub(r"<[^>]+>", " ", unescape(title))
        title = re.sub(r"\s+", " ", title).strip(" *#>-")
        if len(title) < 2 or not any(character.isalpha() for character in title):
            continue
        identity = parts.hostname + parts.path.rstrip("/")
        query_keys = set(contract.get("identity_query_keys", []))
        if query_keys:
            identity += "?" + "&".join(
                f"{key}={value}" for key, value in parse_qsl(parts.query)
                if key in query_keys
            )
        links.append((match.start(), match.end(), identity, url, title))

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
    return list(candidates.values())


def evaluate_dynamic_list(content,contract,base_url,eligible=True,links=None):
    candidates=extract_named_entries(content,contract,base_url,links)
    enough=len(candidates)>=contract.get('minimum_entries',3)
    return {'structure_pass':bool(eligible and enough),'named_entry_count':len(candidates),
            'minimum_entries':contract.get('minimum_entries',3),'factual_correctness':None,
            'snapshot_f1':None,'dynamic_titles':[x['title'] for x in candidates],
            'verification_note':'仅验证标题与详情入口结构；没有同时间、同地区、同会话的独立参考，不判事实正确性或完整度。'}
