"""English translations for the first adoption-page narrative shard."""

TEXT = {
    "中文": "Chinese",
    "Lexmount 两个区域、Firecrawl、Tavily、Exa、Cloudflare；通过各自 API 调用。":
        "Lexmount in both regions, Firecrawl, Tavily, Exa, and Cloudflare; called through their respective APIs.",
    "CowAgent、OpenClaw、DeepSeek Harness；按本轮保留的实现与配置执行。":
        "CowAgent, OpenClaw, and DeepSeek Harness; run with the implementations and configurations retained for this run.",
    "本地运行": "Local execution",
    "LangChain WebBaseLoader、Crawl4AI、Moli WebFetch；使用本机网络与运行环境。":
        "LangChain WebBaseLoader, Crawl4AI, and Moli WebFetch; using the local network and runtime environment.",
    "内容选择": "Content selection",
    "网页 → Markdown": "Webpage → Markdown",
    "现有 PDF / Office 解析": "Existing PDF / Office parsing",
    "有限支持": "Limited support",
    "Markdown 引用；无独立列表": "Markdown references; no separate list",
    "页面超链接；非媒体接口": "Page hyperlinks; not a media interface",
    "无此能力": "Not supported",
    "本端点只返回 Markdown": "This endpoint returns Markdown only",
    "HTML → 纯文本": "HTML → plain text",
    "PDF / DOCX / TXT 等": "PDF / DOCX / TXT, etc.",
    "不提取图片属性": "Does not extract image attributes",
    "无媒体提取器": "No media extractor",
    "受支持文档落盘": "Supported documents saved to disk",
    "渲染 HTML → Markdown": "Rendered HTML → Markdown",
    "PDF 专用策略": "PDF-specific strategy",
    "media.images + 元数据": "media.images + metadata",
    "原生 HTTP 文件策略或浏览器下载": "Native HTTP file strategy or browser download",
    "拒绝 PDF / Office MIME": "Rejects PDF / Office MIME types",
    "HTML 中的 Markdown 引用": "Markdown references in HTML",
    "页面中的普通超链接": "Ordinary hyperlinks on the page",
    "拒绝二进制 MIME": "Rejects binary MIME types",
    "PDF；Office 未声明": "PDF; Office not declared",
    "普通 links 不保证媒体流": "Ordinary links do not guarantee media streams",
    "无原文件返回字段": "No field for returning the original file",
    "PDF（含 OCR）/ Office": "PDF (including OCR) / Office",
    "images 返回图片 URL": "images returns image URLs",
    "audio / video 下载地址": "audio / video download URLs",
    "rawBase64 原始响应": "rawBase64 raw response",
    "BeautifulSoup → 纯文本": "BeautifulSoup → plain text",
    "PDF / Office 需其他 loader": "PDF / Office requires another loader",
    "load 不返回 src": "load does not return src",
    "load 不返回媒体字段": "load does not return media fields",
    "Document 不是原始文件": "Document is not the original file",
    "未列 PDF / Office 契约": "No PDF / Office contract listed",
    "无专用媒体字段": "No dedicated media fields",
    "无原文件返回契约": "No contract for returning the original file",
    "JS 渲染网页 → Markdown / HTML": "JS-rendered webpage → Markdown / HTML",
    "未声明 PDF / Office 正文解析": "PDF / Office body parsing not declared",
    "页面引用；JSON HTML / eval 可取地址": "Page references; JSON HTML / eval can retrieve URLs",
    "页面入口；HTML / eval 可取地址": "Page entries; HTML / eval can retrieve URLs",
    "原始非 HTML 响应仅支持 html / json 模式":
        "Raw non-HTML responses are supported only in html / json modes",
    "无 PDF / Office 解析器": "No PDF / Office parser",
    "原生 Markdown / 超链接": "Native Markdown / hyperlinks",
    "正文中的普通超链接": "Ordinary hyperlinks in the body",
    "文本输出不是原文件": "Text output is not the original file",
    "Markdown / 纯文本": "Markdown / plain text",
    "Extract 未列格式保证": "Extract lists no format guarantee",
    "无转写/媒体下载契约": "No transcription/media download contract",
    "无原始字节字段": "No raw-byte field",
    "支持 · 浏览器 + 等待参数": "Supported · Browser + wait parameters",
    "URL 或 HTML 字符串；/content、/links、/screenshot 为其他端点。":
        "URL or HTML string; /content, /links, and /screenshot are separate endpoints.",
    "/markdown 返回字符串；AI /json 是独立端点。":
        "/markdown returns a string; AI /json is a separate endpoint.",
    "支持 cookies、HTTP authenticate 等参数；浏览器流量仍可能被识别和拦截。":
        "Supports cookies, HTTP authenticate, and other parameters; browser traffic may still be detected and blocked.",
    "无此能力 · HTTP，不运行 JS": "Not supported · HTTP, no JS execution",
    "HTTP(S) URL；不是本地文件上传接口。": "HTTP(S) URL; not a local file-upload interface.",
    "文本、文档标题和保存路径；没有通用 JSON schema 抽取。":
        "Text, document title, and saved path; no generic JSON schema extraction.",
    "当前工具未提供站点 Cookie 参数；不是登录浏览器。":
        "The current tool provides no site Cookie parameter; it is not a logged-in browser.",
    "支持 · 浏览器 + JS 动作": "Supported · Browser + JS actions",
    "网页 URL；远程或本地 PDF 需专用策略。":
        "Webpage URL; remote or local PDF requires a dedicated strategy.",
    "可选 CSS/JSON 或 LLM 提取策略；media/links 是结构化资源信息。":
        "Optional CSS/JSON or LLM extraction strategies; media/links provides structured resource information.",
    "可配置持久会话、Cookie 和页面动作；并非保证突破每个站点限制。":
        "Configurable persistent sessions, cookies, and page actions; does not guarantee bypassing every site's restrictions.",
    "无此能力 · 匿名 HTTP 获取": "Not supported · anonymous HTTP fetching",
    "HTTP(S) URL；只接受 HTML、text/*、JSON/XML 等文字 MIME。":
        "HTTP(S) URL; accepts only textual MIME types such as HTML, text/*, and JSON/XML.",
    "Turndown 转换正文和表格，返回工具文本与状态。":
        "Turndown converts body content and tables, returning tool text and status.",
    "拒绝 URL 内嵌凭据；跨源重定向需独立调用，不是登录会话浏览器。":
        "Rejects credentials embedded in URLs; cross-origin redirects require a separate call, and this is not a logged-in session browser.",
    "支持 · Contents 动态页抓取": "Supported · Contents dynamic-page crawling",
    "一组 URL；可配置 subpages，不是任意本地文件上传接口。":
        "A set of URLs; subpages is configurable, and this is not an arbitrary local file-upload interface.",
    "text、highlights、summary；摘要可用 schema，含模型生成环节。":
        "text, highlights, and summary; summary supports a schema and includes a model-generation step.",
    "maxAgeHours 控制缓存/实时抓取，不提供登录成功保证。":
        "maxAgeHours controls cached/live crawling; it does not guarantee successful login.",
    "支持 · 渲染与 Actions": "Supported · rendering and Actions",
    "Scrape 接收 URL；本地文件上传走另一个 /v2/parse 接口。":
        "Scrape accepts a URL; local file uploads use the separate /v2/parse endpoint.",
    "可选 JSON schema、产品字段、截图等格式；不能都视为本轮已测。":
        "Optional formats include JSON schema, product fields, and screenshots; they cannot all be treated as tested in this run.",
    "可配置页面动作；站点授权、反爬与套餐限制仍会影响访问。":
        "Page actions are configurable; site authorization, anti-bot measures, and plan limits can still affect access.",
    "单个或多个网页 URL；load 返回 Document 对象。":
        "One or more webpage URLs; load returns Document objects.",
    "page_content + 页面元数据；scrape 可另取 BeautifulSoup 自行提取属性。":
        "page_content + page metadata; scrape can separately return BeautifulSoup for custom attribute extraction.",
    "可传入 requests Session / headers；不自动执行登录交互。":
        "Accepts a requests Session / headers; does not automate login interactions.",
    "支持 · 按需渲染": "Supported · on-demand rendering",
    "URL 或已捕获的 dom_id；/v1/dom/dump 为独立 DOM 捕获接口。":
        "URL or a captured dom_id; /v1/dom/dump is a separate DOM-capture endpoint.",
    "title、author、links、images 等固定字段；可选 trace/raw_dom。":
        "Fixed fields such as title, author, links, and images; optional trace/raw_dom.",
    "API Key + Project ID 是服务鉴权，不是源站登录凭据；公开文档面向公共网页。":
        "API Key + Project ID authenticate the service, not the source site; the public documentation targets public webpages.",
    "支持 · 默认运行 JS，支持多种就绪等待":
        "Supported · runs JS by default and supports multiple readiness waits",
    "单个 URL；每个页面单独调用 fetch。": "A single URL; fetch is called separately for each page.",
    "JSON 返回最终 URL、状态、标题、响应头、重定向链和 HTML；--eval 可返回 DOM 字段。":
        "JSON returns the final URL, status, title, response headers, redirect chain, and HTML; --eval can return DOM fields.",
    "支持授权 cookie-file / profile-dir 和请求头；仍可能被站点拒绝。默认适配器不加载登录状态。":
        "Supports authorized cookie-file / profile-dir and request headers; sites may still reject access. The default adapter does not load login state.",
    "无此能力 · 需另用 Browser": "Not supported · requires a separate Browser",
    "HTTP(S) URL；可选 Markdown 或纯文本。": "HTTP(S) URL; optional Markdown or plain text.",
    "正文与抓取元数据；不提供任意 schema 抽取。":
        "Body content and crawl metadata; no arbitrary schema extraction.",
    "可配置请求头；登录/JS 重页面由独立 Browser 工具处理。":
        "Request headers are configurable; login-heavy or JS-heavy pages are handled by the separate Browser tool.",
    "支持 · 复杂页建议 advanced": "Supported · advanced is recommended for complex pages",
}
