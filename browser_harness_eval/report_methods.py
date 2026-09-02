"""Provider-centred introduction and reproducible, sample-scoped adoption counts."""
import json
from pathlib import Path
from .methods import method_info
from .report_components import tabs,cards
from .report_interfaces import render_interfaces
from .report_capabilities import render_capabilities

PROFILES={
 'cowagent-built-in':('项目内置','CowAgent 内的 HTTP 抓取和文本、文档解析工具。','https://github.com/zhayujie/CowAgent/blob/main/agent/tools/web_fetch/web_fetch.py'),
 'lexmount-global':('托管 API','Lexmount 国际 API，返回网页主正文与元数据。','https://browser.lexmount.com/en/docs/webfetch'),
 'lexmount-cn':('托管 API','Lexmount 中国区 API；与国际 API 分开实测，不重复计算品牌采用。','https://browser.lexmount.com/en/docs/webfetch'),
 'firecrawl':('托管 API','本轮调用云端 Scrape，将网页或文档转换为 Markdown。','https://docs.firecrawl.dev/features/scrape'),
 'tavily':('托管 API','本轮调用 Extract，从给定 URL 提取可读内容。','https://docs.tavily.com/documentation/api-reference/endpoint/extract'),
 'exa':('托管 API','本轮调用 Contents 取得指定 URL 的正文，不测试搜索。','https://exa.ai/docs/reference/contents-retrieval'),
 'cloudflare-markdown':('托管 API','云端浏览器加载网页后转换为 Markdown。','https://developers.cloudflare.com/browser-run/quick-actions/markdown-endpoint/'),
 'openclaw-built-in':('项目内置','本轮使用 HTTP + Readability；关闭 Firecrawl 回退，避免混成两个方法。','https://github.com/openclaw/openclaw/blob/main/docs/tools/web-fetch.md'),
 'deepseek-harness-built-in':('项目内置','本轮使用匿名 HTTP provider 与 Turndown 转换。','https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/web'),
 'langchain-webbase':('本地开源库','WebBaseLoader 使用 HTTP 与 BeautifulSoup 取得网页文字。','https://reference.langchain.com/python/langchain-community/document_loaders/web_base/WebBaseLoader'),
 'crawl4ai':('本地开源库','本地浏览器渲染，再抽取 Markdown、媒体及链接信息。','https://docs.crawl4ai.com/'),
}

def adoption_counts(data,names):
    result=[]
    for name in names:
        configured=data['provider_signatures'].get(name)
        sigs=set(configured) if configured is not None else None
        repos={};files=set()
        if sigs is not None:
            for row in data['included']:
                hits=[m for m in row.get('matches',[]) if m['signature'] in sigs]
                if not hits:continue
                repo=row['repo'].lower()
                target=repos.setdefault(repo,{'repo':row['repo'],'url':row['url'],'matches':[]})
                for m in hits:
                    key=(repo,m['path'])
                    if key not in files:target['matches'].append(m)
                    files.add(key)
        result.append({'provider':name,'repository_count':len(repos) if sigs is not None else None,'file_count':len(files) if sigs is not None else None,'signatures':sorted(sigs or []),'repositories':list(repos.values())})
    return sorted(result,key=lambda x:(x['repository_count'] is None,-(x['repository_count'] or 0),x['provider']))

def render_methods(out,records,*,hero,section,table,cell,link,esc):
    source=Path(__file__).resolve().parents[1]/'research/provider-adoption.json'
    data=json.loads(source.read_text());names=sorted({r['provider'] for r in records})
    counts=adoption_counts(data,names)
    dest=out/'adoption';dest.mkdir(exist_ok=True)
    (dest/'evidence.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    (dest/'counts.json').write_text(json.dumps({'snapshot':data['generated_at'],'scope':'签名命中线索，不是运行调用次数或全网采用量','methods':counts},ensure_ascii=False,indent=2)+'\n')
    intro=[];rows=[];details=''
    for name in names:
        kind,desc,url=PROFILES.get(name,('测试适配器','本地可重复测试实现。','https://example.com'));r=next(r for r in records if r['provider']==name);info=method_info(r['provider_type'])
        intro.append(('',cell(link('../providers/'+name+'.html',name))+cell(kind)+cell(desc)+cell(esc(info['output']))+cell(esc(info['freshness']))+cell(link(url,'文档 / 源码 ↗'))))
    for item in counts:
        name=item['provider'];n=item['repository_count'];known=n is not None
        rows.append(('',cell(link('../providers/'+name+'.html',name))+cell(link('#evidence-'+name,str(n)) if known else '—',n if known else -1)+cell(str(item['file_count']) if known else '—',item['file_count'] if known else -1)))
        if known:
            evidence_rows=[('',cell(link(r['url'],r['repo']))+cell('<br>'.join(link(m['url'],m['path']) for m in r['matches']))) for r in item['repositories']]
            details+=f'<details id="evidence-{name}"><summary>{esc(name)} · {n} 个仓库</summary>'+table(['仓库','命中文件'],evidence_rows)+'</details>'
    stats=table(['方法','命中仓库数','命中文件数'],rows,'adoption-ranking',True,'ranking').replace('<table class="ranking"','<table class="ranking" data-col="2" data-dir="desc"')
    controls=data.get('implementation_controls',[])
    for control in controls:
        official='、'.join(link(m['url'],control['repo']) for m in control['matches'])
        details+=f'<p class="source-note">{esc(control["provider"])} 官方实现：{official}。'+('已纳入上述样本。' if control['included_in_count'] else '源码已核验；该官方仓库不符合当前星数 / 活跃度筛选条件，单列来源，不计入采用样本。')+'</p>'
    scope=cards([
        ('检索样本',f'{data["generated_at"][:10]} 核验：{len(data["signatures"])} 个签名，纳入 {len(data["included"])} 个仓库。以每签名前 {data["per_signature_cap"]} 条搜索结果发现候选，并保留历史已发现的专用签名文件；统一刷新默认分支并逐文件核验。要求 stars ≥ {data["minimum_stars"]:,}、近 7 天活跃且未归档。'),
        ('计数规则','同一方法在同一仓库命中多处，仓库数只加 1；同一文件命中多个签名，文件数只加 1。保留样本中的官方仓库、示例、文档和 fork，不冒充独立生产用户。'),
        ('不能推出什么','搜索结果有截断和排序偏差，计数是已核验候选集的下限，不是全网热度排名。0 只表示候选集中未发现符合条件的仓库；官方实现与第三方引用不同，示例、文档及源码命中也不证明生产采用。'),
        ('方法归属规则','逐一读取命中的 Git 文件并校验内容。通用 web_fetch 和同名函数不能证明归属；Exa 要求 Contents 端点或 SDK 导入及 get_contents。内置方法也包括源码复制与官方仓库，不能冒充第三方集成。'),
    ])+'<p>'+link('evidence.json','完整检索证据')+' · '+link('counts.json','逐方法统计数据')+'</p>'
    body=hero('方法介绍','先了解参评方法，再看开源采用线索与本轮实测。采用热度不代替质量成绩。')
    body+=section('参评方法',cards([('托管 API','Lexmount 两个区域、Firecrawl、Tavily、Exa、Cloudflare；通过各自 API 调用。'),('项目内置','CowAgent、OpenClaw、DeepSeek Harness；按本轮保留的实现与配置执行。'),('本地开源库','LangChain WebBaseLoader、Crawl4AI；使用本机网络与运行环境。')]))
    body+=section('能力、采用与接口',tabs('method-guide',[
        ('capabilities','官方能力',render_capabilities(out,names,table=table,cell=cell,link=link)),
        ('adoption','采用统计',f'<p>{data["generated_at"][:10]} 核验的公开源码候选集；历史已发现的引用不会因搜索排序变化被丢弃。计数是已核验下限，不是全网采用量；点击数字查看仓库与文件。</p>'+stats+details),
        ('profiles','方法详情','<p>下表介绍本轮实际调用的实现；方法名称可打开其 140 个案例结果。功能范围以本轮配置为准。</p>'+table(['方法','形态','做什么','本轮输出','本轮新鲜度设置','来源'],intro)),
        ('interfaces','接口与示例',render_interfaces(out,records)),
        ('scope','统计口径',scope),
    ]))
    return body
