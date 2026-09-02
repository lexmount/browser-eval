"""Independent source capture; never reads candidate/provider outputs.

Capture is evidence, NOT automatic approval of ground truth. Review selectors,
text and failures before freezing a dataset. Bounded threads isolate requests.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
import requests
from bs4 import BeautifulSoup


def extract_html(raw: bytes, url: str):
    soup = BeautifulSoup(raw, 'html.parser')
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    resources = []
    for node in soup.select('img[src], video[src], video source[src], iframe[src], embed[src], object[data]'):
        resources.append({'tag': node.name, 'url': urljoin(url, node.get('src') or node.get('data')), 'alt': node.get('alt', '')})
    selectors = ['article.markdown-body', '#mw-content-text .mw-parser-output', '#mainContent', '#main-content', 'main', '[role=main]', '.document .body', 'article', 'body']
    host = urlsplit(url).hostname or ''
    if host == 'books.toscrape.com':
        selectors = ['.col-sm-8.col-md-9', '.page_inner', 'body']
    elif host.endswith('rfc-editor.org') or host.endswith('w3.org') or host == 'fetch.spec.whatwg.org':
        selectors = ['body']
    elif host.endswith('w3schools.com'):
        selectors = ['#main', 'body']
    selector, root = next(((s, soup.select_one(s)) for s in selectors if soup.select_one(s)), ('body', soup))
    for node in root.select('script,style,noscript,nav,footer,[aria-hidden=true],.mw-editsection,.toc,.vector-toc,.sidebar,.sphinxsidebar'):
        node.decompose()
    text = '\n'.join(line.strip() for line in root.get_text('\n').splitlines() if line.strip())
    return title, text, selector, resources


def capture(case, out):
    dest = out / case['id']
    dest.mkdir(parents=True, exist_ok=True)
    result = {'case_id':case['id'], 'url':case['url'], 'checked_at':datetime.now(timezone.utc).isoformat(), 'source':'independent HTTP GET (no provider output)', 'attempts':[]}
    try:
        for attempt in range(2):
            try:
                r = requests.get(case['url'], timeout=(12,35), headers={'User-Agent':'Mozilla/5.0 (compatible; WebFetchEvaluation/1.0)'})
                result['attempts'].append({'status':r.status_code, 'url':r.url})
                if r.status_code < 500:
                    break
            except requests.RequestException as e:
                result['attempts'].append({'error':str(e)[:400]})
                if attempt: raise
        raw = r.content
        mime = r.headers.get('Content-Type','')
        result.update(status=r.status_code, final_url=r.url, mime=mime, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),response_headers={k:r.headers[k] for k in ['X-Frame-Options','Content-Security-Policy','Content-Type'] if k in r.headers})
        suffix = '.html' if 'html' in mime else '.pdf' if raw.startswith(b'%PDF') else '.docx' if raw.startswith(b'PK') else '.bin'
        (dest / ('source'+suffix)).write_bytes(raw)
        if suffix == '.pdf':
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            text = '\n\n'.join(p.extract_text() or '' for p in reader.pages)
            result.update(title=case['name'], selector='PDF all pages', page_count=len(reader.pages), resources=[])
        elif suffix == '.docx':
            text = extract_docx(raw)
            result.update(title=case['name'], selector='DOCX XML: document paragraphs including nested tables/textboxes, footnotes/endnotes, headers/footers', resources=[])
        elif 'html' in mime:
            title,text,selector,resources = extract_html(raw,r.url)
            result.update(title=title,selector=selector,resources=resources)
        elif mime.startswith('text/'):
            text = r.content.decode('utf-8',errors='replace')
            result.update(title=case['name'],selector='entire text file',resources=[])
        else:
            text = ''
            result.update(title=case['name'],selector='binary file',resources=[])
        (dest/'reference.txt').write_text(text,encoding='utf-8')
        result['text_chars'] = len(text)
        result['excerpt'] = text[:800]
        result['missing_facts'] = [f['id'] for f in case.get('required_facts',[]) if not any(re.sub(r'\s+',' ',a).casefold() in re.sub(r'\s+',' ',text).casefold() for a in f['alternatives'])]
        result['state'] = 'captured' if r.status_code == 200 else 'needs_review'
    except Exception as e:
        result.update(state='needs_review',error=f'{type(e).__name__}: {e}')
    (dest/'evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(case['id'], result.get('status'), result.get('text_chars'), result.get('missing_facts'), flush=True)
    return result


def extract_docx(raw):
    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        parts=['word/document.xml']+sorted(n for n in z.namelist() if re.fullmatch(r'word/(footnotes|endnotes|header\d+|footer\d+)\.xml',n))
        paragraphs=[]
        def text_nodes(node):
            if node.tag == '{'+ns['w']+'}t':
                yield node.text or ''
            elif node.tag in {'{'+ns['w']+'}tab','{'+ns['w']+'}br'}:
                yield ' '
            for child in node:
                yield from text_nodes(child)
            if node.tag == '{'+ns['w']+'}p':
                yield '\n'
        for name in parts:
            root=ET.fromstring(z.read(name))
            # Walk once: text boxes contain nested paragraphs and must not repeat.
            paragraphs.extend(line for line in ''.join(text_nodes(root)).splitlines() if line.strip())
        return '\n'.join(paragraphs)


if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('dataset',type=Path);p.add_argument('output',type=Path);p.add_argument('--workers',type=int,default=8);p.add_argument('--only',default='')
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    cases=json.loads(args.dataset.read_text())['cases']
    if args.only:
        cases=[c for c in cases if c['id'] in args.only.split(',')]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results=list(pool.map(lambda c:capture(c,args.output),cases))
    (args.output/'audit.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
