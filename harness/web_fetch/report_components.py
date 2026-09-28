"""Small, shared presentation components; never change measured results."""
from html import escape


def tabs(group, panels):
    buttons=[]; bodies=[]
    for i,(key,label,body) in enumerate(panels):
        pid=f'{group}-{key}'
        buttons.append(f'<button type="button" role="tab" id="tab-{pid}" aria-controls="{pid}" aria-selected="{str(i==0).lower()}" tabindex="{0 if i==0 else -1}" data-tab="{key}" onclick="chooseTab(\'{group}\',\'{key}\')">{escape(label)}</button>')
        bodies.append(f'<div role="tabpanel" id="{pid}" aria-labelledby="tab-{pid}" data-panel="{key}"'+(' hidden' if i else '')+f'>{body}</div>')
    return f'<div class="tabs" id="{group}"><div class="tab-bar" role="tablist" aria-label="内容选择" onkeydown="tabKeys(event,\'{group}\')">'+''.join(buttons)+'</div>'+''.join(bodies)+'</div>'


def cards(items):
    return '<div class="guide-grid">'+''.join(f'<article class="guide-card"><h3>{title}</h3><p>{body}</p></article>' for title,body in items)+'</div>'


def metric_guide():
    return '<p>静态网站正文、动态网站正文、热门网站正文和文档正文使用同一评分流程。页面来源与加载方式不同，改变的是案例要求，不改变指标定义。</p>'+cards([
        ('统一验收流程','接口返回 → 排除空内容与错误页 → 核对题目要求的文字和字段 → 检查列表数量、类型与唯一性 → 汇总任务结果。只执行该例适用的检查；失败详情保留具体未满足项。图片与视频沿用请求和交付检查，资源地址或文件字节另按格式核验。'),
        ('任务通过率','满足该例全部验收规则的次数 ÷ 已有验收证据的任务。段落、列表、字段都属于内容要求；<strong>没有 F1 最低分门槛</strong>。请求失败计入分母；任何计划调用缺失或无法判分时，整份报告标为不可靠并禁止发布。'),
        ('文字核对覆盖','每例命中的必需文字组数 ÷ 登记组数，再按案例取均值。固定标题、说明、字段标签或事实句均可作为核对组；易变名单和价格不固定为答案。没有文字依据的案例不记作满分，覆盖案例数另列。'),
        ('请求成功率','接口正常返回的次数占全部调用的比例。接口返回成功，不代表抓到了目标内容。'),
        ('完整度 · 纯净度 · F1','必须有完整参考，且与输出的目标范围和内容具有可比性；不能用条目数量推算全文 F1。请求失败计 0；请求成功但任务未通过，仍按实际正文评分，所以 0 次通过不等于 F1 为 0。逐案例计算后取平均；平均 F1 不是平均完整度与平均纯净度的调和平均。'),
        ('均值 · P50 · P95','仅统计该类型任务通过的调用耗时。列表任务与正文任务均按各自声明的规则判定。P50 是中位数；P95 表示约 95% 的通过调用不超过该耗时。'),
        ('资源 · 文件 · 信息','资源命中率：资源任务中找齐指定入口的比例；文件取得率：原文件任务中取得可验证字节的比例；信息通过率：信息任务中通过文字核对的比例。文件缺少字节证据会使报告不完整并禁止发布。'),
        ('稳定任务契约','同时核对登记的稳定内容锚点（稳定标题、栏目、说明）与字段结构、数量、唯一性及类型，不是只检查数量。频繁变化的名单、条目标题和价格按声明的结构与类型核验，不要求与旧快照取值相同。通过不证明这些变化值的事实准确性或实时性。'),
    ])+'<div class="worked-example"><strong>举例</strong><span>10 个任务</span><span>8 次请求成功 → 请求成功率 80%</span><span>6 次任务通过 → 任务通过率 60%</span></div>'


METRIC_DEFINITIONS={
 '文字核对覆盖':'每例命中的必需文字组数 ÷ 登记文字组数，再按案例取均值；请求失败计 0。实际覆盖范围在表前说明。事实句、标题或说明均按组匹配；难度不同，不宜跨榜单直接比较。不是全文完整度，空核对集不计为 100%。',
 '请求成功率':'接口正常返回次数 ÷ 全部调用次数；不代表内容正确。',
 '任务通过率':'满足本例全部验收规则的次数 ÷ 已测任务次数；已测任务的请求失败计入分母。未测排除且单列覆盖，不设全文 F1 门槛。',
 '判分完整性':'可判定通过或失败的调用数 ÷ 计划调用数。它是报告可靠性门槛，不是通过率或正文完整度；任何一行未满都禁止发布排名。',
 '正文通过率':'满足声明的正文验收规则的比例，与完整度、纯净度及 F1 分开计算。',
 '列表核验率':'列表任务中同时满足稳定内容锚点、字段结构、类型、数量及唯一性要求的比例。',
 '结构达标率':'动态列表中取得至少 3 个具名详情入口的任务比例；不判事实正确性。',
 '完整度':'参考正文中有多少被取回；越高越少遗漏。',
 '纯净度':'返回文字中有多少属于参考正文；越高越少杂文。',
 'F1':'逐案例计算完整度与纯净度的调和平均，再对案例 F1 取均值；没有完整参考不计算。0 次通过不等于 F1 为 0。',
 '标题完整度':'同一详情链接对应的参考标题中，有多少词语或汉字被返回。只统计有独立参考的已返回标题，不是榜单召回率。',
 '标题纯净度':'返回标题中，有多少词语或汉字属于该条目的参考标题。附带的按钮、标签等文字会降低分数；不评整页杂文。',
 '标题 F1':'逐标题计算完整度和纯净度的调和平均，再逐案例、跨案例取均值。使用独立记录的名称或别名，不要求条目仍在当前榜单；差异不直接判为内容错误。',
 '标题参考覆盖':'有独立参考的标题数 / 返回标题总数；有可评分标题的案例数 / 本类案例数。无标题和未知身份不计文字分，不能用高分代替任务通过率。',
 '资源命中率':'资源任务中，找齐所有指定图片或播放入口的比例。',
 '文件取得率':'已测文件任务中有原文件验收通过记录的比例；历史缺少字节验收时显示 —，不是 0% 或产品不支持。',
 '信息通过率':'文字信息任务中，通过指定标题、发布者等核对的比例。',
 '均值':'通过任务的平均耗时，单位毫秒。',
 'P50':'通过任务的中位耗时，单位毫秒。',
 'P95':'通过耗时从小到大排序，取第 ⌈95% × 样本数⌉ 个值，单位毫秒。',
 '耗时':'本次调用从发起到结束的时间，包含失败调用，单位毫秒。',
 '运行 ID':'按执行记录及开始时间生成的稳定本地标识，重测可区分；供应商请求 ID 另见详情。',
 '达标率':'该案例中按已登记任务规则判为通过的调用比例；不因缺少全文参考而隐藏任务结果。',
 '结果':'完整报告中的任务均明确判为通过或失败。诊断构建可显示未测，但会被标为不可靠且不能发布。通过不证明事实准确性、实时性或整页完整性。',
 '失败分类':'先按失败环节区分请求失败、请求成功但任务失败，再按失败类别细分原因。“访问被拒绝”不等于网站失效。',
 '命中仓库数':'样本内出现该方法签名的去重仓库数；同一仓库只计一次。',
 '命中文件数':'样本内命中的去重仓库与文件组合数；不是调用次数。',
 '请求':'接口是否正常返回；是否取得目标内容另看任务结果。',
 'HTTP 状态':'已记录的 HTTP 状态码；空值表示接口未提供，不据此推断成功。',
 '输出字符':'该次保存的完整输出字符数量，不代表有效正文量。',
 '输出校验值':'输出文件的 SHA-256，用于检查文件内容是否改变。',
 '具名详情入口':'识别出的不同条目数，每条需有名称和本站详情链接。',
}


def metric_legend(headers):
    entries=[(h,METRIC_DEFINITIONS[h]) for h in headers if h in METRIC_DEFINITIONS]
    if not entries:return ''
    missing='“—”表示未进行可归属的专用采用统计；0 表示本样本未命中。' if '命中仓库数' in headers else '“—”表示不适用、缺少参考或没有可计算的样本，不等于 0。'
    return '<div class="metric-help" aria-label="本表指标说明" data-i18n-attrs="aria-label"><dl>'+''.join('<div><dt>'+escape(k)+'</dt><dd>'+escape(v)+'</dd></div>' for k,v in entries)+'</dl><p class="source-note">'+missing+'</p></div>'


TRACK_CONTEXT = {
    'web': {
        'flow': ['请求网页', '定位正文区域', '去除导航等杂文', '输出正文'],
        'flow_note': '目标是读出页面内容，而不是保存整份网页源码。正文若已在响应中，普通 HTTP 加解析器就可能完成。',
        'meaning': '常见网页的正文提取：从文章、文档站、百科或项目主页中取回要读的内容，剔除导航、广告等干扰。难点是找对正文边界，既不漏段落，也不混入大量杂文。',
        'example': '项目仓库页以 README 为正文：只返回仓库名称和简介不够；把文件目录、导航一起带回来，也可能降低纯净度。',
        'boundary': '这组按正文提取任务选样，没有逐页按渲染方式分组，不能等同于“纯静态网页”。请求成功、任务通过、全文质量分别衡量；不测站内搜索、点击或多页浏览。',
    },
    'dynamic': {
        'flow': ['取得页面外壳', '浏览器运行页面', '加载数据或嵌入子页', '目标内容就绪', '提取正文与字段'],
        'flow_note': '这是常见的浏览器渲染路径，可由 Web Fetch 内部完成，无需先手动打开网页。只取初始 HTML 可能缺正文；但若能直接解析内嵌数据、请求公开接口或读取子页，也不必走完浏览器渲染流程。',
        'meaning': '这里的“动态”指内容的加载方式，不是文字经常更新。目标正文不直接以完整可读文本出现在初始页面中，还需异步取数、客户端组装、等待呈现或加载嵌入子页。难点是从页面壳找到真正的数据。',
        'example': 'Quotes 异步引文页：只拿到页面标题不够，要取得首批引文及每条对应的作者、标签；不能把不同条目的字段拼在一起。',
        'case_id': 'dynamic-quotes-api',
        'boundary': '只测各案例声明的初次自动加载范围，不要求滚动、翻页或点击。执行 JavaScript 不是唯一合格路线，解析内嵌数据或调用公开接口也可。固定演示文字仍核对内容，易变字段不固定旧值。',
    },
    'challenge': {
        'flow': ['请求目标网站', '识别内容页或受限页', '提取稳定说明与栏目', '提取列表与详情链接'],
        'flow_note': '接口正常返回也可能只是登录页或验证页。只有到达目标内容，并满足本例条目要求，才算任务通过。',
        'meaning': '从有风控、登录限制或复杂内容组织的热门网站提取任务正文：既要取得列表，也要保留登记的栏目、说明或固定字段标签。只返回页面壳、登录提示或几个孤立链接，不足以满足有正文核对项的任务。',
        'example': 'IMDb：取得至少三部不同影片的名称与详情链接，还须保留榜单标题和“由常规用户投票”的说明；影片名称、名次与评分不固定为旧值。',
        'case_id': 'challenge-09',
        'boundary': '不向方法提供站点登录凭据，不操作验证码；某一次访问受限不等于网站失效。标题完整度、纯净度和 F1 只衡量返回条目与独立记录名称的文字重合，不测整页正文、榜单召回、实时排名或价格。标题本身也可能被编辑，文字差异不直接改判任务结果。',
    },
    'document': {
        'flow': ['打开文件或查看页', '定位实际文档', '解析页面、段落与表格', '输出文档文字'],
        'flow_note': '下载链接只是入口；正文提取还需读取文件内容。内嵌查看器的菜单、按钮不是文档正文。',
        'meaning': '读取 PDF、DOCX、TXT 以及网页内嵌文档中的文字。与普通网页不同，正文可能在文件或查看器后面；难点是识别文档并保留跨页、段落和表格中的内容。',
        'example': 'Attention Is All You Need PDF：需要论文正文并命中标题、Transformer 等核对点；只返回下载地址不通过，只提取首页会丢失后续全文。',
        'case_id': 'document-01',
        'boundary': '衡量文字提取，不衡量原版排版还原、图表理解，也不能据此推断扫描件 OCR 能力。拿到文件与读出文件内容是两件事，全文质量仍单独计算。',
    },
    'image': {
        'flow': ['读取含图页面', '定位指定正文图片', '解析实际资源地址', '返回图片链接'],
        'flow_note': '这是含图网页的任务路径。直接图片 URL 属于另一类文件任务：请求图片 → 返回原始字节 → 校验文件，不能用链接代替文件。',
        'meaning': '定位网页中的目标图片，或取得直接图片文件。难点是区分正文图片、缩略图和装饰图，并保留可用的资源地址；这不是让方法描述图片里画了什么。',
        'example': 'MDN 响应式图片页：返回参考清单中的两张图片地址。文字写着“这里有图片”不算命中；直接 PNG 任务则不能仅复述输入网址。',
        'case_id': 'image-01',
        'boundary': '资源命中只证明返回了指定地址，不证明图片字节已下载或内容已理解。历史原文件记录缺少字节验收证据，不能用于断言某产品不支持下载。',
    },
    'video': {
        'flow': ['读取视频页面', '定位播放器或信息区', '解析媒体入口或文字', '交付本例指定结果'],
        'flow_note': '按题目返回播放地址，或标题与发布者，不要求每题同时完成所有步骤。直接视频 URL 另走文件下载与字节校验路径。',
        'meaning': '分别测试网页播放入口、直接视频文件，以及视频标题和发布者。难点是辨别播放器外壳、实际媒体地址与文字信息；这三类输出不能互相替代。',
        'example': 'YouTube 的 Me at the zoo 信息任务核对标题与发布者 jawed；MDN 视频页另测播放地址。前者通过，不代表已经下载或看懂了视频。',
        'case_id': 'video-09',
        'boundary': '不测画面理解、语音转写或字幕质量。找到播放入口不等于取得视频字节；历史调用缺少原文件字节验收，相关记录显示未测，不计为产品失败。',
    },
}


def task_pass_guide(track, base='', cases=None):
    rules = {
        'dynamic': '核验初次加载范围：稳定内容锚点（稳定标题、栏目、说明）与条目的必需字段、数量、唯一性和类型共同决定任务通过。频繁变化的名单、条目标题和价格按声明的结构与类型核验，不固定旧快照取值。HTTP 页面壳不通过；渲染、内嵌数据和公开 API 均可作为实现路径。全文质量仅在有完整、可比的参考时另算。',
        'web': '请求成功，并同时满足本例最低字符数、全部必需核对点、显著标记及结构要求；不得命中非参考正文自身包含的错误页标记。完整度、纯净度和 F1 另算，不作为通过门槛。',
        'document': '文档正文采用与网页相同的文字验收规则：请求、最低长度、全部核对点及错误页检查满足；不是只拿到下载链接。F1 另算，不作为通过门槛。',
        'image': '含图页面须在实际返回值中找齐指定图片地址；直接 PNG 须交付文件字节并通过格式、长度与目标 SHA-256 验收。',
        'video': '按案例区分：找齐播放入口、核对标题与发布者，或交付通过格式、长度与目标 SHA-256 验收的原文件。链接不等于文件，均不测画面理解。',
        'challenge': '请求成功，取得至少 3 条具名详情链接，字段完整、类型正确、条目不重复，链接符合目标站点规则；有独立内容证据的稳定标题、栏目、说明还须命中。频繁变化的名单和价格不固定旧值。通过表示满足上述取数要求，不证明变化值真实或最新。',
    }
    text = rules.get(track, '按案例中登记的验收规则逐项检查。')
    coverage=[c['body_requirements'] for c in (cases or {}).values() if c.get('body_requirements')]
    if coverage:
        thresholds=' / '.join(f'{v:.0%}' for v in sorted({r['minimum_coverage'] for r in coverage}))
        text+=f' 已登记正文覆盖规则的案例，还须在最多 {max(len(r["regions"]) for r in coverage)} 个参考区域中逐区达到 {thresholds} 词语计数覆盖，不能只返回标题或开头。'
    for c in (cases or {}).values():
        fields=c.get('acceptance_contract',{}).get('field_minimum_records',{})
        if fields:
            labels={'financing':'分期','shipping':'配送'}
            text+=' '+c['name']+' 的字段覆盖要求：'+'、'.join(f'{labels.get(k,k)}至少 {v} 条' for k,v in fields.items())+'，各归属于完整且唯一的记录。'
    context = TRACK_CONTEXT.get(track)
    if not context:
        return '<div class="pass-contract"><strong>怎样才算任务通过？</strong><p>'+escape(text)+'</p></div>'
    example_link = ''
    if context.get('case_id') in (cases or {}):
        example_link = ' <a href="'+escape(base+'datasets/'+context['case_id']+'.html')+'">查看案例 →</a>'
    return ('<div class="track-guide" data-track-guide="'+escape(track)+'">'
            '<p class="track-meaning">'+escape(context['meaning'])+'</p>'
            '<figure class="track-flow" aria-label="典型处理流程"><figcaption>典型处理流程</figcaption><ol>'
            + ''.join('<li><span>'+escape(step)+'</span></li>' for step in context['flow'])+'</ol>'
            '<p>'+escape(context['flow_note'])+'</p></figure>'
            '<div class="pass-contract"><strong>怎样才算任务通过？</strong><p>'+escape(text)+'</p></div>'
            '<details class="track-more"><summary>任务例子与评测边界</summary><dl>'
            '<div><dt>一个例子</dt><dd>'+escape(context['example'])+example_link+'</dd></div>'
            '<div><dt>范围与边界</dt><dd>'+escape(context['boundary'])+'</dd></div></dl>'
            '<a href="'+escape(base+'methodology/index.html#task-examples-'+track)+'">任务示例与完整判定规则 →</a></details></div>')


def examples():
    items=[
        ('web','静态网站正文',[
            ('任务','打开 Books to Scrape 首页，取得本页书目正文。'),
            ('标准答案','独立保存页面完整书目；核对书名、价格与库存。例如首本 A Light in the Attic，£51.77，In stock。'),
            ('怎么测','对照完整正文计算完整度、纯净度与 F1；另检查长度、关键事实及错误页标记。'),
            ('怎样算通过','关键事实与正文规则全部通过。通过不等于没有漏字，仍需看全文指标。'),
        ]),
        ('dynamic','动态网站正文',[
            ('任务','读取各案例声明的初次加载范围。例如 GraphQL 首批 20 条评论、React 目录 16 个商品、Swagger 的 20 条接口摘要，或 iframe 中的 14 个分类名称。各案例独立评分。'),
            ('任务契约','独立观察初次加载范围，登记稳定内容锚点（稳定标题、栏目、说明）及条目数量与字段结构，例如商品名称、数值价格、可见分期字段。仅频繁变化的条目标题、价格不设为永久答案，稳定内容仍核对。'),
            ('怎么测','核对稳定内容锚点，并检查各条目的字段归属、数量、唯一性与类型；支持声明的字段顺序和常见排版。完整、可比的文本参考可用于独立重合诊断，不等于变化值的事实准确性或实时性判定。'),
            ('怎样算通过','请求成功且本例全部正文规则通过。限定首批记录，不暗含滚动、翻页或点击操作；缺少计划调用或判定会使报告不完整，不能发布。'),
        ]),
        ('document','文档正文',[
            ('任务','以 Attention Is All You Need PDF 为例，读取整篇论文；另测 DOCX、TXT 和嵌入文档页面。'),
            ('标准答案','从原始文件全部页面、段落和表格独立提取正文，并保存原文件。'),
            ('怎么测','输入原始文档 URL。方法能直接返回正文时直接使用；只返回 PDF、DOCX 等原始字节时，在同一计时预算内本地解析。取得可读正文后停表，再对照全文计算完整度、纯净度与 F1，并检查标题、核心术语及章节。'),
            ('怎样算通过','必须返回文档正文，只返回论文下载地址不通过。<a href="../datasets/document-01.html">查看本例完整参考与逐方法结果 →</a>'),
        ]),
        ('image','图片获取',[
            ('任务','以 MDN 响应式图片页为例，找到指定正文图片；输入直接 PNG 时交付图片文件。'),
            ('标准答案','含图页使用从源页保存的图片地址清单；直接图片使用原文件与校验值。'),
            ('怎么测','逐项匹配允许的图片地址；直接文件核对实际字节的格式特征、长度与目标 SHA-256。'),
            ('怎样算通过','找齐指定入口，或实际返回文件；只复述 PNG 地址不算取得文件。本榜不测“看懂图片”。<a href="../datasets/image-01.html">查看图片参考与结果 →</a>'),
        ]),
        ('video','视频获取',[
            ('任务','找到网页中的播放入口、交付直接视频文件，或读取标题与发布者。'),
            ('标准答案','分别保存源页播放地址、原视频文件或可见标题与发布者，三种任务分开核对。'),
            ('怎么测','例如 MDN 视频页匹配目标播放地址；信息页核对文字事实；直接 MP4 核对实际字节的格式特征、长度与目标 SHA-256。'),
            ('怎样算通过','完成指定任务；找到播放地址不等于拿到视频，更不等于能识别画面或字幕。<a href="../datasets/video-01.html">查看视频参考与结果 →</a>'),
        ]),
        ('challenge','热门网站正文',[
            ('任务','以 JustWatch 为例取得声明数量的不同剧集及名称、详情入口；任务结果与耗时进入热门网站主表。缺少完整参考只影响全文指标，不隐藏任务结果。'),
            ('核对依据','独立页面提供稳定内容锚点与条目结构要求：剧集名称和本站详情入口，不固定当天的剧集名单。'),
            ('结构怎么测','至少 3 个有名称的本站详情链接，按地址去重。仅网址、导航按钮和登录提示不计入；频繁变化的条目名称、名次、价格或 ID 不固定为旧快照取值，不免除登记的稳定内容核对。'),
            ('怎样算通过','稳定内容锚点与必需字段、类型、唯一性和数量全部通过。通过不证明变化值的事实准确性或实时性；缺少完整、可比的参考时完整度、纯净度和 F1 为 —。<a href="../datasets/challenge-10-justwatch.html">查看参考与实测 →</a>'),
        ]),
    ]
    order = ['web', 'dynamic', 'challenge', 'document', 'image', 'video']
    items.sort(key=lambda item: order.index(item[0]))
    return tabs('task-examples',[(k,l,cards(c)) for k,l,c in items])


def task_contract_guide():
    return cards([
        ('任务契约','按声明范围同时核对稳定内容锚点（稳定标题、栏目、说明）和条目数量、必需字段、唯一性和字段类型，不是只检查数量。频繁变化的名单、条目标题、价格和名次只按声明的结构、类型与数量核验，不固定旧快照取值；这不等于证明这些变化值真实或最新。'),
        ('结果与规则绑定','任务结果对应已登记的内容锚点、字段要求与数量范围；保留原始输出及评分规则，便于逐项复核。'),
        ('完整性与模式','原始输出哈希和证据完整性检查保留。已验证的 stable_contract 按数据不变量验收，不依赖快照时效；exact_snapshot 模式仍可按其协议检查参考。两种模式不能混用，也不声称所有调用满足同一采集窗口。'),
        ('全文诊断独立','完整度、纯净度与 F1 必须有完整参考，且与输出的目标范围和内容具有可比性；不能用条目数量推算全文 F1。缺少完整、可比的参考显示 —（全文指标未评测），任务通过率和耗时仍保留。稳定内容锚点仍用于任务验收；文本重合不证明变化值的事实准确性或实时性，名单变化本身不使稳定任务契约过期。'),
    ])


def list_rule_description(c):
    if c.get('acceptance_contract'):
        return stable_rule_description(c)
    contract = c.get('source_benchmark', {})
    minimum = contract.get('minimum_entries', 3)
    if contract.get('verification_scope') == 'first_three_name_detail_pairs':
        return f'本例按名称—链接身份核验：至少 {minimum} 个具名详情入口，返回的前 {minimum} 条须有独立内容证据；不要求当天固定名单、排序或价格。未证实不等于内容错误。'
    return f'本例按登记的稳定内容锚点及字段／数量规则核验，至少 {minimum} 个不同的具名详情入口；稳定标题、栏目、说明按登记内容核对，频繁变化的条目标题和价格只核对声明的结构与类型。通过不证明变化值的事实准确性或实时性。'



SCHEMA_LABELS = {
    'table_row':'表格行',
    'earthquake':'地震条目',
    'quote':'引文列表', 'product':'商品列表', 'catalog_product':'商品目录',
    'review':'评论列表', 'name_description':'名称与说明', 'api_operation':'接口摘要',
    'taxonomy_family':'科名列表', 'named_link':'具名详情入口',
    'linked_product':'具名商品详情', 'page_identity':'页面身份',
}
FIELD_REQUIREMENTS = {
    'magnitude':('震级','有效的数字震级，不固定某次地震的数值。'),
    'location':('地点','同一地震条目的非空地点文字，不能是占位内容。'),
    'time':('发生时间','包含有效日期和时分秒；支持 ISO 时间及页面显示的 UTC 偏移。'),
    'depth':('深度','同一地震条目中带 km 单位的数值。'),
    'quote':('引文','至少 3 个字符，含文字，不能只是网址、金额或占位内容。'),
    'author':('作者','至少 2 个字符，含文字，不能是占位内容。'),
    'tags':('标签','非空文字，至少 2 个字符，不能是占位内容。'),
    'name':('名称','至少 2 个字符，含文字，不能只是网址、金额或占位内容。'),
    'title':('标题','具名文字，至少 2 个字符；不能只是网址或导航按钮。'),
    'price':('价格','带货币标记的非负金额，最多 2 位小数；支持 $、£、€、¥ 或 USD、EUR、GBP、CNY。'),
    'description':('说明','至少 3 个字符，含文字，不能只是网址、金额或占位内容。'),
    'reviews':('评论数','非负整数，可带 review / reviews 标签。'),
    'date':('日期','有效的 YYYY-MM-DD 日历日期。'),
    'text':('评论正文','至少 3 个字符，含文字，不能只是网址、金额或占位内容。'),
    'method':('请求方法','GET、POST、PUT、PATCH、DELETE、HEAD、OPTIONS、TRACE 或 CONNECT。'),
    'path':('接口路径','以单个 / 开头的接口路径，允许路径参数；不是完整网址。'),
    'summary':('接口说明','至少 3 个字符，含文字，不能只是网址、金额或占位内容。'),
    'family':('科名','英文字母组成且以 idae 结尾。'),
    'url':('详情链接','HTTP(S) 链接，符合本例允许的域名与详情路径；不得含用户名或密码。'),
}


def task_instruction(c):
    contract = c.get('acceptance_contract')
    if not contract:
        reference = c.get('ground_truth_main_content', '')
        if c.get('track') == 'document' and 0 < len(reference) < 100:
            return '读取这个最小文档的全部可见正文。该样本只检查能否取得短文档文字，不代表多页、表格或附录能力。'
        return c.get('task_prompt', '')
    from .stable_tasks import SCHEMA_FIELDS
    if contract['schema'] == 'page_identity':
        return '核对声明的页面、栏目或查询身份；不以变化中的条目名单作为固定答案。'
    if contract['schema'] == 'body_text':
        instruction = '提取页面声明范围内的正文。'
        if contract.get('stable_text_anchors') or contract.get('page_patterns'):
            instruction += '同时核对下方列明的稳定文字与页面要求。'
        if c.get('body_requirements'):
            instruction += '正文还须达到下方各区域的覆盖要求。'
        return instruction
    fields = '、'.join(FIELD_REQUIREMENTS[f][0] for f in SCHEMA_FIELDS[contract['schema']])
    if contract['schema'] == 'table_row':
        fields = '、'.join(col['label'] for col in contract.get('table_columns', []))
    instruction = f'取得至少 {contract["minimum_records"]} 条完整、不重复的记录，每条包含{fields}。'
    if contract.get('stable_text_anchors') or contract.get('page_patterns'):
        instruction += '同时核对下方列明的稳定文字与页面要求。'
    return instruction + '变化字段按声明的格式核验，不固定旧值。'


def stable_rule_description(c):
    contract = c['acceptance_contract']
    if contract['schema'] == 'body_text':
        return ('页面正文按登记的稳定文字锚点核验；新闻、日期、轮播卡片、仓库状态等'
                '易变内容不固定为答案。历史完整快照只作诊断，不进入全文质量汇总。')
    if contract['schema'] == 'page_identity':
        return '页面身份：所有独立登记的稳定文字组均须命中；不声明或虚构条目数量。'
    maximum = contract.get('maximum_records')
    limit = f'至少 {contract["minimum_records"]} 条' + (f'，最多 {maximum} 条' if maximum is not None else '')
    return (f'{SCHEMA_LABELS.get(contract["schema"], contract["schema"])}：{limit}完整且唯一的记录。'
            '按登记的稳定文本锚点与页面模式核对稳定标题、栏目、说明，同时检查条目字段归属、类型、完整性和唯一性。'
            '频繁变化的名单和价格不固定旧快照取值；通过不证明这些变化值真实或最新。')


def stable_contract_details(c):
    from .stable_tasks import SCHEMA_FIELDS, UNIQUE_FIELDS
    contract = c['acceptance_contract']
    schema = contract['schema']
    fields = ''.join('<li><strong>'+escape(FIELD_REQUIREMENTS[f][0])+'</strong>：'+escape(FIELD_REQUIREMENTS[f][1])+'</li>' for f in SCHEMA_FIELDS[schema])
    unique = '＋'.join(FIELD_REQUIREMENTS[f][0] for f in UNIQUE_FIELDS[schema])
    if schema == 'table_row':
        types = {
            'text': '非空文字',
            'token': '符合声明格式的非空字段',
            'integer': '整数',
            'number': '数值',
            'currency_usd': '美元金额',
            'date_dmy': '有效的日/月/年日期',
            'date_ordinal_en': '有效的英文序数日期',
        }
        fields = ''.join('<li><strong>'+escape(col['label'])+'</strong>：'+escape(types[col['type']])+'</li>'
                         for col in contract.get('table_columns', []))
        labels = {col['name']: col['label'] for col in contract.get('table_columns', [])}
        unique = '＋'.join(labels.get(key, key) for key in contract.get('record_identity_fields', []))
    requirements = ('' if schema in {'body_text','page_identity'} else
        '<h3>必需字段与要求</h3><ul>'+fields+'</ul><p>各字段须属于同一条记录，不得跨条目拼接。按'+escape(unique)+'核对唯一性。</p>')
    if schema == 'name_description':
        requirements += '<p>说明不能仅重复名称。</p>'
    if schema in {'product','catalog_product'}:
        requirements += '<p>如返回分期或运费字段，分期须为正整数期数及货币金额；运费须为免费或货币金额。</p>'
    if schema in {'named_link','linked_product'}:
        requirements += '<p>允许域名：'+escape('、'.join(contract.get('hosts',[])))+'；详情路径：<code>'+escape(contract.get('entry_path',''))+'</code>。入口按域名与路径去重。</p>'
    if schema == 'linked_product':
        requirements += '<p>价格须在同一商品详情入口的声明范围内，并符合本例的币种与价格格式。</p>'
    patterns = ''.join('<li><code>'+escape(p)+'</code></li>' for p in contract.get('page_patterns', []))
    anchors = ''.join('<li>'+escape(a['id'])+'：'+escape(' / '.join(a['alternatives']))+('（不能由包含商品条目标题的文字行代替）' if a.get('match_scope')=='non_record_lines' else '')+'</li>'
                      for a in contract.get('stable_text_anchors', []))
    annotation = (c.get('mutable_body_policy',{}).get('stable_annotation',{})
                  or c.get('source_benchmark',{}).get('stable_annotation',{}))
    evidence = ((c.get('stable_text_evidence') or {}).get('path')
                or annotation.get('anchor_evidence'))
    provenance = ('<p>独立文字依据：<a href="../'+escape(evidence,quote=True)+'">查看限定范围原文</a>。'
        '这些核对项验证文字是否保留，不证明整页正文完整，也不以榜单当前成员为答案。</p>') if evidence else ('<p>本例未登记稳定文字依据；文字核对覆盖不计算，已执行的列表规则与结果仍保留。</p>' if annotation else '')
    return ('<p>'+escape(stable_rule_description(c))+'</p>'+requirements
            + provenance
            + ('<h3>稳定文本锚点</h3><p>每组至少命中一种允许表达，所有组均须通过。</p><ul>'+anchors+'</ul>' if anchors else '')
            + ('<h3>页面模式</h3><p>以下模式均须匹配。</p><ul>'+patterns+'</ul>' if patterns else ''))


def basis_label(c):
    if c.get('acceptance_contract'):return '稳定内容＋字段结构契约'
    if c.get('task_kind')=='structure':return '名称＋详情身份核验' if c.get('source_benchmark',{}).get('verification_scope')=='first_three_name_detail_pairs' else '登记内容＋字段规则'
    if c.get('task_kind')=='file':return '原文件'
    resources=c.get('expected_resources',[])
    if resources:return f'资源 · {len(resources)} 项'
    n=len(c.get('required_facts',[]));p=len(c.get('required_patterns',[]))
    return ' · '.join(([f'文字 {n} 项'] if n else [])+([f'列表 {p} 项'] if p else [])) or '未设置'


def reference_label(c):
    annotation = (c.get('mutable_body_policy',{}).get('stable_annotation',{})
                  or c.get('source_benchmark',{}).get('stable_annotation',{}))
    if c.get('acceptance_contract') and annotation.get('structural_only_fields') and c.get('ground_truth_main_content'):return '快照文字诊断；含非固定字段'
    if c.get('ground_truth_main_content'):return '完整正文'
    if c.get('task_kind')=='structure':return '列表任务依据；无完整文本参考'
    if c.get('task_kind')=='file':return '原文件'
    if c.get('expected_resources'):return '资源清单'
    if c.get('track')=='video' and c.get('required_facts'):return '文字信息'
    return '未提供完整参考'


def comparable_body_reference(c):
    """Exclude snapshots whose lexical scope still includes nonliteral fields.

    Keep the measured values in attempt details; do not silently use them as
    invariant text quality or replace them with partial-anchor precision.
    """
    annotation = (c.get('mutable_body_policy',{}).get('stable_annotation',{})
                  or c.get('source_benchmark',{}).get('stable_annotation',{}))
    return bool(c.get('ground_truth_main_content')) and not bool(
        c.get('acceptance_contract') and annotation.get('structural_only_fields'))


def reference_status(cases):
    full=sum(bool(c.get('ground_truth_main_content')) for c in cases)
    dynamic=sum(c.get('task_kind')=='structure' for c in cases)
    overview=cards([
        (f'{full} 例完整参考','全文指标须有完整、可比的参考正文；图片和视频按资源、文件或文字信息核对。'),
        (f'{dynamic} 例列表任务','按登记的稳定内容锚点、字段、类型、数量及唯一性验收；任务通过率与耗时进入对应主表，不固定当天名单。'),
        ('快照范围','知乎 30 条热榜、大众点评 15 家商户、JustWatch 40 部剧集。仅公开列表字段，账号信息与原始登录页面不发布。'),
        ('动态核验边界','稳定标题、栏目、说明按登记内容核对；频繁变化的名单和价格只按声明的字段结构、类型、数量及唯一性核验，不证明变化值的事实准确性与实时性。完整参考缺失不影响任务结果展示。'),
    ])
    links=[f'<a href="{c["id"]}.html">{escape(c["name"])}</a>' for c in cases if c['id'] in {'challenge-04','challenge-06','challenge-10-justwatch'}]
    return overview+('<details><summary>参考示例</summary><p>'+' · '.join(links)+'</p><p>来源快照与独立访问的条件见各案例。访问受限与网站失效分开判断。</p></details>' if links else '')


def sources_panel():
    return '<p>方法参考以下公开项目；本报告使用自己的实时源页快照，不是其官方成绩。</p>'+cards([
        ('<a href="https://github.com/Murrough-Foley/web-content-extraction-benchmark">WCXB</a>','完整正文与词语重合评分的参考。'),
        ('<a href="https://github.com/scrapinghub/article-extraction-benchmark">Scrapinghub</a>','正文抽取的精确率、召回率与 F1。'),
        ('<a href="https://mozilla.github.io/pdf.js/">PDF.js</a>','直接 PDF 与嵌入文档阅读器案例。'),
        ('<a href="https://github.com/mdn/learning-area">MDN 媒体示例</a>','图片和视频入口案例。'),
    ])
