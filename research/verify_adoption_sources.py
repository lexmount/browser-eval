"""Confirm search hits against the returned immutable Git blobs, not search counts."""
import base64,hashlib,json,re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from discover_webfetch_adopters import gh

ROOT=Path(__file__).resolve().parents[1]

def matches(signature,text,repo):
    rules={
      'firecrawl-sdk':lambda:'FirecrawlApp' in text,
      'firecrawl-api':lambda:'api.firecrawl.dev/v2/scrape' in text,
      'tavily-extract':lambda:'api.tavily.com/extract' in text,
      'tavily-sdk-extract':lambda:'tavily_client.extract' in text,
      'exa-contents':lambda:'api.exa.ai/contents' in text,
      'exa-sdk-contents':lambda:bool(re.search(r'(?:from exa_py import|import.*exa|Exa\()',text)) and 'get_contents' in text,
      'cloudflare-markdown':lambda:'browser-rendering/markdown' in text,
      'langchain-web-base-loader':lambda:'WebBaseLoader' in text,
      'crawl4ai':lambda:'AsyncWebCrawler' in text,
      'cowagent-webfetch-import':lambda:'agent.tools.web_fetch.web_fetch' in text,
      'cowagent-webfetch-source':lambda:'ALL_DOC_SUFFIXES' in text and 'WebFetch' in text,
      # The common createWebFetchTool name alone also occurs in unrelated tools.
      'openclaw-webfetch-source':lambda:'createWebFetchTool' in text and ('resolveWebFetchConfig' in text or ('openclaw' in text.lower() and ('import' in text or 'require(' in text))),
      'dsh-webfetch-module':lambda:'@deepseek-ai/dsh-web-fetch-http' in text,
      'lexmount-global-extract':lambda:'api.lexmount.com' in text and '/v1/extract' in text,
      'lexmount-cn-extract':lambda:'api.lexmount.cn' in text and '/v1/extract' in text,
    }
    return rules[signature]()

def main():
    path=ROOT/'research/provider-adoption.json';data=json.loads(path.read_text())
    jobs={(r['repo'],m['blob_sha']):None for r in data['included'] for m in r['matches']}
    def fetch(key):
        repo,sha=key;blob=gh(f'repos/{repo}/git/blobs/{sha}')
        assert blob['encoding']=='base64'
        raw=base64.b64decode(blob['content']);assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==sha
        return key,raw.decode('utf-8',errors='replace')
    texts=dict(ThreadPoolExecutor(max_workers=6).map(fetch,jobs))
    rejected=[]
    for r in data['included']:
        kept=[]
        for m in r['matches']:
            text=texts[(r['repo'],m['blob_sha'])]
            if matches(m['signature'],text,r['repo']):
                m['verified_blob_sha256']=hashlib.sha256(text.encode()).hexdigest();kept.append(m)
            else:rejected.append(m)
        r['matches']=kept
    data['included']=[r for r in data['included'] if r['matches']]
    data['rejected_search_hits']=rejected
    data['verification']='Returned Git blob SHA verified; provider-specific literal and attribution checks applied to every counted file.'
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    print('Verified',len(jobs),'blobs;',len(rejected),'unattributable hits excluded;',len(data['included']),'repositories')

if __name__=='__main__':main()
