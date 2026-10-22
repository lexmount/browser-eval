"""Official capability evidence, deliberately independent of evaluation scores."""
import json
from harness.numbers import positive_number
from html import escape
from .report_components import tabs, bilingual_text

from .methods import report_method, report_method_catalog, browser_method_label, method_version, normalized_method_version


def c(status, detail):
    return {'status':status, 'detail':detail}


CAPABILITIES = {name:item['capabilities'] for name,item in report_method_catalog()['methods'].items()
                if item.get('capabilities')}
SOURCES = {key:(item['label'],item['url']) for key,item in report_method_catalog()['sources'].items()}

def task_capabilities(out, names, records, *, sources=None, language="zh"):
    """Describe the configured native task routes, without upgrading product claims."""
    catalog = report_method_catalog() if language == "zh" else report_method_catalog(language)
    sources = {key:(item["label"],item["url"]) for key,item in catalog["sources"].items()} if sources is None else sources
    from .report_interfaces import browser_task_interface_matrix
    measured=[r for r in records if r.get('provider_metadata',{}).get('output_contract')]
    if (not measured or not all(report_method(name).get('kind') == 'browser' for name in names)
            or not (out/'dataset.json').is_file()):return None
    dataset=json.loads((out/'dataset.json').read_text())
    cases={case['id']:case for case in dataset['cases']}
    matrix=browser_task_interface_matrix(cases,names,language=language)
    configured={}
    budget=dataset.get('comparison',{}).get('timeout_seconds')
    budget_zh=(f'外层预算 {budget:g} 秒。' if positive_number(budget) else '外层预算未记录。')
    budget_en=(f'Outer budget: {budget:g} seconds.' if positive_number(budget) else 'Outer budget not recorded.')
    for name in names:
        registered=report_method(name,language=language)
        row={**registered.get('capabilities',{})}
        if not row:continue
        row.update(registered.get('task_capabilities',{}))
        row.update(scope=matrix['capabilities'][name],
                   tested=budget_zh+'接口按任务大类预选；具体请求参数见保存的调用记录。',
                   tested_en=budget_en+' Interfaces are selected by task family; exact request parameters are in the saved call records.')
        source='task-native-'+name
        source_url=report_method(name).get('source_url','')
        sources[source]=(name+(' · 官方接口' if language=='zh' else ' · Official interface'),source_url)
        row['sources']=[*row.get('sources',[]),source]
        configured[name]=row
    return configured

def source_version_checks(names, records):
    """A dated source check applies to its bound version, not every future binary."""
    checks = report_method_catalog()['sources']
    result = {}
    for name in names:
        subset = [row for row in records if row.get('provider') == name]
        registered = report_method(name, subset[0] if subset else None)
        versions = {normalized_method_version(registered, version) if version else None
                    for row in subset for version in [method_version(row)]}
        rows = []
        for key in registered.get('capabilities', {}).get('sources', []):
            source = checks[key]
            checked = source.get('checked_version')
            expected = normalized_method_version(registered, checked) if checked else None
            if expected and versions and None not in versions:
                status = 'matched' if versions == {expected} else 'mismatch'
            else:
                status = 'unverified'
            rows.append({'source': key, 'checked_version': checked,
                         'measured_versions': sorted(v for v in versions if v is not None),
                         'missing_measured_version': not versions or None in versions,
                         'status': status})
        result[name] = rows
    return result


def render_source_version_notice(rows):
    checks = report_method_catalog()['sources']
    english_checks = report_method_catalog('en')['sources']
    notices = []
    for row in rows:
        if row['status'] == 'matched':
            continue
        measured = ', '.join(row['measured_versions']) or 'not recorded'
        checked = row['checked_version'] or 'not recorded'
        source = checks[row['source']]
        zh_status = '资料版本与实测版本不一致' if row['status'] == 'mismatch' else '资料适用版本未确认'
        en_status = 'Source and measured versions differ' if row['status'] == 'mismatch' else 'Source applicability is unverified'
        notices.append('<li>'+bilingual_text(
            f"{zh_status}：核对版本 {checked}；实测版本 {measured}。不能据此认定当前版本具备相同能力。",
            f"{en_status}: checked version {checked}; measured version {measured}. This does not establish the same capability for the measured version.")+
            ' <a href="'+escape(source['url'],quote=True)+'">'+bilingual_text(source['label'],english_checks[row['source']]['label'])+'</a></li>')
    return '<ul class="source-version-notice">'+''.join(notices)+'</ul>' if notices else ''


def render_capabilities(out, names, *, table, cell, link, records=()):
    labels={name:browser_method_label(name) for name in names}
    sources = dict(SOURCES)
    configured=task_capabilities(out,names,records,sources=sources)
    capabilities=configured or {name:report_method(name).get('capabilities') for name in names if report_method(name).get('capabilities')}
    english_catalog=report_method_catalog('en')
    english_sources={key:(item['label'],item['url']) for key,item in english_catalog['sources'].items()}
    english_configured=task_capabilities(out,names,records,sources=english_sources,language='en')
    english_capabilities=english_configured or {name:report_method(name,language='en').get('capabilities') for name in capabilities}
    unknown = sorted(set(names) - set(capabilities))
    version_checks = source_version_checks(names, records)
    evidence = {'source_version_checks': version_checks, 'source_checks': report_method_catalog()['sources'], 'scope': '官方契约/固定源码能力，不是实测成绩',
                'sources': sources, 'methods': {n: capabilities[n] for n in names if n in capabilities}, 'unreviewed': unknown}
    (out / 'adoption' / 'capabilities.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')
    content_rows, runtime_rows, details = [], [], []
    for name in names:
        if name not in capabilities:
            continue
        row = capabilities[name]
        english = english_capabilities[name]
        def localized(key):
            return bilingual_text(row[key],english[key])
        tested = (bilingual_text(row['tested'], row['tested_en']) if row.get('tested_en') else bilingual_text('仓库登记配置示例；实际调用以保存记录为准。', 'Repository configuration example; consult saved records for actual calls.') + localized('tested'))
        method = cell(link('#capability-'+name, labels.get(name,name),browser_method_label(name,language='en')))
        values = []
        for key in ['web', 'document', 'image', 'av', 'file']:
            item = row[key]
            values.append(cell('<strong>'+bilingual_text(item['status'],english[key]['status'])+'</strong><small>'+bilingual_text(item['detail'],english[key]['detail'])+'</small>'))
        content_rows.append(('', method+''.join(values)))
        runtime_rows.append(('', method+cell(bilingual_text(row['js']['status']+' · '+row['js']['detail'],english['js']['status']+' · '+english['js']['detail']))+
                             ''.join(cell(localized(k)) for k in ['input', 'structure', 'access'])))
        source_links = ' · '.join(link(sources[key][1], sources[key][0]+' ↗',english_sources[key][0]+' ↗') for key in row['sources'])
        details.append(f'<details class="interface-panel" id="capability-{escape(name)}"><summary>{escape(labels.get(name,name))} · 范围与官方依据</summary>'+
                       f'<p><strong>对应接口：</strong>{localized("scope")}</p><p>{localized("notes")}</p>'+
                       f'<p><strong>登记配置：</strong>{tested}</p><p class="source-note">{source_links}</p>'+render_source_version_notice(version_checks[name])+'</details>')
    checks = report_method_catalog()['sources']
    used = sorted({source for row in capabilities.values() for source in row.get('sources',[])})
    dates = ' · '.join(link(checks[key]['url'], checks[key]['label']+' · '+checks[key]['checked_at'],english_catalog['sources'][key]['label']+' · '+checks[key]['checked_at'])
                       for key in used if key in checks)
    legend = '<p>'+bilingual_text('官方能力资料不是实测成绩。官方来源及资料核对日期：', 'Official capability documentation is not a measured result. Sources and verification dates: ')+dates+'</p>'
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
