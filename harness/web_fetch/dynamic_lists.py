"""Representation-neutral-ish list structure probe, NOT factual verification.

No saved names, ranks, prices or item IDs are compared. Correctness remains
unverified without an independent, contemporaneous reference in the same context.
"""
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin,urlsplit


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


def extract_named_entries(content,contract,base_url,links=None):
    parser=Links();parser.feed(content)
    from .response_input import safe_named_links
    items=list(parser.items) + safe_named_links(links, base_url)
    # Resolve image labels before parsing the surrounding link, not afterwards.
    markdown=re.sub(r'!\[([^]]*)\]\([^)]*\)',r'\1',content)
    # A single soft break is legal inside an inline link label. Blank lines
    # remain block boundaries and must not join unrelated cards.
    markdown=re.sub(r'\[([^\[\]]*\n[^\[\]]*)\]\((https?://[^\s)]+|/[^\s)]+)\)',
        lambda m: m[0] if re.search(r'\n\s*\n', m[1]) else '['+re.sub(r'\s+', ' ', m[1])+']('+m[2]+')', markdown)
    for line in markdown.splitlines():
        for match in re.finditer(r'\[([^\n]+?)\]\((https?://[^\s)]+|/[^\s)]+)\)',line):
            # Prose recommendations are not list entries. Allow headings, bullets
            # and ranks before a card link, but not a preceding sentence.
            prefix=line[:match.start()].strip()
            if prefix and not re.fullmatch(r'[\s#>*\-\d.)|]*',prefix):continue
            items.append((match[2],match[1]))
    lines=content.splitlines()
    for i,line in enumerate(lines):
        if re.fullmatch(r'https?://\S+',line.strip()) and i:
            items.append((line.strip(),lines[i-1].strip()))
    candidates={}
    for url,title in items:
        url=urljoin(base_url,unescape(url));parts=urlsplit(url)
        if parts.hostname not in contract['hosts'] or not re.fullmatch(contract['entry_path'],parts.path.rstrip('/')):continue
        title=re.sub(r'<[^>]+>',' ',unescape(title))
        title=re.sub(r'\s+',' ',title).strip(' *#>-')
        if len(title)<2 or title.startswith(('http://','https://')):continue
        if not any(c.isalpha() for c in title):continue
        if re.fullmatch(r'(详情|查看|更多|评论|分享|图片|点击|了解更多|TV|Free TV|Watch|Details|Read more|View more|\d+)',title,re.I):continue
        # Deduplicate by identity, but retain the provider's navigable URL.
        # Query parameters can be required for access (e.g. signed note links).
        candidates[parts.hostname+parts.path.rstrip('/')]={'url':url,'title':title}
    return list(candidates.values())


def evaluate_dynamic_list(content,contract,base_url,eligible=True,links=None):
    candidates=extract_named_entries(content,contract,base_url,links)
    enough=len(candidates)>=contract.get('minimum_entries',3)
    return {'structure_pass':bool(eligible and enough),'named_entry_count':len(candidates),
            'minimum_entries':contract.get('minimum_entries',3),'factual_correctness':None,
            'snapshot_f1':None,'dynamic_titles':[x['title'] for x in candidates],
            'verification_note':'仅验证标题与详情入口结构；没有同时间、同地区、同会话的独立参考，不判事实正确性或完整度。'}
