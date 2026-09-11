"""Merge the scoped homepage rerun, rescore reviewed rule variants, and bind the audit."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.metrics import summarize
from harness.web_fetch.models import execution_id
from harness.web_fetch.report import succeeded
from harness.web_fetch.result_review import require_result_review
from scripts.rescore_content import rescore


RERUN_CASES = {
    'global-homepage-001-google-com',
    'global-homepage-002-youtube-com',
}
RULE_REVISION_CASES = {
    'global-homepage-015-yahoo-com',
    'global-homepage-028-microsoft-com',
    'global-homepage-063-paypal-com',
    'global-homepage-067-deepseek-com',
    'global-homepage-080-cnn-com',
    'global-homepage-082-shein-com',
    'global-homepage-088-primevideo-com',
}
SCORER_CORRECTION_CASES = {
    'global-homepage-013-yahoo-co-jp',
    'global-homepage-034-news-yahoo-co-jp',
    'global-homepage-036-globo-com',
    'global-homepage-062-bbc-com',
}
PROVIDER_FAILURE_CASES = {
    'global-homepage-005-chatgpt-com', 'global-homepage-006-reddit-com',
    'global-homepage-012-gemini-google-com', 'global-homepage-016-amazon-com',
    'global-homepage-020-naver-com', 'global-homepage-027-dzen-ru',
    'global-homepage-029-twitch-tv', 'global-homepage-030-weather-com',
    'global-homepage-035-fandom-com', 'global-homepage-037-mail-ru',
    'global-homepage-044-spotify-com', 'global-homepage-047-booking-com',
    'global-homepage-052-roblox-com', 'global-homepage-060-espn-com',
    'global-homepage-065-ya-ru', 'global-homepage-068-indeed-com',
    'global-homepage-081-namu-wiki', 'global-homepage-085-douyin-com',
}
LOCALE_RERUN_CASES = {
    'global-homepage-001-google-com',
    'global-homepage-002-youtube-com',
    'global-homepage-088-primevideo-com',
}
LOCALE_RESCORE_CASES = {
    'global-homepage-005-chatgpt-com',
    'global-homepage-010-bing-com',
    'global-homepage-012-gemini-google-com',
    'global-homepage-015-yahoo-com',
    'global-homepage-032-canva-com',
    'global-homepage-067-deepseek-com',
    'global-homepage-082-shein-com',
}
CONTRACT_FAILURE_CASES = {
    'global-homepage-010-bing-com', 'global-homepage-018-baidu-com',
    'global-homepage-025-bilibili-com', 'global-homepage-039-duckduckgo-com',
    'global-homepage-045-nytimes-com', 'global-homepage-072-rakuten-co-jp',
    'global-homepage-080-cnn-com', 'global-homepage-084-qq-com',
    'global-homepage-088-primevideo-com', 'global-homepage-090-rutube-ru',
}


def sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def sha(path):
    return sha_bytes(path.read_bytes())


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def missing_contract_summary(record):
    metrics = record.get('metrics', {})
    missing = []
    for group in metrics.get('stable_anchor_results', []):
        if not group.get('pass'):
            missing.append(' / '.join(group.get('alternatives', [])))
    errors = list(metrics.get('stable_task_errors', []))
    minimum = metrics.get('stable_minimum_records', 0)
    complete = metrics.get('stable_unique_record_count', 0)
    if minimum and complete < minimum:
        errors.append(f'完整且唯一记录 {complete}/{minimum}')
    parts = []
    if missing:
        parts.append('未命中稳定文字组：' + '；'.join(missing))
    if errors:
        parts.append('结构诊断：' + '；'.join(map(str, errors)))
    return '；'.join(parts) or '保存输出未满足至少一项冻结契约，详见逐项机器判定。'


def classification(case_id):
    if case_id in SCORER_CORRECTION_CASES:
        return 'scorer_normalization_or_structure_error'
    if case_id in PROVIDER_FAILURE_CASES:
        return 'provider_access_or_extraction_failure'
    if case_id in CONTRACT_FAILURE_CASES:
        return 'target_retrieved_but_frozen_contract_not_met'
    if case_id in RULE_REVISION_CASES:
        return 'reference_locale_language_or_page_variant_limitation'
    return 'verified_contract_pass'


def review_note(record, category):
    if category == 'provider_access_or_extraction_failure':
        status = record.get('status_code') or record.get('provider_metadata', {}).get('upstream_status')
        final = record.get('final_url') or '—'
        return f'未取得可满足首页契约的内容；请求状态 {status or "—"}，最终 URL {final}。'
    if category == 'target_retrieved_but_frozen_contract_not_met':
        return missing_contract_summary(record)
    if category == 'scorer_normalization_or_structure_error':
        return ('原评分器把单行 Markdown 中首个链接之后的内容错误地排除在记录作用域外；'
                '通用解析修正后，保存输出满足同一冻结契约，未新增供应商调用。')
    if category == 'reference_locale_language_or_page_variant_limitation':
        return ('独立页面核对确认原规则的地区、语言或当前页面文案不准确；'
                '仅按独立页面证据修正规则文字后，原始保存输出通过离线复评分，未新增供应商调用。')
    if record['case_id'] in RERUN_CASES:
        return '加强首页稳定文字契约后仅重跑本案例；新调用命中全部登记文字组，旧调用保留为被替代证据。'
    return '请求取得目标首页，保存输出满足冻结的页面身份、稳定文字及适用结构要求。'


def audit(source: Path, rerun: Path, canonical: Path):
    source, rerun, canonical = source.resolve(), rerun.resolve(), canonical.resolve()
    history = source/'history'/'pre-homepage-audit-2026-09-11'
    if history.exists():
        raise ValueError('Audit history already exists; refusing to overwrite it')
    old_dataset = (source/'dataset.json').read_bytes()
    old_results = (source/'results.jsonl').read_bytes()
    old_records = [json.loads(line) for line in old_results.splitlines()]
    rerun_records = [json.loads(line) for line in (rerun/'results.jsonl').read_text().splitlines()]
    if len(old_records) != 53 or {row['case_id'] for row in rerun_records} != RERUN_CASES:
        raise ValueError('Unexpected source or rerun case inventory')

    history.mkdir(parents=True)
    for name in ('dataset.json','results.jsonl','manifest.json','result-review.json','summary.json'):
        if (source/name).is_file():
            shutil.copy2(source/name, history/name)
    if (source/'report').is_dir():
        shutil.copytree(source/'report', history/'report')
    for row in old_records:
        if row['case_id'] in RERUN_CASES:
            target = history/row['artifact_path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source/row['artifact_path'], target)

    canonical_raw = canonical.read_bytes()
    (source/'dataset.json').write_bytes(canonical_raw)
    shutil.copytree(canonical.parent/'sources', source/'sources', dirs_exist_ok=True)

    current = {row['case_id']:row for row in old_records}
    rerun_execution_path = rerun/'execution.json'
    if rerun_execution_path.is_file():
        rerun_batch = json.loads(rerun_execution_path.read_text())['batches'][0]
    else:
        rerun_execution_path = rerun/'manifest.json'
        rerun_batch = json.loads(rerun_execution_path.read_text())
    renamed_ids = []
    for row in rerun_records:
        revised = deepcopy(row)
        revised['run'] = 2
        revised['attempt_id'] = revised['attempt_id'].replace('__r001','__r002')
        revised['artifact_path'] = revised['artifact_path'].replace('run-001.txt','run-002.txt')
        revised['run_id'] = execution_id(revised)
        target = source/revised['artifact_path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rerun/row['artifact_path'], target)
        current[row['case_id']] = revised
        renamed_ids.append(revised['attempt_id'])
    records = [current[row['case_id']] for row in old_records]
    (source/'results.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=True)+'\n' for row in records))

    original_batch = json.loads((source/'manifest.json').read_text())
    original_ids = [row['attempt_id'] for row in records if row['case_id'] not in RERUN_CASES]
    projection = deepcopy(original_batch)
    projection['attempt_ids'] = original_ids
    projection['attempt_count'] = len(original_ids)
    projection['dataset'] = {**projection['dataset'], 'case_count':len(original_ids)}
    projection['projection'] = {
        'kind':'superseded-task-contract-cases-removed',
        'source_manifest_sha256':sha(source/'manifest.json'),
        'superseded_case_ids':sorted(RERUN_CASES),
    }
    rerun_batch['attempt_ids'] = renamed_ids
    rerun_batch['attempt_count'] = len(renamed_ids)
    rerun_batch['projection'] = {
        'kind':'strengthened-homepage-contract-targeted-rerun',
        'source_execution_sha256':sha(rerun_execution_path),
    }
    write_json(source/'execution.json', {'batches':[projection,rerun_batch]})

    rescore_input = (source/'results.jsonl').read_bytes()
    decisions = rescore(source, case_ids=RULE_REVISION_CASES, allow_rule_revision=True)
    final_results = (source/'results.jsonl').read_bytes()
    changed_fields = ['acceptance_contract','task_prompt','reference_answer','source_benchmark']
    scoring_receipt = {
        'kind':'audited_scoring_rule_revision',
        'scored_at':datetime.now(timezone.utc).isoformat(),
        'input_dataset_sha256':sha(source/'dataset.json'),
        'input_results_sha256':sha_bytes(rescore_input),
        'output_dataset_sha256':sha(source/'dataset.json'),
        'output_results_sha256':sha_bytes(final_results),
        'changed_cases':[{'case_id':case_id,'fields':(
            ['acceptance_contract'] if case_id in {'global-homepage-080-cnn-com'} else changed_fields)}
            for case_id in sorted(RULE_REVISION_CASES)],
        'task_decision_changes':decisions,
        'provider_calls':0,
        'independent_rule_evidence':'research/global-homepage-rule-variant-review-2026-09-11.json',
        'immutable_call_fields':'output bytes/hash, request status, timestamps and elapsed time retained',
    }
    write_json(source/'scoring-revisions.json', [scoring_receipt])

    rerun_receipt = {
        'schema_version':1,
        'kind':'audited_targeted_task_contract_rerun',
        'created_at':datetime.now(timezone.utc).isoformat(),
        'input_dataset_sha256':sha_bytes(old_dataset),
        'input_results_sha256':sha_bytes(old_results),
        'output_dataset_sha256':sha(source/'dataset.json'),
        'output_results_sha256':sha(source/'results.jsonl'),
        'changed_contract_cases':sorted(RERUN_CASES | RULE_REVISION_CASES),
        'rerun_case_ids':sorted(RERUN_CASES),
        'offline_rescore_case_ids':sorted(RULE_REVISION_CASES),
        'provider_calls':2,
        'selection':'Only cases whose mandatory stable-text contract was materially strengthened were rerun; no best-of selection.',
        'superseded_evidence':'history/pre-homepage-audit-2026-09-11',
        'targeted_rerun_evidence':str(rerun),
    }
    write_json(source/'task-rerun.json', rerun_receipt)

    dataset = json.loads((source/'dataset.json').read_text())
    records = [json.loads(line) for line in (source/'results.jsonl').read_text().splitlines()]
    cases = {case['id']:case for case in dataset['cases']}
    attempts = []
    for record in records:
        category = classification(record['case_id'])
        verdict = 'pass' if succeeded(record,cases[record['case_id']]) else 'fail'
        note = review_note(record, category)
        metrics = record.get('metrics', {})
        attempts.append({
            'attempt_id':record['attempt_id'],
            'machine_verdict':verdict,
            'agent_verdict':verdict,
            'final_verdict':verdict,
            'classification':category,
            'review_note':note,
            'request_evidence':{
                'ok':record.get('ok'), 'status_code':record.get('status_code'),
                'upstream_status':record.get('provider_metadata',{}).get('upstream_status'),
                'final_url':record.get('final_url'), 'error_type':record.get('error_type'),
            },
            'output_evidence':{
                'artifact_path':record.get('artifact_path'),
                'content_sha256':metrics.get('content_sha256'),
                'content_chars':metrics.get('content_chars'),
            },
            'contract_evidence':{
                'schema':metrics.get('stable_task_schema'),
                'acceptance_contract_sha256':metrics.get('acceptance_contract_sha256'),
                'stable_anchor_hits':metrics.get('stable_anchor_hits'),
                'stable_anchor_total':metrics.get('stable_anchor_total'),
                'stable_unique_record_count':metrics.get('stable_unique_record_count'),
                'stable_minimum_records':metrics.get('stable_minimum_records'),
                'stable_task_errors':metrics.get('stable_task_errors',[]),
            },
            'independent_rule_evidence':(
                'research/global-homepage-rule-variant-review-2026-09-11.json'
                if record['case_id'] in RERUN_CASES | RULE_REVISION_CASES
                else 'research/global-homepage-rule-evidence-final-v2-2026-09-11.json'),
        })
    review = {
        'schema_version':1,
        'bindings':{'dataset_sha256':sha(source/'dataset.json'),
                    'results_sha256':sha(source/'results.jsonl')},
        'reviewer':{'agent':'Codex post-run evidence audit'},
        'review_protocol':{
            'machine_judgments_withheld':False,
            'scope':'all',
            'post_run_independent_audit':True,
            'provider_output_not_used_as_rule_source':True,
            'method':'逐项核对请求状态、完整保存输出、冻结契约、独立页面证据与评分诊断；先归因，再决定离线修订或定向重跑。',
            'classification_policy':'失败分为供应商访问/提取、已取回但未满足契约、评分器错误、参考变体限制和证据不足；通过项另标记为已核实满足契约。',
        },
        'attempt_count':len(attempts),
        'review_rounds':[
            {
                'date':'2026-09-11',
                'title':'原始 53 项结果与通用评分器核对',
                'scope':'全部首页调用、保存输出和原冻结契约。',
                'finding':'四个列表型首页因单行 Markdown 链接作用域错误被误判。',
                'fix':'增加回归测试并统一修正解析；对原输出离线复评分，不新增供应商调用。',
                'pending':'进入页面变体与首页文字充分性复核。',
            },
            {
                'date':'2026-09-11',
                'title':'地区、语言与当前页面变体独立复核',
                'scope':'Google、YouTube、Yahoo、Microsoft、PayPal、DeepSeek、CNN、SHEIN 和 Prime Video。',
                'finding':'部分规则遗漏等价地区语言，Google 与 YouTube 的原首页身份契约又过弱。',
                'fix':'仅依据独立页面证据补充等价写法；地区变体离线复评分，Google 与 YouTube 转入定向重跑。',
                'pending':'核验两项新调用并重新审计完整 53 项。',
            },
            {
                'date':'2026-09-11',
                'title':'加强契约定向重跑与最终逐项审计',
                'scope':'Google、YouTube 各一次新调用，以及最终 53 条权威逻辑结果。',
                'finding':'两项新调用均命中加强后的全部稳定文字；其余失败可明确区分访问/提取失败和已取回但契约未满足。',
                'fix':'保留旧调用并以 r002 替代两项当前逻辑结果；绑定 53 项分类、证据和最终判定。',
                'pending':'无；证据不足项为 0。',
            },
        ],
        'attempts':attempts,
        'disagreements':0,
        'unresolved':0,
    }
    write_json(source/'result-review.json', review)
    require_result_review(source,dataset,records)
    write_json(source/'summary.json', summarize(records))
    return {
        'attempts':len(records),
        'passes':sum(succeeded(row,cases[row['case_id']]) for row in records),
        'provider_calls_added':2,
        'offline_decision_changes':decisions,
        'classifications':{name:sum(row['classification']==name for row in attempts)
                           for name in sorted({row['classification'] for row in attempts})},
    }


def locale_followup(source: Path, rerun: Path, canonical: Path):
    """Apply the independently reviewed single-language homepage correction."""
    source, rerun, canonical = source.resolve(), rerun.resolve(), canonical.resolve()
    history = source/'history'/'pre-homepage-fixed-language-correction-2026-09-11'
    resuming = history.exists()
    old_dataset = ((history if resuming else source)/'dataset.json').read_bytes()
    old_results = ((history if resuming else source)/'results.jsonl').read_bytes()
    old_records = [json.loads(line) for line in old_results.splitlines()]
    rerun_records = [json.loads(line) for line in (rerun/'results.jsonl').read_text().splitlines()]
    if len(old_records) != 53 or {row['case_id'] for row in rerun_records} != LOCALE_RERUN_CASES:
        raise ValueError('Unexpected source or locale-rerun case inventory')

    if not resuming:
        history.mkdir(parents=True)
        for name in ('dataset.json','results.jsonl','execution.json','manifest.json',
                     'result-review.json','summary.json','task-rerun.json'):
            if (source/name).is_file():
                shutil.copy2(source/name, history/name)
        if (source/'report').is_dir():
            shutil.copytree(source/'report', history/'report')
        for row in old_records:
            if row['case_id'] in LOCALE_RERUN_CASES:
                target = history/row['artifact_path']
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source/row['artifact_path'], target)

    canonical_raw = canonical.read_bytes()
    (source/'dataset.json').write_bytes(canonical_raw)
    shutil.copytree(canonical.parent/'sources', source/'sources', dirs_exist_ok=True)

    current = {row['case_id']:row for row in old_records}
    rerun_execution_path = rerun/'execution.json'
    if rerun_execution_path.is_file():
        rerun_batch = json.loads(rerun_execution_path.read_text())['batches'][0]
    else:
        rerun_execution_path = rerun/'manifest.json'
        rerun_batch = json.loads(rerun_execution_path.read_text())
    renamed_ids = []
    for row in rerun_records:
        revised = deepcopy(row)
        artifact_dir = source/Path(revised['artifact_path']).parent
        existing_runs = [
            int(match.group(1)) for path in artifact_dir.glob('run-*.txt')
            if (match := re.fullmatch(r'run-([0-9]+)\.txt', path.name))
        ]
        run_number = max(existing_runs, default=0) + 1
        revised['run'] = run_number
        revised['attempt_id'] = revised['attempt_id'].replace(
            '__r001', f'__r{run_number:03d}'
        )
        revised['artifact_path'] = str(
            Path(revised['artifact_path']).with_name(f'run-{run_number:03d}.txt')
        )
        revised['run_id'] = execution_id(revised)
        target = source/revised['artifact_path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rerun/row['artifact_path'], target)
        current[row['case_id']] = revised
        renamed_ids.append(revised['attempt_id'])
    records = [current[row['case_id']] for row in old_records]
    (source/'results.jsonl').write_text(
        ''.join(json.dumps(row, ensure_ascii=True)+'\n' for row in records)
    )

    rescore(
        source,
        case_ids=LOCALE_RESCORE_CASES,
        allow_rule_revision=True,
    )
    records = [json.loads(line) for line in (source/'results.jsonl').read_text().splitlines()]

    execution_path = source/'execution.json'
    execution = json.loads(execution_path.read_text())
    rerun_batch['attempt_ids'] = renamed_ids
    rerun_batch['attempt_count'] = len(renamed_ids)
    rerun_batch['projection'] = {
        'kind':'independent-homepage-locale-contract-targeted-rerun',
        'source_execution_sha256':sha(rerun_execution_path),
        'superseded_case_ids':sorted(LOCALE_RERUN_CASES),
    }
    execution['batches'].append(rerun_batch)
    write_json(execution_path, execution)

    prior_receipt = history/'task-rerun.json'
    receipt = {
        'schema_version':1,
        'kind':'audited_homepage_locale_contract_rerun',
        'created_at':datetime.now(timezone.utc).isoformat(),
        'input_dataset_sha256':sha_bytes(old_dataset),
        'input_results_sha256':sha_bytes(old_results),
        'output_dataset_sha256':sha(source/'dataset.json'),
        'output_results_sha256':sha(source/'results.jsonl'),
        'changed_contract_cases':sorted(LOCALE_RERUN_CASES | LOCALE_RESCORE_CASES),
        'rerun_case_ids':sorted(LOCALE_RERUN_CASES),
        'offline_rescore_case_ids':sorted(LOCALE_RESCORE_CASES),
        'provider_calls':len(LOCALE_RERUN_CASES),
        'selection':'Only the three cases whose requested URL changed were called once. Seven unchanged URLs were rescored from their original formal output under independently observed single-language rules; no best-of selection.',
        'independent_rule_evidence':'research/global-homepage-fixed-locale-observations-2026-09-11.json',
        'provider_output_used_as_rule_source':False,
        'superseded_evidence':str(history.relative_to(source)),
        'targeted_rerun_evidence':str(rerun),
    }
    if prior_receipt.is_file():
        receipt['previous_rerun_receipt'] = str(prior_receipt.relative_to(source))
        receipt['previous_rerun_receipt_sha256'] = sha(prior_receipt)
    write_json(source/'task-rerun.json', receipt)

    dataset = json.loads((source/'dataset.json').read_text())
    cases = {case['id']:case for case in dataset['cases']}
    previous_review = json.loads((history/'result-review.json').read_text())
    previous_attempts = {item['attempt_id'].split('__r')[0]:item
                         for item in previous_review['attempts']}
    attempts = []
    for record in records:
        base_attempt = record['attempt_id'].split('__r')[0]
        if record['case_id'] not in LOCALE_RERUN_CASES | LOCALE_RESCORE_CASES:
            attempts.append(previous_attempts[base_attempt])
            continue
        passed = succeeded(record,cases[record['case_id']])
        if not record.get('ok'):
            category = 'provider_access_or_extraction_failure'
            note = (f"本次定向调用未取得目标首页；请求状态 {record.get('status_code') or '—'}，"
                    f"上游状态 {record.get('provider_metadata',{}).get('upstream_status') or '—'}。")
        elif passed:
            category = 'verified_contract_pass'
            note = '本次定向调用满足独立首页证据修订后的全部稳定文字组。'
        else:
            category = 'target_retrieved_but_frozen_contract_not_met'
            note = missing_contract_summary(record)
        metrics = record.get('metrics', {})
        verdict = 'pass' if passed else 'fail'
        attempts.append({
            'attempt_id':record['attempt_id'],
            'machine_verdict':verdict,
            'agent_verdict':verdict,
            'final_verdict':verdict,
            'classification':category,
            'review_note':note,
            'request_evidence':{
                'ok':record.get('ok'), 'status_code':record.get('status_code'),
                'upstream_status':record.get('provider_metadata',{}).get('upstream_status'),
                'final_url':record.get('final_url'), 'error_type':record.get('error_type'),
            },
            'output_evidence':{
                'artifact_path':record.get('artifact_path'),
                'content_sha256':metrics.get('content_sha256'),
                'content_chars':metrics.get('content_chars'),
            },
            'contract_evidence':{
                'schema':metrics.get('stable_task_schema'),
                'acceptance_contract_sha256':metrics.get('acceptance_contract_sha256'),
                'stable_anchor_hits':metrics.get('stable_anchor_hits'),
                'stable_anchor_total':metrics.get('stable_anchor_total'),
                'stable_unique_record_count':metrics.get('stable_unique_record_count'),
                'stable_minimum_records':metrics.get('stable_minimum_records'),
                'stable_task_errors':metrics.get('stable_task_errors',[]),
            },
            'independent_rule_evidence':'research/global-homepage-fixed-locale-observations-2026-09-11.json',
        })
    previous_review['bindings'] = {
        'dataset_sha256':sha(source/'dataset.json'),
        'results_sha256':sha(source/'results.jsonl'),
    }
    previous_review['attempts'] = attempts
    previous_review['attempt_count'] = len(attempts)
    previous_review['review_rounds'].append({
        'date':'2026-09-11',
        'title':'十二个跨语言风险首页的单一语言规则修订',
        'scope':'Google、YouTube、Prime Video 固定语言首页；其余七个非日语风险案例保留原 URL 并固定独立观察到的语言；两个日本站保持原样。',
        'finding':'跨语言候选词会允许一次输出混合命中多种语言，不适合作为同一页面变体的验收方式。',
        'fix':'规则只取独立首页证据。仅三个 URL 变化案例新增一次正式调用；七个 URL 不变案例复用原正式输出离线复评分。',
        'pending':'无规则证据缺口；供应商失败与未命中项按修订后契约如实保留。',
    })
    previous_review['disagreements'] = 0
    previous_review['unresolved'] = 0
    write_json(source/'result-review.json', previous_review)
    require_result_review(source,dataset,records)
    write_json(source/'summary.json', summarize(records))
    return {
        'attempts':len(records),
        'passes':sum(succeeded(row,cases[row['case_id']]) for row in records),
        'provider_calls_added':len(LOCALE_RERUN_CASES),
        'rerun_case_ids':sorted(LOCALE_RERUN_CASES),
        'offline_rescore_case_ids':sorted(LOCALE_RESCORE_CASES),
        'classifications':{name:sum(row['classification']==name for row in attempts)
                           for name in sorted({row['classification'] for row in attempts})},
    }


def reconcile_repeat_observations(source: Path):
    """Move repeat observations for superseded URLs out of the current report view."""
    source = source.resolve()
    path = source/'verification-observations.json'
    history = source/'history'/'pre-homepage-fixed-language-correction-2026-09-11'
    history_copy = history/'verification-observations.json'
    payload = json.loads(path.read_text())
    superseded = [
        item for item in payload.get('observations', [])
        if item.get('case_id') in LOCALE_RERUN_CASES
    ]
    retained = [
        item for item in payload.get('observations', [])
        if item.get('case_id') not in LOCALE_RERUN_CASES
    ]
    if superseded and not history_copy.exists():
        shutil.copy2(path, history_copy)
    payload['observations'] = retained
    payload['superseded_by_task_input_revision'] = [
        {
            'case_id': item['case_id'],
            'prior_url': item['url'],
            'history_file': str(history_copy.relative_to(source)),
            'reason': 'The current task uses a different fixed-language URL; the old same-URL formal/verification pair remains historical evidence.',
        }
        for item in superseded
    ]
    write_json(path, payload)
    return {'retained': len(retained), 'superseded': len(superseded)}


def preserve_first_run(source: Path, canonical: Path):
    """Restore first formal calls and retain later calls as verification only."""
    source, canonical = source.resolve(), canonical.resolve()
    initial = source/'history'/'pre-homepage-audit-2026-09-11'
    pre_locale = source/'history'/'pre-homepage-locale-correction-2026-09-11'
    correction_history = source/'history'/'pre-first-run-preservation-2026-09-11'
    if correction_history.exists():
        raise ValueError('First-run-preservation history already exists; refusing to overwrite it')
    if not initial.is_dir() or not pre_locale.is_dir():
        raise ValueError('Required first-run and follow-up histories are missing')

    correction_history.mkdir(parents=True)
    for name in ('dataset.json','results.jsonl','execution.json','manifest.json',
                 'result-review.json','summary.json','task-rerun.json'):
        if (source/name).is_file():
            shutil.copy2(source/name, correction_history/name)
    if (source/'report').is_dir():
        shutil.copytree(source/'report', correction_history/'report')

    current_records = [json.loads(line) for line in (source/'results.jsonl').read_text().splitlines()]
    initial_records = {row['case_id']:row for row in [
        json.loads(line) for line in (initial/'results.jsonl').read_text().splitlines()]}
    pre_locale_records = {row['case_id']:row for row in [
        json.loads(line) for line in (pre_locale/'results.jsonl').read_text().splitlines()]}
    first_run_cases = RERUN_CASES | LOCALE_RERUN_CASES
    formal = {row['case_id']:row for row in current_records}
    for case_id in RERUN_CASES:
        formal[case_id] = deepcopy(initial_records[case_id])
    for case_id in LOCALE_RERUN_CASES:
        formal[case_id] = deepcopy(pre_locale_records[case_id])
    formal_records = [formal[row['case_id']] for row in current_records]

    verification_root = source/'verification-artifacts'
    verification_rows = []
    current_by_case = {row['case_id']:row for row in current_records}
    pre_locale_by_case = pre_locale_records
    for case_id in sorted(first_run_cases):
        observed = current_by_case[case_id] if case_id in LOCALE_RERUN_CASES else pre_locale_by_case[case_id]
        formal_row = formal[case_id]
        source_artifact = source/observed['artifact_path']
        relative_artifact = Path('verification-artifacts')/observed['provider']/case_id/Path(observed['artifact_path']).name
        target_artifact = source/relative_artifact
        target_artifact.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_artifact, target_artifact)
        raw_output_differs = (
            formal_row.get('metrics',{}).get('content_sha256')
            != observed.get('metrics',{}).get('content_sha256')
        )
        equivalent_failure = (
            not formal_row.get('ok') and not observed.get('ok')
            and formal_row.get('error_type') == observed.get('error_type')
            and formal_row.get('provider_metadata',{}).get('upstream_status')
                == observed.get('provider_metadata',{}).get('upstream_status')
        )
        meaningful_content_differs = raw_output_differs and not equivalent_failure
        verification_text = target_artifact.read_text(encoding='utf-8', errors='replace')
        css_rule_count = len(re.findall(
            r'[.#][A-Za-z_][^{}\n]{0,120}\{[^{}]{0,500}:[^{}]{0,500}\}',
            verification_text,
        ))
        diagnostics = []
        if css_rule_count >= 3:
            diagnostics.append({'code':'suspected_stylesheet_leak',
                                'observed_rule_count':css_rule_count,
                                'affects_formal_verdict':False})
        if equivalent_failure:
            finding = ('两次均未取得页面正文，并返回相同类型的上游拒绝；原始错误响应校验值不同，'
                       '不能据此认定页面内容或提取结果发生变化。')
        elif meaningful_content_differs:
            finding = '重复观察结果不同，存在提取稳定性或页面变体问题。'
        else:
            finding = '重复观察的输出校验值相同；后续调用仍仅作为核验证据。'
        if diagnostics:
            finding += (' 后续核验输出还包含大量疑似 CSS 规则，作为提取纯净度诊断记录；'
                        '不改变首次正式调用的判定。')
        verification_rows.append({
            'case_id':case_id,
            'url':next(case['url'] for case in json.loads(canonical.read_text())['cases']
                       if case['id']==case_id),
            'formal_attempt_id':formal_row['attempt_id'],
            'formal_content_sha256':formal_row.get('metrics',{}).get('content_sha256'),
            'formal_elapsed_ms':formal_row.get('elapsed_ms'),
            'formal_verdict':'pass' if formal_row.get('metrics',{}).get('stable_task_pass') else 'fail',
            'verification_attempt_id':observed['attempt_id'],
            'verification_content_sha256':observed.get('metrics',{}).get('content_sha256'),
            'verification_elapsed_ms':observed.get('elapsed_ms'),
            'verification_verdict':'pass' if observed.get('metrics',{}).get('stable_task_pass') else 'fail',
            'verification_artifact_path':relative_artifact.as_posix(),
            'raw_output_differs':raw_output_differs,
            'meaningful_content_differs':meaningful_content_differs,
            'equivalent_failure':equivalent_failure,
            'diagnostics':diagnostics,
            'finding':finding,
            'authoritative_result':'formal_first_call',
        })
    verification = {
        'schema_version':1,
        'kind':'non_authoritative_repeat_observations',
        'created_at':datetime.now(timezone.utc).isoformat(),
        'policy':'同一 URL 的后续人工核验调用不替换第一次正式测试结果，不从多次返回中择优。',
        'provider_output_used_as_rule_source':False,
        'formal_results_file':'results.jsonl',
        'observations':verification_rows,
    }
    write_json(source/'verification-observations.json', verification)

    (source/'dataset.json').write_bytes(canonical.read_bytes())
    shutil.copytree(canonical.parent/'sources', source/'sources', dirs_exist_ok=True)
    (source/'results.jsonl').write_text(
        ''.join(json.dumps(row, ensure_ascii=True)+'\n' for row in formal_records)
    )

    original_batch = json.loads((source/'manifest.json').read_text())
    original_batch['projection'] = {
        'kind':'formal-first-run-authority',
        'policy':'Later same-URL verification calls do not replace first formal results.',
    }
    old_execution = json.loads((correction_history/'execution.json').read_text())
    verification_batches = old_execution['batches'][1:]
    for batch in verification_batches:
        batch['projection'] = {
            **batch.get('projection',{}),
            'kind':'non-authoritative-repeat-observation',
            'affects_formal_results':False,
            'selection_policy':'No best-of selection; first formal call remains authoritative.',
        }
    write_json(source/'execution.json', {'batches':[original_batch,*verification_batches]})

    decisions = rescore(source, case_ids=first_run_cases, allow_rule_revision=True)
    rescored_records = [json.loads(line) for line in (source/'results.jsonl').read_text().splitlines()]
    if decisions:
        raise ValueError(f'Current independent rules changed first-run binary verdicts: {decisions}')

    prior_revisions_path = source/'scoring-revisions.json'
    revisions = json.loads(prior_revisions_path.read_text()) if prior_revisions_path.is_file() else []
    revisions.append({
        'kind':'first_formal_call_rescored_against_independently_revised_homepage_contracts',
        'scored_at':datetime.now(timezone.utc).isoformat(),
        'input_dataset_sha256':sha(correction_history/'dataset.json'),
        'input_results_sha256':sha(correction_history/'results.jsonl'),
        'output_dataset_sha256':sha(source/'dataset.json'),
        'output_results_sha256':sha(source/'results.jsonl'),
        'changed_cases':sorted(first_run_cases),
        'task_decision_changes':decisions,
        'provider_calls':0,
        'provider_output_used_as_rule_source':False,
        'immutable_call_fields':'first formal output bytes/hash, request status, timestamps and elapsed time retained',
        'repeat_observations':'verification-observations.json',
    })
    write_json(prior_revisions_path, revisions)

    dataset = json.loads((source/'dataset.json').read_text())
    cases = {case['id']:case for case in dataset['cases']}
    prior_review = json.loads((correction_history/'result-review.json').read_text())
    prior_attempts = {item['attempt_id'].split('__r')[0]:item for item in prior_review['attempts']}
    attempts = []
    for record in rescored_records:
        base = record['attempt_id'].split('__r')[0]
        old = deepcopy(prior_attempts[base])
        if record['case_id'] in first_run_cases:
            passed = succeeded(record,cases[record['case_id']])
            if not record.get('ok'):
                category = 'provider_access_or_extraction_failure'
            elif passed:
                category = 'verified_contract_pass'
            else:
                category = 'target_retrieved_but_frozen_contract_not_met'
            metrics = record.get('metrics',{})
            old.update({
                'attempt_id':record['attempt_id'],
                'machine_verdict':'pass' if passed else 'fail',
                'agent_verdict':'pass' if passed else 'fail',
                'final_verdict':'pass' if passed else 'fail',
                'classification':category,
                'review_note':(
                    '第一次正式调用保持权威；后续同 URL 结果仅记录为重复观察，不参与择优或替换。'
                    + ('' if passed else ' '+missing_contract_summary(record))
                ),
                'request_evidence':{
                    'ok':record.get('ok'),'status_code':record.get('status_code'),
                    'upstream_status':record.get('provider_metadata',{}).get('upstream_status'),
                    'final_url':record.get('final_url'),'error_type':record.get('error_type'),
                },
                'output_evidence':{
                    'artifact_path':record.get('artifact_path'),
                    'content_sha256':metrics.get('content_sha256'),
                    'content_chars':metrics.get('content_chars'),
                },
                'contract_evidence':{
                    'schema':metrics.get('stable_task_schema'),
                    'acceptance_contract_sha256':metrics.get('acceptance_contract_sha256'),
                    'stable_anchor_hits':metrics.get('stable_anchor_hits'),
                    'stable_anchor_total':metrics.get('stable_anchor_total'),
                    'stable_unique_record_count':metrics.get('stable_unique_record_count'),
                    'stable_minimum_records':metrics.get('stable_minimum_records'),
                    'stable_task_errors':metrics.get('stable_task_errors',[]),
                },
                'independent_rule_evidence':'research/global-homepage-rule-variant-review-2026-09-11.json',
            })
        attempts.append(old)
    prior_review['bindings']={'dataset_sha256':sha(source/'dataset.json'),
                              'results_sha256':sha(source/'results.jsonl')}
    prior_review['attempts']=attempts
    prior_review['attempt_count']=len(attempts)
    prior_review['review_rounds'].append({
        'date':'2026-09-11',
        'title':'正式首轮结果与后续重复观察分离',
        'scope':'Google、YouTube、ChatGPT、Bing、Gemini、Canva 的同 URL 后续调用。',
        'finding':'部分重复观察返回不同正文；差异可能来自提取稳定性或页面变体，不能从多次结果中挑选较好一次。',
        'fix':'恢复第一次正式调用的正文、耗时和权威地位；后续调用及哈希转入独立重复观察记录。',
        'pending':'无；未来正式新一轮测试应使用修订后的当前测试集重新开始，而不是混入本轮核验调用。',
    })
    prior_review['disagreements']=0
    prior_review['unresolved']=0
    write_json(source/'result-review.json',prior_review)
    require_result_review(source,dataset,rescored_records)
    write_json(source/'summary.json',summarize(rescored_records))

    if (source/'task-rerun.json').is_file():
        shutil.move(source/'task-rerun.json', correction_history/'superseded-task-rerun.json')
    return {
        'attempts':len(rescored_records),
        'passes':sum(succeeded(row,cases[row['case_id']]) for row in rescored_records),
        'authoritative_repeat_policy':'formal_first_call',
        'repeat_observations':len(verification_rows),
        'different_outputs':sum(row['raw_output_differs'] for row in verification_rows),
        'meaningfully_different_outputs':sum(row['meaningful_content_differs'] for row in verification_rows),
        'decision_changes':decisions,
    }


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--rerun',type=Path,required=True)
    parser.add_argument('--canonical',type=Path,default=ROOT/'evaluation/global-homepages-dataset.json')
    parser.add_argument('--locale-followup',action='store_true')
    parser.add_argument('--preserve-first-run',action='store_true')
    args=parser.parse_args()
    if args.locale_followup and args.preserve_first_run:
        parser.error('Choose only one follow-up action')
    if args.preserve_first_run:
        result = preserve_first_run(args.source,args.canonical)
    else:
        action = locale_followup if args.locale_followup else audit
        result = action(args.source,args.rerun,args.canonical)
    print(json.dumps(result,ensure_ascii=False,indent=2))
