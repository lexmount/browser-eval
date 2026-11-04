"""Evidence-based failure stage, preserving uncertainty about remote internals."""

import json
import re

from lexbench_webfetch.metrics import task_measured

from harness.web_fetch.report_i18n import t


def _platform_capability_restricted(message):
    """Match an explicit service-side regional capability restriction."""
    return bool(re.search(
        r'\buse of\b.{1,500}\bis not allowed(?: by default)? in '
        r'(?:your|this) (?:country|region)\b',
        message,
        re.S,
    ))


def _unsupported_output_error(message):
    """Use an explicit delivery-format rejection rather than debug option names."""
    return re.search(
        r'raw download output only supports[^\r\n]*|'
        r'unsupported output format[^\r\n]*|'
        r'(?:--dump[ =]+\S+|output format)[^\r\n]{0,80}\b(?:is not supported|unsupported)\b[^\r\n]*',
        message, re.I,
    )


def _timeout_error(message, error_type):
    if re.search(r'(?:^|_)(?:timeout|timed_out)$', str(error_type or '').lower()):
        return True
    # timeout_ms, request_timeout_ms and timeout=45000 describe configuration,
    # whereas a standalone timeout or an explicit deadline failure is evidence.
    return bool(re.search(r'timed out|\btimeout\b(?!["\']?\s*[:=])|deadline exceeded', message, re.I))


def _stable_failure(metrics):
    """Describe measured contract errors; never infer a shortfall by default."""
    errors = metrics.get('stable_task_errors', [])
    if 'page_identity_mismatch' in errors or any(e.startswith('missing_stable_anchor:') for e in errors):
        label = t('required_information_missing', 'Required information missing')
    elif 'too_many_records' in errors:
        label = t('too_many_valid_records', 'Too many valid records')
    elif 'duplicate_records' in errors:
        label = t('duplicate_records', 'Duplicate records')
    elif any(e in errors for e in ('incomplete_records', 'invalid_link_output')):
        label = t('invalid_field_format_or_incomplete_record', 'Invalid field format or incomplete record')
    elif 'too_few_complete_records' in errors:
        label = t('insufficient_valid_records', 'Insufficient valid records')
    else:
        label = t('task_requirements_not_met', 'Task requirements not met')
    counts = t('complete_deduplicated_records_at_least_required', '{get} complete, deduplicated records; at least {get2} required', get=metrics.get('stable_unique_record_count', t('not_provided', 'Not provided')), get2=metrics.get('stable_minimum_records', t('not_provided', 'Not provided')))
    maximum = metrics.get('stable_maximum_records')
    if maximum is not None:
        counts += t('at_most_allowed', ', at most {maximum} allowed', maximum=maximum)
    return label, t('content_validation', 'Content validation'), f"{counts}。规则：{', '.join(errors) or t('no_specific_error_provided', 'No specific error provided')}"


def diagnose(r,c,raw):
    if not task_measured(r['metrics'], c.get('task_kind')):
        return None
    try:payload=json.loads(raw)
    except (ValueError,TypeError):payload={}
    if not isinstance(payload,dict):payload={}
    nested=payload.get('error') or {}
    if not isinstance(nested,dict):nested={}
    meta=r.get('provider_metadata',{});m=r['metrics']
    fragments=[str(r.get('error_type') or ''),str(r.get('error_message') or '')]
    if not r['ok']:fragments.extend([raw[:3000],json.dumps(meta.get('errors') or [],ensure_ascii=False)[:1000]])
    message=' '.join(fragments);low=message.lower()
    upstream=nested.get('upstream_status_code') or meta.get('upstream_status')
    category=None;stage='';proof='';certainty=t('direct_response_basis', 'Direct-response basis')
    def set_result(label,where,evidence):return label,where,evidence
    if m.get('deadline_overrun'):
        category,stage,proof=set_result(t('end_to_end_deadline_exceeded', 'End-to-end deadline exceeded'),t('call_deadline', 'Call deadline'),t('the_actual_latency_exceeded_the_budget_registered', 'The actual latency exceeded the budget registered for this call; the original content and timing are kept, but it does not count as on-time delivery.'))
    elif meta.get('empty_content'):
        category,stage,proof=set_result(t('body_extraction_empty', 'Body extraction empty'),t('content_extraction', 'Content extraction'),t('the_interface_succeeded_but_the_returned_result', 'The interface succeeded, but the returned result is empty; response type: {value}', value=str(meta.get('content_type') or t('not_provided', 'Not provided'))))
    elif not r['ok']:
        output_error = _unsupported_output_error(message)
        if output_error:
            category,stage,proof=set_result(t('required_output_format_unsupported', 'Required output format unsupported'),t('content_delivery', 'Content delivery'),output_error.group(0)[:650])
        elif any(x in low for x in ['unsupported_content_type','unexpected_content_type','unsupported_file','file type that firecrawl cannot process']):
            category,stage,proof=set_result(t('unsupported_input_type', 'Unsupported input type'),t('method_capability', 'Method capability'),message[:650])
        elif 'do not support this site' in low:
            category,stage,proof=set_result(t('method_does_not_support_this_site', 'Method does not support this site'),t('method_policy', 'Method policy'),message[:650])
        elif 'web_redirect_blocked' in low:
            category,stage,proof=set_result(t('method_blocks_cross_site_redirects', 'Method blocks cross-site redirects'),t('method_security_policy', 'Method security policy'),message[:650])
        elif any(x in low for x in ['web_blocked_url','private/internal','ssrfblocked','non-public ip']):
            category,stage,proof=set_result(t('method_safety_restriction', 'Method safety restriction'),t('method_security_policy', 'Method security policy'),message[:650])
        elif 'datadome captcha' in low:
            category,stage,proof=set_result(t('source_site_requires_verification', 'Source site requires verification'),t('origin_access', 'Origin access'),message[:650])
        elif 'blocked by anti-bot protection: structural:' in low:
            category,stage,proof=set_result(t('method_empty_page_rule_blocked_retrieval', 'Method empty-page rule blocked retrieval'),t('method_content_check', 'Method content check'),(f"{str(r.get('error_message'))[:650]}" + t('this_is_the_method_s_structural_heuristic', "; this is the method's structural heuristic and does not confirm anti-bot blocking by the source site.")))
        elif 'download is starting' in low:
            category,stage,proof=set_result(t('download_not_handled_by_the_method', 'Download not handled by the method'),t('browser_download', 'Browser download'),message[:650])
        elif 'crawl_empty_content' in low:
            category,stage,proof=set_result(t('body_extraction_empty', 'Body extraction empty'),t('content_extraction', 'Content extraction'),message[:650])
        elif 'source_not_available' in low:
            category,stage,proof=set_result(t('service_rejected_this_source', 'Service rejected this source'),t('provider_retrieval', 'Provider retrieval'),message[:650])
            certainty=t('the_service_returned_source_not_available_this', 'The service returned SOURCE_NOT_AVAILABLE; this does not imply the site is unavailable to all visitors')
        elif r.get('error_type')=='authentication':
            category,stage,proof=set_result(t('api_authentication_failed', 'API authentication failed'),t('service_interface', 'Service interface'),message[:650])
        elif r.get('status_code')==429 or 'rate_limit' in low:
            category,stage,proof=set_result(t('rate_limit_quota', 'Rate limit / quota'),t('service_interface', 'Service interface'),message[:650])
        elif nested.get('code')=='browser_fetch_failed' and 'deadline exceeded' in low:
            category,stage,proof=set_result(t('browser_timeout_inside_the_service', 'Browser timeout inside the service'),t('provider_retrieval', 'Provider retrieval'),str(nested.get('message')))
        elif _platform_capability_restricted(low):
            category,stage,proof=set_result(t('platform_call_capability_restricted', 'Platform call capability restricted'),t('service_interface', 'Service interface'),message[:650])
            certainty=t('the_service_explicitly_restricts_request_capability_by', 'The service explicitly restricts request capability by calling region; this is not attributed to the source site')
        elif 'execution context was destroyed' in low:
            category,stage,proof=set_result(t('browser_execution_context_interrupted', 'Browser execution context interrupted'),t('browser_execution', 'Browser execution'),message[:650])
        elif _timeout_error(message, r.get('error_type')):
            category,stage,proof=set_result(t('load_extraction_timeout', 'Load / extraction timeout'),t('page_fetch_or_extraction', 'Page fetch or extraction'),message[:650])
        elif upstream in {401,403} or re.search(r'(?:http[_ :]*|failed \(|status code[: ]*|status_not_ok.{0,80})(?:401|403)',low) or re.search(r'(401|403) client error',low):
            category,stage,proof=set_result(t('origin_denied_this_request', 'Origin denied this request'),t('origin_access', 'Origin access'),str(nested.get('message') or r.get('error_message') or message)[:650])
        elif any(x in low for x in ['crawl_not_found','404 page not found','failed (404)']):
            category,stage,proof=set_result(t('retrieval_returned_404', 'Retrieval returned 404'),t('origin_access', 'Origin access'),message[:650])
        elif 'err_aborted' in low or 'network connection aborted' in low:
            category,stage,proof=set_result(t('browser_navigation_aborted', 'Browser navigation aborted'),t('browser_navigation', 'Browser navigation'),message[:650])
        elif any(x in low for x in ['name resolution','err_name_not_resolved']) or r.get('error_type')=='dns':
            category,stage,proof=set_result(t('domain_name_resolution_failed', 'Domain name resolution failed'),t('network_connection', 'Network connection'),message[:650])
        elif 'extraction failed' in low and 'no content' in low:
            category,stage,proof=set_result(t('body_extraction_empty', 'Body extraction empty'),t('content_extraction', 'Content extraction'),message[:650])
        elif any(x in low for x in ['failed to connect','connection','err_']) or r.get('error_type')=='network':
            category,stage,proof=set_result(t('retrieval_connection_failed', 'Retrieval connection failed'),t('network_connection', 'Network connection'),message[:650])
        elif r.get('error_type') in {'provider_exception','response_validation'}:
            category,stage,proof=set_result(t('local_adapter_execution_failed', 'Local adapter execution failed'),t('local_execution', 'Local execution'),message[:650])
        else:
            category,stage,proof=set_result(t('provider_fetch_failed', 'Provider fetch failed'),t('provider_retrieval', 'Provider retrieval'),message[:650])
            certainty=t('the_provider_did_not_expose_a_more', 'The provider did not expose a more specific cause; this error alone cannot attribute fault to the origin or the provider internals.')
    elif m.get('text_encoding_corruption'):
        category,stage,proof=set_result(
            t('corrupted_text_decoding', 'Corrupted text decoding'),t('content_extraction', 'Content extraction'),
            t('saved_output_contains_unicode_replacement_characters_the', 'Saved output contains {get} Unicode replacement characters ({value:.1f}%); the source bytes were replaced before the method returned and cannot be restored from this Markdown.', get=m.get('replacement_character_count', 0), value=m.get('replacement_character_ratio', 0) * 100))
    elif c.get('task_kind', 'body') == 'body' and m.get('declared_truncation_hits'):
        category,stage,proof=set_result(
            t('incomplete_body_coverage', 'Incomplete body coverage'),t('content_validation', 'Content validation'),
            t('the_provider_explicitly_declares_that_the_returned', 'The provider explicitly declares that the returned body is truncated: {join}', join=', '.join(m['declared_truncation_hits'])))
    elif 'stable_task_pass' in m:
        if not m.get('stable_task_pass'):
            category,stage,proof=_stable_failure(m)
    elif c.get('task_kind')=='structure':
        if m.get('named_entry_count',0) < m.get('minimum_entries',3):
            category,stage,proof=set_result(t('insufficient_valid_records', 'Insufficient valid records'),t('content_validation', 'Content validation'),t('found_named_detail_entries_at_least_required', 'Found {get} named detail entries; at least {get2} required', get=m.get('named_entry_count', 0), get2=m.get('minimum_entries', 3)))
        elif m.get('list_field_coverage',0)<1:
            category,stage,proof=set_result(t('invalid_field_format_or_incomplete_record', 'Invalid field format or incomplete record'),t('content_validation', 'Content validation'),t('there_are_enough_named_detail_entries_but', 'There are enough named detail entries, but not all list fields meet the requirements.'))
        elif not m.get('structure_pass'):
            category,stage,proof=set_result(t('incomplete_content_structure', 'Incomplete content structure'),t('content_validation', 'Content validation'),t('there_are_enough_named_detail_entries_but_2', 'There are enough named detail entries, but the structure check failed; this does not establish too few entries.'))
        elif not m.get('list_task_pass'):
            restricted=[x for x in m.get('list_checks',[]) if x.get('access_restricted')]
            if restricted:
                category,stage,proof=set_result(t('detail_access_restricted', 'Detail access restricted'),t('independent_content_validation', 'Independent content validation'),t('details_of_returned_links_are_access_restricted', 'Details of {count} returned links are access-restricted: {get}. Name and detail identity remain unconfirmed; this does not establish that the notes are dead or the content wrong.', count=len(restricted), get=restricted[0].get('access_note', '')))
            else:
                category,stage,proof=set_result(t('entry_content_unconfirmed', 'Entry content unconfirmed'),t('independent_content_validation', 'Independent content validation'),t('of_the_first_three_entries_have_independent', '{get} of the first three entries have independent name-and-link evidence; unconfirmed does not mean wrong', get=m.get('confirmed_entries', 0)))
    elif c.get('task_kind')=='file' and not m.get('raw_file_pass'):
        if meta.get('file_evaluation', {}).get('files'):
            category,stage,proof=set_result(t('file_validation_failed', 'File validation failed'),t('delivery_validation', 'Delivery validation'),t('file_bytes_were_returned_but_format_length', 'File bytes were returned, but format, length or target SHA-256 validation failed; see the file-delivery evidence for individual checks'))
        else:
            category,stage,proof=set_result(t('original_file_not_obtained', 'Original file not obtained'),t('delivery_validation', 'Delivery validation'),t('no_verifiable_original_file_bytes_text_or', 'No verifiable original-file bytes; text or a URL cannot substitute for the file.'))
    elif c.get('task_kind')=='resource' and not m.get('resource_pass'):
        missing=[x.get('id','') for x in m.get('resource_results',[]) if not x['pass']]
        unexpected=m.get('unexpected_resource_urls',[])
        if missing:
            proof=t('missing_resource', 'Missing resource: {join}', join=', '.join(missing))
            if unexpected:proof+=(t('also_returned_unexpected_resources', '; also returned unexpected resources: ') + f"{', '.join(unexpected)}")
            category,stage,proof=set_result(t('required_resource_missing', 'Required resource missing'),t('resource_validation', 'Resource validation'),proof)
        elif unexpected:
            category,stage,proof=set_result(
                t('returned_unexpected_resources', 'Returned unexpected resources'),t('resource_validation', 'Resource validation'),
                t('the_target_resource_was_found_but_answer', 'The target resource was found, but answer-bearing fields also returned unexpected resources: {join}', join=', '.join(unexpected)))
        else:category,stage,proof=set_result(t('required_resource_missing', 'Required resource missing'),t('resource_validation', 'Resource validation'),t('no_registered_target_resource_was_found', 'No registered target resource was found.'))
    elif c.get('task_kind','body')=='body' and not m.get('content_pass'):
        if m.get('body_coverage_pass') is False:
            category,stage,proof=set_result(t('incomplete_body_coverage', 'Incomplete body coverage'),t('content_validation', 'Content validation'),(t('body_region_coverage_requirement_not_met', 'Body-region coverage requirement not met:') + f"{', '.join((x['id'] for x in m.get('body_region_results', []) if not x['pass']))}"))
        elif not m.get('length_pass'):
            category,stage,proof=set_result(t('insufficient_body_text', 'Insufficient body text'),t('content_validation', 'Content validation'),t('extracted_body_text_did_not_reach_this', "Extracted body text did not reach this task's minimum length"))
        elif any(not f['pass'] for f in m.get('fact_results',[])):
            if any(not f['pass'] and f.get('match_scope')=='entry' for f in m['fact_results']):
                category,stage,proof=set_result(t('entry_not_fully_matched', 'Entry not fully matched'),t('content_validation', 'Content validation'),t('no_complete_field_combination_for_an_entry', 'No complete field combination for an entry was matched; this does not mean every field is absent. See the item-by-item verdict.'))
            else:
                category,stage,proof=set_result(t('required_information_missing', 'Required information missing'),t('content_validation', 'Content validation'),(t('missed_checks', 'Missed checks:') + f"{', '.join((f['id'] for f in m['fact_results'] if not f['pass']))}"))
        elif m.get('legacy_marker_pass') is False:
            category,stage,proof=set_result(t('required_information_missing', 'Required information missing'),t('content_validation', 'Content validation'),(t('required_body_markers_missed', 'Required body markers missed:') + f"{', '.join(m.get('expected_markers', []))}"))
        elif any(not p['pass'] and p['count'] < p['minimum'] for p in m.get('pattern_results',[])):
            category,stage,proof=set_result(t('insufficient_valid_records', 'Insufficient valid records'),t('content_validation', 'Content validation'),t('count_checks_not_met', 'Count checks not met: {join}', join=', '.join((t('found_at_least_required', '{id} found {count}, at least {minimum} required', id=p['id'], count=p['count'], minimum=p['minimum']) for p in m['pattern_results'] if not p['pass']))))
        else:category,stage,proof=set_result(t('incomplete_content_structure', 'Incomplete content structure'),t('content_validation', 'Content validation'),t('this_task_s_field_or_count_rules', "This task's field or count rules were not met; see the item-by-item verdict"))
    if not category:return None
    return {'category':category,'stage':stage,'evidence':proof,'certainty':certainty}
