"""Interfaces actually used by the benchmark, not a claim to cover entire SDKs."""
import json
import re
from html import escape
from .safety import contained_path
from .methods import native_browser_engine, browser_method_registry, browser_method_label

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
 'moli':{'account':'不需要供应商账号或 API Key。','configuration':'安装与本报告版本和 SHA-256 一致的二进制，配置 MOLI_BINARY；详见“环境与复现”。','env_vars':['MOLI_BINARY'],'links':[('官方项目与安装说明','https://github.com/lexmount/moli')]},
 'moli-1.1.7':{'account':'不需要供应商账号或 API Key。','configuration':'从官方 v1.1.7 release 获取二进制，配置 MOLI_1_1_7_BINARY。','env_vars':['MOLI_1_1_7_BINARY'],'links':[('Moli 1.1.7 正式发布','https://github.com/lexmount/moli/releases/tag/v1.1.7')]},
 'moli-1.1.11':{'account':'不需要供应商账号或 API Key。','configuration':'从官方 v1.1.11 release 获取二进制，配置 MOLI_1_1_11_BINARY。','env_vars':['MOLI_1_1_11_BINARY'],'links':[('Moli 1.1.11 正式发布','https://github.com/lexmount/moli/releases/tag/v1.1.11')]},
 'moli-pr643':{'account':'不需要供应商账号或 API Key。','configuration':'从 Moli PR #643 的固定源码提交构建，配置 MOLI_PR643_BINARY；此方法是候选构建版。','env_vars':['MOLI_PR643_BINARY'],'links':[('Moli 源码 PR','https://github.com/lexmount/moli/pull/643')]},
 'moli-1.1.3':{'account':'不需要供应商账号或 API Key。','configuration':'从官方 v1.1.3 release 获取二进制，配置 MOLI_1_1_3_BINARY；发布包与可执行文件均校验 SHA-256。','env_vars':['MOLI_1_1_3_BINARY'],'links':[('Moli 1.1.3 正式发布','https://github.com/lexmount/moli/releases/tag/v1.1.3')]},
 'chromium':{'account':'不需要 Google 账号或 API Key；不加载个人登录会话。','configuration':'安装与本报告版本和 SHA-256 一致的浏览器，配置 CHROMIUM_BINARY；详见“环境与复现”。','env_vars':['CHROMIUM_BINARY'],'links':[('Chrome for Testing','https://googlechromelabs.github.io/chrome-for-testing/'),('官方浏览器安装说明','https://playwright.dev/python/docs/browsers')]},
}
def spec(entry,auth,params,fields,request,source,notes):
 return dict(entry=entry,auth=auth,params=params,fields=fields,request=request,source=source,notes=notes)
SPECS={
 'moli-webfetch':spec('CLI · 原生 Markdown 或原始响应路线','本地二进制；无 API Key',
 [('URL','string · 必填','公开 HTTP(S) 页面或直接文件'),('--dump markdown','所有网页、文档正文与网页媒体信息任务','直接取得 Moli 原生 Markdown；直接 PDF/DOCX 未提供可读正文时失败'),('--with-frames','网页媒体信息任务','让同一次原生 Markdown 调用包含已加载子文档；不切换输出格式'),('--wait-until / --wait-selector / --wait-script','string','使用 Moli 官方等待能力；只等待，不抽取或拼接正文'),('外层截止','number','每次调用 45 秒；不改写 Moli 内部 HTTP 和连接超时默认值'),('--block-private-networks','flag','默认启用')],
 [('stdout','UTF-8 string / bytes','Markdown、eval 结果或直接文件原始字节'),('stderr','string','独立诊断；不混入正文'),('exit_code','integer','0 表示工具请求完成；正文或文件为空仍由任务规则单独判失败'),('status_code / final_url','可能未提供','CLI 未提供时保持未知，不伪造 HTTP 200 或最终 URL')],
 {'page_body':'moli fetch --dump markdown --wait-until done URL','exact_page_identity':'正文仍用原生 Markdown；最终网址不是评分条件，无法确认时保持空值','webpage_media':'moli fetch --dump markdown --with-frames --wait-until done URL','direct_document_body':'moli fetch --dump markdown --wait-until done URL','direct_file':'Moli 原生 CDP 响应；附件事件使用同一 Moli 原始附件接口'},
 'harness/web_fetch/providers/moli.py','网页正文、文档正文、结构题和网页媒体信息题统一使用 Moli 原生 Markdown。网页媒体信息只在这份原生输出中核对题目指定的资源地址，不下载媒体，也不切换 HTML、JSON 或网络跟踪接口。旧版 selector 合同只映射到官方等待参数，不参与正文抽取。直接 PDF/DOCX 没有交付可读正文时按失败记录，评测端不下载后解析。直接媒体文件使用原生 CDP 响应或同一二进制的原始附件接口。最终网址只作诊断，不决定正文是否通过；失败后不切换格式或借用其他 provider。'),
 'lexmount-global':spec('POST https://api.lexmount.com/v1/extract','X-API-Key: <API_KEY>；X-Project-Id: <PROJECT_ID>',
 [('extract.url','string · 必填','目标网页 URL'),('extract.engine','string · 可选','指定提取引擎；本轮不传，使用服务端选择')],
 [('result.main_text','string','正文 → content'),('result.title / final_url','string','页面标题 / 最终地址'),('result.links / images','array','页面链接和图片 → provider_metadata'),('result.status_code','number','源站状态；与 HTTP API 状态分开'),('metadata.server_elapsed_ms','number','服务端耗时')],
 {'extract':{'url':URL}},'harness/web_fetch/providers/lexmount.py','本轮为实时提取。中国区与国际区分开调用、分开计分。'),
 'firecrawl':spec('POST https://api.firecrawl.dev/v2/scrape','Authorization: Bearer <API_KEY>',
 [('url','string · 必填','目标网页或文档'),('formats','array','正文/资源为 markdown、links、images；视频资源题另请求 video；原文件只使用 rawBase64'),('actions','array','动态顶层选择器用 wait action，随后执行预声明稳定延迟'),('timeout','number','不超过共享内部 HTTP 截止预算'),('maxAge','number','本轮 0，不复用旧缓存')],
 [('success','boolean','接口成功标记'),('data.markdown','string','正文 → content'),('data.rawBase64','string','原文件题解码为目标 URL 字节并统一验收'),('data.links / images / video','array / string','资源地址；video 是有时效的视频下载入口'),('data.metadata','object','标题、源 URL、源站状态、积分等'),('error / reason','string','错误仅作私有响应证据；公开记录使用稳定分类')],
 {'ordinary_body':{'url':URL,'formats':['markdown'],'maxAge':0,'timeout':40500},'dynamic':{'url':URL,'formats':['markdown','links','images'],'actions':[{'type':'wait','selector':'.ready'}],'maxAge':0},'video_resource':{'url':URL,'formats':['markdown','links','images','video'],'maxAge':0},'original_file':{'url':URL,'formats':['rawBase64'],'parsers':[],'maxAge':0}},'harness/web_fetch/providers/firecrawl.py','已知单页使用 Scrape；Search、Crawl、Agent 会先找页面或跟随其他链接，不符合本轮给定 URL 的单次任务。动态题由 Actions 等待可表达的顶层选择器；匹配数量和 iframe 子文档条件仍可能不完整。video 返回的下载地址不等于原文件或画面理解。每次响应后冷却 6.1 秒，冷却不计入单次耗时；完整 API 响应只保存在私有哈希绑定证据。'),
 'tavily':spec('POST https://api.tavily.com/extract','Authorization: Bearer <API_KEY>',
 [('urls','array · 必填','接口接受网址数组；本轮每次只放一个'),('extract_depth','string','本轮 advanced'),('timeout','number','服务端提取截止；由客户端内层截止推导，本轮 40.5 秒，官方范围 1–60 秒'),('format','string','本轮 markdown'),('include_images','boolean','正文配置 false；资源配置 true，额外返回图片地址')],
 [('results[].raw_content','string','提取正文 → content'),('results[].url','string','结果 URL'),('results[].images / images','array','结果条目或响应顶层的图片地址'),('failed_results','array','未成功提取的网址及原因'),('response_time','number','服务端耗时')],
 {'urls':[URL],'extract_depth':'advanced','timeout':40.5,'format':'markdown','include_images':False},'harness/web_fetch/providers/tavily.py','已知 URL 使用 advanced Extract；Search 根据查询找页面，Crawl 扩展到链接页面，Research 生成答案，均不替代目标 URL 的正文。资源题开启 include_images；没有原文件或视频下载字段。45 秒外层预算预留持久化后，客户端与服务端提取截止均为 40.5 秒。HTTP 200 但没有 raw_content 仍按适配器请求失败；原始响应只保存在私有哈希绑定证据。'),
 'exa':spec('POST https://api.exa.ai/contents','x-api-key: <API_KEY>',
 [('urls','array · 必填','本轮每次一个目标 URL，不调用搜索'),('text.verbosity','string','本轮 full，要求完整正文而非 compact 默认值'),('maxAgeHours','number','本轮 0'),('livecrawlTimeout','integer','本轮 15,000 毫秒；采用官方建议上界，且小于 40.5 秒客户端内层截止'),('extras','object','正文配置不传；资源配置 links:20、imageLinks:20；具名列表配置 richLinks:100')],
 [('results[].text','string','正文 → content'),('results[].title / url','string','标题与结果网址'),('results[].extras.richLinks','array','明确的 anchor—URL 对 → 具名详情入口'),('results[].extras.links / imageLinks','array','不带名称归属的链接和图片地址'),('statuses[]','array','逐网址的成功状态或错误'),('costDollars','object','费用信息')],
 {'ordinary_body':{'urls':[URL],'text':{'verbosity':'full'},'maxAgeHours':0,'livecrawlTimeout':15000},'named_link':{'urls':[URL],'text':{'verbosity':'full'},'maxAgeHours':0,'livecrawlTimeout':15000,'extras':{'richLinks':100}}},'harness/web_fetch/providers/exa.py','已知 URL 使用 Contents 的 full 正文与 maxAgeHours=0 实时抓取；Search 先检索，Agent 和 summary 生成回答，不能代替指定 URL 的原文。资源题请求 links/imageLinks，具名列表只接纳 richLinks 的明确 anchor—URL 配对，不把正文标题与裸地址按位置猜配。livecrawlTimeout 为 15 秒，低于 40.5 秒客户端截止。逐网址状态失败或没有正文时，不能仅凭 HTTP 200 判成功；原始响应只保存在私有证据。'),
 'cloudflare-markdown':spec('POST https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/browser-rendering/{markdown|scrape}; direct files POST …/ai/tomarkdown','Authorization: Bearer <API_TOKEN>',
 [('url','string · 必填','目标页面；直接文件先下载公开 URL 的原始字节再上传'),('gotoOptions.waitUntil','string','网页和资源页使用 networkidle2；动态题使用预声明就绪信号'),('elements','array · 资源题','/scrape 按预声明 CSS 选择器取图片或媒体属性'),('files','multipart · 直接文档','/ai/tomarkdown 接受 PDF / DOCX 等受支持文件；输入字节由客户端取得'),('gotoOptions.timeout / actionTimeout','number','网页和资源页绑定 40,500 毫秒客户端内层截止')],
 [('success','boolean','接口是否成功'),('result','string / array','/markdown 网页正文；/scrape 元素属性；/ai/tomarkdown 文档 Markdown'),('errors[]','array','接口错误代码与信息'),('meta','object · 若提供','Browser Run 最终 URL、标题、源站状态等'),('source_sha256','string · 直接文档','上传文件的输入字节摘要；下载与转换均计入同一次耗时')],
 {'web':{'endpoint':'/browser-rendering/markdown','url':URL,'gotoOptions':{'waitUntil':'networkidle2','timeout':40500},'actionTimeout':40500},'image_or_video_resource':{'endpoint':'/browser-rendering/scrape','url':URL,'elements':[{'selector':'img'},{'selector':'video'}]},'video_detail_link':{'endpoint':'/browser-rendering/scrape','url':URL,'elements':[{'selector':'a[href]'}]},'direct_pdf_or_docx':{'endpoint':'/ai/tomarkdown','files':'<downloaded public document bytes>'}},'harness/web_fetch/providers/cloudflare.py','按任务预选官方接口：网页正文用 Browser Run /markdown；页面图片与视频地址用 /scrape 的 DOM 属性；直接 PDF / DOCX 使用 Workers AI 文件转换。/pdf 是生成网页 PDF，/links 只列普通链接，/crawl 会跟随其他页面，均不满足这里的文档文字或目标资源合同。直接文档先在客户端下载，下载与转换共享预算；该下载不是 Cloudflare 的独立成绩。浏览器等待计时器彼此独立，账户配额或转换权限错误单列。'),
 'cowagent-built-in':spec('Python · WebFetch({"cwd": workspace}).execute(args)','本地内置工具；无供应商 API Key',
 [('url','string · 必填','公开 HTTP/HTTPS 网页或文档 URL'),('cwd','构造配置','解析文档时使用的本地工作目录，不是工具输入参数')],
 [('ToolResult.status','string','success / error 等工具状态'),('ToolResult.result','string','提取文字或错误说明 → content')],
 {'url':URL},'harness/web_fetch/workers/cowagent_worker.py','底层默认请求超时 30 秒；评测预算 45 秒不会改写工具自己的默认值。网页与 PDF/DOCX 走不同解析路径。'),
 'openclaw-built-in':spec('TypeScript · createWebFetchTool(...).execute(callId, args, signal)','本地内置工具；本轮禁用 Firecrawl 回退',
 [('url','string · 必填','目标页面'),('extractMode','string','本轮 markdown'),('maxChars','number','本轮 200000；输出可能截断'),('signal','AbortSignal','本轮 45 秒预算，通过函数参数传入')],
 [('details.text','string','正文 → content；缺失时取 content[].text'),('details.status / finalUrl','number / string','源站状态和最终 URL'),('details.extractor','string','实际正文提取器'),('details.truncated','boolean','输出是否被截断')],
 {'url':URL,'extractMode':'markdown','maxChars':200000},'harness/web_fetch/workers/openclaw_webfetch_worker.mts','Readability 开启，cacheTtlMinutes=0。函数异常转成失败记录，不把其他 provider 的回退结果算给 OpenClaw。'),
 'deepseek-harness-built-in':spec('TypeScript · ctx.tools.execute({name: "web_fetch", arguments, ...})','本地 HTTP fetch provider；无云端抓取 Key',
 [('name','string','web_fetch'),('arguments.url','string · 必填','目标网址'),('callId / signal','运行时参数','调用标识和取消信号；示例省略初始化')],
 [('isError','boolean','工具执行是否报错'),('value.body.content','string','接口声明的 HTML/文本正文；声明为空时保持为空'),('content[].text','string','模型展示文本；与声明正文分别保存'),('value.statusCode / value.url','number / string','成功态的规范源站状态与最终 URL'),('meta','object · 若提供','展示投影；仅在规范值缺失时补充状态或 URL'),('error.info.code','string · 若提供','工具错误类型')],
 {'name':'web_fetch','arguments':{'url':URL}},'harness/web_fetch/workers/dsh_webfetch_worker.mts','通过内置 HTTP provider 返回声明正文及展示文本；调用预算和读取字段以保留证据为准。不是 DeepSeek 聊天 API，也没有调用模型生成答案。'),
 'langchain-webbase':spec('Python · WebBaseLoader(...).load()','本地开源库；无供应商 API Key',
 [('web_paths','tuple[str]','本轮仅一个目标 URL'),('requests_kwargs.timeout','number','本轮 45 秒'),('raise_for_status','boolean','本轮 true，HTTP 错误抛异常'),('continue_on_failure','boolean','本轮 false')],
 [('Document.page_content','string','各 Document 正文合并 → content'),('Document.metadata','dict','source、title、language 等'),('HTTP / Python exception','exception','转成失败记录及错误说明')],
 'loader = WebBaseLoader(\n    web_paths=("https://example.com/",),\n    requests_kwargs={"timeout": 45},\n    raise_for_status=True,\n    continue_on_failure=False,\n)\ndocs = loader.load()',
 'harness/web_fetch/workers/langchain_webbase_worker.py','不运行网页 JavaScript；本轮没有把它替换成其他 LangChain loader。'),
 'crawl4ai':spec('Python · await AsyncWebCrawler(...).arun(url, config)','本地 Chromium；无云端抓取 Key',
 [('url','string · 必填','目标网址'),('cache_mode','CacheMode','本轮 BYPASS'),('page_timeout','number','45000 毫秒'),('wait_until','string','非动态网页为 domcontentloaded；动态任务使用预声明值'),('process_iframes / remove_overlay_elements','boolean','本轮均为 true')],
 [('result.success','boolean','爬取是否成功'),('result.markdown.raw_markdown','string','Markdown → content'),('result.media','dict','images、videos、audios'),('result.links','dict','internal / external'),('result.status_code / error_message','number / string','源站状态或失败信息')],
 'config = CrawlerRunConfig(\n    cache_mode=CacheMode.BYPASS,\n    page_timeout=45000,\n    wait_until="domcontentloaded",\n    process_iframes=True,\n    remove_overlay_elements=True,\n)\nresult = await crawler.arun(\n    url="https://example.com/", config=config\n)',
 'harness/web_fetch/workers/crawl4ai_worker.py','非动态网页使用本地无头 Chrome，并在 DOMContentLoaded 后提取；动态任务仍按预声明完成条件等待。直接 PDF 正文在调用前选择 PDFCrawlerStrategy 与 PDFContentScrapingStrategy，文件交付任务选择 AsyncHTTPCrawlerStrategy 并统一验收 downloaded_files。媒体链接不等于下载；文件字节校验不等于理解媒体。'),
}
SPECS['lexmount-cn']={**SPECS['lexmount-global'],'entry':'POST https://api.lexmount.cn/v1/extract','auth':'X-API-Key: <CN_API_KEY>；X-Project-Id: <CN_PROJECT_ID>'}
SPECS['kitesurf']=spec(
 'POST …/browser-rendering/markdown?browser=kitesurf','Authorization: Bearer <API_TOKEN>',
 [('url','string','唯一任务输入'),('gotoOptions.waitUntil','string','networkidle2'),('browser','query','kitesurf'),('timeout','seconds','45 秒客户端端到端截止')],
 [('result','string','Cloudflare 原生 Markdown → content'),('success / errors','boolean / array','接口状态与错误'),('browser_ms_used','number-like','服务端浏览器计费时间；不等于客户端耗时')],
 {'url':URL,'gotoOptions':{'waitUntil':'networkidle2'}},'harness/web_fetch/providers/cloudflare.py',
 '正文使用账号版 Browser Run 原生 Markdown；图片、视频资源通过指定 Kitesurf 的官方 CDP 执行共用 DOM 脚本，可读取已加载的同源 iframe。直接文档使用官方 Markdown 接口，嵌入文档通过该 CDP 读取已加载的查看器文字；直接文件通过 CDP 获取原始响应。Workers AI 文档转换不计入 Kitesurf 成绩。客户端耗时包含公网往返、排队与执行。')
for engine,wait in (('lightpanda','done'),('obscura','load + adaptive settle'),('moli','done')):
 SPECS[engine]=spec(
  'CLI · '+engine+' fetch --dump markdown','本地独立浏览器；不需要供应商 API Key',
  [('URL','string','唯一任务输入'),('--dump','string','markdown'),('wait_until','string',wait),('timeout','seconds','45 秒外层截止；CLI 参数按各自单位换算')],
  [('stdout','UTF-8 string','浏览器原生 Markdown → content'),('native_markdown_ms','number','CLI 调用及原生序列化阶段耗时'),('browser_stderr','string','浏览器诊断；不进入正文')],
  {'url':URL,'engine':engine,'timeout':45,'wait_until':wait,'dump':'markdown'},'harness/web_fetch/providers/headless_worker.py',
  '正文使用原生 Markdown。文件使用浏览器已有的原始响应接口；资源与嵌入文档使用共用 DOM 脚本。嵌入文档只读取浏览器内已经加载的文字，不下载后外部解析。不在失败后切换引擎或请求外部服务补正文。接口未提供 HTTP 状态或最终 URL 时保留空值。')
def moli_release_spec(version):
 base=SPECS['moli']
 return {**base,
 'entry':f'CLI · Moli {version} 原生 Markdown / 原始文件接口',
 'params':[('URL','string · 必填','唯一任务输入'),('--dump markdown','网页、文档正文、结构与网页媒体信息任务','直接取得 Markdown；直接 PDF/DOCX 不提供可读正文时失败'),('--with-frames','网页媒体信息任务','在同一次 Markdown 调用中包含已加载子文档'),('--wait-selector / --wait-script','旧版预声明完成条件','只等待，不抽取或拼接正文'),('外层截止','seconds','45 秒；不改写产品内部超时默认值')],
 'fields':[('stdout','UTF-8 string / bytes','网页与文档任务为原生 Markdown；直接文件为原始字节'),('raw_stdout_path / sha256','string','保存的原始文档或媒体证据'),('file_evaluation','object','文件格式、长度和目标 SHA-256 验收')],
 'request':{'page_or_document_body':'moli fetch --dump markdown --wait-until done URL','webpage_media':'moli fetch --dump markdown --with-frames --wait-until done URL','direct_file':'原始响应或附件接口；具体路线见逐次调用记录','version':version},
 'notes':f'本方法只使用官方 {version} 发行二进制，与其他 Moli 版本分开计分。网页正文、文档正文、结构题和网页媒体信息题统一使用原生 Markdown；网页媒体只核对这份输出中的目标地址，不下载，也不切换 HTML、JSON 或网络跟踪接口。预声明完成条件只控制等待。直接 PDF/DOCX 没有交付可读正文时按失败记录；直接媒体文件验收原始字节。评测端不使用外部 PDF/DOCX 解析器补正文，也不在失败后切换引擎或输出模式。'}
SPECS['moli-1.0.6']=moli_release_spec('1.0.6')
SPECS['moli']=moli_release_spec('1.1.5')
SPECS['moli-1.1.7']=moli_release_spec('1.1.7')
SPECS['moli-1.1.3']=moli_release_spec('1.1.3')
SPECS['moli-1.1.11']=moli_release_spec('1.1.11')
SPECS['chromium']=spec(
 'Playwright · Chromium → DOM HTML → node-html-markdown','本地独立浏览器；不需要 Google 账号，不加载个人会话',
 [('url','string','唯一任务输入'),('wait_until','string','load'),('timeout','seconds','45 秒端到端截止'),('converter','string','node-html-markdown 2.0.0')],
 [('content','string','转换后的 Markdown'),('rendered_html_path / sha256','string','转换前 DOM HTML 证据'),('fetch_html_ms / conversion_ms','number','分阶段耗时'),('browser_stderr','string','浏览器诊断；不进入正文')],
 {'url':URL,'engine':'chromium','timeout':45,'wait_until':'load','dump':'html','converter':'node-html-markdown 2.0.0'},'harness/web_fetch/providers/headless_worker.py',
 '网页正文取完整渲染 DOM 后转为 Markdown；图片、视频资源使用共用 DOM 脚本。直接文档使用浏览器自身返回的页面内容，嵌入文档读取已加载的查看器文字；不使用外部文档解析器。DOM 转换、页面获取与原始文件读取均计入耗时；原始字节、HTML 与转换器哈希均保留。')

def method_provenance(out):
 path=out/'implementation-provenance.json'
 return {item['binary_sha256']:item for item in json.loads(path.read_text()).get('implementations',[])} if path.is_file() else {}


def method_version(record, provenance=None):
 """Use executable identity, never a different engine's CDP compatibility number."""
 meta=record.get('provider_metadata') or {}
 name=record.get('provider')
 if name=='kitesurf':return 'Beta · 托管后端版本未公开'
 bound=(provenance or {}).get(meta.get('binary_sha256'),{})
 if bound.get('version'):return bound['version']
 if meta.get('reported_version'):return meta['reported_version']
 value=meta.get('browser_version')
 if name in {'moli','lightpanda','obscura'} and re.fullmatch(r'\d+\.\d+\.\d+\.\d+',str(value)):
  return None
 return value or meta.get('binary_version') or meta.get('moli_version') or meta.get('library_version') or meta.get('version')


def method_access(name, records):
 engine=native_browser_engine(name) or name
 access=HEADLESS_ACCESS.get(name) or HEADLESS_ACCESS.get(engine)
 if access and engine!=name:
  source=browser_method_registry()[name].get('source_url')
  return {**access,'configuration':'使用本轮绑定提交构建的二进制；提交、构建环境与 SHA-256 见环境与复现。','links':[(name+' · 源码分支',source)] if source else access['links']}
 if access is None or name!='moli':return access
 versions=set()
 for row in records:
  if row['provider']!=name:continue
  match=re.fullmatch(r'moli (\d+\.\d+\.\d+)',row.get('provider_metadata',{}).get('reported_version',''))
  if match:versions.add(match.group(1))
 if len(versions)!=1:return access
 version=next(iter(versions))
 return {**access,'links':[(f'Moli {version}',f'https://github.com/lexmount/moli/releases/tag/v{version}')]}


def method_spec(name, record):
 engine=native_browser_engine(name) or name
 if record and record.get('provider_type')=='moli_webfetch':
  base=SPECS['moli-webfetch']
  meta=record.get('provider_metadata') or {}
  policy=meta.get('internal_timeout_policy')
  if policy=='native_default':
   timeout_description='45 秒外层截止；Moli 总等待、HTTP 和连接超时保留该二进制默认值'
   timeout_note='这批调用未传入 Moli 内部超时参数。'
  elif policy=='explicit':
   timeout_ms=meta.get('http_connect_timeout_ms')
   timeout_description=('45 秒外层截止；实测命令同时显式传入 Moli 总等待、HTTP 和连接超时'
                        +((' '+str(timeout_ms)+' 毫秒') if isinstance(timeout_ms,int) else ''))
   timeout_note='这批调用使用保存记录中的显式 Moli 超时，未写成产品默认值。'
  else:
   timeout_description='45 秒外层截止；Moli 内部超时以逐次调用记录为准'
   timeout_note='保存证据未提供可核对的 Moli 内部超时策略。'
  params=[(key,kind,timeout_description if key=='外层截止' else description)
          for key,kind,description in base['params']]
  entry=base['entry'] if name in SPECS else 'CLI · Moli · 按任务预选原生 Markdown、HTML 或原始响应接口'
  return {**base,'entry':entry,'params':params,'notes':base['notes']+' '+timeout_note}
 base=SPECS.get(name) or SPECS[engine]
 meta=(record or {}).get('provider_metadata',{})
 if name=='moli':
  match=re.fullmatch(r'moli (\d+\.\d+\.\d+)',meta.get('reported_version',''))
  if match:base=moli_release_spec(match.group(1))
  else:
   base={**base,'entry':'CLI · Moli','request':{k:v for k,v in base['request'].items() if k!='version'}}
  return {**base,'notes':'实际版本、配置和调用路线见“环境与复现”及逐次调用记录。'}
 if name=='chromium':
  request={**base['request']}
  params=list(base['params'])
  wait=meta.get('navigation_wait_until')
  if wait in {'load','domcontentloaded','networkidle','commit'}:
   request['wait_until']=wait
   params=[(key,kind,wait if key=='wait_until' else description)
           for key,kind,description in params]
  converter_sha=meta.get('converter_sha256')
  notes=base['notes']
  if isinstance(converter_sha,str) and re.fullmatch(r'[0-9a-f]{64}',converter_sha):
   request['converter_sha256']=converter_sha
   params=[(key,kind,
            description+'；包装脚本 SHA-256 '+converter_sha
            if key=='converter' else description)
           for key,kind,description in params]
   notes+=' 转换包装还负责代码块围栏与语言标签；本轮以完整脚本 SHA-256 绑定，不把它写成未修改的包默认行为。'
  return {**base,'request':request,'params':params,'notes':notes}
 native={k:v for k,v in meta.get('native_metadata',{}).items() if k in {'format','script_sha256'}}
 format_name=native.get('format')
 if (name,format_name) not in {('lightpanda','lightpanda_json'),('obscura','obscura_eval')}:return base
 command=('lightpanda fetch URL --dump markdown --json' if name=='lightpanda'
          else 'obscura fetch URL --eval <upstream-native-markdown-and-location>')
 request={**base['request'],'command':command,'native_metadata':native}
 if name=='obscura':request.pop('dump',None)
 return {**base,'entry':'CLI · '+command,'request':request,
         'params':[row for row in base['params'] if name!='obscura' or row[0]!='--dump'],
         'notes':'正文保留同一次调用的原生 Markdown 和最终地址。Lightpanda 解包原生 JSON；Obscura 执行固定哈希的官方 Markdown 脚本。直接文件分别使用 Lightpanda CDP 与 Obscura --dump original；资源和嵌入文档使用共用 DOM 读取脚本，不外部解析文档；获取与输出处理均计入耗时。'}



def browser_task_interface_matrix(cases, providers):
 """Describe selected task routes without receiving answers or measured scores."""
 from collections import Counter
 from types import SimpleNamespace
 from .providers.headless import output_mode_for_case
 capabilities={
  'moli':'网页正文、文档正文、结构和网页媒体信息统一使用原生 Markdown；网页媒体只核对输出中的目标地址。最终网址不参与正文判分。',
  'lightpanda':'内置 Markdown 转换；动态正文题统一使用 CLI --dump markdown --json。iframe 合同只增加官方加载与序列化参数；原生 Markdown 仍不含子文档时如实判失败，不切换 HTML 或共同转换。',
  'obscura':'内置 Markdown 与 LP.getMarkdown 共用官方 JS 实现；本次执行固定版本的同一脚本，并在同次 --eval 中返回最终网址。',
  'chromium':'没有直接输出 Markdown 的原生接口；取得渲染后的 DOM，再使用共同的 node-html-markdown 2.0.0。',
  'kitesurf':'官方 Browser Run /markdown?browser=kitesurf 返回 Markdown；这是托管服务的转换能力，不是客户端共同转换。',
 }
 markdown={
  'moli':'CLI fetch --dump markdown → 内置转换 → Markdown',
  'lightpanda':'CLI fetch --dump markdown --json → 内置转换 → 解包 Markdown；iframe 合同只增加 --load-resources iframe 与 --with-frames',
  'obscura':'CLI --eval 官方固定版本脚本与 location.href → 官方转换 → Markdown',
  'chromium':'Playwright/CDP 导航 → 最终 DOM → 共同转换 → Markdown',
  'kitesurf':'/markdown?browser=kitesurf → 官方托管转换 → Markdown',
 }
 original={
  'moli':'原生 CDP 响应；附件事件使用同一 Moli 原始附件接口',
  'lightpanda':'原生 CDP Response.body()',
  'obscura':'CLI fetch --dump original（原始 HTTP 响应，不运行页面 JS）',
  'chromium':'Playwright/CDP 原始响应或原生下载',
  'kitesurf':'指定 Kitesurf 的官方 CDP 原始响应',
 }
 resource={
  'moli':'CLI fetch --dump markdown --with-frames → 原生 Markdown；共同评分器只识别题目指定的资源 URL',
  'lightpanda':'原生 CDP 执行共用 DOM 元素读取脚本',
  'obscura':'CLI --eval 执行共用 DOM 元素读取脚本；内置 assets 缺少所需 srcset/picture 地址',
  'chromium':'Playwright/CDP 执行共用 DOM 元素读取脚本',
  'kitesurf':'指定 Kitesurf 的官方 CDP 执行共用 DOM 元素读取脚本',
 }
 runtime={'moli':'原生 CDP','obscura':'原生 CDP','lightpanda':'原生 CDP',
          'chromium':'Playwright/CDP','kitesurf':'指定 Kitesurf 的官方 CDP'}
 waits={
  'moli':'CLI 等待 done',
  'lightpanda':'Markdown CLI 等待 done',
  'obscura':'CLI 使用官方默认 load 加自适应静默等待（最多 5 秒）',
  'chromium':'页面提取等待 load',
  'kitesurf':'Markdown API 等待 networkidle2',
 }
 tracks=[]
 from .models import REPORT_FAMILIES, report_family
 for case in cases.values():
  track=report_family(case)
  if track not in tracks:tracks.append(track)
 order=list(REPORT_FAMILIES)
 tracks.sort(key=lambda track:order.index(track) if track in order else len(order))
 rows=[]
 for track in tracks:
  group=[case for case in cases.values() if report_family(case)==track]
  cells={}
  for provider in providers:
   engine=native_browser_engine(provider)
   if engine not in capabilities:raise ValueError('Unregistered native browser participant: '+provider)
   parts=[];counts=Counter()
   for case in group:
    mode=output_mode_for_case(engine,'headless_markdown',SimpleNamespace(
     url=case['url'],track=case.get('track','web'),task_kind=case.get('task_kind','body')))
    counts[mode]+=1
   for mode,count in counts.items():
    if mode=='markdown':
     if track=='document' and engine=='moli':
      description=markdown[engine]+'；HTML/TXT 可直接返回正文，直接 PDF/DOCX 未提供可读正文；评测端不使用外部解析器'
     else:
      description=markdown[engine]+('；文档必须由此接口直接返回文字，不使用外部 PDF/DOCX 解析器' if track=='document' else '')
    elif mode=='document':description=original[engine]+' → 原始 PDF/DOCX；只取得字节不能通过文档正文题，评测端不外部解析'
    elif mode=='file':description=original[engine]+' → 原始文件字节（不转 Markdown）'
    elif mode=='resource':description=resource[engine]+(
      ' → 图片或视频资源地址（直接从原生 Markdown 核对）' if engine=='moli'
      else ' → 图片或视频资源地址（不转 Markdown）')
    elif mode=='document_page':description=runtime[engine]+' 读取嵌入文档 → 已加载 PDF 查看器原生文字，或页面可见文字 → 文档正文；不下载后另行解析'
    elif mode=='media_metadata':description=runtime[engine]+' 执行共用脚本，读取正文、作者及 JSON-LD → 页面正文与媒体元数据（不转 Markdown）'
    else:raise ValueError('Unsupported browser task interface mode: '+mode)
    wait=('读取原始响应或下载文件，不等待查看器绘制' if mode in {'file','document'} else waits[engine])
    if mode not in {'file','document','markdown'}:
     if engine in {'lightpanda','kitesurf'} or (engine=='obscura' and mode in {'document_page','media_metadata'}):
      wait='CDP 页面提取等待 load'
    if mode=='document_page':wait+='；随后等待查看器文档就绪或页面内容'
    parts.append({'count':count,'mode':mode,'description':description,'wait':wait})
   cells[provider]=parts
  rows.append({'track':track,'count':len(group),'cells':cells})
 return {'providers':providers,'capabilities':{name:capabilities[native_browser_engine(name)] for name in providers},'rows':rows}


def render_interfaces(out,records):
 from .artifacts import record_public_call_log_bindings, verify_public_call_log
 dataset_path=out/"dataset.json"
 native_default=dataset_path.is_file() and json.loads(dataset_path.read_text()).get("comparison",{}).get("kind")=="uniform_native_local_measurement"
 def pre(value):return '<pre>'+escape(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2))+'</pre>'
 def fields(rows):return '<dl class="interface-fields">'+''.join('<div><dt><code>'+escape(k)+'</code> <small>'+escape(t)+'</small></dt><dd>'+escape(d)+'</dd></div>' for k,t,d in rows)+'</dl>'
 measured={r['provider'] for r in records}
 names=sorted(set(SPECS)|{name for name in measured if native_browser_engine(name)},key=lambda name:(name not in measured,name))
 body='<p>按本轮适配器介绍，不混用完整产品能力。输入示例展示正文配置，资源配置差异单列；它们不是每条历史请求的逐字回放。各调用所属批次及实际配置见“环境与复现”。输出示例取存档调用，经适配器整理后的真实节选，不冒充原始 HTTP 响应。Key 和账号均用占位符。</p>'
 if native_default:
  body='<p>已测方法的输入展示保存的公开调用字段，输出展示同一次调用的实际节选。完整配置、原始响应和日志见环境与调用详情。未参评方法保留仓库登记示例。</p>'
 for i,name in enumerate(names):
  r=next((r for r in records if r['provider']==name and r['case_id']=='static-simple'),next((r for r in records if r['provider']==name),None))
  s=method_spec(name,r)
  if r and native_default:
   bindings=record_public_call_log_bindings(r)
   if not bindings:
    raise ValueError("Measured native interface requires a saved call log: "+name)
   request=json.loads(verify_public_call_log(out,bindings[-1]).read_text())["request"]
   s={**s,"params":[],"request":request,"notes":"此处展示该次公开调用记录保存的请求字段；完整配置和日志见环境与调用详情。产品内部超时以冻结配置和实际请求为准。"}
  access=method_access(name,records)
  if r:
   content=contained_path(out,r['artifact_path']).read_text();sample={k:r.get(k) for k in ['ok','status_code','title','final_url']};sample['content_excerpt']=content[:450];sample['content_characters']=len(content)
  else:sample={'measurement':None,'note':'仓库登记接口，未纳入本轮；此处不提供虚构的运行输出。'}
  # Explicit whitelist: no local paths, request IDs, credentials or session metadata.
  meta=r.get('provider_metadata',{}) if r else {};sample['metadata']={k:meta[k] for k in ['engine','upstream_status','links_count','images_count','extractor','truncated','document_count'] if k in meta}
  dest=out/'adoption'/'interfaces';dest.mkdir(exist_ok=True);(dest/(name+'.json')).write_text(json.dumps({'interface':s,'access':access,'sample':sample,'attempt_id':r['attempt_id'] if r else None},ensure_ascii=False,indent=2)+'\n')
  body+=f'<details class="interface-panel" id="interface-{name}"'+(' open' if i==0 else '')+'><summary>'+escape(browser_method_label(name))+'</summary>'
  body+='<p class="interface-endpoint"><code>'+escape(s['entry'])+'</code></p><p class="source-note">'+escape(s['auth'])+'</p>'
  if access:
   body+='<h3>账号与获取入口</h3><p>'+escape(access['account'])+'</p><p>'+escape(access['configuration'])+'</p><p>'+ ' · '.join('<a href="'+escape(url,quote=True)+'">'+escape(label)+'</a>' for label,url in access['links'])+'</p><p class="source-note">实际版本和接口见运行记录；下方链接指向官方说明。</p>'
  sample_label='本轮输出节选 · 统一记录' if r else '本轮未执行'
  body+='<div class="guide-grid"><article class="guide-card"><h3>输入</h3>'+fields(s['params'])+('<h4>本轮保存的调用字段</h4>' if r and native_default else '<h4>调用输入示例</h4>')+pre(s['request'])+'</article><article class="guide-card"><h3>输出</h3>'+fields(s['fields'])+'<h4>'+sample_label+'</h4>'+pre(sample)+'</article></div>'
  measured_link='<a href="../attempts/'+escape(r['attempt_id'])+'.html">该次完整输出与判定 →</a>' if r else '未纳入本轮；参数说明为仓库登记模式，不计入本轮结果'
  body+='<p>'+escape(s['notes'])+'</p><p class="source-note">核对实现：<code>'+escape(s['source'])+'</code> · '+measured_link+' · <a href="interfaces/'+name+'.json">示例 JSON</a></p></details>'
 return body
