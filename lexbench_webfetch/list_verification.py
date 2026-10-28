"""Bounded, independently evidenced name-to-detail verification, not list recall."""
import re
import unicodedata
from urllib.parse import urlsplit
from .dynamic_lists import extract_named_entries


def identity(url):
    p=urlsplit(url);host=(p.hostname or '').removeprefix('www.')
    path=p.path.rstrip('/')
    if host=='amazon.com':
        m=re.search(r'/dp/([A-Z0-9]{10})(?:/|$)',path)
        if m:return host+'/dp/'+m[1]
    if host=='reddit.com':
        m=re.search(r'(/r/[^/]+/comments/[^/]+)',path)
        if m:return host+m[1]
    return host+path


def normalize(title):
    text=unicodedata.normalize('NFKC',title).casefold()
    text=re.sub(r'^\s*\d+[.)、]\s*','',text)
    # Observed card badges, not arbitrary words before the actual name.
    text=re.sub(r'^(?:top rated\s*|free\s+)','',text)
    return re.sub(r'[^\w\s]',' ',text).strip()


def name_matches(title,reference):
    # Exact words/characters with harmless punctuation/rank formatting removed.
    # No edit-distance guessing; unknown aliases require independent evidence.
    a=' '.join(normalize(title).split());b=' '.join(normalize(reference).split())
    if not a or not b:return False
    return a==b or (len(b)>=5 and a.startswith(b+' '))


def verify_list(content,contract,url,reference,eligible=True,links=None):
    entries=[];seen=set()
    for item in extract_named_entries(content,contract,url,links):
        key=identity(item['url'])
        if key not in seen:entries.append(item);seen.add(key)
    required=contract['minimum_entries'];selected=entries[:required]
    index={}
    for item in reference.get('items',[]):
        if item.get('url') and item.get('title'):
            index.setdefault(identity(item['url']),[]).append(item)
    checks=[]
    access={identity(x['url']):x for x in reference.get('access_checks',[])}
    for item in selected:
        candidates=index.get(identity(item['url']),[])
        match=next((x for x in candidates if name_matches(item['title'],x['title'])),None)
        observation=access.get(identity(item['url']),{})
        # Name differences can be aliases or changes: never turn absence of
        # corroboration into a claim that the provider fabricated content.
        checks.append({**item,'verdict':'confirmed' if match else 'unconfirmed',
                       'reference_titles':[x['title'] for x in candidates],
                       'evidence_url':(match or {}).get('url'),
                       'evidence_checked_at':(match or observation).get('checked_at',reference.get('checked_at')),
                       'access_restricted':bool(not match and observation.get('restriction')),
                       'access_note':observation.get('restriction_message','') if not match else ''})
    confirmed=sum(x['verdict']=='confirmed' for x in checks)
    return {'list_task_pass':bool(eligible and len(selected)==required and confirmed==required),
            'list_checks':checks,'confirmed_entries':confirmed,'checked_entries':len(checks),
            'list_confirmation_rate':confirmed/len(checks) if checks else None,
            'list_field_coverage':min(len(entries),required)/required if eligible else 0,
            'list_reference':reference.get('case_id'),
            'verification_note':'核对返回顺序中的前三个不同条目的名称与详情链接；不测实时排名、价格或整页召回。未确认不等于内容错误。'}
