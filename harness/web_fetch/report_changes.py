"""Compact public release history: conclusions and audit evidence, not work logs."""
import json
import shutil
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
HEADLESS_HISTORY_START='2026-09-04-five-browser-fastest-markdown'
FILE_RERUN_EVIDENCE = (
    'file-provider-rerun', 'file-provider-rerun-native',
    'file-provider-rerun-crawl-http', 'file-provider-rerun-crawl-final',
)


def require_review_rounds(data):
    """Keep the compact public review log present across future releases."""
    rounds = data.get('review_rounds')
    if not isinstance(rounds, list) or not rounds:
        raise ValueError('report-changes.json requires a nonempty top-level review_rounds list')
    required = {'date', 'title', 'scope', 'finding', 'fix', 'pending'}
    for index, row in enumerate(rounds, 1):
        if not isinstance(row, dict) or any(not str(row.get(key, '')).strip() for key in required):
            raise ValueError(
                f'Review round {index} must include date, title, scope, finding, fix and pending'
            )
    return rounds


def published_releases(releases, *, require_state=False):
    public=[]
    for release in releases:
        state=release.get('publication_state',None if require_state else 'published')
        if state not in {'published','unpublished'}:
            raise ValueError('Unknown release publication state')
        if state=='published':public.append(release)
    return public


def select_cohort_history(data, dataset, records):
    """Select public history for the measured cohort, including legacy untagged entries.

    New entries may declare ``comparison_kinds``. The existing history predates
    that field, so its first headless release is the stable partition boundary.
    """
    comparison=(dataset or {}).get('comparison') or {}
    kind=comparison.get('kind')
    if not kind and records and all(
        row.get('provider_type') in {'headless_html','headless_markdown'} for row in records
    ):
        kind='headless_markdown'
    if not kind:kind='generic'
    headless=kind in {'headless_html','headless_markdown'}
    releases=published_releases(data.get('releases',[]), require_state=data.get('schema_version',2)>=3)
    boundary=next((i for i,row in enumerate(releases) if row.get('id')==HEADLESS_HISTORY_START),None)
    selected=[]
    for index,release in enumerate(releases):
        declared=release.get('comparison_kinds')
        if declared is not None:
            if kind in declared:selected.append(release)
        elif boundary is None or (index<=boundary)==headless:
            selected.append(release)
    rounds=require_review_rounds(data) if headless else []
    return {**data,'releases':selected,'review_rounds':rounds}


def first_correction(history, releases):
    release=next((r for r in releases
                  if r['id']=='2026-09-03-fairness-correction'),None)
    if release is None:
        raise ValueError('Release 2026-09-03-fairness-correction not found')
    digest=release['scoring_revision_input_sha256']
    matches=[r for r in history if r.get('input_results_sha256')==digest]
    if len(matches)!=1:
        raise ValueError('First correction must bind to exactly one scoring revision')
    return matches[0]


def require_revision_release_bindings(history, releases):
    """Every bundled revision receipt must name exactly one public release.

    Older releases may refer to receipts retained by an earlier report bundle;
    absence from the current bundle is not evidence that they never existed.
    """
    for receipt in history:
        digest = receipt.get('input_results_sha256')
        matches = []
        for release in published_releases(releases):
            declared = release.get('scoring_revision_input_sha256')
            declared = declared if isinstance(declared, list) else [declared]
            if digest in declared:
                matches.append(release)
        if len(matches) != 1:
            raise ValueError('Each bundled scoring revision must bind to exactly one release')


def release_details(release, table, cell, esc):
    rows=[('',cell(esc(a))+cell(esc(b))+cell(esc(c))+cell(esc(d))) for a,b,c,d in release['changes']]
    return ('<details><summary>查看修正依据、影响与边界</summary>'
            +table(['项目','发现的问题','正式修正','结果影响与边界'],rows,metrics=[])
            +'</details>')


def localized_release_history(data, kind):
    """Select one report's release records, newest first, with complete EN labels."""
    releases = [row for row in published_releases(data.get('releases', []), require_state=data.get('schema_version',2)>=3) if kind in row.get('comparison_kinds', [])]
    if not releases or len({row['id'] for row in releases}) != len(releases):
        raise ValueError(kind + ' release history is absent or has duplicate IDs')
    for row in releases:
        en = row.get('i18n', {}).get('en', {})
        if not row.get('date') or not row.get('title') or not en.get('title'):
            raise ValueError(kind + ' release requires a date and bilingual title: ' + row['id'])
    latest = releases[0]
    en = latest['i18n']['en']
    if not latest.get('summary') or not en.get('summary') or len(latest.get('changes', [])) != len(en.get('changes', [])):
        raise ValueError('Current ' + kind + ' release requires bilingual summary and change details')
    if any(len(row) != 4 for row in latest['changes'] + en['changes']):
        raise ValueError(kind + ' release change rows require four columns')
    return releases


def webmainbench_history(data):
    return localized_release_history(data, 'webmainbench_frozen_html_to_markdown')


def render_localized_release_history(data, english, evidence_links=(), *, kind='webmainbench_frozen_html_to_markdown'):
    """Render reader-facing release history without embedding release prose in report.py."""
    from .report import hero, section, table, cell, esc, link
    releases = localized_release_history(data, kind)
    latest, *older = releases
    def localized(row, key):
        return row['i18n']['en'][key] if english else row[key]
    body = hero('Changes' if english else '变更记录', localized(latest, 'summary'))
    rows = [('', ''.join(cell(esc(value)) for value in change))
            for change in localized(latest, 'changes')]
    headings = (['Measure', 'Prior issue', 'Correction', 'Effect and limits'] if english
                else ['项目', '此前问题', '正式修正', '结果影响与边界'])
    details = ('<details><summary>'
               + ('Inspect basis, correction and effects' if english else '查看依据、修正与影响')
               + '</summary>' + table(headings, rows, metrics=[]) + '</details>')
    if evidence_links:
        details += '<p class="actions">' + ' · '.join(
            link(('../../' if english else '../') + path, label_en if english else label_zh)
            for path, label_zh, label_en in evidence_links) + '</p>'
    body += section('Current release' if english else '当前版本',
                    '<article class="release-current" data-release-id="' + esc(latest['id']) + '">'
                    '<div class="eyebrow">' + esc(latest['date']) + '</div><h3>'
                    + esc(localized(latest, 'title')) + '</h3>' + details + '</article>',
                    'current-release')
    if older:
        history = ''.join('<li data-release-id="' + esc(row['id']) + '"><time>'
                          + esc(row['date']) + '</time> · ' + esc(localized(row, 'title'))
                          + '</li>' for row in older)
        body += section('Earlier releases' if english else '早期版本', '<ol>' + history + '</ol>')
    body += '<p class="source-note">' + link(
        '../../report-changes.json' if english else '../report-changes.json',
        'Download the structured release record' if english else '下载结构化版本记录') + '</p>'
    return body


def render_webmainbench_history(data, english, evidence_links=()):
    return render_localized_release_history(data, english, evidence_links)


def render_changes(out, cases, records, dataset=None):
    from .report import hero, section, table, cell, esc, link, write
    from .storage import research_evidence_dir
    evidence_root=research_evidence_dir()
    selection=out/'file-rerun-selection.json'
    rerun_sources=[]
    if selection.exists():
        rerun_sources=[evidence_root/name for name in FILE_RERUN_EVIDENCE]
        missing=[path for path in rerun_sources if not path.is_dir()]
        if missing:
            raise FileNotFoundError(
                'Required file-rerun evidence directories are missing: '
                + ', '.join(str(path) for path in missing))
    data=json.loads((ROOT/'research/report-changes.json').read_text())
    require_review_rounds(data)
    public_data=select_cohort_history(data,dataset,records)
    (out/'report-changes.json').write_text(json.dumps(public_data,ensure_ascii=False,indent=2)+'\n')
    releases=public_data['releases']
    if not releases:
        raise ValueError('No release history applies to the current report cohort')
    latest,*older=releases
    body=hero('报告版本说明','每个版本只记录影响读者判断的正式修正；结论与分数影响直接展示，技术证据按需展开。')
    body+=section('当前版本',
        '<article class="release-current"><div class="eyebrow">'+esc(latest['date'])+'</div><h3>'+esc(latest['title'])+'</h3><p>'+esc(latest['summary'])+'</p>'
        +release_details(latest,table,cell,esc)+'</article>',latest['id'])

    if selection.exists():
        selected=json.loads(selection.read_text())
        grouped=defaultdict(lambda:[0,0])
        for row in selected['records']:
            grouped[row['provider']][0]+=1
            grouped[row['provider']][1]+=row['task_pass']
        rows=[('',cell(esc(p))+cell(str(n))+cell(str(passed))+cell(str(n-passed))) for p,(n,passed) in sorted(grouped.items())]
        for name,source in zip(FILE_RERUN_EVIDENCE,rerun_sources,strict=True):
            shutil.copytree(source,out/name)
        total=sum(v[0] for v in grouped.values());passed=sum(v[1] for v in grouped.values())
        body+=section('本版结果影响',
            '<div class="guide-grid"><article class="guide-card"><h3>文件任务</h3><p>'+str(total)+' 次计划调用全部完成判定：'+str(passed)+' 次通过、'+str(total-passed)+' 次未通过、0 次未测。未通过保留为真实结果，不删除案例。</p></article>'
            '<article class="guide-card"><h3>整份报告</h3><p>当前报告共 '+str(len(cases))+' 个案例、'+str(len({row["provider"] for row in records}))+' 种方法和 '+str(len(records))+' 条权威逻辑结果；其中其他 '+str(max(0,len(records)-total))+' 条未被本次文件补测改写。</p></article></div>'
            '<details><summary>查看文件任务逐方法结果</summary>'+table(['方法','计划并判定','通过','未通过'],rows,metrics=[])+'</details>'
            '<details><summary>查看原始运行与选择证据</summary><p>'+' · '.join(link('../'+path,label) for path,label in [
                ('file-rerun-selection.json','选定运行清单'),('history/file-baseline/results.jsonl','历史文件记录'),
                ('file-provider-rerun/results.jsonl','首轮调用'),('file-provider-rerun-native/results.jsonl','原生文件适配复测'),
                ('file-provider-rerun-crawl-http/results.jsonl','Crawl4AI 原生路径验证'),('file-provider-rerun-crawl-final/results.jsonl','Crawl4AI 最终调用')])
            +'</p><p>累计 48 次调用包含适配验证与复测；榜单固定选入最终配置对应的 36 次，不按最好或最快结果挑选。成功文件可在对应调用页下载并核对格式、字节数与 SHA-256。</p></details>', 'file-gap')

    diagnostic=evidence_root/'file-delivery-check'
    if (diagnostic/'scope.json').exists():
        shutil.copytree(diagnostic,out/'file-delivery-check')
        scope=json.loads((diagnostic/'scope.json').read_text())
        body+=section('独立文件源对照',
            '<p>公共文件另以独立 HTTP 字节链路验证 '+str(scope['control_pass'])+'/'+str(scope['control_calls'])+' 次目标哈希匹配，用于确认源文件与统一字节验收链路可用。该对照不是参评方法，不进入排名，也不替代任何 provider 的调用。</p>'
            '<details><summary>查看独立对照记录</summary><p>'+link('../file-delivery-check/results.jsonl','下载调用与哈希记录')+'</p></details>')

    if older:
        entries=''
        for release in older:
            entries+='<article class="release-archive" id="'+esc(release['id'])+'"><div class="eyebrow">'+esc(release['date'])+'</div><h3>'+esc(release['title'])+'</h3><p>'+esc(release['summary'])+'</p>'+release_details(release,table,cell,esc)+'</article>'
        body+=section('历史版本','<details class="release-history"><summary>展开 '+str(len(older))+' 个较早版本</summary>'+entries+'</details>')

    revision=out/'scoring-revisions.json'
    if revision.exists():
        require_revision_release_bindings(json.loads(revision.read_text()),data['releases'])
    body+=section('范围与下载','<p>'+esc(data['history_scope'])+'</p><p>'+link('../report-changes.json','下载结构化版本记录')+' · '+link('../reliability.json','下载完整性状态')+' · '+link('https://github.com/lexmount/browser-eval/commits/main','查看仓库变更')+'</p>')
    write(out,'changes/index.html','报告版本说明','<div class="change-log">'+body+'</div>')
