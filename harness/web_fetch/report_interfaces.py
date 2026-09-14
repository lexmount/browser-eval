"""Interfaces actually used by the benchmark, not a claim to cover entire SDKs."""
import json
from html import escape
from .safety import contained_path

URL='https://example.com/'
HEADLESS_ACCESS={
 'kitesurf':{
  'account':'需要开通 Browser Rendering / Browser Run 的 Cloudflare 账号及 API Token。',
  'configuration':'在仓库本地 .env 配置 <ACCOUNT_ID> 与 <API_TOKEN>；报告和示例只保留占位符。',
  'env_vars':['CLOUDFLARE_ACCOUNT_ID','CLOUDFLARE_API_TOKEN'],
  'links':[('Cloudflare 控制台','https://dash.cloudflare.com/'),('Kitesurf 接入说明','https://developers.cloudflare.com/browser-run/kitesurf/'),('Markdown 端点','https://developers.cloudflare.com/browser-run/quick-actions/markdown-endpoint/')],
 },
 'lightpanda':{'account':'不需要供应商账号或 API Key。','configuration':'从官方项目获取二进制，配置 LIGHTPANDA_BINARY；不使用托管服务凭据。','env_vars':['LIGHTPANDA_BINARY'],'links':[('官方项目与安装说明','https://github.com/lightpanda-io/browser')]},
 'obscura':{'account':'不需要供应商账号或 API Key。','configuration':'从官方项目获取程序及配套 worker，配置 OBSCURA_BINARY。','env_vars':['OBSCURA_BINARY'],'links':[('官方项目与安装说明','https://github.com/h4ckf0r0day/obscura')]},
 'moli-1.0.6':{'account':'不需要供应商账号或 API Key。','configuration':'从官方 v1.0.6 release 获取二进制，配置 MOLI_1_0_6_BINARY；发布归档与可执行文件均校验 SHA-256。','env_vars':['MOLI_1_0_6_BINARY'],'links':[('Moli 1.0.6 正式发布','https://github.com/lexmount/moli/releases/tag/v1.0.6')]},
 'moli':{'account':'不需要供应商账号或 API Key。','configuration':'从官方 v1.1.1 release 获取二进制，配置 MOLI_BINARY；不是 Lexmount 云服务接口。','env_vars':['MOLI_BINARY'],'links':[('Moli 1.1.1 正式发布','https://github.com/lexmount/moli/releases/tag/v1.1.1')]},
 'moli-1.1.3':{'account':'不需要供应商账号或 API Key。','configuration':'从官方 v1.1.3 release 获取二进制，配置 MOLI_1_1_3_BINARY；发布包与可执行文件均校验 SHA-256。','env_vars':['MOLI_1_1_3_BINARY'],'links':[('Moli 1.1.3 正式发布','https://github.com/lexmount/moli/releases/tag/v1.1.3')]},
 'chromium':{'account':'不需要 Google 账号或 API Key；不加载个人登录会话。','configuration':'通过 Playwright 安装匹配的 Chromium，配置 CHROMIUM_BINARY。','env_vars':['CHROMIUM_BINARY'],'links':[('官方浏览器安装说明','https://playwright.dev/python/docs/browsers')]},
}
def spec(entry,auth,params,fields,request,source,notes):
 return dict(entry=entry,auth=auth,params=params,fields=fields,request=request,source=source,notes=notes)
SPECS={
 'moli-webfetch':spec('CLI · 按任务预选 Markdown、JSON 诊断、DOM eval 或裸响应路线','本地二进制；无 API Key',
 [('URL','string · 必填','公开 HTTP(S) 页面或直接文件'),('--dump markdown','普通正文','直接取得 Markdown'),('--eval','动态 / 资源页 / 嵌入文档','一次返回有界记录文本、结构化 URL 或页面运行时文档'),('--wait-until / --wait-selector / --wait-script','string','使用预声明的生命周期或动态完成条件'),('--timeout / --http-timeout','number','毫秒；导航、HTTP、进程与本地文档解析共享同一端到端预算'),('--block-private-networks','flag','默认启用，阻止私网地址')],
 [('stdout','UTF-8 string / bytes','Markdown、eval 结果或直接文件原始字节'),('stderr','string','独立诊断；不混入正文'),('exit_code','integer','0 且交付非空表示工具执行成功，任务另按规则判定'),('status_code / final_url','可能未提供','CLI 未提供时保持未知，不伪造 HTTP 200 或最终 URL')],
 {'ordinary_body':'moli fetch --dump markdown … URL','challenge':'moli fetch --dump json --trace-network … URL → 最终 DOM 本地转 Markdown','dynamic_or_resource_page':'moli fetch --eval <answer-free expression> <bounded wait> … URL','direct_document_or_media':'moli fetch … URL'},
 'harness/web_fetch/providers/moli.py','普通正文使用 Markdown；动态、资源页和嵌入文档使用一次有界 DOM eval；直接 PDF、DOCX 与媒体文件在调用前选择不带 --dump 和页面等待参数的原始响应路线。直接文档正文在同一截止预算内解析，文件任务按统一字节规则验收；不在失败后切换格式或借用其他 provider。'),
 'lexmount-global':spec('POST https://api.lexmount.com/v1/extract','X-API-Key: <API_KEY>；X-Project-Id: <PROJECT_ID>',
 [('extract.url','string · 必填','目标网页 URL'),('extract.engine','string · 可选','指定提取引擎；本轮不传，使用服务端选择')],
 [('result.main_text','string','正文 → content'),('result.title / final_url','string','页面标题 / 最终地址'),('result.links / images','array','页面链接和图片 → provider_metadata'),('result.status_code','number','源站状态；与 HTTP API 状态分开'),('metadata.server_elapsed_ms','number','服务端耗时')],
 {'extract':{'url':URL}},'harness/web_fetch/providers/lexmount.py','本轮为实时提取。中国区与国际区分开调用、分开计分。'),
 'firecrawl':spec('POST https://api.firecrawl.dev/v2/scrape','Authorization: Bearer <API_KEY>',
 [('url','string · 必填','目标网页或文档'),('formats','array','正文/资源为 markdown、links、images；原文件唯一使用 rawBase64'),('actions','array','动态顶层选择器用 wait action，随后执行预声明稳定延迟'),('timeout','number','不超过共享内部 HTTP 截止预算'),('maxAge','number','本轮 0，不复用旧缓存')],
 [('success','boolean','接口成功标记'),('data.markdown','string','正文 → content'),('data.rawBase64','string','原文件题解码为目标 URL 字节并统一验收'),('data.links / images','array','资源 → provider_metadata'),('data.metadata','object','标题、源 URL、源站状态、积分等'),('error / reason','string','错误仅作私有响应证据；公开记录使用稳定分类')],
 {'url':URL,'formats':['markdown'],'maxAge':0,'timeout':40500},'harness/web_fetch/providers/firecrawl.py','本轮每次响应后冷却 6.1 秒；冷却不计入单次请求耗时，不代表无速率限制的吞吐量。完整 API 响应进入私有哈希绑定证据。选择器需要多个匹配或位于 iframe 时，Scrape Actions 不能完整表达该条件，报告明确披露而不伪称已等待。'),
 'tavily':spec('POST https://api.tavily.com/extract','Authorization: Bearer <API_KEY>',
 [('urls','array · 必填','接口接受网址数组；本轮每次只放一个'),('extract_depth','string','本轮 advanced'),('timeout','number','服务端提取截止；由客户端内层截止推导，本轮 40.5 秒，官方范围 1–60 秒'),('format','string','本轮 markdown'),('include_images','boolean','正文配置 false；资源配置 true，额外返回图片地址')],
 [('results[].raw_content','string','提取正文 → content'),('results[].url','string','结果 URL'),('results[].images / images','array','结果条目或响应顶层的图片地址'),('failed_results','array','未成功提取的网址及原因'),('response_time','number','服务端耗时')],
 {'urls':[URL],'extract_depth':'advanced','timeout':40.5,'format':'markdown','include_images':False},'harness/web_fetch/providers/tavily.py','使用官方高级提取路线；45 秒外层预算预留持久化时间后，客户端与服务端提取截止均为 40.5 秒。HTTP 200 但没有 raw_content 仍按适配器请求失败记录。原始响应在私有证据边界独立哈希，公开报告只保留规范化结果与绑定摘要。'),
 'exa':spec('POST https://api.exa.ai/contents','x-api-key: <API_KEY>',
 [('urls','array · 必填','本轮每次一个目标 URL，不调用搜索'),('text.verbosity','string','本轮 full，要求完整正文而非 compact 默认值'),('maxAgeHours','number','本轮 0'),('livecrawlTimeout','integer','本轮 15,000 毫秒；采用官方建议上界，且小于 40.5 秒客户端内层截止'),('extras','object','正文配置不传；资源配置 links:20、imageLinks:20；具名列表配置 richLinks:100')],
 [('results[].text','string','正文 → content'),('results[].title / url','string','标题与结果网址'),('results[].extras.richLinks','array','明确的 anchor—URL 对 → 具名详情入口'),('results[].extras.links / imageLinks','array','不带名称归属的链接和图片地址'),('statuses[]','array','逐网址的成功状态或错误'),('costDollars','object','费用信息')],
 {'ordinary_body':{'urls':[URL],'text':{'verbosity':'full'},'maxAgeHours':0,'livecrawlTimeout':15000},'named_link':{'urls':[URL],'text':{'verbosity':'full'},'maxAgeHours':0,'livecrawlTimeout':15000,'extras':{'richLinks':100}}},'harness/web_fetch/providers/exa.py','强制实时抓取使用官方建议的 15 秒 livecrawlTimeout，并保持在本轮 40.5 秒客户端内层截止以内。具名列表只使用 richLinks 中明确配对且 anchor 非空的记录；不把正文标题与裸 links 数组按位置猜配。没有正文或逐网址状态失败，不能仅凭 HTTP 200 判成功；原始 API 响应只进入私有哈希绑定证据，不随公开报告发布。'),
 'cloudflare-markdown':spec('POST https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/browser-rendering/markdown','Authorization: Bearer <API_TOKEN>',
 [('url','string · 必填','目标页面'),('gotoOptions.waitUntil','string','本轮 networkidle2，等待页面网络活动趋于空闲'),('gotoOptions.timeout / actionTimeout','number','本轮均为 40,500 毫秒，与实际客户端内层 HTTP 截止一致；动态题还受预声明完成截止约束')],
 [('success','boolean','接口是否成功'),('result','string','Markdown 正文 → content'),('errors[]','array','接口错误代码与信息'),('meta','object · 若提供','最终 URL、标题、源站状态等')],
 {'url':URL,'gotoOptions':{'waitUntil':'networkidle2','timeout':40500},'actionTimeout':40500},'harness/web_fetch/providers/cloudflare.py','本轮是 Markdown 端点，不是截图或浏览器控制接口。45 秒外层预算预留结果持久化时间后，服务端导航与 action 计时器绑定 40.5 秒客户端 HTTP 截止；Cloudflare 的各等待计时器相互独立，不把它们误写成串行总预算。账户配额不足会单列失败。'),
 'cowagent-built-in':spec('Python · WebFetch({"cwd": workspace}).execute(args)','本地内置工具；无供应商 API Key',
 [('url','string · 必填','公开 HTTP/HTTPS 网页或文档 URL'),('cwd','构造配置','解析文档时使用的本地工作目录，不是工具输入参数')],
 [('ToolResult.status','string','success / error 等工具状态'),('ToolResult.result','string','提取文字或错误说明 → content')],
 {'url':URL},'harness/web_fetch/providers/cowagent.py','底层默认请求超时 30 秒；评测预算 45 秒不会改写工具自己的默认值。网页与 PDF/DOCX 走不同解析路径。'),
 'openclaw-built-in':spec('TypeScript · createWebFetchTool(...).execute(callId, args, signal)','本地内置工具；本轮禁用 Firecrawl 回退',
 [('url','string · 必填','目标页面'),('extractMode','string','本轮 markdown'),('maxChars','number','本轮 200000；输出可能截断'),('signal','AbortSignal','本轮 45 秒预算，通过函数参数传入')],
 [('details.text','string','正文 → content；缺失时取 content[].text'),('details.status / finalUrl','number / string','源站状态和最终 URL'),('details.extractor','string','实际正文提取器'),('details.truncated','boolean','输出是否被截断')],
 {'url':URL,'extractMode':'markdown','maxChars':200000},'scripts/oss/openclaw_webfetch_worker.mts','Readability 开启，cacheTtlMinutes=0。函数异常转成失败记录，不把其他 provider 的回退结果算给 OpenClaw。'),
 'deepseek-harness-built-in':spec('TypeScript · ctx.tools.execute({name: "web_fetch", arguments, ...})','本地 HTTP fetch provider；无云端抓取 Key',
 [('name','string','web_fetch'),('arguments.url','string · 必填','目标网址'),('callId / signal','运行时参数','调用标识和取消信号；示例省略初始化')],
 [('isError','boolean','工具执行是否报错'),('content[].text','string','文本块合并 → content'),('value.statusCode / value.url','number / string','成功态的规范源站状态与最终 URL'),('meta','object · 若提供','展示投影；仅在规范值缺失时补充状态或 URL'),('error.info.code','string · 若提供','工具错误类型')],
 {'name':'web_fetch','arguments':{'url':URL}},'scripts/oss/dsh_webfetch_worker.mts','本轮 response 上限 10 MB、正文上限 200000 字符；不是 DeepSeek 聊天 API，也没有调用模型生成答案。'),
 'langchain-webbase':spec('Python · WebBaseLoader(...).load()','本地开源库；无供应商 API Key',
 [('web_paths','tuple[str]','本轮仅一个目标 URL'),('requests_kwargs.timeout','number','本轮 45 秒'),('raise_for_status','boolean','本轮 true，HTTP 错误抛异常'),('continue_on_failure','boolean','本轮 false')],
 [('Document.page_content','string','各 Document 正文合并 → content'),('Document.metadata','dict','source、title、language 等'),('HTTP / Python exception','exception','转成失败记录及错误说明')],
 'loader = WebBaseLoader(\n    web_paths=("https://example.com/",),\n    requests_kwargs={"timeout": 45},\n    raise_for_status=True,\n    continue_on_failure=False,\n)\ndocs = loader.load()',
 'scripts/oss/langchain_webbase_worker.py','不运行网页 JavaScript；本轮没有把它替换成其他 LangChain loader。'),
 'crawl4ai':spec('Python · await AsyncWebCrawler(...).arun(url, config)','本地 Chromium；无云端抓取 Key',
 [('url','string · 必填','目标网址'),('cache_mode','CacheMode','本轮 BYPASS'),('page_timeout','number','45000 毫秒'),('wait_until','string','非动态网页为 domcontentloaded；动态任务使用预声明值'),('process_iframes / remove_overlay_elements','boolean','本轮均为 true')],
 [('result.success','boolean','爬取是否成功'),('result.markdown.raw_markdown','string','Markdown → content'),('result.media','dict','images、videos、audios'),('result.links','dict','internal / external'),('result.status_code / error_message','number / string','源站状态或失败信息')],
 'config = CrawlerRunConfig(\n    cache_mode=CacheMode.BYPASS,\n    page_timeout=45000,\n    wait_until="domcontentloaded",\n    process_iframes=True,\n    remove_overlay_elements=True,\n)\nresult = await crawler.arun(\n    url="https://example.com/", config=config\n)',
 'scripts/oss/crawl4ai_worker.py','非动态网页使用本地无头 Chrome，并在 DOMContentLoaded 后提取；动态任务仍按预声明完成条件等待。直接 PDF 正文在调用前选择 PDFCrawlerStrategy 与 PDFContentScrapingStrategy，文件交付任务选择 AsyncHTTPCrawlerStrategy 并统一验收 downloaded_files。媒体链接不等于下载；文件字节校验不等于理解媒体。'),
}
SPECS['lexmount-cn']={**SPECS['lexmount-global'],'entry':'POST https://api.lexmount.cn/v1/extract','auth':'X-API-Key: <CN_API_KEY>；X-Project-Id: <CN_PROJECT_ID>'}
SPECS['kitesurf']=spec(
 'POST …/browser-rendering/markdown?browser=kitesurf','Authorization: Bearer <API_TOKEN>',
 [('url','string','唯一任务输入'),('gotoOptions.waitUntil','string','networkidle2'),('browser','query','kitesurf'),('timeout','seconds','45 秒客户端端到端截止')],
 [('result','string','Cloudflare 原生 Markdown → content'),('success / errors','boolean / array','接口状态与错误'),('browser_ms_used','number-like','服务端浏览器计费时间；不等于客户端耗时')],
 {'url':URL,'gotoOptions':{'waitUntil':'networkidle2'}},'harness/web_fetch/providers/cloudflare.py',
 '账号版 Browser Run 原生 Markdown 路径。客户端耗时包含公网往返、远端排队与执行；browser_ms_used 仅作附加诊断。')
for engine,wait in (('lightpanda','done'),('obscura','load + adaptive settle'),('moli','done')):
 SPECS[engine]=spec(
  'CLI · '+engine+' fetch --dump markdown','本地独立浏览器；不需要供应商 API Key',
  [('URL','string','唯一任务输入'),('--dump','string','markdown'),('wait_until','string',wait),('timeout','seconds','45 秒外层截止；CLI 参数按各自单位换算')],
  [('stdout','UTF-8 string','浏览器原生 Markdown → content'),('native_markdown_ms','number','CLI 调用及原生序列化阶段耗时'),('browser_stderr','string','浏览器诊断；不进入正文')],
  {'url':URL,'engine':engine,'timeout':45,'wait_until':wait,'dump':'markdown'},'scripts/headless_worker.py',
  '不点击、不滚动、不补抓资源，不在失败后切换引擎或输出模式。CLI 未提供可靠 HTTP 状态和最终 URL 时保持空值。')
def moli_release_spec(version):
 base=SPECS['moli']
 return {**base,
 'entry':f'CLI · Moli {version} 普通网页 Markdown / 直接文档与媒体自动原始响应',
 'params':[('URL','string · 必填','唯一任务输入'),('--dump markdown','普通网页正文','直接取得 Markdown'),('不传 --dump','直接文档或媒体','由 CLI 自动选择原始响应或序列化页面；不附加页面等待参数'),('timeout','seconds','45 秒外层端到端截止；文档解析包含在内')],
 'fields':[('stdout','UTF-8 string / bytes','普通网页为 Markdown；直接文件为原始字节'),('document_extractor','string','直接文档正文使用的本地解析器'),('raw_stdout_path / sha256','string','保存的原始文档或媒体证据'),('file_evaluation','object','文件格式、长度和目标 SHA-256 验收')],
 'request':{'ordinary_web':'moli fetch --dump markdown --wait-until done --timeout 45000 URL','direct_document_or_media':'moli fetch --timeout 45000 --http-timeout 45000 URL','version':version},
 'notes':f'本方法只使用官方 {version} 发行二进制，与其他 Moli 版本分开计分。普通网页使用原生 Markdown；直接文档与媒体的裸 fetch 路线在正式二进制上实测确认。正文任务若取得受支持的原始文档，会在同一计时预算内解析文字；文件任务直接验收字节。不在失败后切换引擎或输出模式。'}
SPECS['moli-1.0.6']=moli_release_spec('1.0.6')
SPECS['moli']=moli_release_spec('1.1.1')
SPECS['moli']['notes']+=' 旧批次只保存了二进制 SHA-256，随后与官方 v1.1.1 归档逐字节匹配；受错误输出模式影响的直接文档与媒体已用正确路线重测。'
SPECS['moli-1.1.3']=moli_release_spec('1.1.3')
SPECS['chromium']=spec(
 'Playwright · Chromium → DOM HTML → node-html-markdown','本地独立浏览器；不需要 Google 账号，不加载个人会话',
 [('url','string','唯一任务输入'),('wait_until','string','domcontentloaded'),('timeout','seconds','45 秒端到端截止'),('converter','string','node-html-markdown 2.0.0')],
 [('content','string','转换后的 Markdown'),('rendered_html_path / sha256','string','转换前 DOM HTML 证据'),('fetch_html_ms / conversion_ms','number','分阶段耗时'),('browser_stderr','string','浏览器诊断；不进入正文')],
 {'url':URL,'engine':'chromium','timeout':45,'wait_until':'domcontentloaded','dump':'html','converter':'node-html-markdown 2.0.0'},'scripts/headless_worker.py',
 'Chromium/CDP 没有原生 Markdown 输出，因此取完整渲染 DOM 后转换；转换计入端到端耗时，HTML 与转换器版本、哈希均保留。')

def render_interfaces(out,records):
 def pre(value):return '<pre>'+escape(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2))+'</pre>'
 def fields(rows):return '<dl class="interface-fields">'+''.join('<div><dt><code>'+escape(k)+'</code> <small>'+escape(t)+'</small></dt><dd>'+escape(d)+'</dd></div>' for k,t,d in rows)+'</dl>'
 measured={r['provider'] for r in records}
 names=sorted(SPECS,key=lambda name:(name not in measured,name))
 body='<p>按本轮适配器介绍，不混用完整产品能力。输入示例展示正文配置，资源配置差异单列；它们不是每条历史请求的逐字回放。各调用所属批次及实际配置见“环境与复现”。输出示例取存档调用，经适配器整理后的真实节选，不冒充原始 HTTP 响应。Key 和账号均用占位符。</p>'
 for i,name in enumerate(names):
  s=SPECS[name];r=next((r for r in records if r['provider']==name and r['case_id']=='static-simple'),next((r for r in records if r['provider']==name),None))
  access=HEADLESS_ACCESS.get(name)
  if r:
   content=contained_path(out,r['artifact_path']).read_text();sample={k:r.get(k) for k in ['ok','status_code','title','final_url']};sample['content_excerpt']=content[:450];sample['content_characters']=len(content)
  else:sample={'measurement':None,'note':'仓库登记接口，未纳入本轮；此处不提供虚构的运行输出。'}
  # Explicit whitelist: no local paths, request IDs, credentials or session metadata.
  meta=r.get('provider_metadata',{}) if r else {};sample['metadata']={k:meta[k] for k in ['engine','upstream_status','links_count','images_count','extractor','truncated','document_count'] if k in meta}
  dest=out/'adoption'/'interfaces';dest.mkdir(exist_ok=True);(dest/(name+'.json')).write_text(json.dumps({'interface':s,'access':access,'sample':sample,'attempt_id':r['attempt_id'] if r else None},ensure_ascii=False,indent=2)+'\n')
  body+=f'<details class="interface-panel" id="interface-{name}"'+(' open' if i==0 else '')+'><summary>'+escape(name)+'</summary>'
  body+='<p class="interface-endpoint"><code>'+escape(s['entry'])+'</code></p><p class="source-note">'+escape(s['auth'])+'</p>'
  if access:
   body+='<h3>账号与获取入口</h3><p>'+escape(access['account'])+'</p><p>'+escape(access['configuration'])+'</p><p>'+ ' · '.join('<a href="'+escape(url,quote=True)+'">'+escape(label)+'</a>' for label,url in access['links'])+'</p><p class="source-note">官方说明核验：2026-09-07。只列账号要求、获取入口与占位符，不公开真实凭据。</p>'
  sample_label='本轮输出节选 · 统一记录' if r else '本轮未执行'
  body+='<div class="guide-grid"><article class="guide-card"><h3>输入</h3>'+fields(s['params'])+'<h4>调用输入示例</h4>'+pre(s['request'])+'</article><article class="guide-card"><h3>输出</h3>'+fields(s['fields'])+'<h4>'+sample_label+'</h4>'+pre(sample)+'</article></div>'
  measured_link='<a href="../attempts/'+escape(r['attempt_id'])+'.html">该次完整输出与判定 →</a>' if r else '未纳入本轮；参数说明为仓库登记模式，不计入本轮结果'
  body+='<p>'+escape(s['notes'])+'</p><p class="source-note">核对实现：<code>'+escape(s['source'])+'</code> · '+measured_link+' · <a href="interfaces/'+name+'.json">示例 JSON</a></p></details>'
 return body
