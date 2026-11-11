"""Validate a diagnostic report model without upgrading it to certification."""

import hashlib
import json
import math
import re
import statistics
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.paths import contained_path, safe_identifier, validate_http_url

from harness.record_io import json_bytes, load_jsonl, read_json
from harness.reporting.release_check import checked_page, open_report, read_page
from harness.reporting.versions import check_build_version
from harness.web_fetch.artifacts import (
    verify_record_private_artifacts,
    verify_record_public_call_logs,
)
from harness.web_fetch.publication_privacy import project_file, publication_risk
from harness.web_fetch.reference_lifecycle import require_verified_reference_checks
from harness.web_fetch.report_i18n import t


def diagnostic_profile_binding(profile):
    snapshot={'variant':profile.get('variant'),'cohort_plan':[
        {key:group[key] for key in ('id','case_count','case_ids_sha256')}
        for group in profile.get('cohort_plan',[])]}
    for name,keys in [('implementation',('reported_version','binary_sha256')),('diagnostic_input',('sha256',))]:
        if profile.get(name):snapshot[name]={key:profile[name][key] for key in keys}
    return {'profile':snapshot,'sha256':hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()}


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
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    if spec.get('schema') != 'webfetch_diagnostic_inputs/1':
        raise ValueError('Unsupported diagnostic input manifest')
    if any(output.iterdir()):
        raise ValueError('Diagnostic preparation requires an empty staging directory')
    model = dict(spec['report']);rows=[];cohorts=[];provenance=[]
    def save(path, raw, scope):
        public, projection = project_file(path, raw)
        target=contained_path(output,path);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(public)
        binding=dict(path=path,sha256=hashlib.sha256(public).hexdigest(),scope=scope)
        if projection:
            binding['publication_projection']=projection
            binding['scope']=binding['scope'].replace(t('unmodified', 'unmodified,'),'')
            binding['scope'] += (' ' + t('this_download_is_a_redacted_public_copy', 'This download is a redacted public copy; original bytes and scores are unchanged. Original SHA-256:') + f"{projection['original_sha256']}。")
        return binding
    for cohort in spec['cohorts']:
        run=contained_path(spec_path.parent,cohort['run'])
        dataset_path=run/'dataset.json';results_path=run/'results.jsonl'
        observations_path=contained_path(spec_path.parent,cohort['observations'])
        review_path=contained_path(spec_path.parent,cohort['review'])
        dataset=json.loads(dataset_path.read_text(encoding='utf-8'));cases={c['id']:c for c in dataset['cases']}
        records=load_jsonl(results_path)
        observations=json.loads(observations_path.read_text(encoding='utf-8'));review=json.loads(review_path.read_text(encoding='utf-8'))
        expected_bindings={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in
            [('dataset_sha256',dataset_path),('results_sha256',results_path),('observations_sha256',observations_path)]}
        if review.get('bindings') != expected_bindings:
            raise ValueError('Independent review does not bind the current cohort')
        reviewed={r['case_id']:r for r in review['rows']}
        if (len(records)!=len(cases) or {r['case_id'] for r in records}!=set(cases)
                or len(review['rows'])!=len(cases) or set(reviewed)!=set(cases)
                or set(observations)!=set(cases) or len({r['provider'] for r in records})!=1):
            raise ValueError('Diagnostic inputs require the complete task/observation/review matrix')
        execution=json.loads((run/'execution.json').read_text(encoding='utf-8'))
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
            answer=save('answers/'+cid+'.txt',raw,t('complete_output_of_this_run_s_only', "Complete output of this run's only call, unmodified and not replaced by a later fetch."))
            log={k:v for k,v in record.items() if k not in ('provider_metadata','artifact_path')}
            metadata=record['provider_metadata'];public_call=metadata.get('call_log')
            if public_call:
                call=json.loads(contained_path(run,public_call['path']).read_text(encoding='utf-8'))
                log['process']=call['process'];log['request']=call['request'];log['call_error']=call['error']
                log['error_message']=call['error']['message']
            log['provider_identity']=dict(reported_version=version,binary_sha256=binary_sha)
            reference=save('reference/'+cid+'.json',json_bytes(case),t('complete_task_and_acceptance_contract_frozen_before', 'Complete task and acceptance contract frozen before the call; not a word-for-word reference of the whole page.'))
            source=save('source/'+cid+'.json',json_bytes({k:v for k,v in observation.items() if k!='outer_html'}),
                t('visible_text_and_access_records_from_an', 'Visible text and access records from an independent browser; see the record for scope and further observations. Hidden DOM is not a visible answer. Access failures keep their errors and do not count as target body text.'))
            row=dict(id=cid,cohort=cohort['id'],url=case['url'],task=case.get('task_prompt') or case.get('name') or cid,
                run_id=record['run_id'],initial_pass=task_passed(record['ok'],record['metrics']),
                first_answer=answer,answer=answer,reference=reference,source=source,
                log=save('logs/'+cid+'.json',json_bytes(log),t('this_run_s_call_scoring_detail_and', "This run's call, scoring detail and redacted process records.")),
                failure=log.get('error_message') or '; '.join(record['metrics'].get('stable_task_errors') or []),
                cause=audit['cause'],cause_detail=audit['cause_detail'],followup_status=audit['followup_status'],
                source_checked_at=observation.get('checked_at'),reviewed=True,source_state=audit['source_state'])
            if row['initial_pass']:
                row['success_audit']={k:v for k,v in audit.items() if k.startswith('source_')}
            if audit.get('independent_verdict'):
                row['independent_verdict'] = audit['independent_verdict']
            rows.append(row)
    for blocks in model.get('pages',{}).values():
        for block in blocks:
            figure=block.get('figure')
            if not figure:continue
            image_path=contained_path(spec_path.parent,figure['path'],reject_symlinks=True)
            raw=image_path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=figure['sha256'] or not raw.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('Diagnosis illustration differs from its input binding')
            target=contained_path(output,figure['path'],reject_symlinks=True)
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    model.update(schema='webfetch_cohort_diagnostic/1',certified=False,cohorts=cohorts,rows=rows,provenance=provenance)
    model['metrics']={g['id']:[sum(r['initial_pass'] for r in rows if r['cohort']==g['id']),g['case_count']] for g in cohorts}
    model['metrics']['overall']=[sum(r['initial_pass'] for r in rows),len(rows)]
    audited=[r for r in rows if r.get('success_audit')]
    model['success_audit']=dict(audited_passes=len(audited),source_state_counts=dict(Counter(r['source_state'] for r in audited)))
    (output/'report.json').write_bytes(json_bytes(model))

FAILURE_TYPES = {
    'request': t('the_call_did_not_deliver_successfully', 'The call did not deliver successfully'), 'blocked': t('verification_or_access_restriction_page', 'Verification or access-restriction page'),
    'identity': t('url_or_page_language_mismatch', 'URL or page language mismatch'), 'text': t('required_text_or_facts_missing', 'Required text or facts missing'),
    'body': t('insufficient_body_coverage', 'Insufficient body coverage'), 'records': t('incomplete_list_or_table', 'Incomplete list or table'),
    'encoding': t('text_encoding_damaged', 'Text encoding damaged'), 'truncated': t('output_truncated', 'Output truncated'),
    'shell': t('only_script_or_page_framework', 'Only script or page framework'), 'length': t('too_little_content_returned', 'Too little content returned'),
    'resource': t('required_resource_missing', 'Required resource missing'),
    'file': t('original_file_not_delivered', 'Original file not delivered'),
    'pattern': t('content_structure_mismatch', 'Content structure mismatch'), 'other': t('other_failed_checks', 'Other failed checks'),
}


def diagnostic_reference_excerpt(row, files):
    """Show actual acceptance requirements, not reference storage metadata."""
    raw=files[row['reference']['path']].decode('utf-8')
    try:case=json.loads(raw)
    except ValueError:return raw
    if not isinstance(case,dict):return raw
    contract=case.get('acceptance_contract') or {};parts=[]
    schemas={'named_link':t('name_and_detail_link', 'Name and detail link'),'table_row':'表格行','body_text':t('page_text', 'Page text'),'page_identity':t('url_and_language', 'URL and language')}
    if contract.get('schema'):
        parts.append('验收内容：'+schemas.get(contract['schema'],contract['schema']))
    if contract.get('minimum_records'):
        parts.append((f"至少 {str(contract['minimum_records'])} " + t('complete_unique_records_2', 'complete, unique records')))
    if contract.get('table_columns'):
        parts.append((t('required_fields_per_row', 'Required fields per row:') + f"{'；'.join((c['label'] for c in contract['table_columns']))}"))
    anchors=contract.get('stable_text_anchors',[])+case.get('required_facts',[])
    if anchors:parts.append((t('required_text', 'Required text:') + f"{'；'.join((' / '.join(a.get('alternatives') or [a.get('id', '')]) for a in anchors))}"))
    if case.get('min_content_chars'):parts.append((t('content_length_at_least', 'Content length at least') + f" {str(case['min_content_chars'])} " + t('characters_2', 'characters')))
    body=case.get('body_requirements') or {}
    if body.get('regions'):parts.append((t('body_text', 'Body text') + f" {str(len(body['regions']))} 个分区；覆盖率及分区原文见完整验收契约"))
    patterns=contract.get('page_patterns') or []
    if patterns:parts.append((t('required_structure_patterns', 'Required structure patterns:') + f"{'；'.join(patterns)}"))
    return '\n'.join(parts) or case.get('task_prompt') or raw


def diagnostic_source_excerpt(row, files):
    """Explain an unavailable source with its observed error, not raw JSON."""
    binding = row.get('source')
    if not binding:
        return row.get('source_error') or t('target_body_text_was_not_obtained', 'Target body text was not obtained.')
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
    return (t('visible_target_body_text_was_not_obtained', 'Visible target body text was not obtained.') + f'''{("""
""" + """
""".join(errors) if errors else '')}''')


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
        add('request', t('the_call_did_not_deliver_successfully', 'The call did not deliver successfully'), t('call_succeeds_and_returns_the_agreed_output', 'Call succeeds and returns the agreed output'), error or row.get('failure') or t('the_saved_record_shows_a_failed_request', 'The saved record shows a failed request status'))
    if metrics.get('blocked_page') or metrics.get('stable_blocked_page'):
        add('blocked', t('returned_a_verification_or_access_restriction_page', 'Returned a verification or access-restriction page'), t('target_page_content_rather_than_a_verification', 'Target page content rather than a verification page'),
            '；'.join(metrics.get('blocked_page_hits') or []) or t('saved_scoring_detected_an_access_restriction_page', 'Saved scoring detected an access-restriction page'))
    if metrics.get('text_encoding_corruption'):
        add('encoding', t('output_text_damaged', 'Output text damaged'), t('readable_text_without_encoding_damage', 'Readable text without encoding damage'), t('saved_scoring_detected_encoding_damage', 'Saved scoring detected encoding damage'))
    if metrics.get('declared_truncation_hits'):
        add('truncated', t('output_declares_truncation', 'Output declares truncation'), t('complete_task_content', 'Complete task content'), '；'.join(metrics['declared_truncation_hits']))
    if metrics.get('script_payload_shell'):
        add('shell', t('only_script_or_page_framework_2', 'Only script or page framework'), '目标正文', t('saved_scoring_detected_script_or_an_empty', 'Saved scoring detected script or an empty framework'))
    if metrics.get('page_identity_status') == 'mismatch':
        add('identity', t('page_identity_diagnostic_does_not_decide_the', 'Page identity diagnostic (does not decide the result)'), t('for_checking_the_access_path_only_no', 'For checking the access path only; no pass requirement'),
            t('final_url_page_language', 'Final URL: {value}; page language: {value2}', value=str(log.get('final_url') or t('not_provided_by_the_record', 'Not provided by the record')), value2=str(metrics.get('observed_document_language') or log.get('document_language') or t('not_provided_by_the_record', 'Not provided by the record'))))
    for check in metrics.get('stable_anchor_results') or []:
        if check.get('pass') is False:
            add('text', t('required_text_missing', 'Required text missing'), ' / '.join(check.get('alternatives') or [check.get('id', t('not_provided', 'Not provided'))]), t('the_first_round_output_did_not_match', 'The first-round output did not match this text'))
    for check in metrics.get('fact_results') or []:
        if check.get('pass') is False:
            add('text', t('required_fact_missing', 'Required fact missing'), ' / '.join(check.get('alternatives') or [check.get('id', t('not_provided', 'Not provided'))]), t('the_first_round_output_did_not_match_2', 'The first-round output did not match this fact'))
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
            add('body', t('body_region_below_required_coverage', 'Body region below required coverage'),
                t('coverage_at_least_reference_content', '{get}: coverage at least {round}%; reference content: {value}', get=str(region.get('id', t('body_region', 'Body region'))), round=str(round(100 * region.get('minimum', 0), 1)), value=sample or t('see_the_reference_answer', 'See the reference answer')),
                (t('actual_coverage', 'Actual coverage') + f" {str(round(100 * region.get('coverage', 0), 1))}%"))
        if not regions:
            add('body', t('insufficient_body_coverage', 'Insufficient body coverage'), t('meets_the_declared_body_region_requirements', 'Meets the declared body-region requirements'), t('body_coverage_check_failed_the_original_record', 'Body coverage check failed; the original record has no region detail'))
    if metrics.get('stable_structure_pass') is False:
        add('records', t('incomplete_list_or_table', 'Incomplete list or table'),
            t('at_least_complete_and_unique_records', 'At least {get} complete and unique records', get=str(metrics.get('stable_minimum_records', t('not_provided_by_the_record', 'Not provided by the record')))),
            t('complete_and_unique_records', '{get} complete and unique records', get=str(metrics.get('stable_unique_record_count', t('not_provided_by_the_record', 'Not provided by the record')))))
    for check in metrics.get('stable_field_results') or []:
        if check.get('pass') is False:
            add('records', t('records_missing_required_fields', 'Records missing required fields'),
                t('at_least_records_provide_this_field', '{get}: at least {get2} records provide this field', get=str(check.get('field', t('unnamed_field', 'Unnamed field'))), get2=str(check.get('minimum', t('not_provided_by_the_record', 'Not provided by the record')))),
                t('records_provide_this_field', '{get} records provide this field', get=str(check.get('count', t('not_provided_by_the_record', 'Not provided by the record')))))
    for group in metrics.get('composite_group_results') or []:
        if group.get('pass') is False:
            add('records', t('incomplete_record_group', 'Incomplete record group'), t('at_least_qualifying_records', '{get}: at least {get2} qualifying records', get=str(group.get('id', t('record_group', 'Record group'))), get2=str(group.get('minimum', t('not_provided_by_the_record', 'Not provided by the record')))),
                t('qualifying_records', '{get} qualifying records', get=str(group.get('count', t('not_provided_by_the_record', 'Not provided by the record')))))
    if metrics.get('composite_acceptance_pass') is False or any(k in str(row.get('failure', '')) for k in ('composite', 'incomplete_records', 'too_few_complete_records')):
        if not any(f['kind'] == 'records' for f in findings):
            add('records', t('incomplete_list_or_table', 'Incomplete list or table'), t('meets_the_task_s_record_count_field', "Meets the task's record count, field, link and uniqueness requirements"), row.get('failure') or t('the_combined_record_check_failed_in_saved', 'The combined-record check failed in saved scoring'))
    if metrics.get('length_pass') is False:
        add('length', t('too_little_content_returned', 'Too little content returned'), t('at_least_characters_3', 'At least {get} characters', get=str(metrics.get('minimum_content_chars', t('not_provided_by_the_record', 'Not provided by the record')))),
            t('characters_3', '{get} characters', get=str(metrics.get('content_chars', t('not_provided_by_the_record', 'Not provided by the record')))))
    for check in (metrics.get('pattern_results') or []) + (metrics.get('stable_page_pattern_results') or []):
        if check.get('pass') is False:
            add('pattern', t('required_content_structure_missing', 'Required content structure missing'), check.get('pattern') or check.get('id', t('declared_structure_requirement', 'Declared structure requirement')),
                t('this_structure_pattern_did_not_match', 'This structure pattern did not match') if 'pattern' in check else
                t('items_at_least_required', '{get} items; at least {get2} required', get=str(check.get('count', t('not_provided_by_the_record', 'Not provided by the record'))), get2=str(check.get('minimum', t('not_provided_by_the_record', 'Not provided by the record')))))
    if metrics.get('document_structure_pass') is False:
        try:
            reference=json.loads(files[row['reference']['path']])
            contract=reference.get('document_structure',{})
        except (KeyError,ValueError,TypeError,AttributeError):
            contract={}
        coverage=metrics.get('document_heading_coverage')
        if coverage is not None and coverage < contract.get('minimum_heading_coverage',1):
            headings={h.get('id','section-'+str(i)):h['text'] for i,h in enumerate(contract.get('headings',[]),1)}
            checks=[h for h in metrics.get('document_heading_results',[]) if h.get('pass') is False]
            absent=[h for h in checks if h.get('present') is not True]
            short=[h for h in checks if h.get('present') is True]
            if absent or not checks:
                names=[headings.get(h['id'],h['id']) for h in absent] or list(headings.values())
                add('pattern',t('document_headings_not_preserved','Document headings not preserved'),
                    ' / '.join(names) or t('declared_structure_requirement','Declared structure requirement'),
                    t('heading_coverage_value','Heading coverage: {value}%',value=round(100*coverage,1)))
            if short:
                add('body',t('document_section_body_missing','Heading present but section text insufficient'),
                    ' / '.join(headings.get(h['id'],h['id']) for h in short),
                    t('section_body_below_requirement','Required section text did not meet its minimum'))
        if metrics.get('document_body_chars',0) < metrics.get('document_minimum_body_chars',0):
            add('body',t('document_body_too_short','Document section body too short'),
                t('at_least_characters_3','At least {get} characters',get=metrics['document_minimum_body_chars']),
                t('characters_3','{get} characters',get=metrics.get('document_body_chars',0)))
    if metrics.get('article_title_pass') is False:
        add('text',t('article_title_missing','Article title missing'),
            t('article_title_in_body','Article title retained as a body component'),
            t('article_title_not_found','No qualifying article title in the returned body'))
    for resource in metrics.get('resource_results') or []:
        if resource.get('pass') is False:
            add('resource',t('required_resource_missing','Required resource missing'),
                ' / '.join(resource.get('expected_urls') or [resource.get('id','resource')]),
                ' / '.join(resource.get('matched_urls') or []) or t('no_matching_resource','No matching resource'))
    if metrics.get('raw_file_pass') is False:
        add('file',t('original_file_not_delivered','Original file not delivered'),
            t('original_file_bytes_required','Original file bytes matching the declared validation'),
            t('original_file_validation_failed','Original file validation failed'))
    if not findings:
        add('other', t('other_failed_checks', 'Other failed checks'), t('meets_the_complete_task_requirements', 'Meets the complete task requirements'), row.get('failure') or t('the_saved_record_lists_no_specific_failed', 'The saved record lists no specific failed item'))
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


def diagnostic_cell_text(value):
    return value['label'] if isinstance(value, dict) else str(value)


def diagnostic_cell_href(value):
    if 'case_id' in value:
        return 'attempts/' + value['case_id'] + '.html'
    if 'issue_id' in value:
        return 'guide/index.html#' + value['issue_id']
    return value['url']


def validate_diagnostic_blocks(model, files, source):
    """Bind authored diagnosis examples and illustrations to inspectable evidence."""
    if model.get('problem_focus') and not any(block.get('examples') for block in model.get('pages',{}).get('guide',[])):
        raise ValueError('Problem-focused guide needs concrete case diagnoses')
    rows = {row['id']: row for row in model['rows']}
    guide_ids = {block['id'] for block in model.get('pages', {}).get('guide', []) if block.get('id')}
    for blocks in model.get('pages', {}).values():
        for block in blocks:
            if block.get('id'):
                safe_identifier(block['id'])
            for values in block.get('table', {}).get('rows', []):
                for value in values:
                    if not isinstance(value, dict):
                        continue
                    kind = next((key for key in ('case_id', 'issue_id', 'url') if key in value), None)
                    if kind is None or set(value) != {kind, 'label'} or not value['label']:
                        raise ValueError('Diagnosis table link has no matching case or issue')
                    if kind == 'url':
                        validate_http_url(value[kind])
                        if urlsplit(value[kind]).username is not None:
                            raise ValueError('Diagnosis table links cannot contain credentials')
                    elif value[kind] not in (rows if kind == 'case_id' else guide_ids):
                        raise ValueError('Diagnosis table link has no matching case or issue')
            figure = block.get('figure')
            if figure:
                if not block.get('id'):
                    raise ValueError('Diagnosis illustration needs a section anchor')
                if not all(isinstance(figure.get(k), str) and figure[k].strip()
                           for k in ('path', 'sha256', 'alt', 'scope')):
                    raise ValueError('Diagnosis illustration needs a hash, description and scope')
                path = contained_path(source, figure['path'], reject_symlinks=True)
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != figure['sha256']:
                    raise ValueError('Diagnosis illustration hash mismatch')
                if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('Diagnosis illustration must be a PNG')
                files[figure['path']] = raw
            for example in block.get('examples', []):
                row = rows.get(example.get('case_id'))
                if row is None or any(not isinstance(example.get(k), str) or not example[k].strip()
                                      for k in ('expected', 'actual', 'cause', 'fix', 'verify')):
                    raise ValueError('Diagnosis example needs a case, discrepancy, cause and repair')
                if not example.get('evidence'):
                    raise ValueError('Diagnosis example needs a verbatim evidence locator')
                for evidence in example['evidence']:
                    binding = row.get(evidence.get('field')) if evidence.get('field') in {
                        'reference', 'first_answer', 'answer', 'source', 'log'} else None
                    if not binding or not evidence.get('quote'):
                        raise ValueError('Diagnosis example evidence field is absent')
                    raw = files.get(binding['path'])
                    if raw is None:
                        raw = contained_path(source, binding['path'], reject_symlinks=True).read_bytes()
                    if evidence['quote'] not in raw.decode('utf-8'):
                        raise ValueError('Diagnosis quotation is absent from bound case evidence')


def load_diagnostic(source):
    source = Path(source).resolve()
    model = json.loads(contained_path(source, "report.json").read_text(encoding='utf-8'))
    if model.get("schema") != "webfetch_cohort_diagnostic/1" or model.get("certified") is not False:
        raise ValueError("Expected an explicitly diagnostic WebFetch model")
    if model.get('scope_views'):
        views = model['scope_views']
        if ({view['id'] for view in views} != {'main', 'full'} or len(views) != 2
                or model.get('content_scope') not in {'main', 'full'}):
            raise ValueError('Content scope navigation needs main and full views')
        for view in views:
            if not view.get('label') or not view.get('href'):
                raise ValueError('Content scope navigation needs labels and destinations')
            if view['href'] != 'index.html':
                validate_http_url(view['href'])
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
                    or t('redacted', 'Redacted') not in binding.get('scope', '')):
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
        if model.get('paired_content_scope'):
            log = json.loads(files[row['log']['path']])
            current = log.get('metrics', {})
            paired = log.get('uniform_full_content', {})
            if (current.get('evaluation_content_scope') != 'main'
                    or paired.get('metrics', {}).get('evaluation_content_scope') != 'full'
                    or not current.get('scoring_source_sha256')
                    or current['scoring_source_sha256'] != paired['metrics'].get('scoring_source_sha256')
                    or type(paired.get('passed')) is not bool
                    or (paired['passed'] and not row['initial_pass'])):
                raise ValueError('Paired content scopes require one scorer and full-pass implies main-pass')
    validate_diagnostic_blocks(model, files, source)
    audited = [r for r in rows if r.get("success_audit")]
    states = Counter(r["success_audit"]["source_state"] for r in audited)
    if len(audited) != model["success_audit"]["audited_passes"] or dict(states) != model["success_audit"]["source_state_counts"]:
        raise ValueError("Success review summary differs from result evidence")
    return model, files


def check_diagnostic_report(output, profile=None):
    """Enforce the shared reader structure and evidence dialogs at release."""


    output = open_report(output)
    model, files = load_diagnostic(output)
    if profile is not None:
        require_diagnostic_scope(model, read_json(Path(profile)))
    manifest = read_json(output / "report-manifest.json")
    check_build_version(output, manifest)
    planned=manifest.get('diagnostic_profile')
    if planned:
        if planned != diagnostic_profile_binding(planned['profile']):
            raise ValueError('Diagnostic planned profile hash differs')
        require_diagnostic_scope(model,planned['profile'])
        planned_input=planned['profile'].get('diagnostic_input')
        if planned_input and manifest.get('input_sha256')!=planned_input['sha256']:
            raise ValueError('Diagnostic input differs from its planned profile')
        if profile is not None and planned != diagnostic_profile_binding(read_json(Path(profile))):
            raise ValueError('Diagnostic build uses another planned profile')
    if profile is not None:
        binding = read_json(Path(profile)).get('diagnostic_input')
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
        checked_page(output / route)
    for name, blocks in model.get('pages', {}).items():
        route = 'index.html' if name == 'overview' else name + '/index.html'
        page = read_page(output / route)
        for index, block in enumerate(blocks):
            if not block.get('table'):
                continue
            spec = block['table']
            rendered = page.select_one(f'#diagnostic-table-{name}-{index}')
            if (not rendered
                    or [c.get_text() for c in rendered.select('thead th')][1:] != spec['headers']
                    or [[c.get_text() for c in r.select('td')][1:] for r in rendered.select('tbody tr')]
                    != [[diagnostic_cell_text(v) for v in r] for r in spec['rows']]):
                raise ValueError('Diagnostic explanatory table differs from evidence model')
            for source_row, rendered_row in zip(spec['rows'],rendered.select('tbody tr'),strict=True):
                for value,td in zip(source_row,rendered_row.select('td')[1:],strict=True):
                    if not isinstance(value,dict):continue
                    href = diagnostic_cell_href(value)
                    anchors=td.select('a')
                    if len(anchors)!=1 or anchors[0].get('href')!=href:
                        raise ValueError('Diagnosis table points to different evidence')
                    if 'issue_id'in value and not read_page(output/'guide/index.html').find(id=value['issue_id']):
                        raise ValueError('Diagnosis issue anchor is absent')
    home = read_page(output / 'index.html')
    overall = diagnostic_call_summary(model['rows'], files)
    for route in (() if model.get('problem_focus') else ('index.html', 'guide/index.html')):
        page = read_page(output / route)
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
    for expected, rendered in zip(expected_summaries, summaries, strict=True):
        values = [c.get('data-sort', '-1') for c in rendered.select('td')][3:]
        expected_values = [expected[k] for k in ('total','delivered','accepted','mean_ms','p50_ms','p95_ms')]
        if model.get('comparison_mode'):
            expected_values[1:3]=[v/expected['total'] if v is not None else None for v in expected_values[1:3]]
        if (rendered.get('data-summary-group') != expected['id']
                or values != [str(-1 if v is None else v) for v in expected_values]):
            raise ValueError('Summary differs from complete cohort calls')
    if model.get('comparison_mode'):
        from harness.reporting.presentation import check_presentation
        for route in ('index.html', 'guide/index.html'):
            comparison = read_page(output / route).select_one('#cohort-summary, #guide-summary')
            if (not comparison or len(comparison.select('td[data-metric-direction]'))
                    != 2 * len(expected_summaries)):
                raise ValueError('Comparison summary lacks metric directions')
            check_presentation(str(comparison))
    bars = home.select('#cohort-results .editorial-bar-row')
    if len(bars) != (0 if model.get('problem_focus') else len(model['cohorts'])):
        raise ValueError('Missing cohort result chart')
    for bar, group in zip(bars, [] if model.get('problem_focus') else model['cohorts'], strict=True):
        passed, total = model['metrics'][group['id']]
        label, count, fill = bar.select_one('span'), bar.select_one('b'), bar.select_one('i')
        if (not label or label.get_text() != group['label'] or not count
                or count.get_text() != f'{passed} / {total}' or not fill):
            raise ValueError('Cohort chart differs from measured results')
        width = re.fullmatch(r'width:([0-9.]+)%', fill.get('style', ''))
        if not width or abs(float(width[1]) - 100 * passed / total) > 1e-9:
            raise ValueError('Cohort chart percentage mismatch')
    analysis = read_page(output / 'analysis/index.html')
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
    guide = read_page(output / 'guide/index.html')
    if not guide.select_one('#guide-results') or not guide.select_one('#guide-conclusions'):
        raise ValueError('Interpretation lacks outcome or conclusion')
    lead = guide.select_one('.hero .lead')
    if not lead or (lead.get_text() != model['interpretation_lead'] if model.get('interpretation_lead') else not lead.get_text().startswith(f"{model['metrics']['overall'][0]} / {len(model['rows'])} ")):
        raise ValueError('Interpretation headline differs from measured outcome')
    if [r.get_text() for r in guide.select('[data-summary-group]')] != [r.get_text() for r in summaries]:
        raise ValueError('Interpretation summary differs from measured outcome')
    causes = Counter(r['cause'] for r in model['rows'] if not r['initial_pass'])
    if not model.get('problem_focus'):
        cause_rows = guide.select('#guide-reviewed-causes tbody tr')
        if len(cause_rows) != len(causes):
            raise ValueError('Interpretation lacks reviewed failure distribution')
        for rendered, (cause, count) in zip(cause_rows, causes.most_common(), strict=True):
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
        for cid, specimen in zip(examples, specimens, strict=True):
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
    for name,page_blocks in model.get('pages',{}).items():
        diagnosis_page=read_page(output/('index.html' if name=='overview' else name+'/index.html'))
        expected_examples = [example for block in page_blocks
                             for example in block.get('examples', [])]
        rendered_examples = diagnosis_page.select('[data-diagnosis-case]')
        if (model.get('problem_focus') and name=='guide' and not expected_examples) or len(expected_examples) != len(rendered_examples):
            raise ValueError('Problem-focused guide lacks concrete case diagnoses')
        for expected, rendered in zip(expected_examples, rendered_examples, strict=True):
            if (rendered['data-diagnosis-case'] != expected['case_id']
                    or not rendered.select_one('a[href="attempts/'+expected['case_id']+'.html"]')):
                raise ValueError('Diagnosis points to a different result')
            for key in ('expected', 'actual', 'cause', 'fix', 'verify'):
                node = rendered.select_one('[data-diagnosis-field="'+key+'"]')
                actual = node.select_one('p').get_text() if key in {'expected', 'actual'} and node else node.get_text() if node else None
                if actual != expected[key]:
                    raise ValueError('Diagnosis omits or changes its evidence-backed '+key)
        for block in page_blocks:
            if not block.get('figure'):
                continue
            rendered = diagnosis_page.select_one('#'+block['id']+' .diagnosis-figure img')
            figure = block['figure']
            if (not rendered or rendered.get('src') != figure['path']
                    or rendered.get('alt') != figure['alt']):
                raise ValueError('Diagnosis illustration is missing or points to different evidence')
            section_node = diagnosis_page.find(id=block['id'])
            if (rendered.find_parent('details') is not None
                    or section_node.find('h2').find_next_sibling() != rendered.find_parent('figure')):
                raise ValueError('Diagnosis illustration must lead its section without folding')
    for row in model["rows"]:
        route = "attempts/" + row["id"] + ".html"
        soup = checked_page(output / route)
        if soup.select_one('[data-task-id]')["data-task-id"] != row["id"]:
            raise ValueError("Result identity mismatch")
        detail = soup.select_one('[data-report-detail]')
        if detail["data-report-detail"] != ('success' if row['initial_pass'] else 'failed'):
            raise ValueError("Rendered verdict differs from measured result")
        expected_findings = diagnostic_findings(row, files)
        rendered_findings = soup.select('#failed-requirements [data-finding-kind]')
        if len(expected_findings) != len(rendered_findings):
            raise ValueError('Missing or duplicate failed requirement explanations')
        for expected, rendered in zip(expected_findings, rendered_findings, strict=True):
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
        soup = read_page(route)
        if model.get('scope_views'):
            switches = soup.select('[data-content-scope]')
            expected = [(view['id'], view['href'], view['label']) for view in model['scope_views']]
            if [(node.get('data-content-scope'), node.get('href'), node.get_text()) for node in switches] != expected:
                raise ValueError('Missing or wrong content scope navigation')
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
