"""Provider-centred introduction and reproducible, sample-scoped adoption counts."""
import json
from pathlib import Path
from .methods import method_info, method_page_model, browser_method_label, method_version
from .report_components import tabs,cards,measurement_presentation
from .report_interfaces import render_interfaces
from .report_capabilities import render_capabilities
from .report_pricing import render_pricing

from .methods import report_method, report_method_catalog

PROFILES = {name: (item['profile']['kind'], item['profile']['description'], item['source_url'])
            for name,item in report_method_catalog()['methods'].items() if item.get('profile')}

def display_name(name):
    return browser_method_label(name)


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

def render_methods(out,records,*,hero,section,table,cell,link,esc,authored=True):
    if records and all(row.get('provider_type') == 'fixture' for row in records):
        body = hero('合成示例适配器', '离线检查安装、评分、证据和报告导航。') + section(
            '示例范围', '<p>返回内容由本地测试适配器生成，不访问网页。成功和失败均为预设场景，不能用来比较浏览器能力。</p>')
        body += section('示例费用', '<p>本例只在本机运行测试适配器，不调用收费接口。</p>', 'method-guide-pricing')
        for name in sorted({row['provider'] for row in records}):
            body += section(esc(name),
                '<p><code>fetch(EvalCase, timeout) → ProviderResult</code></p>'
                '<p>输入为任务定义与超时预算，输出为预设的内容或错误。完整输出见该方法的结果详情。</p>',
                'interface-' + name)
        return body
    dataset=json.loads((out/'dataset.json').read_text()) if (out/'dataset.json').is_file() else {}
    native=measurement_presentation(dataset).native_outputs
    if not authored and not native:
        body=hero('本轮方法与接口','以下内容来自本轮结果记录与适配器契约。')
        anchors={'method-guide-pricing'}
        for name in sorted({row['provider'] for row in records}):
            selected=[row for row in records if row['provider']==name]
            model=method_page_model(name,name,selected)
            anchors.add(model['pricing_anchor'])
            rows=[]
            for row in selected:
                metadata=row.get('provider_metadata',{})
                values=(row.get('provider_type','未记录'),metadata.get('engine','未记录'),
                        method_version(row) or '未记录',
                        metadata.get('extractor','未记录'),metadata.get('output_contract','未记录'))
                if values not in rows:rows.append(values)
            body+=section(esc(name),'<p>'+link('../providers/'+name+'.html','查看该方法的结果')+'</p>'
                          +'<p><code>fetch(EvalCase, timeout) → ProviderResult</code></p>'
                          +table(['适配器类型','记录的引擎','记录的版本','记录的提取方式','记录的输出契约'],
                                 [('', ''.join(cell(esc(str(value))) for value in row)) for row in rows],metrics=[]),
                          'interface-'+name)
        for anchor in sorted(anchors):
            body+=section('费用信息','<p>本轮结果未绑定价格资料；费用取决于调用方配置与服务账单。</p>',anchor)
        return body
    source=Path(__file__).resolve().parents[2]/'research/provider-adoption.json'
    data=json.loads(source.read_text());names=sorted({r['provider'] for r in records})
    counts=adoption_counts(data,sorted(set(PROFILES)|set(names)))
    dest=out/'adoption';dest.mkdir(exist_ok=True)
    (dest/'evidence.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    (dest/'counts.json').write_text(json.dumps({'snapshot':data['generated_at'],'scope':'签名命中线索，不是运行调用次数或全网采用量','methods':counts},ensure_ascii=False,indent=2)+'\n')
    intro=[];rows=[];details=''
    for name in names:
        from .methods import browser_method_registry
        registered=report_method(name,next(row for row in records if row['provider']==name))
        profile=registered.get('profile',{})
        kind=profile.get('kind','未登记 / Not registered')
        desc=profile.get('description','Method description has not been registered.')
        url=registered.get('source_url','')
        r=next(r for r in records if r['provider']==name);info=method_info(r['provider_type'])
        url=browser_method_registry().get(name,{}).get('source_url',info.get('source_url',url))
        if native:
            model=method_page_model(name,name,[row for row in records if row['provider']==name])
            desc=model['contract']
            info={**info,'output':'产品原生返回；保留实际输出与日志','freshness':'缓存与等待选项见本轮固定配置'}
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
    body+=section('参评方法','<p>'+' · '.join(
        link('../providers/'+name+'.html',display_name(name)) for name in names)+'</p>')
    body+=section('能力、价格与接口',tabs('method-guide',[
        ('capabilities','官方能力',render_capabilities(out,names,table=table,cell=cell,link=link,records=records)),
        ('pricing','价格与计费',render_pricing(out,names,table=table,cell=cell,link=link,esc=esc)),
        ('adoption','采用统计',f'<p>{data["generated_at"][:10]} 核验的公开源码候选集，包括搜索结果与登记文件。计数是已核验下限，不是全网采用量；点击数字查看仓库与文件。</p>'+stats+details),
        ('profiles','方法详情','<p>下表介绍本轮实际调用的实现；方法名称可打开其全部已测案例。各组覆盖数量以执行记录为准，功能范围以本轮配置为准。</p>'+table(['方法','形态','做什么','本轮输出','本轮新鲜度设置','来源'],intro)),
        ('interfaces','接口与示例',render_interfaces(out,records)),
        ('scope','统计口径',scope),
    ]))
    return body
