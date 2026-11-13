"""Judge generated answers with the shared transport and frozen native evidence."""

import hashlib
import json
from copy import deepcopy

from harness.benchmark_execution import ExecutionPolicy, Job, exclusive_policy, run_jobs
from harness.web_search.relevance import run_judge_request, validate_physical_receipts
from lexbench_websearch.answers import (
    CITATION_CORRECTION,
    CITATION_PROMPT,
    CITATION_RULE,
    CITATION_VALIDATION,
    SCENARIO_PROMPT,
    SIMPLEQA_UPSTREAM,
    answer_messages,
    answer_packet,
    citation_grades,
    factual_grade,
    factual_messages,
    official_template,
    scenario_grade,
)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _inputs_digest(profile, rows):
    # The report validates these two storage receipts before reconstructing results.
    # They describe serialization and are absent in the original collection rows.
    original = [{key: value for key, value in row.items()
                 if key not in ('result_count', 'normalized_results_sha256')} for row in rows]
    return _digest(dict(profile=profile, rows=original))


def _write(path, value):
    raw = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(raw, encoding='utf-8')
    temporary.replace(path)


def _prompt_hashes(source):
    return {kind: hashlib.sha256(text.encode()).hexdigest()
            for kind, text in dict(scenario=SCENARIO_PROMPT, citations=CITATION_PROMPT,
                                   factual=official_template(source)).items()}


def _jobs(profile, rows, upstream_source):
    official_template(upstream_source)
    cases = {c['id']: c for c in profile['cases']}
    excluded = set(profile.get('factual_quality_exclusions', []))
    expected = {(mid['id'], cid) for mid in profile['methods'] for cid in cases}
    actual = [(row['method'], row['case_id']) for row in rows]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError('Answer grading requires the complete method-by-task population')
    jobs, packets = {}, {}
    for row in rows:
        case = cases[row['case_id']]
        if case['id'] in excluded:
            continue
        packet = answer_packet(case, dict(row, results=row['results'][:profile['protocol']['top_k']]))
        key = row['method'] + '/' + case['id']
        if key in packets:
            raise ValueError('Answer judging requires one logical row per method and task')
        packets[key] = dict(method=row['method'], packet=packet)
        kind = 'factual' if 'reference_answer' in case else 'scenario'
        messages = (factual_messages(case, packet['answer'], upstream_source)
                    if kind == 'factual' else answer_messages(packet))
        jobs[key + '/' + kind] = dict(kind=kind, packet_key=key, messages=messages)
        if packet['citations']:
            jobs[key + '/citations'] = dict(kind='citations', packet_key=key,
                                            messages=answer_messages(packet, citations=True))
    return packets, jobs


def _request(job, model):
    request = dict(model=model, temperature=0, max_tokens=8192, messages=job['messages'],
                   extra_params={'enable_thinking': False})
    if job['kind'] != 'factual':
        request['response_format'] = {'type': 'json_object'}
    return request


def _parse(job, packet, response):
    try:
        text = response['choices'][0]['message']['content']
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError('Missing answer-judge response text') from error
    if not isinstance(text, str):
        raise ValueError('Answer-judge response must be text')
    if job['kind'] == 'factual':
        return factual_grade(text)
    if job['kind'] == 'scenario':
        return scenario_grade(packet, text)
    return citation_grades(packet, text)


def _format_request(job, model):
    request = _request(job, model)
    suffix = (
        "\nOutput-format correction only; keep the grading rubric unchanged. "
        "Return every citation_index exactly once, with a JSON boolean supported and "
        "source_refs containing only ids in that occurrence's supplied source_fields. "
        "Unsupported citations require source_refs=[]. Do not copy claims or quotations."
        if job['kind'] == 'citations' else
        "\nOutput-format correction only; keep the rubric unchanged. Return the required "
        "scenario JSON with only an integer grade and a short reason; do not copy evidence."
        if job['kind'] == 'scenario' else
        "\nOutput-format correction only; keep the original rubric unchanged. Return exactly A, B or C."
    )
    messages = deepcopy(request['messages'])
    messages[0]['content'] += suffix
    return dict(request, messages=messages)


def _validate_call(job, packet, call, configuration, key):
    """Accept at most one explicit repair of a physically verified invalid response."""
    base = _request(job, configuration['model'])
    repair = call.get('format_repair')
    if repair:
        prior = repair['prior_call']
        if (prior.get('case_id') != key or prior.get('request') != base
                or prior.get('format_repair')):
            raise ValueError('Invalid answer format-repair provenance')
        validate_physical_receipts(prior, configuration)
        try:
            _parse(job, packet, prior['response'])
        except (ValueError, KeyError, TypeError):
            pass
        else:
            raise ValueError('A valid answer judgment cannot be repaired')
        expected = _format_request(job, configuration['model'])
    else:
        expected = base
    if (call.get('case_id') != key or call.get('request') != expected
            or configuration.get('attempt_limit') != 1 or len(call.get('attempts', [])) != 1):
        raise ValueError('Answer judge request or attempt count changed')
    validate_physical_receipts(call, configuration)
    return _parse(job, packet, call['response'])


def verify_answer_evidence(profile, rows, evidence, upstream_source):
    """Rebuild judge requests and recompute every grade from retained response bytes."""
    if evidence.get('schema') != 'web-search-answer-evidence/1':
        raise ValueError('Unsupported answer evidence')
    if evidence.get('validation') != CITATION_VALIDATION:
        raise ValueError('Answer evidence lacks its decision-evidence validation receipt')
    from harness.web_search.revisions import validate_revision_controls

    validate_revision_controls(CITATION_VALIDATION)
    packets, jobs = _jobs(profile, rows, upstream_source)
    configuration = evidence['configuration']
    if (configuration.get('inputs_sha256') != _inputs_digest(profile, rows)
            or configuration.get('jobs_sha256') != _digest(jobs)
            or configuration.get('maximum_calls') != len(jobs)
            or configuration.get('upstream') != SIMPLEQA_UPSTREAM
            or configuration.get('citation_rule') != CITATION_RULE
            or configuration.get('scoring_provenance') != CITATION_CORRECTION
            or configuration.get('prompt_sha256') != _prompt_hashes(upstream_source)
            or configuration.get('temperature') != 0
            or type(configuration.get('attempt_limit')) is not int
            or configuration['attempt_limit'] != 1
            or configuration.get('extra_params') != {'enable_thinking': False}
            or configuration.get('request_headers') != {'x-bf-passthrough-extra-params': 'true'}
            or set(evidence['calls']) != set(jobs)):
        raise ValueError('Answer evidence differs from frozen inputs or judge configuration')
    results = {}
    for key, value in packets.items():
        packet = value['packet']
        count = len(packet['citations'])
        results[key] = dict(method=value['method'], case_id=packet['case_id'],
                            citation_count=count,
                            citations_in_results=sum(c['in_results'] for c in packet['citations']),
                            supported_citations=0, citation_support=None, cited_urls_in_results=None)
    for key, job in jobs.items():
        call = evidence['calls'][key]
        packet_key = job['packet_key']
        grade = _validate_call(job, packets[packet_key]['packet'], call, configuration, key)
        result = results[packet_key]
        if job['kind'] == 'factual':
            result['factual_grade'] = grade
        elif job['kind'] == 'scenario':
            result['answer_grade'] = grade['grade']
        else:
            result['supported_citations'] = sum(item['supported'] for item in grade)
            result['citation_support'] = result['supported_citations'] / result['citation_count']
            result['cited_urls_in_results'] = result['citations_in_results'] / result['citation_count']
    values = list(results.values())
    if 'results' in evidence and evidence['results'] != values:
        raise ValueError('Saved answer grades differ from replayed judgments')
    return values


def judge_answers(profile, rows, args, token):
    """Finish a complete L3 population; a resumed run replays saved calls verbatim."""
    if args.retries:
        raise ValueError('L3 answer judging requires zero retries')
    if args.simpleqa_grader is None:
        raise ValueError('L3 requires the pinned official SimpleQA grader source')
    if args.reuse_evidence or args.source_requirements:
        raise ValueError('L3 does not use relevance inheritance or format-repair inputs')
    from harness.web_search.revisions import validate_revision_controls

    validate_revision_controls(CITATION_CORRECTION)
    validate_revision_controls(CITATION_VALIDATION)
    upstream_source = args.simpleqa_grader.read_bytes()
    packets, jobs = _jobs(profile, rows, upstream_source)
    configuration = dict(schema='web-search-answer-judge/1', model=args.model,
                         base_url=args.base_url, temperature=0, max_tokens=8192,
                         attempt_limit=1, extra_params={'enable_thinking': False},
                         request_headers={'x-bf-passthrough-extra-params': 'true'},
                         upstream=deepcopy(SIMPLEQA_UPSTREAM),
                         citation_rule=CITATION_RULE, scoring_provenance=deepcopy(CITATION_CORRECTION),
                         prompt_sha256=_prompt_hashes(upstream_source),
                         inputs_sha256=_inputs_digest(profile, rows),
                         jobs_sha256=_digest(jobs), maximum_calls=len(jobs))
    args.output.mkdir(parents=True, exist_ok=True)
    contract = args.output/'contract.json'
    if contract.exists() and json.loads(contract.read_bytes()) != configuration:
        raise ValueError('Existing answer judge has different frozen inputs')
    _write(contract, configuration)
    retained_source = args.output/'simpleqa_eval.py'
    if retained_source.exists() and retained_source.read_bytes() != upstream_source:
        raise ValueError('Saved official grader source changed')
    retained_source.write_bytes(upstream_source)
    calls = {}

    def execute(item):
        key, job = item
        path = args.output/(hashlib.sha256(key.encode()).hexdigest() + '.json')
        request = _request(job, args.model)
        if path.exists():
            call = json.loads(path.read_bytes())
        else:
            call = run_judge_request(path, key, request, configuration, token)
        packet = packets[job['packet_key']]['packet']
        try:
            _validate_call(job, packet, call, configuration, key)
        except (ValueError, KeyError, TypeError):
            if not args.repair_format or call.get('format_repair'):
                raise
            # Transport/request verification must pass before a paid format correction.
            if call.get('case_id') != key or call.get('request') != request:
                raise ValueError('Saved answer judge input changed') from None
            validate_physical_receipts(call, configuration)
            call = run_judge_request(path, key, _format_request(job, args.model),
                                     configuration, token, prior_call=call)
            _validate_call(job, packet, call, configuration, key)
        return call

    def retain(job, value, error):
        if error:
            raise error
        calls[job.key] = value
        print(job.key, 'scored', flush=True)

    policy_value = (exclusive_policy('judge', 'Explicit --workers=1 limits this judge account to one outstanding request')
                    if args.workers == 1 else dict(purpose='quality', max_in_flight=args.workers,
                                                   resources={'judge': {'capacity': args.workers}}))
    policy = ExecutionPolicy.from_dict(policy_value)
    run_jobs([Job(key, ('judge',), (key, job)) for key, job in jobs.items()],
             execute, retain, policy)
    evidence = dict(schema='web-search-answer-evidence/1', configuration=configuration,
                    validation=deepcopy(CITATION_VALIDATION),
                    calls={key: calls[key] for key in jobs})
    evidence['results'] = verify_answer_evidence(profile, rows, evidence, upstream_source)
    _write(args.output/'answers.json', evidence)
    return evidence
