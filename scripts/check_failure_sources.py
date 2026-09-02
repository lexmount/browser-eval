"""Bounded independent HTTP diagnostics for every source with a failed request."""
import datetime as dt
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'research/failure-sources.json'
def check(case):
    item={'case_id':case['id'],'url':case['url'],'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'method':'independent HTTP GET; at most 128 KiB'}
    try:
        with requests.get(case['url'],headers={'User-Agent':'Mozilla/5.0'},timeout=(10,25),stream=True) as r:
            raw=b''
            for chunk in r.iter_content(8192):
                raw+=chunk
                if len(raw)>=131072:break
            text=raw.decode(r.encoding or 'utf-8',errors='replace')
            title=re.search(r'<title[^>]*>(.*?)</title>',text,re.S|re.I)
            signals=[label for label,pattern in [('access_denied',r'access denied|prove your humanity|enable js and disable|just a moment'),('login',r'<title[^>]*>[^<]*(?:登录|sign in|signin)'),('captcha',r'<title[^>]*>[^<]*(?:验证|captcha)')] if re.search(pattern,text,re.I)]
            item.update(status=r.status_code,final_url=r.url,content_type=r.headers.get('content-type'),title=re.sub(r'\s+',' ',title[1])[:240] if title else '',access_signals=signals,prefix_sha256=hashlib.sha256(raw).hexdigest(),sampled_bytes=len(raw))
    except requests.RequestException as e:item.update(status=None,error_type=type(e).__name__,error=str(e)[:350])
    return item

if __name__=='__main__':
    d=json.loads((ROOT/'evaluation/dataset.json').read_text())
    failed={json.loads(l)['case_id'] for l in (ROOT/'evaluation/results.jsonl').read_text().splitlines() if not json.loads(l)['ok']}
    with ThreadPoolExecutor(max_workers=8) as pool:rows=list(pool.map(check,[c for c in d['cases'] if c['id'] in failed]))
    OUT.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
    print('Checked',len(rows),'failed-request sources')
