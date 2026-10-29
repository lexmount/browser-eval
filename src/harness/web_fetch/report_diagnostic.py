"""Validate a diagnostic report model without upgrading it to certification."""

import hashlib
import json
import math
import re
import statistics
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.paths import contained_path, safe_identifier, validate_http_url

from harness.record_io import load_jsonl
from harness.reporting.presentation import check_presentation
from harness.reporting.versions import check_build_version
from harness.web_fetch.artifacts import (
    verify_record_private_artifacts,
    verify_record_public_call_logs,
)
from harness.web_fetch.publication_privacy import project_file, publication_risk
from harness.web_fetch.reference_lifecycle import require_verified_reference_checks


def require_diagnostic_scope(model, profile):
    """Compare the report with an independent planned population, not itself."""
    if profile.get('variant') != 'diagnostic-cohorts':
        raise ValueError('Expected a diagnostic cohort profile')
    plan = profile.get('cohort_plan', [])
    expected = {g['id']: g for g in plan}
    groups = {g['id']: g for g in model['cohorts']}
    if not expected or len(expected) != len(plan) or set(groups) != set(expected):
        raise ValueError('Report cohort scope differs from the planned matrix')
    for key, group in expected.items():
        ids = sorted(r['id'] for r in model['rows'] if r['cohort'] == key)
        digest = hashlib.sha256(('\n'.join(ids) + '\n').encode()).hexdigest()
        if (len(ids) != group['case_count'] or len(set(ids)) != len(ids)
                or groups[key]['case_count'] != len(ids)
                or digest != group['case_ids_sha256']):
            raise ValueError('Report case scope differs from the planned matrix: ' + key)
    identity = profile.get('implementation')
    if identity:
        provenance = {g['cohort']: g for g in model.get('provenance', [])}
        if set(provenance) != set(expected):
            raise ValueError('Planned implementation provenance is incomplete')
        for key in expected:
            if (groups[key].get('version') != identity['reported_version']
                    or provenance[key].get('binary_sha256') != identity['binary_sha256']):
                raise ValueError('Report implementation differs from the planned matrix: ' + key)


def _saved_implementation_identity(record, batches):
    metadata = record['provider_metadata']
    direct = (metadata.get('reported_version'), metadata.get('binary_sha256'))
    if all(direct):
        return direct
    if any(direct) or record.get('ok') or record.get('error_type') != 'timeout':
        raise ValueError('Missing provider implementation identity')
    # Older deadline boundaries discarded worker metadata. The execution
    # receipt still binds this exact attempt to its preflight-verified binary.
    identities = []
    for batch in batches:
        if record['attempt_id'] in batch.get('attempt_ids', []):
            for provider in batch.get('providers', []):
                if provider.get('name') == record['provider']:
                    identity = provider.get('implementation_identity', {})
                    identities.append((identity.get('reported_version'), identity.get('binary_sha256')))
    if len(identities) != 1 or not all(identities[0]):
        raise ValueError('Timeout attempt has no uniquely bound implementation identity')
    return identities[0]


def prepare_diagnostic_evidence(spec_path, output):
    """Adapt complete saved cohorts and bound reviews, never make provider calls."""

    spec_path, output = Path(spec_path).resolve(), Path(output)
    spec = json.loads(spec_path.read_text())
    if spec.get('schema') != 'webfetch_diagnostic_inputs/1':
        raise ValueError('Unsupported diagnostic input manifest')
    if any(output.iterdir()):
        raise ValueError('Diagnostic preparation requires an empty staging directory')
    model = dict(spec['report']);rows=[];cohorts=[];provenance=[]
    def encode(value):return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()
    def save(path, raw, scope):
        public, projection = project_file(path, raw)
        target=contained_path(output,path);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(public)
        binding=dict(path=path,sha256=hashlib.sha256(public).hexdigest(),scope=scope)
        if projection:
            binding['publication_projection']=projection
            binding['scope']=binding['scope'].replace('未改写、','')
            binding['scope'] += ' 此下载件为脱敏公开副本；原始字节及评分保持不变，原始 SHA-256：'+projection['original_sha256']+'。'
        return binding
    for cohort in spec['cohorts']:
        run=contained_path(spec_path.parent,cohort['run'])
        dataset_path=run/'dataset.json';results_path=run/'results.jsonl'
        observations_path=contained_path(spec_path.parent,cohort['observations'])
        review_path=contained_path(spec_path.parent,cohort['review'])
        dataset=json.loads(dataset_path.read_text());cases={c['id']:c for c in dataset['cases']}
        records=load_jsonl(results_path)
        observations=json.loads(observations_path.read_text());review=json.loads(review_path.read_text())
        expected_bindings={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in
            [('dataset_sha256',dataset_path),('results_sha256',results_path),('observations_sha256',observations_path)]}
        if review.get('bindings') != expected_bindings:
            raise ValueError('Independent review does not bind the current cohort')
        reviewed={r['case_id']:r for r in review['rows']}
        if (len(records)!=len(cases) or {r['case_id'] for r in records}!=set(cases)
                or len(review['rows'])!=len(cases) or set(reviewed)!=set(cases)
                or set(observations)!=set(cases) or len({r['provider'] for r in records})!=1):
            raise ValueError('Diagnostic inputs require the complete task/observation/review matrix')
        execution=json.loads((run/'execution.json').read_text())
        attempt_ids=[aid for batch in execution['batches'] for aid in batch.get('attempt_ids',[])]
        if (len(attempt_ids)!=len(records) or set(attempt_ids)!={r['attempt_id'] for r in records}
                or set(execution.get('aggregate',{}).get('case_ids',[]))!=set(cases)
                or not execution.get('aggregate',{}).get('completed_at')):
            raise ValueError('Incomplete saved execution receipt')
        require_verified_reference_checks(execution['batches'],cases=cases,records=records,source=run)
        identities={_saved_implementation_identity(r,execution['batches']) for r in records}
        if len(identities)!=1 or any(not v for v in next(iter(identities))):
            raise ValueError('Missing or mixed provider implementation identity')
        version,binary_sha=next(iter(identities))
        cohorts.append(dict(id=cohort['id'],label=cohort['label'],case_count=len(cases),version=version))
        provenance.append(dict(cohort=cohort['id'],**expected_bindings,
            review_sha256=hashlib.sha256(review_path.read_bytes()).hexdigest(),binary_sha256=binary_sha))
        revision_path = run / 'scoring-revisions.json'
        if revision_path.is_file():
            revision_raw = revision_path.read_bytes()
            provenance[-1]['scoring_revision_sha256'] = hashlib.sha256(revision_raw).hexdigest()
            provenance[-1]['scoring_revisions'] = json.loads(revision_raw)
        for record in records:
            cid=safe_identifier(record['case_id']);case=cases[cid];audit=reviewed[cid];observation=observations[cid]
            if audit.get('reviewed') is not True or not all(audit.get(k) for k in ('cause','cause_detail','followup_status','source_state')):
                raise ValueError('Incomplete independent review: '+cid)
            if observation.get('case_id') != cid or observation.get('independent') is not True:
                raise ValueError('Observation identity is not independent: '+cid)
            verify_record_private_artifacts(run,record);verify_record_public_call_logs(run,record)
            raw=contained_path(run,record['artifact_path']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=record['metrics']['content_sha256']:
                raise ValueError('Primary answer changed: '+cid)
            answer=save('answers/'+cid+'.txt',raw,'本轮唯一调用的完整输出，未改写、未以补抓替换。')
            log={k:v for k,v in record.items() if k not in ('provider_metadata','artifact_path')}
            metadata=record['provider_metadata'];public_call=metadata.get('call_log')
            if public_call:
                call=json.loads(contained_path(run,public_call['path']).read_text())
                log['process']=call['process'];log['request']=call['request'];log['call_error']=call['error']
                log['error_message']=call['error']['message']
            log['provider_identity']=dict(reported_version=version,binary_sha256=binary_sha)
            reference=save('reference/'+cid+'.json',encode(case),'调用前冻结的完整任务和验收契约；不是网页全部内容的逐字标准答案。')
            source=save('source/'+cid+'.json',encode({k:v for k,v in observation.items() if k!='outer_html'}),
                '独立浏览器的可见文字及访问记录；具体范围和补充观察见记录，隐藏 DOM 不作为可见答案。访问失败时保留错误，不算目标正文。')
            row=dict(id=cid,cohort=cohort['id'],url=case['url'],task=case.get('task_prompt') or case.get('name') or cid,
                run_id=record['run_id'],initial_pass=task_passed(record['ok'],record['metrics']),
                first_answer=answer,answer=answer,reference=reference,source=source,
                log=save('logs/'+cid+'.json',encode(log),'本轮调用、评分明细和脱敏后的进程记录。'),
                failure=log.get('error_message') or '; '.join(record['metrics'].get('stable_task_errors') or []),
                cause=audit['cause'],cause_detail=audit['cause_detail'],followup_status=audit['followup_status'],
                source_checked_at=observation.get('checked_at'),reviewed=True,source_state=audit['source_state'])
            if row['initial_pass']:
                row['success_audit']={k:v for k,v in audit.items() if k.startswith('source_')}
            if audit.get('independent_verdict'):
                row['independent_verdict'] = audit['independent_verdict']
            rows.append(row)
    model.update(schema='webfetch_cohort_diagnostic/1',certified=False,cohorts=cohorts,rows=rows,provenance=provenance)
    model['metrics']={g['id']:[sum(r['initial_pass'] for r in rows if r['cohort']==g['id']),g['case_count']] for g in cohorts}
    model['metrics']['overall']=[sum(r['initial_pass'] for r in rows),len(rows)]
    audited=[r for r in rows if r.get('success_audit')]
    model['success_audit']=dict(audited_passes=len(audited),source_state_counts=dict(Counter(r['source_state'] for r in audited)))
    (output/'report.json').write_bytes(encode(model))

FAILURE_TYPES = {
    'request': '调用未成功交付', 'blocked': '验证或访问限制页面',
    'identity': '网址或页面语言不符', 'text': '缺少必需文字或事实',
    'body': '正文覆盖不足', 'records': '列表或表格不完整',
    'encoding': '文字编码损坏', 'truncated': '输出被截断',
    'shell': '仅有脚本或页面框架', 'length': '返回内容过少',
    'pattern': '内容结构不符', 'other': '其它未通过检查',
}


def diagnostic_reference_excerpt(row, files):
    """Show actual acceptance requirements, not reference storage metadata."""
    raw=files[row['reference']['path']].decode('utf-8')
    try:case=json.loads(raw)
    except ValueError:return raw
    if not isinstance(case,dict):return raw
    contract=case.get('acceptance_contract') or {};parts=[]
    schemas={'named_link':'名称与详情链接','table_row':'表格行','body_text':'网页文字','page_identity':'网址与语言'}
    if contract.get('schema'):
        parts.append('验收内容：'+schemas.get(contract['schema'],contract['schema']))
    if contract.get('minimum_records'):
        parts.append('至少 '+str(contract['minimum_records'])+' 条完整、唯一的记录')
    if contract.get('table_columns'):
        parts.append('每行必需字段：'+'；'.join(c['label'] for c in contract['table_columns']))
    anchors=contract.get('stable_text_anchors',[])+case.get('required_facts',[])
    if anchors:parts.append('必需文字：'+'；'.join(' / '.join(a.get('alternatives') or [a.get('id','')]) for a in anchors))
    if case.get('min_content_chars'):parts.append('内容长度至少 '+str(case['min_content_chars'])+' 个字符')
    body=case.get('body_requirements') or {}
    if body.get('regions'):parts.append('正文 '+str(len(body['regions']))+' 个分区；覆盖率及分区原文见完整验收契约')
    patterns=contract.get('page_patterns') or []
    if patterns:parts.append('要求的结构模式：'+'；'.join(patterns))
    return '\n'.join(parts) or case.get('task_prompt') or raw


def diagnostic_source_excerpt(row, files):
    """Explain an unavailable source with its observed error, not raw JSON."""
    binding = row.get('source')
    if not binding:
        return row.get('source_error') or '未取得目标正文。'
    raw = files[binding['path']].decode('utf-8')
    try:
        observation = json.loads(raw)
    except ValueError:
        return raw
    if not isinstance(observation, dict):
        return raw
    if observation.get('content_text'):
        return observation['content_text']
    errors = [e.get('message') or e.get('type', '') for e in observation.get('attempt_errors', [])]
    return '未取得可见目标正文。'+('\n'+'\n'.join(errors) if errors else '')


def diagnostic_specimen_excerpt(row, field, files, model):
    """Select a relevant verbatim window; a focus cannot invent evidence."""
    if field=='reference':text=diagnostic_reference_excerpt(row,files)
    elif field=='source':text=diagnostic_source_excerpt(row,files)
    else:text=files[row[field]['path']].decode('utf-8')
    focus=model.get('interpretation_focus',{}).get(row['id'],{}).get(field)
    start=0
    if focus:
        if not isinstance(focus,str) or focus not in text:
            raise ValueError('Interpretation focus absent from bound evidence: '+row['id']+'/'+field)
        start=max(0,text.index(focus)-60)
    return ('…' if start else '')+text[start:start+600]+('…' if len(text)>start+600 else '')


def diagnostic_findings(row, files):
    """Explain saved first-call checks, without inferring an engine root cause."""
    if row['initial_pass']:
        return []
    try:
        log = json.loads(files[row['log']['path']])
    except (ValueError, TypeError):
        log = {}
    if not isinstance(log, dict):
        log = {}
    metrics = log.get('metrics') or {}
    if not isinstance(metrics, dict):
        metrics = {}
    findings = []
    def add(kind, label, expected, actual):
        findings.append(dict(kind=kind, label=label, expected=str(expected), actual=str(actual)))
    error = '; '.join(str(log.get(k) or '') for k in
                      ('process_error', 'parse_error', 'score_error', 'error_message')).strip('; ')
    process = log.get('process') or {}
    if isinstance(process, dict) and process.get('exit_code') and process.get('stderr'):
        stderr = process['stderr']
        error = stderr[stderr.rfind('\nError:') + 1:] if '\nError:' in stderr else stderr
    if log.get('ok') is False or metrics.get('request_ok') is False:
        add('request', '调用未成功交付', '调用成功并返回约定的输出', error or row.get('failure') or '保存记录中的请求状态为失败')
    if metrics.get('blocked_page') or metrics.get('stable_blocked_page'):
        add('blocked', '返回验证或访问限制页面', '目标网页内容，而非验证页面',
            '；'.join(metrics.get('blocked_page_hits') or []) or '保存评分识别到访问限制页面')
    if metrics.get('text_encoding_corruption'):
        add('encoding', '输出文字损坏', '可读、无编码损坏的文字', '保存评分识别到编码损坏')
    if metrics.get('declared_truncation_hits'):
        add('truncated', '输出声明被截断', '完整的任务内容', '；'.join(metrics['declared_truncation_hits']))
    if metrics.get('script_payload_shell'):
        add('shell', '只有脚本或页面框架', '目标正文', '保存评分识别到脚本或空框架')
    if metrics.get('page_identity_status') == 'mismatch':
        add('identity', '页面身份诊断（不参与通过判定）', '仅供核对访问路径，无通过要求',
            '最终网址：' + str(log.get('final_url') or '记录未提供') + '；页面语言：' + str(metrics.get('observed_document_language') or log.get('document_language') or '记录未提供'))
    for check in metrics.get('stable_anchor_results') or []:
        if check.get('pass') is False:
            add('text', '缺少必需文字', ' / '.join(check.get('alternatives') or [check.get('id', '未提供')]), '首轮输出未匹配到该文字')
    for check in metrics.get('fact_results') or []:
        if check.get('pass') is False:
            add('text', '缺少必需事实', ' / '.join(check.get('alternatives') or [check.get('id', '未提供')]), '首轮输出未匹配到该事实')
    if metrics.get('body_coverage_pass') is False:
        try:
            reference = json.loads(files[row['reference']['path']])
            reference_regions = {r['id']: r.get('text', '') for r in
                reference.get('body_requirements', {}).get('regions', [])}
        except (KeyError, ValueError, TypeError, AttributeError):
            reference_regions = {}
        regions = [r for r in metrics.get('body_region_results', []) if r.get('pass') is False]
        for region in regions:
            # Matcher fragments have normalized/deleted characters and are not
            # readable quotations. Display frozen source text, never reconstruct
            # a supposed sentence from the score's unmatched character spans.
            sample = re.sub(r'\s+', ' ', reference_regions.get(region.get('id'), '')).strip()[:300]
            add('body', '正文分区未达覆盖要求',
                str(region.get('id', '正文分区')) + '：覆盖率至少 ' + str(round(100 * region.get('minimum', 0), 1)) + '%；参考内容：' + (sample or '见标准答案'),
                '实际覆盖率 ' + str(round(100 * region.get('coverage', 0), 1)) + '%')
        if not regions:
            add('body', '正文覆盖不足', '满足声明的正文分区要求', '正文覆盖检查不通过；原记录未提供分区明细')
    if metrics.get('stable_structure_pass') is False:
        add('records', '列表或表格不完整',
            '至少 ' + str(metrics.get('stable_minimum_records', '记录未提供')) + ' 条完整且唯一的记录',
            str(metrics.get('stable_unique_record_count', '记录未提供')) + ' 条完整且唯一的记录')
    for check in metrics.get('stable_field_results') or []:
        if check.get('pass') is False:
            add('records', '记录缺少必需字段',
                str(check.get('field', '未提供字段'))+'：至少 '+str(check.get('minimum', '记录未提供'))+' 条记录提供该字段',
                str(check.get('count', '记录未提供'))+' 条记录提供该字段')
    for group in metrics.get('composite_group_results') or []:
        if group.get('pass') is False:
            add('records', '记录组不完整', str(group.get('id', '记录组')) + '：至少 ' + str(group.get('minimum', '记录未提供')) + ' 条合格记录',
                str(group.get('count', '记录未提供')) + ' 条合格记录')
    if metrics.get('composite_acceptance_pass') is False or any(k in str(row.get('failure', '')) for k in ('composite', 'incomplete_records', 'too_few_complete_records')):
        if not any(f['kind'] == 'records' for f in findings):
            add('records', '列表或表格不完整', '满足任务的记录数量、字段、链接和唯一性要求', row.get('failure') or '保存评分中的组合记录检查不通过')
    if metrics.get('length_pass') is False:
        add('length', '返回内容过少', '至少 ' + str(metrics.get('minimum_content_chars', '记录未提供')) + ' 个字符',
            str(metrics.get('content_chars', '记录未提供')) + ' 个字符')
    for check in (metrics.get('pattern_results') or []) + (metrics.get('stable_page_pattern_results') or []):
        if check.get('pass') is False:
            add('pattern', '缺少要求的内容结构', check.get('pattern') or check.get('id', '声明的结构要求'),
                '未匹配到该结构模式' if 'pattern' in check else
                str(check.get('count', '记录未提供')) + ' 项；至少要求 ' + str(check.get('minimum', '记录未提供')) + ' 项')
    if not findings:
        add('other', '其它未通过检查', '满足完整任务要求', row.get('failure') or '保存记录没有提供具体失败项')
    return findings


def diagnostic_failure_counts(model, files):
    return Counter(kind for row in model['rows']
                   for kind in {f['kind'] for f in diagnostic_findings(row, files)})


def diagnostic_call_summary(rows, files):
    """Pool physical primary-call observations, never subgroup percentiles."""
    logs = []
    for row in rows:
        try:
            log = json.loads(files[row['log']['path']])
        except (ValueError, TypeError):
            log = {}
        logs.append(log if isinstance(log, dict) else {})
    statuses = [(l.get('metrics') or {}).get('request_ok', l.get('ok')) for l in logs]
    times = [l.get('elapsed_ms') for l in logs]
    complete_times = all(type(t) in (int, float) and math.isfinite(t) and t >= 0 for t in times)
    times = sorted(times) if complete_times else []
    return {'total': len(rows), 'accepted': sum(r['initial_pass'] for r in rows),
        'delivered': sum(statuses) if all(type(s) is bool for s in statuses) else None,
        'mean_ms': statistics.mean(times) if times else None,
        'p50_ms': statistics.median(times) if times else None,
        'p95_ms': times[max(0, math.ceil(.95 * len(times)) - 1)] if times else None}


def diagnostic_cohort_summary(model, files):
    return [{'id': group['id'], **diagnostic_call_summary(
        [r for r in model['rows'] if r['cohort'] == group['id']], files)}
        for group in model['cohorts']]


def load_diagnostic(source):
    source = Path(source).resolve()
    model = json.loads(contained_path(source, "report.json").read_text())
    if model.get("schema") != "webfetch_cohort_diagnostic/1" or model.get("certified") is not False:
        raise ValueError("Expected an explicitly diagnostic WebFetch model")
    groups = {g["id"]: g for g in model["cohorts"]}
    if len(groups) != len(model["cohorts"]):
        raise ValueError("Duplicate cohort")
    rows = model["rows"]
    ids = [safe_identifier(r["id"]) for r in rows]
    if not rows or len(ids) != len(set(ids)):
        raise ValueError("Empty or duplicate result inventory")
    examples = model.get('interpretation_case_ids', [])
    if not isinstance(examples, list) or len(examples) != len(set(examples)) or set(examples) - set(ids):
        raise ValueError('Invalid interpretation case inventory')
    actual = Counter(r["cohort"] for r in rows)
    if actual != Counter({key: group["case_count"] for key, group in groups.items()}):
        raise ValueError("Result population differs from declared cohorts")
    metrics = {key: [sum(r["initial_pass"] for r in rows if r["cohort"] == key), count]
               for key, count in actual.items()}
    metrics["overall"] = [sum(r["initial_pass"] for r in rows), len(rows)]
    if metrics != model["metrics"]:
        raise ValueError("Summary differs from individual results")
    files = {}
    for row in rows:
        if type(row["initial_pass"]) is not bool:
            raise ValueError("Result must have an explicit Boolean verdict")
        validate_http_url(row["url"])
        safe_identifier(row["run_id"])
        for field in ("first_answer", "answer", "reference", "source", "log"):
            binding = row.get(field)
            if binding is None:
                if field in ("first_answer", "answer", "reference", "log"):
                    raise ValueError("Missing result evidence: " + field)
                continue
            if not isinstance(binding, dict):
                raise ValueError("Invalid evidence binding: " + field)
            if not isinstance(binding.get('path'), str) or not binding['path'].strip():
                raise ValueError("Invalid evidence path: " + field)
            digest = binding.get('sha256')
            if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
                raise ValueError("Invalid evidence sha256: " + field)
            if field == 'reference' or 'scope' in binding:
                scope = binding.get('scope')
                if not isinstance(scope, str) or not scope.strip():
                    raise ValueError("Invalid " + field + " evidence scope: expected nonempty text")
            relative = binding["path"]
            original = source / relative
            if any(p.is_symlink() for p in (original, *original.parents)
                   if p == source or p.is_relative_to(source)):
                raise ValueError("Symlink evidence is not allowed")
            path = contained_path(source, relative)
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
                raise ValueError("Evidence hash mismatch: " + relative)
            projection = binding.get('publication_projection')
            if projection is not None and (
                    projection.get('path') != relative
                    or projection.get('published_sha256') != digest
                    or projection.get('published_bytes') != len(raw)
                    or not re.fullmatch(r'[0-9a-f]{64}', str(projection.get('original_sha256', '')))
                    or type(projection.get('replacements')) is not int
                    or projection['replacements'] < 1
                    or '脱敏' not in binding.get('scope', '')):
                raise ValueError('Invalid publication projection binding')
            if relative in files and files[relative] != raw:
                raise ValueError("Conflicting evidence binding")
            files[relative] = raw
        projection = row['first_answer'].get('publication_projection')
        if projection:
            log=json.loads(files[row['log']['path']])
            if projection['original_sha256'] != log.get('metrics', {}).get('content_sha256'):
                raise ValueError('Published answer does not bind the scored original')
        if row["cause"] not in model["cause_labels"]:
            raise ValueError("Unknown result cause")
    audited = [r for r in rows if r.get("success_audit")]
    states = Counter(r["success_audit"]["source_state"] for r in audited)
    if len(audited) != model["success_audit"]["audited_passes"] or dict(states) != model["success_audit"]["source_state_counts"]:
        raise ValueError("Success review summary differs from result evidence")
    return model, files


def check_diagnostic_report(output, profile=None):
    """Enforce the shared reader structure and evidence dialogs at release."""


    output = Path(output)
    model, files = load_diagnostic(output)
    if profile is not None:
        require_diagnostic_scope(model, json.loads(Path(profile).read_text()))
    manifest = json.loads((output / "report-manifest.json").read_text())
    check_build_version(output, manifest)
    if profile is not None:
        binding = json.loads(Path(profile).read_text()).get('diagnostic_input')
        if binding and manifest.get('input_sha256') != binding['sha256']:
            raise ValueError('Rendered diagnostic instance does not bind the planned input')
    if manifest["template"]["track"] != "webfetch" or manifest["template"]["renderer"] not in {"src/harness/web_fetch/report.py", "harness/web_fetch/report.py"}:
        raise ValueError("Diagnostic report bypassed its registered template")
    if manifest["model_sha256"] != hashlib.sha256((output / "report.json").read_bytes()).hexdigest():
        raise ValueError("Report model binding mismatch")
    if manifest.get("certified") is not False or manifest.get("provider_calls_added") != 0:
        raise ValueError("Diagnostic report cannot upgrade evidence or add calls")
    if manifest.get("artifact_hashes") != {p: hashlib.sha256(raw).hexdigest() for p, raw in files.items()}:
        raise ValueError("Report artifact inventory mismatch")
    core = ["index.html", "datasets/index.html", "methodology/index.html", "analysis/index.html",
            "review/index.html", "guide/index.html", "reproduce/index.html", "en/index.html"]
    for route in core:
        page = (output / route).read_text()
        check_presentation(page)
    home = BeautifulSoup((output / 'index.html').read_text(), 'html.parser')
    overall = diagnostic_call_summary(model['rows'], files)
    for route in ('index.html', 'guide/index.html'):
        page = BeautifulSoup((output / route).read_text(), 'html.parser')
        rendered = page.select('[data-summary-overall]')
        expected_values = [overall[k] for k in ('total','delivered','accepted','mean_ms','p50_ms','p95_ms')]
        if (len(rendered) != 1 or [c.get('data-sort') for c in rendered[0].select('td')][3:]
                != [str(-1 if v is None else v) for v in expected_values]):
            raise ValueError('Overall summary differs from the complete dataset calls')
        for key in ('delivered', 'accepted'):
            if overall[key] is not None and f'{100*overall[key]/overall["total"]:.1f}%' not in rendered[0].get_text():
                raise ValueError('Overall summary omits the complete dataset rate')
    summaries = home.select('[data-summary-group]')
    expected_summaries = diagnostic_cohort_summary(model, files)
    if len(summaries) != len(expected_summaries):
        raise ValueError('Missing cohort summary')
    for expected, rendered in zip(expected_summaries, summaries):
        values = [c.get('data-sort') for c in rendered.select('td')][3:]
        expected_values = [expected[k] for k in ('total','delivered','accepted','mean_ms','p50_ms','p95_ms')]
        if (rendered.get('data-summary-group') != expected['id']
                or values != [str(-1 if v is None else v) for v in expected_values]):
            raise ValueError('Summary differs from complete cohort calls')
    bars = home.select('#cohort-results .editorial-bar-row')
    if len(bars) != len(model['cohorts']):
        raise ValueError('Missing cohort result chart')
    for bar, group in zip(bars, model['cohorts']):
        passed, total = model['metrics'][group['id']]
        label, count, fill = bar.select_one('span'), bar.select_one('b'), bar.select_one('i')
        if (not label or label.get_text() != group['label'] or not count
                or count.get_text() != f'{passed} / {total}' or not fill):
            raise ValueError('Cohort chart differs from measured results')
        width = re.fullmatch(r'width:([0-9.]+)%', fill.get('style', ''))
        if not width or abs(float(width[1]) - 100 * passed / total) > 1e-9:
            raise ValueError('Cohort chart percentage mismatch')
    analysis = BeautifulSoup((output / 'analysis/index.html').read_text(), 'html.parser')
    expected_counts = diagnostic_failure_counts(model, files)
    actual_counts = analysis.select('[data-failure-type]')
    if Counter(c.get('data-failure-type') for c in actual_counts) != Counter({k: 1 for k in expected_counts}):
        raise ValueError('Missing or duplicate failure types')
    for count in actual_counts:
        kind = count.get('data-failure-type')
        if (kind not in expected_counts or count.get('data-count') != str(expected_counts[kind])
                or FAILURE_TYPES[kind] not in count.get_text()
                or not count.select_one('a[href="analysis/index.html#issue='+kind+'"]')):
            raise ValueError('Failure type count or drilldown differs from saved checks')
    guide = BeautifulSoup((output / 'guide/index.html').read_text(), 'html.parser')
    if not guide.select_one('#guide-results') or not guide.select_one('#guide-conclusions'):
        raise ValueError('Interpretation lacks outcome or conclusion')
    lead = guide.select_one('.hero .lead')
    if not lead or not lead.get_text().startswith(f"{model['metrics']['overall'][0]} / {len(model['rows'])} "):
        raise ValueError('Interpretation headline differs from measured outcome')
    if [r.get_text() for r in guide.select('[data-summary-group]')] != [r.get_text() for r in summaries]:
        raise ValueError('Interpretation summary differs from measured outcome')
    causes = Counter(r['cause'] for r in model['rows'] if not r['initial_pass'])
    cause_rows = guide.select('#guide-reviewed-causes tbody tr')
    if len(cause_rows) != len(causes):
        raise ValueError('Interpretation lacks reviewed failure distribution')
    for rendered, (cause, count) in zip(cause_rows, causes.most_common()):
        cells = rendered.select('td')
        expected_case=next(r for r in model['rows'] if not r['initial_pass'] and r['cause']==cause)
        if (cells[1].get_text() != model['cause_labels'][cause]
                or cells[2].get_text() != f'{count} / {sum(causes.values())}'
                or not cells[3].select_one('a[href="attempts/'+expected_case['id']+'.html"]')):
            raise ValueError('Interpretation reviewed counts differ from per-case review')
    examples = model.get('interpretation_case_ids')
    if examples is None:
        examples = [next(r['id'] for r in model['rows'] if not r['initial_pass'])] if expected_counts else []
        if any(r['initial_pass'] for r in model['rows']):
            examples.append(next(r['id'] for r in model['rows'] if r['initial_pass']))
    specimens = guide.select('[data-interpretation-case]')
    if [s['data-interpretation-case'] for s in specimens] != examples or not specimens:
        raise ValueError('Interpretation specimen inventory differs from model')
    for cid, specimen in zip(examples, specimens):
        row = next(r for r in model['rows'] if r['id'] == cid)
        if not specimen.select_one('a[href="attempts/'+cid+'.html"]'):
            raise ValueError('Interpretation specimen lacks full evidence')
        panels = specimen.select('[data-specimen-field]')
        if [p['data-specimen-field'] for p in panels] != ['reference','first_answer','source']:
            raise ValueError('Interpretation lacks reference/output/source comparison')
        for panel in panels:
            field = panel['data-specimen-field'];binding = row.get(field)
            if binding and panel.get('data-sha256') != binding['sha256']:
                raise ValueError('Interpretation specimen evidence binding differs')
            expected_preview = diagnostic_specimen_excerpt(row,field,files,model)
            if panel.select_one('pre').get_text() != expected_preview:
                raise ValueError('Interpretation specimen differs from bound evidence')
    for row in model["rows"]:
        route = "attempts/" + row["id"] + ".html"
        soup = check_presentation((output / route).read_text())
        if soup.select_one('[data-task-id]')["data-task-id"] != row["id"]:
            raise ValueError("Result identity mismatch")
        detail = soup.select_one('[data-report-detail]')
        if detail["data-report-detail"] != ('success' if row['initial_pass'] else 'failed'):
            raise ValueError("Rendered verdict differs from measured result")
        expected_findings = diagnostic_findings(row, files)
        rendered_findings = soup.select('#failed-requirements [data-finding-kind]')
        if len(expected_findings) != len(rendered_findings):
            raise ValueError('Missing or duplicate failed requirement explanations')
        for expected, rendered in zip(expected_findings, rendered_findings):
            values = [cell.get_text() for cell in rendered.select('td')][1:]
            if (rendered.get('data-finding-kind') != expected['kind']
                    or values != [expected['label'], expected['expected'], expected['actual']]):
                raise ValueError('Expected/actual explanation differs from saved first-call checks')
        for field in ("reference", "first_answer", "answer", "source", "log"):
            if not row.get(field):
                continue
            # The renderer shares the first-call panel only for an identical
            # binding. Supplemental outputs require their own checked panel.
            if field == "answer" and row[field] == row["first_answer"]:
                continue
            panel = soup.select_one(f'[data-evidence-kind="{field}"]')
            if not panel or not panel.select_one('pre'):
                raise ValueError("Missing result evidence panel: " + field)
            pre = panel.select_one('pre')
            binding = row[field]
            if pre.get('data-evidence-file'):
                if _local_target(output, output / route, soup, pre['data-evidence-file']) != contained_path(output, binding['path']):
                    raise ValueError("Lazy evidence target mismatch")
            elif pre.get_text() != files[binding['path']].decode('utf-8'):
                raise ValueError("Rendered evidence differs from saved output")
    for route in output.rglob("*.html"):
        soup = BeautifulSoup(route.read_text(), "html.parser")
        for node in soup.select("[href], [src], [data-evidence-file]"):
            if node.name == 'base':
                continue
            value = node.get("href") or node.get("src") or node.get('data-evidence-file')
            if value.startswith(("http:", "https:", "#", "data:")):
                continue
            target = _local_target(output, route, soup, value)
            if not target.is_file():
                raise ValueError("Broken report target: " + value)
    for path in output.rglob('*'):
        if path.is_symlink():
            raise ValueError('Public report must not contain symlinks')
        if path.is_file() and (risk := publication_risk(path.read_bytes())):
            raise ValueError('Public report privacy scan: ' + path.relative_to(output).as_posix() + ': ' + risk)
    return {"cases": len(model["rows"]), "core_routes": len(core), "status": "passed"}


def _local_target(output, route, soup, value):
    """Resolve a packaged link exactly as the browser resolves document.baseURI."""
    document = route.resolve().as_uri()
    base = soup.select_one('base[href]')
    resolved = urlsplit(urljoin(urljoin(document, base['href']) if base else document, value))
    if resolved.scheme != 'file' or resolved.netloc:
        raise ValueError('Expected local report target: ' + value)
    target = Path(unquote(resolved.path)).resolve()
    if not target.is_relative_to(output.resolve()):
        raise ValueError('Report target escapes package: ' + value)
    return target
