"""Official capability evidence, deliberately independent of evaluation scores."""
import json
from html import escape
from .report_components import tabs

from .methods import report_method, report_method_catalog, browser_method_label


def c(status, detail):
    return {'status':status, 'detail':detail}


CAPABILITIES = {name:item['capabilities'] for name,item in report_method_catalog()['methods'].items()
                if item.get('capabilities')}
SOURCES = {key:(item['label'],item['url']) for key,item in report_method_catalog()['sources'].items()}

def task_capabilities(out, names, records, *, sources=None):
    """Describe the configured native task routes, without upgrading product claims."""
    sources = dict(SOURCES) if sources is None else sources
    from .report_interfaces import browser_task_interface_matrix
    from .methods import native_browser_engine, browser_method_registry
    measured=[r for r in records if r.get('provider_metadata',{}).get('output_contract')]
    if (not measured or not all(report_method(name).get('kind') == 'browser' for name in names)
            or not (out/'dataset.json').is_file()):return None
    dataset=json.loads((out/'dataset.json').read_text())
    cases={case['id']:case for case in dataset['cases']}
    matrix=browser_task_interface_matrix(cases,names)
    configured={}
    budget=dataset.get('comparison',{}).get('timeout_seconds')
    budget_zh=(f'外层预算 {budget:g} 秒。' if isinstance(budget,(int,float)) and not isinstance(budget,bool) and budget>0 else '外层预算未记录。')
    budget_en=(f'Outer budget: {budget:g} seconds.' if isinstance(budget,(int,float)) and not isinstance(budget,bool) and budget>0 else 'Outer budget not recorded.')
    for name in names:
        engine=native_browser_engine(name)
        row={**report_method(name).get('capabilities',{})}
        if not row:continue
        row.update(
            scope=matrix['capabilities'][name],
            web=c('支持','原生网页正文输出' if engine!='chromium' else '原生 DOM 加共同 HTML 转换'),
            document=(c('有限支持','HTML/TXT 可由原生 Markdown 返回；直接 PDF/DOCX 未提供可读正文，评测端不使用外部解析器')
                      if engine=='moli' else
                      c('共同转换','使用浏览器渲染后的完整 HTML，再由固定 HTML-to-Markdown 包转换；不读取查看器私有对象，不使用外部 PDF/DOCX 解析器')
                      if engine=='chromium' else
                      c('原生输出','直接使用浏览器正文接口；不使用外部 PDF/DOCX 解析器')),
            image=c('资源地址 / 字节',
                    '原生 Markdown 由共同评分器识别目标资源 URL；直接文件通过原生响应取得'
                    if engine!='chromium' else
                    '完整渲染 HTML 经过共同转换，评分器从 Markdown 核对资源地址；直接文件通过原生响应取得'),
            av=c('资源与元数据',
                 '完整渲染 HTML 经过共同转换，评分器从 Markdown 核对地址或页面信息；直接文件读取原始字节'
                 if engine=='chromium' else
                 '原生接口读取地址、页面元数据或原始字节；不做媒体理解'),
            file=c('原生获取','使用该浏览器原始响应或附件接口，按固定规则验收字节'),
            structure='按任务交付 Markdown、文档正文、资源地址、媒体元数据或原始文件。',
            notes=('浏览器负责访问与原生产物获取；网页内嵌媒体与正文统一走原生 Markdown，共同评分器只核对目标资源 URL；只有 Chromium 使用标准 HTML 转 Markdown 包。完整分类及边界见报告解读的接口表。'
                   if engine!='chromium' else
                   'Chromium 负责访问和渲染；所有页面类任务都取得完整 HTML，再由固定标准包转成 Markdown。评测端不运行资源提取脚本，也不读取文档查看器私有对象。完整分类及边界见报告解读的接口表。'),
            tested=budget_zh+'接口按任务大类预选；具体请求参数见保存的调用记录。',
            tested_en=budget_en+' Interfaces are selected by task family; exact request parameters are in the saved call records.')
        source='task-native-'+name
        source_url=browser_method_registry().get(name,{}).get('source_url',report_method(name).get('source_url',''))
        sources[source]=(name+' · 官方接口',source_url)
        row['sources']=[source]
        configured[name]=row
    return configured

def render_capabilities(out, names, *, table, cell, link, records=()):
    labels={name:browser_method_label(name) for name in names}
    sources = dict(SOURCES)
    configured=task_capabilities(out,names,records,sources=sources)
    capabilities=configured or {name:report_method(name).get('capabilities') for name in names if report_method(name).get('capabilities')}
    unknown = sorted(set(names) - set(capabilities))
    evidence = {'source_checks': report_method_catalog()['sources'], 'scope': '官方契约/固定源码能力，不是实测成绩',
                'sources': sources, 'methods': {n: capabilities[n] for n in names if n in capabilities}, 'unreviewed': unknown}
    (out / 'adoption' / 'capabilities.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')
    content_rows, runtime_rows, details = [], [], []
    for name in names:
        if name not in capabilities:
            continue
        row = capabilities[name]
        from .report_components import bilingual_text
        tested = (bilingual_text(row['tested'], row['tested_en']) if row.get('tested_en') else bilingual_text('仓库登记配置示例；实际调用以保存记录为准。', 'Repository configuration example; consult saved records for actual calls.') + escape(row['tested']))
        method = cell(link('#capability-'+name, labels.get(name,name)))
        values = []
        for key in ['web', 'document', 'image', 'av', 'file']:
            item = row[key]
            values.append(cell('<strong>'+escape(item['status'])+'</strong><small>'+escape(item['detail'])+'</small>'))
        content_rows.append(('', method+''.join(values)))
        runtime_rows.append(('', method+cell(escape(row['js']['status']+' · '+row['js']['detail']))+
                             ''.join(cell(escape(row[k])) for k in ['input', 'structure', 'access'])))
        source_links = ' · '.join(link(sources[s][1], sources[s][0]+' ↗') for s in row['sources'])
        details.append(f'<details class="interface-panel" id="capability-{escape(name)}"><summary>{escape(labels.get(name,name))} · 范围与官方依据</summary>'+
                       f'<p><strong>对应接口：</strong>{escape(row["scope"])}</p><p>{escape(row["notes"])}</p>'+
                       f'<p><strong>登记配置：</strong>{tested}</p><p class="source-note">{source_links}</p></details>')
    checks = report_method_catalog()['sources']
    used = sorted({source for row in capabilities.values() for source in row.get('sources',[])})
    dates = ' · '.join(link(checks[key]['url'], checks[key]['label']+' · '+checks[key]['checked_at'])
                       for key in used if key in checks)
    legend = '<p>官方能力资料不是实测成绩。Official capability sources and their verification dates: '+dates+'</p>'
    if configured:
        legend='<p>下表说明本报告配置的接口与处理边界，浏览器版本及固定来源见“环境与复现”。共同解析、资源读取脚本和 HTML 转换由评测提供，不计作浏览器内置能力。接口可用不代表每题都能完成；实测结果见结果分析。</p>'
    content = ('<p class="source-note">能力声明不是实测任务成绩；任务通过按登记的稳定内容锚点与字段结构等规则判定，不证明频繁变化值的事实准确性或实时性。文档列指 PDF / Office 中的文字；普通 TXT 不等于文档解析。图片、音视频列说明网页资源取得方式，<strong>不代表视觉理解或转写</strong>；直接媒体 URL 能否取得字节看原文件列。原文件不是页面正文、截图或下载链接本身。</p>'+
               table(['方法 / 依据', '静态网站正文', '文档文字', '图片资源', '音视频资源', '原文件'], content_rows, cls='capability-matrix', metrics=[]))
    runtime = table(['方法 / 依据', 'JS 渲染', '输入范围', '结构化输出', '访问与会话'], runtime_rows, cls='capability-runtime', metrics=[])
    if unknown:
        legend += '<p>尚无已核对的官方能力资料：'+escape('、'.join(unknown))+'。</p>'
    return legend + tabs('capability-views', [('content','内容与资源', content), ('runtime','运行与接入', runtime)]) + (
        '<p class="source-note">模型生成的摘要、OCR 文字、网页截图、资源 URL 与原始字节是不同产物；均不能自动当作独立标准答案。原文件实测仍需核验 MIME、字节数和 SHA-256。访问被拒绝只说明该次访问受限，不等于网站无效或所有 provider 均不支持。</p>'
        '<h3>接口边界与依据</h3><p class="source-note">点击方法名定位；展开查看本轮启用了什么，以及哪些属于其他接口或配置。</p>') + ''.join(details) + '<p>'+link('capabilities.json', '下载能力与来源数据')+'</p>'
