"""Public release history: user-visible consequences, not assistant work logs."""
import json
import shutil
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


def first_correction(history, releases):
    release=next(r for r in releases if r['id']=='2026-09-03-fairness-correction')
    digest=release['scoring_revision_input_sha256']
    matches=[r for r in history if r.get('input_results_sha256')==digest]
    if len(matches)!=1:
        raise ValueError('First correction must bind to exactly one scoring revision')
    return matches[0]


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
        for row in first_correction(json.loads(revision.read_text()),data['releases'])['records']:
            name=row['attempt_id'].split('__',1)[0]; totals=grouped[name]
            totals[0]+=bool(row['before']['task_pass']);totals[1]+=bool(row['after']['task_pass'])
            totals[2]+=row['before']['task_pass']!=row['after']['task_pass']
            totals[3]+=not row['after']['task_in_denominator'];totals[4]+=1
        rows=[('',cell(esc(name))+cell(str(v[0]))+cell(str(v[1]))+cell(str(v[2]))+cell(str(v[3]))) for name,v in sorted(grouped.items())]
        body+=section('首次纠正对成绩的影响','<p>下面汇总六类任务的判定变化数，仅用于核对修订，不是跨类型总排名。通过数不含未测；不同指标的改变可能重叠，不能把各修复的影响简单相加。</p><details><summary>逐方法：纠正前后通过数、判定变化与缺测数</summary>'+table(['方法','原通过数','纠正后通过数','判定改变','改为未测'],rows,metrics=[])+'</details><p>'+link('../scoring-revisions.json','下载逐调用的前后分数与规则哈希')+'</p>')
    missing=[r for r in records if cases[r['case_id']].get('task_kind')=='file' and not isinstance(r['metrics'].get('raw_file_pass'),bool)]
    ids=sorted({r['case_id'] for r in missing})
    body+=section('文件任务的证据状态','<p>历史记录缺失的是文件字节证据，不是源文件不存在。文本或一个 URL 不能证明已经收到目标文件；删除案例、把缺测改失败、拿另一种方法的下载冒充原方法，都不会补完评测。</p><p>当前选入榜单的文件调用缺测 '+str(len(missing))+' 条。'+ ('涉及案例：'+'、'.join(link('../datasets/'+key+'.html',cases[key]['name']) for key in ids) if ids else '本次已通过真实接口补测完成判分；未通过不等于未执行。')+'</p>', 'file-gap')
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
    selection=out/'file-rerun-selection.json'
    if selection.exists():
        selected=json.loads(selection.read_text())
        grouped=defaultdict(lambda:[0,0])
        for r in selected['records']:
            grouped[r['provider']][0]+=1
            grouped[r['provider']][1]+=r['task_pass']
        rows=[('',cell(esc(p))+cell(str(n))+cell(str(passed))+cell(str(n-passed))) for p,(n,passed) in sorted(grouped.items())]
        for name in ['file-provider-rerun','file-provider-rerun-native','file-provider-rerun-crawl-http','file-provider-rerun-crawl-final']:
            shutil.copytree(ROOT/'research'/name,out/name)
        total=sum(v[0] for v in grouped.values()); passed=sum(v[1] for v in grouped.values())
        summary=f'<p>2026-09-04 累计执行 {selected["executed_calls"]} 次真实接口调用，按登记的原生文件配置选入 {total} 次：{passed} 次通过、{total-passed} 次未通过，0 次缺测。首次 36 次、适配修正后的 6 次，以及 Crawl4AI 原生文件路径验证 3 次、去除无用浏览器启动后重测 3 次均另存，未按最高分挑选。</p>'
        summary+='<p>Moli 与 Crawl4AI 的 PNG、WebM、MP4 均匹配独立目标 SHA-256。Crawl4AI 正文仍用浏览器，文件预先选择原生 HTTP 策略；不是浏览器失败后的兜底。其他结果包括类型拒绝、仅返回文本/地址、抓取错误。结论限定于本轮接口、配置与预算，不代表产品所有功能。</p>'
        evidence=' · '.join(link('../'+path,label) for path,label in [('file-rerun-selection.json','新旧运行 ID 与选择清单'),('history/file-baseline/results.jsonl','36 条历史缺测记录'),('file-provider-rerun/results.jsonl','首次 36 次接口响应'),('file-provider-rerun-native/results.jsonl','适配修正后的 6 次调用'),('file-provider-rerun-crawl-http/results.jsonl','Crawl4AI 原生文件路径验证'),('file-provider-rerun-crawl-final/results.jsonl','Crawl4AI 最终 3 次文件调用')])
        body+=section('十二种方法：文件补测已完成',summary+'<details><summary>逐方法的已测、通过和未通过数量</summary>'+table(['方法','已测','通过','未通过'],rows,metrics=[])+'</details><details><summary>运行证据、历史记录及选取规则</summary><p>'+evidence+'</p><p>历史字节、文本、调用时间不修改。当前视图仍为 150 个案例、1,800 条选定调用；不是同一时刻的全量重跑。原始文件在具体案例的调用证据中提供。</p></details>')
    body+=section('历史范围与证据','<p>'+esc(data['history_scope'])+'</p><p>'+link('../report-changes.json','下载变更记录')+' · '+link('https://github.com/lexmount/browser-eval/pull/4','对应修复 PR')+'</p>')
    write(out,'changes/index.html','报告变更记录','<div class="change-log">'+body+'</div>')
