"""Small, shared presentation components; never change measured results."""

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.stable_tasks import SCHEMA_FIELDS, UNIQUE_FIELDS

from harness.reporting.presentation import esc
from harness.web_fetch.models import report_family
from harness.web_fetch.report_i18n import TEXT, pair, t


@dataclass(frozen=True)
class MeasurementPresentation:
    """Presentation choices only; execution and release gates validate the protocol."""

    local_matrix: bool = False
    native_outputs: bool = False
    all_call_latency: bool = False
    explain_unjudged: bool = False


_MEASUREMENT_PRESENTATIONS = {
    "uniform_native_local_measurement": MeasurementPresentation(
        local_matrix=True, native_outputs=True, all_call_latency=True, explain_unjudged=True),
}


def measurement_presentation(dataset):
    """Resolve cohort display behavior without spreading kind checks across pages."""
    return _MEASUREMENT_PRESENTATIONS.get(
        dataset.get("comparison", {}).get("kind"), MeasurementPresentation())


def reference_text(case):
    """Show the frozen reference, or the actual declared acceptance rules."""
    reference=case.get('ground_truth_main_content') or case.get('reference_answer')
    if reference:return reference
    if case.get('task_kind','body')!='body':
        return t('this_task_is_scored_by_resource_or', 'This task is scored by resource or structure rules; full-text F1 is not computed from a short page summary.')
    rules=[(t('minimum_characters', 'Minimum characters'),case.get('min_content_chars',1))]
    for key,label in [('expected_any',t('at_least_one_text_must_match', 'At least one text must match')),('required_facts',t('required_text_groups', 'Required text groups')),
                      ('required_patterns',t('format_and_count_requirements', 'Format and count requirements')),
                      ('body_requirements',t('body_scope_requirements', 'Body-scope requirements')),('acceptance_contract',t('acceptance_contract', 'Acceptance contract'))]:
        if case.get(key):rules.append((label,case[key]))
    if case.get('required_facts'):
        rules.append((t('minimum_required_text_group_coverage', 'Minimum required-text-group coverage'),case.get('fact_coverage_threshold',1.0)))
    return (t('this_task_has_no_full_text_reference', 'This task has no full-text reference and is accepted by these rules:') + f'''\n{"""
""".join((label + '：' + json.dumps(value, ensure_ascii=False, indent=2) for label, value in rules))}''')


def tabs(group, panels):
    buttons=[]; bodies=[]
    for i,(key,label,body) in enumerate(panels):
        pid=f'{group}-{key}'
        buttons.append(f'<button type="button" role="tab" id="tab-{pid}" aria-controls="{pid}" aria-selected="{str(i==0).lower()}" tabindex="{0 if i==0 else -1}" data-tab="{key}" onclick="chooseTab(\'{group}\',\'{key}\')">{escape(label)}</button>')
        bodies.append(f'<div role="tabpanel" id="{pid}" aria-labelledby="tab-{pid}" data-panel="{key}"'+(' hidden' if i else '')+f'>{body}</div>')
    return (f'<div class="tabs" id="{group}"><div class="tab-bar" role="tablist" aria-label="' + t('content_selection', 'Content selection') + f"""" onkeydown="tabKeys(event,'{group}')">{''.join(buttons)}</div>{''.join(bodies)}</div>""")


def cards(items):
    return '<div class="guide-grid">'+''.join(f'<article class="guide-card"><h3>{title}</h3><p>{body}</p></article>' for title,body in items)+'</div>'


def metric_guide():
    return ('<p>' + t('static_site_dynamic_site_popular_site_risk', 'Static-site, dynamic-site, popular-site, risk-control, and document body-text tasks share one scoring pipeline. Their sources and loading behavior affect case requirements, not metric definitions.') + f"""</p>{cards([(t('shared_acceptance_pipeline', 'Shared acceptance pipeline'), t('response_check_for_empty_target_content_verify', 'Response → check for empty target content → verify required text and fields → check list count, types, and uniqueness → aggregate the task verdict. Only checks applicable to that case run; failure details retain unmet requirements. Images and videos use request and delivery checks, with resource URLs or file bytes validated by format.')), (t('task_pass_rate', 'Task pass rate'), t('calls_satisfying_every_acceptance_rule_divided_by', 'Calls satisfying every acceptance rule divided by tasks with usable acceptance evidence. Paragraphs, lists, and fields all count as content requirements;') + '<strong>' + t('no_minimum_f1_threshold', 'No minimum F1 threshold') + '</strong>' + t('request_failures_remain_in_the_denominator_if', '. Request failures remain in the denominator. If any planned call is missing or cannot be judged, the entire report is marked unreliable and cannot be published.')), (t('required_text_coverage', 'Required-text coverage'), t('required_text_groups_matched_divided_by_registered', 'Required text groups matched divided by registered groups, averaged by task. Stable headings, descriptions, field labels, and factual statements may form a check group; volatile lists and prices are not frozen as answers. Tasks without text evidence are not treated as perfect, and coverage is reported separately.')), (t('request_success_rate', 'Request success rate'), t('share_of_calls_for_which_the_interface', 'Share of calls for which the interface returned normally. A successful response does not mean the requested content was retrieved.')), (t('recall_precision_f1_3', 'Recall · precision · F1'), t('a_complete_reference_comparable_in_scope_and', 'A complete reference comparable in scope and content is required; record counts cannot be used to infer full-text F1. Request failures score 0. A successful request that fails the task is still scored against its actual text, so 0 passes does not imply an F1 of 0. Metrics are calculated per task and then averaged; mean F1 is not the harmonic mean of mean recall and mean precision.')), (t('mean_p50_p95', 'Mean · P50 · P95'), t('latency_includes_only_passing_calls_in_this', 'Latency includes only passing calls in this task family. List and body-text tasks are judged by their own declared rules. P50 is the median; P95 is the latency not exceeded by approximately 95% of passing calls.')), (t('resources_files_metadata', 'Resources · files · metadata'), t('resource_match_rate_is_the_share_of', 'Resource match rate is the share of resource tasks returning every required endpoint; file delivery rate is the share of original-file tasks yielding verifiable bytes; metadata pass rate is the share of metadata tasks passing their text checks. Missing byte evidence makes the report incomplete and blocks publication.')), (t('stable_task_contract', 'Stable task contract'), t('validate_registered_stable_content_anchors_stable_headings', 'Validate registered stable content anchors (stable headings, sections, and descriptions) together with field shape, count, uniqueness, and types—not count alone. Frequently changing lists, item titles, and prices are checked against declared shape and types rather than old snapshot values. A pass does not establish the factual accuracy or freshness of those values.'))])}<div class="worked-example"><strong>""" + t('example', 'Example') + '</strong><span>' + t('10_tasks', '10 tasks') + '</span><span>' + t('8_successful_requests_80_request_success_rate', '8 successful requests → 80% request success rate') + '</span><span>' + t('6_task_passes_60_task_pass_rate', '6 task passes → 60% task pass rate') + '</span></div>')


METRIC_DEFINITIONS={
 t('required_text_coverage', 'Required-text coverage'):t('required_text_groups_matched_divided_by_registered_2', 'Required text groups matched divided by registered groups, averaged by task; request failures score 0. Coverage scope is stated before each table. Facts, headings, and descriptions are matched by group. Difficulty varies, so values should not be compared across rankings. This is not full-text recall, and an empty check set is not treated as 100%.'),
 t('request_success_rate', 'Request success rate'):t('calls_with_a_normal_interface_response_divided', 'Calls with a normal interface response divided by all calls; this does not establish content correctness.'),
 t('task_pass_rate', 'Task pass rate'):t('calls_satisfying_every_task_rule_divided_by', 'Calls satisfying every task rule divided by measured tasks. Request failures remain in the denominator; unmeasured tasks are excluded and reported separately. There is no full-text F1 threshold for task acceptance.'),
 t('judgement_coverage', 'Judgement coverage'):t('calls_with_a_pass_fail_judgement_divided', 'Calls with a pass/fail judgement divided by planned calls. This is a report reliability gate, not pass rate or body completeness; any incomplete row blocks publication of rankings.'),
 t('body_text_pass_rate', 'Body-text pass rate'):t('share_of_tasks_meeting_the_declared_body', 'Share of tasks meeting the declared body-text acceptance rules, computed separately from recall, precision and F1.'),
 t('list_verification_rate', 'List verification rate'):t('share_of_list_tasks_meeting_the_stable', 'Share of list tasks meeting the stable content anchors plus the field structure, type, count and uniqueness requirements.'),
 t('structure_pass_rate', 'Structure pass rate'):t('share_of_dynamic_list_tasks_returning_at', 'Share of dynamic-list tasks returning at least 3 named detail entries; factual correctness is not judged.'),
 t('recall', 'Recall'):t('share_of_the_reference_text_recovered_higher', 'Share of the reference text recovered; higher means fewer omissions.'),
 t('precision', 'Precision'):t('share_of_returned_text_that_belongs_to', 'Share of returned text that belongs to the reference; higher means less unrelated text.'),
 'F1':t('compute_the_harmonic_mean_of_recall_and', 'Compute the harmonic mean of recall and precision per task, then average task F1. Tasks without a complete reference are excluded. 0 passes does not imply an F1 of 0.'),
 t('title_recall', 'Title recall'):t('how_many_words_or_characters_of_the', 'How many words or characters of the reference titles for the same detail link were returned. Counts only returned titles with an independent reference; this is not list recall.'),
 t('title_precision', 'Title precision'):t('how_many_words_or_characters_of_the_2', "How many words or characters of the returned titles belong to that entry's reference title. Extra buttons or labels lower the score; page-wide noise is not scored."),
 t('title_f1', 'Title F1'):t('harmonic_mean_of_recall_and_precision_per', 'Harmonic mean of recall and precision per title, then averaged per task and across tasks. Uses independently recorded names or aliases and does not require the entry to remain on the current list; a difference is not itself a content error.'),
 t('title_reference_coverage', 'Title reference coverage'):t('titles_with_an_independent_reference_all_returned', 'Titles with an independent reference / all returned titles; tasks with scorable titles / tasks in this family. Missing titles and unknown identities earn no text score, and a high score cannot replace the task pass rate.'),
 t('resource_match_rate', 'Resource match rate'):t('share_of_resource_tasks_returning_every_required', 'Share of resource tasks returning every required image or playback endpoint.'),
 t('file_delivery_rate', 'File delivery rate'):t('share_of_measured_file_tasks_with_an', 'Share of measured file tasks with an accepted original-file record. Historical calls without byte validation are shown as —, not 0% or lack of product support.'),
 t('metadata_pass_rate', 'Metadata pass rate'):t('share_of_text_metadata_tasks_passing_required', 'Share of text-metadata tasks passing required checks such as title and publisher.'),
 t('mean', 'Mean'):t('mean_latency_of_passing_tasks_in_milliseconds', 'Mean latency of passing tasks, in milliseconds.'),
 'P50':t('median_latency_of_passing_tasks_in_milliseconds', 'Median latency of passing tasks, in milliseconds.'),
 'P95':t('passing_latencies_sorted_ascending_using_the_item', 'Passing latencies sorted ascending, using the item at ⌈95% × sample count⌉, in milliseconds.'),
 t('elapsed_time', 'Elapsed time'):t('elapsed_time_from_call_start_to_completion', 'Elapsed time from call start to completion, including failed calls, in milliseconds.'),
 t('run_id', 'Run ID'):t('stable_local_identifier_derived_from_the_execution', 'Stable local identifier derived from the execution record and start time; reruns remain distinguishable. The provider request ID is shown separately when available.'),
 t('pass_rate', 'Pass rate'):t('share_of_calls_passing_this_task_s', "Share of calls passing this task's registered acceptance rules. Task verdicts remain visible even when a complete full-text reference is unavailable."),
 t('result', 'Result'):t('every_task_in_a_complete_report_has', 'Every task in a complete report has an explicit pass or fail verdict. Diagnostic builds may show unmeasured tasks, but are marked unreliable and cannot be published. A pass does not prove factual accuracy, freshness, or whole-page completeness.'),
 t('failure_class', 'Failure class'):t('first_separate_request_failures_from_successful_requests', 'First separate request failures from successful requests that fail task acceptance, then classify the specific cause. “Access denied” does not mean the site was unavailable.'),
 t('matched_repositories_2', 'Matched repositories'):t('distinct_repositories_in_the_sample_containing_the', 'Distinct repositories in the sample containing the method signature; each repository is counted once.'),
 t('matched_files_2', 'Matched files'):t('distinct_repository_file_pairs_matched_in_the', 'Distinct repository-file pairs matched in the sample; this is not a call count.'),
 t('request', 'Request'):t('whether_the_interface_returned_normally_delivery_of', 'Whether the interface returned normally. Delivery of the requested content is judged separately.'),
 t('http_status', 'HTTP status'):t('recorded_http_status_a_blank_means_the', 'Recorded HTTP status. A blank means the interface did not provide one; success is not inferred from it.'),
 t('output_characters', 'Output characters'):t('character_count_of_the_complete_saved_output', 'Character count of the complete saved output; this is not a measure of useful body text.'),
 t('output_checksum', 'Output checksum'):t('sha_256_of_the_saved_output_used', 'SHA-256 of the saved output, used to detect content changes.'),
 t('named_detail_records', 'Named detail records'):t('number_of_distinct_identified_records_each_with', 'Number of distinct identified records, each with a name and a same-site detail URL.'),
}


def metric_legend(headers):
    entries=[(h,METRIC_DEFINITIONS[h]) for h in headers if h in METRIC_DEFINITIONS]
    if not entries:return ''
    missing=t('means_no_attributable_method_specific_adoption_count', '“—” means no attributable method-specific adoption count was performed; 0 means no match in this sample.') if t('matched_repositories_2', 'Matched repositories') in headers else t('means_not_applicable_no_reference_or_no', '“—” means not applicable, no reference, or no computable sample; it does not mean 0.')
    return ('<div class="metric-help" aria-label="' + t('metric_definitions_for_this_table', 'Metric definitions for this table') + f"""" data-i18n-attrs="aria-label"><dl>{''.join(('<div><dt>' + escape(k) + '</dt><dd>' + escape(v) + '</dd></div>' for k, v in entries))}</dl><p class="source-note">{missing}</p></div>""")


TRACK_CONTEXT = json.loads((Path(__file__).resolve().parent / 'data/report-track-context.json').read_text(encoding='utf-8'))


def media_population_text(track, cases):
    """Describe the declared population, without assuming a historical cohort."""
    selected = [case for case in (cases or {}).values() if report_family(case) == track]
    if track == 'media_file':
        return (t('the_input_points_directly_at_a_media', "The input points directly at a media file, {count} tasks in total: the original bytes must be obtained and meet each task's registered format, length and file-fingerprint requirements.", count=len(selected)),
                f'The input points directly to a media file. Across {len(selected)} tasks, return original bytes satisfying each registered format, length and fingerprint requirement.')
    resources = sum(case.get('task_kind', 'body') == 'resource' for case in selected)
    other = len(selected) - resources
    return (t('the_input_is_a_web_page_tasks', 'The input is a web page, {count} tasks in total: {resources} require resource URLs and {other} require registered text or structure. Accepting a URL does not mean the media file was downloaded.', count=len(selected), resources=resources, other=other),
            f'The input is a webpage. Across {len(selected)} tasks, {resources} require resource URLs and {other} require registered text or structure. URL acceptance does not establish media-file download.')


def bilingual_text(zh, en):
    return '<span data-report-en="' + escape(en, quote=True) + '">' + escape(zh) + '</span>'


def task_pass_guide(track, base='', cases=None):
    rules = {
        'dynamic': t('checks_the_initial_load_scope_stable_content', 'Checks the initial-load scope: stable content anchors (stable titles, sections, descriptions) plus required entry fields, count, uniqueness and type jointly decide the task. Frequently changing lists, entry titles and prices are checked by declared structure and type, not pinned to old snapshot values. An HTTP page shell does not pass; rendering, embedded data and public APIs are all valid routes. Full-text quality is computed separately only with a complete, comparable reference.'),
        'web': t('the_request_must_succeed_and_satisfy_minimum', 'The request must succeed and satisfy minimum length, every required checkpoint, significant marker, and structure requirement. Verification or error notices do not independently determine acceptance. Completeness, purity, and F1 are separate metrics, not pass thresholds.'),
        'document': t('document_body_text_is_accepted_by_request', 'Document body text is accepted by request status, minimum length and all content checks; a download link alone does not pass. Verification or error notices are diagnostics only. F1 is computed separately and is not a pass threshold.'),
        'image': t('image_bearing_pages_must_return_every_specified', 'Image-bearing pages must return every specified image URL. A direct PNG task must deliver file bytes that pass format, length, and target SHA-256 validation.'),
        'video': t('depending_on_the_task_return_every_playback', 'Depending on the task, return every playback endpoint, validate title and publisher, or deliver an original file that passes format, length, and target SHA-256 checks. A URL is not a file, and none of these tasks measure visual understanding.'),
        'web_media': media_population_text('web_media', cases)[0],
        'media_file': media_population_text('media_file', cases)[0],
        'challenge': t('the_request_must_succeed_and_return_at', 'The request must succeed and return at least 3 named detail links with complete fields, correct types, unique records, and URLs that follow the target-site rules. Stable headings, sections, and descriptions with independent evidence must also match. Frequently changing lists and prices are not frozen to old values. A pass means these retrieval requirements were met; it does not establish that volatile values are accurate or current.'),
        'risk_control': t('the_request_must_succeed_and_return_the', 'The request must succeed and return the specified target page. Body-region rules, composite rules, and stable text marked as required must pass. Diagnostic stable text only checks retrieval scope and does not affect acceptance. An article cannot return only its title or opening. A list must meet its declared field, real-link, path, deduplication, and quantity requirements. Verification pages, empty responses, and page shells fail.'),
    }
    rules.update({key: value['pass_rule'] for key, value in TRACK_CONTEXT.items() if 'pass_rule' in value})
    text = rules.get(track, t('each_acceptance_rule_registered_for_the_task', 'Each acceptance rule registered for the task is checked in turn.'))
    missing_translations = {
        'dynamic': 'Validate the initial-load scope: stable text and required record fields, counts, uniqueness and types jointly determine acceptance. Volatile members, titles and prices are checked structurally rather than frozen to old values. An HTTP shell is insufficient; rendering, embedded data and public APIs are valid routes. Full-text quality is separate and requires a complete comparable reference.',
        'document': 'Document text must satisfy request status, minimum length and every registered content check; a download link alone is insufficient. Verification and error hints are diagnostic. F1 is reported separately and is not a pass threshold.',
    }
    text_en = TEXT.get(text, missing_translations.get(track, 'Check each registered task requirement.'))
    coverage=[c['body_requirements'] for c in (cases or {}).values() if c.get('body_requirements')]
    coverage_note = ''
    if coverage:
        coverage_note = ('<span data-report-en="Registered body-coverage tasks must meet each task&#39;s regional thresholds and minimum coverage floors; a title or opening excerpt alone is insufficient."> ' + t('tasks_with_registered_body_coverage_rules_are', "Tasks with registered body-coverage rules are accepted by each task's comparison unit, region threshold and required region count; returning only the title or opening does not pass.") + '</span>')
        if any(r.get('unit')=='region_compact_hexagram_recall' for r in coverage):
            text+=(' ' + t('the_character_six_gram_rule_ignores_layout', "The character six-gram rule ignores layout whitespace and keeps text order; each region's requirement is on the task detail."))
    for c in (cases or {}).values():
        fields=c.get('acceptance_contract',{}).get('field_minimum_records',{})
        if fields:
            labels={'financing':t('installments', 'Installments'),'shipping':t('shipping', 'Shipping')}
            text+=(' ' + t('field_coverage_required_for_each_belonging_to', 'Field coverage required for {name}: {join}, each belonging to a complete and unique record.', name=c['name'], join='、'.join((f'{labels.get(k, k)}至少 {v} 条' for k, v in fields.items()))))
            text_en+=' '+c['name']+' field coverage: '+', '.join(f'{k}: at least {v} records' for k,v in fields.items())+'; every field must belong to a complete, unique record.'
    context = TRACK_CONTEXT.get(track)
    if track in {'web_media', 'media_file'}:
        text, text_en = media_population_text(track, cases)
        context = {**context, 'flow_note': text}
    kinds = {c.get('task_kind', 'body') for c in (cases or {}).values()
             if c.get('track', 'web') == track}
    if track == 'web' and kinds and kinds != {'body'}:
        output_rules = {
            'body': rules['web'],
            'structure': t('structured_content_is_checked_against_the_task', 'Structured content is checked against the task-defined fields, types, counts, uniqueness and link rules.'),
            'resource': t('resource_discovery_is_checked_against_the_task', 'Resource discovery is checked against the task-defined target addresses and answer fields.'),
            'file': t('file_delivery_requires_original_bytes_that_pass', 'File delivery requires original bytes that pass format, length and any declared target SHA-256 checks; text or links cannot replace a file.'),
        }
        text = ' '.join(output_rules[kind] for kind in sorted(kinds) if kind in output_rules)
        english_outputs = {
            'body': TEXT[rules['web']],
            'structure': 'Structured output must satisfy registered fields, types, counts, uniqueness and link rules.',
            'resource': 'Resource discovery must return the registered target addresses and answer fields.',
            'file': 'File delivery requires original bytes with valid format, length and any registered SHA-256; text or links cannot replace the file.',
        }
        text_en = ' '.join(english_outputs[k] for k in sorted(kinds) if k in english_outputs)
        context = None
    if not context:
        return ('<div class="pass-contract"><strong>' + t('what_counts_as_a_pass', 'What counts as a pass?') + f'</strong><p><span data-report-en="{escape(text_en, quote=True)}">{escape(text)}</span>{coverage_note}</p></div>')
    example_link = ''
    if context.get('case_id') in (cases or {}):
        example_link = (f''' <a href="{escape(base + 'datasets/' + context['case_id'] + '.html')}">''' + t('view_task', 'View task →') + '</a>')
    return ((f'''<div class="track-guide" data-track-guide="{escape(track)}"><p class="track-meaning">{escape(context['meaning'])}</p><figure class="track-flow" aria-label="''' + t('typical_processing_flow', 'Typical processing flow') + '"><figcaption>' + t('typical_processing_flow', 'Typical processing flow') + f"""</figcaption><ol>{''.join(('<li><span>' + escape(step) + '</span></li>' for step in context['flow']))}</ol><p>{(bilingual_text(text, text_en) if track in {'web_media', 'media_file'} else escape(context['flow_note']))}</p></figure><div class="pass-contract"><strong>""" + t('what_counts_as_a_pass', 'What counts as a pass?') + f'</strong><p><span data-report-en="{escape(text_en, quote=True)}">{escape(text)}</span>{coverage_note}</p></div><details class="track-more"><summary>' + t('example_and_evaluation_boundary', 'Example and evaluation boundary') + '</summary><dl><div><dt>' + t('example_2', 'Example') + f"</dt><dd>{escape(context['example'])}{example_link}</dd></div><div><dt>" + t('scope_and_limits', 'Scope and limits') + f'''</dt><dd>{escape(context['boundary'])}</dd></div></dl><a href="{escape(base + 'methodology/index.html#task-examples-' + track)}">''' + t('examples_and_complete_acceptance_rules', 'Examples and complete acceptance rules →') + '</a></details></div>'))


def examples(cases=None):
    items=[
        ('web',t('general_web_text', 'General web text'),[
            (t('task', 'Task'),t('open_the_books_to_scrape_home_page', 'Open the Books to Scrape home page and retrieve the book-list body on that page.')),
            (t('reference_answer', 'Reference answer'),t('independently_retain_the_complete_page_listing_and', 'Independently retain the complete page listing and check title, price, and availability—for example, the first item A Light in the Attic, £51.77, In stock.')),
            (t('how_it_is_measured', 'How it is measured'),t('calculate_recall_precision_and_f1_against_the', 'Calculate recall, precision, and F1 against the complete body; separately check length, required facts, and error-page signals.')),
            (t('what_counts_as_a_pass_2', 'What counts as a pass'),t('all_required_facts_and_body_rules_pass', 'All required facts and body rules pass. Acceptance does not mean every character was recovered; consult the full-text metrics.')),
        ]),
        ('dynamic',t('rendered_web_text', 'Rendered web text'),[
            (t('task', 'Task'),t('read_each_task_s_declared_initial_load', "Read each task's declared initial-load scope—for example, the first 20 GraphQL reviews, 16 React catalog products, 20 Swagger endpoint summaries, or 14 category names inside an iframe. Each task is scored independently.")),
            (t('task_contract', 'Task contract'),t('observe_the_initial_load_scope_independently_and', 'Observe the initial-load scope independently and register stable content anchors (stable headings, sections, and descriptions) plus record count and field structure, such as product name, numeric price, and visible financing fields. Only frequently changing item titles and prices are excluded from permanent answers; stable content remains checked.')),
            (t('how_it_is_measured', 'How it is measured'),t('check_stable_content_anchors_together_with_each', "Check stable content anchors together with each record's field ownership, count, uniqueness, and types, allowing declared field order and common layouts. A complete comparable text reference may support a separate overlap diagnostic, but does not establish the factual accuracy or freshness of volatile values.")),
            (t('what_counts_as_a_pass_2', 'What counts as a pass'),t('the_request_succeeds_and_every_body_rule', 'The request succeeds and every body rule for the task passes. The contract covers only the declared initial records and does not imply scrolling, pagination, or clicks. A missing planned call or verdict makes the report incomplete and blocks publication.')),
        ]),
        ('document',t('document_text', 'Document text'),[
            (t('task', 'Task'),t('use_the_attention_is_all_you_need', 'Use the Attention Is All You Need PDF as an example and read the complete paper; DOCX, TXT, and embedded-document pages are tested separately.')),
            (t('reference_answer', 'Reference answer'),t('independently_extract_body_text_from_every_page', 'Independently extract body text from every page, paragraph, and table in the original file, and retain the file.')),
            (t('how_it_is_measured', 'How it is measured'),t('provide_the_original_document_url_and_use', "Provide the original document URL and use the body text returned by the browser's official interface. A task fails if the method returns only raw PDF, DOCX, or other file bytes; the evaluation harness does not parse the file on its behalf. Stop timing once readable body text is available, then compare it with the full reference text for recall, precision, and F1, and check the title, key terms, and sections.")),
            (t('what_counts_as_a_pass_2', 'What counts as a pass'),(t('the_document_body_is_required_a_paper', 'The document body is required; a paper download URL alone fails.') + '<a href="../datasets/document-01.html">' + t('view_the_complete_reference_and_per_method', 'View the complete reference and per-method results →') + '</a>')),
        ]),
        ('web_media',t('embedded_webpage_media', 'Embedded webpage media'),[
            (t('task', 'Task'),bilingual_text(*media_population_text('web_media', cases))),
            (t('reference_answer', 'Reference answer'),bilingual_text(*pair('each_task_independently_declares_target_resource_urls', 'Each task independently declares target resource URLs, text or structural requirements.'))),
            (t('how_it_is_measured', 'How it is measured'),t('url_tasks_check_whether_the_returned_value', 'URL tasks check whether the returned value contains the target URL and do not require a separate media download. The text task checks the registered facts.')),
            (t('what_counts_as_a_pass_2', 'What counts as a pass'),(t('return_the_url_or_text_required_by', 'Return the URL or text required by the task. A URL alone does not deliver the file or show understanding of images, video, captions, or audio.') + '<a href="../datasets/image-01.html">' + t('view_web_media_references_and_results', 'View web-media references and results →') + '</a>')),
        ]),
        ('media_file',t('direct_media_files', 'Direct media files'),[
            (t('task', 'Task'),bilingual_text(*media_population_text('media_file', cases))),
            (t('reference_answer', 'Reference answer'),t('independently_saved_file_format_length_and_sha', 'Independently saved file format, length, and SHA-256.')),
            (t('how_it_is_measured', 'How it is measured'),t('check_the_returned_bytes_against_the_required', 'Check the returned bytes against the required file signature, length, and SHA-256. Binary files are not converted to Markdown.')),
            (t('what_counts_as_a_pass_2', 'What counts as a pass'),(t('the_method_must_return_the_matching_file', 'The method must return the matching file bytes. Repeating the input URL or returning page text does not pass. These tasks do not test media understanding.') + '<a href="../datasets/image-10.html">' + t('view_original_file_references_and_results', 'View original-file references and results →') + '</a>')),
        ]),
        ('challenge',t('popular_site_text', 'Popular-site text'),[
            (t('task', 'Task'),t('for_a_justwatch_example_retrieve_the_declared', 'For a JustWatch example, retrieve the declared number of distinct shows with names and detail endpoints. Task verdict and latency enter the popular-site primary table. A missing complete reference affects only full-text metrics and does not hide the verdict.')),
            (t('reference_basis', 'Reference basis'),t('an_independent_page_supplies_stable_content_anchors', "An independent page supplies stable content anchors and record-structure requirements: show name and a same-site detail endpoint. The day's show list is not frozen.")),
            (t('how_structure_is_measured', 'How structure is measured'),t('require_at_least_3_named_same_site', 'Require at least 3 named same-site detail URLs, deduplicated by address. Bare URLs, navigation buttons, and login prompts do not count. Frequently changing names, ranks, prices, and IDs are not frozen to old snapshot values, but registered stable content is still required.')),
            (t('what_counts_as_a_pass_2', 'What counts as a pass'),(t('stable_content_anchors_and_every_required_field', 'Stable content anchors and every required field, type, uniqueness, and count check pass. Acceptance does not establish the factual accuracy or freshness of volatile values; recall, precision, and F1 are — without a complete comparable reference.') + '<a href="../datasets/challenge-10-justwatch.html">' + t('view_reference_and_measured_results', 'View reference and measured results →') + '</a>')),
        ]),
        ('risk_control',t('risk_control_page_body_text', 'Risk-control page body text'),[
            (t('task', 'Task'),t('open_the_specified_url_and_retrieve_the', 'Open the specified URL and retrieve the target body, lists, and real links loaded automatically, without clicking, typing, scrolling, pagination, or login.')),
            (t('reference_basis', 'Reference basis'),t('use_independently_saved_target_page_evidence_to', 'Use independently saved target-page evidence to register stable text, complete body regions, and name-link, path, field, deduplication, and quantity requirements. Stable text is explicitly marked as required or diagnostic.')),
            (t('how_it_is_measured', 'How it is measured'),t('check_the_request_and_returned_content_then', 'Check the request and returned content, then apply the declared stable text, body coverage and composite requirements. Diagnostic text reports matches without changing the verdict.')),
            (t('what_counts_as_a_pass_2', 'What counts as a pass'),t('body_and_list_rules_and_stable_text', 'Body and list rules and stable text marked as required must pass; diagnostic text does not affect the verdict. An article with only a title or opening, a list below its quantity threshold, or links that do not match real paths fails.')),
        ]),
    ]
    order = ['web', 'dynamic', 'challenge', 'risk_control', 'document', 'web_media', 'media_file']
    items.sort(key=lambda item: order.index(item[0]))
    return tabs('task-examples',[(k,l,cards(c)) for k,l,c in items])


def task_contract_guide():
    return cards([
        (t('task_contract', 'Task contract'),t('within_the_declared_scope_validate_stable_content', 'Within the declared scope, validate stable content anchors (stable headings, sections, and descriptions), record count, required fields, uniqueness, and field types. Each anchor is marked as required or diagnostic: required anchors affect acceptance, while diagnostic anchors only check retrieval scope. Frequently changing lists, item titles, prices, and ranks are checked by declared structure, type, and count rather than old snapshot values; this does not establish that those values are accurate or current.')),
        (t('verdict_to_rule_binding', 'Verdict-to-rule binding'),t('task_results_are_bound_to_registered_required', 'Task results are bound to registered required anchors, field requirements, and count ranges; diagnostic anchors are shown separately. Raw outputs and scoring rules are retained for item-by-item review.')),
        (t('integrity_and_reference_modes', 'Integrity and reference modes'),t('raw_output_hashes_and_evidence_integrity_checks', 'Raw-output hashes and evidence-integrity checks are retained. A validated stable_contract uses data invariants and does not depend on snapshot age; exact_snapshot references remain governed by their own protocol. The modes cannot be mixed, and the report does not claim one capture window for every call.')),
        (t('independent_full_text_diagnostics', 'Independent full-text diagnostics'),t('recall_precision_and_f1_require_a_complete', 'Recall, precision, and F1 require a complete reference comparable in target scope and content; record count cannot imply full-text F1. Without such a reference, full-text metrics are shown as — while task pass rate and latency remain. Stable content anchors marked as required affect acceptance; diagnostic anchors only check retrieval scope. Text overlap does not establish factual accuracy or freshness, and list changes alone do not expire a stable task contract.')),
    ])


def list_rule_description(c):
    if c.get('acceptance_contract'):
        return stable_rule_description(c)
    contract = c.get('source_benchmark', {})
    minimum = contract.get('minimum_entries', 3)
    if contract.get('verification_scope') == 'first_three_name_detail_pairs':
        return t('this_task_is_checked_by_name_and', 'This task is checked by name-and-link identity: at least {minimum} named detail entries, and the first {minimum} returned must have independent content evidence. No fixed daily list, order or price is required. Unconfirmed does not mean wrong.', minimum=minimum)
    return t('this_task_is_checked_against_registered_stable', 'This task is checked against registered stable content anchors and field and count rules, with at least {minimum} distinct named detail entries. Stable titles, sections and descriptions are checked against registered content; frequently changing entry titles and prices are checked only for the declared structure and type. A pass does not prove that changing values are accurate or current.', minimum=minimum)


SCHEMA_LABELS = {
    'table_row':'表格行',
    'earthquake':t('earthquake_entries', 'Earthquake entries'),
    'quote':t('quotation_list', 'Quotation list'), 'product':t('product_list', 'Product list'), 'catalog_product':t('product_catalog', 'Product catalog'),
    'review':t('review_list', 'Review list'), 'name_description':t('names_and_descriptions', 'Names and descriptions'), 'api_operation':t('interface_summaries', 'Interface summaries'),
    'taxonomy_family':t('family_name_list', 'Family-name list'), 'named_link':t('named_detail_records', 'Named detail records'),
    'linked_product':t('named_product_details', 'Named product details'), 'page_identity':t('target_page_check', 'Target-page check'),
}
FIELD_REQUIREMENTS = {
    'magnitude':(t('magnitude', 'Magnitude'),t('a_valid_numeric_magnitude_not_pinned_to', "A valid numeric magnitude, not pinned to any one earthquake's value.")),
    'location':(t('location', 'Location'),t('non_empty_location_text_from_the_same', 'Non-empty location text from the same earthquake entry; placeholders do not count.')),
    'time':(t('time_of_occurrence', 'Time of occurrence'),t('contains_a_valid_date_and_time_to', 'Contains a valid date and time to the second; ISO times and displayed UTC offsets are supported.')),
    'depth':(t('depth', 'Depth'),t('a_value_with_a_km_unit_from', 'A value with a km unit from the same earthquake entry.')),
    'quote':(t('quote', 'Quote'),t('at_least_3_characters_containing_text_not', 'At least 3 characters containing text; not just a URL, amount or placeholder.')),
    'author':(t('author', 'Author'),t('at_least_2_characters_containing_text_not', 'At least 2 characters containing text; not a placeholder.')),
    'tags':(t('tags', 'Tags'),t('non_empty_text_of_at_least_2', 'Non-empty text of at least 2 characters; not a placeholder.')),
    'name':(t('name', 'Name'),t('at_least_2_characters_containing_text_not_2', 'At least 2 characters containing text; not just a URL, amount or placeholder.')),
    'title':(t('title', 'Title'),t('named_text_of_at_least_2_characters', 'Named text of at least 2 characters; not just a URL or navigation button.')),
    'price':(t('price', 'Price'),t('a_non_negative_amount_with_a_currency', 'A non-negative amount with a currency mark and at most 2 decimals; $, £, €, ¥ or USD, EUR, GBP, CNY are supported.')),
    'description':(t('description', 'Description'),t('at_least_3_characters_containing_text_not', 'At least 3 characters containing text; not just a URL, amount or placeholder.')),
    'reviews':(t('review_count', 'Review count'),t('a_non_negative_integer_optionally_labeled_review', 'A non-negative integer, optionally labeled review / reviews.')),
    'date':('日期',t('a_valid_yyyy_mm_dd_calendar_date', 'A valid YYYY-MM-DD calendar date.')),
    'text':(t('review_text', 'Review text'),t('at_least_3_characters_containing_text_not', 'At least 3 characters containing text; not just a URL, amount or placeholder.')),
    'method':(t('request_method', 'Request method'),t('get_post_put_patch_delete_head_options', 'GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS, TRACE or CONNECT.')),
    'path':(t('endpoint_path', 'Endpoint path'),t('an_endpoint_path_starting_with_a_single', 'An endpoint path starting with a single /; path parameters are allowed; not a complete URL.')),
    'summary':(t('endpoint_description', 'Endpoint description'),t('at_least_3_characters_containing_text_not', 'At least 3 characters containing text; not just a URL, amount or placeholder.')),
    'family':(t('family_name', 'Family name'),t('made_of_english_letters_and_ending_in', 'Made of English letters and ending in idae.')),
    'url':(t('details_2', 'Details'),t('an_http_s_link_matching_this_task', "An HTTP(S) link matching this task's allowed domains and detail paths; no username or password.")),
}


def task_instruction(c):
    contract = c.get('acceptance_contract')
    if not contract:
        reference = c.get('ground_truth_main_content', '')
        if c.get('track') == 'document' and 0 < len(reference) < 100:
            return t('read_all_visible_body_text_of_this', 'Read all visible body text of this minimal document. This sample only checks that short document text can be obtained; it does not represent multi-page, table or appendix capability.')
        return c.get('task_prompt', '')
    if contract['schema'] == 'page_identity':
        return t('checks_the_declared_page_section_or_query', 'Checks the declared page, section or query identity; the changing entry list is not a fixed answer.')
    if contract['schema'] == 'body_text':
        instruction = t('extract_the_body_text_within_the_page', "Extract the body text within the page's declared scope.")
        if contract.get('stable_text_anchors') or contract.get('page_patterns'):
            instruction += t('also_checks_the_stable_text_and_page', 'Also checks the stable text and page requirements listed below.')
        if c.get('body_requirements'):
            instruction += t('the_body_text_must_also_meet_the', 'The body text must also meet the region coverage requirements below.')
        return instruction
    fields = '、'.join(FIELD_REQUIREMENTS[f][0] for f in SCHEMA_FIELDS[contract['schema']])
    if contract['schema'] == 'table_row':
        fields = '、'.join(col['label'] for col in contract.get('table_columns', []))
    instruction = f'取得至少 {contract["minimum_records"]} 条完整、不重复的记录，每条包含{fields}。'
    if contract.get('stable_text_anchors') or contract.get('page_patterns'):
        instruction += t('also_checks_the_stable_text_and_page', 'Also checks the stable text and page requirements listed below.')
    return (f'{instruction}' + t('changing_fields_are_checked_by_declared_format', 'Changing fields are checked by declared format, not pinned to old values.'))


def stable_rule_description(c):
    contract = c['acceptance_contract']
    if contract['schema'] == 'body_text':
        role = contract.get('stable_text_role', 'required')
        anchor_rule = (t('stable_text_only_checks_the_fetch_scope', 'Stable text only checks the fetch scope and does not affect the result;')
                       if role == 'diagnostic' else t('registered_stable_text_must_match', 'Registered stable text must match;'))
        return ((t('page_body_text_is_checked_by_the', 'Page body text is checked by the registered body rules;') + f'{anchor_rule}' + t('volatile_content_such_as_news_dates_carousel', 'Volatile content such as news, dates, carousel cards and repository status is not a fixed answer. Historical full snapshots are diagnostics only and stay out of the full-text quality summary.')))
    if contract['schema'] == 'page_identity':
        return t('target_page_check_every_specified_stable_text', 'Target-page check: every specified stable-text group must match; no fixed entry count is required.')
    maximum = contract.get('maximum_records')
    limit = f"至少 {contract['minimum_records']} 条{(f'，最多 {maximum} 条' if maximum is not None else '')}"
    return (t('complete_and_unique_records_stable_titles_sections', '{get}: {limit} complete and unique records. Stable titles, sections and descriptions are checked against registered stable-text anchors and page patterns, along with entry field attribution, type, completeness and uniqueness. Frequently changing lists and prices are not pinned to old snapshot values; a pass does not prove those changing values are real or current.', get=SCHEMA_LABELS.get(contract['schema'], contract['schema']), limit=limit))


def stable_contract_details(c, *, evidence_root=None):
    contract = c['acceptance_contract']
    schema = contract['schema']
    fields = ''.join('<li><strong>'+escape(FIELD_REQUIREMENTS[f][0])+'</strong>：'+escape(FIELD_REQUIREMENTS[f][1])+'</li>' for f in SCHEMA_FIELDS[schema])
    unique = '＋'.join(FIELD_REQUIREMENTS[f][0] for f in UNIQUE_FIELDS[schema])
    if schema == 'table_row':
        types = {
            'text': t('non_empty_text', 'Non-empty text'),
            'token': t('non_empty_field_in_the_declared_format', 'Non-empty field in the declared format'),
            'integer': t('integer', 'Integer'),
            'number': t('number', 'Number'),
            'currency_usd': t('us_dollar_amount', 'US-dollar amount'),
            'date_dmy': t('valid_day_month_year_date', 'Valid day/month/year date'),
            'date_ordinal_en': t('valid_english_ordinal_date', 'Valid English ordinal date'),
        }
        fields = ''.join('<li><strong>'+escape(col['label'])+'</strong>：'+escape(types[col['type']])+'</li>'
                         for col in contract.get('table_columns', []))
        labels = {col['name']: col['label'] for col in contract.get('table_columns', [])}
        unique = '＋'.join(labels.get(key, key) for key in contract.get('record_identity_fields', []))
    requirements = ('' if schema in {'body_text','page_identity'} else
        ('<h3>' + t('required_fields_and_constraints', 'Required fields and constraints') + f'</h3><ul>{fields}</ul><p>' + t('fields_must_belong_to_one_record_and', 'Fields must belong to one record and must not be stitched across entries. Uniqueness is checked by {unique}.', unique=escape(unique)) + '</p>'))
    if schema == 'name_description':
        requirements += ('<p>' + t('a_description_must_not_merely_repeat_the', 'A description must not merely repeat the name.') + '</p>')
    if schema in {'product','catalog_product'}:
        requirements += ('<p>' + t('if_installment_or_shipping_fields_are_returned', 'If installment or shipping fields are returned, installments must be a positive whole number of periods with a currency amount; shipping must be free or a currency amount.') + '</p>')
    if schema in {'named_link','linked_product'}:
        requirements += ('<p>' + t('allowed_domains', 'Allowed domains:') + f"{escape('、'.join(contract.get('hosts', [])))}" + t('detail_paths', '; detail paths:') + f"<code>{escape(contract.get('entry_path', ''))}</code>" + t('entries_are_deduplicated_by_domain_and_path', '. Entries are deduplicated by domain and path.') + '</p>')
    if schema == 'linked_product':
        requirements += ('<p>' + t('the_price_must_be_within_the_declared', "The price must be within the declared scope of the same product detail entry and match this task's currency and price format.") + '</p>')
    patterns = ''.join('<li><code>'+escape(p)+'</code></li>' for p in contract.get('page_patterns', []))
    anchors = ''.join('<li>'+escape(a['id'])+'：'+escape(' / '.join(a['alternatives']))+(t('a_text_line_containing_a_product_entry', ' (a text line containing a product entry title cannot substitute)') if a.get('match_scope')=='non_record_lines' else '')+'</li>'
                      for a in contract.get('stable_text_anchors', []))
    annotation = (c.get('mutable_body_policy',{}).get('stable_annotation',{})
                  or c.get('source_benchmark',{}).get('stable_annotation',{}))
    evidence = ((c.get('stable_text_evidence') or {}).get('path')
                or annotation.get('anchor_evidence'))
    if evidence and evidence_root is not None and not (evidence_root/evidence).is_file():
        evidence = None
    provenance = (('<p>' + t('independent_text_evidence', 'Independent text evidence:') + f'<a href="../{escape(evidence, quote=True)}">' + t('view_source_text_for_the_declared_scope', 'View source text for the declared scope') + '</a>' + t('these_checks_confirm_that_text_was_kept', '. These checks confirm that text was kept; they do not prove the whole body is complete, and current list members are not the answer.') + '</p>')) if evidence else (('<p>' + t('fixed_text_checks_are_listed_below_this', 'Fixed-text checks are listed below; this report does not attach the original page file from early annotation.') + '</p>') if annotation and anchors else ('<p>' + t('this_task_has_no_registered_stable_text', 'This task has no registered stable-text basis; text-check coverage is not computed, and the list rules already executed and their results remain.') + '</p>') if annotation else '')
    anchors_required = contract.get('stable_text_role', 'required') != 'diagnostic'
    anchor_note = (t('at_least_one_accepted_variant_must_match', 'At least one accepted variant must match in every group; all groups are required.') if anchors_required else
                   t('these_texts_only_verify_retrieval_scope_missing', 'These texts only verify retrieval scope; missing them does not fail the task.'))
    return ('<p>'+escape(stable_rule_description(c))+'</p>'+requirements
            + provenance
            + (('<h3>' + t('stable_text_anchors', 'Stable text anchors') + f'</h3><p>{anchor_note}</p><ul>{anchors}</ul>') if anchors else '')
            + (('<h3>' + t('page_patterns', 'Page patterns') + '</h3><p>' + t('all_of_these_patterns_must_match', 'All of these patterns must match.') + f'</p><ul>{patterns}</ul>') if patterns else ''))


def basis_label(c):
    if c.get('acceptance_contract'):return t('stable_content_field_structure_contract', 'Stable content + field-structure contract')
    if c.get('task_kind')=='structure':return t('name_and_detail_identity_check', 'Name and detail identity check') if c.get('source_benchmark',{}).get('verification_scope')=='first_three_name_detail_pairs' else t('registered_content_and_field_rules', 'Registered content and field rules')
    if c.get('task_kind')=='file':return t('original_file_2', 'Original file')
    resources=c.get('expected_resources',[])
    if resources:return t('required_resources', '{count} required resources', count=len(resources))
    n=len(c.get('required_facts',[]));p=len(c.get('required_patterns',[]))
    return ' · '.join(([t('text_checks', '{n} text checks', n=n)] if n else [])+([f'列表 {p} 项'] if p else [])) or t('not_configured_2', 'Not configured')


def reference_label(c):
    annotation = (c.get('mutable_body_policy',{}).get('stable_annotation',{})
                  or c.get('source_benchmark',{}).get('stable_annotation',{}))
    if c.get('acceptance_contract') and annotation.get('structural_only_fields') and c.get('ground_truth_main_content'):return t('snapshot_text_diagnostic_includes_non_invariant_fields', 'Snapshot-text diagnostic; includes non-invariant fields')
    if c.get('ground_truth_main_content'):return t('complete_body_reference', 'Complete body reference')
    if c.get('task_kind')=='structure':return t('list_task_evidence_no_complete_text_reference', 'List-task evidence; no complete text reference')
    if c.get('task_kind')=='file':return t('original_file_2', 'Original file')
    if c.get('expected_resources'):return t('resource_manifest', 'Resource manifest')
    if c.get('track')=='video' and c.get('required_facts'):return t('text_metadata', 'Text metadata')
    return t('no_complete_reference', 'No complete reference')


def comparable_body_reference(c):
    """Exclude snapshots whose lexical scope still includes nonliteral fields.

    Keep the measured values in attempt details; do not silently use them as
    invariant text quality or replace them with partial-anchor precision.
    """
    annotation = (c.get('mutable_body_policy',{}).get('stable_annotation',{})
                  or c.get('source_benchmark',{}).get('stable_annotation',{}))
    return bool(c.get('ground_truth_main_content')) and not bool(
        c.get('acceptance_contract') and annotation.get('structural_only_fields'))


def reference_status(cases):
    full=sum(bool(c.get('ground_truth_main_content')) for c in cases)
    dynamic=sum(c.get('task_kind')=='structure' for c in cases)
    overview=cards([
        (t('tasks_with_complete_references', '{full} tasks with complete references', full=full),t('full_text_metrics_require_a_complete_comparable', 'Full-text metrics require a complete, comparable body reference. Image and video tasks are checked as resources, files, or text metadata.')),
        (t('list_tasks_2', '{dynamic} list tasks', dynamic=dynamic),t('acceptance_uses_registered_stable_content_anchors_plus', "Acceptance uses registered stable content anchors plus fields, types, counts, and uniqueness. Task pass rate and latency enter the corresponding primary table; the day's list membership is not frozen.")),
        (t('snapshot_scope', 'Snapshot scope'),t('zhihu_30_trending_entries_dianping_15_merchants', 'Zhihu: 30 trending entries; Dianping: 15 merchants; JustWatch: 40 shows. Only list fields are published; account information and original login pages are excluded.')),
        (t('volatile_content_validation_boundary', 'Volatile-content validation boundary'),t('stable_headings_sections_and_descriptions_are_checked', 'Stable headings, sections, and descriptions are checked against registered content. Frequently changing lists and prices are checked only for declared field structure, types, count, and uniqueness; this does not establish their factual accuracy or freshness. Missing complete references do not suppress task verdicts.')),
    ])
    links=[f'<a href="{c["id"]}.html">{escape(c["name"])}</a>' for c in cases if c['id'] in {'challenge-04','challenge-06','challenge-10-justwatch'}]
    return overview+(('<details><summary>' + t('reference_examples', 'Reference examples') + f"</summary><p>{' · '.join(links)}</p><p>" + t('each_task_records_its_source_snapshot_and', 'Each task records its source snapshot and independent-access conditions. Restricted access and site unavailability are judged separately.') + '</p></details>') if links else '')


def sources_panel():
    return ('<p>' + t('the_methodology_draws_on_the_following_public', 'The methodology draws on the following public projects. This report uses its own live source snapshots and does not present official scores from those projects.') + f"""</p>{cards([('<a href="https://github.com/Murrough-Foley/web-content-extraction-benchmark">WCXB</a>', t('reference_for_complete_body_text_and_text', 'Reference for complete body text and text-overlap scoring.')), ('<a href="https://github.com/scrapinghub/article-extraction-benchmark">Scrapinghub</a>', t('precision_recall_and_f1_for_body_text', 'Precision, recall, and F1 for body-text extraction.')), ('<a href="https://mozilla.github.io/pdf.js/">PDF.js</a>', t('direct_pdf_and_embedded_document_viewer_tasks', 'Direct-PDF and embedded-document-viewer tasks.')), ('<a href="https://github.com/mdn/learning-area">' + t('mdn_media_examples', 'MDN media examples') + '</a>', t('image_and_video_endpoint_tasks', 'Image- and video-endpoint tasks.'))])}""")


def acceptance_scope(cases, records):
    """Describe frozen requirements and saved outcomes without changing either."""

    families = defaultdict(Counter)
    affiliated = []
    for case in cases.values():
        counts = families[case.get('track', 'web')]
        contract = case.get('acceptance_contract') or {}
        counts['tasks'] += 1
        for key, present in {
            'identity': case.get('page_identity_required', False),
            'literal': bool(case.get('expected_any') or contract.get('stable_text_anchors')),
            'facts': bool(case.get('required_facts')),
            'body_regions': bool((case.get('body_requirements') or {}).get('regions')),
            'records': bool(contract.get('minimum_records', 0)),
            'resources': bool(case.get('expected_resources')),
            'full_reference': bool(case.get('ground_truth_main_content') and case.get('content_score_applicable', True)),
            'short_length_floor': case.get('min_content_chars', 1) <= 1,
        }.items():
            counts[key] += bool(present)
        url = urlsplit(case.get('url', ''))
        if url.hostname == 'github.com' and (url.path.rstrip('/') == '/lexmount/moli' or url.path.startswith('/lexmount/moli/')):
            # The benchmark maintainer and this evaluated repository share an
            # organization. The frozen task remains in the original population.
            affiliated.append(case['id'])
    methods = defaultdict(Counter)
    for row in records:
        case = cases[row['case_id']]
        metrics = row.get('metrics') or {}
        counts = methods[row['provider']]
        counts['results'] += 1
        passed = task_passed(row['ok'], metrics, case.get('task_kind', 'body'))
        counts['passes'] += passed
        counts['identity_verified'] += metrics.get('page_identity_status') == 'verified'
        counts['unverified_identity_passes'] += bool(passed and metrics.get('page_identity_status') != 'verified')
        counts['blocked_diagnostics'] += bool(metrics.get('blocked_page') or metrics.get('stable_blocked_page'))
        if case.get('expected_resources'):
            counts['resource_results'] += 1
            counts['unexpected_resources'] += len(metrics.get('unexpected_resource_urls') or [])
            precision = metrics.get('resource_precision')
            if isinstance(precision, (int, float)) and not isinstance(precision, bool):
                counts['resource_precision_sum'] += precision
                counts['resource_precision_count'] += 1
    return {'families': dict(families), 'methods': dict(methods), 'affiliated_cases': affiliated}


def render_acceptance_scope(cases, records):
    from harness.web_fetch.report import cell, link, pct, provider_label, section, table
    from harness.web_fetch.report import model_text
    scope = acceptance_scope(cases, records)
    fields = ('tasks', 'identity', 'literal', 'facts', 'body_regions', 'records', 'resources', 'full_reference', 'short_length_floor')
    rows = [('', cell(esc(track)) + ''.join(cell(str(counts[key])) for key in fields))
            for track, counts in sorted(scope['families'].items())]
    body = '<p>' + model_text(*pair('the_table_counts_requirements_declared_by_frozen', 'The table counts requirements declared by frozen tasks. A task may use several checks. Literal text, fact groups and record structure are separate; tasks without body regions or a full reference do not establish full-body quality.')) + '</p>'
    body += table([model_text(*pair('task_family_2', 'Task family')), model_text(*pair('tasks', 'Tasks')), model_text(*pair('identity_required', 'Identity required')),
                   model_text(*pair('literal_text', 'Literal text')), model_text(*pair('fact_groups', 'Fact groups')), model_text(*pair('body_regions', 'Body regions')),
                   model_text(*pair('records', 'Records')), model_text(*pair('resource_targets', 'Resource targets')), model_text(*pair('full_reference', 'Full reference')), model_text(*pair('minimum_length_1', 'Minimum length ≤1'))], rows, metrics=[])
    rows = []
    for name, counts in sorted(scope['methods'].items()):
        unverified = counts['unverified_identity_passes']
        passes = counts['passes']
        precision = (counts['resource_precision_sum'] / counts['resource_precision_count']
                     if counts['resource_precision_count'] else None)
        values = (str(counts['results']), str(passes), str(counts['identity_verified']),
                  str(unverified) + '/' + str(passes) + ' (' + pct(unverified / passes if passes else None) + ')',
                  str(counts['blocked_diagnostics']), str(counts['resource_results']),
                  str(counts['unexpected_resources']), pct(precision))
        rows.append(('', cell(esc(provider_label(name))) + ''.join(cell(value) for value in values)))
    body += '<p>' + model_text(*pair('passes_without_verified_page_identity_retain_their', 'Passes without verified page identity retain their frozen verdicts; their share uses all passes as its denominator. Blocked-page markers are diagnostic. Unexpected URLs and mean precision describe resource tasks without adding a new recall threshold. Language checks compare the primary tag unless a task declares exact regional matching.')) + '</p>'
    body += table([model_text(*pair('method', 'Method')), model_text(*pair('results_2', 'Results')), model_text(*pair('passes', 'Passes')),
                   model_text(*pair('identity_verified', 'Identity verified')), model_text(*pair('unverified_identity_passes_all_passes', 'Unverified identity passes / all passes')),
                   model_text(*pair('blocked_markers', 'Blocked markers')), model_text(*pair('resource_results', 'Resource results')),
                   model_text(*pair('unexpected_resource_urls', 'Unexpected resource URLs')), model_text(*pair('mean_resource_precision', 'Mean resource precision'))], rows, metrics=[])
    if scope['affiliated_cases']:
        body += '<p>' + model_text(*pair('affiliation_these_tasks_concern_a_product_repository', 'Affiliation: these tasks concern a product repository in the benchmark maintainer’s organization. The original tasks and denominator are retained.')) + ' ' + ' · '.join(
                             link('../datasets/' + identifier + '.html', esc(identifier))
                             for identifier in scope['affiliated_cases']) + '</p>'
    return section(model_text(*pair('acceptance_coverage_and_diagnostics', 'Acceptance coverage and diagnostics')), body, 'acceptance-scope')
