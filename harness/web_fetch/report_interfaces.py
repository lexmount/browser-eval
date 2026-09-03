"""Interfaces actually used by the benchmark, not a claim to cover entire SDKs."""
import json
from html import escape
from .safety import contained_path

URL='https://example.com/'
def spec(entry,auth,params,fields,request,source,notes):
 return dict(entry=entry,auth=auth,params=params,fields=fields,request=request,source=source,notes=notes)
SPECS={
 'moli-webfetch':spec('CLI · moli fetch --dump markdown','本地二进制；无 API Key',
 [('URL','string · 必填','公开 HTTP(S) 页面'),('--wait-until','string','done；可配置 networkidle / domstable 等'),('--timeout','number','毫秒；与外层进程截止时间使用同一预算'),('--block-private-networks','flag','默认启用，阻止私网地址')],
 [('stdout','UTF-8 string','原样保存 Markdown → content'),('stderr','string','独立诊断；不混入正文'),('exit_code','integer','0 且正文非空表示工具执行成功，任务另按规则判定'),('status_code / final_url','未提供','Markdown 模式没有这些字段，不伪造 HTTP 200 或最终 URL')],
 'moli fetch --dump markdown --wait-until done --timeout 45000 --block-private-networks "https://example.com/"',
 'harness/web_fetch/providers/moli.py','固定 Markdown 模式；不在失败时切换格式或供应商。JSON、截图与 PDF 是官方其他输出模式，不算本适配器的执行结果。'),
 'lexmount-global':spec('POST https://api.lexmount.com/v1/extract','X-API-Key: <API_KEY>；X-Project-Id: <PROJECT_ID>',
 [('extract.url','string · 必填','目标网页 URL'),('extract.engine','string · 可选','指定提取引擎；本轮不传，使用服务端选择')],
 [('result.main_text','string','正文 → content'),('result.title / final_url','string','页面标题 / 最终地址'),('result.links / images','array','页面链接和图片 → provider_metadata'),('result.status_code','number','源站状态；与 HTTP API 状态分开'),('metadata.server_elapsed_ms','number','服务端耗时')],
 {'extract':{'url':URL}},'harness/web_fetch/providers/lexmount.py','本轮为实时提取。中国区与国际区分开调用、分开计分。'),
 'firecrawl':spec('POST https://api.firecrawl.dev/v2/scrape','Authorization: Bearer <API_KEY>',
 [('url','string · 必填','目标网页或文档'),('formats','array','本轮请求 markdown、links、images'),('maxAge','number','本轮 0，不复用旧缓存')],
 [('success','boolean','接口成功标记'),('data.markdown','string','正文 → content'),('data.links / images','array','资源 → provider_metadata'),('data.metadata','object','标题、源 URL、源站状态、积分等'),('error / reason','string','错误或额度原因')],
 {'url':URL,'formats':['markdown','links','images'],'maxAge':0},'harness/web_fetch/providers/firecrawl.py','本轮每次响应后冷却 6.1 秒；冷却不计入单次请求耗时。'),
 'tavily':spec('POST https://api.tavily.com/extract','Authorization: Bearer <API_KEY>',
 [('urls','array · 必填','接口接受网址数组；本轮每次只放一个'),('extract_depth','string','本轮 basic'),('format','string','本轮 markdown'),('include_images','boolean','本轮 true，额外返回图片地址')],
 [('results[].raw_content','string','提取正文 → content'),('results[].url','string','结果 URL'),('results[].images / images','array','结果条目或响应顶层的图片地址'),('failed_results','array','未成功提取的网址及原因'),('response_time','number','服务端耗时')],
 {'urls':[URL],'extract_depth':'basic','format':'markdown','include_images':True},'harness/web_fetch/providers/tavily.py','HTTP 200 但没有 raw_content 仍按请求失败记录。'),
 'exa':spec('POST https://api.exa.ai/contents','x-api-key: <API_KEY>',
 [('urls','array · 必填','本轮每次一个目标 URL，不调用搜索'),('text','boolean','本轮 true，要求正文'),('maxAgeHours','number','本轮 0'),('extras','object','本轮 links:20、imageLinks:20')],
 [('results[].text','string','正文 → content'),('results[].title / url','string','标题与结果网址'),('results[].extras','object','额外链接和图片'),('statuses[]','array','逐网址的成功状态或错误'),('costDollars','object','费用信息')],
 {'urls':[URL],'text':True,'maxAgeHours':0,'extras':{'links':20,'imageLinks':20}},'harness/web_fetch/providers/exa.py','没有正文或逐网址状态失败，不能仅凭 HTTP 200 判成功。'),
 'cloudflare-markdown':spec('POST https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/browser-rendering/markdown','Authorization: Bearer <API_TOKEN>',
 [('url','string · 必填','目标页面'),('gotoOptions.waitUntil','string','本轮 networkidle2，等待页面网络活动趋于空闲')],
 [('success','boolean','接口是否成功'),('result','string','Markdown 正文 → content'),('errors[]','array','接口错误代码与信息'),('meta','object · 若提供','最终 URL、标题、源站状态等')],
 {'url':URL,'gotoOptions':{'waitUntil':'networkidle2'}},'harness/web_fetch/providers/cloudflare.py','本轮是 Markdown 端点，不是截图或浏览器控制接口；账户配额不足会单列失败。'),
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
 [('isError','boolean','工具执行是否报错'),('content[].text','string','文本块合并 → content'),('output.presentationMeta','object · 若提供','URL、源站状态等展示信息'),('error.info.code','string · 若提供','工具错误类型')],
 {'name':'web_fetch','arguments':{'url':URL}},'scripts/oss/dsh_webfetch_worker.mts','本轮 response 上限 10 MB、正文上限 200000 字符；不是 DeepSeek 聊天 API，也没有调用模型生成答案。'),
 'langchain-webbase':spec('Python · WebBaseLoader(...).load()','本地开源库；无供应商 API Key',
 [('web_paths','tuple[str]','本轮仅一个目标 URL'),('requests_kwargs.timeout','number','本轮 45 秒'),('raise_for_status','boolean','本轮 true，HTTP 错误抛异常'),('continue_on_failure','boolean','本轮 false')],
 [('Document.page_content','string','各 Document 正文合并 → content'),('Document.metadata','dict','source、title、language 等'),('HTTP / Python exception','exception','转成失败记录及错误说明')],
 'loader = WebBaseLoader(\n    web_paths=("https://example.com/",),\n    requests_kwargs={"timeout": 45},\n    raise_for_status=True,\n    continue_on_failure=False,\n)\ndocs = loader.load()',
 'scripts/oss/langchain_webbase_worker.py','不运行网页 JavaScript；本轮没有把它替换成其他 LangChain loader。'),
 'crawl4ai':spec('Python · await AsyncWebCrawler(...).arun(url, config)','本地 Chromium；无云端抓取 Key',
 [('url','string · 必填','目标网址'),('cache_mode','CacheMode','本轮 BYPASS'),('page_timeout','number','45000 毫秒'),('wait_until','string','networkidle'),('process_iframes / remove_overlay_elements','boolean','本轮均为 true')],
 [('result.success','boolean','爬取是否成功'),('result.markdown.raw_markdown','string','Markdown → content'),('result.media','dict','images、videos、audios'),('result.links','dict','internal / external'),('result.status_code / error_message','number / string','源站状态或失败信息')],
 'config = CrawlerRunConfig(\n    cache_mode=CacheMode.BYPASS,\n    page_timeout=45000,\n    wait_until="networkidle",\n    process_iframes=True,\n    remove_overlay_elements=True,\n)\nresult = await crawler.arun(\n    url="https://example.com/", config=config\n)',
 'scripts/oss/crawl4ai_worker.py','本轮使用本地无头 Chrome，媒体单列在返回对象中；这不等于下载或理解媒体。'),
}
SPECS['lexmount-cn']={**SPECS['lexmount-global'],'entry':'POST https://api.lexmount.cn/v1/extract','auth':'X-API-Key: <CN_API_KEY>；X-Project-Id: <CN_PROJECT_ID>'}

def render_interfaces(out,records):
 def pre(value):return '<pre>'+escape(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2))+'</pre>'
 def fields(rows):return '<dl class="interface-fields">'+''.join('<div><dt><code>'+escape(k)+'</code> <small>'+escape(t)+'</small></dt><dd>'+escape(d)+'</dd></div>' for k,t,d in rows)+'</dl>'
 names=sorted({r['provider'] for r in records if r['provider'] in SPECS})
 body='<p>按本轮实际适配器介绍，不混用完整产品能力。输入示例采用当前配置；输出示例取存档调用，经适配器整理后的真实节选，不冒充原始 HTTP 响应。Key 和账号均用占位符。</p>'
 for i,name in enumerate(names):
  s=SPECS[name];r=next((r for r in records if r['provider']==name and r['case_id']=='static-simple'),next(r for r in records if r['provider']==name))
  content=contained_path(out,r['artifact_path']).read_text();sample={k:r.get(k) for k in ['ok','status_code','title','final_url']};sample['content_excerpt']=content[:450];sample['content_characters']=len(content)
  # Explicit whitelist: no local paths, request IDs, credentials or session metadata.
  meta=r.get('provider_metadata',{});sample['metadata']={k:meta[k] for k in ['engine','upstream_status','links_count','images_count','extractor','truncated','document_count'] if k in meta}
  dest=out/'adoption'/'interfaces';dest.mkdir(exist_ok=True);(dest/(name+'.json')).write_text(json.dumps({'interface':s,'sample':sample,'attempt_id':r['attempt_id']},ensure_ascii=False,indent=2)+'\n')
  body+=f'<details class="interface-panel" id="interface-{name}"'+(' open' if i==0 else '')+'><summary>'+escape(name)+'</summary>'
  body+='<p class="interface-endpoint"><code>'+escape(s['entry'])+'</code></p><p class="source-note">'+escape(s['auth'])+'</p>'
  body+='<div class="guide-grid"><article class="guide-card"><h3>输入</h3>'+fields(s['params'])+'<h4>调用输入示例</h4>'+pre(s['request'])+'</article><article class="guide-card"><h3>输出</h3>'+fields(s['fields'])+'<h4>本轮输出节选 · 统一记录</h4>'+pre(sample)+'</article></div>'
  body+='<p>'+escape(s['notes'])+'</p><p class="source-note">核对实现：<code>'+escape(s['source'])+'</code> · <a href="../attempts/'+escape(r['attempt_id'])+'.html">该次完整输出与判定 →</a> · <a href="interfaces/'+name+'.json">示例 JSON</a></p></details>'
 return body
