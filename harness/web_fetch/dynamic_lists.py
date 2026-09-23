"""Representation-neutral-ish list structure probe, NOT factual verification.

No saved names, ranks, prices or item IDs are compared. Correctness remains
unverified without an independent, contemporaneous reference in the same context.
"""
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
    base_url = contract.get("link_base_url", base_url)
    parser=Links();parser.feed(content)
    from .response_input import safe_named_links
    from .reference_quality import markdown_links, visible_markdown_links
    items=list(parser.items) + safe_named_links(links, base_url)
    for link in markdown_links(content):
        destination=link.destination.strip()
        target=urlsplit(destination)
        if (link.is_image or not destination or destination.startswith(('#','?'))
                or (target.scheme and target.scheme not in {'http','https'})):
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
                and not re.fullmatch(r'[\s#>*\-\d.)|]*',prefix)):
            continue
        non_image_label=link.label
        for nested in reversed(markdown_links(link.label)):
            if nested.is_image:
                non_image_label=non_image_label[:nested.start]+non_image_label[nested.end:]
        paired_heading=None
        if not re.search(r'[\w\u3400-\u9fff]',non_image_label):
            image_label=visible_markdown_links(link.label).strip()
            tail=content[link.end:]
            boundary=len(tail)
            for following in markdown_links(tail):
                candidate=urlsplit(urljoin(base_url,unescape(following.destination)))
                if (candidate.hostname in contract['hosts']
                        and re.fullmatch(contract['entry_path'],candidate.path.rstrip('/'))):
                    boundary=following.start
                    break
            headings=re.findall(r'(?m)^\s*#{2,6}\s+(.+?)\s*$',tail[:boundary])
            if len(headings)==1 and _label_key(headings[0])==_label_key(image_label):
                paired_heading=headings[0].strip()
                non_image_label=paired_heading
        if not re.search(r'[\w\u3400-\u9fff]',non_image_label):
            continue
        title=re.sub(r'\s+',' ',paired_heading or visible_markdown_links(link.label)).strip()
        items.append((link.destination,title))
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
        identity_keys = contract.get('identity_query_keys', [])
        if identity_keys:
            query_values = {key: [] for key in identity_keys}
            for key, value in parse_qsl(parts.query, keep_blank_values=True):
                if key in query_values:
                    query_values[key].append(value)
            present = {key: values for key, values in query_values.items() if values}
            if (not present or any(len(values) != 1 or not values[0].strip()
                                   for values in present.values())):
                continue
            identity += '?' + urlencode(sorted(
                (key, values[0]) for key, values in present.items()
            ))
        candidates[parts.hostname+'::'+identity]={'url':url,'title':title}
    return list(candidates.values())


def evaluate_dynamic_list(content,contract,base_url,eligible=True,links=None):
    candidates=extract_named_entries(content,contract,base_url,links)
    enough=len(candidates)>=contract.get('minimum_entries',3)
    return {'structure_pass':bool(eligible and enough),'named_entry_count':len(candidates),
            'minimum_entries':contract.get('minimum_entries',3),'factual_correctness':None,
            'snapshot_f1':None,'dynamic_titles':[x['title'] for x in candidates],
            'verification_note':'仅验证标题与详情入口结构；没有同时间、同地区、同会话的独立参考，不判事实正确性或完整度。'}
