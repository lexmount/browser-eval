"""Public release history: user-visible consequences, not assistant work logs."""
import json
import shutil
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


def render_changes(out, cases, records):
    from .report import hero, section, table, cell, esc, link, write
    data=json.loads((ROOT/'research/report-changes.json').read_text())
    shutil.copy2(ROOT/'research/report-changes.json',out/'report-changes.json')
    body=hero('报告变更记录','先看结论变了什么，再按需展开规则、证据和逐方法影响。历史输出、计时与新版规则分别留痕。')
    body+=section('这两列怎么读？','<p><b>已判分/已调用</b>：有足够验收证据、能判定通过或失败的调用数 / 实际调用数。不是通过数，也不是正文完整度。</p><p><b>已执行/总案例</b>：实际调用过的不同案例数 / 本类登记案例数。重复运行不会增加不同案例数。</p><p>例如视频榜单的 8/10 表示 10 次调用中有 8 次可判分、2 次文件任务缺少字节证据；不是通过八例。100/100 仅表示证据覆盖齐全，不表示全部通过。</p>')
    for release in data['releases']:
        rows=[('',cell(esc(a))+cell(esc(b))+cell(esc(c))+cell(esc(d))) for a,b,c,d in release['changes']]
        details='<details><summary>展开逐项变化：原问题 → 修正 → 如何解读</summary>'+table(['项目','之前的问题','本次变化','影响与边界'],rows,metrics=[])+'</details>'
        body+=section(esc(release['date']+' · '+release['title']),'<p>'+esc(release['summary'])+'</p>'+details,release['id'])
    revision=out/'scoring-revisions.json'
    if revision.exists():
        grouped=defaultdict(lambda:[0,0,0,0,0])
        for row in json.loads(revision.read_text())[0]['records']:
            name=row['attempt_id'].split('__',1)[0]; totals=grouped[name]
            totals[0]+=bool(row['before']['task_pass']);totals[1]+=bool(row['after']['task_pass'])
            totals[2]+=row['before']['task_pass']!=row['after']['task_pass']
            totals[3]+=not row['after']['task_in_denominator'];totals[4]+=1
        rows=[('',cell(esc(name))+cell(str(v[0]))+cell(str(v[1]))+cell(str(v[2]))+cell(str(v[3]))) for name,v in sorted(grouped.items())]
        body+=section('首次纠正对成绩的影响','<p>下面汇总六类任务的判定变化数，仅用于核对修订，不是跨类型总排名。通过数不含未测；不同指标的改变可能重叠，不能把各修复的影响简单相加。</p><details><summary>逐方法：纠正前后通过数、判定变化与缺测数</summary>'+table(['方法','原通过数','纠正后通过数','判定改变','改为未测'],rows,metrics=[])+'</details><p>'+link('../scoring-revisions.json','下载逐调用的前后分数与规则哈希')+'</p>')
    missing=[r for r in records if cases[r['case_id']].get('task_kind')=='file' and not isinstance(r['metrics'].get('raw_file_pass'),bool)]
    ids=sorted({r['case_id'] for r in missing})
    body+=section('原文件任务：为什么还有缺测？','<p>历史记录缺失的是文件字节证据，不是源文件不存在。文本或一个 URL 不能证明已经收到目标文件；删除案例、把缺测改失败、拿另一种方法的下载冒充原方法，都不会补完评测。</p><p>缺测 '+str(len(missing))+' 条，涉及 '+str(len(ids))+' 个案例：'+ '、'.join(link('../datasets/'+key+'.html',cases[key]['name']) for key in ids)+'。这些历史记录保留，真实补测必须另存时间、配置、原始字节与耗时。</p>', 'file-gap')
    diagnostic=ROOT/'research/file-delivery-check'
    if (diagnostic/'scope.json').exists():
        shutil.copytree(diagnostic,out/'file-delivery-check')
        scope=json.loads((diagnostic/'scope.json').read_text())
        rs=[json.loads(line) for line in (diagnostic/'results.jsonl').read_text().splitlines()]
        rows=[]
        for r in rs:
            files=r.get('provider_metadata',{}).get('file_evaluation',{}).get('files',[])
            f=files[0] if files else {}
            rows.append(('',cell(esc(r['case_name']))+cell(str(r['status_code']))+cell(str(f.get('bytes','—')))+cell('匹配' if f.get('pass') else '未匹配')+cell(str(r['elapsed_ms'])+' ms')+cell(link('../file-delivery-check/'+f['artifact_path'],'原始文件') if f.get('artifact_path') else '—')))
        body+=section('真实字节对照验证','<p>单独运行 HTTP 下载 → 字节获取 → 格式特征/长度/SHA-256 验收。'+str(scope['control_pass'])+'/'+str(scope['control_calls'])+' 次匹配独立保存的目标哈希。此对照不是十二种参评方法之一，不加入它们的成绩；只能证明本次公共文件与字节链路的状况，不能倒推历史成功。</p><details><summary>查看对照的文件大小、耗时与原始证据</summary>'+table(['案例','HTTP','字节数','目标哈希','端到端耗时','证据'],rows,metrics=[])+'<p>'+link('../file-delivery-check/results.jsonl','对照调用记录')+'</p></details>')
        ready=[('',cell(esc(x['provider']))+cell(esc('、'.join(x['missing_configuration']) or '基础配置检查无缺项'))+cell('字节输出适配未接通；未执行补测')) for x in scope['supplier_readiness']]
        body+=section('十二种方法的补测状态','<p>计划 36 次供应商补测，当前已执行 '+str(scope['executed_supplier_calls'])+' 次。以下是本次环境预检，不代表产品能力判断。发布账号只用于上传报告，不提供供应商调用权限。</p><details><summary>展开方法、配置缺项与字节输出缺口</summary>'+table(['方法','本次配置预检','补测状态'],ready,metrics=[])+'</details>')
    body+=section('历史范围与证据','<p>'+esc(data['history_scope'])+'</p><p>'+link('../report-changes.json','下载变更记录')+' · '+link('https://github.com/lexmount/browser-eval/pull/4','对应修复 PR')+'</p>')
    write(out,'changes/index.html','报告变更记录','<div class="change-log">'+body+'</div>')
