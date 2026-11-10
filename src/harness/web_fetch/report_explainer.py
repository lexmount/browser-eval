"""Bound report interpretations and a population-driven default summary."""

import base64
import hashlib
import json
import re
import statistics
from collections import Counter
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from lexbench_webfetch.metrics import percentile, task_passed
from lexbench_webfetch.paths import contained_path

from harness.record_io import canonical_json_sha256
from harness.web_fetch.artifacts import record_public_call_log_bindings, verify_public_call_log
from harness.web_fetch.methods import (
    browser_method_label,
    method_versions,
    native_browser_engine,
    with_measured_method_labels,
)
from harness.web_fetch.models import REPORT_FAMILIES as BROWSER_FAMILY_NAMES
from harness.web_fetch.models import report_family
from harness.web_fetch.providers.headless import engine_definition
from harness.web_fetch.report_components import (
    bilingual_text,
    comparable_body_reference,
    task_instruction,
)
from harness.web_fetch.report_i18n import pair, t
from harness.web_fetch.report_interfaces import browser_task_interface_matrix, method_provenance
from harness.web_fetch.report_resources import render_resource_comparison
from harness.web_fetch.report_style import STYLE


def _mean(values):
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else None


def _table(headers, rows, caption):
    headers = [t('no', 'No.')] + list(headers)
    rows = [(str(index), *row) for index, row in enumerate(rows, 1)]
    head = ''.join('<th scope="col">'+(bilingual_text(*x) if isinstance(x,tuple) else escape(x))+'</th>' for x in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{x}</td>' for x in row) + '</tr>' for row in rows)
    return (('<p class="editorial-table-hint">' + t('scroll_horizontally_to_view_every_column', 'Scroll horizontally to view every column.') + f'</p><div class="editorial-table" role="region" tabindex="0" aria-label="{escape(caption)}"><table><caption>{escape(caption)}</caption><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'))


def _headless_article_input(cases, records, providers):
    """Bind measured article inputs while ignoring report-only annotations."""
    derived_fields = {'diagnosis', 'failure_category', 'title_quality'}
    def measured_record(record):
        result = {key: value for key, value in record.items() if key not in derived_fields}
        if 'provider_metadata' in result:
            result['provider_metadata'] = {key: value for key, value in result['provider_metadata'].items()
                if key != 'local_report_complete_diagnostic_links'}
        return result
    return {
        'cases': cases,
        'records': sorted(
            [measured_record(record) for record in records if record['provider'] in providers],
            key=lambda record: record['attempt_id']),
    }


def _article_speed_population(cases, records, providers, *, execution_scope):
    """Describe one common-pass diagnostic over a complete declared matrix."""
    indexed = {}
    for record in records:
        if record['provider'] not in providers:
            continue
        key = (record['provider'], record['case_id'])
        if key in indexed:
            raise ValueError('Article latency requires one authoritative record per provider and task')
        indexed[key] = record
    if any((provider, cid) not in indexed for provider in providers for cid in cases):
        raise ValueError('Article latency requires the complete declared matrix')
    common = sorted(cid for cid, case in cases.items() if all(
        task_passed(indexed[provider, cid]['ok'], indexed[provider, cid]['metrics'],
                    case.get('task_kind', 'body'))
        for provider in providers))
    if not common:
        raise ValueError('Article latency has no commonly accepted tasks')
    rows = []
    for provider in providers:
        records_for_provider = [indexed[provider, cid] for cid in common]
        values = [record['elapsed_ms'] for record in records_for_provider]
        times = [record['started_at'] for record in records_for_provider if record.get('started_at')]
        rows.append({
            'provider': provider,
            'attempts': len(common),
            'values_ms': values,
            'mean_ms': statistics.mean(values),
            'p50_ms': percentile(values, .5),
            'p95_ms': percentile(values, .95),
            'recorded_start_dates': dict(sorted(Counter(time[:10] for time in times).items())),
            'latency_corrected_cases': [
                record['case_id'] for record in records_for_provider
                if 'latency_correction' in record.get('provider_metadata', {})
            ],
        })
    return {
        'providers': providers,
        'planned_case_count': len(cases),
        'common_pass_count': len(common),
        'common_pass_cases': common,
        'track_counts': dict(sorted(Counter(
            cases[cid].get('track', 'web') for cid in common).items())),
        'execution_scope': execution_scope,
        'rows': rows,
    }


def _render_article_resources(summary, selected):
    if summary is None:
        return (('<p>' + t('the_current_evidence_has_no_independent_client_3', 'The current evidence has no independent client-side resource measurement; this article does not infer CPU or memory from latency.') + '</p>'),
                t('resource_metrics_will_be_added_only_after', 'Resource metrics will be added only after an independent matched-task measurement.'))
    selected = summary.get('providers', selected)
    rendered, result = render_resource_comparison(summary, selected)
    interpretation = (
        t('is_the_resource_efficiency_leader_in_this', '{winner_label} is the resource-efficiency leader in this run, with the lowest use in {winner_count} of {ranked_metric_count} lower-is-better metrics. Resource leadership is not the same as leadership in overall task pass rate.', winner_label=result['winner_label'], ranked_metric_count=result['ranked_metric_count'], winner_count=result['winner_count'])
    )
    return rendered, interpretation


def load_browser_failure_review(out, cases, records, content):
    """Validate a complete, current per-failure diagnosis independently of scoring."""
    binding = content.get('failure_review')
    if not binding:
        if content.get('require_failure_review'):
            raise ValueError('Browser interpretation requires a failure review')
        return {}
    path = contained_path(out, binding['path'], reject_symlinks=True)
    if path.is_symlink():
        raise ValueError('Failure review must not be a symlink')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding['sha256']:
        raise ValueError('Failure review hash mismatch')
    review = json.loads(raw)
    if review.get('schema') != 'webfetch_failure_review/1':
        raise ValueError('Unsupported failure review schema')
    result_path = contained_path(out, 'results.jsonl', reject_symlinks=True)
    if review.get('results_sha256') != hashlib.sha256(result_path.read_bytes()).hexdigest():
        raise ValueError('Failure review results binding is stale')
    expected = {row['attempt_id']: row for row in records if not task_passed(
        row['ok'], row['metrics'], cases[row['case_id']].get('task_kind', 'body'))}
    failures = review.get('failures')
    if not isinstance(failures, list):
        raise ValueError('Failure review requires a failure list')
    indexed, labels, evidence_digests = {}, {}, {}
    for item in failures:
        aid = item.get('attempt_id')
        if aid in indexed:
            raise ValueError('Duplicate failure review attempt')
        row = expected.get(aid)
        if row is None or any(item.get(key) != row[key] for key in ('provider', 'case_id')):
            raise ValueError('Failure review contains a pass or mismatched identity')
        category = item.get('category', '')
        if not isinstance(category, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', category):
            raise ValueError('Failure review requires a stable root cause category')
        for field in ('category_label', 'reason', 'remediation_status'):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError('Failure review is missing ' + field)
        if category in labels and labels[category] != item['category_label']:
            raise ValueError('Failure review category labels disagree')
        labels[category] = item['category_label']
        evidence = item.get('evidence_paths')
        if not isinstance(evidence, list) or not evidence:
            raise ValueError('Failure review requires direct evidence')
        for binding in evidence:
            if not isinstance(binding, dict) or not binding.get('path') or not binding.get('sha256'):
                raise ValueError('Failure review evidence requires path and sha256')
            evidence_path = contained_path(out, binding['path'], reject_symlinks=True)
            if binding['path'] not in evidence_digests:
                evidence_digests[binding['path']] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            if evidence_digests[binding['path']] != binding['sha256']:
                raise ValueError('Failure review evidence hash mismatch')
        indexed[aid] = item
    if set(indexed) != set(expected):
        raise ValueError('Failure review must cover every failed result exactly once')
    return indexed


def browser_example_evidence(out, case, row, *, focus=None, reference_focus=None):
    """Project saved scoring evidence for a compact example; never rescore outputs."""
    m = row['metrics']
    def text(value):
        if isinstance(value, str): return value
        if isinstance(value, bool): return t('yes', 'Yes') if value else t('no_2', 'No')
        if isinstance(value, list): return ' / '.join(text(item) for item in value) or t('not_matched', 'Not matched')
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    reference = case.get('ground_truth_main_content') or case.get('reference_answer')
    requirements = {key:case[key] for key in (
        'required_facts','min_content_chars','body_requirements','acceptance_contract',
        'composite_acceptance','expected_resources','resource_output_contract','expected_files') if case.get(key) is not None}
    expected = task_instruction(case)+'\n\n'+(str(reference) if reference else text(requirements))
    delivered_chars = None
    if case.get('task_kind') == 'file':
        files = (row.get('provider_metadata') or {}).get('file_evaluation', {}).get('files', [])
        actual = text([{
            key:item[key] for key in ('mime','bytes','sha256','pass','error') if key in item
        } for item in files]) if files else '本次未交付可校验的原始文件字节。'
    else:
        raw = contained_path(out, row['artifact_path'], reject_symlinks=True).read_bytes()
        delivered_chars = len(raw.decode('utf-8', errors='replace'))
        # Body/document/resource artifacts are scored text, never a PDF/media payload.
        if raw.startswith((b'%PDF-', b'PK\x03\x04', b'\x89PNG', b'\xff\xd8\xff')) or b'\x00' in raw[:1024]:
            actual = '本次返回二进制响应，没有形成可供该题验收的正文。'
        else:
            actual = raw.decode('utf-8', errors='replace') or '本次没有返回内容。'
    checks = []
    def add(key, title, expected, actual, verdict):
        checks.append({'key':key,'label':title,'expected':text(expected),'actual':text(actual),
                       'verdict':t('pass_2', 'Pass') if verdict is True else t('fail_2', 'Fail') if verdict is False else t('not_recorded', 'Not recorded')})
    add('request','请求完成','成功返回','成功返回' if row['ok'] else '未成功返回',bool(row['ok']))
    if 'length_pass' in m:
        add('length',t('body_length', 'Body length'),(t('at_least_characters_2', 'At least {min_content_chars} characters', min_content_chars=str(case['min_content_chars']))) if 'min_content_chars' in case else '题目登记的最小长度',t('characters', '{get} characters', get=str(m.get('content_chars', t('not_recorded', 'Not recorded')))),m['length_pass'])
    for field,title in [('fact_results','事实组'),('stable_anchor_results',t('stable_text_2', 'Stable text')),
                        ('pattern_results','字段模式'),('stable_page_pattern_results',t('page_patterns', 'Page patterns')),
                        ('body_region_results','正文分段'),('stable_field_results',t('record_fields', 'Record fields')),
                        ('resource_results','目标资源'),('composite_group_results',t('combined_rule', 'Combined rule'))]:
        for index,item in enumerate(m.get(field, []),1):
            target = item.get('alternatives', item.get('minimum', item.get('pattern', item.get('expected'))))
            observed = item.get('matched', item.get('count', item.get('actual')))
            if field == 'body_region_results':
                rule=case.get('body_requirements') or {}
                target=('本区至少 '+format(rule['minimum_floor'],'.0%')+'；' if 'minimum_floor' in rule else '')
                target+=('至少 '+str(m.get('body_required_target_regions',1))+' 区达到 '+format(rule['minimum_coverage'],'.0%')) if 'minimum_coverage' in rule else '满足本区登记的覆盖要求'
                observed=format(item['coverage'],'.1%') if item.get('coverage') is not None else '未记录覆盖率'
                if item.get('reference_fragments') is not None:
                    observed+='（命中 '+str(item.get('matched_fragments',0))+'/'+str(item['reference_fragments'])+' 个参考片段）'
            elif field == 'resource_results':
                target = next((value for value in case.get('expected_resources', []) if value.get('id') == item.get('id')), item.get('id'))
                observed = {key:value for key,value in item.items() if key not in {'pass','id'}}
            elif field == 'composite_group_results':
                target = next((value for value in (case.get('composite_acceptance') or {}).get('groups', []) if value.get('id') == item.get('id')), item.get('id'))
                observed = {key:value for key,value in item.items() if key not in {'pass','id'}}
            add(field+'_'+str(index),title+' '+str(item.get('id',item.get('field',index))),
                target if target is not None else '满足本题登记要求',
                observed if observed is not None else {key:value for key,value in item.items() if key not in {'pass','id'}},
                item.get('target_pass',item.get('pass')))
    if 'stable_unique_record_count' in m:
        add('records','完整且唯一的记录数',m.get('stable_minimum_records'),m['stable_unique_record_count'],m.get('stable_structure_pass'))
    for field,title in [('body_coverage_pass','正文覆盖'),('stable_content_pass','稳定内容'),
                        ('list_task_pass','列表结构'),('raw_file_pass','原始文件格式、长度与哈希'),
                        ('resource_pass','媒体地址交付')]:
        if field in m:
            add(field,title,'满足本题登记要求',m[field],m[field])
    accepted = task_passed(row['ok'],m,case.get('task_kind','body'))
    add('task','最终任务判定','满足完整验收规则',accepted,accepted)
    # Prefer frozen rule evidence and corresponding body paragraphs to navigation.
    facts=case.get('required_facts') or []
    if facts:
        expected=task_instruction(case)+'\n\n必需内容：\n'+'\n'.join(text(fact.get('any',fact.get('alternatives',fact))) for fact in facts)+'\n\n'+str(reference or '')
    if case.get('acceptance_contract'):
        contract=case['acceptance_contract']
        labels={'body_text':'稳定正文','named_link':'带名称的目标链接记录','table':'表格','quote':'名言记录','taxonomy_family':'龟科目录'}
        requirements=[task_instruction(case),'验收内容：'+labels.get(contract.get('schema'),str(contract.get('schema')))]
        if contract.get('minimum_records'):
            requirements.append('完整且唯一的记录至少 '+str(contract['minimum_records'])+' 条。')
        if contract.get('entry_path'):
            requirements.append('目标记录链接格式：'+contract['entry_path'])
        anchors=contract.get('stable_text_anchors') or []
        if anchors:
            requirements.append('必需的可见文字（节选）：'+ '；'.join(' / '.join(item.get('alternatives') or []) for item in anchors[:8]))
        if contract.get('required_fields'):
            requirements.append('每条记录必需字段：'+text(contract['required_fields']))
        if reference:
            requirements.append('冻结要求节选：\n'+str(reference)[:1500])
        expected='\n\n'.join(requirements)
    full_actual_chars=len(actual) if delivered_chars is None else delivered_chars
    if focus:
        if not isinstance(focus,str) or focus not in actual:
            raise ValueError('Browser example focus is absent from measured output')
        start=actual.index(focus)
        actual=actual[start:start+3600]
    else:
        urls=[url for item in m.get('resource_results',[]) for url in item.get('matched_urls',[])]
        located=next((actual.index(url) for url in urls if url in actual),None)
        if located is not None:
            start=actual.rfind('\n',0,located)+1
            actual=actual[max(start,located-500):located+2500]
        elif len(actual)>3600:
            paragraphs=actual.split('\n\n')
            substantive=[part for part in paragraphs if len(part)>100 and sum(len(link) for link in re.findall(r'\[[^\]]*\]\([^)]*\)',part))<len(part)*0.7]
            if substantive:
                actual='\n\n[…节选…]\n\n'.join(substantive[:5])[:3600]
    actual=re.sub(r'data:image/[^;,)\s]+;base64,[A-Za-z0-9+/=]+','[内嵌图片字节省略，完整输出保留]',actual)
    reference_text=str(reference or '')
    if reference_focus:
        if not isinstance(reference_focus,str) or reference_focus not in reference_text:
            raise ValueError('Browser example reference focus is absent from the frozen reference')
        reference_text=reference_text[reference_text.index(reference_focus):]
    return {'reference':reference_text[:650], 'reference_chars':len(str(reference or '')),
            'expected':expected[:3600], 'actual':actual[:3600], 'checks':checks,
            'expected_chars':len(expected), 'actual_chars':full_actual_chars,
            'run_id':str(row.get('run_id') or row.get('attempt_id')), 'passed':accepted}


def current_browser_article_model(out, cases, records, binding):
    """Bind new cohort prose to measured inputs; derive every comparison value."""
    path = contained_path(out, binding['path'])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding['sha256']:
        raise ValueError('Browser interpretation content hash mismatch')
    content = json.loads(raw)
    providers = sorted({row['provider'] for row in records})
    if content.get('schema') != 'webfetch_browser_interpretation/1':
        raise ValueError('Unsupported browser interpretation content schema')
    if content.get('evidence_sha256') != canonical_json_sha256(_headless_article_input(cases, records, providers)):
        raise ValueError('Browser interpretation evidence changed')
    if set(content.get('providers', {})) != set(providers):
        raise ValueError('Browser interpretation must explain every participant')
    if not cases or not providers or any(report_family(c) not in BROWSER_FAMILY_NAMES for c in cases.values()):
        raise ValueError('Browser interpretation requires supported nonempty task families')
    expected = {(name, cid) for name in providers for cid in cases}
    indexed = {(row['provider'], row['case_id']): row for row in records}
    if len(indexed) != len(records) or set(indexed) != expected:
        raise ValueError('Browser interpretation requires the complete declared matrix')
    def passed(row):
        return task_passed(row['ok'], row['metrics'], cases[row['case_id']].get('task_kind', 'body'))
    def distribution(rows):
        times = [row['elapsed_ms'] for row in rows]
        return {'n': len(rows), 'mean': statistics.mean(times) if times else None,
                'p50': statistics.median(times) if times else None,
                'p95': percentile(times, .95) if times else None}
    tracks = [track for track in BROWSER_FAMILY_NAMES if any(report_family(c) == track for c in cases.values())]
    failure_review = load_browser_failure_review(out, cases, records, content)
    quality_cases=[cid for cid,case in cases.items() if report_family(case)=='web' and comparable_body_reference(case)]
    figures = content.get('figures', [])
    if not isinstance(figures, list):
        raise ValueError('Browser figures must be a list')
    for figure in figures:
        image = contained_path(out, figure['path'], reject_symlinks=True)
        if image.is_symlink() or hashlib.sha256(image.read_bytes()).hexdigest() != figure['sha256']:
            raise ValueError('Browser figure hash mismatch')
        if image.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'} or not all(
                isinstance(figure.get(key), str) and figure[key].strip() for key in ('alt', 'caption')):
            raise ValueError('Browser figure needs a raster asset, alt and caption')
    examples = []
    for item in content.get('case_examples', []):
        row = indexed.get((item.get('provider'), item.get('case_id')))
        if row is None or not isinstance(item.get('explanation'), str) or not item['explanation'].strip():
            raise ValueError('Browser example requires a measured result and explanation')
        examples.append({'row': row, 'explanation': item['explanation'],
                         'evidence': browser_example_evidence(out,cases[row['case_id']],row,focus=item.get('focus'),reference_focus=item.get('reference_focus'))})
    stats = {}

    provenance = method_provenance(out)
    for name in providers:
        rows = [indexed[name, cid] for cid in cases]
        successes = [row for row in rows if passed(row)]
        failures = [row for row in rows if not passed(row)]
        chapter = content['providers'][name]
        specimen = indexed.get((name, chapter.get('specimen_case_id')))
        if specimen is None or (failures and passed(specimen)):
            raise ValueError('Browser chapter requires an evidenced failure specimen when failures exist')
        explanation = chapter.get('explanation')
        if not isinstance(explanation, str) or not explanation.strip():
            raise ValueError('Browser chapter requires a measured explanation')
        versions = method_versions(rows,provenance)
        groups = {track: [row for row in rows if report_family(cases[row['case_id']]) == track] for track in tracks}
        final_times=[]
        physical_calls=0
        for row in rows:
            attempts=((row.get('provider_metadata') or {}).get('retry') or {}).get('attempts') or []
            physical_calls+=len(attempts) or 1
            final_times.append(attempts[-1]['elapsed_ms'] if attempts else row['elapsed_ms'])
        quality_rows=[indexed[name,cid] for cid in quality_cases if indexed[name,cid]['metrics'].get('word_f1') is not None]
        stats[name] = {'quality':{'n':len(quality_rows),'reference_cases':len(quality_cases),**{key:_mean(row['metrics'].get(key) for row in quality_rows) for key in ('word_recall','word_precision','word_f1')}}, 'version': ' / '.join(versions) if versions else '托管服务，内部版本未公开' if name == 'kitesurf' else '未提供版本',
                       'passed': len(successes), 'pass_rate': len(successes)/len(cases),
                       'tracks': {track: {'passed': sum(passed(row) for row in subset), 'n': len(subset)} for track,subset in groups.items()},
                       'all': distribution(rows), 'successful': distribution(successes),
                       'physical_calls':physical_calls, 'final_call_mean_ms':statistics.mean(final_times),
                       'prior_call_mean_ms':statistics.mean(row['elapsed_ms'] for row in rows)-statistics.mean(final_times),
                       'failure_counts': dict(Counter(row.get('error_type') or 'acceptance' for row in failures)),
                       'reviewed_failure_counts': dict(Counter(failure_review[row['attempt_id']]['category'] for row in failures)) if failure_review else {},
                       'failure_reviews': [failure_review[row['attempt_id']] for row in failures] if failure_review else [],
                       'specimen': specimen}
    for field in ('title', 'intro', 'scoring', 'conclusions'):
        if not isinstance(content.get(field), str) or not content[field].strip():
            raise ValueError('Browser interpretation is missing prose: ' + field)
    tracks = [track for track in BROWSER_FAMILY_NAMES if any(report_family(c) == track for c in cases.values())]
    return {'content': content, 'providers': providers, 'stats': stats, 'tracks': tracks,
            'cases': len(cases), 'results': len(records), 'failure_review': failure_review,
            'figures': figures, 'examples': examples}


def _offline_browser_article(body, out, cases, model):
    """Embed specimen evidence; an independent download needs no report host."""
    document = BeautifulSoup(body, 'html.parser')
    for script in document.select('script'):
        script.decompose()
    targets = {}
    appendices = []
    def evidence(path, anchor, title, payload):
        if path in targets:
            return
        targets[path] = '#'+anchor
        appendices.append('<details class="wrap" id="'+escape(anchor)+'"><summary>'
                          +escape(title)+'</summary>'+payload+'</details>')
    for name in model['providers']:
        row = model['stats'][name]['specimen']
        case = cases[row['case_id']]
        evidence('../datasets/'+case['id']+'.html', 'offline-task-'+case['id'],
                 '任务与固定验收规则 · '+case['id'],
                 '<pre>'+escape(json.dumps(case,ensure_ascii=False,indent=2))+'</pre>')
        raw = contained_path(out,row['artifact_path']).read_text(encoding='utf-8',errors='replace')
        payload = '<h3>完整实际返回</h3><pre data-standalone-output="'+escape(name)+'">'+escape(raw)+'</pre>'
        payload += '<h3>结果与评分</h3><pre>'+escape(json.dumps({key:row[key] for key in ('attempt_id','provider','case_id','run_id','started_at','elapsed_ms','ok','status_code','error_type','metrics') if key in row},ensure_ascii=False,indent=2))+'</pre>'
        for binding in record_public_call_log_bindings(row):
            snapshot = json.loads((out/'dataset.json').read_text(encoding='utf-8')).get('comparison',{}).get('score_audit') if (out/'dataset.json').is_file() else None
            if snapshot:
                from harness.web_fetch.published_snapshot import read_snapshot_public_file
                log = read_snapshot_public_file(out,binding).read_text(encoding='utf-8')
            else:
                log = verify_public_call_log(out,binding).read_text(encoding='utf-8')
            payload += '<h3>调用日志</h3><pre>'+escape(log)+'</pre>'
        evidence('../attempts/'+row['attempt_id']+'.html', 'offline-result-'+row['attempt_id'],
                 '完整结果与调用证据 · '+name, payload)
    for example in model.get('examples', []):
        row = example['row']; case = cases[row['case_id']]
        evidence('../datasets/'+case['id']+'.html', 'offline-task-'+case['id'],
                 '任务与固定验收规则 · '+case['id'],
                 '<pre>'+escape(json.dumps(case,ensure_ascii=False,indent=2))+'</pre>')
        evidence('../attempts/'+row['attempt_id']+'.html', 'offline-result-'+row['attempt_id'],
                 '实例返回与判定 · '+row['case_id'],
                 '<p>'+escape(example['explanation'])+'</p><pre>'+escape(json.dumps(row,ensure_ascii=False,indent=2))+'</pre>')
    referenced={anchor.get('href') for anchor in document.select('a[href]')}
    for item in model.get('failure_review', {}).values():
        target='../attempts/'+item['attempt_id']+'.html'
        if target in referenced and target not in targets:
            evidence(target,'offline-failure-'+item['attempt_id'],'失败原因 · '+item['case_id'],
                     '<p>'+escape(item['category_label'])+'：'+escape(item['reason'])+'</p><p>'+escape(item['remediation_status'])+'</p>')
    for route,title in (('../analysis/index.html','全部逐题结果'),('../methodology/index.html',t('methodology', 'Methodology')),('../adoption/index.html',t('methods', 'Methods'))):
        evidence(route,'offline-'+route.split('/')[1],title,'<p>完整调用及逐题结果见报告的对应栏目；本文保留各类原因的代表证据。</p>')
    for image in document.select('img[data-browser-figure]'):
        relative = image['data-browser-figure']
        suffix = Path(relative).suffix.lower()
        mime = 'image/jpeg' if suffix in {'.jpg','.jpeg'} else 'image/'+suffix[1:]
        image['src'] = 'data:'+mime+';base64,'+base64.b64encode(contained_path(out,relative).read_bytes()).decode('ascii')
    if (out/'result-review.json').is_file():
        review=json.loads((out/'result-review.json').read_text(encoding='utf-8'))
        summary={key:review[key] for key in ('reviewed_attempts','total_attempts','expected_attempts','accepted_unreviewed_passes','disagreements','unresolved') if key in review}
        evidence('../review/index.html','offline-review','独立审查范围',
                 '<pre>'+escape(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre>')
    for anchor in document.select('a[href]'):
        href = anchor['href']
        if href in targets:
            anchor['href'] = targets[href]
        elif not href.startswith(('https://','http://','#')):
            raise ValueError('Independent article has an unbound evidence target: '+href)
    return str(document)+''.join(appendices)


def _browser_network_rows(records, providers):
    """Summarize recorded DNS routes without filling missing metadata."""
    rows=[]
    for provider in providers:
        label=browser_method_label(provider)
        if label==provider:label=provider.title()
        if engine_definition(native_browser_engine(provider)).hosted:
            rows.append({'provider':provider,'mode':'service_network',
                         'text':label+'在 Cloudflare Browser Run 托管网络中执行；本地 DNS 配置不适用于该服务。耗时包含云端部署位置和公网路径。'})
            continue
        selected=[row for row in records if row['provider']==provider]
        metadata=[row.get('provider_metadata') or {} for row in selected]
        modes=[str(meta['dns_mode']) for meta in metadata if meta.get('dns_mode')]
        missing=len(metadata)-len(modes)
        distinct=sorted(set(modes))
        if not distinct:
            mode='unknown'
            text=label+'的调用记录未保存可核对的 DNS 模式；报告不把它推定为 public DoH。'
        else:
            mode=('partial:' if missing else '')+(','.join(distinct) if len(distinct)>1 else distinct[0])
            counts=Counter(modes)
            details='、'.join(name+' '+str(counts[name])+' 次' for name in distinct)
            text=label+'的调用记录中，DNS 模式为 '+details
            if missing:
                text+='；另有 '+str(missing)+' 次没有可核对的 DNS 元数据'
            text+='。'
            if distinct==['public_doh']:
                no_fallback=sum(meta.get('system_dns_fallback') is False for meta in metadata)
                strict=sum(meta.get('secure_dns_mode') in {'secure','strict'} for meta in metadata)
                if no_fallback:text+=' 其中 '+str(no_fallback)+' 次明确记录不回退系统 DNS。'
                if strict:text+=' 其中 '+str(strict)+' 次由浏览器严格安全 DNS 配置执行。'
                text+=' 统一解析策略不保证不同时间、地点或浏览器一定取得同一 IP。'
        rows.append({'provider':provider,'mode':mode,'text':text})
    return rows


def _browser_error_excerpt(row, has_failures, *, reviewed_error=None):
    error = str(reviewed_error or row.get('error_message') or (
        '未满足完整验收规则；逐项期望与实际值见结果详情。' if has_failures else '满足该题验收规则。'))
    if len(error) > 4000:
        matches=list(re.finditer(r'(?im)^.*(?:panicked at|FATAL ERROR|fatal runtime error|WeakMap\.get|FINAL ERROR:|\bError:).*$',error))
        fragments=[]
        for match in (matches[:2]+matches[-2:]):
            fragment=error[max(0,match.start()-80):min(len(error),match.end()+300)]
            if fragment not in fragments:fragments.append(fragment)
        excerpt=(f'''{"""
[…节选…]
""".join((fragment[:450] for fragment in fragments))}\n[…结尾…]\n{error[-1800:]}''')[:4000] if fragments else error[-4000:]
        return '错误过长，仅节选关键错误及结尾；完整错误和日志见结果详情。\n'+excerpt
    return error


def _browser_wait_summary(records, name):
    labels={'done':'原生默认完成（done）','domstable':'DOM 稳定（domstable）',
            'load':'文档加载完成（load）','networkidle2':'网络静默（networkidle2）',
            'load + adaptive settle':'文档加载后自适应静默等待',
            'automatic raw response':'自动原始响应',
            'navigation response bytes':'导航响应字节',
            'browser response bytes or completed native download':'响应字节或原生下载完成'}
    waits=Counter(str((row.get('provider_metadata') or {}).get('wait_until') or t('not_recorded', 'Not recorded'))
                  for row in records if row['provider']==name)
    return '；'.join(labels.get(wait,wait)+'：'+str(count)+' 题' for wait,count in waits.items())


def _browser_reviewed_error(out, model, row):
    item=model['failure_review'].get(row['attempt_id'],{})
    relative=item.get('error_excerpt_path')
    if not relative:return None
    if relative not in {binding['path'] for binding in item['evidence_paths']}:
        raise ValueError('Program error excerpt has no verified evidence binding')
    return contained_path(out,relative,reject_symlinks=True).read_text(encoding='utf-8')


@with_measured_method_labels
def _render_current_browser_article(out, cases, records, binding):
    model = current_browser_article_model(out, cases, records, binding)
    content, names, stats = model['content'], model['providers'], model['stats']
    engine_count = len({native_browser_engine(name) or name for name in names})
    def label(name):
        return bilingual_text(browser_method_label(name) if browser_method_label(name) != name else name.title(),
                              browser_method_label(name,language='en') if browser_method_label(name,language='en') != name else name.title())
    def paragraph(value):
        return '<p>' + escape(value).replace('\n\n', '</p><p>') + '</p>'
    def section(title, body, anchor, english_title=None):
        heading = escape(title) if english_title is None else bilingual_text(title,english_title)
        return '<section class="section" id="'+anchor+'"><h2>'+heading+'</h2>'+body+'</section>'
    def metric(name, key, value, values, higher=False, suffix=''):
        valid = [v for v in values if v is not None]
        extreme = (max(valid) if higher else min(valid)) if valid else None
        winner = value is not None and value == extreme and valid.count(extreme) == 1
        text = '—' if value is None else (f'{value:.1%}' if key == 'pass_rate' or key.startswith(('family_','quality_')) else f'{value:,.0f}{suffix}')
        return ('<span data-browser-metric="'+escape(key)+'" data-provider="'+escape(name)
                +'" data-value="'+('' if value is None else str(value))+'"'
                + (' data-winner="true" class="good"' if winner else '')+'>'+text+'</span>')
    def latency_table(population):
        return _table([t('browser', 'Browser'), '样本数', '平均总耗时 · ms', 'P50 · ms', 'P95 · ms'], [
            (label(name), str(stats[name][population]['n']), *(
                metric(name, population+'_'+key, stats[name][population][key],
                       [stats[n][population][key] for n in names]) for key in ('mean','p50','p95')))
            for name in names], '均值、P50、P95 使用同一行样本；每题只计一次，耗时含该题重试。')
    body = '<article data-browser-interpretation="1">'
    dates = sorted({row['started_at'][:10] for row in records})
    figures = ''.join('<figure class="editorial-figure"><img data-browser-figure="'+escape(item['path'],quote=True)
                      +'" src="../'+escape(item['path'],quote=True)+'" alt="'+escape(item['alt'],quote=True)
                      +'" style="display:block;width:100%;height:auto"><figcaption>'+escape(item['caption'])+'</figcaption></figure>'
                      for item in model['figures'])
    body += section('这次测什么', paragraph(content.get('background', content['intro'])), 'scope')
    def family_cell(name, track):
        group = stats[name]['tracks'][track]
        rate = group['passed']/group['n']
        values = [stats[n]['tracks'][track]['passed']/stats[n]['tracks'][track]['n'] for n in names]
        return f"{group['passed']}/{group['n']}<br>" + metric(name,'family_'+track,rate,values,True)
    interfaces = browser_task_interface_matrix(cases, names, records, compact=True)
    english_interfaces = browser_task_interface_matrix(cases, names, records, compact=True, language='en')
    english_routes = {(name,row['track']):row['cells'][name] for row in english_interfaces['rows'] for name in names}
    route_rows = []
    for row in interfaces['rows']:
        cells = []
        for name in names:
            parts = row['cells'][name]
            english_parts = english_routes[name,row['track']]
            if [(p['mode'],p['count']) for p in parts] != [(p['mode'],p['count']) for p in english_parts]:
                raise ValueError('Interface route populations differ between languages')
            cells.append('<div data-browser-interface="'+escape(name)+'" data-track="'+escape(row['track'])+'">'
                         + '<br><br>'.join('<span data-route-mode="'+escape(part['mode'])+'" data-case-count="'+str(part['count'])+'" data-report-en="'+escape(str(part['count'])+' tasks: '+english_part['description'],quote=True)+'">'
                           +str(part['count'])+' 题：'+escape(part['description'])+'</span>' for part,english_part in zip(parts,english_parts,strict=True))+'</div>')
        route_rows.append((escape(BROWSER_FAMILY_NAMES[row['track']])+f" · {row['count']} 题", *cells))
    matrix = _table([t('task_family', 'Task family'),*((browser_method_label(name),browser_method_label(name,language='en')) for name in names)], route_rows, '每格为实际请求的接口路线与目标交付物；调用失败或能力不足仍保留原路线，不代表已经成功交付。')
    network_rows = _browser_network_rows(records, names)
    network_note = '<details><summary>网络执行位置与解析方式</summary>'+''.join(
        '<p data-network-method="'+escape(row['provider'])+'" data-network-mode="'
        +escape(row['mode'])+'">'+escape(row['text'])+'</p>' for row in network_rows)+'</details>'
    wait_rows=[]
    for name in names:
        wait_rows.append((label(name),'<span data-browser-wait="'+escape(name)+'">'+escape(_browser_wait_summary(records,name))+'</span>'))
    body += section('七类任务分别用了什么接口',
        (f'<p>下表列出实际获取方式与交付物。本轮网页和文档正文统一选择 Markdown 输出。Chromium 额外使用固定的 HTML 转换器；其余方法保留官方原生输出，不在评测端补正文。网页内嵌媒体主要验收指定地址，直接媒体文件验收原始字节。</p><div class="browser-interface-matrix">{matrix}</div><p>Moli 使用 fetch --dump markdown；动态正文整类启用 --layout 与 --with-frames。Lightpanda 使用 --dump markdown，JSON 外壳只用于读取同一次返回的 Markdown；Obscura 执行固定版本的官方 Markdown 脚本；Kitesurf 使用 Cloudflare Browser Run 的 /markdown 接口。完整命令、转换器版本和各题原始调用见<a href="../methodology/index.html">' + t('methodology', 'Methodology') + '</a>及<a href="../adoption/index.html">' + t('methods', 'Methods') + f"</a>。接口缺失的记录在表中单列，不推定它使用了什么路线。</p>{_table([t('method', 'Method'), '原生等待条件及题数'], wait_rows, '等待条件来自保存的调用记录；文件字节路线不等待查看器绘制。')}<p>{bilingual_text(*pair('call_budgets_and_retry_limits_are_listed', 'Call budgets and retry limits are listed in the execution configuration on the methodology page. Timing includes startup, access, waiting, conversion and any recorded retries; scoring runs after timing ends. The final URL is diagnostic and does not affect body-text scoring. The harness adds no PDF or DOCX parser.'))}</p>{network_note}"),'interfaces')
    flow = 'flowchart LR\n A[同一目标网址与验收规则] --> B[各浏览器原生访问与提取]\n B --> C[保留完整输出与调用日志]\n C --> D[统一评分]\n D --> E[整体与专项结果]'
    fallback = ' → '.join('<span class="tag">'+x+'</span>' for x in ('目标网址与规则','浏览器原生访问','完整输出与日志','统一评分','整体与专项'))
    mechanism = '<div class="wmb-flow"><div data-wmb-flow-fallback>'+fallback+'</div><pre class="mermaid" hidden>'+escape(flow)+'</pre></div>'
    body += section('怎样判断一次获取是否可用', mechanism+paragraph(content['scoring'])+figures, 'scoring')
    if model['examples']:
        examples = ''
        for example_index,item in enumerate(model['examples']):
            row = item['row']; case = cases[row['case_id']]
            examples += '<details'+(' open' if example_index==0 else '')+' data-browser-example="'+escape(row['attempt_id'])+'"><summary>'+escape(BROWSER_FAMILY_NAMES[report_family(case)])+' · '+escape(case.get('name',case['id']))+'</summary>'
            evidence = item['evidence']
            examples += (f"{paragraph(item['explanation'])}<p>" + t('method_3', 'Method: ') + f'''{label(row['provider'])} · 运行 ID：<code>{escape(evidence['run_id'])}</code> · 任务判定：<strong data-example-verdict="{escape(row['attempt_id'])}">{(t('pass_2', 'Pass') if evidence['passed'] else t('fail_2', 'Fail'))}</strong></p>''')
            examples += '<div class="evidence-compare" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:1rem"><article class="evidence-pane"><h4>题目要求与参考节选</h4><p>共 '+str(evidence['expected_chars'])+' 字符，节选 '+str(len(evidence['expected']))+' 字符。</p><pre style="max-height:28rem;overflow:auto;white-space:pre-wrap" data-example-expected="'+escape(row['attempt_id'])+'">'+escape(evidence['expected'])+'</pre></article><article class="evidence-pane"><h4>原始输出节选</h4><p>文件任务展示交付元数据；正文节选 '+str(len(evidence['actual']))+' 字符。</p><pre style="max-height:28rem;overflow:auto;white-space:pre-wrap" data-example-actual="'+escape(row['attempt_id'])+'">'+escape(evidence['actual'])+'</pre></article></div>'
            examples += _table(['检查项目','题目要求',t('observed_result', 'Observed result'),t('verdict', 'Verdict')],[(escape(check['label']),escape(check['expected']),escape(check['actual']),'<span class="'+('good' if check['verdict']==t('pass_2', 'Pass') else 'bad' if check['verdict']==t('fail_2', 'Fail') else 'neutral')+'" data-example-check="'+escape(row['attempt_id'])+'" data-check-key="'+escape(check['key'])+'">'+escape(check['verdict'])+'</span>') for check in evidence['checks']], '检查值来自本次保存的统一评分结果；完整输出和逐项证据见结果详情。')
            examples += '<p><a href="../datasets/'+escape(case['id'],quote=True)+'.html">任务与验收规则</a> · <a href="../attempts/'+escape(row['attempt_id'],quote=True)+'.html">实际返回与判定证据</a></p></details>'
        body += section('同一任务怎样验收', examples, 'examples')
    body += section('全部调用的耗时', f"<p>P50 是中位数，P95 表示 95% 任务的累计耗时不超过该值。每行均为全部任务，包含成功、失败与自动重试的累计耗时。{bilingual_text(*pair('when_retries_occur_total_latency_includes_prior', 'When retries occur, total latency includes prior calls and retry waits; a per-call budget is not the total task duration.'))}评分时间不计入。调用分布在不同日期，不能把耗时差异直接归因于浏览器引擎或新版改进；耗时较短也不等于任务通过率更高。</p>{latency_table('all')}", 'all-latency')
    timing_rows=[(label(name),str(stats[name]['physical_calls']),
                  *(metric(name,key,stats[name][key],[stats[n][key] for n in names]) for key in ('final_call_mean_ms','prior_call_mean_ms'))) for name in names]
    body += section('重试与原生等待怎样影响耗时',
        _table([t('method', 'Method'),'实际调用数','末次调用平均耗时 · ms','此前调用及重试等待 · ms/题'],timing_rows,
               '每行均对应完整任务集；两项平均耗时相加等于上表平均总耗时。没有按成功与否筛选任务。')
        +paragraph(content.get('timing_explanation','原生完成条件决定何时交付。等待更多页面变化可能延长耗时，仍须同时检查交付内容；提前返回错误或空正文也会使平均耗时缩短。')),'timing-boundary')
    body += section('通过任务的耗时', '<p>只计各浏览器自己通过的任务。各行样本可能不同，须结合通过数阅读；这张表不改变任务范围或整体排名。</p>'+latency_table('successful'), 'successful-latency')
    resource = contained_path(out, 'resource-cohort/resource-comparison.json')
    if resource.is_file():
        resource_summary = json.loads(resource.read_text(encoding='utf-8'))
        # The resource cohort is an independently registered measurement.  Its
        # provider IDs can differ from the main result matrix (for example,
        # ``moli`` versus a source-build comparison ID).  Render the cohort's
        # complete declared population instead of silently dropping rows whose
        # IDs do not appear in the quality matrix.
        table, explanation = _render_article_resources(
            resource_summary, resource_summary['providers'])
        body += section('客户端资源消耗', table+paragraph(explanation), 'resources')
    else:
        body += section('客户端资源消耗', '<p>本轮未登记完整可比的 CPU、内存和网络资源测量，不据此作资源效率排名。</p>', 'resources')
    quality_n=max(stats[name]['quality']['reference_cases'] for name in names)
    if quality_n:
        quality_table=_table([t('method', 'Method'),'已计算 / 有参考','参考词语找回比例 · Recall','输出词语纯净度 · Precision',t('combined_text_overlap_f1', 'Combined text overlap · F1')],[
            (label(name),str(stats[name]['quality']['n'])+'/'+str(quality_n),*(metric(name,'quality_'+key,stats[name]['quality'][key],[stats[n]['quality'][key] for n in names],True) for key in ('word_recall','word_precision','word_f1'))) for name in names],
            '仅使用有完整可比参考的静态网页，逐题算术平均，包含已测失败项；未提供参考的网页不记为零或满分。')
        qfocus=content.get('focus_provider') or max(names,key=lambda name:stats[name]['passed'])
        qbaseline=content.get('comparison_provider') or next((name for name in names if name!=qfocus),qfocus)
        interpretation=''
        if all(stats[name]['quality']['word_recall'] is not None for name in (qfocus,qbaseline)):
            interpretation='<p>在这 '+str(quality_n)+' 道题中，'+label(qfocus)+' 平均找回 '+format(stats[qfocus]['quality']['word_recall'],'.1%')+' 的参考词语，'+label(qbaseline)+' 为 '+format(stats[qbaseline]['quality']['word_recall'],'.1%')+'。平均值包含本次未取得正文的样本，并非仅统计通过网页的正文完整程度。</p>'
        body+=section('正文完整程度与任务通过率', '<p>本轮 '+str(sum(report_family(case)=='web' for case in cases.values()))+' 道静态正文题中，'+str(quality_n)+' 道有完整可比参考。Recall 衡量参考词语找回多少，Precision 衡量返回词语有多少与参考重合，F1 综合二者。它们检查文字重合，不判断事实正确性，也不代表其余网页的全文质量。</p>'+interpretation+quality_table+'<p>满足题目要求，仍可能夹带导航等多余文字；找回部分正文，也可能因为缺少必需段落而失败。任务通过率和全文质量分别回答这两个问题。</p>', 'body-quality')
    def reviewed_distribution(items, provider):
        counts = Counter(item['category'] for item in items)
        labels = {item['category']: item['category_label'] for item in items}
        return _table(['核验后的失败原因', '未通过数量'], [
            ('<a href="../attempts/'+escape(next(item['attempt_id'] for item in items if item['category']==category)) +'.html">'+escape(labels[category])+'</a>',
             '<span data-reviewed-failure="'+escape(provider)+'" data-category="'+escape(category)+'" data-count="'+str(count)+'">'+str(count)+'</span>')
            for category,count in sorted(counts.items(),key=lambda item:(-item[1],item[0]))],
            '每个失败结果只归入一个主要原因；程序错误分类单独保留。')
    if model['failure_review']:
        body += section('失败集中在哪些环节', (f"""{reviewed_distribution(list(model['failure_review'].values()), 'all')}<p>每条失败只归入一个主要原因。验证码、登录和风控要求有明确返回证据；子资源报错不等于主导航失败。全部逐题原因、处理结论和原始日志保留在<a href="../analysis/index.html">""" + t('analysis', 'Analysis') + '</a>与结果详情。</p>'), 'failure-summary')
    errors = {'acceptance':'内容或交付验收未通过','timeout':'访问或提取超时',
              'browser_exit':'浏览器进程返回错误','browser_error':'浏览器访问或运行错误',
              'http_status':'网页返回错误状态','empty_response':'返回内容为空','tool_error':'工具执行错误','api_error':'服务接口错误'}
    for name in names:
        stat, chapter = stats[name], content['providers'][name]
        counts = stat['failure_counts']
        distribution = _table(['程序分类','未通过数量'], [
            (escape(errors.get(key, key)), '<span data-browser-failure="'+escape(name)+'" data-category="'+escape(key)+'" data-count="'+str(count)+'">'+str(count)+'</span>')
            for key,count in sorted(counts.items())], f'{name} 的全部未通过结果，每个结果归入一个程序分类。') if counts else '<p>本轮所有任务均通过。</p>'
        if model['failure_review']:
            reviewed = stat['failure_reviews']
            diagnosis = reviewed_distribution(reviewed, name) if reviewed else '<p>本轮所有任务均通过。</p>'
            distribution = diagnosis+'<details><summary>程序返回分类</summary>'+distribution+'</details>'
        row = stat['specimen'];case = cases[row['case_id']]
        raw = contained_path(out,row['artifact_path']).read_text(encoding='utf-8',errors='replace')
        specimen_evidence=browser_example_evidence(out,case,row)
        reference=specimen_evidence['expected']
        raw=specimen_evidence['actual']
        error = _browser_error_excerpt(row, bool(counts),reviewed_error=_browser_reviewed_error(out,model,row))
        example = '<details data-browser-specimen="'+escape(name)+'"><summary>'+escape(case.get('name',case['id']))+' · '+escape(case['id'])+'</summary>'
        example += '<p><a href="'+escape(case['url'],quote=True)+'">原始页面</a> · <a href="../datasets/'+escape(case['id'],quote=True)+'.html">任务与验收规则</a> · <a data-browser-result="'+escape(name)+'" href="../attempts/'+escape(row['attempt_id'],quote=True)+'.html">完整结果、评分与日志</a></p>'
        example += '<h4>参考范围或验收要求（节选）</h4><pre>'+escape(reference)+'</pre><h4>实际返回（节选）</h4><pre>'+escape(raw)+'</pre><h4>程序返回或验收结果</h4><pre style="max-height:24rem;overflow:auto;white-space:pre-wrap" data-browser-error="'+escape(name)+'">'+escape(error)+'</pre></details>'
        body += section((browser_method_label(name) if browser_method_label(name) != name else name.title())+'：'+str(stat['passed'])+'/'+str(model['cases'])+' 题通过',
                        '<div data-browser-chapter="'+escape(name)+'">'+paragraph(chapter['explanation'])+distribution+example+'</div>', 'browser-'+name,
                        browser_method_label(name,language='en')+': '+str(stat['passed'])+'/'+str(model['cases'])+' tasks passed')
    review_path = contained_path(out,'result-review.json')
    review = json.loads(review_path.read_text(encoding='utf-8')) if review_path.is_file() else None
    body += section('结论与审查范围', paragraph(content['conclusions'])+
                    (('<p>独立核验范围与修订见<a href="../review/index.html">' + t('review_record', 'Review record') + '</a>。逐题原因与原始证据可从结果分析打开。</p>') if review else
                     '<p>独立结果审查：0/'+str(model['results'])+'。未经独立复判的程序通过结果：'+str(sum(stats[n]['passed'] for n in names))+'。本轮报告使用统一评分器的原始验收结果。</p>'), 'conclusions')
    # Reuse the reference article's reading components with current measured data.
    article = BeautifulSoup(body, 'html.parser')
    parts = {node['id']:node for node in article.select('article > section[id]')}
    reading = BeautifulSoup('<article class="m-article publication-article browser-interpretation" data-browser-interpretation="1" aria-labelledby="article-title"></article>', 'html.parser')
    owner = reading.article
    def append_html(parent, markup):
        fragment=BeautifulSoup(markup,'html.parser')
        for node in list(fragment.contents):parent.append(node.extract())
    primary=content.get('focus_provider') or max(names,key=lambda name:stats[name]['passed'])
    baseline=content.get('comparison_provider') or next((name for name in names if name!=primary),primary)
    previous=content.get('version_baseline')
    if primary not in names or baseline not in names or (previous and previous not in names):
        raise ValueError('Reading comparison refers to an unregistered method')
    order=sorted(names,key=lambda name:(-stats[name]['passed'],name))
    family_titles={'web':'静态正文','dynamic':'动态正文','challenge':'热门正文','risk_control':'风控正文','document':t('document_text', 'Document text'),'web_media':t('embedded_webpage_media', 'Embedded webpage media'),'media_file':t('direct_media_files', 'Direct media files')}
    combined=_table([t('browser', 'Browser'),t('overall_task_pass_rate', 'Overall task pass rate'),*(family_titles.get(track,BROWSER_FAMILY_NAMES[track]) for track in model['tracks'])], [
        (label(name),metric(name,'pass_rate',stats[name]['pass_rate'],[stats[n]['pass_rate'] for n in names],True)
         +f'<br>{stats[name]["passed"]}/{model["cases"]}',*(family_cell(name,track) for track in model['tracks'])) for name in order],
        '每种配置执行相同的 '+str(model['cases'])+' 题，每题等权。分项为通过数 / 本组题数；绿色只标记该列唯一最高值。')
    diff=stats[primary]['passed']-stats[baseline]['passed']
    cards='<div><span>整体通过数 · 相对 '+label(baseline)+'</span><b>'+f'{diff:+d}'+' 题</b><p>'+label(primary)+f' {stats[primary]["passed"]}/{model["cases"]}；'+label(baseline)+f' {stats[baseline]["passed"]}/{model["cases"]}'+f'<br>相差 {(stats[primary]["pass_rate"]-stats[baseline]["pass_rate"])*100:.1f} 个百分点</p></div>'
    if previous:
        old={r['case_id']:task_passed(r['ok'],r['metrics'],cases[r['case_id']].get('task_kind','body')) for r in records if r['provider']==previous}
        new={r['case_id']:task_passed(r['ok'],r['metrics'],cases[r['case_id']].get('task_kind','body')) for r in records if r['provider']==primary}
        gains=sum(new[cid] and not old[cid] for cid in cases);losses=sum(old[cid] and not new[cid] for cid in cases)
        cards+='<div><span>通过数 · 相对 '+label(previous)+'</span><b>'+f'{gains-losses:+d}'+' 题</b><p>'+str(gains)+' 题由失败变为通过<br>'+str(losses)+' 题由通过变为失败</p></div>'
    else:
        cards+='<div><span>完整评测范围</span><b>'+str(model['results'])+' 个结果</b><p>'+str(len(names))+' 种配置 × '+str(model['cases'])+' 道相同任务</p></div>'
    fastest=min(names,key=lambda name:stats[name]['all']['mean'])
    cards+=('<div><span>全部任务 · 平均总耗时最短</span><b>' + t('seconds_2', '{value:.2f} seconds', value=stats[fastest]['all']['mean'] / 1000) + f'</b><p>{label(fastest)}<br>含成功、失败与重试，非纯内核测速</p></div>')
    hero=reading.new_tag('header',attrs={'class':'m-hero'})
    append_html(hero,('<div class="m-chips"><span>' + t('web_fetch_benchmark', 'Web Fetch benchmark') + f"""</span><span>{str(engine_count)} 种浏览器 · {str(len(names))} 种配置</span><span>{str(model['cases'])} 道任务</span></div><h1 id="article-title">{escape(content['title']).replace('：', '：<br>', 1)}</h1><p class="m-deck">{escape(content['intro'])}</p><div class="m-hero-table" id="overall">{combined}</div><div class="m-ledger" data-reading-ledger="1">{cards}</div><p class="m-scope-note">正文与文档采用本轮登记的原生 Markdown 路线；Chromium 使用固定的 HTML 转换器。原始字节与媒体地址分别验收。{escape(content.get('version_note', ''))}</p>"""))
    owner.append(hero)
    # Figures are explanatory assets, never substitutes for measured charts.
    figures_by_section={key:[] for key in ('scope','method','conclusion')}
    for figure in article.select('figure.editorial-figure'):
        identity=figure.find('img')['data-browser-figure'];definition=next(item for item in model['figures'] if item['path']==identity)
        figures_by_section.get(definition.get('section','method'),figures_by_section['method']).append(figure.extract())
    for figure in figures_by_section['scope']:parts['scope'].insert(1,figure)
    for figure in figures_by_section['method']:parts['scoring'].append(figure)
    if content.get('results_explanation'):
        decomposition=reading.new_tag('section',id='result-decomposition')
        append_html(decomposition,paragraph(content['results_explanation']))
        panels=[]
        for comparison in content.get('comparisons',[]):
            left,right=comparison['left'],comparison['right'];tracks=comparison['tracks']
            if left not in names or right not in names or not set(tracks)<=set(model['tracks']):
                raise ValueError('Reading task comparison differs from the declared population')
            totals={name:(sum(stats[name]['tracks'][track]['passed'] for track in tracks),sum(stats[name]['tracks'][track]['n'] for track in tracks)) for name in (left,right)}
            l,denominator=totals[left];r,other=totals[right]
            if denominator!=other:raise ValueError('Reading task comparison needs the same tasks')
            panels.append('<div class="m-split-panel" data-reading-comparison="'+escape(comparison['id'])+'"><span>'+escape(comparison['title'])+' · '+str(denominator)+' 题</span><div class="m-duel"><div><b>'+str(l)+'</b><small>'+label(left)+'</small></div><em>:</em><div><b>'+str(r)+'</b><small>'+label(right)+'</small></div></div><p>通过数相差 '+str(abs(l-r))+' 题</p></div>')
        if panels:append_html(decomposition,'<figure class="m-figure"><div class="m-split">'+''.join(panels)+'</div><figcaption>'+escape(content.get('comparison_caption','同一任务类别与验收规则下的实际通过数。'))+'</figcaption></figure>')
        if content.get('results_context'):append_html(decomposition,paragraph(content['results_context']))
        for source in content.get('context_sources',[]):
            if urlsplit(source['url']).scheme not in {'http','https'}:raise ValueError('Reading source must use an HTTP(S) URL')
            append_html(decomposition,'<p class="m-scope-note"><a href="'+escape(source['url'],quote=True)+'">'+escape(source['title'])+'</a></p>')
        parts['result-decomposition']=decomposition
    # One visual per latency statistic, with the full common task population.
    columns=[]
    for key,title,meaning in [('mean','平均总耗时','每题累计耗时的算术平均'),('p50',t('p50_median', 'P50 · median'),'一半任务的耗时不超过此值'),('p95',t('p95_slower_end', 'P95 · slower end'),'95% 任务的耗时不超过此值')]:
        values=[stats[name]['all'][key] for name in names];maximum=max(values);minimum=min(values)
        rows=[]
        for name in order:
            value=stats[name]['all'][key];winner=value==minimum and values.count(minimum)==1
            rows.append('<div class="m-speed-row" data-reading-speed="'+escape(name)+'" data-statistic="'+key+'" data-value="'+str(value)+'"><span>'+label(name)+'</span><b'+(' class="good"' if winner else '')+'>'+f'{value/1000:.2f}'+' s</b><div class="m-speed-track"><i'+(' class="best"' if winner else '')+' style="width:'+f'{value/maximum*100:.4f}'+'%"></i></div></div>')
        columns.append('<div class="m-speed-column"><h4>'+title+'</h4><p>'+meaning+'</p>'+''.join(rows)+'</div>')
    latency=parts['all-latency'];latency_table_markup=''.join(str(node) for node in latency.contents if getattr(node,'name',None) not in {'h2','p'})
    latency.clear();append_html(latency,f"""<h3>耗时的差距：等待策略与长尾</h3><p>以下每行使用全部 {str(model['cases'])} 题，包含成功、失败和重试。P50 是中位数，P95 是 95% 任务耗时不超过的值。调用来自不同日期，这些数值描述本轮交付路径，不能分离出纯浏览器内核的速度。</p><figure class="m-figure"><h3>全部任务的总耗时 · 秒</h3><div class="m-speed-grid">{''.join(columns)}</div><figcaption>从进程启动到交付结果，包含访问、原生等待、转换及该题重试；评分时间不计入。</figcaption></figure>{paragraph(content.get('timing_explanation', '不同方法的原生完成条件不同；等待成本需要和内容交付一同判断。'))}<details class="m-details"><summary>精确耗时数值与样本范围</summary>{latency_table_markup}</details>""")
    retry=parts['timing-boundary'];retry_tables=''.join(str(node) for node in retry.contents if getattr(node,'name',None) not in {'h2','p'})
    retry.clear();retry.name='details';retry['class']='m-details';append_html(retry,f"<summary>重试增加了多少耗时</summary><p>{bilingual_text(*pair('the_retry_limit_follows_the_execution_configuration', 'The retry limit follows the execution configuration; the table shows actual call counts. Mean final-call latency plus mean prior-call and retry-wait time equals mean total latency.'))}</p>{retry_tables}")
    successful=parts['successful-latency'];heading=successful.find('h2');heading.name='summary';successful.name='details';successful['class']='m-details'
    # Reuse the case workbench: frozen reference, exact output and checks stay together.
    example_section=parts.get('examples')
    if example_section:
        panels=[];tabs=[];radios=[]
        for i,node in enumerate(example_section.select('[data-browser-example]'),1):
            item=model['examples'][i-1];row=item['row'];case=cases[row['case_id']]
            title=node.summary.get_text();node.summary.decompose();node.name='section';node.attrs.pop('open',None);node['class']='case-panel';node['id']='article-panel-'+str(i);node['aria-labelledby']='article-label-'+str(i)
            heading=reading.new_tag('h3');heading.string=case.get('name',case['id']);node.insert(0,heading)
            input_line=BeautifulSoup(('<div class="case-input">' + t('input_2', 'Input: ') + f'''<a href="{escape(case['url'], quote=True)}">{escape(case['url'])}</a></div>'''),'html.parser').div;node.insert(2,input_line)
            compare=node.select_one('.evidence-compare');compare['class']='case-compare';compare.attrs.pop('style',None)
            for pane,klass in zip(compare.find_all('article',recursive=False),('case-reference','case-actual'),strict=True):
                pane.name='div';pane['class']=klass
                pre=pane.find('pre');pre['class']='case-excerpt';pre.attrs.pop('style',None)
                if klass=='case-reference':
                    if item['evidence']['reference']:
                        existing=list(pane.contents);pane.clear()
                        reference_title=('目标文件要求' if case.get('task_kind')=='file' else '目标媒体与资源要求' if case.get('expected_resources') else '独立参考正文' if case.get('ground_truth_main_content') else '固定验收要求')
                        append_html(pane,f'''<h4>{reference_title}</h4><p>{('全文' if len(item['evidence']['reference']) == item['evidence']['reference_chars'] else '原文节选')} · {str(len(item['evidence']['reference']))} 字符</p><pre class="case-excerpt" data-example-reference="{escape(row['attempt_id'])}">{escape(item['evidence']['reference'])}</pre>''')
                        rule=reading.new_tag('details',attrs={'class':'m-details'});summary=reading.new_tag('summary');summary.string='完整验收说明与要求';rule.append(summary)
                        for old_node in existing:rule.append(old_node.extract())
                        pane.append(rule)
                    else:pane.find('h4').string='固定验收要求'
                else:
                    pane.find('h4').string='本次真实返回'
                    if item['evidence']['actual_chars']==0:
                        pane.find('p').string='本次未交付正文 · 0 字符'
                    else:pane.find('p').string=f"{('原始文件交付元数据' if case.get('task_kind') == 'file' else '原始 Markdown 全文' if len(item['evidence']['actual']) == item['evidence']['actual_chars'] else '原始 Markdown 节选')} · {str(len(item['evidence']['actual']))} 字符"
            meta=next((p for p in node.find_all('p',recursive=False) if t('run_id_3', 'Run ID: ') in p.get_text()),None)
            if meta is not None:meta['class']='case-input'
            for table in node.select('table'):table['class']='case-checks'
            panels.append(str(node.extract()));tab=BROWSER_FAMILY_NAMES[report_family(case)]
            tabs.append('<label id="article-label-'+str(i)+'" for="article-case-'+str(i)+'">'+escape(tab)+'</label>')
            radios.append('<input class="case-choice" type="radio" name="article-case" id="article-case-'+str(i)+'" aria-labelledby="article-label-'+str(i)+'"'+(' checked' if i==1 else '')+'>')
        example_section.clear();append_html(example_section,('<h3>各类任务的真实输入与输出</h3><p>每组一道实测，左右对照题目要求和原始返回，下面保留实际判定。示例用于解释验收边界，不代表各类失败的比例。</p><div class="m-rule-strip"><b>' + t('each_task_is_pass_or_fail', 'Each task is pass or fail') + f"""</b><span>相同网址与要求</span><span>{bilingual_text(*pair('see_execution_configuration_for_budgets', 'See execution configuration for budgets'))}</span><span>失败计入总题数</span></div><div class="case-workbench">{''.join(radios)}<div class="case-tab-nav">{''.join(tabs)}</div><div class="case-panels">{''.join(panels)}</div></div>"""))
    # Charts explain frequency; the exact tables and canonical result links remain inspectable.
    for provider,node in [('all',parts.get('failure-summary'))]+[(name,parts['browser-'+name]) for name in names]:
        if node is None:continue
        items=list(model['failure_review'].values()) if provider=='all' else stats[provider]['failure_reviews']
        if not items:continue
        counts=Counter(item['category'] for item in items);labels={item['category']:item['category_label'] for item in items};total=len(items)
        ordered=sorted(counts,key=lambda key:(-counts[key],key));bars=[]
        for key in ordered:
            count=counts[key]
            bars.append('<div class="m-loss-row"><span>'+escape(labels[key])+'</span><div class="m-loss-track"><i data-reading-cause="'+escape(key)+'" data-count="'+str(count)+'" style="width:'+f'{count/total*100:.4f}'+'%"></i></div><b>'+str(count)+'</b></div>')
        figure=BeautifulSoup(f'''<figure class="m-figure" data-reading-failures="{escape(provider)}"><h4>{('全部方法' if provider == 'all' else label(provider))} · {str(total)} 条失败结果</h4><div class="m-loss-bars">{''.join(bars)}</div><figcaption>横条按该方法全部失败结果的占比绘制；右侧为结果数，每条失败只归入一个主要原因。</figcaption></figure>''','html.parser').figure
        table=node.find('table');container=table.parent
        while container.parent!=node and container.parent.get('data-browser-chapter') is None:container=container.parent
        detail=reading.new_tag('details',attrs={'class':'m-details'});summary=reading.new_tag('summary');summary.string='完整原因分布与代表证据';detail.append(summary);container.replace_with(detail);detail.append(container)
        if provider=='all':
            detail.insert(1,figure);summary.string='全部失败结果的合并分布与证据'
        else:detail.insert_before(figure)
        for item in node.select('[data-browser-specimen]'):item['class']='m-details'
    versions=_table([t('method', 'Method'),'实测版本'],[(label(name),escape(stats[name]['version'])) for name in order],'版本身份来自保存的物理调用；完整命令和版本依据见方法介绍。')
    append_html(parts['interfaces'],'<details class="m-details"><summary>本轮版本与构建身份</summary>'+versions+'</details>')
    if content.get('failure_mechanism'):
        append_html(parts['failure-summary'],'<div class="m-webfail"><div><h3>页面已返回，目标列表尚未出现</h3><div class="m-delay"><span>外层页面稳定</span><i>→</i><span>返回 Markdown</span></div><p>'+escape(content['failure_mechanism']['waiting'])+'</p></div><div><h3>页面里的子页面，需要独立交付</h3><div class="m-frame-map">外层网页<div>iframe 子页面：正文、表格或文档</div></div><p>'+escape(content['failure_mechanism']['frames'])+'</p></div></div>')
    for figure in figures_by_section['conclusion']:parts['conclusions'].insert(1,figure)
    if previous:
        both=sum(new[cid] and old[cid] for cid in cases);neither=sum(not new[cid] and not old[cid] for cid in cases)
        changes=[('两种配置都通过',both),((f'仅{label(primary)}' + t('pass_2', 'Pass')),gains),((f'仅{label(previous)}' + t('pass_2', 'Pass')),losses),('两种配置都失败',neither)]
        append_html(parts['conclusions'],f'''<figure class="m-figure" data-reading-version="{escape(primary)}"><h3>同一批题的两版结果 · {str(model['cases'])} 题</h3><div class="m-verdict-grid">{''.join(('<div data-version-relation="' + str(i) + '" data-count="' + str(count) + '"><h4>' + title + '</h4><div class="m-duel"><b>' + str(count) + '</b><small>题</small></div></div>' for i, (title, count) in enumerate(changes)))}</div><figcaption>同一验收标准，所有任务都保留在对照中；变化是在线调用的实际结果，不单独证明代码因果。</figcaption></figure>''')
    chapters=[('scope',t('01_what_was_tested', '01 What was tested'),['scope']),('results',t('02_pass_rates_latency_and_resources', '02 Pass rates, latency, and resources'),['result-decomposition','all-latency','timing-boundary','successful-latency','resources']),('method',t('03_methods_and_examples', '03 Methods and examples'),['scoring','examples']),('configuration',t('04_browsers_and_test_configurations', '04 Browsers and test configurations'),['interfaces']),('failures',t('05_failed_tasks_and_missing_text', '05 Failed tasks and missing text'),['body-quality','failure-summary']+['browser-'+name for name in order]),('conclusion','06 结论与适用范围',['conclusions'])]
    toc='<nav class="m-toc" aria-label="报告解读目录" data-reading-contents="1">'+''.join('<a href="#reading-'+anchor+'">'+title+'</a>' for anchor,title,_ in chapters)+'</nav>'
    append_html(hero,toc)
    for anchor,title,children in chapters:
        wrapper=reading.new_tag('section',attrs={'class':'m-section reading-chapter','id':'reading-'+anchor});heading=reading.new_tag('h2');heading.string=title;wrapper.append(heading)
        for child in children:
            node=parts.get(child)
            if node is None:continue
            node['class']=list(node.get('class',[]))+['m-content-block']
            for heading in node.find_all('h2',recursive=False):
                if child in {'scope','scoring','conclusions'}:heading.decompose()
                else:heading.name='h3'
            wrapper.append(node.extract())
        owner.append(wrapper)
    append_html(owner,'<footer class="m-end">调用日期：'+escape('、'.join(dates))+'。本页使用本轮冻结结果；任务、输出与逐题审查见报告对应栏目。</footer>')
    style=(Path(__file__).parent/'templates/interpretation-headless.css').read_text(encoding='utf-8')
    body='<style>'+style+'</style>'+str(reading)
    body += '<script>document.addEventListener("DOMContentLoaded",()=>{if(typeof wmbRenderFlow==="function")wmbRenderFlow()});</script>'
    evidence = {'schema':'webfetch_browser_interpretation_evidence/1','input_sha256':content['evidence_sha256'],
                'cases':model['cases'],'results':model['results'],'providers':names,'interfaces':interfaces,
                'stats':{n:{k:v for k,v in stats[n].items() if k not in {'specimen','failure_reviews'}} for n in names}}
    guide = contained_path(out,'guide');guide.mkdir(parents=True,exist_ok=True)
    (guide/'interpretation-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
    from harness.web_fetch.report import EXTRA
    standalone = _offline_browser_article(body,out,cases,model)
    (guide/'article.html').write_text('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+escape(content['title'])+'</title><style>'+STYLE+EXTRA+'</style></head><body>'+standalone+'</body></html>', encoding='utf-8')
    return (f'{body}<aside class="wrap"><a href="article.html" download>' + t('download_standalone_html', 'Download standalone HTML') + '</a> · <a href="interpretation-evidence.json">' + t('article_calculations', 'Article calculations') + '</a></aside>')


@with_measured_method_labels
def check_current_browser_article(out, cases, records, binding):
    """Verify numerical populations, unique highlights and complete drilldowns."""
    model = current_browser_article_model(out, cases, records, binding)
    interfaces = browser_task_interface_matrix(cases, model['providers'], records, compact=True)
    expected_interfaces = {(name,row['track']):[(part['mode'],part['count'],str(part['count'])+' 题：'+part['description']) for part in row['cells'][name]]
                           for row in interfaces['rows'] for name in model['providers']}
    expected = {}
    failures = {}
    for name in model['providers']:
        stats = model['stats'][name]
        expected[name, 'pass_rate'] = stats['pass_rate']
        if stats['quality']['reference_cases']:
            for key in ('word_recall','word_precision','word_f1'):expected[name,'quality_'+key]=stats['quality'][key]
        for key in ('final_call_mean_ms','prior_call_mean_ms'):
            expected[name,key]=stats[key]
        for population in ('all','successful'):
            for key in ('mean','p50','p95'):
                expected[name, population+'_'+key] = stats[population][key]
        for track, group in stats['tracks'].items():
            expected[name, 'family_'+track] = group['passed']/group['n']
        failures.update({(name,key):count for key,count in stats['failure_counts'].items()})
    class Inspect(HTMLParser):
        def __init__(self):
            super().__init__();self.metrics=[];self.failures=[];self.chapters=[];self.errors=[];self.links=[];self.active=None
            self.interfaces={};self.interface=None;self.route=None;self.network=[];self.network_row=None
        def handle_starttag(self, tag, attrs):
            data=dict(attrs)
            if 'data-browser-interface' in data:
                self.interface=(data['data-browser-interface'],data['data-track'])
                if self.interface in self.interfaces:raise ValueError('Duplicate browser task interface')
                self.interfaces[self.interface]=[]
            if 'data-route-mode' in data:
                if self.interface is None:raise ValueError('Browser route has no task interface')
                self.route=[data['data-route-mode'],int(data['data-case-count']),'']
                self.interfaces[self.interface].append(self.route)
            if 'data-network-method' in data:
                self.network_row=[data['data-network-method'],data['data-network-mode'],'']
                self.network.append(self.network_row)
            if 'data-browser-metric' in data:
                self.active=[data,''];self.metrics.append(self.active)
            if 'data-browser-failure' in data:
                self.failures.append((data['data-browser-failure'],data['data-category'],int(data['data-count'])))
            if 'data-browser-chapter' in data:self.chapters.append(data['data-browser-chapter'])
            if 'data-browser-error' in data:self.errors.append(data['data-browser-error'])
            if 'data-browser-result' in data:self.links.append((data['data-browser-result'],data.get('href')))
        def handle_data(self,text):
            if self.active is not None:self.active[1]+=text
            if self.route is not None:self.route[2]+=text
            if self.network_row is not None:self.network_row[2]+=text
        def handle_endtag(self,tag):
            if tag=='span':self.active=None;self.route=None
            if tag=='p':self.network_row=None
    for relative in ('guide/index.html','guide/article.html'):
        page=contained_path(out,relative)
        inspect=Inspect();inspect.feed(page.read_text(encoding='utf-8'))
        document = BeautifulSoup(page.read_text(encoding='utf-8'), 'html.parser')
        chapter_ids = [node.get('id') for node in document.select('.reading-chapter')]
        if chapter_ids != ['reading-'+key for key in ('scope','results','method','configuration','failures','conclusion')]:
            raise ValueError('Browser interpretation reading sequence is incomplete')
        # Protect the reference's reading behavior, not only a table of contents.
        root=document.select_one('article.m-article[data-browser-interpretation]')
        hero=root.find('header',recursive=False) if root else None
        if hero is None or 'm-hero' not in hero.get('class',[]) or hero.select_one('.m-deck') is None or hero.select_one('#overall table') is None or hero.select_one('[data-reading-ledger]') is None:
            raise ValueError('Browser interpretation is missing its opening result and reading hierarchy')
        charts=document.select('[data-reading-speed]')
        chart_keys=[(node['data-reading-speed'],node['data-statistic']) for node in charts]
        if len(chart_keys)!=len(set(chart_keys)) or set(chart_keys)!={(name,key) for name in model['providers'] for key in ('mean','p50','p95')}:
            raise ValueError('Browser interpretation latency charts omit a method or statistic')
        for node in charts:
            name,key=node['data-reading-speed'],node['data-statistic'];value=model['stats'][name]['all'][key]
            peers=[model['stats'][n]['all'][key] for n in model['providers']];best=value==min(peers) and peers.count(value)==1
            bar=node.select_one('.m-speed-track i');number=node.find('b')
            if node.get('data-value')!=str(value) or number is None or number.get_text()!=f'{value/1000:.2f} s' or bar is None or bar.get('style')!=f'width:{value/max(peers)*100:.4f}%':
                raise ValueError('Browser interpretation chart values or bar lengths differ from evidence')
            if ('best' in bar.get('class',[]))!=best or ('good' in number.get('class',[]))!=best:
                raise ValueError('Browser interpretation chart highlights differ from the full population')
        if model['examples']:
            controls=document.select('.case-workbench > input[type="radio"]')
            if len(controls)!=len(model['examples']) or sum(node.has_attr('checked') for node in controls)!=1 or not controls[0].has_attr('checked'):
                raise ValueError('Browser interpretation case selection is incomplete')
            for index,node in enumerate(controls,1):
                panel=document.find(id='article-panel-'+str(index));tab=document.find(id='article-label-'+str(index))
                if node.get('id')!='article-case-'+str(index) or panel is None or 'case-panel' not in panel.get('class',[]) or panel.select_one('.case-compare') is None or tab is None or tab.get('for')!=node['id']:
                    raise ValueError('Browser interpretation case tabs are not connected to their evidence')
        for comparison in model['content'].get('comparisons',[]):
            node=document.select_one('[data-reading-comparison="'+comparison['id']+'"]')
            expected_counts=[sum(model['stats'][name]['tracks'][track]['passed'] for track in comparison['tracks']) for name in (comparison['left'],comparison['right'])]
            if node is None or [item.get_text() for item in node.select('.m-duel b')]!=[str(value) for value in expected_counts]:
                raise ValueError('Browser interpretation grouped task comparison differs from evidence')
        expected_charts={name:dict(Counter(item['category'] for item in model['stats'][name]['failure_reviews'])) for name in model['providers'] if model['stats'][name]['failure_reviews']}
        if model['failure_review']:expected_charts['all']=dict(Counter(item['category'] for item in model['failure_review'].values()))
        actual_charts={}
        for figure in document.select('[data-reading-failures]'):
            name=figure['data-reading-failures'];parts=figure.select('[data-reading-cause]')
            if name in actual_charts:raise ValueError('Duplicate browser failure chart')
            counts={part['data-reading-cause']:int(part['data-count']) for part in parts};actual_charts[name]=counts
            if len(counts)!=len(parts):raise ValueError('Duplicate browser failure category in chart')
            for part in parts:
                if part.get('style')!=f'width:{int(part["data-count"])/sum(counts.values())*100:.4f}%':
                    raise ValueError('Browser failure chart bar differs from its population')
        if actual_charts!=expected_charts:raise ValueError('Browser failure charts omit reviewed results')
        previous=model['content'].get('version_baseline')
        if previous:
            primary=model['content'].get('focus_provider') or max(model['providers'],key=lambda name:model['stats'][name]['passed'])
            lookup={(row['provider'],row['case_id']):task_passed(row['ok'],row['metrics'],cases[row['case_id']].get('task_kind','body')) for row in records}
            pairs=[(lookup[previous,cid],lookup[primary,cid]) for cid in cases]
            expected_transitions=[sum(before and after for before,after in pairs),sum(not before and after for before,after in pairs),sum(before and not after for before,after in pairs),sum(not before and not after for before,after in pairs)]
            nodes=document.select('[data-version-relation]')
            if len(nodes)!=4 or [int(node['data-count']) for node in nodes]!=expected_transitions:
                raise ValueError('Browser version comparison does not cover every task')
        expected_root = {}
        for name in model['providers']:
            expected_root.update({(name,key):count for key,count in model['stats'][name]['reviewed_failure_counts'].items()})
        if model['failure_review']:
            expected_root.update({('all',key):count for key,count in Counter(item['category'] for item in model['failure_review'].values()).items()})
        actual_root = [(node['data-reviewed-failure'],node['data-category'],int(node['data-count'])) for node in document.select('[data-reviewed-failure]')]
        if len(actual_root) != len(expected_root) or {(name,key):count for name,key,count in actual_root} != expected_root:
            raise ValueError('Reviewed failure distribution differs from evidence')
        category_labels = {item['category']:item['category_label'] for item in model['failure_review'].values()}
        for node in document.select('[data-reviewed-failure]'):
            if node.find_parent('tr').find('a').get_text() != category_labels[node['data-category']]:
                raise ValueError('Reviewed failure category label differs from evidence')
        if document.select('[data-reviewed-reason], [data-reviewed-treatment]'):
            raise ValueError('Interpretation duplicates the per-result audit inventory')
        for item in model['figures']:
            matches = [node for node in document.select('img[data-browser-figure]') if node['data-browser-figure'] == item['path']]
            if len(matches) != 1 or matches[0].get('alt') != item['alt']:
                raise ValueError('Browser interpretation figure is missing or wrong')
            if relative.endswith('article.html'):
                suffix = Path(item['path']).suffix.lower()
                mime = 'image/jpeg' if suffix in {'.jpg','.jpeg'} else 'image/'+suffix[1:]
                expected_src = 'data:'+mime+';base64,'+base64.b64encode(contained_path(out,item['path']).read_bytes()).decode('ascii')
            else:
                expected_src = '../'+item['path']
            if matches[0].get('src') != expected_src or matches[0].find_parent('figure').find('figcaption').get_text() != item['caption']:
                raise ValueError('Browser interpretation figure bytes or caption differ')
        for name in model['providers']:
            values = document.select('[data-browser-error="'+name+'"]')
            stat = model['stats'][name]
            if len(values) != 1 or values[0].get_text() != _browser_error_excerpt(stat['specimen'], bool(stat['failure_counts']),reviewed_error=_browser_reviewed_error(out,model,stat['specimen'])):
                raise ValueError('Browser specimen error excerpt is missing or differs from evidence')
        examples = document.select('[data-browser-example]')
        expected_examples = {item['row']['attempt_id']:item for item in model['examples']}
        if len(examples) != len(expected_examples) or {node['data-browser-example'] for node in examples} != set(expected_examples):
            raise ValueError('Browser interpretation examples are incomplete')
        for node in examples:
            item = expected_examples[node['data-browser-example']]
            if node.find('p').get_text() != item['explanation']:
                raise ValueError('Browser interpretation example explanation differs')
            evidence = item['evidence']
            aid = item['row']['attempt_id']
            for field in ('expected','actual'):
                values = node.select('[data-example-'+field+']')
                if len(values) != 1 or values[0].get('data-example-'+field) != aid or values[0].get_text() != evidence[field]:
                    raise ValueError('Browser example expected or actual evidence is missing or wrong')
            references=node.select('[data-example-reference]')
            if evidence['reference']:
                if len(references)!=1 or references[0].get_text()!=evidence['reference']:
                    raise ValueError('Browser example reference excerpt differs from frozen text')
            elif references:raise ValueError('Browser example invents a reference excerpt')
            checks = node.select('[data-example-check]')
            actual_checks = {value['data-check-key']:value.get_text() for value in checks if value.get('data-example-check') == aid}
            if len(checks) != len(evidence['checks']) or actual_checks != {value['key']:value['verdict'] for value in evidence['checks']}:
                raise ValueError('Browser example scoring checks are missing or wrong')
            for value in checks:
                check = next(item for item in evidence['checks'] if item['key'] == value['data-check-key'])
                expected_state = 'good' if check['verdict']==t('pass_2', 'Pass') else 'bad' if check['verdict']==t('fail_2', 'Fail') else 'neutral'
                if value.get('class') != [expected_state]:
                    raise ValueError('Browser example check color contradicts its verdict')
                cells = value.find_parent('tr').find_all('td')[1:]
                if [cell.get_text() for cell in cells] != [check['label'],check['expected'],check['actual'],check['verdict']]:
                    raise ValueError('Browser example expected/actual check values differ')
            verdict = node.select_one('[data-example-verdict]')
            if verdict is None or verdict.get_text() != (t('pass_2', 'Pass') if evidence['passed'] else t('fail_2', 'Fail')) or evidence['run_id'] not in node.get_text():
                raise ValueError('Browser example run identity or verdict is missing')
            row = item['row']
            target = '#offline-result-'+row['attempt_id'] if relative.endswith('article.html') else '../attempts/'+row['attempt_id']+'.html'
            if target not in [anchor.get('href') for anchor in node.select('a[href]')]:
                raise ValueError('Browser interpretation example evidence target is broken')

        for name in model['providers']:
            expected_wait=_browser_wait_summary(records,name)
            node=document.select('[data-browser-wait="'+name+'"]')
            if len(node)!=1 or node[0].get_text()!=expected_wait:
                raise ValueError('Browser wait disclosure is missing or wrong')
        timing=document.find(id='timing-boundary')
        if timing is None or len(timing.select('tbody tr'))!=len(model['providers']):
            raise ValueError('Browser retry timing boundary is missing')
        for node,name in zip(timing.select('tbody tr'),model['providers'],strict=True):
            values=[cell.get_text() for cell in node.find_all('td')]
            stat=model['stats'][name]
            if values[-3:]!=[str(stat['physical_calls']),f"{stat['final_call_mean_ms']:,.0f}",f"{stat['prior_call_mean_ms']:,.0f}"]:
                raise ValueError('Browser retry cost differs from recorded calls')
        for anchor in document.select('a[href^="#failure-category-"]'):
            if document.find(id=anchor['href'][1:]) is None:
                raise ValueError('Failure category drilldown is broken')
        contents = document.select_one('[data-reading-contents]')
        if contents is None or [item.get('href') for item in contents.select('a')] != ['#'+key for key in chapter_ids]:
            raise ValueError('Browser interpretation chapter navigation is incomplete')
        if 'P50 是中位数' not in document.get_text() or '不同日期' not in document.find(id='all-latency').get_text():
            raise ValueError('Browser latency population or timing limitation is missing')
        actual_interfaces={key:[tuple(part) for part in parts] for key,parts in inspect.interfaces.items()}
        if actual_interfaces != expected_interfaces:
            raise ValueError('Browser task interface matrix is missing or differs from declared tasks')
        expected_network=[(row['provider'],row['mode'],row['text'])
                          for row in _browser_network_rows(records,model['providers'])]
        if [tuple(row) for row in inspect.network] != expected_network:
            raise ValueError('Browser network environment disclosure is missing or wrong')
        actual={}
        for data,text in inspect.metrics:
            key=(data['data-provider'],data['data-browser-metric'])
            if key in actual or key not in expected:raise ValueError('Duplicate or unknown browser metric')
            value=expected[key]
            if data.get('data-value') != ('' if value is None else str(value)):
                raise ValueError('Browser metric value differs from evidence')
            peers=[v for (name,metric),v in expected.items() if metric==key[1] and v is not None]
            high=key[1]=='pass_rate' or key[1].startswith(('family_','quality_'))
            extreme=(max(peers) if high else min(peers)) if peers else None
            winner=value is not None and value==extreme and peers.count(extreme)==1
            if (data.get('data-winner')=='true') != winner:
                raise ValueError('Browser metric unique highlight is missing or wrong')
            visible='—' if value is None else (f'{value:.1%}' if high else f'{value:,.0f}')
            if text.strip()!=visible:raise ValueError('Visible browser metric differs from evidence')
            actual[key]=value
        if actual != expected:raise ValueError('Browser metric population is incomplete')
        if len(inspect.failures)!=len(failures) or {(n,k):v for n,k,v in inspect.failures}!=failures:
            raise ValueError('Browser failure distribution differs from evidence')
        for values in (inspect.chapters,inspect.errors):
            if Counter(values)!=Counter(model['providers']):raise ValueError('Browser chapter or program error evidence is missing')
        if Counter(n for n,link in inspect.links)!=Counter(model['providers']):
            raise ValueError('Browser result drilldown is missing')
        for name,link in inspect.links:
            attempt = model['stats'][name]['specimen']['attempt_id']
            expected_link = '#offline-result-'+attempt if relative.endswith('article.html') else '../attempts/'+attempt+'.html'
            if link!=expected_link or (not link.startswith('#') and not (page.parent/urlsplit(link).path).is_file()):
                raise ValueError('Browser result evidence target is broken')
        if relative.endswith('article.html'):
            offline = BeautifulSoup(page.read_text(encoding='utf-8'),'html.parser')
            if offline.select('script,link[rel="stylesheet"]'):
                raise ValueError('Independent article requires external runtime')
            for name in model['providers']:
                row = model['stats'][name]['specimen']
                anchor = offline.find(id='offline-result-'+row['attempt_id'])
                output = anchor.select_one('[data-standalone-output]') if anchor else None
                if output is None or output.get_text() != contained_path(out,row['artifact_path']).read_text(encoding='utf-8',errors='replace'):
                    raise ValueError('Independent article omits complete specimen output')
    return model


def render_explainer(out, cases, records, *, table=None, cell=None, link=None, authored=True, content_binding=None):
    """Render explicitly bound editorial content, otherwise summarize these inputs."""
    out = Path(out)
    dataset_path = out / 'dataset.json'
    if content_binding is None and dataset_path.is_file():
        content_binding = json.loads(dataset_path.read_text(encoding='utf-8')).get('comparison', {}).get('report_interpretation')
    if authored and content_binding is not None:
        return _render_current_browser_article(out, cases, records, content_binding)
    passes = sum(task_passed(row['ok'], row['metrics'],
                 cases[row['case_id']].get('task_kind', 'body')) for row in records)
    context = ''
    if dataset_path.is_file():
        scope = json.loads(dataset_path.read_text(encoding='utf-8')).get('saved_matrix_scope', {})
        if scope:
            from harness.web_fetch.report import provider_label
            rows = []
            for name in dict.fromkeys(row['provider'] for row in records):
                selected = [row for row in records if row['provider'] == name]
                passed = sum(task_passed(row['ok'], row['metrics'],
                             cases[row['case_id']].get('task_kind', 'body')) for row in selected)
                rows.append((escape(provider_label(name)), str(len(selected)),
                             str(passed), f'{passed/len(selected):.1%}'))
            context = _table([('方法', 'Method'), ('保存结果', 'Saved results'),
                              ('通过', 'Passed'), ('通过率', 'Pass rate')], rows,
                             t('method_comparison_for_this_round', 'Method comparison for this round'))
            context += '<h2>' + bilingual_text('结果说明与限制', 'Findings and limitations') + '</h2>'
            context += ''.join('<p>'+escape(note)+'</p>' for note in scope.get('limitations', []))
            context += '<p><a href="../review/index.html">' + bilingual_text(
                '查看逐题核查、实际返回对照及修订证据',
                'View case audits, actual response comparisons and revision evidence') + '</a></p>'
    return ('<section class="wrap"><h1>'
            + bilingual_text(*pair('task_scope_and_acceptance_rules', 'Task scope and acceptance rules'))
            + '</h1><p>' + bilingual_text(*pair('this_report_contains_tasks_and_logical_results', 'This report contains {count} tasks and {count2} logical results, with {passes}/{count2} passing. Findings apply only to the registered tasks and rules.', count=len(cases), count2=len(records), passes=passes))
            + '</p><p><a href="../datasets/index.html">'
            + bilingual_text(*pair('view_all_tasks_and_acceptance_rules', 'View all tasks and acceptance rules'))
            + '</a> · <a href="../analysis/index.html">'
            + bilingual_text(*pair('view_results_actual_responses_and_failure_evidence', 'View results, actual responses and failure evidence'))
            + '</a></p>' + context + '</section>')
