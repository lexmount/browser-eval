"""Official capability evidence, deliberately independent of evaluation scores."""
import json
from html import escape
from .report_components import tabs

CHECKED_AT = '2026-09-02'
SOURCES = {
 'cow': ('CowAgent · WebFetch 源码', 'https://github.com/zhayujie/CowAgent/blob/ed5bb344cfe42cee443c8266ba47ee666d8d7471/agent/tools/web_fetch/web_fetch.py'),
 'lex': ('Lexmount · Extract 契约', 'https://browser.lexmount.com/docs/webfetch/reference/all-in-one-extract'),
 'lex-overview': ('Lexmount · WebFetch 概览', 'https://browser.lexmount.com/en/docs/webfetch'),
 'fire': ('Firecrawl · Scrape 格式', 'https://docs.firecrawl.dev/features/scrape'),
 'fire-doc': ('Firecrawl · 文档解析', 'https://docs.firecrawl.dev/features/document-parsing'),
 'tav': ('Tavily · Extract 契约', 'https://docs.tavily.com/documentation/api-reference/endpoint/extract'),
 'tav-depth': ('Tavily · 提取深度', 'https://docs.tavily.com/documentation/best-practices/best-practices-extract'),
 'tav-research': ('Tavily · Research 文件上传', 'https://www.tavily.com/blog/what-tavily-shipped-in-may-26'),
 'exa': ('Exa · Contents 参数', 'https://exa.ai/docs/reference/contents-api-guide-for-coding-agents'),
 'cf': ('Cloudflare · Markdown 端点', 'https://developers.cloudflare.com/browser-run/quick-actions/markdown-endpoint/'),
 'claw': ('OpenClaw · Web Fetch 文档', 'https://github.com/openclaw/openclaw/blob/12c4bdf7482f24c85b011e9d858fdec3c245101c/docs/tools/web-fetch.md'),
 'claw-code': ('OpenClaw · 抽取实现', 'https://github.com/openclaw/openclaw/blob/12c4bdf7482f24c85b011e9d858fdec3c245101c/src/agents/tools/web-fetch.ts'),
 'claw-html': ('OpenClaw · HTML 转换器', 'https://github.com/openclaw/openclaw/blob/12c4bdf7482f24c85b011e9d858fdec3c245101c/src/agents/tools/web-fetch-utils.ts'),
 'claw-reader': ('OpenClaw · Readability 插件', 'https://github.com/openclaw/openclaw/blob/12c4bdf7482f24c85b011e9d858fdec3c245101c/extensions/web-readability/web-content-extractor.ts'),
 'dsh': ('DeepSeek Harness · MIME 策略', 'https://github.com/deepseek-ai/deepseek-harness/blob/dd6322d604e00eec1ba5e0c8541159906a21094a/packages/web/web-fetch-http/src/policy.ts'),
 'dsh-text': ('DeepSeek Harness · Markdown 转换', 'https://github.com/deepseek-ai/deepseek-harness/blob/dd6322d604e00eec1ba5e0c8541159906a21094a/packages/web/tool-web/src/fetch.ts'),
 'lc': ('LangChain · WebBaseLoader 源码', 'https://github.com/langchain-ai/langchain-community/blob/main/libs/community/langchain_community/document_loaders/web_base.py'),
 'lc-loaders': ('LangChain · 其他文档加载器', 'https://docs.langchain.com/oss/python/integrations/document_loaders/index'),
 'c4-media': ('Crawl4AI · 链接与媒体', 'https://docs.crawl4ai.com/core/link-media/'),
 'c4-pdf': ('Crawl4AI · PDF 策略', 'https://docs.crawl4ai.com/advanced/pdf-parsing/'),
 'c4-download': ('Crawl4AI · 文件下载', 'https://docs.crawl4ai.com/advanced/file-downloading/'),
 'c4-config': ('Crawl4AI · 配置', 'https://docs.crawl4ai.com/api/parameters/'),
}

# Each cell identifies the contract, not a score or an extrapolated success rate.
def c(status, detail):
    return {'status': status, 'detail': detail}

CAPABILITIES = {
 'cowagent-built-in': {
  'scope': 'WebFetch.execute', 'sources': ['cow'],
  'web': c('支持', 'HTML → 纯文本'), 'document': c('支持', 'PDF / DOCX / TXT 等'),
  'image': c('无此能力', '不提取图片属性'), 'av': c('无此能力', '无媒体提取器'),
  'file': c('有限支持', '受支持文档落盘'), 'js': c('无此能力', 'HTTP，不运行 JS'),
  'input': 'HTTP(S) URL；不是本地文件上传接口。',
  'structure': '文本、文档标题和保存路径；没有通用 JSON schema 抽取。',
  'access': '当前工具未提供站点 Cookie 参数；不是登录浏览器。',
  'notes': 'PDF 使用 pypdf 读取文字层，不含 OCR；DOCX 读取段落。另有 XLSX、PPTX 解析分支并依赖相应库。虽然扩展名列表包含 XLS/PPT，实际采用 openpyxl/python-pptx，不能据此保证旧二进制格式可解析。文档下载成功可返回本地路径；不是任意图片或视频下载器。',
  'tested': '本轮调用同一 WebFetch；本机是否安装解析依赖、文件是否能解析仍看实测，支持声明不等于必然成功。',
 },
 'lexmount-global': {
  'scope': 'POST /v1/extract · 国际区', 'sources': ['lex', 'lex-overview'],
  'web': c('支持', 'main_text'), 'document': c('未声明', '未列 PDF / Office 契约'),
  'image': c('仅链接', 'images'), 'av': c('未声明', '无专用媒体字段'),
  'file': c('未声明', '无原文件返回契约'), 'js': c('支持', '按需渲染'),
  'input': 'URL 或已捕获的 dom_id；/v1/dom/dump 为独立 DOM 捕获接口。',
  'structure': 'title、author、links、images 等固定字段；可选 trace/raw_dom。',
  'access': 'API Key + Project ID 是服务鉴权，不是源站登录凭据；公开文档面向公共网页。',
  'notes': 'images 是资源引用，不是图片字节或视觉理解；links 可能包含文档或视频入口，但不保证提取视频流。PDF/Office、音视频转写和通用二进制下载均未在所核对的 Extract 契约中明确声明，不能写成确定不支持。',
  'tested': '国际区与中国区共用公开契约，但部署与实测分开；这里不推断两区内部引擎完全相同。',
 },
 'firecrawl': {
  'scope': 'POST /v2/scrape', 'sources': ['fire', 'fire-doc'],
  'web': c('支持', 'Markdown / HTML'), 'document': c('支持', 'PDF（含 OCR）/ Office'),
  'image': c('仅链接', 'images 返回图片 URL'), 'av': c('需配置', 'audio / video 下载地址'),
  'file': c('需配置', 'rawBase64 原始响应'), 'js': c('支持', '渲染与 Actions'),
  'input': 'Scrape 接收 URL；本地文件上传走另一个 /v2/parse 接口。',
  'structure': '可选 JSON schema、产品字段、截图等格式；不能都视为本轮已测。',
  'access': '可配置页面动作；站点授权、反爬与套餐限制仍会影响访问。',
  'notes': 'rawBase64 必须单独请求，解码后是目标 URL 的原始 HTTP 响应体，不包含页面引用的所有资源。audio/video 针对支持的视频站点，返回有时效的下载 URL，不保证任意站点或原编码字节，也不是字幕转写/画面理解。文档解析覆盖 PDF、Word、Excel、PowerPoint 等。',
  'tested': '本轮仅请求 markdown、links、images；未启用 rawBase64、audio、video、JSON 或截图，未调用 Parse。原文件榜不能代表这些未测模式。',
 },
 'tavily': {
  'scope': 'POST /extract', 'sources': ['tav', 'tav-depth', 'tav-research'],
  'web': c('支持', 'Markdown / 纯文本'), 'document': c('未声明', 'Extract 未列格式保证'),
  'image': c('仅链接', 'include_images'), 'av': c('未声明', '无转写/媒体下载契约'),
  'file': c('未声明', '无原始字节字段'), 'js': c('支持', '复杂页建议 advanced'),
  'input': '单个或批量 URL；Extract 与 Search、Research 是不同接口。',
  'structure': 'raw_content 和图片列表；query 可选择相关片段，并非必然返回全文。',
  'access': '公开 Extract 参数没有源站登录会话接口；advanced 不等于保证绕过登录。',
  'notes': 'advanced 面向动态页、表格和嵌入内容；不能把 embedded media 理解为下载视频或理解画面。官方 Research 的 PDF/Markdown/JSON/TXT 文件上传能力不属于本次 Extract；Extract 的 PDF/Office 支持在本次核对资料中未明确承诺。',
  'tested': '本轮 basic + markdown + include_images=true，不传 query；未测试 advanced 或 Research 文件上传。',
 },
 'exa': {
  'scope': 'POST /contents', 'sources': ['exa'],
  'web': c('支持', 'text → Markdown'), 'document': c('支持', 'PDF；Office 未声明'),
  'image': c('仅链接', 'extras.imageLinks'), 'av': c('未声明', '普通 links 不保证媒体流'),
  'file': c('未声明', '无原文件返回字段'), 'js': c('支持', 'Contents 动态页抓取'),
  'input': '一组 URL；可配置 subpages，不是任意本地文件上传接口。',
  'structure': 'text、highlights、summary；摘要可用 schema，含模型生成环节。',
  'access': 'maxAgeHours 控制缓存/实时抓取，不提供登录成功保证。',
  'notes': 'extras.links 和 imageLinks 是地址；PDF text 是解析后的文字，不是原 PDF。summary/highlights 与正文原文提取的语义不同，生成摘要不能作为独立 ground truth。',
  'tested': 'text=true、maxAgeHours=0，并取 links/imageLinks；本轮没有调用摘要或高亮生成。',
 },
 'cloudflare-markdown': {
  'scope': 'Browser Run · /markdown', 'sources': ['cf'],
  'web': c('支持', '网页 → Markdown'), 'document': c('未声明', '现有 PDF / Office 解析'),
  'image': c('有限支持', 'Markdown 引用；无独立列表'), 'av': c('有限支持', '页面超链接；非媒体接口'),
  'file': c('无此能力', '本端点只返回 Markdown'), 'js': c('支持', '浏览器 + 等待参数'),
  'input': 'URL 或 HTML 字符串；/content、/links、/screenshot 为其他端点。',
  'structure': '/markdown 返回字符串；AI /json 是独立端点。',
  'access': '支持 cookies、HTTP authenticate 等参数；浏览器流量仍可能被识别和拦截。',
  'notes': '页面中的链接可能被保留，不是媒体专用抽取保证。/pdf 是把网页打印成 PDF，不是读取已有 PDF；Workers AI 的 AI.toMarkdown 文档转换也不是本轮 /markdown。',
  'tested': '只调用 /markdown，waitUntil=networkidle2；未提供源站登录 Cookie，未调用其他端点。',
 },
 'openclaw-built-in': {
  'scope': 'web_fetch · 内置 HTTP 路径', 'sources': ['claw', 'claw-code', 'claw-html', 'claw-reader'],
  'web': c('支持', 'Readability → Markdown'), 'document': c('无此能力', '无 PDF / Office 解析器'),
  'image': c('有限支持', '原生 Markdown / 超链接'), 'av': c('有限支持', '正文中的普通超链接'),
  'file': c('无此能力', '文本输出不是原文件'), 'js': c('无此能力', '需另用 Browser'),
  'input': 'HTTP(S) URL；可选 Markdown 或纯文本。',
  'structure': '正文与抓取元数据；不提供任意 schema 抽取。',
  'access': '可配置请求头；登录/JS 重页面由独立 Browser 工具处理。',
  'notes': 'Readability 的 HTML 输出经自带转换器处理，不直接提取 img.src；只有服务端原生 Markdown 或正文普通超链接等路径可能留下图片地址。不能保证收集图片或播放入口。PDF/媒体二进制误解码成字符串不是原文件支持；Firecrawl 等可选回退是另一路径。',
  'tested': '本轮开启 Readability、关闭 Firecrawl 回退及缓存；不能把整个 OpenClaw 的浏览器或 PDF 工具能力算到此行。',
 },
 'deepseek-harness-built-in': {
  'scope': 'web_fetch · HTTP provider', 'sources': ['dsh', 'dsh-text'],
  'web': c('支持', 'HTML → Markdown'), 'document': c('无此能力', '拒绝 PDF / Office MIME'),
  'image': c('有限支持', 'HTML 中的 Markdown 引用'), 'av': c('有限支持', '页面中的普通超链接'),
  'file': c('无此能力', '拒绝二进制 MIME'), 'js': c('无此能力', '匿名 HTTP 获取'),
  'input': 'HTTP(S) URL；只接受 HTML、text/*、JSON/XML 等文字 MIME。',
  'structure': 'Turndown 转换正文和表格，返回工具文本与状态。',
  'access': '拒绝 URL 内嵌凭据；跨源重定向需独立调用，不是登录会话浏览器。',
  'notes': '依据本轮固定源码的内容类型白名单；PDF、image/*、video/* 不在可解码类型中。HTML 内出现媒体链接不意味着可直接抓取对应二进制 URL。',
  'tested': '仅匿名 HTTP provider；不是 DeepSeek 模型 API，也没有使用模型作评分。',
 },
 'langchain-webbase': {
  'scope': 'WebBaseLoader.load', 'sources': ['lc', 'lc-loaders'],
  'web': c('支持', 'BeautifulSoup → 纯文本'), 'document': c('无此能力', 'PDF / Office 需其他 loader'),
  'image': c('无此能力', 'load 不返回 src'), 'av': c('无此能力', 'load 不返回媒体字段'),
  'file': c('无此能力', 'Document 不是原始文件'), 'js': c('无此能力', 'HTTP，不运行 JS'),
  'input': '单个或多个网页 URL；load 返回 Document 对象。',
  'structure': 'page_content + 页面元数据；scrape 可另取 BeautifulSoup 自行提取属性。',
  'access': '可传入 requests Session / headers；不自动执行登录交互。',
  'notes': 'get_text 不保留 href/src 属性；页面若本身写出 URL，文字中仍可能出现地址。LangChain 有独立 PDF、Word 和浏览器加载器，但不能合并成 WebBaseLoader.load 的能力。',
  'tested': '只调用 load 读取正文；没有额外 soup 解析、PDF loader 或浏览器渲染。',
 },
 'crawl4ai': {
  'scope': '正文 AsyncWebCrawler；文件 AsyncHTTPCrawlerStrategy', 'sources': ['c4-media', 'c4-pdf', 'c4-download', 'c4-config'],
  'web': c('支持', '渲染 HTML → Markdown'), 'document': c('需配置', 'PDF 专用策略'),
  'image': c('仅链接', 'media.images + 元数据'), 'av': c('仅链接', 'media.videos / audios'),
  'file': c('需配置', '原生 HTTP 文件策略或浏览器下载'), 'js': c('支持', '浏览器 + JS 动作'),
  'input': '网页 URL；远程或本地 PDF 需专用策略。',
  'structure': '可选 CSS/JSON 或 LLM 提取策略；media/links 是结构化资源信息。',
  'access': '可配置持久会话、Cookie 和页面动作；并非保证突破每个站点限制。',
  'notes': 'PDF 需 PDFCrawlerStrategy + PDFContentScrapingStrategy，可选提取并保存内嵌图片，默认不含扫描件 OCR。浏览器下载需 accept_downloads 并触发下载；原生 HTTP 文件策略直接将非 HTML 响应保存到 downloaded_files。网页截图和打印 PDF 是新生成的文件，不是目标原文件。',
  'tested': '正文使用原通用浏览器策略、无缓存；文件补测在调用前选择原生 HTTP 文件策略，读取 downloaded_files 并统一校验。未启用专用 PDF 策略、截图或 LLM 提取；媒体字段不代表内容理解。',
 },
}
SOURCES['moli'] = ('Moli · WebFetch skill', 'https://github.com/lexmount/moli/blob/91ad8c5fd4e50663323569401ec07704380ce52c/skills/moli-webfetch/SKILL.md')
SOURCES['moli-recipes'] = ('Moli · 输出与接口边界', 'https://github.com/lexmount/moli/blob/91ad8c5fd4e50663323569401ec07704380ce52c/skills/moli-webfetch/references/fetch-recipes.md')
CAPABILITIES['moli-webfetch'] = {
 'scope':'moli fetch；正文 markdown，原文件 html 字节输出', 'sources':['moli','moli-recipes'],
 'web':c('支持','JS 渲染网页 → Markdown / HTML'),
 'document':c('未声明','未声明 PDF / Office 正文解析'),
 'image':c('有限支持','页面引用；JSON HTML / eval 可取地址'),
 'av':c('有限支持','页面入口；HTML / eval 可取地址'),
 'file':c('有限支持','原始非 HTML 响应仅支持 html / json 模式'),
 'js':c('支持','默认运行 JS，支持多种就绪等待'),
 'input':'单个 URL；每个页面单独调用 fetch。',
 'structure':'JSON 返回最终 URL、状态、标题、响应头、重定向链和 HTML；--eval 可返回 DOM 字段。',
 'access':'支持授权 cookie-file / profile-dir 和请求头；仍可能被站点拒绝。默认适配器不加载登录状态。',
 'notes':'截图和 PDF 是启用 --layout 后生成的页面产物，不是读取已有文档或下载原媒体。官方未承诺文档 OCR、媒体理解或音频转写；非 HTML 响应不支持 Markdown。',
 'tested':'正文使用 Markdown，文件任务预先选择 html 原始字节模式；done + 私网屏蔽，无 layout、额外资源加载或 eval，无失败后格式回退。只有实际执行记录参与榜单。',
}
CAPABILITIES['lexmount-cn'] = {**CAPABILITIES['lexmount-global'], 'scope': 'POST /v1/extract · 中国区'}

def render_capabilities(out, names, *, table, cell, link):
    unknown = sorted(set(names) - set(CAPABILITIES))
    evidence = {'checked_at': CHECKED_AT, 'scope': '官方契约/固定源码能力，不是实测成绩',
                'sources': SOURCES, 'methods': {n: CAPABILITIES[n] for n in names if n in CAPABILITIES}, 'unreviewed': unknown}
    (out / 'adoption' / 'capabilities.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')
    content_rows, runtime_rows, details = [], [], []
    for name in names:
        if name not in CAPABILITIES:
            continue
        row = CAPABILITIES[name]
        method = cell(link('#capability-'+name, name))
        values = []
        for key in ['web', 'document', 'image', 'av', 'file']:
            item = row[key]
            values.append(cell('<strong>'+escape(item['status'])+'</strong><small>'+escape(item['detail'])+'</small>'))
        content_rows.append(('', method+''.join(values)))
        runtime_rows.append(('', method+cell(escape(row['js']['status']+' · '+row['js']['detail']))+
                             ''.join(cell(escape(row[k])) for k in ['input', 'structure', 'access'])))
        sources = ' · '.join(link(SOURCES[s][1], SOURCES[s][0]+' ↗') for s in row['sources'])
        details.append(f'<details class="interface-panel" id="capability-{escape(name)}"><summary>{escape(name)} · 范围与官方依据</summary>'+
                       f'<p><strong>对应接口：</strong>{escape(row["scope"])}</p><p>{escape(row["notes"])}</p>'+
                       f'<p><strong>本轮配置：</strong>{escape(row["tested"])}</p><p class="source-note">{sources}</p></details>')
    legend = ('<p>核对日期：'+CHECKED_AT+'。托管服务按官方文档；内置工具按固定源码。此处说明可选能力，<strong>不是实测成绩</strong>；没有启用的选项不补算进榜单。</p>'
              '<dl class="capability-legend"><div><dt>支持 / 需配置</dt><dd>有对应实现或契约；需配置表示要启用额外模式。</dd></div>'
              '<div><dt>仅链接 / 有限支持</dt><dd>仅返回地址，或只覆盖指定输入、正文区域。</dd></div>'
              '<div><dt>无此能力 / 未声明</dt><dd>前者有明确实现边界；后者是官方资料不足，不能当作不支持。</dd></div></dl>')
    content = ('<p class="source-note">能力声明不是实测任务成绩；任务通过按登记的稳定内容锚点与字段结构等规则判定，不证明频繁变化值的事实准确性或实时性。文档列指 PDF / Office 中的文字；普通 TXT 不等于文档解析。图片、音视频列说明网页资源取得方式，<strong>不代表视觉理解或转写</strong>；直接媒体 URL 能否取得字节看原文件列。原文件不是页面正文、截图或下载链接本身。</p>'+
               table(['方法 / 依据', '网站正文', '文档文字', '图片资源', '音视频资源', '原文件'], content_rows, cls='capability-matrix', metrics=[]))
    runtime = table(['方法 / 依据', 'JS 渲染', '输入范围', '结构化输出', '访问与会话'], runtime_rows, cls='capability-runtime', metrics=[])
    if unknown:
        legend += '<p>尚无已核对的官方能力资料：'+escape('、'.join(unknown))+'。</p>'
    return legend + tabs('capability-views', [('content','内容与资源', content), ('runtime','运行与接入', runtime)]) + (
        '<p class="source-note">模型生成的摘要、OCR 文字、网页截图、资源 URL 与原始字节是不同产物；均不能自动当作独立标准答案。原文件实测仍需核验 MIME、字节数和 SHA-256。访问被拒绝只说明该次访问受限，不等于网站无效或所有 provider 均不支持。</p>'
        '<h3>接口边界与依据</h3><p class="source-note">点击方法名定位；展开查看本轮启用了什么，以及哪些属于其他接口或配置。</p>') + ''.join(details) + '<p>'+link('capabilities.json', '下载能力与来源数据')+'</p>'
