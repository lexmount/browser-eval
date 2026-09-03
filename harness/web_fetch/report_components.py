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
    return cards([
        ('任务通过率','满足该例全部验收规则的次数 ÷ 全部任务。正文检查长度、核对点和错误标记，<strong>没有 F1 最低分门槛</strong>；资源与列表另按专门规则判断。请求失败计入分母。'),
        ('请求成功率','接口正常返回的次数占全部调用的比例。接口返回成功，不代表抓到了目标内容。'),
        ('完整度 · 纯净度 · F1','完整度看正文漏了多少；纯净度看混入多少无关文字；F1 综合两者。必须有完整参考。请求失败计 0；请求成功但任务未通过，仍按实际正文评分。'),
        ('均值 · P50 · P95','仅统计任务通过的耗时。P50 是中位数；P95 表示约 95% 的通过调用不超过该耗时。'),
        ('资源 · 文件 · 信息','资源命中率：资源任务中找齐指定入口的比例；文件取得率：原文件任务中有已验证通过记录的比例（本轮字节验收未接通）；信息通过率：信息任务中通过文字核对的比例。'),
        ('动态条目核验','返回至少 3 条具名详情入口，并独立确认前三条名称对应其详情页。只核验这三个条目，不代表整页召回、当前排名或价格正确。'),
    ])+'<div class="worked-example"><strong>举例</strong><span>10 个任务</span><span>8 次请求成功 → 请求成功率 80%</span><span>6 次任务通过 → 任务通过率 60%</span></div>'


METRIC_DEFINITIONS={
 '请求成功率':'接口正常返回次数 ÷ 全部调用次数；不代表内容正确。',
 '任务通过率':'满足本例全部验收规则的次数 ÷ 全部任务次数；请求失败计入分母。具体规则见上方说明，不设全文 F1 门槛。',
 '结构达标率':'动态列表中取得至少 3 个具名详情入口的任务比例；不判事实正确性。',
 '完整度':'参考正文中有多少被取回；越高越少遗漏。',
 '纯净度':'返回文字中有多少属于参考正文；越高越少杂文。',
 'F1':'综合完整度和纯净度，越高越好；没有完整参考不计算。',
 '资源命中率':'资源任务中，找齐所有指定图片或播放入口的比例。',
 '文件取得率':'文件任务中有原文件验收通过记录的比例；本轮未接通统一字节验收链路，0 不代表官方不支持。',
 '信息通过率':'文字信息任务中，通过指定标题、发布者等核对的比例。',
 '均值':'通过任务的平均耗时，单位毫秒。',
 'P50':'通过任务的中位耗时，单位毫秒。',
 'P95':'通过耗时从小到大排序，取第 ⌈95% × 样本数⌉ 个值，单位毫秒。',
 '耗时':'本次调用从发起到结束的时间，包含失败调用，单位毫秒。',
 '运行 ID':'按执行记录及开始时间生成的稳定本地标识，重测可区分；供应商请求 ID 另见详情。',
 '达标率':'该案例参评方法中完成任务的比例；动态列表还要求前三条内容有独立证据。',
 '结果':'按预设任务规则分为通过或失败；请求错误、字段不足与内容未证实分别列明。',
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
    return '<div class="metric-help" aria-label="本表指标说明"><dl>'+''.join('<div><dt>'+escape(k)+'</dt><dd>'+escape(v)+'</dd></div>' for k,v in entries)+'</dl><p class="source-note">'+missing+'</p></div>'


def task_pass_guide(track, base=''):
    rules = {
        'web': '请求成功，并同时满足本例最低字符数、全部必需核对点、显著标记及结构要求；不得命中非参考正文自身包含的错误页标记。完整度、纯净度和 F1 另算，不作为通过门槛。',
        'document': '文档正文采用与网页相同的文字验收规则：请求、最低长度、全部核对点及错误页检查满足；不是只拿到下载链接。F1 另算，不作为通过门槛。',
        'image': '含图页面须在实际返回值中找齐指定图片地址；直接 PNG 须有文件验收证据。当前原文件字节验收链路未完整接通，其结果不能用于判断产品是否支持下载。',
        'video': '按案例区分：找齐播放入口、核对标题与发布者，或取得原文件证据。链接不等于文件，均不测画面理解；原文件字节验收链路当前未完整接通。',
        'challenge': '请求成功，至少 3 个不同的具名详情入口，且返回顺序中的前三条名称与详情身份均有独立证据；不核对当天名单、名次或价格。缺证据不等于内容错误。',
    }
    text = rules.get(track, '按案例中登记的验收规则逐项检查。')
    return '<div class="pass-contract"><strong>怎样才算任务通过？</strong><p>'+escape(text)+'</p><a href="'+escape(base+'guide/index.html#rules')+'">用具体案例理解判断过程 →</a></div>'


def examples():
    items=[
        ('web','网页正文',[
            ('任务','打开 Books to Scrape 首页，取得本页书目正文。'),
            ('标准答案','独立保存页面完整书目；核对书名、价格与库存。例如首本 A Light in the Attic，£51.77，In stock。'),
            ('怎么测','对照完整正文计算完整度、纯净度与 F1；另检查长度、关键事实及错误页标记。'),
            ('怎样算通过','关键事实与正文规则全部通过。通过不等于没有漏字，仍需看全文指标。'),
        ]),
        ('document','文档正文',[
            ('任务','以 Attention Is All You Need PDF 为例，读取整篇论文；另测 DOCX、TXT 和嵌入文档页面。'),
            ('标准答案','从原始文件全部页面、段落和表格独立提取正文，并保存原文件。'),
            ('怎么测','对照全文计算完整度、纯净度与 F1；检查标题、核心术语及章节。'),
            ('怎样算通过','必须返回文档正文，只返回论文下载地址不通过。<a href="../datasets/document-01.html">查看本例完整参考与逐方法结果 →</a>'),
        ]),
        ('image','图片获取',[
            ('任务','以 MDN 响应式图片页为例，找到指定正文图片；输入直接 PNG 时交付图片文件。'),
            ('标准答案','含图页使用从源页保存的图片地址清单；直接图片使用原文件与校验值。'),
            ('怎么测','逐项匹配允许的图片地址；原文件的目标是核对实际字节与源文件，但本轮字节验收链路未接通，未给出已验证通过记录。'),
            ('怎样算通过','找齐指定入口，或实际返回文件；只复述 PNG 地址不算取得文件。本榜不测“看懂图片”。<a href="../datasets/image-01.html">查看图片参考与结果 →</a>'),
        ]),
        ('video','视频获取',[
            ('任务','找到网页中的播放入口、交付直接视频文件，或读取标题与发布者。'),
            ('标准答案','分别保存源页播放地址、原视频文件或可见标题与发布者，三种任务分开核对。'),
            ('怎么测','例如 MDN 视频页匹配目标播放地址；信息页核对文字事实。直接 MP4 的字节验收链路本轮未接通，不能据此判断产品是否支持下载。'),
            ('怎样算通过','完成指定任务；找到播放地址不等于拿到视频，更不等于能识别画面或字幕。<a href="../datasets/video-01.html">查看视频参考与结果 →</a>'),
        ]),
        ('challenge','真实网站',[
            ('任务','以 JustWatch 剧集目录为例，取得至少 3 个不同剧集的名称和详情入口；电商、社区及商家列表采用相同字段原则。'),
            ('标准答案','独立页面与已保存的公开列表字段提供名称—详情链接对应关系；新条目使用新证据，不要求返回旧快照里的固定名单。'),
            ('结构怎么测','至少 3 个有名称的本站详情链接，按地址去重。仅网址、导航按钮和登录提示不计入；不固定名称、名次、价格或条目 ID。'),
            ('怎样算通过','返回顺序中的前三个不同条目都能独立核实名称与详情身份。未证实不算通过，也不认定为内容错误。实时排名、价格及整页 F1 不在范围内。<a href="../datasets/challenge-10-justwatch.html">查看参考与实测 →</a>'),
        ]),
    ]
    return tabs('task-examples',[(k,l,cards(c)) for k,l,c in items])




def basis_label(c):
    if c.get('task_kind')=='structure':return '名称＋详情核验'
    if c.get('task_kind')=='file':return '原文件'
    resources=c.get('expected_resources',[])
    if resources:return f'资源 · {len(resources)} 项'
    n=len(c.get('required_facts',[]));p=len(c.get('required_patterns',[]))
    return ' · '.join(([f'文字 {n} 项'] if n else [])+([f'列表 {p} 项'] if p else [])) or '未设置'


def reference_label(c):
    if c.get('task_kind')=='structure':return '独立条目证据'
    if c.get('ground_truth_main_content'):return '完整正文'
    if c.get('task_kind')=='file':return '原文件'
    if c.get('expected_resources'):return '资源清单'
    if c.get('track')=='video' and c.get('required_facts'):return '文字信息'
    return '未提供完整参考'


def reference_status(cases):
    full=sum(bool(c.get('ground_truth_main_content')) for c in cases)
    dynamic=sum(c.get('task_kind')=='structure' for c in cases)
    overview=cards([
        (f'{full} 例完整参考','网页与文档按完整参考正文计算全文指标；图片和视频按资源、文件或文字信息核对。'),
        (f'{dynamic} 例动态列表','固定字段与前三条内容核验要求，不固定名单；各方法面对同一组网址，以任务通过率比较。'),
        ('快照范围','知乎 30 条热榜、大众点评 15 家商户、JustWatch 40 部剧集。仅公开列表字段，账号信息与原始登录页面不发布。'),
        ('动态核验边界','确认名称与详情身份，不验证条目实时名次或整页完整度。旧快照之外的条目必须有独立新证据；没有证据的条目不当作内容错误。'),
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
