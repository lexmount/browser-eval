"""Provider-centred introduction and reproducible, sample-scoped adoption counts."""
import json
from pathlib import Path
from .methods import method_info
from .report_components import tabs,cards
from .report_interfaces import render_interfaces
from .report_capabilities import render_capabilities
from .report_pricing import render_pricing

PROFILES={
 'kitesurf':('远程账号 API','Cloudflare Browser Run 账户级 Markdown 端点，指定 browser=kitesurf，直接返回 Markdown。','https://developers.cloudflare.com/browser-run/kitesurf/'),
 'lightpanda':('本地浏览器 CLI','Lightpanda fetch --dump markdown，直接输出浏览器原生 Markdown。','https://github.com/lightpanda-io/browser'),
 'obscura':('本地浏览器 CLI','Obscura fetch --dump markdown；标准模式，不启用 stealth 或备用引擎。','https://github.com/h4ckf0r0day/obscura'),
 'moli-1.0.6':('本地浏览器 CLI','普通网页使用 fetch --dump markdown；直接文档和媒体使用裸 fetch。原始文档的本地正文解析计入耗时，文件任务直接校验字节。','https://github.com/lexmount/moli/releases/tag/v1.0.6'),
 'moli':('本地浏览器 CLI','普通网页使用 fetch --dump markdown；直接文档和媒体使用裸 fetch。原始文档的本地正文解析计入耗时，文件任务直接校验字节。','https://github.com/lexmount/moli/releases/tag/v1.1.5'),
 'moli-1.1.7':('本地浏览器 CLI','普通网页使用 fetch --dump markdown；直接文档和媒体使用裸 fetch。原始文档的本地正文解析计入耗时，文件任务直接校验字节。','https://github.com/lexmount/moli/releases/tag/v1.1.7'),
 'moli-1.1.3':('本地浏览器 CLI','普通网页使用 fetch --dump markdown；直接文档和媒体使用裸 fetch。原始文档的本地正文解析计入耗时，文件任务直接校验字节。','https://github.com/lexmount/moli/releases/tag/v1.1.3'),
 'chromium':('本地浏览器 CDP','Playwright 启动 Chromium，取完整渲染 DOM，再由 node-html-markdown 2.0.0 生成 Markdown。','https://playwright.dev/python/docs/browsers'),
 'moli-webfetch':('本地 CLI','Moli：普通正文用 Markdown，挑战题用 JSON 返回最终 DOM、状态和跳转诊断；动态等待与正文范围分离。DOCX 在同一次 CLI 运行中二次 fetch 原始字节；不调用模型。','https://github.com/lexmount/moli/tree/v1.1.4/skills/moli-webfetch'),
 'cowagent-built-in':('项目内置','CowAgent 内的 HTTP 抓取和文本、文档解析工具。','https://github.com/zhayujie/CowAgent/blob/main/agent/tools/web_fetch/web_fetch.py'),
 'lexmount-global':('托管 API','Lexmount 国际 API，返回网页主正文与元数据。','https://browser.lexmount.com/en/docs/webfetch'),
 'lexmount-cn':('托管 API','Lexmount 中国区 API；与国际 API 分开实测，不重复计算品牌采用。','https://browser.lexmount.com/en/docs/webfetch'),
 'firecrawl':('托管 API','云端 Scrape 为正文与资源题返回 Markdown/地址，原文件题用 rawBase64 交付目标字节。','https://docs.firecrawl.dev/features/scrape'),
 'tavily':('托管 API','本轮调用 Extract，从给定 URL 提取可读内容。','https://docs.tavily.com/documentation/api-reference/endpoint/extract'),
 'exa':('托管 API','本轮调用 Contents 取得指定 URL 的正文，不测试搜索。','https://exa.ai/docs/reference/contents-retrieval'),
 'cloudflare-markdown':('托管 API','云端浏览器加载网页后转换为 Markdown。','https://developers.cloudflare.com/browser-run/quick-actions/markdown-endpoint/'),
 'openclaw-built-in':('项目内置','本轮使用 HTTP + Readability；关闭 Firecrawl 回退，避免混成两个方法。','https://github.com/openclaw/openclaw/blob/main/docs/tools/web-fetch.md'),
 'deepseek-harness-built-in':('项目内置','本轮使用匿名 HTTP provider 与 Turndown 转换。','https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/web'),
 'langchain-webbase':('本地开源库','WebBaseLoader 使用 HTTP 与 BeautifulSoup 取得网页文字。','https://reference.langchain.com/python/langchain-community/document_loaders/web_base/WebBaseLoader'),
 'crawl4ai':('本地开源库','本地浏览器渲染，再抽取 Markdown、媒体及链接信息。','https://docs.crawl4ai.com/'),
}
DISPLAY_NAMES={'moli-1.0.6':'Moli 1.0.6','moli':'Moli 1.1.5','moli-1.1.3':'Moli 1.1.3','moli-1.1.7':'Moli 1.1.7'}
def display_name(name):return DISPLAY_NAMES.get(name,name)

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
    source=Path(__file__).resolve().parents[2]/'research/provider-adoption.json'
    data=json.loads(source.read_text());names=sorted({r['provider'] for r in records})
    counts=adoption_counts(data,sorted(set(PROFILES)|set(names)))
    dest=out/'adoption';dest.mkdir(exist_ok=True)
    (dest/'evidence.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    (dest/'counts.json').write_text(json.dumps({'snapshot':data['generated_at'],'scope':'签名命中线索，不是运行调用次数或全网采用量','methods':counts},ensure_ascii=False,indent=2)+'\n')
    intro=[];rows=[];details=''
    for name in names:
        kind,desc,url=PROFILES.get(name,('测试适配器','本地可重复测试实现。','https://example.com'));r=next(r for r in records if r['provider']==name);info=method_info(r['provider_type'])
        intro.append(('',cell(link('../providers/'+name+'.html',display_name(name)))+cell(kind)+cell(desc)+cell(esc(info['output']))+cell(esc(info['freshness']))+cell(link(url,'文档 / 源码 ↗'))))
    for item in counts:
        name=item['provider'];n=item['repository_count'];known=n is not None
        provider_label=link('../providers/'+name+'.html',display_name(name)) if name in names else link('#interface-'+name,display_name(name)+'（未纳入本轮）')
        rows.append(('',cell(provider_label)+cell(link('#evidence-'+name,str(n)) if known else '—',n if known else -1)+cell(str(item['file_count']) if known else '—',item['file_count'] if known else -1)))
        if known:
            evidence_rows=[('',cell(link(r['url'],r['repo']))+cell('<br>'.join(link(m['url'],m['path']) for m in r['matches']))) for r in item['repositories']]
            details+=f'<details id="evidence-{name}"><summary>{esc(name)} · {n} 个仓库</summary>'+table(['仓库','命中文件'],evidence_rows)+'</details>'
    stats=table(['方法','命中仓库数','命中文件数'],rows,'adoption-ranking',True,'ranking').replace('<table class="ranking"','<table class="ranking" data-col="2" data-dir="desc"')
    controls=data.get('implementation_controls',[])
    for control in controls:
        official='、'.join(link(m['url'],control['repo']) for m in control['matches'])
        details+=f'<p class="source-note">{esc(control["provider"])} 官方实现：{official}。'+('已纳入上述样本。' if control['included_in_count'] else '源码已核验；该官方仓库不符合当前星数 / 活跃度筛选条件，单列来源，不计入采用样本。')+'</p>'
    scope=cards([
        ('检索样本',f'{data["generated_at"][:10]} 核验：{len(data["signatures"])} 个签名，纳入 {len(data["included"])} 个仓库。候选集包括每签名前 {data["per_signature_cap"]} 条搜索结果和已登记的专用签名文件；按默认分支逐文件核验。要求 stars ≥ {data["minimum_stars"]:,}、近 7 天活跃且未归档。'),
        ('计数规则','同一方法在同一仓库命中多处，仓库数只加 1；同一文件命中多个签名，文件数只加 1。保留样本中的官方仓库、示例、文档和 fork，不冒充独立生产用户。'),
        ('不能推出什么','搜索结果有截断和排序偏差，计数是已核验候选集的下限，不是全网热度排名。0 只表示候选集中未发现符合条件的仓库；官方实现与第三方引用不同，示例、文档及源码命中也不证明生产采用。'),
        ('方法归属规则','逐一读取命中的 Git 文件并校验内容。通用 web_fetch 和同名函数不能证明归属；Exa 要求 Contents 端点或 SDK 导入及 get_contents。内置方法也包括源码复制与官方仓库，不能冒充第三方集成。'),
    ])+'<p>'+link('evidence.json','完整检索证据')+' · '+link('counts.json','逐方法统计数据')+'</p>'
    body=hero('方法介绍','参评方法的官方能力、价格、接口与开源采用；实测表现见各方法执行记录。')
    if records and all(r.get('provider_type') in {'headless_html','headless_markdown'} for r in records):
        body+=section('参评方法',cards([('远程正文接口','Kitesurf 使用 Cloudflare 账户级 Browser Run /markdown API；耗时包含公网往返、远端排队与执行。'),('本地原生产物','Lightpanda、Obscura 与 Moli 1.1.1 的普通网页使用 CLI 正文输出。Moli 的直接文档和媒体 URL 使用裸 fetch：正文任务在预算内解析，文件任务校验字节。'),('本地 DOM 转换','Chromium 152 由 Playwright 取完整渲染 DOM，再用 node-html-markdown 2.0.0 转换。五种方法都不调用模型。')]))
        body+=section('Kitesurf 能否本地部署',cards([('当前结论','截至 2026-09-07，Cloudflare 尚未发布 Kitesurf 源码或自行部署包；本轮只能通过 Cloudflare 账号版 Browser Run 远程测试，不能把它当作本地开源浏览器。'),('后续意向','Cloudflare 官方博客表示计划在准备好后开源，并希望允许客户部署到自己的账户。这是未来计划，不是当前已有能力。')])+f'<p class="source-note"><a href="https://developers.cloudflare.com/browser-run/kitesurf/">当前官方接入说明</a> · <a href="https://blog.cloudflare.com/kitesurf/">官方开源计划说明</a></p>')
    else:
        body+=section('参评方法',cards([('托管 API','Lexmount 两个区域、Firecrawl、Tavily、Exa、Cloudflare；通过各自 API 调用。'),('项目内置','CowAgent、OpenClaw、DeepSeek Harness；按本轮保留的实现与配置执行。'),('本地运行','LangChain WebBaseLoader、Crawl4AI、Moli WebFetch；使用本机网络与运行环境。')]))
    body+=section('能力、价格与接口',tabs('method-guide',[
        ('capabilities','官方能力',render_capabilities(out,names,table=table,cell=cell,link=link)),
        ('pricing','价格与计费',render_pricing(out,names,table=table,cell=cell,link=link,esc=esc)),
        ('adoption','采用统计',f'<p>{data["generated_at"][:10]} 核验的公开源码候选集，包括搜索结果与登记文件。计数是已核验下限，不是全网采用量；点击数字查看仓库与文件。</p>'+stats+details),
        ('profiles','方法详情','<p>下表介绍本轮实际调用的实现；方法名称可打开其全部已测案例。各组覆盖数量以执行记录为准，功能范围以本轮配置为准。</p>'+table(['方法','形态','做什么','本轮输出','本轮新鲜度设置','来源'],intro)),
        ('interfaces','接口与示例',render_interfaces(out,records)),
        ('scope','统计口径',scope),
    ]))
    return body
