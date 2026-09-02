"""Public-source adoption survey for every measured method; failed searches are never zeroes."""
import datetime as dt
import json
import time
from collections import defaultdict
from pathlib import Path
from discover_webfetch_adopters import gh, metadata_batch

ROOT=Path(__file__).resolve().parents[1]
QUERIES={
 'firecrawl':{'firecrawl-sdk':'"FirecrawlApp"','firecrawl-api':'"api.firecrawl.dev/v2/scrape"'},
 'tavily':{'tavily-extract':'"api.tavily.com/extract"','tavily-sdk-extract':'"tavily_client.extract"'},
 'exa':{'exa-contents':'"api.exa.ai/contents"','exa-sdk-contents':'"get_contents" "exa"'},
 'cloudflare-markdown':{'cloudflare-markdown':'"browser-rendering/markdown"'},
 'langchain-webbase':{'langchain-web-base-loader':'"WebBaseLoader"'},
 'crawl4ai':{'crawl4ai':'"AsyncWebCrawler"'},
 'cowagent-built-in':{'cowagent-webfetch-import':'"agent.tools.web_fetch.web_fetch"','cowagent-webfetch-source':'"ALL_DOC_SUFFIXES" "WebFetch"'},
 'openclaw-built-in':{'openclaw-webfetch-source':'"createWebFetchTool"'},
 'deepseek-harness-built-in':{'dsh-webfetch-module':'"@deepseek-ai/dsh-web-fetch-http"'},
 'lexmount-global':{'lexmount-global-extract':'"api.lexmount.com" "extract"'},
 'lexmount-cn':{'lexmount-cn-extract':'"api.lexmount.cn" "extract"'},
}

def main():
    now=dt.datetime.now(dt.timezone.utc);cutoff=now-dt.timedelta(days=7)
    dest=ROOT/'research/provider-adoption.json';checkpoint=ROOT/'research/adoption-searches.json'
    previous=json.loads(dest.read_text()) if dest.exists() else None
    searches=[];hits=defaultdict(list)
    for provider,queries in QUERIES.items():
        for signature,query in queries.items():
            result=gh('search/code','-X','GET','-f','q='+query,'-f','per_page=100')
            if result.get('incomplete_results'):raise RuntimeError('Incomplete search: '+query)
            if signature=='firecrawl-sdk' and not result.get('total_count'):raise RuntimeError('Known-positive control failed; do not publish zero counts')
            items=[{'repo':x['repository']['full_name'],'path':x['path'],'url':x['html_url'],'blob_sha':x['sha']} for x in result['items'] if not x['repository'].get('private')]
            searches.append({'provider':provider,'signature':signature,'query':query,'searched_at':dt.datetime.now(dt.timezone.utc).isoformat(),'total_count':result['total_count'],'returned':len(items),'cap':100,'items':items})
            for item in items:hits[item['repo']].append({**item,'signature':signature,'provider':provider})
            checkpoint.write_text(json.dumps(searches,ensure_ascii=False,indent=2)+'\n')
            print(provider,signature,result['total_count'],len(items),flush=True)
            time.sleep(7)
    names=sorted(hits);metadata={}
    for offset in range(0,len(names),30):metadata.update(metadata_batch(names[offset:offset+30]))
    included=[]
    for repo in names:
        m=metadata.get(repo,{})
        target=(m.get('defaultBranchRef') or {}).get('target') or {}
        stamp=target.get('committedDate')
        if not stamp or m.get('isArchived') or m.get('stargazerCount',0)<1000:continue
        if dt.datetime.fromisoformat(stamp.replace('Z','+00:00'))<cutoff:continue
        included.append({'repo':repo,'url':m['url'],'stars':m['stargazerCount'],'is_fork':m['isFork'],'default_branch_head_at':stamp,'default_branch_head':target['oid'],'matches':hits[repo]})
    result={'generated_at':now.isoformat(),'cutoff':cutoff.isoformat(),'minimum_stars':1000,'per_signature_cap':100,'unique_repositories_examined':len(names),'signatures':{k:v for q in QUERIES.values() for k,v in q.items()},'provider_signatures':{p:list(q) for p,q in QUERIES.items()},'searches':searches,'included':included,'scope':'公开默认分支的专用源码/导入/接口签名；每查询前100条，stars≥1000且默认分支7天内活跃。含官方仓库、fork、示例和文档，不代表生产用户或全网采用量。'}
    from refresh_adoption_candidates import refresh
    refresh(previous_snapshot=previous,discovered=result)
    print('COMPLETE',len(QUERIES),'providers',len(included),'included repositories',flush=True)

if __name__=='__main__':main()
